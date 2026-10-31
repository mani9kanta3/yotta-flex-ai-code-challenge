"""
Create the database if needed, then create every table from schema.sql.

Run:  python -m scripts.setup_database

It is safe to run again. schema.sql drops every table first, so this always
gives back an empty database.
"""

from app import config, db


def ensure_database(dbname=None):
    """
    Connects to the "postgres" database, because you can't create a database
    from inside itself. CREATE DATABASE also refuses to run in a transaction,
    which is why autocommit is on.
    """
    dbname = dbname or config.DB_NAME
    connection = db.get_connection("postgres")
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cursor.fetchone():
                print(f"database {dbname} already exists")
            else:
                # The name comes from our own .env or tests, and CREATE DATABASE
                # won't take a bound parameter.
                cursor.execute(f"CREATE DATABASE {dbname}")
                print(f"created database {dbname}")
    finally:
        connection.close()


def run_sql_file(file_name, dbname=None):
    path = config.DB_DIR / file_name
    connection = db.get_connection(dbname)
    try:
        with connection.cursor() as cursor:
            cursor.execute(path.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    print(f"ran {file_name}")


def main():
    ensure_database()
    run_sql_file("schema.sql")


if __name__ == "__main__":
    main()
