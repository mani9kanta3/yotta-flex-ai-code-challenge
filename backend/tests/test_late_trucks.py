"""
What happens to paid orders when their truck runs late, against a real PostgreSQL.

The first promise stays in history, another location is tried first, the promise is
updated when it has to be, and the hold is cancelled past the limit. Moving a hold must
never reserve the same stock twice.

In these tests the NDC's whole stock is on one truck to the south warehouse, so an
order can only be promised from that truck.
"""

import threading
from datetime import timedelta

import pytest

from app import atp, late_trucks, orders, reservations, shipping
from app.movements import add_movement
from tests.conftest import NDC, SKU_A, SKU_B, WAREHOUSE_NORTH, WAREHOUSE_SOUTH, add_stock, connect
from tests.test_orders import at, call, fetch, line, place, reserved, run_in_threads, wait_until_blocked_on_a_lock
from tests.test_transfers import SHIPMENT, WAITING_CHECKS, assert_checks_pass, order_on_truck, receive, ship

pytestmark = pytest.mark.database

DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)

# The truck is due at 20:00, so the first promise is 20:00 the next day.
ETA = at(20)
FIRST_PROMISE = ETA + DAY
NOTICE = at(21)

LATE_CHECKS = WAITING_CHECKS + [
    "line holding more than its quantity at any moment", "first promise on the reservation differs from its first event",
    "new promise that is not later than the one before", "finished order that still has an open hold",
    "allocated line whose allocations do not add up to its quantity", "allocation and its reservation disagree",
    "inbound holds above what the shipment carries", "order sale that differs from its allocation",
]


def truck_with_nothing_else_on_hand(qty=4):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, NDC, qty, allow_inbound=False)
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 0)
        add_stock(cursor, SKU_A, WAREHOUSE_NORTH, 0)
    connection.close()
    ship(qty=qty, shipped_at=at(8), eta_at=ETA)


def stock_arrives(location_id, qty, arrived_at):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_movement(cursor, SKU_A, location_id, "RECEIPT", qty, arrived_at, "SUPPLIER_DELIVERY", f"RCV-{arrived_at}")
    connection.close()


def report_late(new_eta_at, t=NOTICE):
    return call(late_trucks.report_late_truck, SHIPMENT, new_eta_at, t)


def only_action(summary):
    actions = [action for action, count in summary.items() if count]
    assert len(actions) == 1, summary
    return actions[0]


def hold_events():
    return fetch("""SELECT reservation_id, from_status, to_status, occurred_at, reason_code, promised_ship_by
                    FROM reservation_event ORDER BY occurred_at, event_id""")


def network_reserved(asked_at):
    return sum(reserved(asked_at, location_id=location) for location in (NDC, WAREHOUSE_SOUTH, WAREHOUSE_NORTH))


# ------------------------------------------------------------ on time

