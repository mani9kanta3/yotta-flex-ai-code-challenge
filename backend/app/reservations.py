"""
What happens to a hold after it is placed: confirm, cancel or expire.

Every change adds an event, and the status column is updated to match. All the
holds of one order move together, because they share a payment and an expiry
time.

Confirming and cancelling lock the order row and then its SKU-location rows, the same order
shipping uses. A new order takes those rows too, so it can't slip in between. The time is
looked at again after the locks are held, because a time read before them can be out of date:
a payment that started before a hold ran out could otherwise commit after someone else took
that stock.

Consuming a hold when stock ships is in shipping.py. What happens to a held order
when its truck is late is in late_trucks.py.
"""

from . import rules
from .orders import (add_order_event, add_reservation_event, decision_time, lock_pairs, run_in_transaction,
                     start_locked_transaction)


class OrderStateError(Exception):
    """The order can't do this from the state it is in."""


def lock_order(cursor, order_id):
    """Stops a confirm and an expiry changing the same order at the same moment."""
    cursor.execute("SELECT status FROM orders WHERE order_id = %s FOR UPDATE", (order_id,))
    row = cursor.fetchone()
    if row is None:
        raise OrderStateError(f"no order {order_id}")
    return row[0]


def get_open_holds(cursor, order_id):
    cursor.execute(
        """SELECT r.reservation_id, r.status, r.expires_at, r.qty
           FROM reservation r JOIN order_line l USING (order_line_id)
           WHERE l.order_id = %s AND r.status IN ('ACTIVE', 'CONFIRMED')
           ORDER BY r.reservation_id""",
        (order_id,),
    )
    return cursor.fetchall()


def check_time_moves_forward(cursor, order_id, t):
    """An event dated before the last one would rewrite what history says happened."""
    cursor.execute("SELECT MAX(occurred_at) FROM order_event WHERE order_id = %s", (order_id,))
    latest = cursor.fetchone()[0]
    if latest is not None and t < latest:
        raise OrderStateError(f"{t} is before the last event on {order_id}, at {latest}")


def lock_stock_of_order(cursor, order_id, t):
    """Returns t moved past anything already done to this stock, so nothing is dated behind it."""
    cursor.execute(
        """SELECT r.sku_id, r.location_id
           FROM reservation r JOIN order_line l USING (order_line_id)
           WHERE l.order_id = %s AND r.status IN ('ACTIVE', 'CONFIRMED')""",
        (order_id,),
    )
    pairs = rules.sorted_pairs(cursor.fetchall())
    lock_pairs(cursor, pairs)
    return decision_time(cursor, pairs, t)


def set_hold_status(cursor, reservation_id, from_status, to_status, occurred_at, reason):
    add_reservation_event(cursor, reservation_id, from_status, to_status, occurred_at, reason)
    cursor.execute("UPDATE reservation SET status = %s WHERE reservation_id = %s",
                   (to_status, reservation_id))


def set_order_status(cursor, order_id, from_status, to_status, occurred_at, reason):
    add_order_event(cursor, order_id, None, from_status, to_status, occurred_at, reason)
    cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", (to_status, order_id))


def cancel_open_lines(cursor, order_id, occurred_at, reason):
    cursor.execute(
        "SELECT order_line_id FROM order_line WHERE order_id = %s AND status = 'ALLOCATED' ORDER BY line_no",
        (order_id,),
    )
    for (order_line_id,) in cursor.fetchall():
        add_order_event(cursor, order_id, order_line_id, "ALLOCATED", "CANCELLED", occurred_at, reason)
        cursor.execute("UPDATE order_line SET status = 'CANCELLED' WHERE order_line_id = %s",
                       (order_line_id,))


def confirm_order_in_transaction(cursor, order_id, t):
    start_locked_transaction(cursor)
    status = lock_order(cursor, order_id)
    if status != "ALLOCATED":
        raise OrderStateError(f"can't confirm {order_id}, it is {status}")
    check_time_moves_forward(cursor, order_id, t)
    t = lock_stock_of_order(cursor, order_id, t)

    holds = get_open_holds(cursor, order_id)
    for _reservation_id, hold_status, expires_at, _qty in holds:
        if not rules.can_confirm(hold_status, t, expires_at):
            raise OrderStateError(f"can't confirm {order_id}, its hold expired at {expires_at}")

    for reservation_id, hold_status, _expires_at, _qty in holds:
        set_hold_status(cursor, reservation_id, hold_status, "CONFIRMED", t, "PAYMENT_RECEIVED")
    set_order_status(cursor, order_id, "ALLOCATED", "CONFIRMED", t, "PAYMENT_RECEIVED")


def confirm_order(connection, order_id, t):
    """Payment arrived. Refused at or after expiry, because that stock is already free."""
    return run_in_transaction(connection, confirm_order_in_transaction, order_id, t)


def cancel_order_in_transaction(cursor, order_id, t, reason):
    start_locked_transaction(cursor)
    status = lock_order(cursor, order_id)
    if status not in ("ALLOCATED", "CONFIRMED"):
        raise OrderStateError(f"can't cancel {order_id}, it is {status}")
    check_time_moves_forward(cursor, order_id, t)
    t = lock_stock_of_order(cursor, order_id, t)

    holds = get_open_holds(cursor, order_id)
    for _reservation_id, hold_status, expires_at, _qty in holds:
        # This hold has already run out. Expiry owns it, not a cancellation.
        if hold_status == "ACTIVE" and t >= expires_at:
            raise OrderStateError(f"can't cancel {order_id}, its hold expired at {expires_at}")

    for reservation_id, hold_status, _expires_at, _qty in holds:
        set_hold_status(cursor, reservation_id, hold_status, "CANCELLED", t, reason)
    cancel_open_lines(cursor, order_id, t, reason)
    set_order_status(cursor, order_id, status, "CANCELLED", t, reason)


def cancel_order(connection, order_id, t, reason="CUSTOMER_CANCELLED"):
    return run_in_transaction(connection, cancel_order_in_transaction, order_id, t, reason)


def expire_due(connection, as_of):
    """
    Record every hold that has run out by as_of.

    ATP stopped counting these at expires_at whether or not this has run, so a late
    job can't lock stock. The event is dated expires_at, when it really happened,
    not when the job got to it. Running it twice changes nothing. No movement is
    written, because no stock physically moved.
    """
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT l.order_id
               FROM reservation r JOIN order_line l USING (order_line_id)
               WHERE r.status = 'ACTIVE' AND r.expires_at <= %s
               ORDER BY l.order_id""",
            (as_of,),
        )
        order_ids = [row[0] for row in cursor.fetchall()]

    summary = {"orders_expired": 0, "reservations_expired": 0, "units_released": 0}
    for order_id in order_ids:
        # One transaction per order, so one bad order can't hold up the rest.
        with connection, connection.cursor() as cursor:
            status = lock_order(cursor, order_id)
            holds = [hold for hold in get_open_holds(cursor, order_id)
                     if hold[1] == "ACTIVE" and hold[2] <= as_of]
            if not holds:
                continue

            expired_at = holds[0][2]
            for reservation_id, hold_status, expires_at, qty in holds:
                set_hold_status(cursor, reservation_id, hold_status, "EXPIRED", expires_at, "HOLD_EXPIRED")
                summary["reservations_expired"] += 1
                summary["units_released"] += qty
            cancel_open_lines(cursor, order_id, expired_at, "HOLD_EXPIRED")
            set_order_status(cursor, order_id, status, "CANCELLED", expired_at, "HOLD_EXPIRED")
            summary["orders_expired"] += 1
    return summary
