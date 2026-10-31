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
    "inventory_movement": ["movement_id", "sku_id", "location_id", "movement_type", "stock_bucket",
                           "quantity", "occurred_at", "reason_code", "source_doc", "movement_group_id",
                           "allocation_id", "shipment_id", "related_movement_id"],
}


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


def movement_rows(ledger):
    """Numbered only here, in time order, so an ID says nothing about how a row was made."""
    rows = []
    for number, row in enumerate(ledger.movements, start=1):
        pair_index, movement_type, bucket, quantity, moment, reason, source_doc, group = row
        pair = ledger.pairs[pair_index]
        rows.append({
            "movement_id": f"MOV-2026-{number:08d}",
            "sku_id": pair["sku_id"],
            "location_id": pair["location_id"],
            "movement_type": movement_type,
            "stock_bucket": bucket,
            "quantity": quantity,
            "occurred_at": moment,
            "reason_code": reason,
            "source_doc": source_doc,
            "movement_group_id": group,
            "allocation_id": None,
            "shipment_id": None,
            "related_movement_id": None,
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


def write_manifest(out_dir, scale, seed, row_counts, counters):
    files = {}
    for table in TABLE_COLUMNS:
        file_name = f"{table}.csv"
        files[file_name] = {"rows": row_counts[table], "sha256": sha256_of(out_dir / file_name)}

    manifest = {
        "generator_command": f"python -m scripts.generate --scale {scale['name']} --seed {seed}",
        "seed": seed,
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
        "files": files,
    }
    path = out_dir / "source_manifest.json"
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        json.dump(manifest, file, indent=2)
        file.write("\n")
    return manifest
