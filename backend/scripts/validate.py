"""
Checks the loaded data and writes validation_summary.json.

Run:  python -m scripts.validate --scale starter

Each check counts rows breaking one rule, so 0 is a pass. If anything fails, the
command exits with code 1, so a script or CI can't miss it.

Tables the generator doesn't fill yet are listed as not generated. They are never
reported as a pass on their own.
"""

import argparse
import json
import sys
from pathlib import Path

from app import atp, config, db, detect, rules
from generator.export import TABLE_COLUMNS, clean_dir_of, sha256_of
from generator.settings import NOT_GENERATED_YET, SCALES

# How a stock level moves over time, one row at a time in ledger order.
RUNNING_STOCK = """
    SELECT m.sku_id, m.location_id, m.stock_bucket,
           (CASE WHEN m.stock_bucket = 'SELLABLE' THEN b.on_hand - b.blocked ELSE b.blocked END)
           + SUM(m.quantity) OVER (
               PARTITION BY m.sku_id, m.location_id, m.stock_bucket
               ORDER BY m.occurred_at, m.movement_id
           ) AS stock_after
    FROM inventory_movement m
    JOIN inventory_balance b
      ON b.sku_id = m.sku_id AND b.location_id = m.location_id AND b.snapshot_type = 'OPENING'
"""


def allowed_pairs_sql(transitions):
    """The transition rules from rules.py, written as a SQL VALUES list."""
    pairs = []
    for from_status, to_statuses in transitions.items():
        left = "NULL::text" if from_status is None else f"'{from_status}'"
        for to_status in sorted(to_statuses):
            pairs.append(f"({left}, '{to_status}')")
    return ", ".join(pairs)


def illegal_transition_sql(table, transitions, where="TRUE"):
    return f"""
        SELECT COUNT(*) FROM {table} e
        WHERE {where} AND NOT EXISTS (
            SELECT 1 FROM (VALUES {allowed_pairs_sql(transitions)}) AS allowed (from_status, to_status)
            WHERE allowed.from_status IS NOT DISTINCT FROM e.from_status
              AND allowed.to_status = e.to_status)"""


def broken_chain_sql(table, key, where="TRUE"):
    """Each event has to start from the status the one before it ended on."""
    return f"""
        SELECT COUNT(*) FROM (
            SELECT from_status,
                   LAG(to_status) OVER (PARTITION BY {key} ORDER BY occurred_at, event_id) AS previous
            FROM {table} WHERE {where}) t
        WHERE t.from_status IS DISTINCT FROM t.previous"""


def status_differs_sql(table, key, event_table, event_key, where="TRUE"):
    """The status column is only a copy of the latest event, so they have to agree."""
    return f"""
        SELECT COUNT(*) FROM {table} p
        JOIN LATERAL (
            SELECT e.to_status FROM {event_table} e
            WHERE e.{event_key} = p.{key} AND {where}
            ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1) latest ON TRUE
        WHERE p.status <> latest.to_status"""


