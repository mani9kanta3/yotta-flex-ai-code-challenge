"""
Ship a paid order to the customer: the SALE movement, the CONSUMED hold and the
FULFILLED order.

An order holding stock on a truck has to wait. It can't ship until that truck has
been received, so a SALE can never post before its TRANSFER_IN. All the holds of an
order ship together, in one box per location, so an order with one line on a truck
waits for that truck.

A waiting order whose truck runs late is handled in late_trucks.py.

NOT BUILT YET, see docs/STATUS.md: anything for a short pick beyond refusing the
shipment.
"""

from . import atp, orders, rules
from .movements import add_movement
from .reservations import (OrderStateError, check_time_moves_forward, lock_order, set_hold_status,
                           set_order_status)
from .transfers import add_shipment_event, get_shipment_state


class WaitingForInbound(OrderStateError):
    """Its stock is still on a truck. Nothing was written."""


def get_holds_to_ship(cursor, order_id):
    cursor.execute(
        """SELECT r.reservation_id, r.order_line_id, r.sku_id, r.location_id, r.qty, r.supply_type,
                  r.inbound_shipment_id, a.allocation_id
           FROM reservation r
           JOIN order_line l USING (order_line_id)
           JOIN order_allocation a USING (reservation_id)
           WHERE l.order_id = %s AND r.status = 'CONFIRMED'
           ORDER BY r.reservation_id""",
        (order_id,),
    )
    return [
        {"reservation_id": row[0], "order_line_id": row[1], "sku_id": row[2], "location_id": row[3],
         "qty": row[4], "supply_type": row[5], "inbound_shipment_id": row[6], "allocation_id": row[7]}
        for row in cursor.fetchall()
    ]


def check_trucks_have_arrived(cursor, order_id, holds, t):
    for hold in holds:
        state = None
        if hold["supply_type"] == "INBOUND":
            state = get_shipment_state(cursor, hold["inbound_shipment_id"], t)
        if not rules.can_ship_hold(hold["supply_type"], state):
            raise WaitingForInbound(f"{order_id} is waiting for {hold['inbound_shipment_id']}, "
                                    f"which is {state} at {t}")


def check_stock_is_on_the_shelf(cursor, hold, shipped_at):
    """Damage found after the order was placed can leave less than was promised."""
    on_hand, blocked = atp.get_stock(cursor, hold["sku_id"], hold["location_id"], shipped_at)
    if on_hand - blocked < hold["qty"]:
        raise OrderStateError(f"short pick: {hold['location_id']} has {on_hand - blocked} "
                              f"of {hold['sku_id']}, needs {hold['qty']}")


def write_customer_shipment(cursor, order_id, location_id, shipped_at):
    shipment_id = f"SHP-2026-{orders.next_id(cursor, 'shipment_number'):06d}"
    cursor.execute(
        """INSERT INTO shipment (shipment_id, shipment_type, status, order_id, from_location_id, created_at)
           VALUES (%s, 'CUSTOMER', 'IN_TRANSIT', %s, %s, %s)""",
        (shipment_id, order_id, location_id, shipped_at),
    )
    add_shipment_event(cursor, shipment_id, None, "CREATED", shipped_at, "PACKED")
    add_shipment_event(cursor, shipment_id, "CREATED", "IN_TRANSIT", shipped_at, "DISPATCHED")
    return shipment_id


def ship_order_in_transaction(cursor, order_id, t, lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    orders.start_locked_transaction(cursor, lock_timeout_ms)

    status = lock_order(cursor, order_id)
    if status != "CONFIRMED":
        raise OrderStateError(f"can't ship {order_id}, it is {status}")
    check_time_moves_forward(cursor, order_id, t)

    holds = get_holds_to_ship(cursor, order_id)
    check_trucks_have_arrived(cursor, order_id, holds, t)

    # A SALE reduces stock, so it takes the same lock as everything else that does.
    pairs = rules.sorted_pairs((hold["sku_id"], hold["location_id"]) for hold in holds)
    orders.lock_pairs(cursor, pairs)
    shipped_at = orders.decision_time(cursor, pairs, t)

    shipment_ids = {}
    line_ids = []
    for hold in holds:
        check_stock_is_on_the_shelf(cursor, hold, shipped_at)

        location_id = hold["location_id"]
        if location_id not in shipment_ids:
            shipment_ids[location_id] = write_customer_shipment(cursor, order_id, location_id, shipped_at)
        shipment_id = shipment_ids[location_id]

        cursor.execute("UPDATE order_allocation SET shipment_id = %s WHERE allocation_id = %s",
                       (shipment_id, hold["allocation_id"]))
        add_movement(cursor, hold["sku_id"], location_id, "SALE", -hold["qty"], shipped_at, "ORDER_SHIPPED",
                     f"SALE-{hold['allocation_id']}", allocation_id=hold["allocation_id"],
                     shipment_id=shipment_id)
        set_hold_status(cursor, hold["reservation_id"], "CONFIRMED", "CONSUMED", shipped_at, "SHIPPED")

        if hold["order_line_id"] not in line_ids:
            line_ids.append(hold["order_line_id"])

    for order_line_id in line_ids:
        orders.add_order_event(cursor, order_id, order_line_id, "ALLOCATED", "SHIPPED", shipped_at, "SHIPPED")
        cursor.execute("UPDATE order_line SET status = 'SHIPPED' WHERE order_line_id = %s", (order_line_id,))
    set_order_status(cursor, order_id, "CONFIRMED", "FULFILLED", shipped_at, "SHIPPED")

    return {"order_id": order_id, "shipped_at": shipped_at, "shipment_ids": list(shipment_ids.values())}


def ship_order(connection, order_id, t, lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    return orders.run_in_transaction(connection, ship_order_in_transaction, order_id, t, lock_timeout_ms)


def deliver_shipment(connection, shipment_id, t):
    """The parcel reached the customer. No stock moves, because it left when it shipped."""
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT status, created_at FROM shipment
               WHERE shipment_id = %s AND shipment_type = 'CUSTOMER' FOR UPDATE""",
            (shipment_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise OrderStateError(f"no customer shipment {shipment_id}")
        if row[0] != "IN_TRANSIT" or t <= row[1]:
            raise OrderStateError(f"can't deliver {shipment_id} at {t}, it is {row[0]}, shipped {row[1]}")
        add_shipment_event(cursor, shipment_id, "IN_TRANSIT", "DELIVERED", t, "DELIVERED")
        cursor.execute("UPDATE shipment SET status = 'DELIVERED' WHERE shipment_id = %s", (shipment_id,))


def ship_waiting_orders(connection, shipment_id, t):
    """
    Call after a truck is received. Paid orders waiting on it ship oldest first. An
    order still waiting on another truck, or short of stock, stays as it is.
    """
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT l.order_id
               FROM reservation r JOIN order_line l USING (order_line_id)
               WHERE r.inbound_shipment_id = %s AND r.status = 'CONFIRMED'
               GROUP BY l.order_id
               ORDER BY MIN(r.created_at), l.order_id""",
            (shipment_id,),
        )
        order_ids = [row[0] for row in cursor.fetchall()]

    summary = {"shipped": [], "still_waiting": []}
    for order_id in order_ids:
        try:
            ship_order(connection, order_id, t)
            summary["shipped"].append(order_id)
        except OrderStateError:
            summary["still_waiting"].append(order_id)
    return summary
