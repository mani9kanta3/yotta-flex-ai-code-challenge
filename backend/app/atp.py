"""
Available-to-promise, worked out in SQL for any point in time.

Everything comes from the ledger and the event tables, up to time t. Nothing
reads a status column, because a status column only knows about now. Asking about
last week has to give the answer it would have given last week.

The arithmetic itself is in rules.py, so there is one written version of it.
"""

from datetime import timedelta

from . import rules

STOCK_SQL = """
SELECT b.on_hand + COALESCE(SUM(m.quantity), 0),
       b.blocked + COALESCE(SUM(m.quantity) FILTER (WHERE m.stock_bucket = 'BLOCKED'), 0)
FROM inventory_balance b
LEFT JOIN inventory_movement m
       ON m.sku_id = b.sku_id AND m.location_id = b.location_id AND m.occurred_at <= %(t)s
WHERE b.sku_id = %(sku_id)s AND b.location_id = %(location_id)s AND b.snapshot_type = 'OPENING'
GROUP BY b.on_hand, b.blocked
"""

# JOIN LATERAL finds the latest event up to t for each reservation. A reservation
# with no event yet at t drops out of the join, so it holds nothing.
RESERVED_SQL = """
SELECT COALESCE(SUM(r.qty), 0)
FROM reservation r
JOIN LATERAL (
    SELECT e.to_status
    FROM reservation_event e
    WHERE e.reservation_id = r.reservation_id AND e.occurred_at <= %(t)s
    ORDER BY e.occurred_at DESC, e.event_id DESC
    LIMIT 1
) latest ON TRUE
WHERE r.sku_id = %(sku_id)s AND r.location_id = %(location_id)s
  AND (latest.to_status = 'CONFIRMED'
       OR (latest.to_status = 'ACTIVE' AND %(t)s < r.expires_at))
"""

# A truck counts as inbound only if it is on the road at t and its current ETA at
# t is due within the horizon. An overdue truck no longer counts. held is what
# open reservations already have on it.
INBOUND_SQL = """
SELECT s.shipment_id, s.transfer_qty, eta.eta_at, COALESCE(pegged.held, 0)
FROM shipment s
JOIN LATERAL (
    SELECT e.to_status
    FROM shipment_event e
    WHERE e.shipment_id = s.shipment_id AND e.occurred_at <= %(t)s
    ORDER BY e.occurred_at DESC, e.event_id DESC
    LIMIT 1
) latest ON latest.to_status = 'IN_TRANSIT'
JOIN LATERAL (
    SELECT COALESCE(
        (SELECT e.eta_at FROM shipment_event e
         WHERE e.shipment_id = s.shipment_id AND e.occurred_at <= %(t)s AND e.eta_at IS NOT NULL
         ORDER BY e.occurred_at DESC, e.event_id DESC
         LIMIT 1),
        s.original_eta_at) AS eta_at
) eta ON TRUE
LEFT JOIN LATERAL (
    SELECT SUM(r.qty) AS held
    FROM reservation r
    JOIN LATERAL (
        SELECT e.to_status
        FROM reservation_event e
        WHERE e.reservation_id = r.reservation_id AND e.occurred_at <= %(t)s
        ORDER BY e.occurred_at DESC, e.event_id DESC
        LIMIT 1
    ) r_latest ON TRUE
    WHERE r.inbound_shipment_id = s.shipment_id
      AND (r_latest.to_status = 'CONFIRMED'
           OR (r_latest.to_status = 'ACTIVE' AND %(t)s < r.expires_at))
) pegged ON TRUE
WHERE s.shipment_type = 'TRANSFER'
  AND s.transfer_sku_id = %(sku_id)s
  AND s.to_location_id = %(location_id)s
  AND eta.eta_at >= %(t)s
  AND eta.eta_at <= %(horizon)s
ORDER BY eta.eta_at, s.shipment_id
"""

