"""
The four detectors, as SQL over the same tables anyone could query.

Each one recalculates what should be true from the ledger and the events, and returns
the records where the stored data disagrees, as (record id, expected, actual). None of
them trusts a status column or a logged decision, and none of them knows what was
injected: nothing in app/ reads the answers.
"""

from app import config, rules

# Only movements up to the snapshot's own time, because a later sale doesn't make it wrong.
BALANCE_SQL = """
SELECT c.balance_id, o.on_hand + COALESCE(m.total_change, 0), c.on_hand
FROM inventory_balance c
JOIN inventory_balance o
  ON o.sku_id = c.sku_id AND o.location_id = c.location_id AND o.snapshot_type = 'OPENING'
LEFT JOIN LATERAL (
    SELECT SUM(quantity) AS total_change,
           SUM(quantity) FILTER (WHERE stock_bucket = 'BLOCKED') AS blocked_change
    FROM inventory_movement
    WHERE sku_id = c.sku_id AND location_id = c.location_id AND occurred_at <= c.as_of_at) m ON TRUE
WHERE c.snapshot_type = 'CLOSING'
  AND (c.on_hand <> o.on_hand + COALESCE(m.total_change, 0)
       OR c.blocked <> o.blocked + COALESCE(m.blocked_change, 0))
ORDER BY c.balance_id
"""

# What could be promised just before this hold, by the same rules as app/atp.py: stock up to
# that moment, less blocked, less every other hold then, less safety stock and buffer. A truck
# only counts for a hold on a truck, on a channel and a rule that allow it, while it is on the
# road and its latest ETA is due within the horizon. A channel or rule not in effect then has
# nothing to promise.
RESERVATION_SQL = f"""
SELECT reservation_id, available, qty FROM (
    SELECT r.reservation_id, r.qty,
           CASE WHEN cl.channel_location_id IS NULL OR rule.rule_id IS NULL THEN 0
                ELSE GREATEST(0, stock.on_hand - stock.blocked - others.held + COALESCE(trucks.inbound, 0)
                                 - CASE WHEN ch.applies_safety_stock THEN rule.safety_stock ELSE 0 END - cl.buffer_units)
           END AS available
    FROM reservation r
    JOIN order_line l USING (order_line_id)
    JOIN orders o USING (order_id)
    JOIN channel ch USING (channel_code)
    LEFT JOIN channel_location cl
      ON cl.channel_code = o.channel_code AND cl.location_id = r.location_id
     AND cl.effective_from <= r.created_at::date AND (cl.effective_to IS NULL OR r.created_at::date < cl.effective_to)
    LEFT JOIN replenishment_rule rule
      ON rule.sku_id = r.sku_id AND rule.location_id = r.location_id
     AND rule.effective_from <= r.created_at::date AND (rule.effective_to IS NULL OR r.created_at::date < rule.effective_to)
    JOIN inventory_balance b
      ON b.sku_id = r.sku_id AND b.location_id = r.location_id AND b.snapshot_type = 'OPENING'
    JOIN LATERAL (
        SELECT b.on_hand + COALESCE(SUM(m.quantity), 0) AS on_hand,
               b.blocked + COALESCE(SUM(m.quantity) FILTER (WHERE m.stock_bucket = 'BLOCKED'), 0) AS blocked
        FROM inventory_movement m
        WHERE m.sku_id = r.sku_id AND m.location_id = r.location_id AND m.occurred_at <= r.created_at
    ) stock ON TRUE
    JOIN LATERAL (
        SELECT COALESCE(SUM(other.qty), 0) AS held
        FROM reservation other
        JOIN LATERAL (
            SELECT e.to_status FROM reservation_event e
            WHERE e.reservation_id = other.reservation_id AND e.occurred_at <= r.created_at
            ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1
        ) latest ON TRUE
        WHERE other.sku_id = r.sku_id AND other.location_id = r.location_id
          AND other.reservation_id <> r.reservation_id
          -- Two holds placed in the same second: the first one didn't see the second.
          AND NOT (other.created_at = r.created_at AND other.reservation_id > r.reservation_id)
          AND (latest.to_status = 'CONFIRMED' OR (latest.to_status = 'ACTIVE' AND r.created_at < other.expires_at))
    ) others ON TRUE
    LEFT JOIN LATERAL (
        SELECT SUM(truck.transfer_qty) AS inbound
        FROM shipment truck
        JOIN LATERAL (
            SELECT e.to_status FROM shipment_event e
            WHERE e.shipment_id = truck.shipment_id AND e.occurred_at <= r.created_at
            ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1
        ) latest ON latest.to_status = 'IN_TRANSIT'
        JOIN LATERAL (
            SELECT COALESCE(
                (SELECT e.eta_at FROM shipment_event e
                 WHERE e.shipment_id = truck.shipment_id AND e.occurred_at <= r.created_at AND e.eta_at IS NOT NULL
                 ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1),
                truck.original_eta_at) AS eta_at
        ) eta ON TRUE
        WHERE r.supply_type = 'INBOUND' AND ch.counts_inbound AND rule.allow_inbound_atp
          AND truck.shipment_type = 'TRANSFER'
          AND truck.transfer_sku_id = r.sku_id AND truck.to_location_id = r.location_id
          AND eta.eta_at >= r.created_at
          AND eta.eta_at <= r.created_at + INTERVAL '{rules.INBOUND_HORIZON_HOURS} hours'
    ) trucks ON TRUE
    WHERE r.status <> 'REJECTED'
) checked
WHERE qty > available
ORDER BY reservation_id
"""

