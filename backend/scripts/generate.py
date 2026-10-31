"""
The generator command. Writes CSV files and a manifest, and never touches the
database.

Run:  python -m scripts.generate --scale starter --seed 20261022
"""

import argparse
import random
import time
from pathlib import Path

from app import config
from generator import export, master, settings, simulate


def generate(scale, seed, out_dir):
    # One seeded generator passed everywhere, so nothing depends on global state.
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)

    products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    channel_locations = master.build_channel_locations(locations)
    pairs = master.build_pairs(rng, scale, skus, locations)
    rules = master.build_rules(pairs)
    master.build_opening_stock(rng, pairs)

    ledger = simulate.simulate(rng, pairs, scale["movements"])

    tables = {
        "product": products,
        "sku": skus,
        "location": locations,
        "channel": export.channel_rows(master.CHANNELS),
        "channel_location": channel_locations,
        "sku_location": pairs,
        "replenishment_rule": rules,
        "inventory_balance": export.balance_rows(ledger),
        "inventory_movement": export.movement_rows(ledger),
    }

    row_counts = {}
    for table, rows in tables.items():
        row_counts[table] = export.write_rows(out_dir, table, rows)

    return export.write_manifest(out_dir, scale, seed, row_counts, ledger.counters)


def main():
    parser = argparse.ArgumentParser(description="Generate the inventory dataset.")
    parser.add_argument("--scale", choices=list(settings.SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    parser.add_argument("--out", help="output folder, default data/<scale>")
    parser.add_argument("--skus", type=int)
    parser.add_argument("--locations", type=int)
    parser.add_argument("--movements", type=int)
    parser.add_argument("--orders", type=int)
    args = parser.parse_args()

    scale = settings.get_scale(args.scale, args.skus, args.locations, args.movements, args.orders)
    out_dir = Path(args.out) if args.out else config.DATA_DIR / args.scale

    started = time.perf_counter()
    manifest = generate(scale, args.seed, out_dir)
    seconds = time.perf_counter() - started

    print(f"scale={scale['name']} seed={args.seed} out={out_dir}")
    for file_name, info in manifest["files"].items():
        print(f"  {file_name:28} {info['rows']:>10,} rows  {info['sha256'][:12]}")
    print(f"  counters: {manifest['generation_counters']}")
    print(f"  took {seconds:.1f}s")


if __name__ == "__main__":
    main()
