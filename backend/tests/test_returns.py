"""
Returns against a real PostgreSQL. Every return names a real sale, goes back to the
location that sold it, and all returns of a sale can't add up to more than was sold.
"""

import threading
from datetime import timedelta

import psycopg2
import pytest

from app import atp, orders, reservations, returns, shipping
from app.movements import add_movement
from tests.conftest import SKU_A, SKU_B, STORE_SOUTH, WAREHOUSE_SOUTH, connect
from tests.test_orders import at, call, fetch, line, place, run_in_threads, stock, wait_until_blocked_on_a_lock
from tests.test_transfers import assert_checks_pass

pytestmark = pytest.mark.database

RETURN_CHECKS = [
    "return not linked to a sale", "return at a different SKU or location than its sale",
    "return at or before its sale", "returned more than was sold",
]


def walk_in_sale(qty=3, sold_at=at(10), location_id=STORE_SOUTH, sku_id=SKU_A, doc="POS-1"):
    connection = connect()
    with connection, connection.cursor() as cursor:
        movement_id = add_movement(cursor, sku_id, location_id, "SALE", -qty, sold_at, "WALK_IN", doc)
    connection.close()
    return movement_id


def give_back(sale_id, qty=1, condition="GOOD", returned_at=at(12)):
    return call(returns.record_return, sale_id, qty, condition, returned_at)


def stock_at(asked_at, location_id=STORE_SOUTH, sku_id=SKU_A):
    connection = connect()
    with connection.cursor() as cursor:
        numbers = atp.get_stock(cursor, sku_id, location_id, asked_at)
    connection.close()
    return numbers


def return_rows():
    return fetch("""SELECT location_id, stock_bucket, quantity, related_movement_id
                    FROM inventory_movement WHERE movement_type = 'RETURN' ORDER BY movement_id""")


# ------------------------------------------------------------ a return that is allowed

def test_good_return_goes_back_on_the_shelf_at_the_location_of_the_sale(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    assert stock_at(at(11)) == (7, 0)

    give_back(sale_id, qty=2, condition="GOOD", returned_at=at(12))

    assert stock_at(at(11, 59, 59)) == (7, 0)
    assert stock_at(at(12)) == (9, 0)
    assert return_rows() == [(STORE_SOUTH, "SELLABLE", 2, sale_id)]
    assert_checks_pass(*RETURN_CHECKS)


def test_damaged_return_counts_as_on_hand_but_cannot_be_sold(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3, location_id=WAREHOUSE_SOUTH)

    connection = connect()
    with connection.cursor() as cursor:
        before = atp.get_positions(cursor, SKU_A, "WEB", at(11))[0]
    connection.close()

    give_back(sale_id, qty=2, condition="DAMAGED", returned_at=at(12))

    connection = connect()
    with connection.cursor() as cursor:
        after = atp.get_positions(cursor, SKU_A, "WEB", at(12))[0]
    connection.close()

    assert (before["on_hand"], before["blocked"], before["atp"]) == (7, 0, 7)
    assert (after["on_hand"], after["blocked"], after["atp"]) == (9, 2, 7)


def test_return_of_an_online_order_links_to_its_sale(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(2)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 5))
    call(shipping.ship_order, order["order_id"], at(11))
    sale_id = fetch("SELECT movement_id FROM inventory_movement WHERE movement_type = 'SALE'")[0][0]

    give_back(sale_id, qty=2, returned_at=at(15))

    assert return_rows() == [(WAREHOUSE_SOUTH, "SELLABLE", 2, sale_id)]
    assert stock_at(at(15), WAREHOUSE_SOUTH) == (10, 0)
    assert_checks_pass(*RETURN_CHECKS)


# ------------------------------------------------------------ never more than was sold

