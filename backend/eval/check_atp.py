"""
Compare the ATP checkpoints with what app/atp.py answers from the loaded database.

Run:  python -m eval.check_atp --scale starter

The checkpoints were worked out by generator/checkpoints.py in plain Python, one second
before and at the moment a hold expired, a truck arrived or a hold was cancelled. These are
times the decision recheck doesn't look at. If the two calculations differ, or a kind of
checkpoint is missing, this command exits 1.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from app import atp, config, db
from generator.checkpoints import KINDS
from generator.export import truth_dir_of
from generator.settings import SCALES

COMPARED = ["on_hand", "blocked", "reserved", "inbound", "atp", "promisable"]


def load_checkpoints(truth_dir):
    with open(truth_dir / "atp_checkpoints.csv", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def differences_in(cursor, row):
    """Every number where the database gives another answer. Empty means they agree."""
    moment = datetime.fromisoformat(row["as_of_at"])
    positions = atp.get_positions(cursor, row["sku_id"], row["channel_code"], moment)
    position = next((p for p in positions if p["location_id"] == row["location_id"]), None)
    if position is None:
        return {"location": ("a position", "none")}
    return {name: (int(row[name]), position[name]) for name in COMPARED if int(row[name]) != position[name]}


def check_checkpoints(cursor, rows):
    """Returns the match count per kind, and each row that differs."""
    results = {kind: {"checked": 0, "matched": 0} for kind in KINDS}
    differing = []
    for row in rows:
        differences = differences_in(cursor, row)
        results[row["kind"]]["checked"] += 1
        if differences:
            differing.append({"checkpoint_id": row["checkpoint_id"], "differences": differences})
        else:
            results[row["kind"]]["matched"] += 1
    return results, differing


def missing_kinds(results):
    """A kind with no checkpoints would otherwise pass by checking nothing."""
    return [kind for kind, result in results.items() if result["checked"] == 0]


def main():
    parser = argparse.ArgumentParser(description="Compare ATP checkpoints with the database.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the final data, default data/<scale>")
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale
    truth_dir = truth_dir_of(data_dir)

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            results, differing = check_checkpoints(cursor, load_checkpoints(truth_dir))
    finally:
        connection.close()

    for kind, result in results.items():
        print(f"  {kind:20} {result['matched']:>4} of {result['checked']:>4} match")
    for row in differing[:20]:
        print(f"  DIFFERS  {row['checkpoint_id']}  {row['differences']}")

    missing = missing_kinds(results)
    if missing:
        print(f"  NO CHECKPOINTS for: {', '.join(missing)}")

    with open(truth_dir / "atp_checkpoint_report.json", "w", encoding="utf-8", newline="\n") as file:
        json.dump({"results": results, "differing": differing, "missing_kinds": missing}, file, indent=2)
        file.write("\n")

    if differing or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
