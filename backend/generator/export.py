"""
Writes the CSV files and the source manifest.

Closing balances are worked out here from opening stock plus the ledger, and
then compared with the simulator's running count. Two separate calculations
have to agree, or the run stops.

Nothing in these files depends on the clock or on the machine, so the same seed
gives byte-identical files.
"""

import csv
import hashlib
import json
import platform

from app import config
from generator import settings

# Parent tables first. load_data.py loads them in this same order.
TABLE_COLUMNS = {
    "product": ["product_id", "product_name", "category", "brand", "status", "launch_date"],
    "sku": ["sku_id", "product_id", "size", "colour", "unit_price", "status",
            "effective_from", "effective_to"],
    "location": ["location_id", "location_name", "location_type", "region", "parent_location_id",
                 "ships_online", "opened_on", "closed_on"],
    "channel": ["channel_code", "is_online", "counts_inbound", "applies_safety_stock", "hold_minutes"],
    "channel_location": ["channel_location_id", "channel_code", "location_id", "buffer_units",
                         "effective_from", "effective_to"],
    "sku_location": ["sku_id", "location_id", "ranged_from"],
    "replenishment_rule": ["rule_id", "sku_id", "location_id", "source_location_id", "safety_stock",
                           "reorder_point", "reorder_qty", "lead_time_days", "allow_inbound_atp",
                           "effective_from", "effective_to"],
    "inventory_balance": ["balance_id", "sku_id", "location_id", "snapshot_type", "as_of_at",
                          "on_hand", "blocked"],
    "orders": ["order_id", "channel_code", "customer_ref", "ship_to_region", "placed_at", "status",
               "order_total"],
    "order_line": ["order_line_id", "order_id", "line_no", "sku_id", "qty", "unit_price", "line_amount", "status"],
    "shipment": ["shipment_id", "shipment_type", "status", "order_id", "from_location_id", "to_location_id",
                 "created_at", "original_eta_at", "transfer_sku_id", "transfer_qty"],
    "reservation": ["reservation_id", "order_line_id", "sku_id", "location_id", "qty", "supply_type",
                    "inbound_shipment_id", "created_at", "expires_at", "promised_ship_by", "status"],
    "order_allocation": ["allocation_id", "order_line_id", "location_id", "qty", "reservation_id",
                         "shipment_id", "allocated_at"],
    "inventory_movement": ["movement_id", "sku_id", "location_id", "movement_type", "stock_bucket",
                           "quantity", "occurred_at", "reason_code", "source_doc", "movement_group_id",
                           "allocation_id", "shipment_id", "related_movement_id"],
    "allocation_decision": ["decision_id", "order_line_id", "decided_at", "requested_qty", "decision_code",
                            "reason_code", "chosen_location_id", "atp_seen", "promisable_seen", "candidates"],
    "order_event": ["event_id", "order_id", "order_line_id", "from_status", "to_status", "occurred_at",
                    "reason_code"],
    "reservation_event": ["event_id", "reservation_id", "from_status", "to_status", "occurred_at", "reason_code",
                          "promised_ship_by"],
    "shipment_event": ["event_id", "shipment_id", "from_status", "to_status", "occurred_at",
                       "reason_code", "eta_at"],
}


GROUND_TRUTH_COLUMNS = ["expected_issue", "looks_like", "record_type", "record_id", "sku_id", "order_id",
                        "expected_value", "actual_value", "expected_decision"]

CHECKPOINT_COLUMNS = ["checkpoint_id", "kind", "moment", "record_id", "reason_code", "event_qty", "sku_id",
                      "location_id", "channel_code", "as_of_at", "on_hand", "blocked", "reserved", "inbound",
                      "atp", "promisable"]


