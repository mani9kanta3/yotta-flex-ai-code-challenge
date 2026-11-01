"""The generator: exact counts, no impossible stock, same seed same files."""

import random
import re
from datetime import date, datetime, timedelta

import pytest

from app import config
from generator import export, master, settings, simulate
from scripts.generate import generate
from tests.conftest import TEST_SEED, small_scale


def build_ledger(target_rows=None):
    scale = small_scale()
    rng = random.Random(TEST_SEED)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    pairs = master.build_pairs(rng, scale, skus, locations)
    master.build_rules(pairs)
    master.build_opening_stock(rng, pairs)
    return simulate.simulate(rng, pairs, target_rows or scale["movements"])


def small_ledger(target_rows, opening_stock):
    """One live store pair and one live warehouse pair, with stock we choose."""
    scale = small_scale()
    rng = random.Random(TEST_SEED)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    sku = next(s for s in skus if s["status"] == "ACTIVE" and s["effective_from"] < date(2026, 1, 1))
    store = next(l for l in locations if l["location_type"] == "STORE")
    store["opened_on"] = date(2021, 1, 1)
    warehouse = next(l for l in locations if l["location_type"] == "WAREHOUSE")

    pairs = [master.make_pair(sku, store, 1.0), master.make_pair(sku, warehouse, 1.0)]
    master.build_rules(pairs)
    for pair in pairs:
        pair["opening_on_hand"] = opening_stock
        pair["opening_blocked"] = 0
    return simulate.Ledger(rng, pairs, target_rows), pairs


# ------------------------------------------------------------ exact counts

@pytest.mark.parametrize("target", [1_500, 1_501, 7_777])
def test_reaches_the_exact_target(target):
    assert len(build_ledger(target).movements) == target


def test_full_preset_asks_for_the_brief_numbers():
    from generator.settings import SCALES
    full = SCALES["full"]
    assert (full["skus"], full["locations"], full["movements"], full["orders"]) == (
        5_000, 100, 1_000_000, 50_000)


# ------------------------------------------------------------ no impossible stock

def test_replaying_the_ledger_never_goes_below_zero():
    """Rebuilds stock from the rows alone, separately from the simulator's count."""
    ledger = build_ledger()
    sellable = {}
    blocked = {}
    for index, pair in enumerate(ledger.pairs):
        sellable[index] = pair["opening_on_hand"] - pair["opening_blocked"]
        blocked[index] = pair["opening_blocked"]

    for row in ledger.movements:
        pair_index, _type, bucket, quantity = row[0], row[1], row[2], row[3]
        stock = sellable if bucket == "SELLABLE" else blocked
        stock[pair_index] += quantity
        # A BLOCK takes from one bucket before adding to the other, and both
        # rows are valid alone.
        assert stock[pair_index] >= 0

    assert sellable == ledger.sellable
    assert blocked == ledger.blocked


def test_sale_is_refused_above_stock_and_writes_no_row():
    ledger, _pairs = small_ledger(target_rows=10, opening_stock=2)
    moment = datetime(2026, 3, 1, 10)

    assert not ledger.sell_walk_in(0, 3, moment)
    assert ledger.movements == []
    assert ledger.sellable[0] == 2
    assert ledger.counters["walk_in_refused"] == 1


@pytest.mark.parametrize("qty", [0, -1, -3])
def test_zero_or_negative_sale_is_refused_and_writes_no_row(qty):
    ledger, _pairs = small_ledger(target_rows=10, opening_stock=5)

    assert not ledger.sell_walk_in(0, qty, datetime(2026, 3, 1, 10))

    assert ledger.movements == []
    assert ledger.sellable[0] == 5
    assert ledger.counters["walk_in_refused"] == 1


def test_sale_is_refused_when_stock_is_held_by_a_reservation():
    ledger, _pairs = small_ledger(target_rows=10, opening_stock=5)
    ledger.reserved[0] = 4

    assert not ledger.sell_walk_in(0, 2, datetime(2026, 3, 1, 10))
    assert ledger.movements == []
    assert ledger.sell_walk_in(0, 1, datetime(2026, 3, 1, 11))
    assert ledger.sellable[0] == 4


# ------------------------------------------------------------ blocked stock

def test_block_moves_stock_without_changing_the_total():
    ledger, _pairs = small_ledger(target_rows=10, opening_stock=10)
    before = ledger.sellable[0] + ledger.blocked[0]

    ledger.block(0, 3, datetime(2026, 3, 1, 10))

    assert ledger.sellable[0] == 7
    assert ledger.blocked[0] == 3
    assert ledger.sellable[0] + ledger.blocked[0] == before

    first, second = ledger.movements
    assert first[3] + second[3] == 0
    assert first[7] == second[7]


