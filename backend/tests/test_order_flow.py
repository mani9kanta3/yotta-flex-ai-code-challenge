"""
The full order flow on generated data in a real PostgreSQL: traceable from order to
movement, the simulator agrees with SQL, and the validator catches broken orders.
"""

from datetime import timedelta

import pytest

from app import atp, config, db, orders, reservations, returns, rules, shipping
from scripts import load_data, validate
from tests.test_transfers import WAITING_CHECKS

pytestmark = pytest.mark.database

SECOND = timedelta(seconds=1)


def violations(cursor, check_name):
    results = validate.run_checks(cursor)
    return next(r["violations"] for r in results if r["check"] == check_name)


def count(cursor, sql, params=None):
    cursor.execute(sql, params)
    return cursor.fetchone()[0]


# ------------------------------------------------------------ traceable

def test_every_line_has_a_decision(cursor):
    assert count(cursor, """SELECT COUNT(*) FROM order_line l
                            LEFT JOIN allocation_decision d USING (order_line_id)
                            WHERE d.decision_id IS NULL""") == 0


def test_every_shipped_line_traces_to_a_movement_at_its_location(cursor):
    shipped = count(cursor, "SELECT COUNT(*) FROM order_line WHERE status = 'SHIPPED'")
    assert shipped > 0
    assert count(cursor, """
        SELECT COUNT(DISTINCT l.order_line_id)
        FROM orders o
        JOIN order_line l USING (order_id)
        JOIN allocation_decision d USING (order_line_id)
        JOIN order_allocation a USING (order_line_id)
        JOIN reservation r USING (reservation_id)
        JOIN shipment s ON s.shipment_id = a.shipment_id AND s.order_id = o.order_id
        JOIN inventory_movement m ON m.allocation_id = a.allocation_id AND m.shipment_id = s.shipment_id
        WHERE l.status = 'SHIPPED' AND r.status = 'CONSUMED' AND m.location_id = a.location_id""") == shipped


def test_every_allocation_row_has_a_reservation(cursor):
    assert count(cursor, "SELECT COUNT(*) FROM order_allocation WHERE reservation_id IS NULL") == 0


def test_rejected_requests_have_no_allocation(cursor):
    assert count(cursor, """SELECT COUNT(*) FROM reservation r
                            JOIN order_allocation a USING (reservation_id)
                            WHERE r.status = 'REJECTED'""") == 0


def test_online_return_traces_back_to_its_order(cursor):
    assert count(cursor, """
        SELECT COUNT(*) FROM inventory_movement r
        JOIN inventory_movement sale ON sale.movement_id = r.related_movement_id
        JOIN order_allocation a ON a.allocation_id = sale.allocation_id
        JOIN order_line l USING (order_line_id)
        WHERE r.movement_type = 'RETURN' AND r.location_id = a.location_id AND r.sku_id = l.sku_id""") > 0


# ------------------------------------------------------------ the simulator against SQL

def test_every_generated_decision_gives_the_same_answer_from_sql(cursor):
    """Two separate calculations of ATP, compared on every order in the dataset."""
    cursor.execute("SELECT COUNT(*) FROM allocation_decision WHERE decision_code <> 'REALLOCATED'")
    decision_count = cursor.fetchone()[0]
    assert decision_count > 400
    results = validate.recheck_decisions(cursor, sample=decision_count)
    assert [r["violations"] for r in results] == [0]


def test_recheck_notices_a_decision_that_is_wrong(cursor):
    cursor.execute("ALTER TABLE allocation_decision DISABLE TRIGGER allocation_decision_append_only")
    cursor.execute("""UPDATE allocation_decision SET chosen_location_id = 'LOC-000001', candidates = '[]'
                      WHERE decision_id IN (SELECT decision_id FROM allocation_decision
                                            WHERE decision_code = 'SINGLE' ORDER BY decision_id LIMIT 3)""")
    cursor.execute("SELECT COUNT(*) FROM allocation_decision")
    assert validate.recheck_decisions(cursor, sample=cursor.fetchone()[0])[0]["violations"] == 3


def test_reserved_from_sql_at_the_end_equals_the_open_holds(cursor):
    cursor.execute("""SELECT sku_id, location_id, SUM(qty) FROM reservation
                      WHERE status IN ('ACTIVE', 'CONFIRMED') GROUP BY 1, 2 ORDER BY 1, 2""")
    rows = cursor.fetchall()
    assert rows
    for sku_id, location_id, held in rows:
        assert atp.get_reserved(cursor, sku_id, location_id, config.AS_OF) == held


