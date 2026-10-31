"""
Transfers against a real PostgreSQL: stock leaves the source, stays in transit, and
reaches the destination without being counted twice. Then orders that hold stock on
a truck, which have to wait for it.

These commit for real, because a lock only means something between transactions.
"""

import threading
from datetime import datetime, timedelta

import pytest

from app import atp, orders, reservations, shipping, transfers
from scripts import validate
from tests.conftest import NDC, SKU_A, STORE_SOUTH, WAREHOUSE_NORTH, WAREHOUSE_SOUTH, add_stock, connect
from tests.test_orders import (at, call, fetch, line, only_reservation, place, promisable, reserved,
                               run_in_threads, stock, wait_until_blocked_on_a_lock)

pytestmark = pytest.mark.database

SHIPMENT = "SHP-2026-000001"


def ship(qty=4, shipped_at=at(8), eta_at=at(20), source=NDC, destination=WAREHOUSE_SOUTH, sku_id=SKU_A):
    return call(transfers.ship_transfer, sku_id, source, destination, qty, shipped_at, eta_at)


def receive(received_at=at(18), shipment_id=SHIPMENT):
    return call(transfers.receive_transfer, shipment_id, received_at)


def snapshot(asked_at, sku_id=SKU_A):
    """Every number that could count a truck, at one moment."""
    connection = connect()
    try:
        with connection.cursor() as cursor:
            source = atp.get_stock(cursor, sku_id, NDC, asked_at)[0]
            destination = atp.get_stock(cursor, sku_id, WAREHOUSE_SOUTH, asked_at)[0]
            positions = atp.get_positions(cursor, sku_id, "WEB", asked_at)
            inbound = [p["inbound"] for p in positions if p["location_id"] == WAREHOUSE_SOUTH][0]
            return {
                "source_on_hand": source,
                "destination_on_hand": destination,
                "destination_inbound": inbound,
                "in_transit": atp.get_in_transit(cursor, sku_id, asked_at),
                "network_on_hand": atp.get_network_on_hand(cursor, sku_id, asked_at),
                "network_promisable": atp.network_promisable(positions),
            }
    finally:
        connection.close()


def add_ndc_and_destination(ndc_stock=10, destination_stock=0):
    connection = connect()
    with connection, connection.cursor() as cursor:
        # The NDC never counts inbound, so a truck can't be promised at both ends.
        add_stock(cursor, SKU_A, NDC, ndc_stock, allow_inbound=False)
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, destination_stock)
    connection.close()


def assert_checks_pass(*names):
    connection = connect()
    try:
        with connection.cursor() as cursor:
            results = {r["check"]: r["violations"] for r in validate.run_checks(cursor)}
    finally:
        connection.close()
    assert {name: results[name] for name in names if results[name] != 0} == {}


TRANSFER_CHECKS = [
    "shipment with no event", "shipment event with an illegal transition", "shipment events that do not chain",
    "shipment status differs from its latest event", "shipment_id format",
    "transfer without exactly one matching TRANSFER_OUT",
    "received transfer without exactly one matching TRANSFER_IN",
    "transfer still in transit with a TRANSFER_IN already posted",
    "transfer movement that does not belong to its shipment", "transfer received at or before it shipped",
    "TRANSFER_IN time differs from the RECEIVED event",
    "transfer units that left, arrived or stayed on the road do not add up",
]

WAITING_CHECKS = TRANSFER_CHECKS + [
    "order sale before its inbound truck arrived", "consumed hold without a sale of the same quantity",
    "order status differs from its latest event", "order line status differs from its latest event",
    "reservation status differs from its latest event", "reservation events that do not chain",
    "reservation event with an illegal transition", "order event with an illegal transition",
    "order line event with an illegal transition",
]


# ------------------------------------------------------------ one truck, start to finish

