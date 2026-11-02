"""Seed a new deployment once, then start the public web service.

Restarts and redeploys keep all existing orders. Only an empty application database
gets the checked-in starter data. The full dataset remains reproducible locally.
"""

import os

import uvicorn

from app import config, db
from scripts.load_data import load

# Two overlapping deployments must not both seed the same new database.
INITIALIZATION_LOCK = 20261022


def initialize_database():
    connection = db.get_connection()
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", (INITIALIZATION_LOCK,))
            cursor.execute("SELECT to_regclass('public.sku')")
            if cursor.fetchone()[0] is None:
                print("Loading starter data into the new deployment database.", flush=True)
                load(config.DATA_DIR / "starter", initialize_exceptions=True)
            else:
                print("Keeping the existing database and orders.", flush=True)
    finally:
        # Closing also releases the initialization lock after a failed seed.
        connection.close()


def main():
    initialize_database()
    uvicorn.run("app.web:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))


if __name__ == "__main__":
    main()
