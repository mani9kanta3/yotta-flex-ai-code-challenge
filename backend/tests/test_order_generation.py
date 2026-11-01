"""
Generated orders, with no database. Each small test sets up one customer and follows
one path. The rest replay the whole generated ledger and check it from the rows alone.
"""

import random
from datetime import date, datetime, timedelta

import pytest

from app import config, rules
from generator import export, master, order_flow, settings, simulate
from tests.conftest import TEST_SEED, SMALL_MOVEMENTS, SMALL_ORDERS, small_scale

STORE, WAREHOUSE, THE_NDC = 0, 1, 2
PLACED = datetime(2026, 3, 1, 10, 0, 0)
DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)


def generated(movements=None, orders=None, seed=TEST_SEED):
    """The clean baseline on its own, the same way scripts/generate.py starts."""
    scale = settings.clean_scale(small_scale())
    movements = movements or scale["movements"]
    orders = orders or scale["orders"]
    rng = random.Random(seed)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    channel_locations = master.build_channel_locations(locations)
    pairs = master.build_pairs(rng, scale, skus, locations)
    master.build_rules(pairs)
    master.build_opening_stock(rng, pairs)
    return simulate.simulate(rng, pairs, movements, channel_locations, orders)


@pytest.fixture(scope="module")
def ledger():
    return generated()


def small_world(warehouse_stock=100, ndc_stock=100, store_stock=0, target_rows=200):
    """One SKU at a store, its warehouse and the NDC, with stock we choose."""
    scale = small_scale()
    rng = random.Random(TEST_SEED)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    sku = next(s for s in skus if s["status"] == "ACTIVE" and s["effective_from"] < date(2026, 1, 1))
    store = next(l for l in locations if l["location_type"] == "STORE")
    store["opened_on"] = date(2021, 1, 1)
    # A store that doesn't ship online would never be a candidate.
    store["ships_online"] = True
    warehouse = next(l for l in locations if l["location_id"] == store["parent_location_id"])
    ndc = next(l for l in locations if l["location_type"] == "NDC")

    pairs = [master.make_pair(sku, store, 1.0), master.make_pair(sku, warehouse, 1.0), master.make_pair(sku, ndc, 1.0)]
    master.build_rules(pairs)
    for pair, opening in zip(pairs, (store_stock, warehouse_stock, ndc_stock)):
        pair["opening_on_hand"] = opening
        pair["opening_blocked"] = 0
    channel_locations = master.build_channel_locations(locations)
    return simulate.Ledger(rng, pairs, target_rows, channel_locations, 0)


@pytest.fixture
def one_customer(monkeypatch):
    """One line on the WEB channel, nothing deliberate, and the customer always pays."""
    monkeypatch.setattr(settings, "ORDER_CHANNEL_WEIGHTS", {"WEB": 1})
    monkeypatch.setattr(settings, "ORDER_LINE_COUNT_WEIGHTS", [1, 0, 0])
    monkeypatch.setattr(settings, "OVERSELL_ATTEMPT_SHARE", 0)
    monkeypatch.setattr(settings, "SPLIT_ATTEMPT_SHARE", 0)
    monkeypatch.setattr(settings, "INBOUND_ATTEMPT_SHARE", 0)
    monkeypatch.setattr(settings, "PAY_CHANCE", 1.0)
    monkeypatch.setattr(settings, "CUSTOMER_CANCEL_CHANCE", 0)
    monkeypatch.setattr(settings, "WAITED_TRUCK_LATE_CHANCE", 0)
    monkeypatch.setattr(settings, "ONLINE_RETURN_CHANCE", 0)
    monkeypatch.setattr(settings, "RUSH_DELIVERY_CHANCE", 0)
    return monkeypatch


def place(world, moment=PLACED):
    world.clock = moment
    return order_flow.place_order(world, moment)


def statuses(order):
    return [hold["status"] for hold in order["holds"]]


# ------------------------------------------------------------ exact counts

@pytest.mark.parametrize("movements, orders", [(SMALL_MOVEMENTS, SMALL_ORDERS), (6_001, 399), (3_000, 100), (2_000, 1)])
def test_reaches_the_exact_movement_and_order_counts(movements, orders):
    world = generated(movements, orders)
    assert len(world.movements) == movements
    assert len(world.orders) == orders
    assert world.orders_left == 0


def test_presets_ask_for_the_right_order_counts():
    assert settings.SCALES["starter"]["orders"] == 1_000
    assert settings.SCALES["full"]["orders"] == 50_000