# Master data only, with no stock numbers. An order runs this before it takes any
# lock, to find out which rows it needs to lock.
CANDIDATES_SQL = """
SELECT sl.location_id, l.location_type, l.region, cl.buffer_units,
       rule.safety_stock, rule.allow_inbound_atp
FROM sku_location sl
JOIN sku s ON s.sku_id = sl.sku_id
JOIN location l ON l.location_id = sl.location_id
JOIN channel_location cl ON cl.location_id = sl.location_id AND cl.channel_code = %(channel_code)s
JOIN replenishment_rule rule ON rule.sku_id = sl.sku_id AND rule.location_id = sl.location_id
WHERE sl.sku_id = %(sku_id)s
  AND sl.ranged_from <= %(t)s::date
  AND s.effective_from <= %(t)s::date AND (s.effective_to IS NULL OR %(t)s::date < s.effective_to)
  AND l.opened_on <= %(t)s::date AND (l.closed_on IS NULL OR %(t)s::date < l.closed_on)
  AND cl.effective_from <= %(t)s::date AND (cl.effective_to IS NULL OR %(t)s::date < cl.effective_to)
  AND rule.effective_from <= %(t)s::date AND (rule.effective_to IS NULL OR %(t)s::date < rule.effective_to)
ORDER BY sl.location_id
"""

CHANNEL_SQL = """
SELECT is_online, counts_inbound, applies_safety_stock, hold_minutes
FROM channel WHERE channel_code = %(channel_code)s
"""


def get_channel(cursor, channel_code):
    cursor.execute(CHANNEL_SQL, {"channel_code": channel_code})
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"unknown channel {channel_code}")
    return {"is_online": row[0], "counts_inbound": row[1],
            "applies_safety_stock": row[2], "hold_minutes": row[3]}


def get_stock(cursor, sku_id, location_id, t):
    """Returns (on_hand, blocked) at time t."""
    cursor.execute(STOCK_SQL, {"sku_id": sku_id, "location_id": location_id, "t": t})
    row = cursor.fetchone()
    return (row[0], row[1]) if row else (0, 0)


def get_reserved(cursor, sku_id, location_id, t):
    cursor.execute(RESERVED_SQL, {"sku_id": sku_id, "location_id": location_id, "t": t})
    return cursor.fetchone()[0]


def get_inbound_shipments(cursor, sku_id, location_id, t):
    horizon = t + timedelta(hours=rules.INBOUND_HORIZON_HOURS)
    cursor.execute(INBOUND_SQL, {"sku_id": sku_id, "location_id": location_id, "t": t, "horizon": horizon})
    return [
        {"shipment_id": row[0], "qty": row[1], "eta_at": row[2], "free_qty": row[1] - row[3]}
        for row in cursor.fetchall()
    ]


def get_candidates(cursor, sku_id, channel_code, t):
    """Where this channel could sell this SKU at time t, from master data alone."""
    cursor.execute(CANDIDATES_SQL, {"sku_id": sku_id, "channel_code": channel_code, "t": t})
    return [
        {"location_id": row[0], "location_type": row[1], "region": row[2], "buffer_units": row[3],
         "safety_stock": row[4], "allow_inbound_atp": row[5]}
        for row in cursor.fetchall()
    ]


def get_positions(cursor, sku_id, channel_code, t):
    """Every number for each eligible location, ready for rules.allocate."""
    channel = get_channel(cursor, channel_code)
    positions = []
    for candidate in get_candidates(cursor, sku_id, channel_code, t):
        location_id = candidate["location_id"]
        on_hand, blocked = get_stock(cursor, sku_id, location_id, t)
        reserved = get_reserved(cursor, sku_id, location_id, t)

        shipments = []
        if candidate["allow_inbound_atp"]:
            shipments = get_inbound_shipments(cursor, sku_id, location_id, t)
        inbound = sum(shipment["qty"] for shipment in shipments)

        position = rules.position(on_hand, blocked, reserved, inbound, candidate["safety_stock"],
                                  candidate["buffer_units"], channel["counts_inbound"],
                                  channel["applies_safety_stock"])
        position["location_id"] = location_id
        position["location_type"] = candidate["location_type"]
        position["region"] = candidate["region"]
        # A channel that ignores inbound must not be offered a truck to wait for.
        position["inbound_shipments"] = shipments if channel["counts_inbound"] else []
        positions.append(position)
    return positions


def network_promisable(positions):
    """Summed after each location's own buffer and safety stock have been taken off."""
    return sum(position["promisable"] for position in positions)
