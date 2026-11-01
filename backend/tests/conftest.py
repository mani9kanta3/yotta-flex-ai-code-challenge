"""
Shared setup for the tests.

Tests that need PostgreSQL skip themselves if it isn't reachable, so a fresh
clone still gets a clean run. They use their own databases, so they can't touch
a real load.

    loaded_database  the clean baseline, loaded once. Used by read-only and
                     rolled-back tests.
    final_database   the final data with its labelled problems, in its own database.
    orders_db        a few hand-written rows, rebuilt for every test. The numbers
                     are exact, and these tests really commit, because a lock only
                     matters between separate transactions.
"""

import pytest
import psycopg2

from app import config, db
from generator import master, settings
from generator.export import clean_dir_of
from scripts import load_data, setup_database
from scripts.generate import generate

config.DB_NAME = "inventory_test"

TEST_SEED = 20261022
ORDERS_DB = "inventory_test_orders"

FINAL_DB = "inventory_test_final"

# The starter size, the smallest where every order outcome and labelled case happens.
SMALL_MOVEMENTS = settings.SCALES["starter"]["movements"]
SMALL_ORDERS = settings.SCALES["starter"]["orders"]
SMALL_CASES_PER_TYPE = settings.SCALES["starter"]["anomalies_per_type"]


def small_scale():
    return settings.get_scale("starter")


def require_postgres():
    try:
        db.get_connection("postgres").close()
    except psycopg2.OperationalError:
        pytest.skip("PostgreSQL is not reachable")


@pytest.fixture(scope="session")
def generated_data(tmp_path_factory):
    """One generated dataset for the whole run. Returns the folder of the final data."""
    data_dir = tmp_path_factory.mktemp("generated") / "tiny"
    generate(small_scale(), TEST_SEED, data_dir)
    return data_dir


@pytest.fixture(scope="session")
def loaded_database(generated_data):
    """The clean baseline, loaded once. Returns its folder."""
    require_postgres()
    clean_dir = clean_dir_of(generated_data)
    load_data.load(clean_dir)
    return clean_dir


@pytest.fixture(scope="session")
def final_database(generated_data):
    """The final data with the labelled problems, in a database of its own. Returns its folder."""
    require_postgres()
    clean_db_name = config.DB_NAME
    config.DB_NAME = FINAL_DB
    try:
        load_data.load(generated_data)
    finally:
        config.DB_NAME = clean_db_name
    return generated_data


@pytest.fixture
def final_cursor(final_database):
    """Like the cursor fixture, on the final data. Everything is rolled back."""
    connection = db.get_connection(FINAL_DB)
    try:
        with connection.cursor() as test_cursor:
            yield test_cursor
    finally:
        connection.rollback()
        connection.close()


@pytest.fixture
def cursor(loaded_database):
    """Everything a test does here is rolled back, so tests can't affect each other."""
    connection = db.get_connection()
    try:
        with connection.cursor() as test_cursor:
            yield test_cursor
    finally:
        connection.rollback()
        connection.close()


# ------------------------------------------------------------ hand-written data

SKU_A = "SKU-000001"
SKU_B = "SKU-000002"
NDC = "LOC-000001"
WAREHOUSE_SOUTH = "LOC-000002"
WAREHOUSE_NORTH = "LOC-000003"
STORE_SOUTH = "LOC-000004"


def connect():
    return db.get_connection(ORDERS_DB)


def add_stock(cursor, sku_id, location_id, on_hand, blocked=0, safety_stock=0, allow_inbound=True):
    """A stocked pair, with the opening balance and rule the ATP queries need."""
    cursor.execute(
        "INSERT INTO sku_location (sku_id, location_id, ranged_from) VALUES (%s, %s, '2025-06-01')",
        (sku_id, location_id),
    )
    cursor.execute(
        """INSERT INTO replenishment_rule
           (rule_id, sku_id, location_id, source_location_id, safety_stock, reorder_point,
            reorder_qty, lead_time_days, allow_inbound_atp, effective_from)
           VALUES ('RUL-' || nextval('event_number'), %s, %s, NULL, %s, %s, 10, 2, %s, '2025-06-01')""",
        (sku_id, location_id, safety_stock, safety_stock, allow_inbound),
    )
    cursor.execute(
        """INSERT INTO inventory_balance
           (balance_id, sku_id, location_id, snapshot_type, as_of_at, on_hand, blocked)
           VALUES ('BAL-' || nextval('event_number'), %s, %s, 'OPENING', %s, %s, %s)""",
        (sku_id, location_id, config.START_AT, on_hand, blocked),
    )


