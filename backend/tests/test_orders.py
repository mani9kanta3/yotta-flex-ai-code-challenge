"""
Reservations, ATP and allocation against a real PostgreSQL.

These tests commit for real, because a lock only means something between separate
transactions. Each one starts from a clean database with exact numbers.
"""

import threading
import time
from datetime import datetime, timedelta

import pytest

from app import atp, orders, reservations, rules
from scripts import validate
from tests.conftest import (NDC, SKU_A, SKU_B, STORE_SOUTH, WAREHOUSE_NORTH, WAREHOUSE_SOUTH,
                            add_shipment_event, add_stock, add_transfer, connect)

pytestmark = pytest.mark.database

T0 = datetime(2026, 3, 1, 10, 0, 0)
HOLD = timedelta(minutes=30)   # WEB and APP


def at(hour, minute=0, second=0):
    return datetime(2026, 3, 1, hour, minute, second)


def stock(orders_db, *pairs, **options):
    connection = connect()
    with connection, connection.cursor() as cursor:
        for sku_id, location_id, on_hand in pairs:
            add_stock(cursor, sku_id, location_id, on_hand, **options)
    connection.close()


def place(lines, placed_at=T0, region="SOUTH", channel="WEB", **options):
    connection = connect()
    try:
        return orders.place_order(connection, channel, "CUST-1", region, lines, placed_at, **options)
    finally:
        connection.close()


def line(qty=1, sku_id=SKU_A):
    return {"sku_id": sku_id, "qty": qty}


def reserved(asked_at, sku_id=SKU_A, location_id=WAREHOUSE_SOUTH):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            return atp.get_reserved(cursor, sku_id, location_id, asked_at)
    finally:
        connection.close()


def promisable(asked_at, sku_id=SKU_A, channel="WEB"):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            return atp.network_promisable(atp.get_positions(cursor, sku_id, channel, asked_at))
    finally:
        connection.close()


def call(function, *args, **kwargs):
    connection = connect()
    try:
        return function(connection, *args, **kwargs)
    finally:
        connection.close()


def fetch(sql, params=()):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()
    finally:
        connection.close()


def only_reservation(order):
    return order["lines"][0]["allocations"][0]


def assert_order_records_are_consistent():
    """The validator's order checks, run against orders placed for real."""
    connection = connect()
    try:
        with connection.cursor() as cursor:
            results = {r["check"]: r["violations"] for r in validate.run_checks(cursor)}
    finally:
        connection.close()
    order_checks = [
        "order_id format", "reservation_id format", "reservation with no event",
        "reservation event with an illegal transition", "reservation events that do not chain",
        "reservation status differs from its latest event", "order event with an illegal transition",
        "order line event with an illegal transition", "order status differs from its latest event",
        "order line status differs from its latest event", "order total differs from its lines",
        "allocated line whose allocations do not add up to its quantity",
        "allocation and its reservation disagree", "inbound holds above what the shipment carries",
    ]
    assert {name: results[name] for name in order_checks if results[name] != 0} == {}


# ------------------------------------------------------------ ATP from the database

def test_worked_example_from_the_plan(orders_db):
    """on_hand 100, blocked 5, reserved 12, inbound 20, safety 10."""
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 100), blocked=5, safety_stock=10)
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_transfer(cursor, "SHP-2026-000001", SKU_A, WAREHOUSE_SOUTH, 20, at(8), at(20))
        cursor.execute("UPDATE channel_location SET buffer_units = 3 WHERE channel_code = 'WEB'")
        cursor.execute("UPDATE channel_location SET buffer_units = 5 WHERE channel_code = 'MARKETPLACE'")
    connection.close()
    place([line(12)], placed_at=at(9), channel="APP")

    connection = connect()
    with connection.cursor() as cursor:
        web = atp.get_positions(cursor, SKU_A, "WEB", at(9, 10))[0]
        marketplace = atp.get_positions(cursor, SKU_A, "MARKETPLACE", at(9, 10))[0]
    connection.close()

    assert (web["on_hand"], web["blocked"], web["reserved"], web["inbound"]) == (100, 5, 12, 20)
    assert web["atp"] == 103
    assert web["promisable"] == 90
    assert marketplace["atp"] == 103
    assert marketplace["promisable"] == 68