def test_held_back_rows_keep_room_for_orders_not_placed_yet():
    world = small_world(target_rows=100)
    world.orders_left = 10
    assert world.free_rows() == 100 - 10 * settings.ROWS_PER_ORDER


# ------------------------------------------------------------ one customer, one path

def test_a_hold_counts_as_reserved_from_the_moment_it_is_placed(one_customer):
    world = small_world()
    order = place(world)
    assert order["status"] == "ALLOCATED"
    assert sum(world.reserved.values()) == order["lines"][0]["qty"]


def test_hold_is_released_at_exactly_its_expiry_and_not_a_second_before(one_customer):
    one_customer.setattr(settings, "PAY_CHANCE", 0.0)
    world = small_world()
    order = place(world)
    expires_at = order["holds"][0]["expires_at"]
    qty = order["holds"][0]["qty"]

    world.tick(expires_at - SECOND)
    assert sum(world.reserved.values()) == qty
    assert statuses(order) == ["ACTIVE"]

    world.tick(expires_at)
    assert sum(world.reserved.values()) == 0
    assert statuses(order) == ["EXPIRED"]
    assert order["status"] == "CANCELLED"
    event = world.reservation_events[-1]
    assert event[0] == expires_at
    assert event[3:5] == ("EXPIRED", "HOLD_EXPIRED")


def test_expired_hold_writes_no_movement(one_customer):
    one_customer.setattr(settings, "PAY_CHANCE", 0.0)
    world = small_world()
    order = place(world)
    world.tick(order["holds"][0]["expires_at"])
    assert world.movements == []


def test_customer_who_cancels_releases_the_hold(one_customer):
    one_customer.setattr(settings, "PAY_CHANCE", 0.0)
    one_customer.setattr(settings, "CUSTOMER_CANCEL_CHANCE", 1.0)
    world = small_world()
    order = place(world)
    world.tick(PLACED + timedelta(minutes=30))
    assert statuses(order) == ["CANCELLED"]
    assert order["status"] == "CANCELLED"
    assert sum(world.reserved.values()) == 0


def test_paid_order_ships_the_next_morning_and_turns_the_hold_into_a_sale(one_customer):
    world = small_world()
    order = place(world)
    qty = order["holds"][0]["qty"]
    before = world.sellable[order["holds"][0]["pair_index"]]

    world.tick(PLACED + timedelta(minutes=30))
    assert order["status"] == "CONFIRMED"
    assert world.movements == []
    assert world.pending_rows == 1

    world.run_scheduled(PLACED.date() + DAY)

    assert order["status"] == "FULFILLED"
    assert statuses(order) == ["CONSUMED"]
    assert order["lines"][0]["status"] == "SHIPPED"
    assert world.pending_rows == 0
    assert sum(world.reserved.values()) == 0
    assert world.sellable[order["holds"][0]["pair_index"]] == before - qty
    sale = world.movements[-1]
    assert (sale[1], sale[3], sale[10]) == ("SALE", -qty, order["holds"][0]["allocation_no"])
    assert world.shipments[sale[8]]["shipment_type"] == "CUSTOMER"


def test_damage_cannot_hit_stock_promised_to_an_order():
    """Otherwise the order would reach the shelf and find less than it was promised."""
    world = small_world(warehouse_stock=5, ndc_stock=5, store_stock=5)
    for pair_index in world.reserved:
        world.reserved[pair_index] = 5

    for _ in range(20):
        simulate.do_damage(world, PLACED.date(), PLACED)

    assert world.movements == []
    assert sum(world.blocked.values()) == 0


def test_damage_can_hit_stock_nobody_has_promised():
    world = small_world(warehouse_stock=5, ndc_stock=5, store_stock=5)
    for pair_index in world.reserved:
        world.reserved[pair_index] = 4

    simulate.do_damage(world, PLACED.date(), PLACED)

    assert sum(world.blocked.values()) == 1


def test_sku_gets_no_order_from_its_stop_date(one_customer):
    world = small_world()
    sku_id = world.sku_ids[0]
    world.orders_stop_on[sku_id] = PLACED.date()
    assert order_flow.can_be_ordered(world, sku_id, PLACED - DAY, set())
    assert not order_flow.can_be_ordered(world, sku_id, PLACED, set())


def test_rows_for_a_paid_order_are_promised_as_soon_as_it_is_placed(one_customer):
    world = small_world()
    place(world)
    assert world.pending_rows == 1