def test_stock_leaves_stays_on_the_road_and_arrives_counted_once(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8), eta_at=at(20))
    receive(at(18))

    before = snapshot(at(7, 59, 59))
    assert before == {"source_on_hand": 10, "destination_on_hand": 0, "destination_inbound": 0,
                      "in_transit": 0, "network_on_hand": 10, "network_promisable": 10}

    # The second it leaves: gone from the source, not yet at the destination.
    left = snapshot(at(8))
    assert left == {"source_on_hand": 6, "destination_on_hand": 0, "destination_inbound": 4,
                    "in_transit": 4, "network_on_hand": 6, "network_promisable": 10}

    last_second_on_road = snapshot(at(17, 59, 59))
    assert last_second_on_road == left

    # The second it arrives: on the shelf and no longer inbound, never both.
    arrived = snapshot(at(18))
    assert arrived == {"source_on_hand": 6, "destination_on_hand": 4, "destination_inbound": 0,
                       "in_transit": 0, "network_on_hand": 10, "network_promisable": 10}


def test_total_stock_is_the_same_at_every_second_around_each_change(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8), eta_at=at(20))
    receive(at(18))

    for change in (at(8), at(18)):
        for step in (-1, 0, 1):
            numbers = snapshot(change + timedelta(seconds=step))
            assert numbers["network_on_hand"] + numbers["in_transit"] == 10
            assert numbers["source_on_hand"] + numbers["destination_on_hand"] == numbers["network_on_hand"]
            assert numbers["network_promisable"] == 10


def test_transfer_writes_exactly_one_row_at_each_end(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8))
    assert fetch("""SELECT movement_type, location_id, stock_bucket, quantity, occurred_at, shipment_id
                    FROM inventory_movement ORDER BY movement_id""") == [
        ("TRANSFER_OUT", NDC, "SELLABLE", -4, at(8), SHIPMENT)]

    receive(at(18))
    assert fetch("""SELECT movement_type, location_id, stock_bucket, quantity, occurred_at, shipment_id
                    FROM inventory_movement ORDER BY movement_id""") == [
        ("TRANSFER_OUT", NDC, "SELLABLE", -4, at(8), SHIPMENT),
        ("TRANSFER_IN", WAREHOUSE_SOUTH, "SELLABLE", 4, at(18), SHIPMENT)]
    assert_checks_pass(*TRANSFER_CHECKS)


def test_shipment_history_comes_from_its_events(orders_db):
    add_ndc_and_destination()
    ship(shipped_at=at(8))
    assert fetch("SELECT status FROM shipment") == [("IN_TRANSIT",)]
    receive(at(18))
    assert fetch("SELECT status FROM shipment") == [("RECEIVED",)]
    assert fetch("""SELECT from_status, to_status, occurred_at FROM shipment_event
                    ORDER BY occurred_at, event_id""") == [
        (None, "CREATED", at(8)), ("CREATED", "IN_TRANSIT", at(8)), ("IN_TRANSIT", "RECEIVED", at(18))]


def test_truck_due_beyond_the_horizon_is_on_the_road_but_not_promisable(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8), eta_at=at(8) + timedelta(days=2))

    numbers = snapshot(at(9))
    assert numbers["in_transit"] == 4
    assert numbers["destination_inbound"] == 0
    assert numbers["network_on_hand"] + numbers["in_transit"] == 10
    assert numbers["network_promisable"] == 6


def test_marketplace_never_counts_the_truck_at_either_end(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8))
    assert promisable(at(9), channel="MARKETPLACE") == 6
    receive(at(18))
    assert promisable(at(18), channel="MARKETPLACE") == 10


# ------------------------------------------------------------ what a source can't ship

def test_cannot_ship_more_than_the_source_has(orders_db):
    add_ndc_and_destination(ndc_stock=3)
    with pytest.raises(transfers.TransferError, match="has 3 free"):
        ship(qty=4)
    assert fetch("SELECT COUNT(*) FROM shipment") == [(0,)]
    assert fetch("SELECT COUNT(*) FROM inventory_movement") == [(0,)]


def test_can_ship_exactly_what_the_source_has(orders_db):
    add_ndc_and_destination(ndc_stock=3)
    ship(qty=3)
    assert snapshot(at(9))["source_on_hand"] == 0