def test_network_adds_up_each_location_after_its_own_buffer(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10), (SKU_A, WAREHOUSE_NORTH, 7), safety_stock=2)
    assert promisable(T0) == 8 + 5


def test_safety_stock_and_buffer_can_sell_nothing(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5), safety_stock=5)
    order = place([line(1)])
    assert order["status"] == "REJECTED"


def test_blocked_stock_cannot_be_promised(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5), blocked=5)
    assert promisable(T0) == 0


def test_store_channel_is_marked_as_not_built(orders_db):
    stock(orders_db, (SKU_A, STORE_SOUTH, 5))
    with pytest.raises(NotImplementedError, match="not built yet"):
        place([line(1)], channel="STORE")
    assert fetch("SELECT COUNT(*) FROM orders") == [(0,)]


# ------------------------------------------------------------ historical reservation states

def test_history_confirmed_then_cancelled(orders_db):
    """The timeline in the plan: 5 units, confirmed at 10:20, cancelled at 11:00."""
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 20))
    call(reservations.cancel_order, order["order_id"], at(11))

    assert reserved(at(9, 59, 59)) == 0
    assert reserved(at(10)) == 5
    assert reserved(at(10, 10)) == 5
    assert reserved(at(10, 45)) == 5
    assert reserved(at(10, 59, 59)) == 5
    assert reserved(at(11)) == 0
    assert reserved(at(12)) == 0


def test_cancelling_today_does_not_change_what_last_week_says(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    assert reserved(at(10, 10)) == 5

    call(reservations.cancel_order, order["order_id"], at(10, 15))

    assert reserved(at(10, 10)) == 5
    assert reserved(at(10, 15)) == 0
    assert fetch("SELECT status FROM reservation") == [("CANCELLED",)]


def test_cancelled_before_confirmation_stops_holding(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    call(reservations.cancel_order, order["order_id"], at(10, 5))
    assert reserved(at(10, 4, 59)) == 5
    assert reserved(at(10, 5)) == 0


def test_confirmed_hold_outlives_its_expiry(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 29, 59))
    assert reserved(at(10, 30)) == 5
    assert reserved(at(23)) == 5
    # expire_due must leave it alone too.
    assert call(reservations.expire_due, at(23))["reservations_expired"] == 0
    assert reserved(at(23)) == 5


def test_rejected_request_never_holds_stock_at_any_time(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 3))
    order = place([line(9)], placed_at=at(10))
    assert order["status"] == "REJECTED"
    for asked_at in (at(9), at(10), at(10, 1), at(23)):
        assert reserved(asked_at) == 0


def test_python_rule_and_sql_agree_at_every_second_around_each_change(orders_db):
    """Two separate calculations of the same thing, compared at each boundary."""
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 20))
    confirmed = place([line(5)], placed_at=at(10))
    call(reservations.confirm_order, confirmed["order_id"], at(10, 20))
    call(reservations.cancel_order, confirmed["order_id"], at(11))
    place([line(2)], placed_at=at(11))
    place([line(30)], placed_at=at(11))

    rows = fetch("SELECT reservation_id, qty, expires_at FROM reservation")
    python_reservations = []
    for reservation_id, qty, expires_at in rows:
        events = fetch("""SELECT occurred_at, event_id, to_status FROM reservation_event
                          WHERE reservation_id = %s""", (reservation_id,))
        python_reservations.append({"qty": qty, "expires_at": expires_at, "events": events})

    changes = [at(10), at(10, 20), at(10, 30), at(11), at(11, 30)]
    times = [change + timedelta(seconds=step) for change in changes for step in (-1, 0, 1)]
    for asked_at in times:
        assert reserved(asked_at) == rules.reserved_at(python_reservations, asked_at), asked_at


# ------------------------------------------------------------ expiry at the exact time

