"""
Load the CSV files into PostgreSQL, then add foreign keys and indexes.

Run:  python -m scripts.load_data --scale starter

It resets the tables first, so it is safe to run again.
"""

import argparse
import time
from pathlib import Path

from app import config, db
from generator.export import TABLE_COLUMNS, clean_dir_of
from generator.settings import SCALES
from scripts import setup_database


def load(data_dir):
    setup_database.main()

    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            # COPY, because one INSERT per row would take far too long at a million rows.
            for table, columns in TABLE_COLUMNS.items():
                sql = (f"COPY {table} ({', '.join(columns)}) FROM STDIN "
                       "WITH (FORMAT csv, HEADER true, NULL '')")
                started = time.perf_counter()
                with open(data_dir / f"{table}.csv", encoding="utf-8") as file:
                    cursor.copy_expert(sql, file)
                print(f"  loaded {table:22} {cursor.rowcount:>10,} rows  "
                      f"{time.perf_counter() - started:.1f}s")
        connection.commit()
    finally:
        connection.close()

    started = time.perf_counter()
    setup_database.run_sql_file("constraints.sql")
    print(f"  foreign keys and indexes took {time.perf_counter() - started:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="Load a generated dataset.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    parser.add_argument("--data", help="folder holding the CSV files, default data/<scale>")
    parser.add_argument("--clean", action="store_true", help="load the clean baseline, data/<scale>_clean")
    args = parser.parse_args()

    data_dir = Path(args.data) if args.data else config.DATA_DIR / args.scale
    load(clean_dir_of(data_dir) if args.clean else data_dir)


if __name__ == "__main__":
    main()
