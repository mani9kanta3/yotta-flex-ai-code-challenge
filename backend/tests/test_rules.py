"""The stock rules. No database, so these run in milliseconds."""

from datetime import datetime, timedelta

import pytest

from app import rules

T = datetime(2026, 3, 1, 10, 0, 0)


def at(hour, minute=0, second=0):
    return datetime(2026, 3, 1, hour, minute, second)


# ------------------------------------------------------------ transfers and returns

def test_truck_can_take_exactly_what_is_free():
    assert rules.can_ship_transfer(qty=3, sellable=5, reserved=2)


def test_truck_cannot_take_stock_held_for_an_order():
    assert not rules.can_ship_transfer(qty=4, sellable=5, reserved=2)


@pytest.mark.parametrize("qty", [0, -1])
def test_truck_cannot_carry_zero_or_negative(qty):
    assert not rules.can_ship_transfer(qty=qty, sellable=5, reserved=0)


@pytest.mark.parametrize("wanted, sellable, reserved, safety, expected", [
    (10, 50, 0, 5, 10),   # plenty
    (10, 12, 0, 5, 7),    # sends what it can spare
    (10, 5, 0, 5, 0),     # only safety stock left
    (10, 3, 0, 5, 0),     # below safety stock
    (10, 20, 12, 5, 3),   # holds come off first
])
def test_source_keeps_its_safety_stock_when_asked_for_stock(wanted, sellable, reserved, safety, expected):
    assert rules.transfer_quantity(wanted, sellable, reserved, safety) == expected


def test_truck_on_the_road_can_be_received_after_its_last_event():
    assert rules.can_receive("IN_TRANSIT", at(8), at(8, 0, 1))


@pytest.mark.parametrize("t", [at(7), at(8)])
def test_truck_cannot_arrive_at_or_before_its_last_event(t):
    assert not rules.can_receive("IN_TRANSIT", at(8), t)


@pytest.mark.parametrize("status", ["CREATED", "RECEIVED", "DELIVERED", "CANCELLED"])
def test_only_a_truck_on_the_road_can_arrive(status):
    assert not rules.can_receive(status, at(8), at(9))


@pytest.mark.parametrize("supply_type, shipment_state, allowed", [
    ("ON_HAND", None, True),
    ("INBOUND", "IN_TRANSIT", False),
    ("INBOUND", "CREATED", False),
    ("INBOUND", None, False),
    ("INBOUND", "RECEIVED", True),
])
def test_hold_on_a_truck_waits_for_arrival(supply_type, shipment_state, allowed):
    assert rules.can_ship_hold(supply_type, shipment_state) == allowed


@pytest.mark.parametrize("qty, sold, returned, allowed", [
    (1, 3, 0, True),
    (3, 3, 0, True),
    (4, 3, 0, False),
    (2, 3, 1, True),
    (3, 3, 1, False),
    (1, 3, 3, False),
    (0, 3, 0, False),
    (-1, 3, 0, False),
])
def test_return_can_never_pass_what_was_sold(qty, sold, returned, allowed):
    assert rules.can_return(qty, sold, returned) == allowed


def test_damaged_return_goes_to_the_blocked_bucket():
    assert rules.RETURN_BUCKETS == {"GOOD": "SELLABLE", "DAMAGED": "BLOCKED"}


# ------------------------------------------------------------ late trucks

FIRST = at(20)
DAY = timedelta(days=1)
SECOND = timedelta(seconds=1)


def test_late_promise_counts_from_the_new_eta():
    assert rules.late_promise(at(20), at(10)) == at(20) + DAY


def test_late_promise_counts_from_now_when_the_truck_is_overdue():
    assert rules.late_promise(at(10), at(15)) == at(15) + DAY