def test_order_paid_too_late_to_ship_is_still_waiting_at_the_end(one_customer):
    world = small_world()
    moment = config.AS_OF - timedelta(hours=3)
    order = place(world, moment)
    world.run_timed(config.AS_OF)
    assert order["status"] == "CONFIRMED"
    assert sum(world.reserved.values()) == order["holds"][0]["qty"]
    assert world.movements == []


def test_oversell_attempt_is_rejected_and_kept_as_evidence(one_customer):
    one_customer.setattr(settings, "OVERSELL_ATTEMPT_SHARE", 1.0)
    world = small_world()
    order = place(world)

    assert order["status"] == "REJECTED"
    assert order["total"] == 0
    assert statuses(order) == ["REJECTED"]
    assert sum(world.reserved.values()) == 0
    assert world.decisions[-1]["reason_code"] == "INSUFFICIENT_ATP"
    assert world.oversell_attempts == {(order["index"], 1)}
    assert order["holds"][0]["allocation_no"] is None


def test_split_attempt_makes_two_holds_at_two_locations(one_customer):
    one_customer.setattr(settings, "SPLIT_ATTEMPT_SHARE", 1.0)
    world = small_world(warehouse_stock=20, ndc_stock=20)
    order = place(world)

    assert world.decisions[-1]["decision_code"] == "SPLIT"
    assert len(order["holds"]) == 2
    assert len({hold["location_id"] for hold in order["holds"]}) == 2
    assert sum(hold["qty"] for hold in order["holds"]) == order["lines"][0]["qty"]


def test_split_order_makes_one_parcel_per_location(one_customer):
    one_customer.setattr(settings, "SPLIT_ATTEMPT_SHARE", 1.0)
    world = small_world(warehouse_stock=20, ndc_stock=20)
    order = place(world)
    world.tick(PLACED + timedelta(minutes=30))
    world.run_scheduled(PLACED.date() + DAY)

    parcels = {hold["customer_shipment"] for hold in order["holds"]}
    assert len(parcels) == 2
    assert order["status"] == "FULFILLED"


# ------------------------------------------------------------ waiting on a truck

ETA = PLACED + timedelta(hours=10)


def order_on_truck(one_customer, ndc_stock=30, truck_qty=30):
    """All the NDC's stock on a truck to the warehouse, and a paid order waiting on it."""
    one_customer.setattr(settings, "INBOUND_ATTEMPT_SHARE", 1.0)
    world = small_world(warehouse_stock=0, ndc_stock=ndc_stock)
    world.clock = PLACED - timedelta(hours=1)
    truck = world.ship_transfer(THE_NDC, WAREHOUSE, truck_qty, PLACED - timedelta(hours=1), ETA)
    order = place(world)
    assert world.decisions[-1]["decision_code"] == "INBOUND"
    world.tick(PLACED + timedelta(minutes=30))
    assert order["status"] == "CONFIRMED"
    return world, order, truck


def test_order_on_a_truck_does_not_ship_before_it_arrives(one_customer):
    world, order, _truck = order_on_truck(one_customer)

    world.run_scheduled(PLACED.date() + DAY)

    assert order["status"] == "CONFIRMED"
    assert all(row[1] != "SALE" for row in world.movements)
    assert world.scheduled == {}


def test_order_ships_when_its_truck_arrives_and_the_sale_comes_after_the_arrival(one_customer):
    world, order, truck = order_on_truck(one_customer)
    arrived_at = world.tick(ETA - timedelta(hours=1))

    world.arrive(truck, arrived_at)

    assert order["status"] == "FULFILLED"
    assert [row[1] for row in world.movements] == ["TRANSFER_OUT", "TRANSFER_IN", "SALE"]
    assert world.movements[2][4] >= world.movements[1][4]
    assert world.pending_rows == 0


def test_truck_count_never_goes_above_what_it_carries(one_customer):
    world, order, truck = order_on_truck(one_customer)
    assert world.shipments[truck]["held"] == order["holds"][0]["qty"] <= world.shipments[truck]["qty"]


def late_notice(world, truck, new_eta_at):
    world.miss_eta(truck, new_eta_at)
    world.tick(ETA + timedelta(hours=settings.ETA_MISSED_NOTICE_HOURS))


