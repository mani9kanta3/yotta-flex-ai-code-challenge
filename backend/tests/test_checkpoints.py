"""
ATP checkpoints: a second calculation, in plain Python, around the moments ATP changes
without an order being placed. Both answers have to agree with app/atp.py.
"""

import csv
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

from app import config
from eval import check_atp
from generator import checkpoints, export
from tests.conftest import FINAL_DB


def load(generated_data):
    return check_atp.load_checkpoints(export.truth_dir_of(generated_data))


def before_and_at(rows, kind):
    """Each chosen event is written as two rows, one after the other."""
    of_kind = [row for row in rows if row["kind"] == kind]
    return list(zip(of_kind[::2], of_kind[1::2]))


def number(row, name):
    return int(row[name])


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_every_checkpoint_matches_the_database(generated_data, final_cursor):
    results, differing = check_atp.check_checkpoints(final_cursor, load(generated_data))
    assert differing == []
    for kind in checkpoints.KINDS:
        assert results[kind]["checked"] == results[kind]["matched"] > 0


@pytest.mark.parametrize("kind", checkpoints.KINDS)
def test_each_kind_has_events_and_no_more_than_asked_for(generated_data, kind):
    # Late trucks are rare at this size, so there may be fewer than 5 to pick from.
    assert 0 < len(before_and_at(load(generated_data), kind)) <= 5


def test_each_pair_is_one_second_apart_on_the_same_record(generated_data):
    for kind in checkpoints.KINDS:
        for before, at in before_and_at(load(generated_data), kind):
            assert (before["moment"], at["moment"]) == ("BEFORE", "AT")
            assert before["record_id"] == at["record_id"]
            assert datetime.fromisoformat(at["as_of_at"]) - datetime.fromisoformat(before["as_of_at"]) == timedelta(seconds=1)


@pytest.mark.parametrize("kind", [checkpoints.KIND_EXPIRY, checkpoints.KIND_CUSTOMER_CANCEL])
def test_a_hold_ending_gives_its_stock_back_and_nothing_else_moves(generated_data, kind):
    for before, at in before_and_at(load(generated_data), kind):
        assert number(before, "reserved") - number(at, "reserved") == number(at, "event_qty")
        assert number(at, "atp") - number(before, "atp") == number(at, "event_qty")
        assert number(before, "on_hand") == number(at, "on_hand")


def test_a_late_truck_cancel_gives_back_at_least_its_own_stock(generated_data):
    """A late truck is handled once for all its holds, so others can end in the same second."""
    for before, at in before_and_at(load(generated_data), checkpoints.KIND_LATE_CANCEL):
        assert number(before, "reserved") - number(at, "reserved") >= number(at, "event_qty")
        assert number(before, "on_hand") == number(at, "on_hand")


def test_a_truck_arriving_leaves_atp_the_same(generated_data):
    """Inbound stock becomes stock on hand, so counting it at both ends would raise ATP here."""
    for before, at in before_and_at(load(generated_data), checkpoints.KIND_ARRIVAL):
        assert number(before, "inbound") >= number(at, "event_qty")
        assert number(before, "inbound") - number(at, "inbound") == number(at, "event_qty")
        assert number(at, "atp") == number(before, "atp")


def test_checkpoints_stay_away_from_every_labelled_case(generated_data):
    labelled_skus = {row["sku_id"] for row in read_csv(export.truth_dir_of(generated_data) / "ground_truth.csv")
                     if row["looks_like"] != "OVERSELL_ATTEMPT"}
    assert not labelled_skus & {row["sku_id"] for row in load(generated_data)}


@pytest.mark.parametrize("name", ["on_hand", "blocked", "reserved", "inbound", "atp", "promisable"])
def test_checker_notices_any_number_that_differs(generated_data, final_cursor, name):
    row = dict(load(generated_data)[0])
    row[name] = number(row, name) + 1
    results, differing = check_atp.check_checkpoints(final_cursor, [row])
    assert results[row["kind"]]["matched"] == 0
    assert list(differing[0]["differences"]) == [name]


def test_checker_notices_a_location_with_no_answer(generated_data, final_cursor):
    row = dict(load(generated_data)[0], location_id="LOC-999999")
    _results, differing = check_atp.check_checkpoints(final_cursor, [row])
    assert "location" in differing[0]["differences"]


