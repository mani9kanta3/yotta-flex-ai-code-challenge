"""
Generate the same dataset twice and compare every file that comes out, byte for byte:
the final data, the clean baseline, the manifests and the ground truth.

Run:  python -m scripts.compare_runs --scale starter
"""

import argparse
import shutil
import sys

from app import config
from generator import export, settings
from scripts.generate import generate


def files_of_run(out_dir):
    """Every exported file, by a name that is the same for both runs."""
    folders = {
        "final": out_dir,
        "clean": export.clean_dir_of(out_dir),
        "ground_truth": export.truth_dir_of(out_dir),
    }
    files = {}
    for label, folder in folders.items():
        for path in sorted(folder.iterdir()):
            files[f"{label}/{path.name}"] = path
    return files


def count_rows(path):
    with open(path, "rb") as file:
        return sum(1 for _ in file)


def compare(files_a, files_b):
    """Returns the names of the files that differ, or that only one run has."""
    different = sorted(set(files_a) ^ set(files_b))
    for name in sorted(set(files_a) & set(files_b)):
        sha_a = export.sha256_of(files_a[name])
        sha_b = export.sha256_of(files_b[name])
        lines_a = count_rows(files_a[name])
        lines_b = count_rows(files_b[name])
        same = sha_a == sha_b
        print(f"  {name:42} {lines_a:>10,} / {lines_b:>10,} lines  {sha_a[:12]} / {sha_b[:12]}  "
              f"{'same' if same else 'DIFFERENT'}")
        if not same:
            different.append(name)
    return different


def main():
    parser = argparse.ArgumentParser(description="Check that a seed reproduces its data.")
    parser.add_argument("--scale", choices=list(settings.SCALES), default="starter")
    parser.add_argument("--seed", type=int, default=config.DEFAULT_SEED)
    args = parser.parse_args()

    scale = settings.get_scale(args.scale)
    folder = config.DATA_DIR / "compare"
    # Anything left from an earlier run would look like a file only one run has.
    if folder.exists():
        shutil.rmtree(folder)
    generate(scale, args.seed, folder / "run_a")
    generate(scale, args.seed, folder / "run_b")

    files_a = files_of_run(folder / "run_a")
    files_b = files_of_run(folder / "run_b")
    different = compare(files_a, files_b)
    if different:
        print(f"NOT reproducible: {len(different)} files differ: {', '.join(different)}")
        sys.exit(1)
    print(f"reproducible: all {len(files_a)} files identical")


if __name__ == "__main__":
    main()
