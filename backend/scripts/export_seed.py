"""
Write the generated tables as SQL and JSON as well as CSV.

Run:  python -m scripts.export_seed --scale starter

The CSV files stay the source. These are built from them, in parent-first order, for anyone who
would rather load a plain .sql file or read JSON. They go in data/seed/, which Git ignores,
because they are the same data again and one command recreates them.
"""

import argparse
import csv
import itertools
import json

from app import config
from generator.export import TABLE_COLUMNS
from generator.settings import SCALES

# One INSERT per batch, so a large table doesn't become one enormous statement.
BATCH_ROWS = 1000


def read_table(data_dir, table):
    """One row at a time, because a million rows held in memory would use far more than the file."""
    with open(data_dir / f"{table}.csv", encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        next(reader)
        for row in reader:
            # An empty cell is NULL, the same as the loader treats it.
            yield [value if value != "" else None for value in row]


def sql_value(value):
    """Every value is written as text, and PostgreSQL converts it to the column's type."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def write_sql(path, data_dir):
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("-- Run backend/db/schema.sql first and backend/db/constraints.sql afterwards.\n")
        for table, columns in TABLE_COLUMNS.items():
            rows = read_table(data_dir, table)
            while True:
                batch = list(itertools.islice(rows, BATCH_ROWS))
                if not batch:
                    break
                values = ",\n".join("(" + ", ".join(sql_value(value) for value in row) + ")" for row in batch)
                file.write(f"INSERT INTO {table} ({', '.join(columns)}) VALUES\n{values};\n")


def write_json(path, data_dir):
    """Written a row at a time for the same reason, so this builds the brackets and commas itself."""
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("{\n")
        for table_number, (table, columns) in enumerate(TABLE_COLUMNS.items()):
            file.write(f"{',' if table_number else ''}{json.dumps(table)}: [\n")
            for row_number, row in enumerate(read_table(data_dir, table)):
                file.write(f"{',' if row_number else ''}{json.dumps(dict(zip(columns, row)))}\n")
            file.write("]\n")
        file.write("}\n")


def export_seed(data_dir, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    write_sql(out_dir / "seed.sql", data_dir)
    write_json(out_dir / "seed.json", data_dir)


def main():
    parser = argparse.ArgumentParser(description="Write the generated tables as SQL and JSON.")
    parser.add_argument("--scale", choices=list(SCALES), default="starter")
    args = parser.parse_args()
    out_dir = config.DATA_DIR / "seed" / args.scale
    export_seed(config.DATA_DIR / args.scale, out_dir)
    print(f"wrote {out_dir / 'seed.sql'} and {out_dir / 'seed.json'}")


if __name__ == "__main__":
    main()