def test_late_truck_updates_the_promise_and_keeps_the_first(one_customer):
    world, order, truck = order_on_truck(one_customer)
    hold = order["holds"][0]
    first_promise = hold["first_promise"]

    late_notice(world, truck, ETA + DAY)

    assert hold["first_promise"] == first_promise
    assert hold["promised"] == ETA + 2 * DAY
    assert hold["status"] == "CONFIRMED"
    assert world.counters["late_repromised"] == 1
    assert world.reservation_events[-1][3:] == ("CONFIRMED", "INBOUND_LATE_REPROMISED", ETA + 2 * DAY)


def test_late_truck_past_the_limit_cancels_and_releases(one_customer):
    world, order, truck = order_on_truck(one_customer)
    late_notice(world, truck, ETA + 4 * DAY)

    assert statuses(order) == ["CANCELLED"]
    assert order["status"] == "CANCELLED"
    assert order["lines"][0]["status"] == "CANCELLED"
    assert sum(world.reserved.values()) == 0
    assert world.shipments[truck]["held"] == 0
    assert world.pending_rows == 0
    assert world.counters["late_cancelled"] == 1


def test_late_truck_at_exactly_the_limit_only_updates_the_promise(one_customer):
    world, order, truck = order_on_truck(one_customer)
    late_notice(world, truck, ETA + 3 * DAY)
    assert statuses(order) == ["CONFIRMED"]
    assert world.counters["late_repromised"] == 1


def test_late_truck_moves_the_hold_when_another_location_has_it(one_customer):
    world, order, truck = order_on_truck(one_customer)
    qty = order["holds"][0]["qty"]
    # The store gets a delivery while the customer waits.
    world.sellable[STORE] = qty + 50
    pending_before = world.pending_rows

    late_notice(world, truck, ETA + DAY)

    old, moved = order["holds"]
    assert (old["status"], moved["status"]) == ("CANCELLED", "CONFIRMED")
    assert moved["hold_no"] == 2
    assert moved["supply_type"] == "ON_HAND"
    assert moved["qty"] == qty
    assert world.shipments[truck]["held"] == 0
    # The total held didn't change, so the same stock isn't reserved twice.
    assert sum(world.reserved.values()) == qty
    assert world.reserved[WAREHOUSE] == 0
    assert world.pending_rows == pending_before
    assert world.decisions[-1]["decision_code"] == "REALLOCATED"


def test_moved_order_ships_the_next_morning_from_its_new_location(one_customer):
    world, order, truck = order_on_truck(one_customer)
    world.sellable[STORE] = order["holds"][0]["qty"] + 50
    late_notice(world, truck, ETA + 2 * DAY)

    world.run_scheduled(ETA.date() + DAY)

    assert order["status"] == "FULFILLED"
    sale = world.movements[-1]
    assert (sale[0], sale[1]) == (STORE, "SALE")
    assert sum(world.reserved.values()) == 0


def test_a_waiting_hold_looks_again_every_day(one_customer):
    world, order, truck = order_on_truck(one_customer)
    late_notice(world, truck, ETA + 2 * DAY)
    assert world.counters["late_repromised"] == 1

    world.sellable[STORE] = order["holds"][0]["qty"] + 50
    world.tick(ETA + timedelta(hours=settings.ETA_MISSED_NOTICE_HOURS) + DAY)

    assert world.counters["late_reallocated"] == 1
    assert order["holds"][0]["status"] == "CANCELLED"


def test_rush_delivery_lets_a_waiting_hold_move_the_next_day(one_customer):
    one_customer.setattr(settings, "RUSH_DELIVERY_CHANCE", 1.0)
    world, order, truck = order_on_truck(one_customer)
    late_notice(world, truck, ETA + 2 * DAY)
    assert world.counters["late_repromised"] == 1
    assert order["holds"][0]["status"] == "CONFIRMED"

    # The delivery comes in the next morning, and the daily look finds it that afternoon.
    world.run_scheduled(ETA.date() + DAY)
    world.tick(ETA + timedelta(hours=settings.ETA_MISSED_NOTICE_HOURS) + DAY)

    assert world.movements[-1][1] == "RECEIPT"
    assert world.counters["late_reallocated"] == 1
    assert order["holds"][1]["status"] == "CONFIRMED"
    assert order["holds"][1]["supply_type"] == "ON_HAND"


def test_rush_delivery_waits_for_the_budget(one_customer):
    world = small_world(target_rows=0)
    world.rush_delivery(world.sku_ids[0], PLACED)
    assert world.scheduled == {}