def test_truck_that_is_still_on_time_changes_nothing(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()
    before = hold_events()

    assert only_action(call(late_trucks.handle_late_truck, SHIPMENT, at(10))) == "KEEP"

    assert hold_events() == before


def test_stock_elsewhere_does_not_move_a_hold_whose_truck_is_on_time(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()
    stock_arrives(WAREHOUSE_NORTH, 5, at(10))

    assert only_action(call(late_trucks.handle_late_truck, SHIPMENT, at(11))) == "KEEP"


# ------------------------------------------------------------ updating the promise

def test_new_promise_is_an_event_and_the_first_stays_in_history(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()

    assert only_action(report_late(ETA + DAY)) == "REPROMISE"

    assert fetch("SELECT promised_ship_by, status FROM reservation") == [(FIRST_PROMISE, "CONFIRMED")]
    assert [(row[1], row[2], row[4], row[5]) for row in hold_events()] == [
        (None, "ACTIVE", "ALLOCATED", FIRST_PROMISE),
        ("ACTIVE", "CONFIRMED", "PAYMENT_RECEIVED", None),
        ("CONFIRMED", "CONFIRMED", "INBOUND_LATE_REPROMISED", ETA + DAY + DAY),
    ]
    assert_checks_pass(*LATE_CHECKS)


def test_order_still_holds_its_stock_and_still_waits_after_a_new_promise(orders_db):
    truck_with_nothing_else_on_hand()
    order_id = order_on_truck(qty=3)
    report_late(ETA + DAY)

    assert reserved(at(23)) == 3
    with pytest.raises(shipping.WaitingForInbound):
        call(shipping.ship_order, order_id, at(23))

    receive(ETA + DAY)
    call(shipping.ship_order, order_id, ETA + DAY)
    assert fetch("SELECT status FROM orders") == [("FULFILLED",)]
    assert_checks_pass(*LATE_CHECKS)


def test_running_it_twice_changes_nothing(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()
    report_late(ETA + DAY)
    before = hold_events()

    assert only_action(call(late_trucks.handle_late_truck, SHIPMENT, NOTICE + SECOND)) == "KEEP"

    assert hold_events() == before


# ------------------------------------------------------------ the limit

def test_delay_of_exactly_the_limit_only_updates_the_promise(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()
    assert only_action(report_late(ETA + 3 * DAY)) == "REPROMISE"
    assert fetch("SELECT status FROM orders") == [("CONFIRMED",)]


def test_delay_one_second_past_the_limit_cancels(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()
    assert only_action(report_late(ETA + 3 * DAY + SECOND)) == "CANCEL"


def test_cancelling_releases_the_hold_and_keeps_the_history(orders_db):
    truck_with_nothing_else_on_hand()
    order_id = order_on_truck(qty=3)
    report_late(ETA + 4 * DAY)

    assert reserved(NOTICE - SECOND) == 3
    assert reserved(NOTICE) == 0
    assert fetch("SELECT status FROM reservation") == [("CANCELLED",)]
    assert fetch("SELECT status FROM order_line") == [("CANCELLED",)]
    assert fetch("SELECT status FROM orders") == [("CANCELLED",)]
    assert hold_events()[-1][4] == "INBOUND_LATE_CANCELLED"
    # The first promise is still there.
    assert fetch("SELECT promised_ship_by FROM reservation") == [(FIRST_PROMISE,)]
    with pytest.raises(reservations.OrderStateError, match="CANCELLED"):
        call(shipping.ship_order, order_id, ETA + 4 * DAY)
    assert_checks_pass(*LATE_CHECKS)


def test_cancelled_hold_gives_its_units_back_to_the_truck(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=3)
    report_late(ETA + 4 * DAY)

    connection = connect()
    with connection.cursor() as cursor:
        trucks = atp.get_inbound_shipments(cursor, SKU_A, WAREHOUSE_SOUTH, NOTICE + DAY)
    connection.close()
    assert trucks == []  # due beyond the horizon for now
    connection = connect()
    with connection.cursor() as cursor:
        trucks = atp.get_inbound_shipments(cursor, SKU_A, WAREHOUSE_SOUTH, ETA + 3 * DAY)
    connection.close()
    assert trucks[0]["free_qty"] == 4


def test_small_delays_cannot_stretch_the_limit(orders_db):
    """The limit counts from the first promise, not from the latest one."""
    truck_with_nothing_else_on_hand()
    order_on_truck()

    assert only_action(report_late(ETA + DAY, NOTICE)) == "REPROMISE"
    assert only_action(report_late(ETA + 2 * DAY, NOTICE + DAY)) == "REPROMISE"
    assert only_action(report_late(ETA + 3 * DAY, NOTICE + 2 * DAY)) == "REPROMISE"
    assert only_action(report_late(ETA + 4 * DAY, NOTICE + 3 * DAY)) == "CANCEL"
    assert_checks_pass(*LATE_CHECKS)


def test_other_lines_of_the_order_carry_on_after_one_is_cancelled(orders_db):
    truck_with_nothing_else_on_hand()
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_B, WAREHOUSE_SOUTH, 5)
    connection.close()
    order = place([line(3), line(1, SKU_B)], placed_at=at(9))
    call(reservations.confirm_order, order["order_id"], at(9, 10))

    report_late(ETA + 4 * DAY)

    assert fetch("SELECT status FROM orders") == [("CONFIRMED",)]
    assert fetch("SELECT status FROM order_line ORDER BY line_no") == [("CANCELLED",), ("ALLOCATED",)]

    # With the truck out of the way, the rest of the order can go.
    call(shipping.ship_order, order["order_id"], at(22))
    assert fetch("SELECT status FROM orders") == [("FULFILLED",)]
    assert fetch("SELECT status FROM order_line ORDER BY line_no") == [("CANCELLED",), ("SHIPPED",)]
    assert_checks_pass(*LATE_CHECKS)


# ------------------------------------------------------------ trying another location

def test_hold_moves_to_a_location_that_has_the_stock(orders_db):
    truck_with_nothing_else_on_hand()
    order_id = order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))

    assert only_action(report_late(ETA + DAY)) == "REALLOCATE"

    assert fetch("""SELECT reservation_id, location_id, supply_type, inbound_shipment_id, status, qty
                    FROM reservation ORDER BY reservation_id""") == [
        (f"RES-{order_id}-01", WAREHOUSE_SOUTH, "INBOUND", SHIPMENT, "CANCELLED", 3),
        (f"RES-{order_id}-02", WAREHOUSE_NORTH, "ON_HAND", None, "CONFIRMED", 3),
    ]
    assert fetch("SELECT decision_code, reason_code, chosen_location_id FROM allocation_decision ORDER BY decision_id")[-1] == (
        "REALLOCATED", "INBOUND_LATE_REALLOCATED", WAREHOUSE_NORTH)
    assert fetch("SELECT status FROM order_line") == [("ALLOCATED",)]
    assert fetch("SELECT status FROM orders") == [("CONFIRMED",)]
    assert_checks_pass(*LATE_CHECKS)


def test_moving_a_hold_never_reserves_the_stock_twice(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))
    report_late(ETA + DAY)

    for step in (-1, 0, 1):
        assert network_reserved(NOTICE + step * SECOND) == 3

    assert reserved(NOTICE - SECOND, location_id=WAREHOUSE_SOUTH) == 3
    assert reserved(NOTICE - SECOND, location_id=WAREHOUSE_NORTH) == 0
    assert reserved(NOTICE, location_id=WAREHOUSE_SOUTH) == 0
    assert reserved(NOTICE, location_id=WAREHOUSE_NORTH) == 3


def test_moved_hold_keeps_the_other_stock_free_for_the_next_order(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))
    report_late(ETA + DAY)

    connection = connect()
    with connection.cursor() as cursor:
        north = [p for p in atp.get_positions(cursor, SKU_A, "WEB", at(22)) if p["location_id"] == WAREHOUSE_NORTH][0]
    connection.close()
    # 5 at the north warehouse, less the 3 now held there.
    assert (north["on_hand"], north["reserved"], north["from_on_hand"]) == (5, 3, 2)
    assert place([line(2)], placed_at=at(22))["lines"][0]["allocations"][0]["location_id"] == WAREHOUSE_NORTH


def test_moved_order_ships_from_its_new_location_without_waiting(orders_db):
    truck_with_nothing_else_on_hand()
    order_id = order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))
    report_late(ETA + DAY)

    call(shipping.ship_order, order_id, at(22))

    assert fetch("SELECT location_id, quantity FROM inventory_movement WHERE movement_type = 'SALE'") == [
        (WAREHOUSE_NORTH, -3)]
    assert_checks_pass(*LATE_CHECKS)