def test_write_off_is_what_lowers_total_stock():
    ledger, _pairs = small_ledger(target_rows=10, opening_stock=10)
    ledger.block(0, 3, datetime(2026, 3, 1, 10))

    ledger.write_off(0, 3, datetime(2026, 3, 2, 10))

    assert ledger.sellable[0] + ledger.blocked[0] == 7


def test_unblock_puts_stock_back_without_changing_the_total():
    ledger, _pairs = small_ledger(target_rows=10, opening_stock=10)
    ledger.block(0, 3, datetime(2026, 3, 1, 10))

    ledger.unblock(0, 3, datetime(2026, 3, 2, 10))

    assert ledger.sellable[0] == 10
    assert ledger.blocked[0] == 0


# ------------------------------------------------------------ transfers

def three_level_ledger(target_rows=50, ndc_stock=100, warehouse_stock=100, store_stock=0):
    """One SKU at a store, its warehouse and the NDC, with stock we choose."""
    scale = small_scale()
    rng = random.Random(TEST_SEED)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    sku = next(s for s in skus if s["status"] == "ACTIVE" and s["effective_from"] < date(2026, 1, 1))
    store = next(l for l in locations if l["location_type"] == "STORE")
    store["opened_on"] = date(2021, 1, 1)
    warehouse = next(l for l in locations if l["location_id"] == store["parent_location_id"])
    ndc = next(l for l in locations if l["location_type"] == "NDC")

    pairs = [master.make_pair(sku, store, 1.0), master.make_pair(sku, warehouse, 1.0), master.make_pair(sku, ndc, 1.0)]
    master.build_rules(pairs)
    for pair, opening in zip(pairs, (store_stock, warehouse_stock, ndc_stock)):
        pair["opening_on_hand"] = opening
        pair["opening_blocked"] = 0
    return simulate.Ledger(rng, pairs, target_rows), pairs


STORE, WAREHOUSE, THE_NDC = 0, 1, 2
SHIPPED = datetime(2026, 3, 1, 10)
DUE = datetime(2026, 3, 3, 12)


def test_truck_takes_stock_from_the_source_and_gives_it_to_nobody_until_it_arrives():
    ledger, _pairs = three_level_ledger()
    total_before = ledger.sellable[STORE] + ledger.sellable[WAREHOUSE]

    index = ledger.ship_transfer(WAREHOUSE, STORE, 10, SHIPPED, DUE)

    assert ledger.sellable[WAREHOUSE] == 90
    assert ledger.sellable[STORE] == 0
    assert ledger.sellable[STORE] + ledger.sellable[WAREHOUSE] + ledger.shipments[index]["qty"] == total_before

    ledger.arrive(index, DUE)

    assert ledger.sellable[WAREHOUSE] == 90
    assert ledger.sellable[STORE] == 10
    assert ledger.sellable[STORE] + ledger.sellable[WAREHOUSE] == total_before
    assert [row[1] for row in ledger.movements] == ["TRANSFER_OUT", "TRANSFER_IN"]


def test_truck_cannot_take_more_than_the_source_has_and_writes_nothing():
    ledger, _pairs = three_level_ledger(warehouse_stock=5)
    assert ledger.ship_transfer(WAREHOUSE, STORE, 6, SHIPPED, DUE) is None
    assert ledger.movements == []
    assert ledger.shipments == []
    assert ledger.sellable[WAREHOUSE] == 5


def test_truck_cannot_take_stock_held_for_an_order():
    ledger, _pairs = three_level_ledger(warehouse_stock=5)
    ledger.reserved[WAREHOUSE] = 4
    assert ledger.ship_transfer(WAREHOUSE, STORE, 2, SHIPPED, DUE) is None


def test_truck_cannot_arrive_twice():
    ledger, _pairs = three_level_ledger()
    index = ledger.ship_transfer(WAREHOUSE, STORE, 10, SHIPPED, DUE)
    ledger.arrive(index, DUE)
    with pytest.raises(RuntimeError, match="can't receive"):
        ledger.arrive(index, DUE + timedelta(days=1))
    assert ledger.sellable[STORE] == 10


def test_truck_cannot_arrive_before_it_left():
    ledger, _pairs = three_level_ledger()
    index = ledger.ship_transfer(WAREHOUSE, STORE, 10, SHIPPED, DUE)
    with pytest.raises(RuntimeError, match="can't receive"):
        ledger.arrive(index, SHIPPED)
    assert ledger.sellable[STORE] == 0


