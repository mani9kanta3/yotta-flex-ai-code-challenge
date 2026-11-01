"""
Measure the loaded final data and write everything the page and the README quote.

Run:  python -m eval.report --scale starter

Run it straight after loading and detecting, before any live orders. It writes
data/<scale>/metrics_summary.json with totals and rates only, and no record IDs. So the API
can serve it without ever reading the answers. Only this folder reads those.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from app import atp, config, db
from eval.check_unrelated import compare_datasets
from eval.score import load_ground_truth
from generator.export import truth_dir_of
from generator.settings import SCALES


def rows_of(cursor, sql, params=None):
    cursor.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def one_number(cursor, sql, params=None):
    cursor.execute(sql, params)
    return cursor.fetchone()[0]


def share(part, whole):
    return round(part / whole, 4) if whole else None


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def measure_decisions(cursor):
    rows = rows_of(cursor, """SELECT decision_code AS code, COUNT(*) AS decisions FROM allocation_decision
                              GROUP BY decision_code ORDER BY COUNT(*) DESC""")
    total = sum(row["decisions"] for row in rows)
    for row in rows:
        row["share"] = share(row["decisions"], total)
    return {"total": total, "by_code": rows}


def measure_holds(cursor):
    statuses = {row["status"]: row["holds"] for row in rows_of(
        cursor, "SELECT status, COUNT(*) AS holds FROM reservation GROUP BY status")}
    placed = sum(count for status, count in statuses.items() if status != "REJECTED")
    return {"placed": placed, "by_status": statuses, "expired": statuses.get("EXPIRED", 0),
            "expiry_rate": share(statuses.get("EXPIRED", 0), placed)}


def measure_oversell(cursor, truth_rows):
    attempts = [row["record_id"] for row in truth_rows if row["looks_like"] == "OVERSELL_ATTEMPT"]
    blocked = one_number(cursor, "SELECT COUNT(*) FROM reservation WHERE reservation_id = ANY(%s) AND status = 'REJECTED'",
                         (attempts,))
    return {"attempts": len(attempts), "blocked": blocked, "blocked_rate": share(blocked, len(attempts))}


def measure_promises(cursor):
    """Shipped by the first promise, split by where the stock was when it was held."""
    kept = rows_of(cursor, """
        SELECT r.supply_type, COUNT(*) AS shipped, COUNT(*) FILTER (WHERE m.occurred_at <= r.promised_ship_by) AS on_time
        FROM reservation r
        JOIN order_allocation a USING (reservation_id)
        JOIN inventory_movement m ON m.allocation_id = a.allocation_id AND m.movement_type = 'SALE'
        GROUP BY r.supply_type ORDER BY r.supply_type""")
    for row in kept:
        row["on_time_share"] = share(row["on_time"], row["shipped"])
    late = {row["reason_code"]: row["events"] for row in rows_of(cursor, """
        SELECT reason_code, COUNT(*) AS events FROM reservation_event
        WHERE reason_code LIKE 'INBOUND_LATE_%%' GROUP BY reason_code""")}
    return {"kept": kept, "repromised": late.get("INBOUND_LATE_REPROMISED", 0),
            "moved": one_number(cursor, """SELECT COUNT(*) FROM reservation_event
                                           WHERE reason_code = 'INBOUND_LATE_REALLOCATED' AND to_status = 'CANCELLED'"""),
            "cancelled": late.get("INBOUND_LATE_CANCELLED", 0)}


def measure_traceability(cursor):
    """Every allocation has to lead back to a line and a hold, and every used hold on to a sale and a parcel."""
    allocations = one_number(cursor, "SELECT COUNT(*) FROM order_allocation")
    linked = one_number(cursor, """SELECT COUNT(*) FROM order_allocation a
                                   JOIN order_line l USING (order_line_id)
                                   JOIN orders o USING (order_id)
                                   JOIN reservation r USING (reservation_id)""")
    consumed = one_number(cursor, "SELECT COUNT(*) FROM reservation WHERE status = 'CONSUMED'")
    consumed_with_sale = one_number(cursor, """
        SELECT COUNT(*) FROM reservation r
        JOIN order_allocation a USING (reservation_id)
        JOIN inventory_movement m ON m.allocation_id = a.allocation_id AND m.movement_type = 'SALE'
        JOIN shipment s ON s.shipment_id = m.shipment_id AND s.shipment_type = 'CUSTOMER'
        WHERE r.status = 'CONSUMED'""")
    return {"allocations": allocations, "allocations_linked": linked, "allocation_share": share(linked, allocations),
            "consumed_holds": consumed, "consumed_with_sale_and_parcel": consumed_with_sale,
            "shipment_share": share(consumed_with_sale, consumed)}


def measure_mix(cursor):
    movements = rows_of(cursor, """
        SELECT CASE WHEN movement_type = 'SALE' AND allocation_id IS NULL THEN 'SALE, walk-in'
                    WHEN movement_type = 'SALE' THEN 'SALE, online order'
                    ELSE movement_type END AS movement, COUNT(*) AS movements
        FROM inventory_movement GROUP BY 1 ORDER BY 2 DESC""")
    total = sum(row["movements"] for row in movements)
    for row in movements:
        row["share"] = share(row["movements"], total)
    channels = rows_of(cursor, """SELECT channel_code AS channel, COUNT(*) AS orders, SUM(order_total) AS order_value
                                  FROM orders GROUP BY channel_code ORDER BY COUNT(*) DESC""")
    for row in channels:
        row["order_value"] = float(row["order_value"])
    order_statuses = rows_of(cursor, "SELECT status, COUNT(*) AS orders FROM orders GROUP BY status ORDER BY COUNT(*) DESC")
    months = rows_of(cursor, """SELECT TO_CHAR(occurred_at, 'YYYY-MM') AS month, COUNT(*) AS movements,
                                       COUNT(*) FILTER (WHERE movement_type = 'SALE') AS sales
                                FROM inventory_movement GROUP BY 1 ORDER BY 1""")
    return {"movements": movements, "channels": channels, "order_statuses": order_statuses, "months": months}


def measure_concentration(cursor):
    """How much of walk-in and online sales the most popular tenth of SKUs accounts for."""
    rows = rows_of(cursor, """SELECT sku_id, -SUM(quantity) AS units FROM inventory_movement
                              WHERE movement_type = 'SALE' GROUP BY sku_id ORDER BY units DESC""")
    total = sum(row["units"] for row in rows)
    top = rows[:max(1, len(rows) // 10)]
    return {"skus_sold": len(rows), "top_tenth_skus": len(top), "top_tenth_units_share": share(sum(row["units"] for row in top), total)}


def measure_network(cursor):
    return {
        "in_transit_trucks": one_number(cursor, """SELECT COUNT(*) FROM shipment
                                                   WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT'"""),
        "in_transit_units": one_number(cursor, """SELECT COALESCE(SUM(transfer_qty), 0) FROM shipment
                                                  WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT'"""),
        "damage_events": one_number(cursor, """SELECT COUNT(DISTINCT movement_group_id) FROM inventory_movement
                                               WHERE movement_type = 'BLOCK'"""),
        "pairs": one_number(cursor, "SELECT COUNT(*) FROM sku_location"),
    }