@pytest.mark.parametrize("can_move, current, new, expected", [
    # The first promise can still be kept.
    (False, FIRST, FIRST, "KEEP"),
    (True, FIRST, FIRST - SECOND, "KEEP"),
    # Another location comes before everything else, even past the limit.
    (True, FIRST, FIRST + DAY, "REALLOCATE"),
    (True, FIRST, FIRST + 10 * DAY, "REALLOCATE"),
    # A hold with a new promise keeps looking.
    (True, FIRST + DAY, FIRST + DAY, "REALLOCATE"),
    # Up to and including the limit, the promise is updated.
    (False, FIRST, FIRST + DAY, "REPROMISE"),
    (False, FIRST, FIRST + 3 * DAY, "REPROMISE"),
    # One second past it, the hold is cancelled.
    (False, FIRST, FIRST + 3 * DAY + SECOND, "CANCEL"),
    # The limit counts from the first promise, not from the current one.
    (False, FIRST + 3 * DAY, FIRST + 3 * DAY + SECOND, "CANCEL"),
    # Nothing has changed since the last update, so there is nothing to do.
    (False, FIRST + DAY, FIRST + DAY, "KEEP"),
    (False, FIRST + 2 * DAY, FIRST + DAY, "KEEP"),
])
def test_what_happens_to_a_hold_on_a_late_truck(can_move, current, new, expected):
    assert rules.late_truck_action(can_move, FIRST, current, new) == expected


def late_candidate(location_id, from_on_hand, region="SOUTH", location_type="WAREHOUSE"):
    return {"location_id": location_id, "from_on_hand": from_on_hand, "promisable": from_on_hand,
            "region": region, "location_type": location_type}


def test_reallocation_needs_one_location_to_cover_the_whole_hold():
    assert rules.choose_reallocation(3, [late_candidate("LOC-1", 2), late_candidate("LOC-2", 2)], "SOUTH") is None


def test_reallocation_takes_the_best_ranked_location_that_covers_it():
    candidates = [late_candidate("LOC-1", 9, region="NORTH"), late_candidate("LOC-2", 3), late_candidate("LOC-3", 1)]
    assert rules.choose_reallocation(3, candidates, "SOUTH")["location_id"] == "LOC-2"


def test_reallocation_with_no_candidates_finds_nothing():
    assert rules.choose_reallocation(1, [], "SOUTH") is None


# ------------------------------------------------------------ walk-in sales

def test_on_hand_includes_blocked():
    assert rules.on_hand(sellable=95, blocked=5) == 100


def test_walk_in_available_leaves_out_reserved():
    assert rules.walk_in_available(sellable=95, reserved=12) == 83


def test_can_sell_exactly_what_is_available():
    assert rules.can_sell_walk_in(qty=3, sellable=5, reserved=2)


def test_cannot_sell_one_more_than_available():
    assert not rules.can_sell_walk_in(qty=4, sellable=5, reserved=2)


def test_cannot_sell_stock_held_for_an_online_order():
    assert not rules.can_sell_walk_in(qty=1, sellable=5, reserved=5)


@pytest.mark.parametrize("qty", [0, -1, -100])
def test_cannot_sell_zero_or_negative(qty):
    assert not rules.can_sell_walk_in(qty=qty, sellable=5, reserved=0)


# ------------------------------------------------------------ state at a past time

def confirmed_then_cancelled():
    return [(at(10), 1, "ACTIVE"), (at(10, 20), 2, "CONFIRMED"), (at(11), 3, "CANCELLED")]


@pytest.mark.parametrize("asked_at, expected", [
    (at(9, 59, 59), None),
    (at(10), "ACTIVE"),
    (at(10, 19, 59), "ACTIVE"),
    (at(10, 20), "CONFIRMED"),
    (at(10, 59, 59), "CONFIRMED"),
    (at(11), "CANCELLED"),
])
def test_state_at_reads_the_latest_event_up_to_that_time(asked_at, expected):
    assert rules.state_at(confirmed_then_cancelled(), asked_at) == expected


def test_state_at_does_not_depend_on_the_order_events_are_listed():
    events = list(reversed(confirmed_then_cancelled()))
    assert rules.state_at(events, at(10, 30)) == "CONFIRMED"


def test_state_at_breaks_a_tie_with_the_event_id():
    events = [(at(10), 8, "CONFIRMED"), (at(10), 7, "ACTIVE")]
    assert rules.state_at(events, at(10)) == "CONFIRMED"


# ------------------------------------------------------------ expiry at the exact time

def test_active_hold_still_holds_one_second_before_expiry():
    assert rules.holds_stock("ACTIVE", at(10, 29, 59), expires_at=at(10, 30))


def test_active_hold_stops_at_exactly_expiry():
    assert not rules.holds_stock("ACTIVE", at(10, 30), expires_at=at(10, 30))


def test_confirmed_hold_ignores_expiry():
    assert rules.holds_stock("CONFIRMED", at(23), expires_at=at(10, 30))


