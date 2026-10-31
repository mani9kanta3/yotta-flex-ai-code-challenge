"""
Checks the loaded data with SQL and writes validation_summary.json.

Run:  python -m scripts.validate --scale starter

Each check is a query that counts rows breaking one rule, so 0 is a pass. This
first version covers the ledger. Later steps add orders, reservations and
shipments.
"""

import argparse
import json

from app import config, db
from generator.settings import SCALES

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

CHECKS = [
    ("movement_id format", """
        SELECT COUNT(*) FROM inventory_movement WHERE movement_id !~ '^MOV-2026-[0-9]{8}$'"""),
    ("sku_id format", """
        SELECT COUNT(*) FROM sku WHERE sku_id !~ '^SKU-[0-9]{6}$'"""),
    ("location_id format", """
        SELECT COUNT(*) FROM location WHERE location_id !~ '^LOC-[0-9]{6}$'"""),

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

    ("duplicate source doc on single-row movements", """
        SELECT COUNT(*) FROM (
            SELECT source_doc FROM inventory_movement WHERE movement_group_id IS NULL
            GROUP BY source_doc HAVING COUNT(*) > 1) d"""),

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
]


def run_checks(cursor):
    """Takes a cursor so a test can break some data in a transaction and check we notice."""
    results = []
    for name, sql in CHECKS:
        cursor.execute(sql)
        violations = cursor.fetchone()[0]
        results.append({"check": name, "violations": violations, "passed": violations == 0})
    return results


def main():
    parser = argparse.ArgumentParser(description="Validate the loaded data.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    args = parser.parse_args()

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            results = run_checks(cursor)
    finally:
        connection.close()

    for result in results:
        word = "PASS" if result["passed"] else "FAIL"
        print(f"  {word}  {result['violations']:>8,}  {result['check']}")

    failed = sum(1 for result in results if not result["passed"])
    print(f"{len(results) - failed} of {len(results)} checks passed")

    path = config.DATA_DIR / args.scale / "validation_summary.json"
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        json.dump({"scale": args.scale, "failed": failed, "checks": results}, file, indent=2)
        file.write("\n")


if __name__ == "__main__":
    main()
