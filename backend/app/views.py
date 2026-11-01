"""
The read-only questions the API answers. Plain SQL, returned as lists of dicts.

Nothing here changes data. The functions that do are in orders.py, reservations.py,
shipping.py, transfers.py, late_trucks.py and returns.py.
"""

from datetime import timedelta

from . import config
from .trace import fetch_all

PAGE_LIMIT = 200

ROW_COUNT_TABLES = ["sku", "location", "sku_location", "inventory_movement", "orders", "order_line", "reservation",
                    "shipment", "inventory_exception"]

ISSUE_NOTES = {
    "BALANCE_NOT_RECONCILED": "The stored closing balance differs from opening stock plus the ledger.",
    "RESERVATION_EXCEEDS_AVAILABLE": "This hold was above what could be promised when it was placed.",
    "FULFILLED_FROM_BLOCKED": "An order was shipped from damaged stock.",
    "TRANSFER_DOUBLE_COUNTED": "Stock reached the destination before it left the source, so it counted at both ends.",
}


def current_time(connection):
    """
    The simulated clock. Nothing reads the real time, so results stay repeatable. It starts
    at the end of the generated data and moves forward as live activity is written.
    """
    rows = fetch_all(connection, """SELECT GREATEST(%s,
                                        (SELECT MAX(occurred_at) FROM reservation_event),
                                        (SELECT MAX(occurred_at) FROM shipment_event),
                                        (SELECT MAX(occurred_at) FROM inventory_movement)) AS now""",
                     (config.AS_OF,))
    return rows[0]["now"]


def next_order_time(connection, order_id):
    """A minute after the order's last event, so a live action never lands before it."""
    rows = fetch_all(connection, "SELECT MAX(occurred_at) AS latest FROM order_event WHERE order_id = %s", (order_id,))
    latest = rows[0]["latest"]
    return latest + timedelta(minutes=1) if latest else current_time(connection)


def get_row_counts(connection):
    counts = {}
    for table in ROW_COUNT_TABLES:
        # The names come from the list above, never from input.
        counts[table] = fetch_all(connection, f"SELECT COUNT(*) AS rows FROM {table}", ())[0]["rows"]
    return counts


def get_overview(connection):
    """The numbers at the top of the first page, worked out now, including any live activity."""
    now = current_time(connection)
    statuses = fetch_all(connection, "SELECT status, COUNT(*) AS rows FROM orders GROUP BY status ORDER BY status", ())
    stock = fetch_all(connection, """SELECT COALESCE(SUM(on_hand), 0) AS opening_on_hand FROM inventory_balance
                                     WHERE snapshot_type = 'OPENING'""", ())[0]
    ledger = fetch_all(connection, """SELECT COALESCE(SUM(quantity), 0) AS total,
                                             COALESCE(SUM(quantity) FILTER (WHERE stock_bucket = 'BLOCKED'), 0) AS blocked
                                      FROM inventory_movement""", ())[0]
    opening_blocked = fetch_all(connection, """SELECT COALESCE(SUM(blocked), 0) AS units FROM inventory_balance
                                               WHERE snapshot_type = 'OPENING'""", ())[0]["units"]
    road = fetch_all(connection, """SELECT COUNT(*) AS trucks, COALESCE(SUM(transfer_qty), 0) AS units FROM shipment
                                    WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT'""", ())[0]
    open_holds = fetch_all(connection, """SELECT COUNT(*) AS holds, COALESCE(SUM(qty), 0) AS units FROM reservation
                                          WHERE status = 'CONFIRMED'
                                             OR (status = 'ACTIVE' AND expires_at > %s)""", (now,))[0]
    locations = fetch_all(connection, "SELECT location_type, COUNT(*) AS rows FROM location GROUP BY location_type", ())
    exceptions = fetch_all(connection, "SELECT issue_type, COUNT(*) AS rows FROM inventory_exception GROUP BY issue_type", ())
    return {
        "as_of": now,
        "row_counts": get_row_counts(connection),
        "on_hand_units": stock["opening_on_hand"] + ledger["total"],
        "blocked_units": opening_blocked + ledger["blocked"],
        "in_transit_trucks": road["trucks"],
        "in_transit_units": road["units"],
        "open_holds": open_holds["holds"],
        "reserved_units": open_holds["units"],
        "order_statuses": statuses,
        "locations_by_type": locations,
        "exceptions_by_type": exceptions,
    }


def get_options(connection):
    """What the forms on the page can choose from."""
    return {
        "channels": fetch_all(connection, "SELECT * FROM channel ORDER BY channel_code", ()),
        "regions": ["CENTRAL", "EAST", "NORTH", "SOUTH", "WEST"],
        "as_of": current_time(connection),
    }


