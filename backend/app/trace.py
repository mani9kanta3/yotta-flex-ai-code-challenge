"""
Everything that happened to one order, from placing it to the stock leaving the shelf.

The chain is order, line, decision, allocation, reservation, shipment, movement. Each step
is found through the IDs stored on the rows, so if a link were missing it would show here
as an empty list, not as a guess.
"""

from psycopg2.extras import RealDictCursor


def fetch_all(connection, sql, params):
    """Rows as dicts, so the API can return them as they are."""
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def get_order_trace(connection, order_id):
    """Returns the whole chain, or None if there is no such order."""
    orders = fetch_all(connection, """SELECT * FROM orders WHERE order_id = %s""", (order_id,))
    if not orders:
        return None

    lines = fetch_all(connection, """SELECT l.*, s.size, s.colour, p.product_name
                                     FROM order_line l
                                     JOIN sku s USING (sku_id)
                                     JOIN product p USING (product_id)
                                     WHERE l.order_id = %s ORDER BY l.line_no""", (order_id,))

    decisions = fetch_all(connection, """SELECT d.* FROM allocation_decision d
                                         JOIN order_line l USING (order_line_id)
                                         WHERE l.order_id = %s
                                         ORDER BY d.decided_at, d.decision_id""", (order_id,))

    reservations = fetch_all(connection, """SELECT r.*, a.allocation_id, a.shipment_id AS customer_shipment_id
                                            FROM reservation r
                                            JOIN order_line l USING (order_line_id)
                                            LEFT JOIN order_allocation a USING (reservation_id)
                                            WHERE l.order_id = %s ORDER BY r.reservation_id""", (order_id,))

    reservation_events = fetch_all(connection, """SELECT e.* FROM reservation_event e
                                                  JOIN reservation r USING (reservation_id)
                                                  JOIN order_line l USING (order_line_id)
                                                  WHERE l.order_id = %s
                                                  ORDER BY e.occurred_at, e.event_id""", (order_id,))

    order_events = fetch_all(connection, """SELECT * FROM order_event WHERE order_id = %s
                                            ORDER BY occurred_at, event_id""", (order_id,))

    # A customer parcel names the order. A truck is found through a hold waiting on it.
    shipments = fetch_all(connection, """SELECT s.* FROM shipment s WHERE s.order_id = %s
                                         UNION
                                         SELECT s.* FROM shipment s
                                         JOIN reservation r ON r.inbound_shipment_id = s.shipment_id
                                         JOIN order_line l USING (order_line_id)
                                         WHERE l.order_id = %s
                                         ORDER BY shipment_id""", (order_id, order_id))
    shipment_ids = [shipment["shipment_id"] for shipment in shipments]
    shipment_events = fetch_all(connection, """SELECT * FROM shipment_event WHERE shipment_id = ANY(%s)
                                               ORDER BY occurred_at, event_id""", (shipment_ids,))

    movements = fetch_all(connection, """SELECT m.* FROM inventory_movement m
                                         JOIN order_allocation a USING (allocation_id)
                                         JOIN order_line l USING (order_line_id)
                                         WHERE l.order_id = %s
                                         ORDER BY m.occurred_at, m.movement_id""", (order_id,))
    sale_ids = [movement["movement_id"] for movement in movements]
    returns = fetch_all(connection, """SELECT * FROM inventory_movement
                                       WHERE related_movement_id = ANY(%s)
                                       ORDER BY occurred_at, movement_id""", (sale_ids,))

    return {
        "order": orders[0],
        "lines": lines,
        "decisions": decisions,
        "reservations": reservations,
        "reservation_events": reservation_events,
        "order_events": order_events,
        "shipments": shipments,
        "shipment_events": shipment_events,
        "movements": movements,
        "returns": returns,
        "fully_traceable": is_fully_traceable(reservations, movements),
    }


def is_fully_traceable(reservations, movements):
    """Every hold that was used up has to lead to a sale of exactly that stock, and on to a parcel."""
    sales_by_allocation = {movement["allocation_id"]: movement for movement in movements}
    for reservation in reservations:
        if reservation["status"] == "CONSUMED":
            sale = sales_by_allocation.get(reservation["allocation_id"])
            if sale is None or sale["movement_type"] != "SALE" or sale["shipment_id"] is None:
                return False
            if sale["sku_id"] != reservation["sku_id"] or sale["location_id"] != reservation["location_id"]:
                return False
            if sale["quantity"] != -reservation["qty"]:
                return False
    return True
