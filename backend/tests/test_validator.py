"""
The validator has to fail when it should, and the command has to say so.

A validator that has only ever passed proves nothing. These tests break the data
on purpose, inside a transaction that is rolled back.
"""

import json
import os
import shutil
import subprocess
import sys

import psycopg2
import pytest

from app import config
from generator.export import TABLE_COLUMNS
from generator.settings import NOT_GENERATED_YET
from scripts import setup_database, validate

pytestmark = pytest.mark.database


def manifest_for(data_dir):
    return json.loads((data_dir / "source_manifest.json").read_text(encoding="utf-8"))


def failed_checks(results):
    return [result["check"] for result in results if not result["passed"]]


def run_validate_command(data_dir, dbname=None):
    """The real command in its own process, so we see the real exit code."""
    environment = dict(os.environ, DB_NAME=dbname or config.DB_NAME)
    return subprocess.run(
        [sys.executable, "-m", "scripts.validate", "--data", str(data_dir)],
        cwd=config.BACKEND_DIR, env=environment, capture_output=True, text=True,
    )


def unstocked_pair(cursor):
    cursor.execute("""
        SELECT s.sku_id, l.location_id FROM sku s CROSS JOIN location l
        WHERE NOT EXISTS (SELECT 1 FROM sku_location p
                          WHERE p.sku_id = s.sku_id AND p.location_id = l.location_id)
        ORDER BY 1, 2 LIMIT 1""")
    return cursor.fetchone()


# ------------------------------------------------------------ good data passes

def test_generated_data_passes_everything(cursor, loaded_database):
    results = validate.validate(cursor, manifest_for(loaded_database))
    assert failed_checks(results) == []


# ------------------------------------------------------------ missing closing balance

def test_missing_closing_balance_fails(cursor, loaded_database):
    sku_id, location_id = unstocked_pair(cursor)
    cursor.execute("INSERT INTO sku_location VALUES (%s, %s, '2026-01-01')", (sku_id, location_id))
    cursor.execute(
        """INSERT INTO inventory_balance VALUES
           ('BAL-9999999', %s, %s, 'OPENING', %s, 5, 0)""",
        (sku_id, location_id, config.START_AT),
    )
    results = validate.run_checks(cursor)
    # The network total is built from closing balances, so it notices as well.
    assert failed_checks(results) == ["stocked pair without a closing balance",
                                      "network stock plus in transit differs from opening plus outside changes"]


def test_missing_opening_balance_fails(cursor):
    sku_id, location_id = unstocked_pair(cursor)
    cursor.execute("INSERT INTO sku_location VALUES (%s, %s, '2026-01-01')", (sku_id, location_id))
    results = validate.run_checks(cursor)
    assert "stocked pair without an opening balance" in failed_checks(results)
    assert "stocked pair without a closing balance" in failed_checks(results)


def test_balance_at_the_wrong_time_fails(cursor):
    sku_id, location_id = unstocked_pair(cursor)
    cursor.execute("INSERT INTO sku_location VALUES (%s, %s, '2026-01-01')", (sku_id, location_id))
    cursor.execute(
        """INSERT INTO inventory_balance VALUES
           ('BAL-9999999', %s, %s, 'OPENING', '2026-02-02 00:00:00', 5, 0)""",
        (sku_id, location_id),
    )
    assert "balance snapshot at the wrong time" in failed_checks(validate.run_checks(cursor))


# ------------------------------------------------------------ empty data

@pytest.mark.parametrize("table", list(TABLE_COLUMNS))
def test_empty_table_fails(cursor, table):
    # TRUNCATE skips the row triggers, and the fixture rolls it back.
    cursor.execute(f"TRUNCATE {table} CASCADE")
    assert f"table has rows: {table}" in failed_checks(validate.check_tables_not_empty(cursor))


def test_loaded_tables_are_not_reported_empty(cursor):
    assert failed_checks(validate.check_tables_not_empty(cursor)) == []


def test_command_fails_on_an_empty_database(loaded_database):
    """Every rule check passes on no rows, which is exactly what must not count as a pass."""
    dbname = "inventory_test_empty"
    setup_database.ensure_database(dbname)
    setup_database.run_sql_file("schema.sql", dbname)

    result = run_validate_command(loaded_database, dbname)

    assert result.returncode == 1
    assert "FAIL" in result.stdout


# ------------------------------------------------------------ wrong row counts

def test_one_extra_row_fails_the_count(cursor, loaded_database):
    cursor.execute("""SELECT sku_id, location_id FROM inventory_balance
                      WHERE snapshot_type = 'OPENING' ORDER BY on_hand DESC LIMIT 1""")
    sku_id, location_id = cursor.fetchone()
    cursor.execute(
        """INSERT INTO inventory_movement
           (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
            occurred_at, reason_code, source_doc)
           VALUES ('MOV-2026-99999999', %s, %s, 'RECEIPT', 'SELLABLE', 1,
                   '2026-09-30 23:00:00', 'TEST', 'TEST-1')""",
        (sku_id, location_id),
    )
    failed = failed_checks(validate.check_row_counts(cursor, manifest_for(loaded_database)))
    assert "row count matches manifest: inventory_movement" in failed
    assert "row count matches target: movements" in failed


