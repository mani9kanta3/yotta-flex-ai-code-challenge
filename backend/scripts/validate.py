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

from app import config, db, rules
from generator.export import TABLE_COLUMNS
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

    ("closing balance does not reconcile to opening plus ledger", """
        SELECT COUNT(*) FROM inventory_balance c
        JOIN inventory_balance o
          ON o.sku_id = c.sku_id AND o.location_id = c.location_id AND o.snapshot_type = 'OPENING'
        LEFT JOIN (
            SELECT sku_id, location_id,
                   SUM(quantity) AS total_change,
                   SUM(quantity) FILTER (WHERE stock_bucket = 'BLOCKED') AS blocked_change
            FROM inventory_movement GROUP BY sku_id, location_id) m
          ON m.sku_id = c.sku_id AND m.location_id = c.location_id
        WHERE c.snapshot_type = 'CLOSING'
          AND (c.on_hand <> o.on_hand + COALESCE(m.total_change, 0)
               OR c.blocked <> o.blocked + COALESCE(m.blocked_change, 0))"""),

    ("stock below zero at any point in the ledger", f"""
        SELECT COUNT(*) FROM ({RUNNING_STOCK}) r WHERE r.stock_after < 0"""),

    ("movement before the pair went live", """
        SELECT COUNT(*) FROM inventory_movement m
        JOIN sku_location l USING (sku_id, location_id)
        WHERE m.occurred_at::date < l.ranged_from"""),
    ("sale after a discontinued SKU stopped", """
        SELECT COUNT(*) FROM inventory_movement m JOIN sku s USING (sku_id)
        WHERE m.movement_type = 'SALE' AND s.effective_to IS NOT NULL
          AND m.occurred_at::date >= s.effective_to"""),
    ("movement outside the simulated period", f"""
        SELECT COUNT(*) FROM inventory_movement
        WHERE occurred_at < '{config.START_AT}' OR occurred_at > '{config.AS_OF}'"""),
    ("movement ids not in time order", """
        SELECT COUNT(*) FROM (
            SELECT occurred_at, LAG(occurred_at) OVER (ORDER BY movement_id) AS previous
            FROM inventory_movement) t
        WHERE t.occurred_at < t.previous"""),

    # These have no rows to look at in generated data until orders are generated.
    # tests/test_orders.py runs them against real placed orders.
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
    ("allocated line whose allocations do not add up to its quantity", """
        SELECT COUNT(*) FROM order_line l
        LEFT JOIN (SELECT order_line_id, SUM(qty) AS allocated
                   FROM order_allocation GROUP BY order_line_id) a USING (order_line_id)
        WHERE l.status IN ('ALLOCATED', 'SHIPPED') AND COALESCE(a.allocated, 0) <> l.qty"""),
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
]

# Size targets, as manifest key and table.
SCALE_TARGETS = [("skus", "sku"), ("locations", "location"), ("movements", "inventory_movement")]


def run_checks(cursor):
    """Takes a cursor so a test can break some data in a transaction and check we notice."""
    results = []
    for name, sql in CHECKS:
        cursor.execute(sql)
        results.append(make_result(name, cursor.fetchone()[0]))
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


def validate(cursor, manifest):
    results = check_tables_not_empty(cursor)
    results += check_row_counts(cursor, manifest)
    results += run_checks(cursor)
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
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale

    manifest = load_manifest(data_dir)
    if manifest is None:
        print(f"FAIL  no source_manifest.json in {data_dir}, so the row counts can't be checked")
        sys.exit(1)

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            results = validate(cursor, manifest)
    finally:
        connection.close()

    for result in results:
        word = "PASS" if result["passed"] else "FAIL"
        print(f"  {word}  {result['violations']:>8,}  {result['check']}")

    failed = sum(1 for result in results if not result["passed"])
    print(f"{len(results) - failed} of {len(results)} checks passed")
    print(f"NOT GENERATED YET, so not covered by this run: {', '.join(NOT_GENERATED_YET)}")

    path = data_dir / "validation_summary.json"
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        summary = {"failed": failed, "not_generated_yet": NOT_GENERATED_YET, "checks": results}
        json.dump(summary, file, indent=2)
        file.write("\n")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
