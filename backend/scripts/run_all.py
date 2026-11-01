"""
Everything, in order, with one command.

Run:  python -m scripts.run_all --scale starter
      python -m scripts.run_all --scale full

Generates the data, proves the clean baseline passes, loads the final data, sorts its failures
into expected and unexpected, runs the detectors and measures everything. It stops at the first
step that doesn't do what it should.

The validator is meant to exit 1 on the final data, because the labelled problems are in there
on purpose. That is the one step where exit code 1 is the right answer, and exit code 0 would
mean it missed them.
"""

import argparse
import json
import subprocess
import sys
import time

from app import config
from generator import settings


def steps_for(scale, seed, skip_compare):
    """Each step is a name, the command, and the exit code it has to give."""
    steps = [
        ("generate", ["scripts.generate", "--scale", scale, "--seed", str(seed)], 0),
        ("load clean baseline", ["scripts.load_data", "--scale", scale, "--clean"], 0),
        ("validate clean baseline", ["scripts.validate", "--scale", scale, "--clean"], 0),
        ("load final data", ["scripts.load_data", "--scale", scale], 0),
        ("validate final data", ["scripts.validate", "--scale", scale], 1),
        ("sort failures by record ID", ["eval.check_failures", "--scale", scale], 0),
        ("run detectors", ["scripts.run_detect"], 0),
        ("score detectors", ["eval.score", "--scale", scale], 0),
        ("compare ATP checkpoints", ["eval.check_atp", "--scale", scale], 0),
        ("check nothing unrelated changed", ["eval.check_unrelated", "--scale", scale], 0),
    ]
    if not skip_compare:
        steps.append(("same seed twice", ["scripts.compare_runs", "--scale", scale, "--seed", str(seed)], 0))
    return steps


def run_step(name, module_and_args, expected_code):
    """Prints the step's own output, and returns how long it took."""
    print(f"\n=== {name}", flush=True)
    started = time.perf_counter()
    result = subprocess.run([sys.executable, "-m", *module_and_args], cwd=config.BACKEND_DIR)
    seconds = round(time.perf_counter() - started, 1)
    if result.returncode != expected_code:
        print(f"\nSTOPPED: {name} exited {result.returncode}, it should exit {expected_code}")
        sys.exit(1)
    print(f"--- {name}: as expected, exit {result.returncode}, {seconds}s", flush=True)
    return seconds


def main():
    parser = argparse.ArgumentParser(description="Run the whole pipeline.")
    parser.add_argument("--scale", choices=list(settings.SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    parser.add_argument("--skip-compare", action="store_true", help="skip generating twice more to compare, the slow part")
    args = parser.parse_args()

    timings = {}
    for name, module_and_args, expected_code in steps_for(args.scale, args.seed, args.skip_compare):
        timings[name] = run_step(name, module_and_args, expected_code)

    # Written before the report, so the report can include it.
    path = config.DATA_DIR / args.scale / "run_timings.json"
    path.write_text(json.dumps(timings, indent=2) + "\n", encoding="utf-8")
    run_step("measure everything", ["eval.report", "--scale", args.scale], 0)

    print(f"\nDone in {round(sum(timings.values()))}s. Start the API with:  uvicorn app.main:app --port 8000")


if __name__ == "__main__":
    main()