def test_cannot_ship_stock_held_for_an_order(orders_db):
    add_ndc_and_destination(ndc_stock=3)
    order = place([line(2)], placed_at=at(7))
    call(reservations.confirm_order, order["order_id"], at(7, 10))

    with pytest.raises(transfers.TransferError, match="has 1 free"):
        ship(qty=2, shipped_at=at(8))
    ship(qty=1, shipped_at=at(8))


def test_cannot_ship_damaged_stock(orders_db):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, NDC, 5, blocked=4)
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 0)
    connection.close()
    with pytest.raises(transfers.TransferError, match="has 1 free"):
        ship(qty=2)


@pytest.mark.parametrize("qty", [0, -1])
def test_zero_or_negative_transfer_is_refused(orders_db, qty):
    add_ndc_and_destination()
    with pytest.raises(transfers.TransferError, match="above zero"):
        ship(qty=qty)
    assert fetch("SELECT COUNT(*) FROM shipment") == [(0,)]


def test_transfer_to_the_same_location_is_refused(orders_db):
    add_ndc_and_destination()
    with pytest.raises(transfers.TransferError, match="two different"):
        ship(destination=NDC)


def test_transfer_to_a_location_that_does_not_stock_the_sku_is_refused(orders_db):
    add_ndc_and_destination()
    with pytest.raises(transfers.TransferError, match="doesn't stock"):
        ship(destination=STORE_SOUTH)


def test_eta_before_shipping_is_refused(orders_db):
    add_ndc_and_destination()
    with pytest.raises(transfers.TransferError, match="ETA"):
        ship(shipped_at=at(8), eta_at=at(8))


# ------------------------------------------------------------ what can't be received

def test_receiving_twice_is_refused_and_adds_nothing(orders_db):
    add_ndc_and_destination()
    ship()
    receive(at(18))
    with pytest.raises(transfers.TransferError, match="RECEIVED"):
        receive(at(19))
    assert snapshot(at(20))["destination_on_hand"] == 4
    assert fetch("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'TRANSFER_IN'") == [(1,)]


@pytest.mark.parametrize("received_at", [at(7), at(8)])
def test_cannot_arrive_at_or_before_shipping(orders_db, received_at):
    """TRANSFER_DOUBLE_COUNTED in the brief. The live path must not be able to make it."""
    add_ndc_and_destination()
    ship(shipped_at=at(8))
    with pytest.raises(transfers.TransferError):
        receive(received_at)
    assert fetch("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'TRANSFER_IN'") == [(0,)]


def test_can_arrive_one_second_after_shipping(orders_db):
    add_ndc_and_destination()
    ship(shipped_at=at(8))
    receive(at(8, 0, 1))
    assert_checks_pass(*TRANSFER_CHECKS)


def test_receiving_an_unknown_shipment_is_refused(orders_db):
    with pytest.raises(transfers.TransferError, match="no transfer"):
        receive(at(18), "SHP-2026-999999")


def test_a_customer_shipment_cannot_be_received_as_a_transfer(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    order = place([line(1)], placed_at=at(10))
    call(reservations.confirm_order, order["order_id"], at(10, 5))
    shipment_id = call(shipping.ship_order, order["order_id"], at(10, 10))["shipment_ids"][0]
    with pytest.raises(transfers.TransferError, match="no transfer"):
        receive(at(18), shipment_id)


def test_arrival_stamped_behind_other_activity_moves_forward(orders_db):
    """Otherwise the arrival would rewrite what an earlier answer said was on the shelf."""
    add_ndc_and_destination(destination_stock=5)
    ship(shipped_at=at(8))
    place([line(1)], placed_at=at(17))

    assert receive(at(16))["received_at"] == at(17)
    assert_checks_pass(*TRANSFER_CHECKS)


# ------------------------------------------------------------ late trucks

def test_late_truck_stops_counting_then_counts_again_with_its_new_eta(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8), eta_at=at(12))

    assert snapshot(at(12))["destination_inbound"] == 4
    assert snapshot(at(12, 0, 1))["destination_inbound"] == 0

    call(transfers.delay_transfer, SHIPMENT, at(20), at(13))

    assert snapshot(at(12, 30))["destination_inbound"] == 0
    assert snapshot(at(13))["destination_inbound"] == 4