CHECKS = [
    ("movement_id format", """
        SELECT COUNT(*) FROM inventory_movement WHERE movement_id !~ '^MOV-2026-[0-9]{8}$'"""),
    ("sku_id format", """
        SELECT COUNT(*) FROM sku WHERE sku_id !~ '^SKU-[0-9]{6}$'"""),
    ("location_id format", """
        SELECT COUNT(*) FROM location WHERE location_id !~ '^LOC-[0-9]{6}$'"""),
    ("order_id format", """
        SELECT COUNT(*) FROM orders WHERE order_id !~ '^ORD-2026-[0-9]{6}$'"""),
    ("reservation_id format", """
        SELECT COUNT(*) FROM reservation r
        JOIN order_line l USING (order_line_id)
        WHERE r.reservation_id !~ ('^RES-' || l.order_id || '-[0-9]{2}$')"""),
    ("shipment_id format", """
        SELECT COUNT(*) FROM shipment WHERE shipment_id !~ '^SHP-2026-[0-9]{6}$'"""),

    ("sku without a valid product", """
        SELECT COUNT(*) FROM sku s LEFT JOIN product p USING (product_id) WHERE p.product_id IS NULL"""),
    ("movement without a stocked pair", """
        SELECT COUNT(*) FROM inventory_movement m
        LEFT JOIN sku_location l USING (sku_id, location_id) WHERE l.sku_id IS NULL"""),
    ("stocked pair without an opening balance", """
        SELECT COUNT(*) FROM sku_location l
        LEFT JOIN inventory_balance b ON b.sku_id = l.sku_id AND b.location_id = l.location_id
             AND b.snapshot_type = 'OPENING'
        WHERE b.balance_id IS NULL"""),
    # The reconcile check below starts from CLOSING rows, so on its own it can't
    # see a closing balance that is missing altogether.
    ("stocked pair without a closing balance", """
        SELECT COUNT(*) FROM sku_location l
        LEFT JOIN inventory_balance b ON b.sku_id = l.sku_id AND b.location_id = l.location_id
             AND b.snapshot_type = 'CLOSING'
        WHERE b.balance_id IS NULL"""),
    ("balance snapshot at the wrong time", f"""
        SELECT COUNT(*) FROM inventory_balance
        WHERE (snapshot_type = 'OPENING' AND as_of_at <> '{config.START_AT}')
           OR (snapshot_type = 'CLOSING' AND as_of_at <> '{config.AS_OF}')"""),

    ("duplicate source doc on single-row movements", """
        SELECT COUNT(*) FROM (
            SELECT source_doc FROM inventory_movement WHERE movement_group_id IS NULL
            GROUP BY source_doc HAVING COUNT(*) > 1) d"""),

    ("sale with a zero or positive quantity", """
        SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'SALE' AND quantity >= 0"""),

    ("BLOCK or UNBLOCK group that is not a matching pair", """
        SELECT COUNT(*) FROM (
            SELECT movement_group_id FROM inventory_movement
            WHERE movement_group_id IS NOT NULL
            GROUP BY movement_group_id
            HAVING COUNT(*) <> 2 OR SUM(quantity) <> 0
                OR COUNT(DISTINCT sku_id) <> 1 OR COUNT(DISTINCT location_id) <> 1
                OR COUNT(DISTINCT occurred_at) <> 1 OR COUNT(DISTINCT stock_bucket) <> 2) g"""),

    ("BLOCK or UNBLOCK that changes total stock", """
        SELECT COUNT(*) FROM (
            SELECT sku_id, location_id FROM inventory_movement
            WHERE movement_type IN ('BLOCK', 'UNBLOCK')
            GROUP BY sku_id, location_id HAVING SUM(quantity) <> 0) t"""),

    ("stock below zero at any point in the ledger", f"""
        SELECT COUNT(*) FROM ({RUNNING_STOCK}) r WHERE r.stock_after < 0"""),

    ("movement before the pair went live", """
        SELECT COUNT(*) FROM inventory_movement m
        JOIN sku_location l USING (sku_id, location_id)
        WHERE m.occurred_at::date < l.ranged_from"""),
    # An online order placed in time can still ship after the SKU stops, so this is
    # walk-in sales only. The check below covers when an order was placed.
    ("walk-in sale after a discontinued SKU stopped", """
        SELECT COUNT(*) FROM inventory_movement m JOIN sku s USING (sku_id)
        WHERE m.movement_type = 'SALE' AND m.allocation_id IS NULL AND s.effective_to IS NOT NULL
          AND m.occurred_at::date >= s.effective_to"""),
    ("stock allocated after a discontinued SKU stopped", """
        SELECT COUNT(*) FROM order_allocation a
        JOIN order_line l USING (order_line_id)
        JOIN sku s USING (sku_id)
        WHERE s.effective_to IS NOT NULL AND a.allocated_at::date >= s.effective_to"""),
    ("movement outside the simulated period", f"""
        SELECT COUNT(*) FROM inventory_movement
        WHERE occurred_at < '{config.START_AT}' OR occurred_at > '{config.AS_OF}'"""),
    ("movement ids not in time order", """
        SELECT COUNT(*) FROM (
            SELECT occurred_at, LAG(occurred_at) OVER (ORDER BY movement_id) AS previous
            FROM inventory_movement) t
        WHERE t.occurred_at < t.previous"""),

    ("reservation with no event", """
        SELECT COUNT(*) FROM reservation r
        LEFT JOIN reservation_event e USING (reservation_id) WHERE e.event_id IS NULL"""),
    ("reservation event with an illegal transition",
        illegal_transition_sql("reservation_event", rules.RESERVATION_TRANSITIONS)),
    ("reservation events that do not chain",
        broken_chain_sql("reservation_event", "reservation_id")),
    ("reservation status differs from its latest event",
        status_differs_sql("reservation", "reservation_id", "reservation_event", "reservation_id")),
    ("order event with an illegal transition",
        illegal_transition_sql("order_event", rules.ORDER_TRANSITIONS, "e.order_line_id IS NULL")),
    ("order line event with an illegal transition",
        illegal_transition_sql("order_event", rules.LINE_TRANSITIONS, "e.order_line_id IS NOT NULL")),
    ("order status differs from its latest event",
        status_differs_sql("orders", "order_id", "order_event", "order_id", "e.order_line_id IS NULL")),
    ("order line status differs from its latest event",
        status_differs_sql("order_line", "order_line_id", "order_event", "order_line_id")),
    ("order total differs from its lines", """
        SELECT COUNT(*) FROM orders o
        JOIN (SELECT order_id, SUM(line_amount) FILTER (WHERE status <> 'REJECTED') AS total
              FROM order_line GROUP BY order_id) l USING (order_id)
        WHERE o.order_total <> COALESCE(l.total, 0)"""),
    # A hold moved off a late truck keeps its old allocation row as history.
    ("allocated line whose allocations do not add up to its quantity", """
        SELECT COUNT(*) FROM order_line l
        LEFT JOIN (SELECT a.order_line_id, SUM(a.qty) AS allocated
                   FROM order_allocation a JOIN reservation r USING (reservation_id)
                   WHERE r.status NOT IN ('CANCELLED', 'EXPIRED')
                   GROUP BY a.order_line_id) a USING (order_line_id)
        WHERE l.status IN ('ALLOCATED', 'SHIPPED') AND COALESCE(a.allocated, 0) <> l.qty"""),
    # Replays each line's holds in order. A hold moved to another location has to let go
    # of the old stock first, or this line would be holding more than it asked for.
    ("line holding more than its quantity at any moment", """
        SELECT COUNT(*) FROM (
            SELECT r.order_line_id, l.qty AS line_qty,
                   SUM(CASE WHEN e.to_status = 'ACTIVE' THEN r.qty
                            WHEN e.to_status IN ('CANCELLED', 'EXPIRED', 'CONSUMED') THEN -r.qty
                            ELSE 0 END)
                       OVER (PARTITION BY r.order_line_id ORDER BY e.occurred_at, e.event_id) AS holding
            FROM reservation_event e
            JOIN reservation r USING (reservation_id)
            JOIN order_line l USING (order_line_id)) t
        WHERE t.holding > t.line_qty"""),
    ("open holds above the stock on the shelf at the end", """
        SELECT COUNT(*) FROM (
            SELECT r.sku_id, r.location_id, SUM(r.qty) AS held
            FROM reservation r
            LEFT JOIN shipment s ON s.shipment_id = r.inbound_shipment_id
            WHERE r.status IN ('ACTIVE', 'CONFIRMED') AND (r.supply_type = 'ON_HAND' OR s.status = 'RECEIVED')
            GROUP BY r.sku_id, r.location_id) h
        JOIN inventory_balance b
          ON b.sku_id = h.sku_id AND b.location_id = h.location_id AND b.snapshot_type = 'CLOSING'
        WHERE h.held > b.on_hand - b.blocked"""),
    ("hold confirmed at or after it expired", """
        SELECT COUNT(*) FROM reservation_event e JOIN reservation r USING (reservation_id)
        WHERE e.from_status = 'ACTIVE' AND e.to_status = 'CONFIRMED' AND e.occurred_at >= r.expires_at"""),
    ("expired hold not dated at its expiry", """
        SELECT COUNT(*) FROM reservation_event e JOIN reservation r USING (reservation_id)
        WHERE e.to_status = 'EXPIRED' AND e.occurred_at <> r.expires_at"""),
    ("first promise on the reservation differs from its first event", """
        SELECT COUNT(*) FROM reservation r
        JOIN reservation_event e ON e.reservation_id = r.reservation_id AND e.from_status IS NULL
        WHERE r.promised_ship_by IS DISTINCT FROM e.promised_ship_by"""),
    ("new promise that is not later than the one before", """
        SELECT COUNT(*) FROM (
            SELECT promised_ship_by,
                   LAG(promised_ship_by) OVER (PARTITION BY reservation_id ORDER BY occurred_at, event_id) AS previous
            FROM reservation_event WHERE promised_ship_by IS NOT NULL) t
        WHERE t.promised_ship_by <= t.previous"""),
    ("order sale that differs from its allocation", """
        SELECT COUNT(*) FROM inventory_movement m
        JOIN order_allocation a USING (allocation_id)
        JOIN order_line l USING (order_line_id)
        WHERE m.movement_type = 'SALE'
          AND (m.location_id <> a.location_id OR m.sku_id <> l.sku_id OR m.quantity <> -a.qty
               OR m.shipment_id IS DISTINCT FROM a.shipment_id)"""),
    ("finished order that still has an open hold", """
        SELECT COUNT(*) FROM reservation r
        JOIN order_line l USING (order_line_id)
        JOIN orders o USING (order_id)
        WHERE r.status IN ('ACTIVE', 'CONFIRMED') AND o.status IN ('FULFILLED', 'CANCELLED', 'REJECTED')"""),
    ("fulfilled order with a line that never shipped or ended", """
        SELECT COUNT(*) FROM order_line l JOIN orders o USING (order_id)
        WHERE o.status = 'FULFILLED' AND l.status IN ('PENDING', 'ALLOCATED')"""),
    ("split decision without two holds", """
        SELECT COUNT(*) FROM (
            SELECT d.decision_id FROM allocation_decision d
            LEFT JOIN reservation r ON r.order_line_id = d.order_line_id AND r.created_at = d.decided_at
            WHERE d.decision_code = 'SPLIT'
            GROUP BY d.decision_id HAVING COUNT(r.reservation_id) <> 2) t"""),
    ("customer shipment without a sale", """
        SELECT COUNT(*) FROM shipment s
        LEFT JOIN inventory_movement m ON m.shipment_id = s.shipment_id AND m.movement_type = 'SALE'
        WHERE s.shipment_type = 'CUSTOMER' AND m.movement_id IS NULL"""),
    ("allocation and its reservation disagree", """
        SELECT COUNT(*) FROM order_allocation a
        JOIN reservation r USING (reservation_id)
        WHERE a.location_id <> r.location_id OR a.qty <> r.qty
           OR a.order_line_id <> r.order_line_id"""),
    ("inbound holds above what the shipment carries", """
        SELECT COUNT(*) FROM (
            SELECT s.shipment_id FROM reservation r
            JOIN shipment s ON s.shipment_id = r.inbound_shipment_id
            WHERE r.status IN ('ACTIVE', 'CONFIRMED')
            GROUP BY s.shipment_id, s.transfer_qty HAVING SUM(r.qty) > s.transfer_qty) t"""),
    ("order sale before its inbound truck arrived", """
        SELECT COUNT(*) FROM inventory_movement sale
        JOIN order_allocation a USING (allocation_id)
        JOIN reservation r USING (reservation_id)
        LEFT JOIN inventory_movement arrival
          ON arrival.shipment_id = r.inbound_shipment_id AND arrival.movement_type = 'TRANSFER_IN'
        WHERE sale.movement_type = 'SALE' AND r.supply_type = 'INBOUND'
          AND (arrival.movement_id IS NULL OR sale.occurred_at < arrival.occurred_at)"""),
    ("consumed hold without a sale of the same quantity", """
        SELECT COUNT(*) FROM reservation r
        JOIN order_allocation a USING (reservation_id)
        LEFT JOIN inventory_movement sale
          ON sale.allocation_id = a.allocation_id AND sale.movement_type = 'SALE'
        WHERE r.status = 'CONSUMED' AND COALESCE(sale.quantity, 0) <> -r.qty"""),

    # Each shipment's history, the same way as the order and reservation history above.
    ("shipment with no event", """
        SELECT COUNT(*) FROM shipment s
        LEFT JOIN shipment_event e USING (shipment_id) WHERE e.event_id IS NULL"""),
    ("shipment event with an illegal transition",
        illegal_transition_sql("shipment_event", rules.SHIPMENT_TRANSITIONS)),
    ("shipment events that do not chain",
        broken_chain_sql("shipment_event", "shipment_id")),
    ("shipment status differs from its latest event",
        status_differs_sql("shipment", "shipment_id", "shipment_event", "shipment_id")),

    # A transfer has to leave once and arrive at most once, for its exact quantity.
    ("transfer without exactly one matching TRANSFER_OUT", """
        SELECT COUNT(*) FROM (
            SELECT s.shipment_id FROM shipment s
            LEFT JOIN inventory_movement m
              ON m.shipment_id = s.shipment_id AND m.movement_type = 'TRANSFER_OUT'
             AND m.sku_id = s.transfer_sku_id AND m.location_id = s.from_location_id
             AND m.quantity = -s.transfer_qty
            WHERE s.shipment_type = 'TRANSFER' AND s.status IN ('IN_TRANSIT', 'RECEIVED')
            GROUP BY s.shipment_id HAVING COUNT(m.movement_id) <> 1) t"""),
    ("received transfer without exactly one matching TRANSFER_IN", """
        SELECT COUNT(*) FROM (
            SELECT s.shipment_id FROM shipment s
            LEFT JOIN inventory_movement m
              ON m.shipment_id = s.shipment_id AND m.movement_type = 'TRANSFER_IN'
             AND m.sku_id = s.transfer_sku_id AND m.location_id = s.to_location_id
             AND m.quantity = s.transfer_qty
            WHERE s.shipment_type = 'TRANSFER' AND s.status = 'RECEIVED'
            GROUP BY s.shipment_id HAVING COUNT(m.movement_id) <> 1) t"""),
    ("transfer still in transit with a TRANSFER_IN already posted", """
        SELECT COUNT(*) FROM shipment s
        JOIN inventory_movement m ON m.shipment_id = s.shipment_id AND m.movement_type = 'TRANSFER_IN'
        WHERE s.status <> 'RECEIVED'"""),
    ("transfer movement that does not belong to its shipment", """
        SELECT COUNT(*) FROM inventory_movement m
        LEFT JOIN shipment s USING (shipment_id)
        WHERE m.movement_type IN ('TRANSFER_OUT', 'TRANSFER_IN')
          AND (s.shipment_type IS DISTINCT FROM 'TRANSFER' OR m.sku_id <> s.transfer_sku_id
               OR (m.movement_type = 'TRANSFER_OUT' AND m.location_id <> s.from_location_id)
               OR (m.movement_type = 'TRANSFER_IN' AND m.location_id <> s.to_location_id))"""),
    # If these two times differed, there would be a moment when the same truck counts
    # as inbound and as on hand.
    ("TRANSFER_IN time differs from the RECEIVED event", """
        SELECT COUNT(*) FROM inventory_movement m
        JOIN shipment_event e ON e.shipment_id = m.shipment_id AND e.to_status = 'RECEIVED'
        WHERE m.movement_type = 'TRANSFER_IN' AND m.occurred_at <> e.occurred_at"""),
    ("transfer units that left, arrived or stayed on the road do not add up", """
        SELECT COUNT(*) FROM (
            SELECT sku_id, SUM(units) FROM (
                SELECT sku_id, quantity AS units FROM inventory_movement
                WHERE movement_type IN ('TRANSFER_OUT', 'TRANSFER_IN')
                UNION ALL
                SELECT transfer_sku_id, transfer_qty FROM shipment
                WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT') t
            GROUP BY sku_id HAVING SUM(units) <> 0) d"""),
    ("return not linked to a sale", """
        SELECT COUNT(*) FROM inventory_movement r
        LEFT JOIN inventory_movement sale ON sale.movement_id = r.related_movement_id
        WHERE r.movement_type = 'RETURN' AND sale.movement_type IS DISTINCT FROM 'SALE'"""),
    ("return at a different SKU or location than its sale", """
        SELECT COUNT(*) FROM inventory_movement r
        JOIN inventory_movement sale ON sale.movement_id = r.related_movement_id
        WHERE r.movement_type = 'RETURN'
          AND (r.sku_id <> sale.sku_id OR r.location_id <> sale.location_id)"""),
    ("return at or before its sale", """
        SELECT COUNT(*) FROM inventory_movement r
        JOIN inventory_movement sale ON sale.movement_id = r.related_movement_id
        WHERE r.movement_type = 'RETURN' AND r.occurred_at <= sale.occurred_at"""),
    ("returned more than was sold", """
        SELECT COUNT(*) FROM (
            SELECT r.related_movement_id FROM inventory_movement r
            JOIN inventory_movement sale ON sale.movement_id = r.related_movement_id
            WHERE r.movement_type = 'RETURN'
            GROUP BY r.related_movement_id, sale.quantity HAVING SUM(r.quantity) > -sale.quantity) t"""),

    # Dates that have to come in order. The effective date ranges are CHECKs in the schema.
    ("hold that expires at or before it was created", """
        SELECT COUNT(*) FROM reservation WHERE status <> 'REJECTED' AND expires_at <= created_at"""),
    ("hold whose first event differs from when it was created", """
        SELECT COUNT(*) FROM reservation r
        JOIN reservation_event e ON e.reservation_id = r.reservation_id AND e.from_status IS NULL
        WHERE e.occurred_at <> r.created_at"""),
    ("decision before its order was placed", """
        SELECT COUNT(*) FROM allocation_decision d
        JOIN order_line l USING (order_line_id)
        JOIN orders o USING (order_id)
        WHERE d.decided_at < o.placed_at"""),
    ("order sale before its allocation", """
        SELECT COUNT(*) FROM inventory_movement m
        JOIN order_allocation a USING (allocation_id)
        WHERE m.movement_type = 'SALE' AND m.occurred_at < a.allocated_at"""),
    ("transfer due before it was created", """
        SELECT COUNT(*) FROM shipment WHERE original_eta_at < created_at"""),
]

