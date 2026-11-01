"""
The schema and the validator, against a real PostgreSQL.

The "catches" tests break the data on purpose inside a transaction. A validator
that has only ever passed proves nothing, so these show each check can fail.
"""

from datetime import timedelta

import psycopg2
import pytest

from generator import settings
from scripts import validate
from tests.conftest import small_scale

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
    assert cursor.fetchone()[0] == settings.clean_scale(small_scale())["movements"]


def test_order_count_matches_the_target(cursor):
    cursor.execute("SELECT COUNT(*) FROM orders")
    assert cursor.fetchone()[0] == settings.clean_scale(small_scale())["orders"]


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


def test_sql_stock_at_the_end_equals_the_closing_balance(cursor):
    """The as-of query has to land on the same number as the stored snapshot."""
    from app import atp, config
    cursor.execute("""SELECT sku_id, location_id, on_hand, blocked FROM inventory_balance
                      WHERE snapshot_type = 'CLOSING' ORDER BY sku_id, location_id""")
    closing = cursor.fetchall()
    assert closing
    for sku_id, location_id, on_hand, blocked in closing:
        assert atp.get_stock(cursor, sku_id, location_id, config.AS_OF) == (on_hand, blocked)


def test_sql_stock_before_anything_happened_equals_the_opening_balance(cursor):
    from app import atp, config
    cursor.execute("""SELECT sku_id, location_id, on_hand, blocked FROM inventory_balance
                      WHERE snapshot_type = 'OPENING' ORDER BY sku_id, location_id LIMIT 50""")
    for sku_id, location_id, on_hand, blocked in cursor.fetchall():
        assert atp.get_stock(cursor, sku_id, location_id, config.START_AT) == (on_hand, blocked)


def test_order_flow_works_on_generated_data(cursor):
    """The fixture rolls this back, so the generated data stays as it was."""
    from app import atp, config, orders
    cursor.execute("""SELECT r.sku_id FROM inventory_balance b
                      JOIN replenishment_rule r USING (sku_id, location_id)
                      JOIN location l USING (location_id)
                      WHERE b.snapshot_type = 'CLOSING' AND l.location_type = 'WAREHOUSE'
                        AND b.on_hand - b.blocked > r.safety_stock + 5
                        AND (r.effective_to IS NULL OR r.effective_to > %s::date)
                      ORDER BY r.sku_id LIMIT 1""", (config.AS_OF,))
    sku_id = cursor.fetchone()[0]
    before = atp.network_promisable(atp.get_positions(cursor, sku_id, "WEB", config.AS_OF))

    order = orders.place_order_in_transaction(
        cursor, "WEB", "CUST-1", "SOUTH", [{"sku_id": sku_id, "qty": 1}, {"sku_id": sku_id, "qty": before}],
        config.AS_OF)

    assert [line["status"] for line in order["lines"]] == ["ALLOCATED", "REJECTED"]
    assert atp.network_promisable(atp.get_positions(cursor, sku_id, "WEB", config.AS_OF)) == before - 1
    order_checks = [r for r in validate.run_checks(cursor) if not r["passed"]]
    assert order_checks == []


# ------------------------------------------------------------ transfers in the generated data

def received_transfer(cursor):
    cursor.execute("""SELECT shipment_id, transfer_sku_id, from_location_id, to_location_id, transfer_qty
                      FROM shipment WHERE shipment_type = 'TRANSFER' AND status = 'RECEIVED'
                      ORDER BY shipment_id LIMIT 1""")
    return cursor.fetchone()


def in_transit_transfer(cursor):
    cursor.execute("""SELECT shipment_id, transfer_sku_id, from_location_id, to_location_id, transfer_qty
                      FROM shipment WHERE shipment_type = 'TRANSFER' AND status = 'IN_TRANSIT'
                      ORDER BY shipment_id LIMIT 1""")
    return cursor.fetchone()


def add_transfer_row(cursor, movement_id, shipment_id, sku_id, location_id, movement_type, quantity,
                     occurred_at="2026-09-30 23:00:00"):
    cursor.execute(
        """INSERT INTO inventory_movement
           (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity, occurred_at,
            reason_code, source_doc, shipment_id)
           VALUES (%s, %s, %s, %s, 'SELLABLE', %s, %s, 'TEST', %s, %s)""",
        (movement_id, sku_id, location_id, movement_type, quantity, occurred_at, f"TEST-{movement_id}", shipment_id),
    )


