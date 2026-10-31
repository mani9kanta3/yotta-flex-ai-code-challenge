"""
The schema and the validator, against a real PostgreSQL.

The "catches" tests break the data on purpose inside a transaction. A validator
that has only ever passed proves nothing, so these show each check can fail.
"""

import psycopg2
import pytest

from scripts import validate

pytestmark = pytest.mark.database

CORE_TABLES = [
    "product", "sku", "location", "inventory_balance", "inventory_movement",
    "reservation", "orders", "order_line", "shipment", "replenishment_rule",
]
APPEND_ONLY_TABLES = [
    "inventory_movement", "inventory_balance", "allocation_decision",
    "order_event", "reservation_event", "shipment_event",
]


def violations(cursor, check_name):
    results = validate.run_checks(cursor)
    return next(r["violations"] for r in results if r["check"] == check_name)


def add_movement(cursor, movement_id, sku_id, location_id, movement_type, bucket, quantity,
                 occurred_at="2026-09-30 23:00:00", group=None):
    cursor.execute(
        """INSERT INTO inventory_movement
           (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
            occurred_at, reason_code, source_doc, movement_group_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'TEST', %s, %s)""",
        (movement_id, sku_id, location_id, movement_type, bucket, quantity, occurred_at,
         f"TEST-{movement_id}", group),
    )


def stocked_pair(cursor):
    cursor.execute("""SELECT sku_id, location_id, on_hand, blocked FROM inventory_balance
                      WHERE snapshot_type = 'CLOSING' ORDER BY on_hand DESC, sku_id, location_id LIMIT 1""")
    return cursor.fetchone()


# ------------------------------------------------------------ schema

@pytest.mark.parametrize("table", CORE_TABLES)
def test_core_table_exists(cursor, table):
    cursor.execute("SELECT to_regclass(%s)", (table,))
    assert cursor.fetchone()[0] == table


def test_every_table_has_a_primary_key(cursor):
    cursor.execute("""
        SELECT t.table_name FROM information_schema.tables t
        LEFT JOIN information_schema.table_constraints c
          ON c.table_name = t.table_name AND c.constraint_type = 'PRIMARY KEY'
        WHERE t.table_schema = 'public' AND c.constraint_name IS NULL""")
    assert cursor.fetchall() == []


def test_location_hierarchy_has_one_root(cursor):
    cursor.execute("SELECT COUNT(*) FROM location WHERE parent_location_id IS NULL")
    assert cursor.fetchone()[0] == 1


@pytest.mark.parametrize("table", APPEND_ONLY_TABLES)
def test_append_only_table_has_its_trigger(cursor, table):
    cursor.execute("""SELECT COUNT(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
                      WHERE NOT t.tgisinternal AND c.relname = %s""", (table,))
    assert cursor.fetchone()[0] == 1


# ------------------------------------------------------------ immutability

def test_movement_update_is_refused(cursor):
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cursor.execute("UPDATE inventory_movement SET quantity = 999 WHERE movement_id = 'MOV-2026-00000001'")


def test_movement_delete_is_refused(cursor):
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cursor.execute("DELETE FROM inventory_movement WHERE movement_id = 'MOV-2026-00000001'")


def test_balance_update_is_refused(cursor):
    cursor.execute("SELECT balance_id FROM inventory_balance LIMIT 1")
    balance_id = cursor.fetchone()[0]
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cursor.execute("UPDATE inventory_balance SET on_hand = 999 WHERE balance_id = %s", (balance_id,))


def test_a_movement_cannot_go_the_wrong_way(cursor):
    sku_id, location_id, _on_hand, _blocked = stocked_pair(cursor)
    with pytest.raises(psycopg2.errors.CheckViolation):
        add_movement(cursor, "MOV-2026-99999999", sku_id, location_id, "RECEIPT", "SELLABLE", -5)


# ------------------------------------------------------------ the validator

def test_generated_data_passes_every_check(cursor):
    results = validate.run_checks(cursor)
    assert [r for r in results if not r["passed"]] == []


def test_movement_count_matches_the_target(cursor):
    cursor.execute("SELECT COUNT(*) FROM inventory_movement")
    assert cursor.fetchone()[0] == 1_500


def test_validator_catches_a_sale_without_stock(cursor):
    sku_id, location_id, on_hand, blocked = stocked_pair(cursor)
    add_movement(cursor, "MOV-2026-99999999", sku_id, location_id, "SALE", "SELLABLE",
                 -(on_hand - blocked + 1))
    assert violations(cursor, "stock below zero at any point in the ledger") == 1


def test_validator_catches_a_block_with_one_row_missing(cursor):
    sku_id, location_id, _on_hand, _blocked = stocked_pair(cursor)
    # Only the +1 BLOCKED row, which would raise total stock from nothing.
    add_movement(cursor, "MOV-2026-99999999", sku_id, location_id, "BLOCK", "BLOCKED", 1, group="GRP-TEST")
    assert violations(cursor, "BLOCK or UNBLOCK group that is not a matching pair") == 1
    assert violations(cursor, "BLOCK or UNBLOCK that changes total stock") == 1


def test_validator_catches_a_balance_that_does_not_reconcile(cursor):
    sku_id, location_id, _on_hand, _blocked = stocked_pair(cursor)
    add_movement(cursor, "MOV-2026-99999999", sku_id, location_id, "ADJUSTMENT", "SELLABLE", -1)
    assert violations(cursor, "closing balance does not reconcile to opening plus ledger") == 1


def test_block_pair_leaves_total_stock_unchanged_in_sql(cursor):
    """The two-row BLOCK, checked with the same SUM the formula uses."""
    sku_id, location_id, _on_hand, _blocked = stocked_pair(cursor)
    total_sql = """SELECT COALESCE(SUM(quantity), 0) FROM inventory_movement
                   WHERE sku_id = %s AND location_id = %s"""
    cursor.execute(total_sql, (sku_id, location_id))
    before = cursor.fetchone()[0]

    add_movement(cursor, "MOV-2026-99999998", sku_id, location_id, "BLOCK", "SELLABLE", -2, group="GRP-TEST")
    add_movement(cursor, "MOV-2026-99999999", sku_id, location_id, "BLOCK", "BLOCKED", 2, group="GRP-TEST")

    cursor.execute(total_sql, (sku_id, location_id))
    assert cursor.fetchone()[0] == before
    assert violations(cursor, "BLOCK or UNBLOCK group that is not a matching pair") == 0