def test_truck_arriving_after_the_move_ships_nothing_for_that_order(orders_db):
    truck_with_nothing_else_on_hand()
    order_id = order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))
    report_late(ETA + DAY)
    call(shipping.ship_order, order_id, at(22))

    receive(ETA + DAY)

    assert call(shipping.ship_waiting_orders, SHIPMENT, ETA + DAY) == {"shipped": [], "still_waiting": []}
    assert fetch("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'SALE'") == [(1,)]
    assert reserved(ETA + DAY, location_id=WAREHOUSE_SOUTH) == 0


def test_stock_kept_back_as_safety_cannot_rescue_a_hold(orders_db):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, NDC, 4, allow_inbound=False)
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 0)
        add_stock(cursor, SKU_A, WAREHOUSE_NORTH, 0, safety_stock=3)
    connection.close()
    ship(qty=4, shipped_at=at(8), eta_at=ETA)
    order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))

    # 5 on hand less 3 of safety leaves 2, which doesn't cover 3.
    assert only_action(report_late(ETA + DAY)) == "REPROMISE"


def test_location_is_tried_before_the_limit_cancels(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=3)
    stock_arrives(WAREHOUSE_NORTH, 5, at(20))
    assert only_action(report_late(ETA + 10 * DAY)) == "REALLOCATE"


