"""
What happens to a paid order when the truck it is waiting for runs late.

For each paid hold on the truck, rules.late_truck_action picks one of:

    KEEP        the promise can still be kept, so nothing changes
    REALLOCATE  another location has it on hand: the old hold is cancelled and a new
                one is created
    CANCEL      past the limit after the first promise: the hold is released
    REPROMISE   a CONFIRMED to CONFIRMED event carries the new date

The first promise stays on the reservation row and every later one is an event, so
history shows them all.

A hold only ever moves inside one transaction. The old hold is cancelled and the new
one created at the same timestamp, so at no moment are both holding stock. The stock
is read before the old hold is released, so a decision can't count the units of the
hold it is about to give up.
"""

from . import atp, orders, rules
from .reservations import OrderStateError, check_time_moves_forward, lock_order, set_hold_status, set_order_status
from .transfers import delay_transfer, get_shipment_state


def get_order_of_hold(cursor, reservation_id):
    cursor.execute(
        """SELECT l.order_id FROM reservation r JOIN order_line l USING (order_line_id)
           WHERE r.reservation_id = %s""",
        (reservation_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise OrderStateError(f"no reservation {reservation_id}")
    return row[0]


def get_hold(cursor, reservation_id):
    cursor.execute(
        """SELECT r.status, r.supply_type, r.inbound_shipment_id, r.sku_id, r.qty, r.order_line_id,
                  r.promised_ship_by, o.channel_code, o.ship_to_region
           FROM reservation r
           JOIN order_line l USING (order_line_id)
           JOIN orders o USING (order_id)
           WHERE r.reservation_id = %s""",
        (reservation_id,),
    )
    row = cursor.fetchone()
    return {"status": row[0], "supply_type": row[1], "shipment_id": row[2], "sku_id": row[3], "qty": row[4],
            "order_line_id": row[5], "first_promise": row[6], "channel_code": row[7], "region": row[8]}


def get_current_promise(cursor, reservation_id, t):
    """The latest promise up to t. The first one is on the first event too."""
    cursor.execute(
        """SELECT promised_ship_by FROM reservation_event
           WHERE reservation_id = %s AND occurred_at <= %s AND promised_ship_by IS NOT NULL
           ORDER BY occurred_at DESC, event_id DESC LIMIT 1""",
        (reservation_id, t),
    )
    return cursor.fetchone()[0]


def get_current_eta(cursor, shipment_id, t):
    cursor.execute(
        """SELECT COALESCE(
               (SELECT e.eta_at FROM shipment_event e
                WHERE e.shipment_id = s.shipment_id AND e.occurred_at <= %s AND e.eta_at IS NOT NULL
                ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1),
               s.original_eta_at)
           FROM shipment s WHERE s.shipment_id = %s""",
        (t, shipment_id),
    )
    return cursor.fetchone()[0]


def next_reservation_number(cursor, order_id):
    cursor.execute(
        """SELECT COUNT(*) + 1 FROM reservation r JOIN order_line l USING (order_line_id)
           WHERE l.order_id = %s""",
        (order_id,),
    )
    return cursor.fetchone()[0]


def cancel_line(cursor, order_id, order_line_id, t, reason):
    orders.add_order_event(cursor, order_id, order_line_id, "ALLOCATED", "CANCELLED", t, reason)
    cursor.execute("UPDATE order_line SET status = 'CANCELLED' WHERE order_line_id = %s", (order_line_id,))
    cursor.execute("SELECT COUNT(*) FROM order_line WHERE order_id = %s AND status = 'ALLOCATED'", (order_id,))
    # The order only ends here if nothing is left. Its other lines can still ship.
    if cursor.fetchone()[0] == 0:
        set_order_status(cursor, order_id, "CONFIRMED", "CANCELLED", t, reason)


def move_hold(cursor, reservation_id, order_id, hold, target, ranked, channel, t):
    reason = "INBOUND_LATE_REALLOCATED"
    set_hold_status(cursor, reservation_id, "CONFIRMED", "CANCELLED", t, reason)

    part = rules.on_hand_part(target, hold["qty"])
    new_hold = orders.write_reservation(cursor, order_id, next_reservation_number(cursor, order_id),
                                        hold["order_line_id"], hold["sku_id"], part, channel, t)
    # The customer has already paid, so the new hold starts out confirmed.
    set_hold_status(cursor, new_hold["reservation_id"], "ACTIVE", "CONFIRMED", t, reason)
    orders.write_decision(cursor, hold["order_line_id"], hold["qty"],
                          rules.decision("REALLOCATED", reason, [part], ranked), t)


def handle_late_hold_in_transaction(cursor, reservation_id, t, lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    """Returns the action taken. Safe to run twice, because the second run finds nothing to do."""
    orders.start_locked_transaction(cursor, lock_timeout_ms)

    # The order first and then the stock, the same order shipping takes them in.
    order_id = get_order_of_hold(cursor, reservation_id)
    lock_order(cursor, order_id)
    hold = get_hold(cursor, reservation_id)
    if hold["status"] != "CONFIRMED" or hold["supply_type"] != "INBOUND":
        return "KEEP"
    if get_shipment_state(cursor, hold["shipment_id"], t) != "IN_TRANSIT":
        return "KEEP"
    check_time_moves_forward(cursor, order_id, t)

    channel = atp.get_channel(cursor, hold["channel_code"])
    candidates = atp.get_candidates(cursor, hold["sku_id"], hold["channel_code"], t)
    pairs = rules.sorted_pairs((hold["sku_id"], candidate["location_id"]) for candidate in candidates)
    orders.lock_pairs(cursor, pairs)
    decided_at = orders.decision_time(cursor, pairs, t)

    positions = atp.get_positions(cursor, hold["sku_id"], hold["channel_code"], decided_at)
    ranked = rules.rank_candidates(positions, hold["region"])
    target = rules.choose_reallocation(hold["qty"], positions, hold["region"])

    new_promise = rules.late_promise(get_current_eta(cursor, hold["shipment_id"], decided_at), decided_at)
    action = rules.late_truck_action(target is not None, hold["first_promise"],
                                     get_current_promise(cursor, reservation_id, decided_at), new_promise)

    if action == "REALLOCATE":
        move_hold(cursor, reservation_id, order_id, hold, target, ranked, channel, decided_at)
    elif action == "CANCEL":
        set_hold_status(cursor, reservation_id, "CONFIRMED", "CANCELLED", decided_at, "INBOUND_LATE_CANCELLED")
        cancel_line(cursor, order_id, hold["order_line_id"], decided_at, "INBOUND_LATE_CANCELLED")
    elif action == "REPROMISE":
        orders.add_reservation_event(cursor, reservation_id, "CONFIRMED", "CONFIRMED", decided_at,
                                     "INBOUND_LATE_REPROMISED", new_promise)
    return action


def handle_late_hold(connection, reservation_id, t):
    return orders.run_in_transaction(connection, handle_late_hold_in_transaction, reservation_id, t)


def handle_late_truck(connection, shipment_id, t):
    """Oldest hold first. One transaction per hold, so one busy SKU can't hold up the rest."""
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT reservation_id FROM reservation
               WHERE inbound_shipment_id = %s AND status = 'CONFIRMED'
               ORDER BY created_at, reservation_id""",
            (shipment_id,),
        )
        reservation_ids = [row[0] for row in cursor.fetchall()]

    summary = {"KEEP": 0, "REALLOCATE": 0, "CANCEL": 0, "REPROMISE": 0}
    for reservation_id in reservation_ids:
        summary[handle_late_hold(connection, reservation_id, t)] += 1
    return summary


def report_late_truck(connection, shipment_id, new_eta_at, t):
    """The truck has a new ETA. Record it, then deal with everyone waiting on it."""
    delay_transfer(connection, shipment_id, new_eta_at, t)
    return handle_late_truck(connection, shipment_id, t)


def handle_overdue_trucks(connection, t):
    """
    For a job to run regularly. It catches a truck that went quiet with no new ETA, and
    a hold that was paid for after the truck's last delay was handled.
    """
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT DISTINCT r.inbound_shipment_id FROM reservation r
               WHERE r.status = 'CONFIRMED' AND r.inbound_shipment_id IS NOT NULL
               ORDER BY r.inbound_shipment_id"""
        )
        shipment_ids = [row[0] for row in cursor.fetchall()]

    handled = []
    for shipment_id in shipment_ids:
        summary = handle_late_truck(connection, shipment_id, t)
        if summary["REALLOCATE"] or summary["CANCEL"] or summary["REPROMISE"]:
            handled.append(shipment_id)
    return handled
