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
    # History refuses a TRUNCATE, so this switches its triggers off. The fixture rolls it all back.
    cursor.execute("SET LOCAL session_replication_role = replica")
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
    assert f"ledger has movements of type {movement_type}" in failed_checks(validate.check_generated_activity(cursor))


def test_empty_ledger_fails_for_every_movement_type(cursor):
    cursor.execute("SET LOCAL session_replication_role = replica")
    cursor.execute("TRUNCATE inventory_movement CASCADE")
    failed = failed_checks(validate.check_generated_activity(cursor))
    for movement_type in validate.GENERATED_MOVEMENT_TYPES:
        assert f"ledger has movements of type {movement_type}" in failed


def test_no_truck_left_on_the_road_fails(cursor):
    cursor.execute("UPDATE shipment SET status = 'RECEIVED' WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT'")
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


# ------------------------------------------------------------ keys, values and dates

def test_foreign_key_list_is_the_same_as_the_database(cursor):
    """If a key is added to constraints.sql and not here, the validator would never check it."""
    cursor.execute("""
        SELECT child.relname,
               (SELECT string_agg(a.attname, ', ' ORDER BY k.ord) FROM unnest(c.conkey) WITH ORDINALITY k(num, ord)
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num),
               parent.relname,
               (SELECT string_agg(a.attname, ', ' ORDER BY k.ord) FROM unnest(c.confkey) WITH ORDINALITY k(num, ord)
                JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num)
        FROM pg_constraint c
        JOIN pg_class child ON child.oid = c.conrelid
        JOIN pg_class parent ON parent.oid = c.confrelid
        WHERE c.contype = 'f'""")
    assert sorted(cursor.fetchall()) == sorted(validate.FOREIGN_KEYS)


def test_primary_key_list_is_the_same_as_the_database(cursor):
    cursor.execute("""
        SELECT t.relname,
               (SELECT string_agg(a.attname, ', ' ORDER BY k.ord) FROM unnest(c.conkey) WITH ORDINALITY k(num, ord)
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num)
        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
        WHERE c.contype = 'p' AND t.relnamespace = 'public'::regnamespace""")
    assert dict(cursor.fetchall()) == validate.PRIMARY_KEYS


def test_duplicate_primary_key_fails(cursor):
    # The database would refuse this, so its own guard has to come off first.
    cursor.execute("ALTER TABLE product DROP CONSTRAINT product_pkey CASCADE")
    cursor.execute("""INSERT INTO product SELECT * FROM product ORDER BY product_id LIMIT 1""")
    assert "duplicate primary key: product" in failed_checks(validate.run_checks(cursor))


def test_reference_to_nothing_fails(cursor):
    cursor.execute("ALTER TABLE sku DROP CONSTRAINT sku_product_id_fkey")
    cursor.execute("""UPDATE sku SET product_id = 'PRD-999999'
                      WHERE sku_id = (SELECT MIN(sku_id) FROM sku)""")
    assert "reference points at nothing: sku.product_id" in failed_checks(validate.run_checks(cursor))


def test_two_column_reference_to_nothing_fails(cursor):
    cursor.execute("ALTER TABLE inventory_balance DROP CONSTRAINT inventory_balance_sku_id_location_id_fkey")
    cursor.execute("ALTER TABLE inventory_balance DROP CONSTRAINT inventory_balance_sku_id_location_id_snapshot_type_key")
    cursor.execute("DROP TRIGGER inventory_balance_append_only ON inventory_balance")
    # Both IDs exist on their own, but this SKU isn't stocked at this location.
    sku_id, location_id = unstocked_pair(cursor)
    cursor.execute("""UPDATE inventory_balance SET sku_id = %s, location_id = %s
                      WHERE balance_id = (SELECT MIN(balance_id) FROM inventory_balance)""", (sku_id, location_id))
    assert "reference points at nothing: inventory_balance.sku_id, location_id" in failed_checks(validate.run_checks(cursor))


def test_empty_optional_reference_is_allowed(cursor):
    """The NDC has no parent, and that isn't a broken reference."""
    results = {r["check"]: r["violations"] for r in validate.run_checks(cursor)}
    assert results["reference points at nothing: location.parent_location_id"] == 0


def test_value_not_allowed_fails(cursor):
    cursor.execute("ALTER TABLE product DROP CONSTRAINT product_category_check")
    cursor.execute("""UPDATE product SET category = 'TOYS'
                      WHERE product_id = (SELECT MIN(product_id) FROM product)""")
    assert "value not allowed: product.category" in failed_checks(validate.run_checks(cursor))


def test_hold_expiring_when_created_fails(cursor):
    cursor.execute("""UPDATE reservation SET expires_at = created_at
                      WHERE reservation_id = (SELECT MIN(reservation_id) FROM reservation WHERE status <> 'REJECTED')""")
    assert "hold that expires at or before it was created" in failed_checks(validate.run_checks(cursor))


def test_decision_before_its_order_fails(cursor):
    cursor.execute("DROP TRIGGER allocation_decision_append_only ON allocation_decision")
    cursor.execute("""UPDATE allocation_decision SET decided_at = decided_at - INTERVAL '10 days'
                      WHERE decision_id = (SELECT MIN(decision_id) FROM allocation_decision)""")
    assert "decision before its order was placed" in failed_checks(validate.run_checks(cursor))


def test_order_sale_before_its_allocation_fails(cursor):
    cursor.execute("""UPDATE order_allocation SET allocated_at = allocated_at + INTERVAL '300 days'
                      WHERE allocation_id = (SELECT MIN(allocation_id) FROM inventory_movement
                                             WHERE allocation_id IS NOT NULL)""")
    assert "order sale before its allocation" in failed_checks(validate.run_checks(cursor))


def test_transfer_due_before_it_was_created_fails(cursor):
    cursor.execute("""UPDATE shipment SET original_eta_at = created_at - INTERVAL '1 day'
                      WHERE shipment_id = (SELECT MIN(shipment_id) FROM shipment WHERE shipment_type = 'TRANSFER')""")
    assert "transfer due before it was created" in failed_checks(validate.run_checks(cursor))


def test_hold_whose_first_event_differs_fails(cursor):
    cursor.execute("""UPDATE reservation SET created_at = created_at - INTERVAL '1 second'
                      WHERE reservation_id = (SELECT MIN(reservation_id) FROM reservation WHERE status <> 'REJECTED')""")
    assert "hold whose first event differs from when it was created" in failed_checks(validate.run_checks(cursor))


# ------------------------------------------------------------ files against the manifest

def test_files_match_their_manifest_checksums(loaded_database):
    results = validate.check_files_match_manifest(loaded_database, manifest_for(loaded_database))
    assert len(results) == len(TABLE_COLUMNS)
    assert failed_checks(results) == []


def test_a_changed_file_fails_its_checksum(loaded_database, tmp_path):
    shutil.copy(loaded_database / "sku.csv", tmp_path / "sku.csv")
    with open(tmp_path / "sku.csv", "a", encoding="utf-8") as file:
        file.write("\n")
    manifest = {"files": {"sku.csv": manifest_for(loaded_database)["files"]["sku.csv"]}}
    assert failed_checks(validate.check_files_match_manifest(tmp_path, manifest)) == [
        "file matches its manifest checksum: sku.csv"]


def test_a_missing_file_fails_its_checksum(loaded_database, tmp_path):
    manifest = {"files": {"sku.csv": manifest_for(loaded_database)["files"]["sku.csv"]}}
    assert failed_checks(validate.check_files_match_manifest(tmp_path, manifest)) == [
        "file matches its manifest checksum: sku.csv"]