def test_python_rule_and_sql_agree_on_reserved_at_past_times(cursor):
    cursor.execute("""SELECT r.sku_id, r.location_id, MIN(r.created_at) FROM reservation r
                      WHERE r.location_id IS NOT NULL AND r.status <> 'REJECTED'
                      GROUP BY 1, 2 ORDER BY COUNT(*) DESC, 1, 2 LIMIT 3""")
    for sku_id, location_id, first_at in cursor.fetchall():
        cursor.execute("""SELECT reservation_id, qty, expires_at FROM reservation
                          WHERE sku_id = %s AND location_id = %s""", (sku_id, location_id))
        python_reservations = []
        for reservation_id, qty, expires_at in cursor.fetchall():
            cursor.execute("""SELECT occurred_at, event_id, to_status FROM reservation_event
                              WHERE reservation_id = %s""", (reservation_id,))
            python_reservations.append({"qty": qty, "expires_at": expires_at, "events": cursor.fetchall()})
        for days in (0, 3, 10, 40, 90):
            asked_at = first_at + timedelta(days=days)
            assert atp.get_reserved(cursor, sku_id, location_id, asked_at) == rules.reserved_at(python_reservations, asked_at)


def test_generated_expired_hold_releases_stock_at_exactly_its_expiry(cursor):
    cursor.execute("""SELECT sku_id, location_id, expires_at, qty FROM reservation
                      WHERE status = 'EXPIRED' ORDER BY reservation_id LIMIT 5""")
    rows = cursor.fetchall()
    assert rows
    for sku_id, location_id, expires_at, qty in rows:
        before = atp.get_reserved(cursor, sku_id, location_id, expires_at - SECOND)
        after = atp.get_reserved(cursor, sku_id, location_id, expires_at)
        assert before - after >= qty


def test_generated_order_on_a_truck_shipped_after_the_truck(cursor):
    assert violations(cursor, "order sale before its inbound truck arrived") == 0
    assert count(cursor, """SELECT COUNT(*) FROM reservation
                            WHERE supply_type = 'INBOUND' AND status = 'CONSUMED'""") > 0


def test_generated_moved_hold_has_its_first_promise_kept_on_the_old_row(cursor):
    assert count(cursor, """SELECT COUNT(*) FROM reservation r
                            JOIN reservation_event e USING (reservation_id)
                            WHERE e.reason_code = 'INBOUND_LATE_REALLOCATED' AND e.to_status = 'CANCELLED'
                              AND r.promised_ship_by IS NOT NULL""") > 0


def test_generated_late_promise_is_later_than_the_first(cursor):
    assert count(cursor, """SELECT COUNT(*) FROM reservation r
                            JOIN reservation_event e USING (reservation_id)
                            WHERE e.reason_code = 'INBOUND_LATE_REPROMISED'
                              AND e.promised_ship_by <= r.promised_ship_by""") == 0


# ------------------------------------------------------------ the validator catches broken orders

def a_hold(cursor, status="CONFIRMED"):
    cursor.execute("""SELECT r.reservation_id, r.order_line_id, r.sku_id, r.location_id, r.qty, r.expires_at
                      FROM reservation r WHERE r.status = %s ORDER BY r.reservation_id LIMIT 1""", (status,))
    return cursor.fetchone()


def test_validator_catches_a_line_holding_stock_twice(cursor):
    reservation_id, line_id, sku_id, location_id, qty, expires_at = a_hold(cursor)
    cursor.execute(
        """INSERT INTO reservation (reservation_id, order_line_id, sku_id, location_id, qty, supply_type,
                                    created_at, expires_at, status)
           VALUES ('RES-TWICE', %s, %s, %s, %s, 'ON_HAND', '2026-09-30 23:00:00', %s, 'ACTIVE')""",
        (line_id, sku_id, location_id, qty, expires_at),
    )
    cursor.execute("""INSERT INTO reservation_event (event_id, reservation_id, to_status, occurred_at, reason_code)
                      VALUES (nextval('event_number'), 'RES-TWICE', 'ACTIVE', '2026-09-30 23:00:00', 'TEST')""")
    assert violations(cursor, "line holding more than its quantity at any moment") == 1