# A sale has to come off the shelf. Expected units from blocked stock are always zero.
BLOCKED_SQL = """
SELECT movement_id, 0, -quantity
FROM inventory_movement
WHERE movement_type = 'SALE' AND stock_bucket = 'BLOCKED'
ORDER BY movement_id
"""

# Arrived at or before it left, so for that time the units count at both ends.
TRANSFER_SQL = """
SELECT arrival.shipment_id, 0, arrival.quantity
FROM inventory_movement arrival
JOIN inventory_movement departure
  ON departure.shipment_id = arrival.shipment_id AND departure.movement_type = 'TRANSFER_OUT'
WHERE arrival.movement_type = 'TRANSFER_IN' AND arrival.occurred_at <= departure.occurred_at
ORDER BY arrival.shipment_id
"""

# issue type, its SQL, which column of inventory_exception it points at, what to do.
DETECTORS = [
    ("BALANCE_NOT_RECONCILED", BALANCE_SQL, "balance_id", "RECOUNT_AND_TRUST_THE_LEDGER"),
    ("RESERVATION_EXCEEDS_AVAILABLE", RESERVATION_SQL, "reservation_id", "CANCEL_OR_REALLOCATE"),
    ("FULFILLED_FROM_BLOCKED", BLOCKED_SQL, "movement_id", "QUARANTINE_AND_REVIEW"),
    ("TRANSFER_DOUBLE_COUNTED", TRANSFER_SQL, "shipment_id", "EXCLUDE_FROM_ATP_UNTIL_RECEIVED"),
]


def find_exceptions(cursor):
    """Everything each detector finds, as a list of dicts, with no writing."""
    exceptions = []
    for issue_type, sql, id_column, action in DETECTORS:
        cursor.execute(sql)
        for record_id, expected_value, actual_value in cursor.fetchall():
            exceptions.append({
                "issue_type": issue_type,
                "id_column": id_column,
                "record_id": record_id,
                "expected_value": expected_value,
                "actual_value": actual_value,
                "recommended_action": action,
            })
    return exceptions


def write_exceptions(cursor, exceptions):
    """Replaces the last run's results, so running it twice gives one set of answers."""
    cursor.execute("DELETE FROM inventory_exception")
    for number, exception in enumerate(exceptions, start=1):
        # The column comes from DETECTORS above, never from input.
        cursor.execute(
            f"""INSERT INTO inventory_exception (exception_id, issue_type, {exception['id_column']}, expected_value,
                                                 actual_value, recommended_action, detected_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (f"EXC-{number:06d}", exception["issue_type"], exception["record_id"], exception["expected_value"],
             exception["actual_value"], exception["recommended_action"], config.AS_OF),
        )