def test_late_truck_stock_is_never_lost_from_the_total(orders_db):
    add_ndc_and_destination()
    ship(qty=4, shipped_at=at(8), eta_at=at(12))
    for asked_at in (at(11), at(12, 0, 1), at(23)):
        numbers = snapshot(asked_at)
        assert numbers["in_transit"] == 4
        assert numbers["network_on_hand"] + numbers["in_transit"] == 10


def test_delay_keeps_the_first_eta_and_writes_no_movement(orders_db):
    add_ndc_and_destination()
    ship(shipped_at=at(8), eta_at=at(12))
    call(transfers.delay_transfer, SHIPMENT, at(20), at(13))

    assert fetch("SELECT original_eta_at, status FROM shipment") == [(at(12), "IN_TRANSIT")]
    assert fetch("""SELECT reason_code, eta_at FROM shipment_event
                    WHERE from_status = 'IN_TRANSIT' AND to_status = 'IN_TRANSIT'""") == [("ETA_MISSED", at(20))]
    assert fetch("SELECT COUNT(*) FROM inventory_movement") == [(1,)]
    assert_checks_pass(*TRANSFER_CHECKS)


def test_a_received_truck_cannot_be_delayed(orders_db):
    add_ndc_and_destination()
    ship(shipped_at=at(8))
    receive(at(18))
    with pytest.raises(transfers.TransferError, match="RECEIVED"):
        call(transfers.delay_transfer, SHIPMENT, at(22), at(19))


def test_cannot_receive_before_the_last_recorded_event(orders_db):
    add_ndc_and_destination()
    ship(shipped_at=at(8), eta_at=at(12))
    call(transfers.delay_transfer, SHIPMENT, at(20), at(13))
    with pytest.raises(transfers.TransferError):
        receive(at(12, 30))


# ------------------------------------------------------------ races

def test_two_transfers_for_the_last_unit_exactly_one_ships(orders_db):
    add_ndc_and_destination(ndc_stock=1)

    def work(_index):
        try:
            ship(qty=1)
            return "SHIPPED"
        except transfers.TransferError:
            return "REFUSED"

    results = run_in_threads(2, work)

    assert sorted(results) == ["REFUSED", "SHIPPED"]
    assert snapshot(at(9))["source_on_hand"] == 0


def test_order_and_transfer_for_the_last_unit_exactly_one_gets_it(orders_db):
    add_ndc_and_destination(ndc_stock=1)
    # Due beyond the horizon, so once on the truck the unit can't be promised to the order.
    eta_at = at(8) + timedelta(days=2)

    def work(index):
        if index == 0:
            return place([line(1)], placed_at=at(8))["status"]
        try:
            ship(qty=1, shipped_at=at(8), eta_at=eta_at)
            return "SHIPPED"
        except transfers.TransferError:
            return "REFUSED"

    results = run_in_threads(2, work)

    assert sorted(results) in (["ALLOCATED", "REFUSED"], ["REJECTED", "SHIPPED"])


def test_transfer_waits_for_an_order_then_sees_what_it_reserved(orders_db):
    """The same race with no luck involved."""
    add_ndc_and_destination(ndc_stock=1)

    order_connection = connect()
    order_cursor = order_connection.cursor()
    order = orders.place_order_in_transaction(order_cursor, "WEB", "CUST-1", "SOUTH", [line(1)], at(8))
    assert order["status"] == "ALLOCATED"

    outcome = {}

    def second():
        try:
            ship(qty=1, shipped_at=at(8))
            outcome["result"] = "SHIPPED"
        except transfers.TransferError:
            outcome["result"] = "REFUSED"

    thread = threading.Thread(target=second)
    thread.start()
    wait_until_blocked_on_a_lock()
    assert "result" not in outcome

    order_connection.commit()
    order_cursor.close()
    order_connection.close()
    thread.join(timeout=30)

    assert outcome["result"] == "REFUSED"
    assert fetch("SELECT COUNT(*) FROM shipment") == [(0,)]


