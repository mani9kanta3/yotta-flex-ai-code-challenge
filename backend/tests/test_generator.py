"""The generator: exact counts, no impossible stock, same seed same files."""

import random
import re
from datetime import date, datetime, timedelta

import pytest

from app import config
from generator import export, master, simulate
from scripts.generate import generate
from tests.conftest import TEST_SEED, tiny_scale


def build_ledger(target_rows=None):
    scale = tiny_scale()
    rng = random.Random(TEST_SEED)
    _products, skus = master.build_products_and_skus(rng, scale)
    locations = master.build_locations(rng, scale)
    pairs = master.build_pairs(rng, scale, skus, locations)
    master.build_rules(pairs)
    master.build_opening_stock(rng, pairs)
    return simulate.simulate(rng, pairs, target_rows or scale["movements"])


def small_ledger(target_rows, opening_stock):
    """One live store pair and one live warehouse pair, with stock we choose."""
    scale = tiny_scale()
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
    first = generate(tiny_scale(), TEST_SEED, tmp_path / "a")
    second = generate(tiny_scale(), TEST_SEED, tmp_path / "b")
    assert first["files"] == second["files"]


def test_different_seed_gives_different_data(tmp_path):
    first = generate(tiny_scale(), TEST_SEED, tmp_path / "a")
    second = generate(tiny_scale(), TEST_SEED + 1, tmp_path / "b")
    assert first["files"]["inventory_movement.csv"]["sha256"] != second["files"]["inventory_movement.csv"]["sha256"]