def test_store_reorders_from_its_warehouse_and_warehouse_from_the_ndc():
    ledger, _pairs = three_level_ledger(target_rows=50, warehouse_stock=100, ndc_stock=500)
    today = date(2026, 3, 1)

    ledger.maybe_reorder(STORE, today)

    shipment = ledger.shipments[0]
    assert (shipment["source_index"], shipment["destination_index"]) == (WAREHOUSE, STORE)
    assert ledger.on_order[STORE]

    ledger.sellable[WAREHOUSE] = 1
    ledger.maybe_reorder(WAREHOUSE, today)
    assert (ledger.shipments[-1]["source_index"], ledger.shipments[-1]["destination_index"]) == (THE_NDC, WAREHOUSE)


def test_only_the_ndc_buys_from_a_supplier():
    ledger, _pairs = three_level_ledger(ndc_stock=0)
    ledger.maybe_reorder(THE_NDC, date(2026, 3, 1))
    assert ledger.shipments == []
    assert ledger.on_order[THE_NDC]
    action = next(iter(ledger.scheduled.values()))[0][0]
    assert action == "RECEIPT"


def test_empty_source_sends_no_truck_and_refills_itself():
    ledger, _pairs = three_level_ledger(warehouse_stock=0, ndc_stock=0)
    ledger.maybe_reorder(STORE, date(2026, 3, 1))

    assert ledger.shipments == []
    assert ledger.movements == []
    assert not ledger.on_order[STORE]
    assert ledger.counters["transfers_short_of_stock"] == 2
    assert ledger.on_order[THE_NDC]


def test_truck_due_after_the_data_ends_stays_in_transit_with_one_row():
    ledger, _pairs = three_level_ledger()
    ledger.clock = datetime(2026, 9, 30, 10)
    ledger.maybe_reorder(STORE, date(2026, 9, 30))

    assert [row[1] for row in ledger.movements] == ["TRANSFER_OUT"]
    assert ledger.pending_rows == 0
    assert ledger.shipments[0]["status"] == "IN_TRANSIT"


def test_arrival_is_promised_in_the_budget():
    ledger, _pairs = three_level_ledger(target_rows=10)
    ledger.maybe_reorder(STORE, date(2026, 3, 1))
    assert len(ledger.movements) == 1
    assert ledger.pending_rows == 1
    assert ledger.free_rows() == 8


def test_no_truck_when_the_budget_cannot_hold_both_rows():
    ledger, _pairs = three_level_ledger(target_rows=1)
    ledger.maybe_reorder(STORE, date(2026, 3, 1))
    assert ledger.movements == []
    assert ledger.counters["reorders_skipped_no_budget"] == 1


def test_late_truck_records_a_new_eta_and_keeps_the_first():
    ledger, _pairs = three_level_ledger()
    index = ledger.ship_transfer(WAREHOUSE, STORE, 10, SHIPPED, DUE)
    new_eta = DUE + timedelta(days=2)

    ledger.miss_eta(index, new_eta)

    # Nobody knows yet. The notice comes in an hour after the first ETA passes.
    assert ledger.shipments[index]["eta_at"] == DUE
    ledger.tick(DUE + timedelta(hours=1))

    assert ledger.shipments[index]["eta_at"] == new_eta
    assert ledger.shipments[index]["original_eta_at"] == DUE
    event = ledger.shipment_events[-1]
    assert (event[2], event[3], event[4], event[5]) == ("IN_TRANSIT", "IN_TRANSIT", "ETA_MISSED", new_eta)
    assert event[0] > DUE


def test_generated_transfers_balance_for_every_sku():
    """Everything that left either arrived or is still on the road. Nothing else."""
    ledger = build_ledger()
    moved = {}
    for row in ledger.movements:
        if row[1] in ("TRANSFER_OUT", "TRANSFER_IN"):
            sku_id = ledger.pairs[row[0]]["sku_id"]
            moved[sku_id] = moved.get(sku_id, 0) + row[3]
    for shipment in ledger.shipments:
        if shipment["status"] == "IN_TRANSIT":
            sku_id = ledger.pairs[shipment["source_index"]]["sku_id"]
            moved[sku_id] = moved.get(sku_id, 0) + shipment["qty"]

    assert ledger.shipments
    assert set(moved.values()) == {0}


def test_network_stock_follows_only_outside_changes():
    """A transfer moves stock around, so it must not change the network total."""
    ledger = build_ledger()
    outside = sum(row[3] for row in ledger.movements if row[1] not in ("TRANSFER_OUT", "TRANSFER_IN"))
    opening = sum(pair["opening_on_hand"] for pair in ledger.pairs)
    closing = sum(ledger.sellable.values()) + sum(ledger.blocked.values())
    on_the_road = sum(s["qty"] for s in ledger.shipments if s["status"] == "IN_TRANSIT")

    assert closing + on_the_road == opening + outside