def test_manifest_claiming_other_counts_fails(cursor, loaded_database):
    manifest = manifest_for(loaded_database)
    manifest["files"]["sku.csv"]["rows"] += 1
    failed = failed_checks(validate.check_row_counts(cursor, manifest))
    assert failed == ["row count matches manifest: sku"]


def test_data_of_the_wrong_scale_fails(cursor, loaded_database):
    """For example, a starter load checked against the full targets."""
    manifest = manifest_for(loaded_database)
    manifest["scale"]["movements"] = 1_000_000
    failed = failed_checks(validate.check_row_counts(cursor, manifest))
    assert failed == ["row count matches target: movements"]


def test_table_missing_from_the_manifest_fails(cursor, loaded_database):
    manifest = manifest_for(loaded_database)
    del manifest["files"]["location.csv"]
    failed = failed_checks(validate.check_row_counts(cursor, manifest))
    assert failed == ["row count matches manifest: location"]


# ------------------------------------------------------------ rules can't pass on no rows

# SALE is left out because returns point at sales. The empty ledger test below covers it.
@pytest.mark.parametrize("movement_type", [t for t in validate.GENERATED_MOVEMENT_TYPES if t != "SALE"])
def test_ledger_with_a_movement_type_missing_fails(cursor, movement_type):
    cursor.execute("ALTER TABLE inventory_movement DISABLE TRIGGER inventory_movement_append_only")
    cursor.execute("DELETE FROM inventory_movement WHERE movement_type = %s", (movement_type,))
    assert failed_checks(validate.check_generated_activity(cursor)) == [
        f"ledger has movements of type {movement_type}"]


def test_empty_ledger_fails_for_every_movement_type(cursor):
    cursor.execute("TRUNCATE inventory_movement CASCADE")
    failed = failed_checks(validate.check_generated_activity(cursor))
    for movement_type in validate.GENERATED_MOVEMENT_TYPES:
        assert f"ledger has movements of type {movement_type}" in failed


def test_no_truck_left_on_the_road_fails(cursor):
    cursor.execute("UPDATE shipment SET status = 'RECEIVED' WHERE status = 'IN_TRANSIT'")
    assert failed_checks(validate.check_generated_activity(cursor)) == [
        "some transfers are still in transit at the end"]


def test_no_late_truck_fails(cursor):
    cursor.execute("ALTER TABLE shipment_event DISABLE TRIGGER shipment_event_append_only")
    cursor.execute("DELETE FROM shipment_event WHERE reason_code = 'ETA_MISSED'")
    assert failed_checks(validate.check_generated_activity(cursor)) == ["some transfers missed their ETA"]


def test_generated_data_has_all_the_activity(cursor):
    assert failed_checks(validate.check_generated_activity(cursor)) == []


# ------------------------------------------------------------ the exit code

def test_command_exits_zero_on_good_data(loaded_database):
    result = run_validate_command(loaded_database)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NOT GENERATED YET" in result.stdout


def test_command_exits_one_when_a_check_fails(loaded_database, tmp_path):
    shutil.copy(loaded_database / "source_manifest.json", tmp_path)
    manifest = manifest_for(tmp_path)
    manifest["files"]["inventory_movement.csv"]["rows"] = 1
    (tmp_path / "source_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = run_validate_command(tmp_path)

    assert result.returncode == 1
    assert "FAIL" in result.stdout


def test_command_exits_one_with_no_manifest(loaded_database, tmp_path):
    result = run_validate_command(tmp_path)
    assert result.returncode == 1
    assert "source_manifest.json" in result.stdout


def test_summary_records_what_is_not_generated(loaded_database):
    run_validate_command(loaded_database)
    summary = json.loads((loaded_database / "validation_summary.json").read_text(encoding="utf-8"))
    assert summary["failed"] == 0
    assert summary["not_generated_yet"] == NOT_GENERATED_YET


# ------------------------------------------------------------ sale quantities

@pytest.mark.parametrize("quantity", [0, 3])
def test_database_refuses_a_sale_that_is_not_negative(cursor, quantity):
    """A SALE row removes stock, so zero or a positive number can't be stored."""
    cursor.execute("""SELECT sku_id, location_id FROM sku_location ORDER BY 1, 2 LIMIT 1""")
    sku_id, location_id = cursor.fetchone()
    with pytest.raises(psycopg2.errors.CheckViolation):
        cursor.execute(
            """INSERT INTO inventory_movement
               (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
                occurred_at, reason_code, source_doc)
               VALUES ('MOV-2026-99999999', %s, %s, 'SALE', 'SELLABLE', %s,
                       '2026-09-30 23:00:00', 'TEST', 'TEST-1')""",
            (sku_id, location_id, quantity),
        )