def test_a_waiting_hold_keeps_looking_for_another_location(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=3)
    assert only_action(report_late(ETA + 2 * DAY)) == "REPROMISE"

    stock_arrives(WAREHOUSE_NORTH, 5, NOTICE + DAY)

    assert call(late_trucks.handle_overdue_trucks, NOTICE + DAY + SECOND) == [SHIPMENT]
    assert fetch("SELECT status, location_id FROM reservation ORDER BY reservation_id") == [
        ("CANCELLED", WAREHOUSE_SOUTH), ("CONFIRMED", WAREHOUSE_NORTH)]
    assert_checks_pass(*LATE_CHECKS)


# ------------------------------------------------------------ two people, the last unit

def test_two_late_holds_cannot_both_move_to_the_last_unit(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=1, placed_at=at(9), paid_at=at(9, 10))
    order_on_truck(qty=1, placed_at=at(9, 1), paid_at=at(9, 11))
    stock_arrives(WAREHOUSE_NORTH, 1, at(20))
    call(late_trucks.delay_transfer, SHIPMENT, ETA + DAY, NOTICE)
    hold_ids = [row[0] for row in fetch("SELECT reservation_id FROM reservation ORDER BY reservation_id")]

    actions = run_in_threads(2, lambda index: call(late_trucks.handle_late_hold, hold_ids[index], NOTICE))

    assert sorted(actions) == ["REALLOCATE", "REPROMISE"]
    assert reserved(NOTICE, location_id=WAREHOUSE_NORTH) == 1
    assert network_reserved(NOTICE) == 2
    assert_checks_pass(*LATE_CHECKS)


def test_oldest_hold_gets_the_only_stock(orders_db):
    truck_with_nothing_else_on_hand()
    older = order_on_truck(qty=1, placed_at=at(9), paid_at=at(9, 10))
    order_on_truck(qty=1, placed_at=at(9, 1), paid_at=at(9, 11))
    stock_arrives(WAREHOUSE_NORTH, 1, at(20))

    report_late(ETA + DAY)

    assert fetch("""SELECT l.order_id FROM reservation r JOIN order_line l USING (order_line_id)
                    WHERE r.location_id = %s AND r.status = 'CONFIRMED'""", (WAREHOUSE_NORTH,)) == [(older,)]


def test_new_order_and_a_moving_hold_cannot_both_take_the_last_unit(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=1)
    stock_arrives(WAREHOUSE_NORTH, 1, at(20))
    call(late_trucks.delay_transfer, SHIPMENT, ETA + 2 * DAY, NOTICE)
    hold_id = fetch("SELECT reservation_id FROM reservation")[0][0]

    def work(index):
        if index == 0:
            return call(late_trucks.handle_late_hold, hold_id, NOTICE)
        return place([line(1)], placed_at=NOTICE)["status"]

    results = run_in_threads(2, work)

    assert sorted(results) in (["REALLOCATE", "REJECTED"], ["ALLOCATED", "REPROMISE"])
    assert reserved(NOTICE, location_id=WAREHOUSE_NORTH) == 1


def test_moving_hold_waits_then_sees_what_a_new_order_took(orders_db):
    """The same race with no luck involved."""
    truck_with_nothing_else_on_hand()
    order_on_truck(qty=1)
    stock_arrives(WAREHOUSE_NORTH, 1, at(20))
    call(late_trucks.delay_transfer, SHIPMENT, ETA + 2 * DAY, NOTICE)
    hold_id = fetch("SELECT reservation_id FROM reservation")[0][0]

    order_connection = connect()
    order_cursor = order_connection.cursor()
    order = orders.place_order_in_transaction(order_cursor, "WEB", "CUST-2", "NORTH", [line(1)], NOTICE)
    assert order["lines"][0]["allocations"][0]["location_id"] == WAREHOUSE_NORTH

    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(
        action=call(late_trucks.handle_late_hold, hold_id, NOTICE)))
    thread.start()
    wait_until_blocked_on_a_lock()
    assert "action" not in outcome

    order_connection.commit()
    order_cursor.close()
    order_connection.close()
    thread.join(timeout=30)

    assert outcome["action"] == "REPROMISE"
    assert reserved(NOTICE, location_id=WAREHOUSE_NORTH) == 1