@pytest.mark.parametrize("state", [None, "REJECTED", "EXPIRED", "CANCELLED", "CONSUMED"])
def test_other_states_hold_nothing(state):
    assert not rules.holds_stock(state, at(10), expires_at=at(10, 30))


def test_confirm_works_one_second_before_expiry():
    assert rules.can_confirm("ACTIVE", at(10, 29, 59), expires_at=at(10, 30))


def test_confirm_is_refused_at_exactly_expiry():
    assert not rules.can_confirm("ACTIVE", at(10, 30), expires_at=at(10, 30))


def test_reserved_at_adds_up_only_what_holds_at_that_time():
    reservations = [
        {"qty": 5, "expires_at": at(10, 30), "events": confirmed_then_cancelled()},
        {"qty": 2, "expires_at": at(10, 30), "events": [(at(10), 4, "ACTIVE")]},
        {"qty": 9, "expires_at": at(10), "events": [(at(10), 5, "REJECTED")]},
    ]
    assert rules.reserved_at(reservations, at(9)) == 0
    assert rules.reserved_at(reservations, at(10, 10)) == 7
    assert rules.reserved_at(reservations, at(10, 30)) == 5
    assert rules.reserved_at(reservations, at(11)) == 0


# ------------------------------------------------------------ transitions

@pytest.mark.parametrize("from_status, to_status", [
    (None, "ACTIVE"), (None, "REJECTED"), ("ACTIVE", "CONFIRMED"), ("ACTIVE", "EXPIRED"),
    ("ACTIVE", "CANCELLED"), ("CONFIRMED", "CANCELLED"), ("CONFIRMED", "CONSUMED"),
    ("CONFIRMED", "CONFIRMED"),
])
def test_allowed_reservation_transitions(from_status, to_status):
    assert rules.can_transition(rules.RESERVATION_TRANSITIONS, from_status, to_status)


@pytest.mark.parametrize("from_status, to_status", [
    (None, "CONFIRMED"), ("ACTIVE", "CONSUMED"), ("CONFIRMED", "EXPIRED"), ("CONFIRMED", "ACTIVE"),
    ("EXPIRED", "CONFIRMED"), ("CANCELLED", "ACTIVE"), ("REJECTED", "ACTIVE"), ("CONSUMED", "CANCELLED"),
])
def test_refused_reservation_transitions(from_status, to_status):
    assert not rules.can_transition(rules.RESERVATION_TRANSITIONS, from_status, to_status)


# ------------------------------------------------------------ ATP, the worked example

def example(counts_inbound, applies_safety, buffer_units):
    return rules.position(
        on_hand_qty=100, blocked=5, reserved=12, inbound=20, safety_stock=10,
        buffer_units=buffer_units, counts_inbound=counts_inbound, applies_safety_stock=applies_safety,
    )


def test_atp_is_the_formula_in_the_brief():
    assert rules.available_to_promise(on_hand_qty=100, reserved=12, blocked=5, inbound=20) == 103


def test_web_promisable_includes_inbound():
    assert example(True, True, 3)["promisable"] == 90


def test_marketplace_promisable_leaves_out_inbound():
    assert example(False, True, 5)["promisable"] == 68


def test_store_has_no_inbound_safety_or_buffer():
    assert example(False, False, 0)["promisable"] == 83


def test_atp_always_shows_the_brief_number_even_if_the_channel_ignores_inbound():
    assert example(False, True, 5)["atp"] == 103


def test_promisable_never_goes_below_zero():
    assert rules.promisable(atp=2, safety_stock=10, buffer_units=3) == 0


def test_blocking_stock_lowers_atp_but_not_on_hand():
    before = rules.position(100, 5, 0, 0, 0, 0, True, True)
    after = rules.position(100, 8, 0, 0, 0, 0, True, True)
    assert after["on_hand"] == before["on_hand"]
    assert after["atp"] == before["atp"] - 3


def test_from_on_hand_ignores_inbound():
    assert example(True, True, 3)["from_on_hand"] == 70


# ------------------------------------------------------------ allocation

def candidate(location_id, region="SOUTH", location_type="WAREHOUSE", from_on_hand=0,
              promisable=None, shipments=None):
    return {
        "location_id": location_id,
        "region": region,
        "location_type": location_type,
        "from_on_hand": from_on_hand,
        "promisable": from_on_hand if promisable is None else promisable,
        "inbound_shipments": shipments or [],
    }


