"""
Generate the same dataset twice and compare row counts and checksums.

Run:  python -m scripts.compare_runs --scale starter
"""

import argparse
import sys

from app import config
from generator import settings
from scripts.generate import generate


def compare(manifest_a, manifest_b):
    """Returns the names of the files that differ."""
    different = []
    for file_name, info_a in manifest_a["files"].items():
        info_b = manifest_b["files"][file_name]
        same = info_a["rows"] == info_b["rows"] and info_a["sha256"] == info_b["sha256"]
        word = "same" if same else "DIFFERENT"
        print(f"  {file_name:28} {info_a['rows']:>10,} / {info_b['rows']:>10,} rows  "
              f"{info_a['sha256'][:12]} / {info_b['sha256'][:12]}  {word}")
        if not same:
            different.append(file_name)
    return different


def main():
    parser = argparse.ArgumentParser(description="Check that a seed reproduces its data.")
    parser.add_argument("--scale", choices=list(settings.SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    args = parser.parse_args()

    scale = settings.get_scale(args.scale)
    folder = config.DATA_DIR / "compare"
    manifest_a = generate(scale, args.seed, folder / "run_a")
    manifest_b = generate(scale, args.seed, folder / "run_b")

    different = compare(manifest_a, manifest_b)
    if different:
        print(f"NOT reproducible: {len(different)} files differ")
        sys.exit(1)
    print(f"reproducible: all {len(manifest_a['files'])} files identical")


if __name__ == "__main__":
    main()