def csv_value(value):
    """Empty means NULL. A plain str() keeps the output the same on every machine."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def write_rows(out_dir, table, rows):
    columns = TABLE_COLUMNS[table]
    path = out_dir / f"{table}.csv"
    # A fixed line ending, or Windows and Linux would give different checksums.
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([csv_value(row[column]) for column in columns])
    return len(rows)


def channel_rows(channels):
    return [
        {"channel_code": code, "is_online": online, "counts_inbound": inbound,
         "applies_safety_stock": safety, "hold_minutes": hold}
        for code, online, inbound, safety, hold in channels
    ]


def movement_id_of(row_position):
    return f"MOV-2026-{row_position + 1:08d}"


def shipment_id_of(shipment_index):
    return f"SHP-2026-{shipment_index + 1:06d}"


def order_id_of(order_index):
    return f"ORD-2026-{order_index + 1:06d}"


def line_id_of(order_index, line_no):
    return f"{order_id_of(order_index)}-L{line_no:02d}"


def reservation_id_of(hold):
    return f"RES-{order_id_of(hold['order_index'])}-{hold['hold_no']:02d}"


def allocation_id_of(allocation_no):
    return f"ALC-2026-{allocation_no:07d}"


def order_rows(ledger):
    return [
        {
            "order_id": order_id_of(order["index"]),
            "channel_code": order["channel_code"],
            "customer_ref": order["customer_ref"],
            "ship_to_region": order["region"],
            "placed_at": order["placed_at"],
            "status": order["status"],
            "order_total": order["total"],
        }
        for order in ledger.orders
    ]


def order_line_rows(ledger):
    rows = []
    for order in ledger.orders:
        for line in order["lines"]:
            rows.append({
                "order_line_id": line_id_of(order["index"], line["line_no"]),
                "order_id": order_id_of(order["index"]),
                "line_no": line["line_no"],
                "sku_id": line["sku_id"],
                "qty": line["qty"],
                "unit_price": line["unit_price"],
                "line_amount": line["qty"] * line["unit_price"],
                "status": line["status"],
            })
    return rows


def reservation_rows(ledger):
    # Sorted, so a row's place in the file says nothing about how it was made.
    return [
        {
            "reservation_id": reservation_id_of(hold),
            "order_line_id": line_id_of(hold["order_index"], hold["line"]["line_no"]),
            "sku_id": hold["sku_id"],
            "location_id": hold["location_id"],
            "qty": hold["qty"],
            "supply_type": hold["supply_type"],
            "inbound_shipment_id": None if hold["shipment_index"] is None else shipment_id_of(hold["shipment_index"]),
            "created_at": hold["created_at"],
            "expires_at": hold["expires_at"],
            "promised_ship_by": hold["first_promise"],
            "status": hold["status"],
        }
        for hold in sorted(ledger.holds, key=reservation_id_of)
    ]


def allocation_rows(ledger):
    rows = []
    with_allocation = [hold for hold in ledger.holds if hold["allocation_no"] is not None]
    for hold in sorted(with_allocation, key=lambda hold: hold["allocation_no"]):
        customer_shipment = hold["customer_shipment"]
        rows.append({
            "allocation_id": allocation_id_of(hold["allocation_no"]),
            "order_line_id": line_id_of(hold["order_index"], hold["line"]["line_no"]),
            "location_id": hold["location_id"],
            "qty": hold["qty"],
            "reservation_id": reservation_id_of(hold),
            "shipment_id": None if customer_shipment is None else shipment_id_of(customer_shipment),
            "allocated_at": hold["created_at"],
        })
    return rows


def decision_rows(ledger):
    rows = []
    for number, decision in enumerate(ledger.decisions, start=1):
        rows.append({
            "decision_id": f"DEC-2026-{number:07d}",
            "order_line_id": line_id_of(decision["order_index"], decision["line_no"]),
            "decided_at": decision["decided_at"],
            "requested_qty": decision["requested_qty"],
            "decision_code": decision["decision_code"],
            "reason_code": decision["reason_code"],
            "chosen_location_id": decision["chosen_location_id"],
            "atp_seen": decision["atp_seen"],
            "promisable_seen": decision["promisable_seen"],
            "candidates": decision["candidates"],
        })
    return rows


def order_event_rows(ledger):
    """A sort that keeps ties in the order written, so PLACED stays before ALLOCATED."""
    ordered = sorted(ledger.order_events, key=lambda event: event[0])
    rows = []
    for number, event in enumerate(ordered, start=1):
        moment, order_index, line_no, from_status, to_status, reason = event
        rows.append({
            "event_id": number,
            "order_id": order_id_of(order_index),
            "order_line_id": None if line_no is None else line_id_of(order_index, line_no),
            "from_status": from_status,
            "to_status": to_status,
            "occurred_at": moment,
            "reason_code": reason,
        })
    return rows


def reservation_event_rows(ledger):
    ordered = sorted(ledger.reservation_events, key=lambda event: event[0])
    rows = []
    for number, event in enumerate(ordered, start=1):
        moment, hold_index, from_status, to_status, reason, promised = event
        rows.append({
            "event_id": number,
            "reservation_id": reservation_id_of(ledger.holds[hold_index]),
            "from_status": from_status,
            "to_status": to_status,
            "occurred_at": moment,
            "reason_code": reason,
            "promised_ship_by": promised,
        })
    return rows


def movement_rows(ledger):
    """Numbered only here, in time order, so an ID says nothing about how a row was made."""
    rows = []
    for position, row in enumerate(ledger.movements):
        (pair_index, movement_type, bucket, quantity, moment, reason, source_doc, group, shipment_index,
         related_row, allocation_no) = row
        pair = ledger.pairs[pair_index]
        rows.append({
            "movement_id": movement_id_of(position),
            "sku_id": pair["sku_id"],
            "location_id": pair["location_id"],
            "movement_type": movement_type,
            "stock_bucket": bucket,
            "quantity": quantity,
            "occurred_at": moment,
            "reason_code": reason,
            "source_doc": source_doc,
            "movement_group_id": group,
            "allocation_id": None if allocation_no is None else allocation_id_of(allocation_no),
            "shipment_id": None if shipment_index is None else shipment_id_of(shipment_index),
            "related_movement_id": None if related_row is None else movement_id_of(related_row),
        })
    return rows


def shipment_rows(ledger):
    rows = []
    for index, shipment in enumerate(ledger.shipments):
        if shipment["shipment_type"] == "CUSTOMER":
            rows.append({
                "shipment_id": shipment_id_of(index),
                "shipment_type": "CUSTOMER",
                "status": shipment["status"],
                "order_id": order_id_of(shipment["order_index"]),
                "from_location_id": shipment["location_id"],
                "to_location_id": None,
                "created_at": shipment["shipped_at"],
                "original_eta_at": None,
                "transfer_sku_id": None,
                "transfer_qty": None,
            })
            continue
        source = ledger.pairs[shipment["source_index"]]
        destination = ledger.pairs[shipment["destination_index"]]
        rows.append({
            "shipment_id": shipment_id_of(index),
            "shipment_type": "TRANSFER",
            "status": shipment["status"],
            "order_id": None,
            "from_location_id": source["location_id"],
            "to_location_id": destination["location_id"],
            "created_at": shipment["shipped_at"],
            "original_eta_at": shipment["original_eta_at"],
            "transfer_sku_id": source["sku_id"],
            "transfer_qty": shipment["qty"],
        })
    return rows


def shipment_event_rows(ledger):
    """A sort that keeps ties in the order written, so CREATED stays before IN_TRANSIT."""
    ordered = sorted(ledger.shipment_events, key=lambda event: (event[0], event[1]))
    rows = []
    for number, event in enumerate(ordered, start=1):
        moment, shipment_index, from_status, to_status, reason, eta_at = event
        rows.append({
            "event_id": number,
            "shipment_id": shipment_id_of(shipment_index),
            "from_status": from_status,
            "to_status": to_status,
            "occurred_at": moment,
            "reason_code": reason,
            "eta_at": eta_at,
        })
    return rows


def balance_rows(ledger):
    """OPENING from the inputs, CLOSING derived from OPENING plus the ledger."""
    sellable_change = {}
    blocked_change = {}
    for row in ledger.movements:
        pair_index, _type, bucket, quantity = row[0], row[1], row[2], row[3]
        changes = sellable_change if bucket == "SELLABLE" else blocked_change
        changes[pair_index] = changes.get(pair_index, 0) + quantity

    rows = []
    for snapshot_type, as_of_at in (("OPENING", config.START_AT), ("CLOSING", config.AS_OF)):
        for index, pair in enumerate(ledger.pairs):
            on_hand = pair["opening_on_hand"]
            blocked = pair["opening_blocked"]
            if snapshot_type == "CLOSING":
                on_hand += sellable_change.get(index, 0) + blocked_change.get(index, 0)
                blocked += blocked_change.get(index, 0)
                if on_hand - blocked != ledger.sellable[index] or blocked != ledger.blocked[index]:
                    raise RuntimeError(f"ledger and running count disagree for {pair['sku_id']} "
                                       f"at {pair['location_id']}")
            rows.append({
                "balance_id": f"BAL-{len(rows) + 1:07d}",
                "sku_id": pair["sku_id"],
                "location_id": pair["location_id"],
                "snapshot_type": snapshot_type,
                "as_of_at": as_of_at,
                "on_hand": on_hand,
                "blocked": blocked,
            })
    return rows


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_dir_of(out_dir):
    """The baseline before anything is injected, beside the final data."""
    return out_dir.parent / f"{out_dir.name}_clean"


def truth_dir_of(out_dir):
    """Ground truth stays out of the folder the loader and the detectors read."""
    return out_dir.parent / "ground_truth" / out_dir.name


def write_answer_file(truth_dir, file_name, columns, rows):
    truth_dir.mkdir(parents=True, exist_ok=True)
    path = truth_dir / file_name
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([csv_value(row[column]) for column in columns])
    return {"rows": len(rows), "sha256": sha256_of(path)}


def write_ground_truth(truth_dir, rows):
    return write_answer_file(truth_dir, "ground_truth.csv", GROUND_TRUTH_COLUMNS, rows)


def write_checkpoints(truth_dir, rows):
    return write_answer_file(truth_dir, "atp_checkpoints.csv", CHECKPOINT_COLUMNS, rows)


def write_manifest(out_dir, scale, seed, row_counts, counters, dataset, ground_truth=None, checkpoints=None):
    files = {}
    for table in TABLE_COLUMNS:
        file_name = f"{table}.csv"
        files[file_name] = {"rows": row_counts[table], "sha256": sha256_of(out_dir / file_name)}

    manifest = {
        "generator_command": f"python -m scripts.generate --scale {scale['name']} --seed {seed}",
        "seed": seed,
        "dataset": dataset,
        "scale": scale,
        "period": {"start": str(config.START_AT), "as_of": str(config.AS_OF)},
        "python_version": platform.python_version(),
        "data_source": {
            "type": "synthetic",
            "external_data_used": False,
            "note": "All rows come from this generator. Brands are invented labels, "
                    "city names are ordinary place names, and there is no personal data.",
        },
        "generation_counters": counters,
        "not_generated_yet": settings.NOT_GENERATED_YET,
        "files": files,
    }
    # Only the checksum, so the two can be matched without the answers being here.
    if ground_truth is not None:
        manifest["ground_truth_file"] = ground_truth
        manifest["atp_checkpoints_file"] = checkpoints
    path = out_dir / "source_manifest.json"
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")
    return manifest
