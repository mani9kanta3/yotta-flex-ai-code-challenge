"""
Check that injecting the cases changed nothing except the cases.

Run:  python -m eval.check_unrelated --scale starter

Compares the final data with the clean baseline, ignoring the ID numbers, because everything
is numbered again after injection. Every clean row has to still be there unchanged, and every
new row has to belong to a labelled case or decoy. It exits 1 if not.
"""

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from app import config
from eval.score import load_ground_truth
from generator.export import clean_dir_of, truth_dir_of
from generator.settings import SCALES

MASTER_TABLES = ["product", "sku", "location", "channel", "channel_location", "sku_location", "replenishment_rule"]

# What a row says, without the IDs that get renumbered.
MOVEMENT_FIELDS = ["sku_id", "location_id", "movement_type", "stock_bucket", "quantity", "occurred_at",
                   "reason_code", "movement_group_id"]
ORDER_FIELDS = ["channel_code", "customer_ref", "ship_to_region", "placed_at", "status", "order_total"]
SHIPMENT_FIELDS = ["shipment_type", "status", "from_location_id", "to_location_id", "created_at", "original_eta_at",
                   "transfer_sku_id", "transfer_qty"]


def read_rows(folder, table):
    with open(folder / f"{table}.csv", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def new_rows(clean_rows, final_rows, fields):
    """Returns the clean rows that went missing, as a count, and the final rows that are new."""
    content = lambda row: tuple(row[field] for field in fields)
    left_to_match = Counter(content(row) for row in clean_rows)
    added = []
    for row in final_rows:
        if left_to_match[content(row)] > 0:
            left_to_match[content(row)] -= 1
        else:
            added.append(row)
    return sum(left_to_match.values()), added


def compare_table(table, clean_dir, final_dir, fields, belongs_to_a_case):
    """Returns the problems found in this table."""
    missing, added = new_rows(read_rows(clean_dir, table), read_rows(final_dir, table), fields)
    unlabelled = [row for row in added if not belongs_to_a_case(row)]
    print(f"  {table:20} {missing:>6} clean rows changed or missing  {len(added):>6} new rows  {len(unlabelled):>6} not labelled")
    problems = []
    if missing:
        problems.append(f"{missing} clean {table} rows changed or went missing")
    if unlabelled:
        problems.append(f"{len(unlabelled)} new {table} rows don't belong to a labelled case")
    return problems


def compare_balances(clean_dir, final_dir, case_skus):
    """Opening balances can't change. A closing balance can only change on a SKU with a case."""
    problems = []
    def by_pair(folder, snapshot_type):
        return {(row["sku_id"], row["location_id"]): (row["on_hand"], row["blocked"])
                for row in read_rows(folder, "inventory_balance") if row["snapshot_type"] == snapshot_type}
    if by_pair(clean_dir, "OPENING") != by_pair(final_dir, "OPENING"):
        problems.append("opening balances differ")
    clean = by_pair(clean_dir, "CLOSING")
    changed = [pair for pair, values in by_pair(final_dir, "CLOSING").items() if clean.get(pair) != values]
    outside = [pair for pair in changed if pair[0] not in case_skus]
    print(f"  {'closing balances':20} {len(changed):>6} changed  {len(outside):>6} on a SKU with no case")
    if outside:
        problems.append(f"{len(outside)} closing balances changed on a SKU with no case")
    return problems


def compare_datasets(final_dir):
    """Returns every problem found, as a list of sentences. Empty means nothing unrelated changed."""
    clean_dir = clean_dir_of(final_dir)
    cases = [row for row in load_ground_truth(truth_dir_of(final_dir)) if row["looks_like"] != "OVERSELL_ATTEMPT"]
    case_skus = {row["sku_id"] for row in cases}
    case_orders = {row["order_id"] for row in cases if row["order_id"]}
    case_movements = {row["record_id"] for row in cases if row["record_type"] == "movement"}
    case_shipments = {row["record_id"] for row in cases if row["record_type"] == "shipment"}

    problems = []
    for table in MASTER_TABLES:
        if (clean_dir / f"{table}.csv").read_bytes() != (final_dir / f"{table}.csv").read_bytes():
            problems.append(f"{table} differs")
    print(f"  {len(MASTER_TABLES)} master tables compared byte for byte")

    problems += compare_table("inventory_movement", clean_dir, final_dir, MOVEMENT_FIELDS,
                              lambda row: row["movement_id"] in case_movements or row["shipment_id"] in case_shipments)
    problems += compare_table("orders", clean_dir, final_dir, ORDER_FIELDS, lambda row: row["order_id"] in case_orders)
    problems += compare_table("shipment", clean_dir, final_dir, SHIPMENT_FIELDS,
                              lambda row: row["shipment_id"] in case_shipments or row["order_id"] in case_orders)
    problems += compare_balances(clean_dir, final_dir, case_skus)
    return problems


def main():
    parser = argparse.ArgumentParser(description="Check that injecting the cases changed nothing else.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the final data, default data/<scale>")
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale

    problems = compare_datasets(data_dir)
    for problem in problems:
        print(f"  PROBLEM  {problem}")
    print(f"{len(problems)} problems")
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
