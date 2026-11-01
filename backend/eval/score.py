"""
Compare what the detectors found with the ground truth, per anomaly type.

Run:  python -m eval.score --scale starter

Only this folder reads the ground truth. The detectors never see it.

    precision = of what was flagged, how much was really a problem
    recall    = of the real problems, how many were flagged
    decoys    = valid records that look wrong. Flagging one is a false positive.

The same person wrote the anomalies and the detectors, so 100% here shows the two are
consistent. It doesn't show real-world accuracy.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from app import config, db
from generator.export import truth_dir_of
from generator.settings import ISSUE_TYPES, SCALES

EXCEPTIONS_SQL = """
SELECT issue_type, COALESCE(balance_id, reservation_id, movement_id, shipment_id)
FROM inventory_exception
"""


def load_ground_truth(truth_dir):
    with open(truth_dir / "ground_truth.csv", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def ratio(top, bottom):
    """None when it can't be worked out, so nothing looks like a score when it isn't one."""
    return round(top / bottom, 4) if bottom else None


def score_issue(issue_type, flagged, truth_rows):
    """flagged is the set of record IDs this detector found."""
    real = {row["record_id"] for row in truth_rows if row["expected_issue"] == issue_type}
    decoys = {row["record_id"] for row in truth_rows
              if row["expected_issue"] == "NONE" and row["looks_like"] == issue_type}
    true_positives = len(flagged & real)
    false_positives = len(flagged - real)
    false_negatives = len(real - flagged)
    precision = ratio(true_positives, true_positives + false_positives)
    recall = ratio(true_positives, true_positives + false_negatives)
    f1 = None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {
        "labelled": len(real),
        "flagged": len(flagged),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "decoys": len(decoys),
        "decoys_flagged": len(flagged & decoys),
    }


def score(exceptions, truth_rows):
    """exceptions is a list of (issue_type, record_id)."""
    results = {}
    for issue_type in ISSUE_TYPES:
        flagged = {record_id for found_issue, record_id in exceptions if found_issue == issue_type}
        results[issue_type] = score_issue(issue_type, flagged, truth_rows)
    return results


def main():
    parser = argparse.ArgumentParser(description="Score the detectors against the ground truth.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the final data, default data/<scale>")
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale
    truth_dir = truth_dir_of(data_dir)

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(EXCEPTIONS_SQL)
            exceptions = cursor.fetchall()
    finally:
        connection.close()

    results = score(exceptions, load_ground_truth(truth_dir))
    print(f"{'issue type':32}{'labelled':>9}{'flagged':>9}{'TP':>6}{'FP':>6}{'FN':>6}"
          f"{'precision':>11}{'recall':>8}{'decoys':>8}{'flagged':>9}")
    for issue_type, result in results.items():
        print(f"{issue_type:32}{result['labelled']:>9}{result['flagged']:>9}{result['true_positives']:>6}"
              f"{result['false_positives']:>6}{result['false_negatives']:>6}{str(result['precision']):>11}"
              f"{str(result['recall']):>8}{result['decoys']:>8}{result['decoys_flagged']:>9}")

    with open(truth_dir / "metrics.json", "w", encoding="utf-8", newline="\n") as file:
        json.dump(results, file, indent=2)
        file.write("\n")

    missed = sum(result["false_negatives"] + result["false_positives"] for result in results.values())
    if missed:
        sys.exit(1)


if __name__ == "__main__":
    main()