def test_hold_is_released_at_exactly_expiry_before_the_job_runs(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    place([line(5)], placed_at=at(10))

    assert reserved(at(10, 29, 59)) == 5
    assert reserved(at(10, 30)) == 0
    # No job has run, so the status column still says ACTIVE. ATP didn't need it.
    assert fetch("SELECT status FROM reservation") == [("ACTIVE",)]


def test_late_job_records_the_real_expiry_time_and_changes_no_answer(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    before = [reserved(t) for t in (at(10, 29, 59), at(10, 30), at(15))]

    summary = call(reservations.expire_due, at(15))

    assert summary == {"orders_expired": 1, "reservations_expired": 1, "units_released": 5}
    assert [reserved(t) for t in (at(10, 29, 59), at(10, 30), at(15))] == before
    assert fetch("""SELECT occurred_at FROM reservation_event WHERE to_status = 'EXPIRED'""") == [(at(10, 30),)]
    assert fetch("SELECT status FROM orders WHERE order_id = %s", (order["order_id"],)) == [("CANCELLED",)]
    assert fetch("SELECT status FROM order_line") == [("CANCELLED",)]
    assert_order_records_are_consistent()


def test_job_expires_at_exact_time_and_not_one_second_earlier(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    place([line(5)], placed_at=at(10))

    assert call(reservations.expire_due, at(10, 29, 59))["reservations_expired"] == 0
    assert call(reservations.expire_due, at(10, 30))["reservations_expired"] == 1


def test_running_expiry_twice_changes_nothing(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    place([line(5)], placed_at=at(10))
    call(reservations.expire_due, at(11))
    assert call(reservations.expire_due, at(11))["reservations_expired"] == 0
    assert fetch("SELECT COUNT(*) FROM reservation_event WHERE to_status = 'EXPIRED'") == [(1,)]


def test_expiry_writes_no_movement(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    place([line(5)], placed_at=at(10))
    call(reservations.expire_due, at(11))
    assert fetch("SELECT COUNT(*) FROM inventory_movement") == [(0,)]


def test_confirm_works_one_second_before_expiry(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 29, 59))
    assert fetch("SELECT status FROM reservation") == [("CONFIRMED",)]


def test_confirm_is_refused_at_exactly_expiry(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    with pytest.raises(reservations.OrderStateError, match="expired"):
        call(reservations.confirm_order, order["order_id"], at(10, 30))
    assert fetch("SELECT status FROM reservation") == [("ACTIVE",)]


def test_stock_can_be_sold_again_at_exactly_expiry_and_not_before(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))
    first = place([line(1)], placed_at=at(10))
    assert first["status"] == "ALLOCATED"

    too_early = place([line(1)], placed_at=at(10, 29, 59))
    assert too_early["status"] == "REJECTED"

    right_on_time = place([line(1)], placed_at=at(10, 30))
    assert right_on_time["status"] == "ALLOCATED"


def test_cannot_expire_then_confirm(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    call(reservations.expire_due, at(11))
    with pytest.raises(reservations.OrderStateError, match="CANCELLED"):
        call(reservations.confirm_order, order["order_id"], at(11, 1))


def test_cannot_write_an_event_dated_before_the_last_one(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(5)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 20))
    with pytest.raises(reservations.OrderStateError, match="before the last event"):
        call(reservations.cancel_order, order["order_id"], at(10, 10))


# ------------------------------------------------------------ two orders, one last unit

def run_in_threads(count, work):
    """Everything starts at the same moment, to give a race the best chance of happening."""
    barrier = threading.Barrier(count)
    results = [None] * count
    errors = []

    def runner(index):
        try:
            barrier.wait()
            results[index] = work(index)
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert errors == []
    return results


def wait_until_blocked_on_a_lock(timeout_seconds=10):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        rows = fetch("""SELECT COUNT(*) FROM pg_stat_activity
                        WHERE wait_event_type = 'Lock' AND datname = current_database()""")
        if rows[0][0] > 0:
            return
        time.sleep(0.02)
    raise AssertionError("the second order never waited on the lock")


def test_two_orders_for_the_last_unit_exactly_one_wins(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))

    results = run_in_threads(2, lambda index: place([line(1)]))

    assert sorted(result["status"] for result in results) == ["ALLOCATED", "REJECTED"]
    assert reserved(T0) == 1
    assert promisable(T0) == 0
    assert_order_records_are_consistent()


def test_twenty_orders_for_the_last_unit_exactly_one_wins(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))

    results = run_in_threads(20, lambda index: place([line(1)]))

    statuses = [result["status"] for result in results]
    assert statuses.count("ALLOCATED") == 1
    assert statuses.count("REJECTED") == 19
    assert reserved(T0) == 1
    assert_order_records_are_consistent()


def test_twenty_orders_for_seven_units_never_oversell(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 7))

    results = run_in_threads(20, lambda index: place([line(1)]))

    assert [r["status"] for r in results].count("ALLOCATED") == 7
    assert reserved(T0) == 7
    assert promisable(T0) == 0


def test_second_order_waits_then_sees_what_the_first_reserved(orders_db):
    """
    The same race with no luck involved. The first order has reserved the last unit
    but hasn't committed. The second must wait at the lock, and after the commit it
    must read fresh numbers and be refused.
    """
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))

    first_connection = connect()
    first_cursor = first_connection.cursor()
    first = orders.place_order_in_transaction(first_cursor, "WEB", "CUST-1", "SOUTH", [line(1)], T0)
    assert first["status"] == "ALLOCATED"

    second_result = {}

    def second_order():
        second_result["order"] = place([line(1)])

    thread = threading.Thread(target=second_order)
    thread.start()
    wait_until_blocked_on_a_lock()
    assert "order" not in second_result

    first_connection.commit()
    first_cursor.close()
    first_connection.close()
    thread.join(timeout=30)

    assert second_result["order"]["status"] == "REJECTED"
    assert second_result["order"]["lines"][0]["reason_code"] == "INSUFFICIENT_ATP"