def test_generated_network_stock_plus_in_transit_is_conserved_in_sql(cursor):
    """Two calculations: this one from the query the live system uses, for every SKU."""
    from app import atp, config
    cursor.execute("""SELECT DISTINCT transfer_sku_id FROM shipment
                      WHERE shipment_type = 'TRANSFER' ORDER BY 1 LIMIT 20""")
    for (sku_id,) in cursor.fetchall():
        cursor.execute("""SELECT COALESCE(SUM(quantity), 0) FROM inventory_movement
                          WHERE sku_id = %s AND movement_type NOT IN ('TRANSFER_OUT', 'TRANSFER_IN')""", (sku_id,))
        outside = cursor.fetchone()[0]
        cursor.execute("""SELECT SUM(on_hand) FROM inventory_balance
                          WHERE sku_id = %s AND snapshot_type = 'OPENING'""", (sku_id,))
        opening = cursor.fetchone()[0]
        on_hand = atp.get_network_on_hand(cursor, sku_id, config.AS_OF)
        assert on_hand + atp.get_in_transit(cursor, sku_id, config.AS_OF) == opening + outside


def test_generated_truck_counts_on_the_road_only_between_its_two_rows(cursor):
    from app import atp
    from app import transfers
    shipment_id, sku_id, _source, _destination, qty = received_transfer(cursor)
    cursor.execute("""SELECT occurred_at FROM inventory_movement
                      WHERE shipment_id = %s ORDER BY occurred_at""", (shipment_id,))
    (left,), (arrived,) = cursor.fetchall()
    second = timedelta(seconds=1)

    assert transfers.get_shipment_state(cursor, shipment_id, left - second) is None
    assert transfers.get_shipment_state(cursor, shipment_id, left) == "IN_TRANSIT"
    assert transfers.get_shipment_state(cursor, shipment_id, arrived - second) == "IN_TRANSIT"
    assert transfers.get_shipment_state(cursor, shipment_id, arrived) == "RECEIVED"
    # Other trucks may carry this SKU too, but this one's units are in the count.
    assert atp.get_in_transit(cursor, sku_id, left) >= qty


def test_live_transfer_after_a_load_does_not_collide_with_generated_ids(cursor):
    from app import orders
    cursor.execute("SELECT COUNT(*) FROM inventory_movement")
    movement_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM shipment")
    shipment_count = cursor.fetchone()[0]
    cursor.execute("SELECT MAX(event_id) FROM shipment_event")
    last_event_id = cursor.fetchone()[0]

    assert orders.next_id(cursor, "movement_number") > movement_count
    assert orders.next_id(cursor, "shipment_number") > shipment_count
    assert orders.next_id(cursor, "event_number") > last_event_id


def test_validator_catches_a_receipt_with_no_arrival_row_behind_it(cursor):
    shipment_id = in_transit_transfer(cursor)[0]
    cursor.execute("UPDATE shipment SET status = 'RECEIVED' WHERE shipment_id = %s", (shipment_id,))
    assert violations(cursor, "received transfer without exactly one matching TRANSFER_IN") == 1


def test_validator_catches_an_arrival_while_still_in_transit(cursor):
    shipment_id, sku_id, _source, destination, qty = in_transit_transfer(cursor)
    add_transfer_row(cursor, "MOV-2026-99999999", shipment_id, sku_id, destination, "TRANSFER_IN", qty)
    assert violations(cursor, "transfer still in transit with a TRANSFER_IN already posted") == 1
    assert violations(cursor, "transfer units that left, arrived or stayed on the road do not add up") == 1


def test_validator_catches_a_truck_arriving_twice(cursor):
    shipment_id, sku_id, _source, destination, qty = received_transfer(cursor)
    add_transfer_row(cursor, "MOV-2026-99999999", shipment_id, sku_id, destination, "TRANSFER_IN", qty)
    assert violations(cursor, "received transfer without exactly one matching TRANSFER_IN") == 1


def test_validator_catches_a_truck_leaving_twice(cursor):
    shipment_id, sku_id, source, _destination, qty = received_transfer(cursor)
    add_transfer_row(cursor, "MOV-2026-99999999", shipment_id, sku_id, source, "TRANSFER_OUT", -qty)
    assert violations(cursor, "transfer without exactly one matching TRANSFER_OUT") == 1


def test_validator_catches_an_arrival_at_the_wrong_location(cursor):
    shipment_id, sku_id, source, _destination, qty = received_transfer(cursor)
    add_transfer_row(cursor, "MOV-2026-99999999", shipment_id, sku_id, source, "TRANSFER_IN", qty)
    assert violations(cursor, "transfer movement that does not belong to its shipment") == 1


def test_validator_catches_a_truck_received_before_it_shipped(cursor):
    shipment_id, sku_id, _source, destination, qty = in_transit_transfer(cursor)
    add_transfer_row(cursor, "MOV-2026-99999999", shipment_id, sku_id, destination, "TRANSFER_IN", qty,
                     occurred_at="2026-01-01 00:00:00")
    assert violations(cursor, "transfer received at or before it shipped") == 1