def test_validator_catches_a_sale_before_its_truck(cursor):
    cursor.execute("""SELECT a.allocation_id FROM reservation r JOIN order_allocation a USING (reservation_id)
                      WHERE r.supply_type = 'INBOUND' AND r.status = 'CONSUMED'
                      ORDER BY a.allocation_id LIMIT 1""")
    allocation_id = cursor.fetchone()[0]
    cursor.execute("ALTER TABLE inventory_movement DISABLE TRIGGER inventory_movement_append_only")
    cursor.execute("""UPDATE inventory_movement SET occurred_at = '2026-01-01 00:00:00'
                      WHERE allocation_id = %s""", (allocation_id,))
    assert violations(cursor, "order sale before its inbound truck arrived") == 1


def test_validator_catches_a_sale_that_differs_from_its_allocation(cursor):
    cursor.execute("SELECT allocation_id FROM inventory_movement WHERE allocation_id IS NOT NULL ORDER BY 1 LIMIT 1")
    allocation_id = cursor.fetchone()[0]
    cursor.execute("ALTER TABLE inventory_movement DISABLE TRIGGER inventory_movement_append_only")
    cursor.execute("UPDATE inventory_movement SET quantity = quantity - 1 WHERE allocation_id = %s", (allocation_id,))
    assert violations(cursor, "order sale that differs from its allocation") == 1


def test_validator_catches_an_expiry_dated_when_the_job_ran(cursor):
    cursor.execute("ALTER TABLE reservation_event DISABLE TRIGGER reservation_event_append_only")
    cursor.execute("""UPDATE reservation_event SET occurred_at = occurred_at + INTERVAL '1 hour'
                      WHERE event_id = (SELECT MIN(event_id) FROM reservation_event WHERE to_status = 'EXPIRED')""")
    assert violations(cursor, "expired hold not dated at its expiry") == 1


def test_validator_catches_a_confirmation_after_expiry(cursor):
    cursor.execute("ALTER TABLE reservation_event DISABLE TRIGGER reservation_event_append_only")
    cursor.execute("""UPDATE reservation_event SET occurred_at = occurred_at + INTERVAL '3 hours'
                      WHERE event_id = (SELECT MIN(event_id) FROM reservation_event
                                        WHERE from_status = 'ACTIVE' AND to_status = 'CONFIRMED')""")
    assert violations(cursor, "hold confirmed at or after it expired") == 1


def test_validator_catches_a_first_promise_that_was_overwritten(cursor):
    reservation_id = a_hold(cursor)[0]
    cursor.execute("""UPDATE reservation SET promised_ship_by = promised_ship_by + INTERVAL '1 day'
                      WHERE reservation_id = %s""", (reservation_id,))
    assert violations(cursor, "first promise on the reservation differs from its first event") == 1


def test_validator_catches_a_new_promise_that_is_earlier(cursor):
    cursor.execute("ALTER TABLE reservation_event DISABLE TRIGGER reservation_event_append_only")
    cursor.execute("""UPDATE reservation_event SET promised_ship_by = '2026-01-01 00:00:00'
                      WHERE event_id = (SELECT MIN(event_id) FROM reservation_event
                                        WHERE reason_code = 'INBOUND_LATE_REPROMISED')""")
    assert violations(cursor, "new promise that is not later than the one before") == 1


def test_validator_catches_a_finished_order_with_an_open_hold(cursor):
    reservation_id = a_hold(cursor, "CONSUMED")[0]
    cursor.execute("UPDATE reservation SET status = 'CONFIRMED' WHERE reservation_id = %s", (reservation_id,))
    assert violations(cursor, "finished order that still has an open hold") == 1


def test_validator_catches_holds_above_the_stock_on_the_shelf(cursor):
    reservation_id = a_hold(cursor)[0]
    cursor.execute("UPDATE reservation SET qty = 100000, supply_type = 'ON_HAND', inbound_shipment_id = NULL "
                   "WHERE reservation_id = %s", (reservation_id,))
    assert violations(cursor, "open holds above the stock on the shelf at the end") == 1


def test_validator_catches_a_stock_allocation_after_a_sku_stopped(cursor):
    cursor.execute("""SELECT a.allocation_id FROM order_allocation a ORDER BY allocation_id LIMIT 1""")
    allocation_id = cursor.fetchone()[0]
    cursor.execute("""UPDATE sku SET effective_to = '2026-01-02', status = 'DISCONTINUED'
                      WHERE sku_id = (SELECT l.sku_id FROM order_allocation a JOIN order_line l USING (order_line_id)
                                      WHERE a.allocation_id = %s)""", (allocation_id,))
    assert violations(cursor, "stock allocated after a discontinued SKU stopped") > 0