def test_every_generated_shipment_has_its_rows_in_the_right_order():
    ledger = build_ledger()
    rows_by_shipment = {}
    for row in ledger.movements:
        if row[8] is not None:
            rows_by_shipment.setdefault(row[8], []).append(row)

    for index, shipment in enumerate(ledger.shipments):
        rows = rows_by_shipment[index]
        assert rows[0][1] == "TRANSFER_OUT"
        assert rows[0][0] == shipment["source_index"]
        assert rows[0][3] == -shipment["qty"]
        if shipment["status"] == "RECEIVED":
            assert [row[1] for row in rows] == ["TRANSFER_OUT", "TRANSFER_IN"]
            assert rows[1][0] == shipment["destination_index"]
            assert rows[1][3] == shipment["qty"]
            assert rows[1][4] > rows[0][4]
        else:
            assert len(rows) == 1


def test_shipment_events_are_exported_in_a_legal_order():
    ledger = build_ledger()
    by_shipment = {}
    for row in export.shipment_event_rows(ledger):
        by_shipment.setdefault(row["shipment_id"], []).append(row)

    assert by_shipment
    for events in by_shipment.values():
        previous = None
        for event in events:
            assert event["from_status"] == previous
            previous = event["to_status"]
            assert event["occurred_at"] <= config.AS_OF


# ------------------------------------------------------------ returns

def sold_ledger(qty=3):
    ledger, _pairs = three_level_ledger(store_stock=10)
    ledger.sell_walk_in(STORE, qty, datetime(2026, 3, 1, 10))
    return ledger, len(ledger.movements) - 1


RETURNED = datetime(2026, 3, 5, 10)


def test_good_return_goes_back_on_the_shelf_and_names_its_sale():
    ledger, sale_row = sold_ledger(qty=3)
    assert ledger.return_goods(sale_row, 2, "GOOD", RETURNED)

    row = ledger.movements[-1]
    assert (row[0], row[1], row[2], row[3], row[9]) == (STORE, "RETURN", "SELLABLE", 2, sale_row)
    assert ledger.sellable[STORE] == 9
    assert ledger.blocked[STORE] == 0


def test_damaged_return_is_on_hand_but_not_sellable():
    ledger, sale_row = sold_ledger(qty=3)
    assert ledger.return_goods(sale_row, 2, "DAMAGED", RETURNED)

    assert ledger.movements[-1][2] == "BLOCKED"
    assert ledger.sellable[STORE] == 7
    assert ledger.blocked[STORE] == 2


def test_cannot_return_more_than_was_sold_and_writes_no_row():
    ledger, sale_row = sold_ledger(qty=3)
    rows_before = len(ledger.movements)
    assert not ledger.return_goods(sale_row, 4, "GOOD", RETURNED)
    assert len(ledger.movements) == rows_before
    assert ledger.sellable[STORE] == 7
    assert ledger.counters["returns_refused"] == 1


def test_parts_of_a_sale_cannot_add_up_to_more_than_was_sold():
    ledger, sale_row = sold_ledger(qty=3)
    assert ledger.return_goods(sale_row, 2, "GOOD", RETURNED)
    assert not ledger.return_goods(sale_row, 2, "GOOD", RETURNED + timedelta(days=1))
    assert ledger.return_goods(sale_row, 1, "GOOD", RETURNED + timedelta(days=2))
    assert not ledger.return_goods(sale_row, 1, "GOOD", RETURNED + timedelta(days=3))
    assert ledger.sellable[STORE] == 10


def test_cannot_return_against_a_row_that_is_not_a_sale():
    ledger, _pairs = three_level_ledger(store_stock=10)
    ledger.receive(STORE, 5, datetime(2026, 3, 1, 10))
    assert not ledger.return_goods(0, 1, "GOOD", RETURNED)
    assert ledger.counters["returns_refused"] == 1


@pytest.mark.parametrize("qty", [0, -1])
def test_zero_or_negative_return_is_refused(qty):
    ledger, sale_row = sold_ledger(qty=3)
    assert not ledger.return_goods(sale_row, qty, "GOOD", RETURNED)


def test_every_generated_return_names_an_earlier_sale_at_the_same_place():
    """Checked from the rows alone, separately from the simulator's own running count."""
    ledger = build_ledger()
    returned = {}
    for row in ledger.movements:
        if row[1] != "RETURN":
            assert row[9] is None
            continue
        sale = ledger.movements[row[9]]
        assert sale[1] == "SALE"
        assert sale[0] == row[0]
        assert sale[4] < row[4]
        returned[row[9]] = returned.get(row[9], 0) + row[3]
        assert returned[row[9]] <= -sale[3]
    assert returned