def test_validator_catches_a_shipment_event_out_of_step_with_its_status(cursor):
    shipment_id = in_transit_transfer(cursor)[0]
    cursor.execute("UPDATE shipment SET status = 'CANCELLED' WHERE shipment_id = %s", (shipment_id,))
    assert violations(cursor, "shipment status differs from its latest event") == 1


def test_validator_catches_arrival_time_that_differs_from_its_event(cursor):
    """That gap would be a moment when the truck counts as inbound and as on hand."""
    shipment_id = received_transfer(cursor)[0]
    cursor.execute("ALTER TABLE inventory_movement DISABLE TRIGGER inventory_movement_append_only")
    cursor.execute("""UPDATE inventory_movement SET occurred_at = occurred_at + INTERVAL '1 hour'
                      WHERE shipment_id = %s AND movement_type = 'TRANSFER_IN'""", (shipment_id,))
    assert violations(cursor, "TRANSFER_IN time differs from the RECEIVED event") == 1


def test_validator_catches_a_lost_truck_in_the_network_total(cursor):
    shipment_id = in_transit_transfer(cursor)[0]
    cursor.execute("UPDATE shipment SET status = 'CANCELLED' WHERE shipment_id = %s", (shipment_id,))
    assert violations(cursor, "network stock plus in transit differs from opening plus outside changes") == 1


def test_database_refuses_a_transfer_row_with_no_shipment(cursor):
    sku_id, location_id, _on_hand, _blocked = stocked_pair(cursor)
    with pytest.raises(psycopg2.errors.CheckViolation):
        add_movement(cursor, "MOV-2026-99999999", sku_id, location_id, "TRANSFER_OUT", "SELLABLE", -1)


def test_database_refuses_a_transfer_to_the_same_location(cursor):
    shipment_id, sku_id, source, _destination, qty = received_transfer(cursor)
    with pytest.raises(psycopg2.errors.CheckViolation):
        cursor.execute(
            """INSERT INTO shipment (shipment_id, shipment_type, status, from_location_id, to_location_id,
                                     created_at, transfer_sku_id, transfer_qty)
               VALUES ('SHP-2026-999999', 'TRANSFER', 'CREATED', %s, %s, '2026-03-01', %s, %s)""",
            (source, source, sku_id, qty),
        )


# ------------------------------------------------------------ returns in the generated data

def a_return(cursor):
    cursor.execute("""SELECT r.movement_id, r.related_movement_id, r.sku_id, r.location_id, r.quantity, s.quantity
                      FROM inventory_movement r JOIN inventory_movement s ON s.movement_id = r.related_movement_id
                      WHERE r.movement_type = 'RETURN' ORDER BY r.movement_id LIMIT 1""")
    return cursor.fetchone()


def add_return(cursor, related_id, sku_id, location_id, quantity, occurred_at="2026-09-30 23:00:00"):
    cursor.execute(
        """INSERT INTO inventory_movement
           (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity, occurred_at,
            reason_code, source_doc, related_movement_id)
           VALUES ('MOV-2026-99999999', %s, %s, 'RETURN', 'SELLABLE', %s, %s, 'TEST', 'TEST-RET', %s)""",
        (sku_id, location_id, quantity, occurred_at, related_id),
    )


def test_validator_catches_a_return_of_more_than_was_sold(cursor):
    _return_id, sale_id, sku_id, location_id, _returned, sold = a_return(cursor)
    add_return(cursor, sale_id, sku_id, location_id, -sold)
    assert violations(cursor, "returned more than was sold") == 1


def test_validator_catches_a_return_linked_to_something_that_is_not_a_sale(cursor):
    return_id, _sale_id, sku_id, location_id, _returned, _sold = a_return(cursor)
    add_return(cursor, return_id, sku_id, location_id, 1)
    assert violations(cursor, "return not linked to a sale") == 1


def test_validator_catches_a_return_at_another_location(cursor):
    _return_id, sale_id, sku_id, location_id, _returned, _sold = a_return(cursor)
    cursor.execute("""SELECT location_id FROM sku_location WHERE sku_id = %s AND location_id <> %s
                      ORDER BY location_id LIMIT 1""", (sku_id, location_id))
    other_location = cursor.fetchone()[0]
    add_return(cursor, sale_id, sku_id, other_location, 1)
    assert violations(cursor, "return at a different SKU or location than its sale") == 1


def test_validator_catches_a_return_before_its_sale(cursor):
    _return_id, sale_id, sku_id, location_id, _returned, _sold = a_return(cursor)
    add_return(cursor, sale_id, sku_id, location_id, 1, occurred_at="2026-01-01 00:00:00")
    assert violations(cursor, "return at or before its sale") == 1


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