def test_two_people_receiving_the_same_truck_add_the_stock_once(orders_db):
    add_ndc_and_destination()
    ship(qty=4)

    def work(_index):
        try:
            receive(at(18))
            return "RECEIVED"
        except transfers.TransferError:
            return "REFUSED"

    results = run_in_threads(10, work)

    assert results.count("RECEIVED") == 1
    assert results.count("REFUSED") == 9
    assert snapshot(at(19))["destination_on_hand"] == 4
    assert_checks_pass(*TRANSFER_CHECKS)


def test_lock_timeout_says_busy_and_ships_nothing(orders_db):
    add_ndc_and_destination()
    holder = connect()
    with holder.cursor() as cursor:
        cursor.execute("SELECT 1 FROM sku_location FOR UPDATE")
        with pytest.raises(orders.StockBusy):
            call(transfers.ship_transfer, SKU_A, NDC, WAREHOUSE_SOUTH, 1, at(8), at(20), lock_timeout_ms=100)
    holder.rollback()
    holder.close()
    assert fetch("SELECT COUNT(*) FROM shipment") == [(0,)]


# ------------------------------------------------------------ orders must wait for arrival

def order_on_truck(qty=3, placed_at=at(9), paid_at=at(9, 10)):
    """A paid order whose stock is on the truck."""
    order = place([line(qty)], placed_at=placed_at)
    assert only_reservation(order)["supply_type"] == "INBOUND"
    call(reservations.confirm_order, order["order_id"], paid_at)
    return order["order_id"]


def test_order_on_a_truck_cannot_ship_before_arrival(orders_db):
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8))
    order_id = order_on_truck()

    with pytest.raises(shipping.WaitingForInbound, match=SHIPMENT):
        call(shipping.ship_order, order_id, at(12))

    # Nothing moved: no sale, the hold is still held, the order is still paid.
    assert fetch("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'SALE'") == [(0,)]
    assert fetch("SELECT status FROM reservation") == [("CONFIRMED",)]
    assert fetch("SELECT status FROM orders") == [("CONFIRMED",)]
    assert reserved(at(12)) == 3


def test_order_ships_at_the_exact_second_of_arrival_and_not_one_before(orders_db):
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8))
    order_id = order_on_truck()
    receive(at(18))

    # Received already, but the shipment is stamped before that. It must still wait.
    with pytest.raises(shipping.WaitingForInbound):
        call(shipping.ship_order, order_id, at(17, 59, 59))

    call(shipping.ship_order, order_id, at(18))

    assert fetch("SELECT status FROM orders") == [("FULFILLED",)]
    assert_checks_pass(*WAITING_CHECKS)


def test_sale_never_posts_before_the_stock_arrives(orders_db):
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8))
    order_id = order_on_truck()
    receive(at(18))
    call(shipping.ship_order, order_id, at(19))

    rows = fetch("""SELECT movement_type, location_id, quantity, occurred_at FROM inventory_movement
                    ORDER BY occurred_at, movement_id""")
    assert rows == [
        ("TRANSFER_OUT", NDC, -4, at(8)),
        ("TRANSFER_IN", WAREHOUSE_SOUTH, 4, at(18)),
        ("SALE", WAREHOUSE_SOUTH, -3, at(19)),
    ]


def test_late_truck_keeps_the_order_waiting_past_its_first_eta(orders_db):
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8), eta_at=at(12))
    order_id = order_on_truck()
    call(transfers.delay_transfer, SHIPMENT, at(20), at(13))

    with pytest.raises(shipping.WaitingForInbound):
        call(shipping.ship_order, order_id, at(14))
    assert reserved(at(14)) == 3

    receive(at(19))
    call(shipping.ship_order, order_id, at(19))
    assert fetch("SELECT status FROM orders") == [("FULFILLED",)]