def test_daily_look_stops_when_the_truck_arrives(one_customer):
    world, order, truck = order_on_truck(one_customer)
    late_notice(world, truck, ETA + DAY)
    world.arrive(truck, world.tick(ETA + DAY))
    assert order["status"] == "FULFILLED"

    world.tick(ETA + 3 * DAY)

    assert world.timed == []


def test_truck_someone_is_waiting_on_can_be_held_up(one_customer):
    one_customer.setattr(settings, "WAITED_TRUCK_LATE_CHANCE", 1.0)
    world, order, truck = order_on_truck(one_customer)
    world.schedule(ETA.date(), "TRANSFER_IN", WAREHOUSE, 30, 1, truck)

    world.run_scheduled(ETA.date())

    assert world.shipments[truck]["status"] == "IN_TRANSIT"
    assert "next_eta_at" in world.shipments[truck]
    assert world.counters["transfers_late"] == 1
    # Its arrival row is still promised for the new day.
    assert world.pending_rows >= 1


def test_truck_nobody_is_waiting_on_is_not_held_up(one_customer):
    one_customer.setattr(settings, "WAITED_TRUCK_LATE_CHANCE", 1.0)
    world = small_world(warehouse_stock=0)
    truck = world.ship_transfer(THE_NDC, WAREHOUSE, 30, PLACED - DAY, ETA)
    world.schedule(ETA.date(), "TRANSFER_IN", WAREHOUSE, 30, 1, truck)

    world.run_scheduled(ETA.date())

    assert world.shipments[truck]["status"] == "RECEIVED"


def test_truck_runs_late_only_once(one_customer):
    one_customer.setattr(settings, "WAITED_TRUCK_LATE_CHANCE", 1.0)
    world, _order, truck = order_on_truck(one_customer)
    world.shipments[truck]["next_eta_at"] = ETA
    assert not world.hold_up_truck(truck, WAREHOUSE, 30, ETA.date())


# ------------------------------------------------------------ the whole generated run

def test_every_required_case_is_in_the_data(ledger):
    counters = ledger.counters
    assert counters["oversell_attempts"] > 0
    assert counters["orders_expired"] > 0
    assert counters["decisions_split"] > 0
    assert counters["decisions_inbound"] > 0
    assert counters["late_reallocated"] > 0
    assert counters["late_repromised"] > 0
    assert counters["late_cancelled"] > 0
    assert counters["orders_unshipped_no_budget"] == 0


def test_replaying_the_ledger_with_orders_never_goes_below_zero(ledger):
    sellable = {}
    blocked = {}
    for index, pair in enumerate(ledger.pairs):
        sellable[index] = pair["opening_on_hand"] - pair["opening_blocked"]
        blocked[index] = pair["opening_blocked"]
    for row in ledger.movements:
        stock = sellable if row[2] == "SELLABLE" else blocked
        stock[row[0]] += row[3]
        assert stock[row[0]] >= 0
    assert sellable == ledger.sellable
    assert blocked == ledger.blocked


def test_reserved_count_equals_the_open_holds(ledger):
    """Two calculations: the running count, and the holds left open at the end."""
    expected = {index: 0 for index in ledger.reserved}
    for hold in ledger.holds:
        if hold["status"] in ("ACTIVE", "CONFIRMED"):
            expected[hold["pair_index"]] += hold["qty"]
    assert ledger.reserved == expected
    assert min(expected.values()) >= 0


def test_truck_counts_equal_the_open_holds_on_each_truck(ledger):
    expected = {}
    for hold in ledger.holds:
        if hold["shipment_index"] is not None and hold["status"] in ("ACTIVE", "CONFIRMED", "CONSUMED"):
            expected[hold["shipment_index"]] = expected.get(hold["shipment_index"], 0) + hold["qty"]
    for index, shipment in enumerate(ledger.shipments):
        if shipment["shipment_type"] == "TRANSFER":
            assert shipment["held"] <= shipment["qty"]
            assert expected.get(index, 0) >= shipment["held"]


def test_nothing_ever_due_after_the_data_ends_was_written(ledger):
    assert max(row[4] for row in ledger.movements) <= config.AS_OF
    assert max(event[0] for event in ledger.reservation_events) <= config.AS_OF
    assert max(event[0] for event in ledger.order_events) <= config.AS_OF
    assert max(event[0] for event in ledger.shipment_events) <= config.AS_OF


