"""
Run the whole check on other seeds at starter scale, to see what depends on the default seed.

Run:  python -m eval.try_seeds --first 1 --count 20

Each seed goes through generating, the clean baseline, the final data, the ID comparison, the
detectors and the ATP checkpoints, in a database of its own. It prints one line per seed and
never hides a failure. docs/STATUS.md quotes what the last run found.
"""

import argparse
import contextlib
import io
import shutil

from app import config, db, detect
from eval import check_atp, check_failures, score
from generator import export, settings
from scripts import load_data, setup_database, validate
from scripts.generate import generate

SEED_DB = "inventory_seed_check"


def run_validator(data_dir):
    manifest = validate.load_manifest(data_dir)
    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            return validate.check_files_match_manifest(data_dir, manifest) + validate.validate(cursor, manifest)
    finally:
        connection.close()


def load_quietly(data_dir):
    with contextlib.redirect_stdout(io.StringIO()):
        load_data.load(data_dir)


def try_seed(seed, out_dir, scale):
    """Returns what happened to this seed, as a dict."""
    try:
        generate(scale, seed, out_dir)
    except RuntimeError as error:
        return {"seed": seed, "generated": False, "error": str(error)}

    load_quietly(export.clean_dir_of(out_dir))
    clean_failures = [r["check"] for r in run_validator(export.clean_dir_of(out_dir)) if not r["passed"]]

    load_quietly(out_dir)
    truth_dir = export.truth_dir_of(out_dir)
    truth_rows = score.load_ground_truth(truth_dir)
    _passed, expected, unexpected = check_failures.classify(run_validator(out_dir), truth_rows)

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            found = [(e["issue_type"], e["record_id"]) for e in detect.find_exceptions(cursor)]
            atp_results, atp_differing = check_atp.check_checkpoints(cursor, check_atp.load_checkpoints(truth_dir))
    finally:
        connection.close()
    scores = score.score(found, truth_rows)

    return {
        "seed": seed,
        "generated": True,
        "clean_failures": clean_failures,
        "expected_failures": len(expected),
        "unexpected_failures": [entry["check"] for entry in unexpected],
        "perfect_scores": all(s["precision"] == 1.0 and s["recall"] == 1.0 for s in scores.values()),
        "atp_differing": len(atp_differing),
        "atp_kinds_missing": check_atp.missing_kinds(atp_results),
    }


def describe(result):
    if not result["generated"]:
        return f"seed {result['seed']:>9}  STOPPED  {result['error']}"
    problems = result["clean_failures"] + result["unexpected_failures"] + result["atp_kinds_missing"]
    if result["atp_differing"]:
        problems.append(f"{result['atp_differing']} ATP checkpoints differ")
    if not result["perfect_scores"]:
        problems.append("a detector score below 1.0")
    return f"seed {result['seed']:>9}  {'ok' if not problems else 'PROBLEM  ' + '; '.join(problems)}"


def main():
    parser = argparse.ArgumentParser(description="Run every check on other seeds, at starter scale.")
    parser.add_argument("--first", type=int, default=1)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    config.DB_NAME = SEED_DB
    setup_database.ensure_database(SEED_DB)
    scale = settings.get_scale("starter")
    folder = config.DATA_DIR / "compare" / "seeds"

    without_problems = 0
    for seed in range(args.first, args.first + args.count):
        line = describe(try_seed(seed, folder / str(seed), scale))
        without_problems += line.endswith("  ok")
        print(line, flush=True)
    shutil.rmtree(folder)
    print(f"{without_problems} of {args.count} seeds had no problem")


if __name__ == "__main__":
    main()