# These also say which records broke the rule, because eval/check_failures.py has to compare
# them with the labels. A count alone can't tell a labelled record from an unrelated one.
# Each query returns the ID first. It runs once, so the count and the IDs can't disagree.
RECORD_CHECKS = [
    ("closing balance does not reconcile to opening plus ledger", """
        SELECT c.balance_id FROM inventory_balance c
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
        ORDER BY c.balance_id"""),
    # The whole network: a transfer only moves stock, so it can't appear in this sum.
    ("network stock plus in transit differs from opening plus outside changes", """
        SELECT sku_id FROM (
            SELECT b.sku_id,
                   SUM(b.on_hand) FILTER (WHERE b.snapshot_type = 'CLOSING') AS closing,
                   SUM(b.on_hand) FILTER (WHERE b.snapshot_type = 'OPENING') AS opening
            FROM inventory_balance b GROUP BY b.sku_id) totals
        LEFT JOIN (
            SELECT sku_id, SUM(quantity) AS change FROM inventory_movement
            WHERE movement_type NOT IN ('TRANSFER_OUT', 'TRANSFER_IN') GROUP BY sku_id) outside USING (sku_id)
        LEFT JOIN (
            SELECT transfer_sku_id AS sku_id, SUM(transfer_qty) AS units FROM shipment
            WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT' GROUP BY transfer_sku_id) road USING (sku_id)
        WHERE totals.closing IS NOT NULL
          AND totals.closing + COALESCE(road.units, 0) <> totals.opening + COALESCE(outside.change, 0)
        ORDER BY sku_id"""),
    ("hold above what could be promised when it was placed", detect.RESERVATION_SQL),
    ("order sale from the blocked bucket", detect.BLOCKED_SQL),
    ("transfer received at or before it shipped", """
        SELECT arrival.shipment_id FROM inventory_movement arrival
        JOIN inventory_movement departure
          ON departure.shipment_id = arrival.shipment_id AND departure.movement_type = 'TRANSFER_OUT'
        WHERE arrival.movement_type = 'TRANSFER_IN' AND arrival.occurred_at <= departure.occurred_at
        ORDER BY arrival.shipment_id"""),
]