def test_hold_keeps_arrived_stock_away_from_the_next_order(orders_db):
    """After arrival the stock is on the shelf, but 3 of the 4 units are already promised."""
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8))
    order_on_truck(qty=3)
    receive(at(18))

    connection = connect()
    with connection.cursor() as cursor:
        destination = [p for p in atp.get_positions(cursor, SKU_A, "WEB", at(18))
                       if p["location_id"] == WAREHOUSE_SOUTH][0]
    connection.close()
    assert (destination["on_hand"], destination["reserved"], destination["inbound"], destination["atp"]) == (4, 3, 0, 1)


def test_arrival_does_not_change_atp_for_an_order_waiting_on_the_truck(orders_db):
    """Before: inbound 4, reserved 3. After: on hand 4, reserved 3. ATP is 1 both times."""
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8))
    order_on_truck(qty=3)
    receive(at(18))

    def destination_atp(asked_at):
        connection = connect()
        with connection.cursor() as cursor:
            positions = atp.get_positions(cursor, SKU_A, "WEB", asked_at)
        connection.close()
        return [p["atp"] for p in positions if p["location_id"] == WAREHOUSE_SOUTH][0]

    assert destination_atp(at(17, 59, 59)) == 1
    assert destination_atp(at(18)) == 1


def test_cancelled_order_stops_waiting_and_leaves_the_stock_free(orders_db):
    add_ndc_and_destination(ndc_stock=4)
    ship(qty=4, shipped_at=at(8))
    order_id = order_on_truck(qty=3)
    call(reservations.cancel_order, order_id, at(10))
    receive(at(18))

    with pytest.raises(reservations.OrderStateError, match="CANCELLED"):
        call(shipping.ship_order, order_id, at(19))
    assert reserved(at(19)) == 0


def test_arrival_ships_paid_orders_oldest_first_and_leaves_unpaid_ones(orders_db):
    add_ndc_and_destination(ndc_stock=6)
    ship(qty=6, shipped_at=at(8))
    older = order_on_truck(qty=2, placed_at=at(9), paid_at=at(9, 10))
    newer = order_on_truck(qty=2, placed_at=at(9, 5), paid_at=at(9, 15))
    unpaid = place([line(1)], placed_at=at(17, 50))["order_id"]
    receive(at(18))

    summary = call(shipping.ship_waiting_orders, SHIPMENT, at(18))

    assert summary == {"shipped": [older, newer], "still_waiting": []}
    assert fetch("""SELECT l.order_id FROM inventory_movement m
                    JOIN order_allocation a USING (allocation_id)
                    JOIN order_line l USING (order_line_id)
                    WHERE m.movement_type = 'SALE' ORDER BY m.movement_id""") == [(older,), (newer,)]
    assert fetch("SELECT status FROM orders WHERE order_id = %s", (unpaid,)) == [("ALLOCATED",)]
    assert_checks_pass(*WAITING_CHECKS)


def test_order_waiting_on_two_trucks_ships_only_after_both(orders_db):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, NDC, 10, allow_inbound=False)
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 0)
    connection.close()
    ship(qty=2, shipped_at=at(8), eta_at=at(20))
    ship(qty=2, shipped_at=at(8, 0, 1), eta_at=at(21))
    connection = connect()
    with connection, connection.cursor() as cursor:
        cursor.execute("UPDATE channel_location SET effective_to = '2025-06-02' WHERE location_id = %s", (NDC,))
    connection.close()

    order = place([line(2), line(2)], placed_at=at(9))
    trucks = [item["allocations"][0]["inbound_shipment_id"] for item in order["lines"]]
    assert sorted(trucks) == ["SHP-2026-000001", "SHP-2026-000002"]
    call(reservations.confirm_order, order["order_id"], at(9, 10))

    receive(at(18), "SHP-2026-000001")
    assert call(shipping.ship_waiting_orders, "SHP-2026-000001", at(18)) == {
        "shipped": [], "still_waiting": [order["order_id"]]}

    receive(at(19), "SHP-2026-000002")
    assert call(shipping.ship_waiting_orders, "SHP-2026-000002", at(19)) == {
        "shipped": [order["order_id"]], "still_waiting": []}


