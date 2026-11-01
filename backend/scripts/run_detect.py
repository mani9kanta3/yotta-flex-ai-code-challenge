"""
Run the four detectors on the loaded data and store what they find.

Run:  python -m scripts.run_detect
"""

import time

from app import db, detect


def main():
    connection = db.get_connection()
    started = time.perf_counter()
    try:
        with connection, connection.cursor() as cursor:
            exceptions = detect.find_exceptions(cursor)
            detect.write_exceptions(cursor, exceptions)
    finally:
        connection.close()

    counts = {issue_type: 0 for issue_type, _sql, _column, _action in detect.DETECTORS}
    for exception in exceptions:
        counts[exception["issue_type"]] += 1
    for issue_type, count in counts.items():
        print(f"  {issue_type:32} {count:>6} found")
    print(f"  took {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
