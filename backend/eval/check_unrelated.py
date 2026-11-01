"""
Check that injecting the cases changed nothing except the cases.

Run:  python -m eval.check_unrelated --scale starter

Everything is numbered again after injection, so the two datasets can't be compared by ID. So
this works out where each clean order, shipment, allocation and movement ended up, from what the
row says. Then it rewrites every clean row with the new IDs and looks for that exact row in the
final data, in all 18 tables. A link pointed at the wrong row doesn't match, even though it would
pass a foreign key. Every clean row has to be there, and every new row has to belong to a labelled
case or decoy. It exits 1 if not.
"""

import argparse
import csv
import sys
from collections import Counter, deque
from pathlib import Path

from app import config
from eval.score import load_ground_truth
from generator.export import TABLE_COLUMNS, clean_dir_of, truth_dir_of
from generator.settings import SCALES

MASTER_TABLES = ["product", "sku", "location", "channel", "channel_location", "sku_location", "replenishment_rule"]

# Which columns hold a renumbered ID, and which map rewrites it.
ROWS_TO_COMPARE = {
    "orders": {"order_id": "order"},
    "order_line": {"order_line_id": "line", "order_id": "order"},
    "shipment": {"shipment_id": "shipment", "order_id": "order"},
    "reservation": {"reservation_id": "hold", "order_line_id": "line", "inbound_shipment_id": "shipment"},
    "order_allocation": {"allocation_id": "allocation", "order_line_id": "line", "reservation_id": "hold",
                         "shipment_id": "shipment"},
    "inventory_movement": {"movement_id": "movement", "allocation_id": "allocation", "shipment_id": "shipment",
                           "related_movement_id": "movement", "source_doc": "document"},
    "allocation_decision": {"order_line_id": "line"},
    "order_event": {"order_id": "order", "order_line_id": "line"},
    "reservation_event": {"reservation_id": "hold"},
    "shipment_event": {"shipment_id": "shipment"},
}

# Plain running numbers that nothing else points at, and that shift when rows are added.
NUMBERS_TO_SKIP = {"event_id", "decision_id"}

# What a row says, without the IDs that get renumbered. Used to find where it ended up.
ORDER_FIELDS = ["channel_code", "customer_ref", "ship_to_region", "placed_at", "status", "order_total"]
SHIPMENT_FIELDS = ["shipment_type", "status", "from_location_id", "to_location_id", "created_at", "original_eta_at",
                   "transfer_sku_id", "transfer_qty"]
MOVEMENT_FIELDS = ["sku_id", "location_id", "movement_type", "stock_bucket", "quantity", "occurred_at",
                   "reason_code", "movement_group_id"]