PRIMARY_KEYS = {
    "product": "product_id", "sku": "sku_id", "location": "location_id", "channel": "channel_code",
    "channel_location": "channel_location_id", "sku_location": "sku_id, location_id",
    "replenishment_rule": "rule_id", "inventory_balance": "balance_id", "orders": "order_id",
    "order_line": "order_line_id", "shipment": "shipment_id", "reservation": "reservation_id",
    "order_allocation": "allocation_id", "inventory_movement": "movement_id",
    "allocation_decision": "decision_id", "order_event": "event_id", "reservation_event": "event_id",
    "shipment_event": "event_id", "inventory_exception": "exception_id",
}

# Child table, child columns, parent table, parent columns. The same list as constraints.sql,
# and a test holds the two together.
FOREIGN_KEYS = [
    ("sku", "product_id", "product", "product_id"),
    ("location", "parent_location_id", "location", "location_id"),
    ("channel_location", "channel_code", "channel", "channel_code"),
    ("channel_location", "location_id", "location", "location_id"),
    ("sku_location", "sku_id", "sku", "sku_id"),
    ("sku_location", "location_id", "location", "location_id"),
    ("replenishment_rule", "sku_id, location_id", "sku_location", "sku_id, location_id"),
    ("replenishment_rule", "source_location_id", "location", "location_id"),
    ("inventory_balance", "sku_id, location_id", "sku_location", "sku_id, location_id"),
    ("orders", "channel_code", "channel", "channel_code"),
    ("order_line", "order_id", "orders", "order_id"),
    ("order_line", "sku_id", "sku", "sku_id"),
    ("shipment", "order_id", "orders", "order_id"),
    ("shipment", "from_location_id", "location", "location_id"),
    ("shipment", "to_location_id", "location", "location_id"),
    ("shipment", "transfer_sku_id", "sku", "sku_id"),
    ("reservation", "order_line_id", "order_line", "order_line_id"),
    ("reservation", "sku_id", "sku", "sku_id"),
    ("reservation", "sku_id, location_id", "sku_location", "sku_id, location_id"),
    ("reservation", "inbound_shipment_id", "shipment", "shipment_id"),
    ("order_allocation", "order_line_id", "order_line", "order_line_id"),
    ("order_allocation", "location_id", "location", "location_id"),
    ("order_allocation", "reservation_id", "reservation", "reservation_id"),
    ("order_allocation", "shipment_id", "shipment", "shipment_id"),
    ("inventory_movement", "sku_id, location_id", "sku_location", "sku_id, location_id"),
    ("inventory_movement", "allocation_id", "order_allocation", "allocation_id"),
    ("inventory_movement", "shipment_id", "shipment", "shipment_id"),
    ("inventory_movement", "related_movement_id", "inventory_movement", "movement_id"),
    ("allocation_decision", "order_line_id", "order_line", "order_line_id"),
    ("allocation_decision", "chosen_location_id", "location", "location_id"),
    ("order_event", "order_id", "orders", "order_id"),
    ("order_event", "order_line_id", "order_line", "order_line_id"),
    ("reservation_event", "reservation_id", "reservation", "reservation_id"),
    ("shipment_event", "shipment_id", "shipment", "shipment_id"),
    ("inventory_exception", "balance_id", "inventory_balance", "balance_id"),
    ("inventory_exception", "reservation_id", "reservation", "reservation_id"),
    ("inventory_exception", "movement_id", "inventory_movement", "movement_id"),
    ("inventory_exception", "shipment_id", "shipment", "shipment_id"),
]

