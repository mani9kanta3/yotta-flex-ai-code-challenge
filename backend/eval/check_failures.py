"""
Sort the validator's failures on the final data into expected and unexpected.

Run:  python -m scripts.validate --scale starter
      python -m eval.check_failures --scale starter

The validator doesn't know what was injected, so it reports every failure and exits 1.
This reads its summary next to the ground truth and compares record IDs, never only counts.
One labelled record missed plus one unrelated record caught would give the right count, and
the two mistakes would hide each other.

Anything that doesn't match exactly is unexpected, and this command exits 1. That includes a
labelled problem the validator missed, a check that didn't report its IDs, and a check that
is missing from the summary altogether.
"""

import argparse
import json
import sys
from pathlib import Path

from app import config
from eval.score import load_ground_truth
from generator.export import truth_dir_of
from generator.settings import ISSUE_BALANCE, ISSUE_BLOCKED, ISSUE_RESERVATION, ISSUE_TRANSFER, SCALES
from scripts.validate import make_result, required_check_names

# The check each labelled problem has to fail, and which column of the ground truth holds
# the ID that check reports. The network check adds up a whole SKU, so it reports the SKU.
EXPECTED_CHECKS = {
    "closing balance does not reconcile to opening plus ledger": (ISSUE_BALANCE, "record_id"),
    "network stock plus in transit differs from opening plus outside changes": (ISSUE_BALANCE, "sku_id"),
    "hold above what could be promised when it was placed": (ISSUE_RESERVATION, "record_id"),
    "order sale from the blocked bucket": (ISSUE_BLOCKED, "record_id"),
    "transfer received at or before it shipped": (ISSUE_TRANSFER, "record_id"),
}

# A stale decision can land in this sample. Each one has to belong to a labelled order.
SAMPLE_CHECK_PREFIX = "sample of "


def labelled_ids(truth_rows, issue, column):
    return [row.get(column) for row in truth_rows if row["expected_issue"] == issue]


def problems_with_records(result):
    """What is wrong with the IDs a failing check reported, whatever they were compared with."""
    records = result.get("records")
    if records is None:
        return ["the check did not report which records failed"]
    problems = []
    if len(records) != result["violations"]:
        problems.append(f"{result['violations']} violations but {len(records)} record IDs")
    if not all(records):
        problems.append("a reported record ID is blank")
    if len(set(records)) != len(records):
        problems.append("the same record ID is reported twice")
    return problems


def compare_with_labels(result, labelled):
    """Returns the entry for one check, with what it missed and what it caught that isn't labelled."""
    records = result.get("records") or []
    entry = dict(result, labelled=len(labelled), missed=sorted(set(labelled) - set(records), key=str),
                 unrelated=sorted(set(records) - set(labelled)))
    entry["problems"] = problems_with_records(result)
    if not labelled:
        entry["problems"].append("nothing is labelled for this check")
    if not all(labelled):
        entry["problems"].append("a labelled record has no ID")
    if entry["missed"]:
        entry["problems"].append(f"{len(entry['missed'])} labelled records were not caught")
    if entry["unrelated"]:
        entry["problems"].append(f"{len(entry['unrelated'])} failing records are not labelled")
    return entry


def check_sample(result, truth_rows):
    """Only a sample, so it can't be expected to catch every labelled order. Each one it catches must be labelled."""
    labelled_orders = set(labelled_ids(truth_rows, ISSUE_RESERVATION, "order_id"))
    records = result.get("records") or []
    unrelated = sorted(line for line in records if line.split("-L")[0] not in labelled_orders)
    entry = dict(result, labelled=len(records), missed=[], unrelated=unrelated, problems=problems_with_records(result))
    if unrelated:
        entry["problems"].append(f"{len(unrelated)} sampled decisions differ on orders that are not labelled")
    return entry


def missing_check(name):
    return dict(make_result(name, 0), passed=False, problems=["this check is missing from the summary"])


def classify(results, truth_rows):
    """Returns three lists: passed, expected failures and unexpected failures."""
    passed, expected, unexpected = [], [], []
    for result in results:
        name = result["check"]
        if name in EXPECTED_CHECKS:
            issue, column = EXPECTED_CHECKS[name]
            entry = dict(compare_with_labels(result, labelled_ids(truth_rows, issue, column)), issue=issue)
        elif result["passed"]:
            passed.append(result)
            continue
        elif name.startswith(SAMPLE_CHECK_PREFIX):
            entry = dict(check_sample(result, truth_rows), issue=ISSUE_RESERVATION)
        else:
            entry = dict(result, problems=["no labelled problem explains this check"])
        (unexpected if entry["problems"] else expected).append(entry)

    # A check that never ran can't fail, so its absence has to be a failure of its own.
    names = {result["check"] for result in results}
    unexpected += [missing_check(name) for name in required_check_names() if name not in names]
    if not any(name.startswith(SAMPLE_CHECK_PREFIX) for name in names):
        unexpected.append(missing_check("sample of order decisions"))
    return passed, expected, unexpected


def main():
    parser = argparse.ArgumentParser(description="Sort validation failures into expected and unexpected.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the final data, default data/<scale>")
    args = parser.parse_args()
    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale

    summary_path = data_dir / "validation_summary.json"
    if not summary_path.exists():
        print(f"no validation_summary.json in {data_dir}, run scripts.validate first")
        sys.exit(1)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    passed, expected, unexpected = classify(summary.get("checks", []), load_ground_truth(truth_dir_of(data_dir)))

    print(f"{len(passed)} checks passed")
    print(f"{len(expected)} checks failed as expected, with the failing IDs matching the labelled IDs:")
    for entry in expected:
        print(f"  EXPECTED  {entry['violations']:>6}  {entry['check']}  [{entry['issue']}]")
    print(f"{len(unexpected)} unexpected failures:")
    for entry in unexpected:
        print(f"  UNEXPECTED  {entry['violations']:>6}  {entry['check']}")
        for problem in entry["problems"]:
            print(f"      {problem}")
        for label in ("missed", "unrelated"):
            if entry.get(label):
                print(f"      {label}: {', '.join(str(record) for record in entry[label][:10])}")

    report = {"passed": len(passed), "expected_failures": expected, "unexpected_failures": unexpected}
    with open(truth_dir_of(data_dir) / "failure_report.json", "w", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, indent=2)
        file.write("\n")

    if unexpected:
        sys.exit(1)


if __name__ == "__main__":
    main()