def test_validator_catches_a_split_with_one_hold(cursor):
    cursor.execute("""SELECT r.reservation_id FROM allocation_decision d
                      JOIN reservation r ON r.order_line_id = d.order_line_id AND r.created_at = d.decided_at
                      WHERE d.decision_code = 'SPLIT' ORDER BY r.reservation_id LIMIT 1""")
    reservation_id = cursor.fetchone()[0]
    cursor.execute("UPDATE reservation SET created_at = created_at - INTERVAL '1 second' WHERE reservation_id = %s",
                   (reservation_id,))
    assert violations(cursor, "split decision without two holds") == 1


@pytest.mark.parametrize("name", [name for name, _sql in validate.GENERATED_ORDER_ACTIVITY])
def test_generated_activity_fails_when_a_case_is_missing(monkeypatch, cursor, name):
    """Point the check at something with no rows, which is what a missing case looks like."""
    replaced = [(n, "SELECT 0") if n == name else (n, sql) for n, sql in validate.GENERATED_ORDER_ACTIVITY]
    monkeypatch.setattr(validate, "GENERATED_ORDER_ACTIVITY", replaced)
    failed = [r["check"] for r in validate.check_generated_activity(cursor) if not r["passed"]]
    assert failed == [f"generated data includes {name}"]


# ------------------------------------------------------------ the live flow, on generated data

@pytest.fixture
def writable_data(loaded_database):
    """These tests commit, so the generated data is loaded again afterwards."""
    yield
    load_data.load(loaded_database)


def sku_with_stock_at_a_warehouse():
    connection = db.get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT r.sku_id FROM inventory_balance b
                              JOIN replenishment_rule r USING (sku_id, location_id)
                              JOIN location l USING (location_id)
                              WHERE b.snapshot_type = 'CLOSING' AND l.location_type = 'WAREHOUSE'
                                AND b.on_hand - b.blocked > r.safety_stock + 10
                                AND (r.effective_to IS NULL OR r.effective_to > %s::date)
                              ORDER BY r.sku_id LIMIT 1""", (config.AS_OF,))
            return cursor.fetchone()[0]
    finally:
        connection.close()


def test_live_order_runs_from_placement_to_return_on_generated_data(writable_data):
    """After a load, a live order has to number itself past all the generated rows."""
    sku_id = sku_with_stock_at_a_warehouse()
    t = config.AS_OF
    connection = db.get_connection()
    try:
        order = orders.place_order(connection, "WEB", "CUST-LIVE", "SOUTH", [{"sku_id": sku_id, "qty": 2}], t)
        assert order["status"] == "ALLOCATED"
        order_id = order["order_id"]

        reservations.confirm_order(connection, order_id, t + SECOND)
        shipped = shipping.ship_order(connection, order_id, t + 2 * SECOND)
        shipping.deliver_shipment(connection, shipped["shipment_ids"][0], t + 3 * SECOND)

        with connection.cursor() as cursor:
            cursor.execute("""SELECT m.movement_id FROM inventory_movement m
                              JOIN order_allocation a USING (allocation_id)
                              JOIN order_line l USING (order_line_id) WHERE l.order_id = %s""", (order_id,))
            sale_id = cursor.fetchone()[0]
        connection.commit()
        returns.record_return(connection, sale_id, 1, "GOOD", t + 4 * SECOND)

        with connection.cursor() as cursor:
            results = {r["check"]: r["violations"] for r in validate.run_checks(cursor)}
        connection.commit()
    finally:
        connection.close()

    assert {name: results[name] for name in WAITING_CHECKS if results[name] != 0} == {}
    assert results["line holding more than its quantity at any moment"] == 0


def test_live_order_can_expire_on_generated_data(writable_data):
    sku_id = sku_with_stock_at_a_warehouse()
    t = config.AS_OF
    connection = db.get_connection()
    try:
        order = orders.place_order(connection, "APP", "CUST-LIVE", "SOUTH", [{"sku_id": sku_id, "qty": 1}], t)
        summary = reservations.expire_due(connection, t + timedelta(minutes=30))
    finally:
        connection.close()
    assert order["status"] == "ALLOCATED"
    assert summary["orders_expired"] >= 1
