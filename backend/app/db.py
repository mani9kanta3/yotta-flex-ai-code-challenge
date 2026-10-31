"""
How the project connects to PostgreSQL.

A fresh connection each time, with no pool. Nothing here needs one yet, and it
keeps one script from affecting the next.
"""

import psycopg2

from . import config


def get_connection(dbname=None):
    return psycopg2.connect(
        dbname=dbname or config.DB_NAME,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        application_name="inventory",
    )