# ------------------------------------------------------------ shipping stock that is on hand

def paid_order(*lines, placed_at=at(10)):
    order = place(list(lines), placed_at=placed_at)
    call(reservations.confirm_order, order["order_id"], placed_at + timedelta(minutes=5))
    return order


def test_shipping_turns_the_hold_into_a_sale_without_changing_atp(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = paid_order(line(3))
    assert promisable(at(10, 59, 59)) == 7

    call(shipping.ship_order, order["order_id"], at(11))

    assert promisable(at(11)) == 7
    assert reserved(at(11)) == 0
    assert fetch("SELECT status FROM reservation") == [("CONSUMED",)]
    assert fetch("SELECT status FROM order_line") == [("SHIPPED",)]
    assert fetch("SELECT status FROM orders") == [("FULFILLED",)]


def test_order_can_be_traced_from_line_to_movement(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = paid_order(line(3))
    result = call(shipping.ship_order, order["order_id"], at(11))

    rows = fetch("""SELECT l.order_line_id, a.allocation_id, a.shipment_id, m.movement_type, m.quantity, m.shipment_id
                    FROM order_line l
                    JOIN order_allocation a USING (order_line_id)
                    JOIN inventory_movement m USING (allocation_id)""")
    assert len(rows) == 1
    assert rows[0][2] == rows[0][5] == result["shipment_ids"][0]
    assert rows[0][3:5] == ("SALE", -3)
    assert_checks_pass(*WAITING_CHECKS)


def test_split_order_makes_one_shipment_per_location(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 3), (SKU_A, WAREHOUSE_NORTH, 4))
    order = paid_order(line(5))

    result = call(shipping.ship_order, order["order_id"], at(11))

    assert len(result["shipment_ids"]) == 2
    assert fetch("""SELECT location_id, quantity FROM inventory_movement
                    WHERE movement_type = 'SALE' ORDER BY location_id""") == [
        (WAREHOUSE_SOUTH, -3), (WAREHOUSE_NORTH, -2)]
    assert_checks_pass(*WAITING_CHECKS)


def test_unpaid_order_cannot_ship(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = place([line(1)], placed_at=at(10))
    with pytest.raises(reservations.OrderStateError, match="ALLOCATED"):
        call(shipping.ship_order, order["order_id"], at(10, 5))
    assert fetch("SELECT COUNT(*) FROM inventory_movement") == [(0,)]


def test_order_cannot_ship_twice(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 10))
    order = paid_order(line(1))
    call(shipping.ship_order, order["order_id"], at(11))
    with pytest.raises(reservations.OrderStateError, match="FULFILLED"):
        call(shipping.ship_order, order["order_id"], at(12))
    assert fetch("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'SALE'") == [(1,)]


def test_short_pick_refuses_the_whole_shipment(orders_db):
    stock(orders_db, (SKU_A, WAREHOUSE_SOUTH, 5))
    order = paid_order(line(3))
    connection = connect()
    with connection, connection.cursor() as cursor:
        for movement_id, bucket, quantity in (("MOV-2026-00000901", "SELLABLE", -4),
                                              ("MOV-2026-00000902", "BLOCKED", 4)):
            cursor.execute(
                """INSERT INTO inventory_movement
                   (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
                    occurred_at, reason_code, source_doc, movement_group_id)
                   VALUES (%s, %s, %s, 'BLOCK', %s, %s, %s, 'DAMAGE', 'DMG-1', 'GRP-1')""",
                (movement_id, SKU_A, WAREHOUSE_SOUTH, bucket, quantity, at(10, 30)),
            )
    connection.close()

    with pytest.raises(reservations.OrderStateError, match="short pick"):
        call(shipping.ship_order, order["order_id"], at(11))

    assert fetch("SELECT COUNT(*) FROM inventory_movement WHERE movement_type = 'SALE'") == [(0,)]
    assert fetch("SELECT status FROM orders") == [("CONFIRMED",)]
    assert fetch("SELECT COUNT(*) FROM shipment") == [(0,)]