def measure_atp_speed(cursor):
    """How long one full ATP answer takes, on 20 SKUs spread evenly, plus the one with the most trucks."""
    cursor.execute("SELECT sku_id FROM sku ORDER BY sku_id")
    skus = [row[0] for row in cursor.fetchall()]
    sample = skus[::max(1, len(skus) // 20)][:20]
    sample.append(one_number(cursor, """SELECT transfer_sku_id FROM shipment WHERE shipment_type = 'TRANSFER'
                                        GROUP BY transfer_sku_id ORDER BY COUNT(*) DESC, transfer_sku_id LIMIT 1"""))
    times = []
    for sku_id in sample:
        started = time.perf_counter()
        atp.get_positions(cursor, sku_id, "WEB", config.AS_OF)
        times.append((time.perf_counter() - started) * 1000)
    return {"skus_timed": len(times), "median_ms": round(statistics.median(times), 1), "slowest_ms": round(max(times), 1)}


def validation_of(data_dir, truth_dir):
    """Counts only. The failing IDs stay in the validator's and the checker's own files."""
    summary = read_json(data_dir / "validation_summary.json")
    failures = read_json(truth_dir / "failure_report.json")
    if summary is None or failures is None:
        return None
    return {
        "checks": len(summary["checks"]),
        "passed": failures["passed"],
        "expected_failures": [{"check": entry["check"], "violations": entry["violations"], "issue": entry["issue"]}
                              for entry in failures["expected_failures"]],
        "unexpected_failures": [{"check": entry["check"], "problems": entry["problems"]}
                                for entry in failures["unexpected_failures"]],
        "decisions_checked": summary["decisions_checked"],
        "decisions_total": summary["decisions_total"],
    }


def build_summary(cursor, data_dir):
    truth_dir = truth_dir_of(data_dir)
    manifest = read_json(data_dir / "source_manifest.json")
    clean_summary = read_json(data_dir.parent / f"{data_dir.name}_clean" / "validation_summary.json")
    checkpoints = read_json(truth_dir / "atp_checkpoint_report.json")
    truth_rows = load_ground_truth(truth_dir)
    return {
        "dataset": {
            "scale": manifest["scale"]["name"],
            "seed": manifest["seed"],
            "generator_command": manifest["generator_command"],
            "python_version": manifest["python_version"],
            "period": manifest["period"],
            "data_source": manifest["data_source"],
            "not_generated_yet": manifest["not_generated_yet"],
            "files": {name: {"rows": info["rows"], "sha256": info["sha256"][:12]} for name, info in manifest["files"].items()},
        },
        "validation": validation_of(data_dir, truth_dir),
        "clean_baseline": clean_summary and {"failed": clean_summary["failed"], "checks": len(clean_summary["checks"])},
        "detection": read_json(truth_dir / "metrics.json"),
        "checkpoints": checkpoints and {"by_kind": checkpoints["results"], "differing": len(checkpoints["differing"]),
                                        "missing_kinds": checkpoints["missing_kinds"]},
        "unrelated_problems": compare_datasets(data_dir),
        "reproducibility": read_json(data_dir / "reproducibility.json"),
        "timings": read_json(data_dir / "run_timings.json"),
        "decisions": measure_decisions(cursor),
        "holds": measure_holds(cursor),
        "oversell": measure_oversell(cursor, truth_rows),
        "promises": measure_promises(cursor),
        "traceability": measure_traceability(cursor),
        "mix": measure_mix(cursor),
        "concentration": measure_concentration(cursor),
        "network": measure_network(cursor),
        "atp_speed": measure_atp_speed(cursor),
    }


def main():
    parser = argparse.ArgumentParser(description="Measure the loaded final data.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the final data, default data/<scale>")
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            summary = build_summary(cursor, data_dir)
    finally:
        connection.close()

    with open(data_dir / "metrics_summary.json", "w", encoding="utf-8", newline="\n") as file:
        json.dump(summary, file, indent=2, default=str)
        file.write("\n")
    print(f"wrote {data_dir / 'metrics_summary.json'}")


if __name__ == "__main__":
    main()