def search_skus(connection, text, limit):
    """Matches an ID, a product or a brand. SKUs still on sale with the most stock come first."""
    pattern = f"%{text}%"
    return fetch_all(connection, """SELECT s.sku_id, p.product_name, p.brand, p.category, s.size, s.colour,
                                           s.unit_price, s.status, COUNT(b.location_id) AS locations
                                    FROM sku s
                                    JOIN product p USING (product_id)
                                    JOIN inventory_balance b ON b.sku_id = s.sku_id AND b.snapshot_type = 'CLOSING'
                                    WHERE s.sku_id ILIKE %s OR p.product_name ILIKE %s OR p.brand ILIKE %s
                                    GROUP BY s.sku_id, p.product_name, p.brand, p.category
                                    ORDER BY s.status = 'ACTIVE' DESC, SUM(b.on_hand) DESC, s.sku_id LIMIT %s""",
                     (pattern, pattern, pattern, min(limit, PAGE_LIMIT)))


def get_sku(connection, sku_id):
    """One SKU and every location that stocks it. None if there is no such SKU."""
    skus = fetch_all(connection, """SELECT s.*, p.product_name, p.brand, p.category
                                    FROM sku s JOIN product p USING (product_id)
                                    WHERE s.sku_id = %s""", (sku_id,))
    if not skus:
        return None
    locations = fetch_all(connection, """SELECT l.location_id, l.location_name, l.location_type, l.region, l.ships_online
                                         FROM sku_location sl JOIN location l USING (location_id)
                                         WHERE sl.sku_id = %s ORDER BY l.location_id""", (sku_id,))
    return dict(skus[0], stocked_at=locations)


def list_orders(connection, status, decision_code, search, limit, offset):
    """Newest first. A decision filter finds the interesting orders, like splits and rejections."""
    conditions = ["TRUE"]
    params = []
    if status:
        conditions.append("o.status = %s")
        params.append(status)
    if decision_code:
        conditions.append("""EXISTS (SELECT 1 FROM order_line l JOIN allocation_decision d USING (order_line_id)
                                     WHERE l.order_id = o.order_id AND d.decision_code = %s)""")
        params.append(decision_code)
    if search:
        conditions.append("""(o.order_id ILIKE %s OR EXISTS (SELECT 1 FROM order_line l
                                                             WHERE l.order_id = o.order_id AND l.sku_id ILIKE %s))""")
        params += [f"%{search}%", f"%{search}%"]
    # The conditions above are our own text. Everything from input goes in as a parameter.
    where = " AND ".join(conditions)
    total = fetch_all(connection, f"SELECT COUNT(*) AS rows FROM orders o WHERE {where}", tuple(params))[0]["rows"]
    rows = fetch_all(connection, f"""
        SELECT o.order_id, o.channel_code, o.ship_to_region, o.placed_at, o.status, o.order_total,
               (SELECT COUNT(*) FROM order_line l WHERE l.order_id = o.order_id) AS lines,
               (SELECT string_agg(DISTINCT d.decision_code, ', ')
                FROM order_line l JOIN allocation_decision d USING (order_line_id)
                WHERE l.order_id = o.order_id) AS decisions
        FROM orders o WHERE {where}
        ORDER BY o.order_id DESC LIMIT %s OFFSET %s""", tuple(params) + (min(limit, PAGE_LIMIT), offset))
    return {"total": total, "orders": rows}


def get_due_holds(connection, as_of):
    """Holds that have run out by this time. These are what an expiry run would release."""
    return fetch_all(connection, """SELECT r.reservation_id, r.sku_id, r.location_id, r.qty, r.expires_at,
                                           o.order_id, o.channel_code
                                    FROM reservation r
                                    JOIN order_line l USING (order_line_id)
                                    JOIN orders o USING (order_id)
                                    WHERE r.status = 'ACTIVE' AND r.expires_at <= %s
                                    ORDER BY r.expires_at, r.reservation_id LIMIT %s""", (as_of, PAGE_LIMIT))