def test_ranks_same_region_then_warehouse_then_most_then_lowest_id():
    ranked = rules.rank_candidates([
        candidate("LOC-5", region="NORTH", from_on_hand=99),
        candidate("LOC-4", location_type="STORE", from_on_hand=50),
        candidate("LOC-3", from_on_hand=5),
        candidate("LOC-2", from_on_hand=9),
        candidate("LOC-1", from_on_hand=9),
    ], "SOUTH")
    assert [c["location_id"] for c in ranked] == ["LOC-1", "LOC-2", "LOC-3", "LOC-4", "LOC-5"]


def test_one_location_covering_exactly_the_quantity_is_enough():
    result = rules.allocate(5, [candidate("LOC-1", from_on_hand=5)], "SOUTH")
    assert result["decision_code"] == "SINGLE"
    assert result["parts"][0]["qty"] == 5


def test_one_unit_short_is_not_covered():
    result = rules.allocate(6, [candidate("LOC-1", from_on_hand=5)], "SOUTH")
    assert result["decision_code"] == "REJECTED"
    assert result["reason_code"] == "INSUFFICIENT_ATP"
    assert result["parts"] == []


def test_splits_across_two_locations():
    result = rules.allocate(7, [candidate("LOC-1", from_on_hand=5), candidate("LOC-2", from_on_hand=3)], "SOUTH")
    assert result["decision_code"] == "SPLIT"
    assert [(p["location_id"], p["qty"]) for p in result["parts"]] == [("LOC-1", 5), ("LOC-2", 2)]


def test_never_splits_across_three_locations():
    candidates = [candidate(f"LOC-{n}", from_on_hand=2) for n in range(1, 4)]
    assert rules.allocate(6, candidates, "SOUTH")["decision_code"] == "REJECTED"


def test_on_hand_is_preferred_over_inbound():
    shipment = {"shipment_id": "SHP-1", "free_qty": 9, "eta_at": T}
    result = rules.allocate(3, [
        candidate("LOC-1", from_on_hand=0, promisable=9, shipments=[shipment]),
        candidate("LOC-2", region="NORTH", from_on_hand=3),
    ], "SOUTH")
    assert result["decision_code"] == "SINGLE"
    assert result["parts"][0]["location_id"] == "LOC-2"


def test_inbound_names_the_shipment_and_its_eta():
    shipment = {"shipment_id": "SHP-1", "free_qty": 4, "eta_at": T}
    result = rules.allocate(4, [candidate("LOC-1", promisable=4, shipments=[shipment])], "SOUTH")
    assert result["decision_code"] == "INBOUND"
    assert result["parts"][0]["inbound_shipment_id"] == "SHP-1"
    assert result["parts"][0]["eta_at"] == T


def test_inbound_cannot_use_more_than_the_shipment_has_free():
    shipment = {"shipment_id": "SHP-1", "free_qty": 3, "eta_at": T}
    result = rules.allocate(4, [candidate("LOC-1", promisable=9, shipments=[shipment])], "SOUTH")
    assert result["decision_code"] == "REJECTED"


def test_no_candidates_has_its_own_reason():
    assert rules.allocate(1, [], "SOUTH")["reason_code"] == "NO_CANDIDATE_LOCATION"


@pytest.mark.parametrize("qty", [0, -1])
def test_allocate_refuses_zero_or_negative(qty):
    with pytest.raises(ValueError):
        rules.allocate(qty, [candidate("LOC-1", from_on_hand=5)], "SOUTH")


def test_promise_on_hand_is_a_day_from_now():
    assert rules.promised_ship_by(T, None) == T + timedelta(hours=24)


def test_promise_inbound_is_a_day_after_the_truck():
    eta = T + timedelta(hours=10)
    assert rules.promised_ship_by(T, eta) == eta + timedelta(hours=24)


def test_lock_order_is_sorted_with_no_duplicates():
    pairs = [("SKU-2", "LOC-1"), ("SKU-1", "LOC-2"), ("SKU-2", "LOC-1"), ("SKU-1", "LOC-1")]
    assert rules.sorted_pairs(pairs) == [("SKU-1", "LOC-1"), ("SKU-1", "LOC-2"), ("SKU-2", "LOC-1")]