def test_a_kind_with_no_checkpoints_is_reported(generated_data, final_cursor):
    only_one_kind = [row for row in load(generated_data) if row["kind"] == checkpoints.KIND_EXPIRY]
    results, _differing = check_atp.check_checkpoints(final_cursor, only_one_kind)
    assert check_atp.missing_kinds(results) == [checkpoints.KIND_ARRIVAL, checkpoints.KIND_CUSTOMER_CANCEL,
                                                checkpoints.KIND_LATE_CANCEL]


def test_the_command_fails_on_a_changed_checkpoint(generated_data, final_database, tmp_path):
    (tmp_path / "ground_truth" / "tiny").mkdir(parents=True)
    rows = load(generated_data)
    rows[0]["atp"] = number(rows[0], "atp") + 1
    with open(tmp_path / "ground_truth" / "tiny" / "atp_checkpoints.csv", "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    environment = dict(os.environ, DB_NAME=FINAL_DB)
    result = subprocess.run([sys.executable, "-m", "eval.check_atp", "--data", str(tmp_path / "tiny")],
                            cwd=config.BACKEND_DIR, env=environment, capture_output=True, text=True)

    assert result.returncode == 1
    assert "DIFFERS" in result.stdout


def test_manifest_names_the_checkpoint_file_by_checksum_only(generated_data):
    text = (generated_data / "source_manifest.json").read_text(encoding="utf-8")
    assert "atp_checkpoints_file" in text
    for row in load(generated_data):
        assert row["record_id"] not in text


# ------------------------------------------------------------ hand-written numbers

T = datetime(2026, 3, 1, 10)
PAIR = {"sku_id": "SKU-1", "location_id": "LOC-1", "opening_on_hand": 10, "opening_blocked": 0, "ranged_from": T.date(),
        "sku": {"effective_from": T.date(), "effective_to": None}, "rule": {"allow_inbound_atp": True}}


class SmallLedger:
    """One pair, one hold and one truck, so each rule can be checked at its exact second."""

    def __init__(self, hold_events=(), truck_events=(), expires_at=T + timedelta(minutes=30), eta_at=T + timedelta(hours=5)):
        self.pairs = [PAIR]
        self.reserved = {0: 0}
        self.movements = []
        self.holds = [{"index": 0, "pair_index": 0, "status": "ACTIVE", "qty": 3, "expires_at": expires_at}]
        self.reservation_events = [(moment, 0, None, status, "TEST", None) for moment, status in hold_events]
        self.shipments = [{"shipment_type": "TRANSFER", "destination_index": 0, "qty": 4, "original_eta_at": eta_at}]
        self.shipment_events = [(moment, 0, None, status, "TEST", None) for moment, status in truck_events]
        self.channel_locations = {(checkpoints.ARRIVAL_CHANNEL, "LOC-1"): {"effective_from": T.date(), "buffer_units": 0}}


def test_a_hold_stops_counting_at_its_expiry_even_with_no_expired_event_yet():
    """A late expiry job can't overstate what is reserved."""
    ledger = SmallLedger(hold_events=[(T, "ACTIVE")])
    timeline = checkpoints.Timeline(ledger)
    expires_at = ledger.holds[0]["expires_at"]
    assert checkpoints.reserved_at(timeline, 0, expires_at - timedelta(seconds=1)) == 3
    assert checkpoints.reserved_at(timeline, 0, expires_at) == 0


def test_a_truck_counts_until_its_eta_and_not_after():
    ledger = SmallLedger(truck_events=[(T, "IN_TRANSIT")])
    timeline = checkpoints.Timeline(ledger)
    eta_at = ledger.shipments[0]["original_eta_at"]
    assert checkpoints.inbound_at(ledger, timeline, 0, eta_at) == 4
    assert checkpoints.inbound_at(ledger, timeline, 0, eta_at + timedelta(seconds=1)) == 0


def test_only_a_truck_still_counted_as_inbound_is_picked_as_an_arrival():
    """An overdue truck was already out of ATP, so it can't show whether arriving double counts."""
    on_time = T + timedelta(hours=4)
    overdue = T + timedelta(hours=6)
    for arrived_at, picked in ((on_time, 1), (overdue, 0)):
        ledger = SmallLedger(truck_events=[(T, "IN_TRANSIT"), (arrived_at, "RECEIVED")])
        assert len(checkpoints.arrivals_to_check(ledger, checkpoints.Timeline(ledger), set(), 5)) == picked


def test_evenly_spaced_takes_at_most_what_was_asked():
    assert checkpoints.evenly_spaced(list(range(10)), 3) == [0, 3, 6]
    assert checkpoints.evenly_spaced([1, 2], 5) == [1, 2]
    assert checkpoints.evenly_spaced([], 5) == []