def get_ledger(connection, sku_id, location_id, limit):
    """
    The ledger for one SKU at one location, with stock after every row, worked out from the
    opening balance. The stored closing balance sits beside it, so a difference shows.
    """
    balances = fetch_all(connection, """SELECT snapshot_type, on_hand, blocked, as_of_at, balance_id FROM inventory_balance
                                        WHERE sku_id = %s AND location_id = %s""", (sku_id, location_id))
    if not balances:
        return None
    by_type = {row["snapshot_type"]: row for row in balances}
    opening = by_type["OPENING"]
    closing = by_type.get("CLOSING")

    rows = fetch_all(connection, """
        SELECT * FROM (
            SELECT m.movement_id, m.occurred_at, m.movement_type, m.stock_bucket, m.quantity, m.reason_code, m.source_doc,
                   m.shipment_id, m.allocation_id, m.related_movement_id,
                   l.order_id,
                   %(on_hand)s + SUM(m.quantity) OVER running AS on_hand_after,
                   %(blocked)s + COALESCE(SUM(m.quantity) FILTER (WHERE m.stock_bucket = 'BLOCKED') OVER running, 0) AS blocked_after
            FROM inventory_movement m
            LEFT JOIN order_allocation a USING (allocation_id)
            LEFT JOIN order_line l ON l.order_line_id = a.order_line_id
            WHERE m.sku_id = %(sku_id)s AND m.location_id = %(location_id)s
            WINDOW running AS (ORDER BY m.occurred_at, m.movement_id)
        ) t ORDER BY occurred_at DESC, movement_id DESC""",
                     {"on_hand": opening["on_hand"], "blocked": opening["blocked"], "sku_id": sku_id,
                      "location_id": location_id})

    worked_out = rows[0]["on_hand_after"] if rows else opening["on_hand"]
    worked_out_blocked = rows[0]["blocked_after"] if rows else opening["blocked"]
    reconciles = closing is not None and closing["on_hand"] == worked_out and closing["blocked"] == worked_out_blocked
    return {
        "sku_id": sku_id,
        "location_id": location_id,
        "opening": opening,
        "stored_closing": closing,
        "worked_out_on_hand": worked_out,
        "worked_out_blocked": worked_out_blocked,
        "reconciles": reconciles,
        "total_rows": len(rows),
        "rows": rows[:min(limit, PAGE_LIMIT)],
        # Oldest first, and every row, because this is what draws the line on the page.
        "history": [{"at": row["occurred_at"], "on_hand": row["on_hand_after"]} for row in reversed(rows)],
    }


def list_exceptions(connection, issue_type, limit):
    """What the detectors found, with the SKU, location and order each one is about."""
    condition = "e.issue_type = %s" if issue_type else "TRUE"
    params = (issue_type, min(limit, PAGE_LIMIT)) if issue_type else (min(limit, PAGE_LIMIT),)
    rows = fetch_all(connection, f"""
        SELECT e.exception_id, e.issue_type, e.expected_value, e.actual_value, e.recommended_action, e.status,
               e.detected_at,
               COALESCE(e.balance_id, e.reservation_id, e.movement_id, e.shipment_id) AS record_id,
               COALESCE(b.sku_id, r.sku_id, m.sku_id, s.transfer_sku_id) AS sku_id,
               COALESCE(b.location_id, r.location_id, m.location_id, s.to_location_id) AS location_id,
               COALESCE(rl.order_id, ml.order_id) AS order_id
        FROM inventory_exception e
        LEFT JOIN inventory_balance b USING (balance_id)
        LEFT JOIN reservation r USING (reservation_id)
        LEFT JOIN order_line rl ON rl.order_line_id = r.order_line_id
        LEFT JOIN inventory_movement m USING (movement_id)
        LEFT JOIN order_allocation a ON a.allocation_id = m.allocation_id
        LEFT JOIN order_line ml ON ml.order_line_id = a.order_line_id
        LEFT JOIN shipment s ON s.shipment_id = e.shipment_id
        WHERE {condition}
        ORDER BY e.exception_id LIMIT %s""", params)
    for row in rows:
        row["explanation"] = ISSUE_NOTES.get(row["issue_type"], "")
    counts = fetch_all(connection, """SELECT issue_type, COUNT(*) AS rows FROM inventory_exception
                                      GROUP BY issue_type ORDER BY issue_type""", ())
    return {"counts": counts, "exceptions": rows}


def list_trucks(connection, status, sku_id, limit):
    """Stock transfers, soonest due first, with the current ETA from their events."""
    conditions = ["s.shipment_type = 'TRANSFER'", "s.status = %s"]
    params = [status]
    if sku_id:
        conditions.append("s.transfer_sku_id = %s")
        params.append(sku_id)
    now = current_time(connection)
    where = " AND ".join(conditions)
    total = fetch_all(connection, f"SELECT COUNT(*) AS rows FROM shipment s WHERE {where}", tuple(params))[0]["rows"]
    rows = fetch_all(connection, f"""
        SELECT s.shipment_id, s.status, s.transfer_sku_id AS sku_id, s.transfer_qty AS qty,
               s.from_location_id, s.to_location_id, s.created_at, s.original_eta_at,
               COALESCE((SELECT e.eta_at FROM shipment_event e
                         WHERE e.shipment_id = s.shipment_id AND e.eta_at IS NOT NULL
                         ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1), s.original_eta_at) AS current_eta_at,
               (SELECT COALESCE(SUM(r.qty), 0) FROM reservation r
                WHERE r.inbound_shipment_id = s.shipment_id AND r.status IN ('ACTIVE', 'CONFIRMED')) AS units_held
        FROM shipment s WHERE {where}
        ORDER BY current_eta_at, s.shipment_id LIMIT %s""", tuple(params) + (min(limit, PAGE_LIMIT),))
    return {"as_of": now, "total": total, "trucks": rows}