REGIONS = ["NORTH", "SOUTH", "EAST", "WEST", "CENTRAL"]
CHANNELS = ["WEB", "APP", "MARKETPLACE", "STORE"]
PRODUCT_STATUSES = ["ACTIVE", "DISCONTINUED"]
ORDER_STATUSES = ["PLACED", "ALLOCATED", "CONFIRMED", "FULFILLED", "CANCELLED", "REJECTED"]
LINE_STATUSES = ["PENDING", "ALLOCATED", "SHIPPED", "CANCELLED", "REJECTED"]
RESERVATION_STATUSES = ["ACTIVE", "CONFIRMED", "CONSUMED", "EXPIRED", "CANCELLED", "REJECTED"]
SHIPMENT_STATUSES = ["CREATED", "IN_TRANSIT", "DELIVERED", "RECEIVED", "CANCELLED"]

# Table, column and every value it may hold. docs/DATA_DICTIONARY.md lists the same values,
# and a test holds the two together.
ALLOWED_VALUES = [
    ("product", "category", ["APPAREL", "FOOTWEAR", "HOME", "ELECTRONICS", "BEAUTY"]),
    ("product", "status", PRODUCT_STATUSES),
    ("sku", "status", PRODUCT_STATUSES),
    ("location", "location_type", ["NDC", "WAREHOUSE", "STORE"]),
    ("location", "region", REGIONS),
    ("channel", "channel_code", CHANNELS),
    ("channel_location", "channel_code", CHANNELS),
    ("inventory_balance", "snapshot_type", ["OPENING", "CLOSING"]),
    ("orders", "channel_code", CHANNELS),
    ("orders", "ship_to_region", REGIONS),
    ("orders", "status", ORDER_STATUSES),
    ("order_line", "status", LINE_STATUSES),
    ("shipment", "shipment_type", ["CUSTOMER", "TRANSFER"]),
    ("shipment", "status", SHIPMENT_STATUSES),
    ("reservation", "supply_type", ["ON_HAND", "INBOUND"]),
    ("reservation", "status", RESERVATION_STATUSES),
    ("inventory_movement", "movement_type", ["RECEIPT", "SALE", "RETURN", "TRANSFER_OUT", "TRANSFER_IN",
                                             "ADJUSTMENT", "BLOCK", "UNBLOCK", "WRITE_OFF"]),
    ("inventory_movement", "stock_bucket", ["SELLABLE", "BLOCKED"]),
    ("allocation_decision", "decision_code", ["SINGLE", "SPLIT", "INBOUND", "REJECTED", "REALLOCATED"]),
    ("order_event", "to_status", sorted(set(ORDER_STATUSES + LINE_STATUSES))),
    ("reservation_event", "to_status", RESERVATION_STATUSES),
    ("shipment_event", "to_status", SHIPMENT_STATUSES),
]