def test_generated_data_includes_a_sale_returned_in_two_parts():
    """Otherwise the limit on the total would only ever have been tested with one return."""
    scale = settings.get_scale("starter")
    rng = random.Random(TEST_SEED)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    pairs = master.build_pairs(rng, scale, skus, locations)
    master.build_rules(pairs)
    master.build_opening_stock(rng, pairs)
    ledger = simulate.simulate(rng, pairs, scale["movements"])

    counts = {}
    for row in ledger.movements:
        if row[1] == "RETURN":
            counts[row[9]] = counts.get(row[9], 0) + 1
    assert max(counts.values()) == 2


def test_refused_sale_tries_to_reorder_again():
    """A store that sold out while its warehouse was empty must not stay empty for good."""
    ledger, _pairs = three_level_ledger(store_stock=0, warehouse_stock=100)
    stores = simulate.build_store_index(ledger.pairs)
    store_id = next(iter(stores))
    day = date(2026, 3, 1)

    simulate.do_walk_in(ledger, stores, [store_id], [1.0], day, datetime(2026, 3, 1, 10))

    assert ledger.counters["walk_in_refused"] == 1
    assert len(ledger.shipments) == 1


# ------------------------------------------------------------ the final top-up

def run_top_up(ledger):
    stores = simulate.build_store_index(ledger.pairs)
    store_ids = list(stores)
    weights = [stores[store_id]["weight"] for store_id in store_ids]
    ledger.clock = datetime(2026, 9, 30, 21, 0, 0)
    simulate.top_up(ledger, stores, store_ids, weights, config.AS_OF.date())


def test_top_up_with_no_stock_writes_no_sale_and_still_reaches_the_target():
    ledger, _pairs = small_ledger(target_rows=5, opening_stock=0)

    run_top_up(ledger)

    assert len(ledger.movements) == 5
    assert all(row[1] == "RECEIPT" for row in ledger.movements)
    assert ledger.counters["top_up_sales"] == 0
    assert ledger.counters["top_up_receipts"] == 5


def test_top_up_respects_reservations():
    ledger, _pairs = small_ledger(target_rows=5, opening_stock=4)
    ledger.reserved[0] = 4

    run_top_up(ledger)

    assert all(row[1] != "SALE" for row in ledger.movements)
    assert ledger.sellable[0] == 4


def test_top_up_stops_with_an_error_rather_than_writing_a_bad_row():
    ledger, _pairs = small_ledger(target_rows=5, opening_stock=0)
    # With no warehouse pair there is no valid event left to post.
    ledger.pairs[1]["location"]["location_type"] = "NDC"

    with pytest.raises(RuntimeError, match="rows short"):
        run_top_up(ledger)


def test_top_up_stops_with_an_error_rather_than_passing_as_of():
    ledger, _pairs = small_ledger(target_rows=5, opening_stock=0)
    stores = simulate.build_store_index(ledger.pairs)
    store_ids = list(stores)
    weights = [stores[store_id]["weight"] for store_id in store_ids]
    ledger.clock = config.AS_OF - timedelta(seconds=2)

    with pytest.raises(RuntimeError, match="past AS_OF"):
        simulate.top_up(ledger, stores, store_ids, weights, config.AS_OF.date())


# ------------------------------------------------------------ IDs and reproducibility

def test_ids_follow_the_brief(tmp_path):
    ledger = build_ledger()
    rows = export.movement_rows(ledger)

    assert all(re.fullmatch(r"MOV-2026-\d{8}", row["movement_id"]) for row in rows)
    assert all(re.fullmatch(r"SKU-\d{6}", row["sku_id"]) for row in rows)
    assert all(re.fullmatch(r"LOC-\d{6}", row["location_id"]) for row in rows)
    assert rows[0]["movement_id"] == "MOV-2026-00000001"


def test_same_seed_gives_identical_files(tmp_path):
    first = generate(small_scale(), TEST_SEED, tmp_path / "a")
    second = generate(small_scale(), TEST_SEED, tmp_path / "b")
    assert first["files"] == second["files"]


def test_different_seed_gives_different_data(tmp_path):
    first = generate(small_scale(), TEST_SEED, tmp_path / "a")
    second = generate(small_scale(), TEST_SEED + 1, tmp_path / "b")
    assert first["files"]["inventory_movement.csv"]["sha256"] != second["files"]["inventory_movement.csv"]["sha256"]