def read_rows(folder, table):
    with open(folder / f"{table}.csv", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def order_of_line(order_line_id):
    return order_line_id[:-len("-L01")]


def order_of_hold(reservation_id):
    return reservation_id[len("RES-"):-len("-01")]


def match_by_content(clean_rows, final_rows, id_column, content_of_clean, content_of_final):
    """Returns clean ID to final ID. A row with nothing the same in the final data has no entry."""
    waiting = {}
    for row in final_rows:
        waiting.setdefault(content_of_final(row), deque()).append(row[id_column])
    mapping = {}
    for row in clean_rows:
        matches = waiting.get(content_of_clean(row))
        if matches:
            mapping[row[id_column]] = matches.popleft()
    return mapping


def fields_of(row, fields):
    return tuple(row[field] for field in fields)


def build_order_maps(clean, final):
    """An order's lines and holds carry its ID inside their own, so they follow from it."""
    orders = match_by_content(clean["orders"], final["orders"], "order_id",
                              lambda row: fields_of(row, ORDER_FIELDS), lambda row: fields_of(row, ORDER_FIELDS))
    lines = {}
    for row in clean["order_line"]:
        new_order = orders.get(row["order_id"])
        if new_order:
            lines[row["order_line_id"]] = new_order + row["order_line_id"][-len("-L01"):]
    holds = {}
    for row in clean["reservation"]:
        new_order = orders.get(order_of_hold(row["reservation_id"]))
        if new_order:
            holds[row["reservation_id"]] = f"RES-{new_order}{row['reservation_id'][-len('-01'):]}"
    return {"order": orders, "line": lines, "hold": holds}


def build_shipment_map(clean, final, maps):
    return match_by_content(
        clean["shipment"], final["shipment"], "shipment_id",
        lambda row: fields_of(row, SHIPMENT_FIELDS) + (maps["order"].get(row["order_id"]) or "",),
        lambda row: fields_of(row, SHIPMENT_FIELDS) + (row["order_id"],),
    )


def build_allocation_map(clean, final, maps):
    """Each allocation has one hold, and the hold's new ID is already known."""
    final_by_hold = {row["reservation_id"]: row["allocation_id"] for row in final["order_allocation"]}
    mapping = {}
    for row in clean["order_allocation"]:
        new_id = final_by_hold.get(maps["hold"].get(row["reservation_id"]))
        if new_id:
            mapping[row["allocation_id"]] = new_id
    return mapping


def build_movement_map(clean, final, maps):
    return match_by_content(
        clean["inventory_movement"], final["inventory_movement"], "movement_id",
        lambda row: fields_of(row, MOVEMENT_FIELDS) + (maps["shipment"].get(row["shipment_id"]) or "",
                                                       maps["allocation"].get(row["allocation_id"]) or ""),
        lambda row: fields_of(row, MOVEMENT_FIELDS) + (row["shipment_id"], row["allocation_id"]),
    )


def build_document_map(clean, final, maps):
    """Documents are numbered again too. Each one has to keep to exactly one new number."""
    final_documents = {row["movement_id"]: row["source_doc"] for row in final["inventory_movement"]}
    mapping = {}
    problems = []
    for row in clean["inventory_movement"]:
        new_id = maps["movement"].get(row["movement_id"])
        if new_id is None:
            continue
        new_document = final_documents[new_id]
        if mapping.setdefault(row["source_doc"], new_document) != new_document:
            problems.append(f"document {row['source_doc']} became two documents")
    if len(set(mapping.values())) != len(mapping):
        problems.append("two clean documents became one document")
    return mapping, problems


def rewritten(row, table, maps):
    """A clean row as it should read in the final data. A link with no new ID can't match anything."""
    values = []
    links = ROWS_TO_COMPARE[table]
    for column in TABLE_COLUMNS[table]:
        if column in NUMBERS_TO_SKIP:
            continue
        value = row[column]
        if column in links and value:
            value = maps[links[column]].get(value, f"NO NEW ID FOR {value}")
        values.append(value)
    return tuple(values)


def as_it_reads(row, table):
    return tuple(row[column] for column in TABLE_COLUMNS[table] if column not in NUMBERS_TO_SKIP)


def compare_table(table, clean_rows, final_rows, maps, belongs_to_a_case):
    """Returns the problems found in this table."""
    expected = Counter(rewritten(row, table, maps) for row in clean_rows)
    added = []
    for row in final_rows:
        content = as_it_reads(row, table)
        if expected[content] > 0:
            expected[content] -= 1
        else:
            added.append(row)
    missing = sum(expected.values())
    unlabelled = [row for row in added if not belongs_to_a_case(row)]
    print(f"  {table:20} {missing:>6} clean rows changed or missing  {len(added):>6} new rows  {len(unlabelled):>6} not labelled")
    problems = []
    if missing:
        problems.append(f"{missing} clean {table} rows changed or went missing")
    if unlabelled:
        problems.append(f"{len(unlabelled)} new {table} rows don't belong to a labelled case")
    return problems


def compare_balances(clean_dir, final_dir, case_skus):
    """Same IDs in both. Opening rows can't change, and a closing row only on a SKU with a case."""
    def by_pair(folder):
        return {(row["sku_id"], row["location_id"], row["snapshot_type"]): (row["balance_id"], row["on_hand"], row["blocked"])
                for row in read_rows(folder, "inventory_balance")}
    clean = by_pair(clean_dir)
    final = by_pair(final_dir)

    problems = []
    one_sided = set(clean) ^ set(final)
    if one_sided:
        problems.append(f"{len(one_sided)} balance rows are only in one of the two datasets")
    both = set(clean) & set(final)
    if any(clean[key] != final[key] for key in both if key[2] == "OPENING"):
        problems.append("opening balances differ")
    changed = [key for key in both if key[2] == "CLOSING" and clean[key] != final[key]]
    outside = [key for key in changed if key[0] not in case_skus]
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

    clean = {table: read_rows(clean_dir, table) for table in ROWS_TO_COMPARE}
    final = {table: read_rows(final_dir, table) for table in ROWS_TO_COMPARE}

    maps = build_order_maps(clean, final)
    maps["shipment"] = build_shipment_map(clean, final, maps)
    maps["allocation"] = build_allocation_map(clean, final, maps)
    maps["movement"] = build_movement_map(clean, final, maps)
    maps["document"], document_problems = build_document_map(clean, final, maps)
    problems += document_problems

    # A labelled order's parcel is part of the case too.
    all_case_shipments = case_shipments | {row["shipment_id"] for row in final["shipment"] if row["order_id"] in case_orders}
    belongs = {
        "orders": lambda row: row["order_id"] in case_orders,
        "order_line": lambda row: row["order_id"] in case_orders,
        "shipment": lambda row: row["shipment_id"] in all_case_shipments,
        "reservation": lambda row: order_of_line(row["order_line_id"]) in case_orders,
        "order_allocation": lambda row: order_of_line(row["order_line_id"]) in case_orders,
        "inventory_movement": lambda row: row["movement_id"] in case_movements or row["shipment_id"] in all_case_shipments,
        "allocation_decision": lambda row: order_of_line(row["order_line_id"]) in case_orders,
        "order_event": lambda row: row["order_id"] in case_orders,
        "reservation_event": lambda row: order_of_hold(row["reservation_id"]) in case_orders,
        "shipment_event": lambda row: row["shipment_id"] in all_case_shipments,
    }
    for table in ROWS_TO_COMPARE:
        problems += compare_table(table, clean[table], final[table], maps, belongs[table])
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