def overlap_sql(table, id_column, same_thing):
    """Two versions in effect at once would make a location a candidate twice, and double its stock."""
    match = " AND ".join(f"a.{column} = b.{column}" for column in same_thing)
    return f"""SELECT COUNT(*) FROM {table} a JOIN {table} b ON {match} AND a.{id_column} < b.{id_column}
               WHERE a.effective_from < COALESCE(b.effective_to, 'infinity'::date)
                 AND b.effective_from < COALESCE(a.effective_to, 'infinity'::date)"""


def duplicate_key_sql(table, key):
    return f"SELECT COUNT(*) FROM (SELECT {key} FROM {table} GROUP BY {key} HAVING COUNT(*) > 1) d"


def orphan_sql(child, child_columns, parent, parent_columns):
    """A reference that is filled in and points at nothing. An empty reference is allowed here."""
    pairs = list(zip(child_columns.split(", "), parent_columns.split(", ")))
    filled = " AND ".join(f"c.{child_column} IS NOT NULL" for child_column, _ in pairs)
    match = " AND ".join(f"p.{parent_column} = c.{child_column}" for child_column, parent_column in pairs)
    return f"SELECT COUNT(*) FROM {child} c WHERE {filled} AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {match})"


def bad_value_sql(table, column, allowed):
    values = ", ".join(f"'{value}'" for value in allowed)
    return f"SELECT COUNT(*) FROM {table} WHERE {column} NOT IN ({values})"


# The database refuses these when loading, but a summary that doesn't report them can't show it.
CHECKS += [(f"duplicate primary key: {table}", duplicate_key_sql(table, key)) for table, key in PRIMARY_KEYS.items()]
CHECKS += [(f"reference points at nothing: {child}.{child_columns}", orphan_sql(child, child_columns, parent, parent_columns))
           for child, child_columns, parent, parent_columns in FOREIGN_KEYS]
CHECKS += [(f"value not allowed: {table}.{column}", bad_value_sql(table, column, allowed))
           for table, column, allowed in ALLOWED_VALUES]
CHECKS += [("replenishment rule versions that overlap", overlap_sql("replenishment_rule", "rule_id", ["sku_id", "location_id"])),
           ("channel buffer versions that overlap", overlap_sql("channel_location", "channel_location_id", ["channel_code", "location_id"]))]

# Size targets, as manifest key and table.
SCALE_TARGETS = [("skus", "sku"), ("locations", "location"), ("movements", "inventory_movement"),
                 ("orders", "orders")]