def test_order_sales_match_a_consumed_hold_exactly(ledger):
    consumed = [hold for hold in ledger.holds if hold["status"] == "CONSUMED"]
    sales = {row[10]: row for row in ledger.movements if row[10] is not None}
    assert len(consumed) == len(sales)
    assert consumed
    for hold in consumed:
        sale = sales[hold["allocation_no"]]
        assert (sale[0], sale[1], sale[2], sale[3]) == (hold["pair_index"], "SALE", "SELLABLE", -hold["qty"])
        assert sale[8] == hold["customer_shipment"]


def test_sale_of_stock_from_a_truck_never_comes_before_the_truck(ledger):
    arrivals = {row[8]: row[4] for row in ledger.movements if row[1] == "TRANSFER_IN"}
    sales = {row[10]: row[4] for row in ledger.movements if row[10] is not None}
    checked = 0
    for hold in ledger.holds:
        if hold["status"] == "CONSUMED" and hold["supply_type"] == "INBOUND":
            assert sales[hold["allocation_no"]] >= arrivals[hold["shipment_index"]]
            checked += 1
    assert checked > 0


def test_expired_holds_are_dated_at_their_expiry(ledger):
    expired = [event for event in ledger.reservation_events if event[3] == "EXPIRED"]
    assert expired
    for event in expired:
        assert event[0] == ledger.holds[event[1]]["expires_at"]


def test_no_hold_is_confirmed_at_or_after_it_expired(ledger):
    for event in ledger.reservation_events:
        if event[2] == "ACTIVE" and event[3] == "CONFIRMED":
            assert event[0] < ledger.holds[event[1]]["expires_at"]


def test_a_cancelled_late_hold_was_really_past_the_limit(ledger):
    cancelled = [hold for hold in ledger.holds
                 if any(event[1] == hold["index"] and event[4] == "INBOUND_LATE_CANCELLED"
                        for event in ledger.reservation_events)]
    assert cancelled
    limit = timedelta(days=rules.LATE_LIMIT_DAYS)
    for hold in cancelled:
        truck = ledger.shipments[hold["shipment_index"]]
        assert truck["eta_at"] + timedelta(hours=rules.PROMISE_HOURS) > hold["first_promise"] + limit


def test_every_moved_hold_gave_up_its_old_hold_in_the_same_second(ledger):
    moved = [event for event in ledger.reservation_events
             if event[4] == "INBOUND_LATE_REALLOCATED" and event[3] == "CONFIRMED"]
    assert moved
    for event in moved:
        new_hold = ledger.holds[event[1]]
        order = ledger.orders[new_hold["order_index"]]
        released = [other for other in order["holds"]
                    if other["line"] is new_hold["line"] and other["status"] == "CANCELLED"]
        assert released
        assert new_hold["created_at"] == event[0]
        assert new_hold["hold_no"] > 1


def test_derived_order_total_comes_from_its_lines(ledger):
    for order in ledger.orders:
        total = sum((line["qty"] * line["unit_price"] for line in order["lines"] if line["status"] != "REJECTED"),
                    start=order["total"] * 0)
        assert order["total"] == total


def test_exported_ids_follow_the_brief(ledger):
    assert export.order_rows(ledger)[0]["order_id"] == "ORD-2026-000001"
    assert export.reservation_rows(ledger)[0]["reservation_id"] == "RES-ORD-2026-000001-01"
    assert export.order_line_rows(ledger)[0]["order_line_id"] == "ORD-2026-000001-L01"
    assert export.decision_rows(ledger)[0]["decision_id"] == "DEC-2026-0000001"


def test_exported_events_are_in_a_legal_order(ledger):
    by_hold = {}
    for row in export.reservation_event_rows(ledger):
        by_hold.setdefault(row["reservation_id"], []).append(row)
    for events in by_hold.values():
        previous = None
        for event in events:
            assert event["from_status"] == previous
            assert rules.can_transition(rules.RESERVATION_TRANSITIONS, previous, event["to_status"])
            previous = event["to_status"]


def test_same_seed_gives_the_same_orders():
    first = generated()
    second = generated()
    assert export.order_rows(first) == export.order_rows(second)
    assert export.reservation_rows(first) == export.reservation_rows(second)
    assert export.decision_rows(first) == export.decision_rows(second)


def test_illegal_state_change_stops_the_run():
    world = small_world()
    hold = {"index": 0, "status": "EXPIRED"}
    with pytest.raises(RuntimeError, match="illegal transition"):
        order_flow.set_hold_status(world, hold, "CONFIRMED", PLACED, "TEST")
