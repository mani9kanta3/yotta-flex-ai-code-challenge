"""
The generator command. Writes CSV files and a manifest, and never touches the
database.

Run:  python -m scripts.generate --scale starter --seed 20261022

Three folders come out of one run:

    data/<scale>_clean/        the valid baseline, before anything is injected
    data/<scale>/              the final data, with labelled problems and decoys
    data/ground_truth/<scale>/ the answers, kept away from the data detectors read

The final data hits the targets exactly. The baseline is smaller by exactly what gets
injected.
"""

import argparse
import random
import time
from pathlib import Path

from app import config
from generator import checkpoints, export, inject, master, settings, simulate


def write_tables(out_dir, tables):
    out_dir.mkdir(parents=True, exist_ok=True)
    row_counts = {}
    for table, rows in tables.items():
        row_counts[table] = export.write_rows(out_dir, table, rows)
    return row_counts


def ledger_tables(ledger):
    """Every table the simulation decides. Worked out from the ledger each time it is asked."""
    return {
        "inventory_balance": export.balance_rows(ledger),
        "orders": export.order_rows(ledger),
        "order_line": export.order_line_rows(ledger),
        "shipment": export.shipment_rows(ledger),
        "reservation": export.reservation_rows(ledger),
        "order_allocation": export.allocation_rows(ledger),
        "inventory_movement": export.movement_rows(ledger),
        "allocation_decision": export.decision_rows(ledger),
        "order_event": export.order_event_rows(ledger),
        "reservation_event": export.reservation_event_rows(ledger),
        "shipment_event": export.shipment_event_rows(ledger),
    }


def generate(scale, seed, out_dir):
    # One seeded generator passed everywhere, so nothing depends on global state.
    rng = random.Random(seed)
    clean = settings.clean_scale(scale)

    products, skus = master.build_products_and_skus(rng, clean)
    locations = master.build_locations(rng, clean)
    channel_locations = master.build_channel_locations(locations)
    pairs = master.build_pairs(rng, clean, skus, locations)
    replenishment_rules = master.build_rules(pairs)
    master.build_opening_stock(rng, pairs)

    master_tables = {
        "product": products,
        "sku": skus,
        "location": locations,
        "channel": export.channel_rows(master.CHANNELS),
        "channel_location": channel_locations,
        "sku_location": pairs,
        "replenishment_rule": replenishment_rules,
    }

    ledger = simulate.simulate(rng, pairs, clean["movements"], channel_locations, clean["orders"])

    # The clean baseline, written before anything is added to it.
    clean_dir = export.clean_dir_of(out_dir)
    row_counts = write_tables(clean_dir, {**master_tables, **ledger_tables(ledger)})
    export.write_manifest(clean_dir, clean, seed, row_counts, dict(ledger.counters), "clean baseline")

    # Then the labelled problems and decoys, and everything numbered again in time order.
    cases = inject.inject(ledger, rng, scale)
    maps = inject.renumber(ledger)
    tables = ledger_tables(ledger)
    inject.attach_closing_rows(ledger, tables["inventory_balance"], cases)
    inject.tamper_balances(tables["inventory_balance"], cases)

    row_counts = write_tables(out_dir, {**master_tables, **tables})
    truth_rows = inject.ground_truth_rows(ledger, cases, maps, tables["inventory_balance"])
    ground_truth = export.write_ground_truth(export.truth_dir_of(out_dir), truth_rows)
    checkpoint_rows = checkpoints.build_checkpoints(ledger, cases, scale["checkpoints_per_kind"])
    checkpoint_file = export.write_checkpoints(export.truth_dir_of(out_dir), checkpoint_rows)
    return export.write_manifest(out_dir, scale, seed, row_counts, ledger.counters, "final", ground_truth, checkpoint_file)


def main():
    parser = argparse.ArgumentParser(description="Generate the inventory dataset.")
    parser.add_argument("--scale", choices=list(settings.SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    parser.add_argument("--out", help="output folder, default data/<scale>")
    parser.add_argument("--skus", type=int)
    parser.add_argument("--locations", type=int)
    parser.add_argument("--movements", type=int)
    parser.add_argument("--orders", type=int)
    parser.add_argument("--anomalies", type=int, help="labelled problems per type")
    parser.add_argument("--decoys", type=int, help="decoys per type")
    args = parser.parse_args()

    scale = settings.get_scale(args.scale, args.skus, args.locations, args.movements, args.orders,
                               args.anomalies, args.decoys)
    out_dir = Path(args.out) if args.out else config.DATA_DIR / args.scale

    started = time.perf_counter()
    manifest = generate(scale, args.seed, out_dir)
    seconds = time.perf_counter() - started

    print(f"scale={scale['name']} seed={args.seed} out={out_dir}")
    for file_name, info in manifest["files"].items():
        print(f"  {file_name:28} {info['rows']:>10,} rows  {info['sha256'][:12]}")
    truth = manifest["ground_truth_file"]
    print(f"  {'ground_truth.csv':28} {truth['rows']:>10,} rows  {truth['sha256'][:12]}")
    points = manifest["atp_checkpoints_file"]
    print(f"  {'atp_checkpoints.csv':28} {points['rows']:>10,} rows  {points['sha256'][:12]}")
    print(f"  counters: {manifest['generation_counters']}")
    print(f"  NOT GENERATED YET: {', '.join(manifest['not_generated_yet'])}")
    print(f"  took {seconds:.1f}s")


if __name__ == "__main__":
    main()