def test_second_order_gets_the_unit_if_the_first_rolls_back(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))

    first_connection = connect()
    first_cursor = first_connection.cursor()
    orders.place_order_in_transaction(first_cursor, "WEB", "CUST-1", "SOUTH", [line(1)], T0)

    second_result = {}
    thread = threading.Thread(target=lambda: second_result.update(order=place([line(1)])))
    thread.start()
    wait_until_blocked_on_a_lock()

    first_connection.rollback()
    first_cursor.close()
    first_connection.close()
    thread.join(timeout=30)

    assert second_result["order"]["status"] == "ALLOCATED"
    assert reserved(T0) == 1


def test_order_stamped_earlier_cannot_look_back_past_a_committed_hold(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))
    place([line(1)], placed_at=at(10, 0, 5))

    late_arrival = place([line(1)], placed_at=at(10, 0, 1))

    assert late_arrival["status"] == "REJECTED"
    assert late_arrival["placed_at"] == at(10, 0, 5)


def test_orders_listing_skus_in_opposite_order_do_not_deadlock(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 50), (SKU_B, WAREHOUSE_SOUTH, 50))

    def work(index):
        lines = [line(1, SKU_A), line(1, SKU_B)]
        if index % 2:
            lines.reverse()
        return place(lines)

    # A deadlock would raise an error in a thread, and run_in_threads fails on that.
    results = run_in_threads(20, work)

    assert all(result["status"] == "ALLOCATED" for result in results)
    assert reserved(T0, SKU_A) == 20
    assert reserved(T0, SKU_B) == 20


def test_lock_timeout_says_busy_and_writes_nothing(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 1))
    holder = connect()
    with holder.cursor() as cursor:
        cursor.execute("SELECT 1 FROM sku_location FOR UPDATE")

        with pytest.raises(orders.OrderBusy):
            place([line(1)], lock_timeout_ms=100)

    holder.rollback()
    holder.close()
    assert fetch("SELECT COUNT(*) FROM orders") == [(0,)]


