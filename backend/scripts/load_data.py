"""
Load the CSV files into PostgreSQL, then add foreign keys and indexes.

Run:  python -m scripts.load_data --scale starter

It replaces whatever is in the database, including any live orders. It checks every file against
the manifest before touching anything, and the whole load is one transaction: resetting the tables,
COPY and the foreign keys. So if any of it fails, the data from before is still there.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from app import config, db
from generator.export import TABLE_COLUMNS, clean_dir_of, sha256_of
from generator.settings import SCALES
from scripts import setup_database


class LoadRefused(Exception):
    """The files aren't what the manifest says. Nothing in the database was touched."""


def check_files(data_dir):
    manifest_path = data_dir / "source_manifest.json"
    if not manifest_path.exists():
        raise LoadRefused(f"no source_manifest.json in {data_dir}, generate the data first")
    files = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    for table in TABLE_COLUMNS:
        file_name = f"{table}.csv"
        if not (data_dir / file_name).exists():
            raise LoadRefused(f"{file_name} is missing from {data_dir}")
        if file_name not in files or sha256_of(data_dir / file_name) != files[file_name]["sha256"]:
            raise LoadRefused(f"{file_name} in {data_dir} differs from its manifest")


def load(data_dir, initialize_exceptions=False):
    check_files(data_dir)
    setup_database.ensure_database()

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(setup_database.read_sql_file("schema.sql"))

            # COPY, because one INSERT per row would take far too long at a million rows.
            for table, columns in TABLE_COLUMNS.items():
                sql = (f"COPY {table} ({', '.join(columns)}) FROM STDIN "
                       "WITH (FORMAT csv, HEADER true, NULL '')")
                started = time.perf_counter()
                with open(data_dir / f"{table}.csv", encoding="utf-8") as file:
                    cursor.copy_expert(sql, file)
                print(f"  loaded {table:22} {cursor.rowcount:>10,} rows  "
                      f"{time.perf_counter() - started:.1f}s")

            started = time.perf_counter()
            cursor.execute(setup_database.read_sql_file("constraints.sql"))
            print(f"  foreign keys and indexes took {time.perf_counter() - started:.1f}s")
            if initialize_exceptions:
                # First deployment: the seed and its detected exceptions commit together.
                from app import detect
                detect.write_exceptions(cursor, detect.find_exceptions(cursor))
        connection.commit()
    finally:
        # Closing without a commit rolls everything back.
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="Load a generated dataset.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the CSV files, default data/<scale>")
    parser.add_argument("--clean", action="store_true", help="load the clean baseline, data/<scale>_clean")
    args = parser.parse_args()

    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale
    try:
        load(clean_dir_of(data_dir) if args.clean else data_dir)
    except LoadRefused as refused:
        print(f"NOT LOADED: {refused}")
        sys.exit(1)


if __name__ == "__main__":
    main()
