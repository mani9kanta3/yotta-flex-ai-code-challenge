"""
Shared setup for the tests.

Tests that need PostgreSQL use the loaded_database fixture and skip themselves
if it isn't reachable, so a fresh clone still gets a clean run. They use their
own inventory_test database, so they can't touch a real load.
"""

import pytest
import psycopg2

from app import config, db
from generator import settings
from scripts import load_data
from scripts.generate import generate

config.DB_NAME = "inventory_test"

TEST_SEED = 20261022


def tiny_scale():
    return settings.get_scale("starter", skus=60, locations=6, movements=1_500)


@pytest.fixture(scope="session")
def loaded_database(tmp_path_factory):
    try:
        db.get_connection("postgres").close()
    except psycopg2.OperationalError:
        pytest.skip("PostgreSQL is not reachable")

    data_dir = tmp_path_factory.mktemp("data")
    generate(tiny_scale(), TEST_SEED, data_dir)
    load_data.load(data_dir)


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