def test_a_failure_part_way_leaves_nothing_behind(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    with pytest.raises(ValueError, match="unknown SKU"):
        place([line(1), line(1, "SKU-999999")])
    assert fetch("SELECT COUNT(*) FROM orders") == [(0,)]
    assert fetch("SELECT COUNT(*) FROM reservation") == [(0,)]


# ------------------------------------------------------------ allocation

def test_allocation_is_traceable_from_order_to_location(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    order = place([line(2)])

    rows = fetch("""
        SELECT o.order_id, l.order_line_id, a.allocation_id, a.location_id, r.reservation_id,
               d.decision_code, d.chosen_location_id, d.atp_seen, d.promisable_seen
        FROM orders o
        JOIN order_line l USING (order_id)
        JOIN order_allocation a USING (order_line_id)
        JOIN reservation r USING (reservation_id)
        JOIN allocation_decision d ON d.order_line_id = l.order_line_id""")

    assert len(rows) == 1
    row = rows[0]
    assert row[0] == order["order_id"]
    assert row[3] == WAREHOUSE_SOUTH
    assert row[4] == f"RES-{order['order_id']}-01"
    assert row[5:] == ("SINGLE", WAREHOUSE_SOUTH, 5, 5)


def test_ids_follow_the_brief(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    order = place([line(1)])
    assert order["order_id"] == "ORD-2026-000001"
    assert only_reservation(order)["reservation_id"] == "RES-ORD-2026-000001-01"
    assert order["lines"][0]["order_line_id"] == "ORD-2026-000001-L01"


def test_hold_length_comes_from_the_channel(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    assert only_reservation(place([line(1)], channel="WEB"))["expires_at"] == T0 + HOLD
    assert only_reservation(place([line(1)], channel="MARKETPLACE"))["expires_at"] == T0 + timedelta(minutes=120)


def test_same_region_is_preferred(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5), (SKU_A, WAREHOUSE_NORTH, 50))
    assert only_reservation(place([line(1)], region="SOUTH"))["location_id"] == WAREHOUSE_SOUTH
    assert only_reservation(place([line(1)], region="NORTH"))["location_id"] == WAREHOUSE_NORTH


def test_splits_across_two_locations_with_two_reservations(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 3), (SKU_A, WAREHOUSE_NORTH, 4))
    order = place([line(5)])

    assert order["lines"][0]["decision_code"] == "SPLIT"
    parts = order["lines"][0]["allocations"]
    assert [(p["location_id"], p["qty"], p["reservation_id"][-2:]) for p in parts] == [
        (WAREHOUSE_SOUTH, 3, "01"), (WAREHOUSE_NORTH, 2, "02")]
    assert_order_records_are_consistent()


def test_second_line_of_an_order_sees_the_first_line(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    order = place([line(4), line(4)])
    assert [item["status"] for item in order["lines"]] == ["ALLOCATED", "REJECTED"]
    assert order["status"] == "ALLOCATED"
    assert order["order_total"] == 400
    assert reserved(T0) == 4
    assert_order_records_are_consistent()


def test_oversell_attempt_is_rejected_and_kept_as_evidence(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 3))
    order = place([line(4)])

    assert order["status"] == "REJECTED"
    assert order["order_total"] == 0
    assert fetch("SELECT status, qty FROM reservation") == [("REJECTED", 4)]
    assert fetch("SELECT decision_code, reason_code FROM allocation_decision") == [
        ("REJECTED", "INSUFFICIENT_ATP")]
    assert fetch("SELECT COUNT(*) FROM order_allocation") == [(0,)]
    assert_order_records_are_consistent()


def test_sku_stocked_nowhere_is_rejected_with_no_location(orders_db):
    order = place([line(1)])
    assert order["lines"][0]["reason_code"] == "NO_CANDIDATE_LOCATION"
    assert fetch("SELECT location_id FROM reservation") == [(None,)]


@pytest.mark.parametrize("qty", [0, -1])
def test_order_refuses_zero_or_negative_quantity_before_writing(orders_db, qty):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    with pytest.raises(ValueError, match="above zero"):
        place([line(qty)])
    assert fetch("SELECT COUNT(*) FROM orders") == [(0,)]


# ------------------------------------------------------------ inbound stock

def truck(qty=5, shipped_at=at(8), eta_at=at(20), shipment_id="SHP-2026-000001"):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_transfer(cursor, shipment_id, SKU_A, WAREHOUSE_SOUTH, qty, shipped_at, eta_at)
    connection.close()


def test_order_on_inbound_names_the_truck_and_records_the_promise(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=at(20))

    part = only_reservation(place([line(3)]))

    assert part["supply_type"] == "INBOUND"
    assert part["inbound_shipment_id"] == "SHP-2026-000001"
    assert part["promised_ship_by"] == at(20) + timedelta(hours=24)


def test_on_hand_stock_is_used_before_inbound(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 3))
    truck(qty=5)
    assert only_reservation(place([line(3)]))["supply_type"] == "ON_HAND"


def test_cannot_promise_more_than_a_truck_carries(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5)
    assert place([line(4)])["status"] == "ALLOCATED"
    assert place([line(2)])["status"] == "REJECTED"
    assert place([line(1)])["status"] == "ALLOCATED"
    assert_order_records_are_consistent()


def test_marketplace_does_not_sell_stock_on_a_truck(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5)
    assert place([line(1)], channel="MARKETPLACE")["status"] == "REJECTED"


def test_truck_due_beyond_the_horizon_does_not_count(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=T0 + timedelta(hours=24, seconds=1))
    assert promisable(T0) == 0


def test_truck_due_exactly_at_the_horizon_counts(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=T0 + timedelta(hours=24))
    assert promisable(T0) == 5


def test_overdue_truck_stops_counting(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=at(12))
    assert promisable(at(12)) == 5
    assert promisable(at(12, 0, 1)) == 0


def test_new_eta_is_read_from_events_as_of_that_time(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=at(12))
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_shipment_event(cursor, "SHP-2026-000001", "IN_TRANSIT", "IN_TRANSIT", at(13),
                           eta_at=at(18))
    connection.close()

    assert promisable(at(11)) == 5       # the first ETA, still ahead
    assert promisable(at(12, 30)) == 0   # overdue, no new ETA known yet at this time
    assert promisable(at(13)) == 5       # the new ETA is known


def test_received_truck_is_no_longer_inbound(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=at(12))
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_shipment_event(cursor, "SHP-2026-000001", "IN_TRANSIT", "RECEIVED", at(11))
    connection.close()
    assert promisable(at(10, 59)) == 5
    assert promisable(at(11)) == 0


def test_rule_can_switch_off_inbound(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0), allow_inbound=False)
    truck(qty=5)
    assert promisable(T0) == 0