def add_transfer(cursor, shipment_id, sku_id, to_location_id, qty, shipped_at, eta_at):
    """A truck on the road, with the events the inbound query reads."""
    cursor.execute(
        """INSERT INTO shipment (shipment_id, shipment_type, status, from_location_id, to_location_id,
                                 created_at, original_eta_at, transfer_sku_id, transfer_qty)
           VALUES (%s, 'TRANSFER', 'IN_TRANSIT', %s, %s, %s, %s, %s, %s)""",
        (shipment_id, NDC, to_location_id, shipped_at, eta_at, sku_id, qty),
    )
    add_shipment_event(cursor, shipment_id, None, "CREATED", shipped_at)
    add_shipment_event(cursor, shipment_id, "CREATED", "IN_TRANSIT", shipped_at)


def add_shipment_event(cursor, shipment_id, from_status, to_status, occurred_at, eta_at=None):
    cursor.execute(
        """INSERT INTO shipment_event (event_id, shipment_id, from_status, to_status, occurred_at,
                                       reason_code, eta_at)
           VALUES (nextval('event_number'), %s, %s, %s, %s, 'TEST', %s)""",
        (shipment_id, from_status, to_status, occurred_at, eta_at),
    )


def close_leftover_connections():
    """
    A race test that fails can leave its transaction open. Its locks would make the
    next test's rebuild wait forever, so one failure would hang the whole run.
    """
    connection = db.get_connection("postgres")
    with connection, connection.cursor() as cursor:
        cursor.execute("""SELECT pg_terminate_backend(pid) FROM pg_stat_activity
                          WHERE datname = %s AND pid <> pg_backend_pid()""", (ORDERS_DB,))
    connection.close()


@pytest.fixture
def orders_db():
    """A clean database with one product, two SKUs and four locations, and no stock yet."""
    require_postgres()
    setup_database.ensure_database(ORDERS_DB)
    close_leftover_connections()
    setup_database.run_sql_file("schema.sql", ORDERS_DB)
    setup_database.run_sql_file("constraints.sql", ORDERS_DB)

    connection = connect()
    with connection, connection.cursor() as cursor:
        cursor.execute("""INSERT INTO product VALUES
            ('PRD-000001', 'Test Shirt', 'APPAREL', 'Brand 01', 'ACTIVE', '2025-06-01')""")
        for sku_id in (SKU_A, SKU_B):
            cursor.execute(
                """INSERT INTO sku VALUES
                   (%s, 'PRD-000001', 'M', 'Black', 100.00, 'ACTIVE', '2025-06-01', NULL)""",
                (sku_id,),
            )
        cursor.execute("""INSERT INTO location VALUES
            ('LOC-000001', 'NDC', 'NDC', 'CENTRAL', NULL, TRUE, '2020-01-01', NULL),
            ('LOC-000002', 'WH South', 'WAREHOUSE', 'SOUTH', 'LOC-000001', TRUE, '2020-01-01', NULL),
            ('LOC-000003', 'WH North', 'WAREHOUSE', 'NORTH', 'LOC-000001', TRUE, '2020-01-01', NULL),
            ('LOC-000004', 'Store South', 'STORE', 'SOUTH', 'LOC-000002', TRUE, '2020-01-01', NULL)""")
        number = 0
        for code, online, inbound, safety, hold in master.CHANNELS:
            cursor.execute("INSERT INTO channel VALUES (%s, %s, %s, %s, %s)",
                           (code, online, inbound, safety, hold))
            for location_id in (NDC, WAREHOUSE_SOUTH, WAREHOUSE_NORTH, STORE_SOUTH):
                number += 1
                # No buffers by default, so "the last unit" really is one unit.
                cursor.execute(
                    """INSERT INTO channel_location VALUES (%s, %s, %s, 0, '2025-06-01', NULL)""",
                    (f"CHL-{number:06d}", code, location_id),
                )
    connection.close()
    return ORDERS_DB