# Each of these has to happen in the generated data, as a name and a SQL count.
GENERATED_ORDER_ACTIVITY = [
    ("rejected oversell attempts", """
        SELECT COUNT(*) FROM allocation_decision WHERE reason_code = 'INSUFFICIENT_ATP'"""),
    ("expired holds", "SELECT COUNT(*) FROM reservation WHERE status = 'EXPIRED'"),
    ("split allocations", "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'SPLIT'"),
    ("orders held on inbound stock", "SELECT COUNT(*) FROM allocation_decision WHERE decision_code = 'INBOUND'"),
    ("online returns", """
        SELECT COUNT(*) FROM inventory_movement r
        JOIN inventory_movement sale ON sale.movement_id = r.related_movement_id
        WHERE r.movement_type = 'RETURN' AND sale.allocation_id IS NOT NULL"""),
    ("holds moved off a late truck", """
        SELECT COUNT(*) FROM reservation_event WHERE reason_code = 'INBOUND_LATE_REALLOCATED'"""),
    ("promises updated for a late truck", """
        SELECT COUNT(*) FROM reservation_event WHERE reason_code = 'INBOUND_LATE_REPROMISED'"""),
    ("holds cancelled past the late limit", """
        SELECT COUNT(*) FROM reservation_event WHERE reason_code = 'INBOUND_LATE_CANCELLED'"""),
    ("orders shipped", "SELECT COUNT(*) FROM orders WHERE status = 'FULFILLED'"),
    ("parcels delivered", "SELECT COUNT(*) FROM shipment WHERE status = 'DELIVERED'"),
]

# How many decisions to work out again. Each one costs several SQL queries.
DECISION_SAMPLE = 200

# The generated ledger has to contain each of these. A rule about returns passes on
# data with no returns, the same trap as an empty table.
GENERATED_MOVEMENT_TYPES = ["RECEIPT", "SALE", "RETURN", "TRANSFER_OUT", "TRANSFER_IN", "ADJUSTMENT",
                            "BLOCK", "UNBLOCK", "WRITE_OFF"]


def run_checks(cursor):
    """Takes a cursor so a test can break some data in a transaction and check we notice."""
    results = []
    for name, sql in CHECKS:
        cursor.execute(sql)
        results.append(make_result(name, cursor.fetchone()[0]))
    for name, sql in RECORD_CHECKS:
        cursor.execute(sql)
        records = [row[0] for row in cursor.fetchall()]
        results.append(dict(make_result(name, len(records)), records=records))
    return results


def make_result(name, violations):
    return {"check": name, "violations": violations, "passed": violations == 0}


def count_rows(cursor, table):
    # The table names come from our own constants, never from input.
    cursor.execute(f"SELECT COUNT(*) FROM {table}")
    return cursor.fetchone()[0]


def check_tables_not_empty(cursor):
    """Every other check passes on an empty table, so empty has to be a failure of its own."""
    results = []
    for table in TABLE_COLUMNS:
        empty = count_rows(cursor, table) == 0
        results.append(make_result(f"table has rows: {table}", 1 if empty else 0))
    return results


def check_generated_activity(cursor):
    """Rule checks can't fail on rows that don't exist, so their absence fails here."""
    results = []
    for movement_type in GENERATED_MOVEMENT_TYPES:
        cursor.execute("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = %s", (movement_type,))
        missing = 1 if cursor.fetchone()[0] == 0 else 0
        results.append(make_result(f"ledger has movements of type {movement_type}", missing))

    cursor.execute("SELECT COUNT(*) FROM shipment WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT'")
    missing = 1 if cursor.fetchone()[0] == 0 else 0
    results.append(make_result("some transfers are still in transit at the end", missing))

    cursor.execute("SELECT COUNT(*) FROM shipment_event WHERE reason_code = 'ETA_MISSED'")
    missing = 1 if cursor.fetchone()[0] == 0 else 0
    results.append(make_result("some transfers missed their ETA", missing))

    for name, sql in GENERATED_ORDER_ACTIVITY:
        cursor.execute(sql)
        missing = 1 if cursor.fetchone()[0] == 0 else 0
        results.append(make_result(f"generated data includes {name}", missing))
    return results


def get_own_holds(cursor, order_line_id, decided_at):
    """The holds this decision itself created. SQL at that second already counts them."""
    cursor.execute(
        """SELECT location_id, qty, inbound_shipment_id FROM reservation
           WHERE order_line_id = %s AND created_at = %s AND status <> 'REJECTED'""",
        (order_line_id, decided_at),
    )
    return cursor.fetchall()


def as_seen_before_the_decision(positions, own_holds, channel):
    """The SQL numbers with this decision's own holds taken back out."""
    seen = []
    for position in positions:
        own = [hold for hold in own_holds if hold[0] == position["location_id"]]
        again = rules.position(position["on_hand"], position["blocked"],
                               position["reserved"] - sum(hold[1] for hold in own), position["inbound"],
                               position["safety_stock"], position["buffer_units"], channel["counts_inbound"], True)
        again["location_id"] = position["location_id"]
        again["location_type"] = position["location_type"]
        again["region"] = position["region"]
        again["inbound_shipments"] = []
        for truck in position["inbound_shipments"]:
            on_this_truck = sum(hold[1] for hold in own if hold[2] == truck["shipment_id"])
            again["inbound_shipments"].append(dict(truck, free_qty=truck["free_qty"] + on_this_truck))
        seen.append(again)
    return seen


def decision_differs(cursor, row):
    """Works one decision out again from the ledger and events, and runs the rule on it."""
    order_line_id, decided_at, sku_id, requested_qty, channel_code, region, decision_code, chosen, candidates = row
    channel = atp.get_channel(cursor, channel_code)
    positions = atp.get_positions(cursor, sku_id, channel_code, decided_at)
    seen = as_seen_before_the_decision(positions, get_own_holds(cursor, order_line_id, decided_at), channel)

    result = rules.allocate(requested_qty, seen, region)
    first_location = result["parts"][0]["location_id"] if result["parts"] else None
    if result["decision_code"] != decision_code or first_location != chosen:
        return True
    return rules.candidates_summary(result["ranked"]) != candidates