def test_expired_hold_frees_the_truck_for_someone_else(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 0))
    truck(qty=5, eta_at=at(23))
    place([line(5)], placed_at=at(10))
    assert place([line(1)], placed_at=at(10, 29, 59))["status"] == "REJECTED"
    assert place([line(5)], placed_at=at(10, 30))["status"] == "ALLOCATED"


# ------------------------------------------------------------ the ledger, through SQL

def test_blocked_movements_leave_on_hand_alone_in_the_atp_query(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    connection = connect()
    with connection, connection.cursor() as cursor:
        for movement_id, bucket, quantity in (("MOV-2026-00000001", "SELLABLE", -3),
                                              ("MOV-2026-00000002", "BLOCKED", 3)):
            cursor.execute(
                """INSERT INTO inventory_movement
                   (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
                    occurred_at, reason_code, source_doc, movement_group_id)
                   VALUES (%s, %s, %s, 'BLOCK', %s, %s, %s, 'DAMAGE', 'DMG-1', 'GRP-1')""",
                (movement_id, SKU_A, WAREHOUSE_SOUTH, bucket, quantity, at(10)),
            )
        assert atp.get_stock(cursor, SKU_A, WAREHOUSE_SOUTH, at(9)) == (10, 0)
        assert atp.get_stock(cursor, SKU_A, WAREHOUSE_SOUTH, at(10)) == (10, 3)
        position = atp.get_positions(cursor, SKU_A, "WEB", at(10))[0]
    connection.close()
    assert position["atp"] == 7


def test_ndc_can_be_a_candidate(orders_db):
    stock(orders_db, (SKU_A, NDC, 5))
    assert only_reservation(place([line(1)]))["location_id"] == NDC