def test_paying_and_being_handled_at_once_leaves_one_clean_result(orders_db):
    truck_with_nothing_else_on_hand()
    order_id = order_on_truck(qty=1)
    stock_arrives(WAREHOUSE_NORTH, 1, at(20))
    call(late_trucks.delay_transfer, SHIPMENT, ETA + DAY, NOTICE)
    hold_id = fetch("SELECT reservation_id FROM reservation")[0][0]

    def work(index):
        if index == 0:
            return call(late_trucks.handle_late_hold, hold_id, NOTICE)
        return call(reservations.cancel_order, order_id, NOTICE)

    run_in_threads(2, work)

    assert_checks_pass(*LATE_CHECKS)
    assert network_reserved(NOTICE) in (0, 1)


# ------------------------------------------------------------ holds this doesn't touch

def test_unpaid_hold_is_left_to_expire(orders_db):
    truck_with_nothing_else_on_hand()
    place([line(1)], placed_at=at(19, 50))
    call(late_trucks.delay_transfer, SHIPMENT, ETA + DAY, at(19, 55))

    assert call(late_trucks.handle_late_truck, SHIPMENT, at(19, 56)) == {
        "KEEP": 0, "REALLOCATE": 0, "CANCEL": 0, "REPROMISE": 0}
    assert fetch("SELECT status FROM reservation") == [("ACTIVE",)]


def test_hold_paid_after_the_delay_is_caught_by_the_regular_run(orders_db):
    truck_with_nothing_else_on_hand()
    order = place([line(1)], placed_at=at(19))
    call(late_trucks.delay_transfer, SHIPMENT, ETA + DAY, at(19, 10))
    call(reservations.confirm_order, order["order_id"], at(19, 20))

    assert call(late_trucks.handle_overdue_trucks, at(19, 30)) == [SHIPMENT]

    assert hold_events()[-1][4:] == ("INBOUND_LATE_REPROMISED", ETA + 2 * DAY)


def test_received_truck_changes_nothing(orders_db):
    truck_with_nothing_else_on_hand()
    order_on_truck()
    receive(at(19))
    before = hold_events()

    assert only_action(call(late_trucks.handle_late_truck, SHIPMENT, NOTICE)) == "KEEP"

    assert hold_events() == before


def test_truck_that_goes_quiet_is_still_handled(orders_db):
    """No notice at all. Each regular run pushes the promise, until the limit stops it."""
    truck_with_nothing_else_on_hand()
    order_on_truck()

    assert call(late_trucks.handle_overdue_trucks, ETA) == []
    assert call(late_trucks.handle_overdue_trucks, ETA + 2 * DAY) == [SHIPMENT]
    assert hold_events()[-1][4:] == ("INBOUND_LATE_REPROMISED", ETA + 3 * DAY)

    assert call(late_trucks.handle_overdue_trucks, ETA + 3 * DAY + SECOND) == [SHIPMENT]
    assert fetch("SELECT status FROM orders") == [("CANCELLED",)]
    assert_checks_pass(*LATE_CHECKS)


# ------------------------------------------------------------ delivery

def shipped_parcel():
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 5)
    connection.close()
    order = place([line(1)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 5))
    return call(shipping.ship_order, order["order_id"], at(11))["shipment_ids"][0]


def test_delivery_is_an_event_and_moves_no_stock(orders_db):
    parcel = shipped_parcel()
    moved_before = fetch("SELECT COUNT(*) FROM inventory_movement")

    call(shipping.deliver_shipment, parcel, at(15))

    assert fetch("SELECT status FROM shipment") == [("DELIVERED",)]
    assert fetch("SELECT COUNT(*) FROM inventory_movement") == moved_before
    assert_checks_pass("shipment events that do not chain", "shipment status differs from its latest event",
                       "shipment event with an illegal transition")


@pytest.mark.parametrize("delivered_at", [at(10), at(11)])
def test_parcel_cannot_arrive_at_or_before_it_shipped(orders_db, delivered_at):
    parcel = shipped_parcel()
    with pytest.raises(reservations.OrderStateError, match="can't deliver"):
        call(shipping.deliver_shipment, parcel, delivered_at)


def test_parcel_cannot_be_delivered_twice(orders_db):
    parcel = shipped_parcel()
    call(shipping.deliver_shipment, parcel, at(15))
    with pytest.raises(reservations.OrderStateError, match="DELIVERED"):
        call(shipping.deliver_shipment, parcel, at(16))


def test_a_truck_cannot_be_delivered_as_a_parcel(orders_db):
    truck_with_nothing_else_on_hand()
    with pytest.raises(reservations.OrderStateError, match="no customer shipment"):
        call(shipping.deliver_shipment, SHIPMENT, at(15))