def recheck_decisions(cursor, sample=DECISION_SAMPLE):
    """
    The generator decided these on its own running counts, with no database. Each one
    has to come out the same from SQL. A hold moved off a late truck is left out, because
    its old hold ends in the same second and the numbers can't be separated afterwards.
    """
    cursor.execute(
        """SELECT d.order_line_id, d.decided_at, l.sku_id, d.requested_qty, o.channel_code, o.ship_to_region,
                  d.decision_code, d.chosen_location_id, d.candidates
           FROM allocation_decision d
           JOIN order_line l USING (order_line_id)
           JOIN orders o USING (order_id)
           WHERE d.decision_code <> 'REALLOCATED'
           ORDER BY d.decision_id"""
    )
    rows = cursor.fetchall()
    # Evenly spaced, so the sample covers the whole period and is the same every run.
    step = max(1, len(rows) // sample)
    chosen = rows[::step][:sample]
    differing = [row[0] for row in chosen if decision_differs(cursor, row)]
    result = make_result(f"sample of {len(chosen)} of {len(rows)} order decisions gives the same answer from SQL",
                         len(differing))
    result["records"] = differing
    result["decisions_checked"] = len(chosen)
    result["decisions_total"] = len(rows)
    return [result]


def check_row_counts(cursor, manifest):
    """The database has to hold exactly what the generator said it wrote."""
    results = []
    for table in TABLE_COLUMNS:
        info = manifest["files"].get(f"{table}.csv")
        expected = info["rows"] if info else None
        actual = count_rows(cursor, table)
        difference = 1 if expected is None else abs(actual - expected)
        results.append(make_result(f"row count matches manifest: {table}", difference))

    for key, table in SCALE_TARGETS:
        difference = abs(count_rows(cursor, table) - manifest["scale"][key])
        results.append(make_result(f"row count matches target: {key}", difference))
    return results


def check_files_match_manifest(data_dir, manifest):
    """Row counts can match while the contents don't, so the checksums are compared too."""
    results = []
    for file_name, info in manifest["files"].items():
        path = data_dir / file_name
        same = path.exists() and sha256_of(path) == info["sha256"]
        results.append(make_result(f"file matches its manifest checksum: {file_name}", 0 if same else 1))
    return results


def required_check_names():
    """
    Every check a run has to report, so a check that went missing can be noticed. The
    decision sample isn't here, because its name carries its size.
    """
    names = [f"table has rows: {table}" for table in TABLE_COLUMNS]
    names += [f"ledger has movements of type {movement_type}" for movement_type in GENERATED_MOVEMENT_TYPES]
    names += ["some transfers are still in transit at the end", "some transfers missed their ETA"]
    names += [f"generated data includes {name}" for name, _sql in GENERATED_ORDER_ACTIVITY]
    names += [f"row count matches manifest: {table}" for table in TABLE_COLUMNS]
    names += [f"row count matches target: {key}" for key, _table in SCALE_TARGETS]
    names += [f"file matches its manifest checksum: {table}.csv" for table in TABLE_COLUMNS]
    names += [name for name, _sql in CHECKS + RECORD_CHECKS]
    return names


def validate(cursor, manifest, decision_sample=DECISION_SAMPLE):
    results = check_tables_not_empty(cursor)
    results += check_generated_activity(cursor)
    results += check_row_counts(cursor, manifest)
    results += run_checks(cursor)
    results += recheck_decisions(cursor, decision_sample)
    return results


def load_manifest(data_dir):
    path = data_dir / "source_manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Validate the loaded data.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the manifest, default data/<scale>")
    parser.add_argument("--clean", action="store_true", help="use the clean baseline, data/<scale>_clean")
    parser.add_argument("--decisions", type=int, default=DECISION_SAMPLE,
                        help="how many order decisions to recalculate in SQL. Each costs several queries")
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale
    if args.clean:
        data_dir = clean_dir_of(data_dir)

    manifest = load_manifest(data_dir)
    if manifest is None:
        print(f"FAIL  no source_manifest.json in {data_dir}, so the row counts can't be checked")
        sys.exit(1)

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            results = check_files_match_manifest(data_dir, manifest) + validate(cursor, manifest, args.decisions)
    finally:
        connection.close()

    for result in results:
        word = "PASS" if result["passed"] else "FAIL"
        print(f"  {word}  {result['violations']:>8,}  {result['check']}")

    failed = sum(1 for result in results if not result["passed"])
    print(f"{len(results) - failed} of {len(results)} checks passed on the {manifest['dataset']} data")
    print(f"NOT GENERATED YET, so not covered by this run: {', '.join(NOT_GENERATED_YET)}")
    sample = next(result for result in results if "decisions_checked" in result)
    print(f"Order decisions recalculated in SQL: only {sample['decisions_checked']} of {sample['decisions_total']}")
    if manifest["dataset"] == "final" and failed:
        print("The final data has labelled problems in it on purpose, so failures are normal here.")
        print("To see whether each one is explained: python -m eval.check_failures")

    path = data_dir / "validation_summary.json"
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        summary = {
            "dataset": manifest["dataset"],
            "seed": manifest["seed"],
            "scale": manifest["scale"]["name"],
            "failed": failed,
            "decisions_checked": sample["decisions_checked"],
            "decisions_total": sample["decisions_total"],
            "not_generated_yet": NOT_GENERATED_YET,
            "checks": results,
        }
        json.dump(summary, file, indent=2)
        file.write("\n")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