def test_cannot_return_more_than_was_sold(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    with pytest.raises(returns.ReturnRefused, match="3 sold, 0 already returned"):
        give_back(sale_id, qty=4)
    assert return_rows() == []
    assert stock_at(at(23)) == (7, 0)


def test_a_sale_can_come_back_in_parts_up_to_exactly_what_was_sold(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)

    give_back(sale_id, qty=1, returned_at=at(12))
    give_back(sale_id, qty=2, returned_at=at(13))

    assert [row[2] for row in return_rows()] == [1, 2]
    assert stock_at(at(14)) == (10, 0)
    assert_checks_pass(*RETURN_CHECKS)


def test_nothing_more_can_come_back_after_the_full_quantity(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    give_back(sale_id, qty=3, returned_at=at(12))
    with pytest.raises(returns.ReturnRefused, match="3 already returned"):
        give_back(sale_id, qty=1, returned_at=at(13))
    assert stock_at(at(14)) == (10, 0)


def test_second_return_is_measured_against_what_is_left(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    give_back(sale_id, qty=2, returned_at=at(12))
    with pytest.raises(returns.ReturnRefused, match="2 already returned"):
        give_back(sale_id, qty=2, returned_at=at(13))


def test_returns_of_one_sale_do_not_use_up_another_sale(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    first = walk_in_sale(qty=1, sold_at=at(10), doc="POS-1")
    second = walk_in_sale(qty=1, sold_at=at(10, 1), doc="POS-2")
    give_back(first, qty=1, returned_at=at(12))
    give_back(second, qty=1, returned_at=at(13))
    assert_checks_pass(*RETURN_CHECKS)


@pytest.mark.parametrize("qty", [0, -1])
def test_zero_or_negative_return_is_refused(orders_db, qty):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    with pytest.raises(returns.ReturnRefused):
        give_back(sale_id, qty=qty)
    assert return_rows() == []


# ------------------------------------------------------------ it has to be a real sale

def test_return_with_no_such_sale_is_refused(orders_db):
    with pytest.raises(returns.ReturnRefused, match="no movement"):
        give_back("MOV-2026-99999999")


def test_return_against_something_that_is_not_a_sale_is_refused(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    connection = connect()
    with connection, connection.cursor() as cursor:
        receipt_id = add_movement(cursor, SKU_A, STORE_SOUTH, "RECEIPT", 5, at(9), "SUPPLIER_DELIVERY", "RCV-1")
    connection.close()
    with pytest.raises(returns.ReturnRefused, match="not a sale"):
        give_back(receipt_id)


def test_return_against_a_return_is_refused(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    first_return = give_back(sale_id, qty=1)["movement_id"]
    with pytest.raises(returns.ReturnRefused, match="not a sale"):
        give_back(first_return, qty=1, returned_at=at(13))


@pytest.mark.parametrize("returned_at", [at(9), at(10)])
def test_return_at_or_before_its_sale_is_refused(orders_db, returned_at):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3, sold_at=at(10))
    with pytest.raises(returns.ReturnRefused, match="before its sale"):
        give_back(sale_id, returned_at=returned_at)


def test_return_one_second_after_its_sale_is_allowed(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3, sold_at=at(10))
    give_back(sale_id, returned_at=at(10) + timedelta(seconds=1))
    assert_checks_pass(*RETURN_CHECKS)


def test_unknown_condition_is_refused(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    with pytest.raises(returns.ReturnRefused, match="unknown condition"):
        give_back(sale_id, condition="USED")


def test_database_refuses_a_return_with_no_sale_named(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    connection = connect()
    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.CheckViolation):
            add_movement(cursor, SKU_A, STORE_SOUTH, "RETURN", 1, at(12), "RETURN_GOOD", "RET-1")
    connection.rollback()
    connection.close()


def test_database_refuses_a_return_naming_a_row_that_does_not_exist(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    connection = connect()
    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            add_movement(cursor, SKU_A, STORE_SOUTH, "RETURN", 1, at(12), "RETURN_GOOD", "RET-1",
                         related_movement_id="MOV-2026-99999999")
    connection.rollback()
    connection.close()


def test_database_refuses_a_sale_that_names_another_sale(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=1)
    connection = connect()
    with connection.cursor() as cursor:
        with pytest.raises(psycopg2.errors.CheckViolation):
            add_movement(cursor, SKU_A, STORE_SOUTH, "SALE", -1, at(12), "WALK_IN", "POS-2",
                         related_movement_id=sale_id)
    connection.rollback()
    connection.close()


# ------------------------------------------------------------ two returns at once

def test_twenty_returns_for_three_units_never_exceed_what_was_sold(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)

    def work(_index):
        try:
            give_back(sale_id, qty=1)
            return "ACCEPTED"
        except returns.ReturnRefused:
            return "REFUSED"

    results = run_in_threads(20, work)

    assert results.count("ACCEPTED") == 3
    assert results.count("REFUSED") == 17
    assert stock_at(at(13)) == (10, 0)
    assert_checks_pass(*RETURN_CHECKS)


def test_second_return_waits_then_sees_what_the_first_took(orders_db):
    """The same race with no luck involved."""
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=1)

    first_connection = connect()
    first_cursor = first_connection.cursor()
    returns.record_return_in_transaction(first_cursor, sale_id, 1, "GOOD", at(12))

    outcome = {}

    def second():
        try:
            give_back(sale_id, qty=1, returned_at=at(12))
            outcome["result"] = "ACCEPTED"
        except returns.ReturnRefused:
            outcome["result"] = "REFUSED"

    thread = threading.Thread(target=second)
    thread.start()
    wait_until_blocked_on_a_lock()
    assert "result" not in outcome

    first_connection.commit()
    first_cursor.close()
    first_connection.close()
    thread.join(timeout=30)

    assert outcome["result"] == "REFUSED"
    assert len(return_rows()) == 1


def test_lock_timeout_says_busy_and_returns_nothing(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3)
    holder = connect()
    with holder.cursor() as cursor:
        cursor.execute("SELECT 1 FROM sku_location FOR UPDATE")
        with pytest.raises(orders.StockBusy):
            call(returns.record_return, sale_id, 1, "GOOD", at(12), lock_timeout_ms=100)
    holder.rollback()
    holder.close()
    assert return_rows() == []


def test_a_return_only_touches_its_own_sku(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 10), (SKU_B, STORE_SOUTH, 10))
    sale_id = walk_in_sale(qty=3, sku_id=SKU_A)
    give_back(sale_id, qty=1)
    assert stock_at(at(13), STORE_SOUTH, SKU_B) == (10, 0)
