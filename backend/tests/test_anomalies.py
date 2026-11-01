"""
The labelled problems: exact totals, ground truth kept apart, each case causing only its
own problem, and the detectors scored against the answers.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta

import pytest

from app import atp, config, detect, orders
from eval import check_failures, score
from generator import export, inject, settings
from scripts import compare_runs, validate
from scripts.generate import generate
from tests.conftest import (FINAL_DB, SKU_A, SMALL_CASES_PER_TYPE, SMALL_MOVEMENTS, SMALL_ORDERS, TEST_SEED,
                            WAREHOUSE_SOUTH, add_stock, connect, small_scale)

SECOND = timedelta(seconds=1)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def truth_rows(data_dir):
    return score.load_ground_truth(export.truth_dir_of(data_dir))


def labelled(data_dir, issue):
    return {row["record_id"] for row in truth_rows(data_dir) if row["expected_issue"] == issue}


def decoys(data_dir, issue):
    return {row["record_id"] for row in truth_rows(data_dir)
            if row["expected_issue"] == "NONE" and row["looks_like"] == issue}


def manifest_of(folder):
    return json.loads((folder / "source_manifest.json").read_text(encoding="utf-8"))


def movement_content(row):
    """A movement with its numbering taken away, so two datasets can be compared."""
    return (row["sku_id"], row["location_id"], row["movement_type"], row["stock_bucket"], row["quantity"],
            row["occurred_at"], row["reason_code"], row["movement_group_id"])


# ------------------------------------------------------------ exact totals

def test_final_data_hits_the_exact_targets(generated_data):
    assert len(read_csv(generated_data / "inventory_movement.csv")) == SMALL_MOVEMENTS
    assert len(read_csv(generated_data / "orders.csv")) == SMALL_ORDERS


def test_clean_baseline_is_smaller_by_exactly_what_gets_injected(generated_data):
    clean_dir = export.clean_dir_of(generated_data)
    per_kind = 2 * SMALL_CASES_PER_TYPE
    assert len(read_csv(clean_dir / "inventory_movement.csv")) == SMALL_MOVEMENTS - per_kind * 3
    assert len(read_csv(clean_dir / "orders.csv")) == SMALL_ORDERS - per_kind * 2


def test_presets_ask_for_the_counts_in_the_plan():
    assert settings.SCALES["starter"]["anomalies_per_type"] == 5
    assert settings.SCALES["starter"]["decoys_per_type"] == 5
    assert settings.SCALES["full"]["anomalies_per_type"] == 50
    assert settings.SCALES["full"]["decoys_per_type"] == 50


def test_clean_scale_leaves_room_for_exactly_the_injected_rows():
    clean = settings.clean_scale(settings.get_scale("full"))
    assert clean["movements"] == 1_000_000 - 300
    assert clean["orders"] == 50_000 - 200


def test_manifest_says_which_dataset_it_is(generated_data):
    assert manifest_of(generated_data)["dataset"] == "final"
    assert manifest_of(export.clean_dir_of(generated_data))["dataset"] == "clean baseline"


@pytest.mark.parametrize("issue", settings.ISSUE_TYPES)
def test_each_type_has_its_anomalies_and_decoys(generated_data, issue):
    assert len(labelled(generated_data, issue)) == SMALL_CASES_PER_TYPE
    assert len(decoys(generated_data, issue)) == SMALL_CASES_PER_TYPE


def test_generator_refuses_when_it_cannot_place_every_case(tmp_path):
    scale = dict(small_scale(), anomalies_per_type=500)
    with pytest.raises(RuntimeError, match="could not place"):
        generate(scale, TEST_SEED, tmp_path / "too_many")


# ------------------------------------------------------------ ground truth stays apart

def test_ground_truth_is_not_in_the_folder_the_loader_reads(generated_data):
    names = {path.name for path in generated_data.iterdir()}
    assert "ground_truth.csv" not in names
    assert (export.truth_dir_of(generated_data) / "ground_truth.csv").exists()


def test_no_input_file_has_a_label_column(generated_data):
    for columns in export.TABLE_COLUMNS.values():
        assert not {"issue_type", "expected_issue", "looks_like", "is_decoy"} & set(columns)


def test_manifest_holds_no_answers(generated_data):
    text = (generated_data / "source_manifest.json").read_text(encoding="utf-8")
    for row in truth_rows(generated_data):
        assert row["record_id"] not in text


@pytest.mark.parametrize("folder", ["app", "generator/simulate.py", "generator/order_flow.py", "scripts/validate.py",
                                    "scripts/load_data.py", "scripts/run_detect.py"])
def test_detectors_and_inputs_never_read_the_answers(folder):
    path = config.BACKEND_DIR / folder
    files = path.glob("*.py") if path.is_dir() else [path]
    for file in files:
        text = file.read_text(encoding="utf-8")
        assert "ground_truth" not in text, file
        assert "from eval" not in text and "import eval" not in text, file


def test_detectors_are_given_nothing_from_the_generator():
    text = (config.BACKEND_DIR / "app" / "detect.py").read_text(encoding="utf-8")
    assert "generator" not in text


# ------------------------------------------------------------ each case causes only its own problem

def test_every_clean_movement_is_still_there_unchanged(generated_data):
    clean = Counter(movement_content(row) for row in read_csv(export.clean_dir_of(generated_data) / "inventory_movement.csv"))
    final = Counter(movement_content(row) for row in read_csv(generated_data / "inventory_movement.csv"))
    assert not clean - final
    assert sum((final - clean).values()) == 2 * SMALL_CASES_PER_TYPE * 3


def test_the_only_new_movements_belong_to_labelled_cases(generated_data):
    clean = Counter(movement_content(row) for row in read_csv(export.clean_dir_of(generated_data) / "inventory_movement.csv"))
    truth = truth_rows(generated_data)
    movement_ids = {r["record_id"] for r in truth if r["record_type"] == "movement"}
    shipment_ids = {r["record_id"] for r in truth if r["record_type"] == "shipment"}

    found = 0
    for row in read_csv(generated_data / "inventory_movement.csv"):
        content = movement_content(row)
        if clean[content] > 0:
            clean[content] -= 1
            continue
        assert row["movement_id"] in movement_ids or row["shipment_id"] in shipment_ids, row
        found += 1
    assert found == 2 * SMALL_CASES_PER_TYPE * 3


def test_every_clean_order_is_still_there_unchanged(generated_data):
    def content(row):
        return (row["channel_code"], row["customer_ref"], row["ship_to_region"], row["placed_at"], row["status"],
                row["order_total"])
    clean = Counter(content(row) for row in read_csv(export.clean_dir_of(generated_data) / "orders.csv"))
    final = Counter(content(row) for row in read_csv(generated_data / "orders.csv"))
    assert not clean - final
    assert sum((final - clean).values()) == 2 * SMALL_CASES_PER_TYPE * 2


def test_master_data_is_the_same_in_both(generated_data):
    clean_dir = export.clean_dir_of(generated_data)
    for table in ("product", "sku", "location", "channel", "channel_location", "sku_location", "replenishment_rule"):
        assert (generated_data / f"{table}.csv").read_bytes() == (clean_dir / f"{table}.csv").read_bytes()


def test_opening_balances_are_the_same_in_both(generated_data):
    def opening(folder):
        return {(r["sku_id"], r["location_id"]): (r["on_hand"], r["blocked"])
                for r in read_csv(folder / "inventory_balance.csv") if r["snapshot_type"] == "OPENING"}
    assert opening(generated_data) == opening(export.clean_dir_of(generated_data))


def test_each_case_is_on_a_sku_of_its_own(generated_data, final_cursor):
    """If two cases shared a SKU they could interact, and neither would be cleanly its own."""
    skus = []
    for row in truth_rows(generated_data):
        if row["looks_like"] == "OVERSELL_ATTEMPT":
            continue
        record_id = row["record_id"]
        sql = {
            "balance": "SELECT sku_id FROM inventory_balance WHERE balance_id = %s",
            "reservation": "SELECT sku_id FROM reservation WHERE reservation_id = %s",
            "movement": "SELECT sku_id FROM inventory_movement WHERE movement_id = %s",
            "shipment": "SELECT transfer_sku_id FROM shipment WHERE shipment_id = %s",
        }[row["record_type"]]
        final_cursor.execute(sql, (record_id,))
        skus.append(final_cursor.fetchone()[0])
    assert len(skus) == len(set(skus))


def test_only_labelled_decisions_differ_from_sql_across_the_whole_final_dataset(generated_data, final_cursor):
    """Every decision, not a sample. Decoys and every normal order must come out the same."""
    final_cursor.execute("SELECT COUNT(*) FROM allocation_decision")
    result = validate.recheck_decisions(final_cursor, sample=final_cursor.fetchone()[0])[0]
    differing_orders = {line.split("-L")[0] for line in result["records"]}
    labelled_orders = {r["order_id"] for r in truth_rows(generated_data)
                       if r["expected_issue"] == settings.ISSUE_RESERVATION}
    assert differing_orders == labelled_orders


# ------------------------------------------------------------ numbering gives nothing away

def test_movement_ids_and_documents_are_in_time_order(generated_data):
    rows = read_csv(generated_data / "inventory_movement.csv")
    assert [row["movement_id"] for row in rows] == [f"MOV-2026-{n:08d}" for n in range(1, len(rows) + 1)]
    assert [row["occurred_at"] for row in rows] == sorted(row["occurred_at"] for row in rows)
    last_seen = {}
    for row in rows:
        prefix, _year, number = row["source_doc"].split("-")
        assert int(number) >= last_seen.get(prefix, 0)
        last_seen[prefix] = int(number)


def test_order_and_shipment_ids_are_in_time_order(generated_data):
    orders = read_csv(generated_data / "orders.csv")
    assert [row["order_id"] for row in orders] == [f"ORD-2026-{n:06d}" for n in range(1, len(orders) + 1)]
    assert [row["placed_at"] for row in orders] == sorted(row["placed_at"] for row in orders)
    shipments = read_csv(generated_data / "shipment.csv")
    assert [row["created_at"] for row in shipments] == sorted(row["created_at"] for row in shipments)


def test_labelled_orders_are_spread_through_the_period(generated_data):
    """If they all sat at the end, the date alone would give them away."""
    orders = read_csv(generated_data / "orders.csv")
    position = {row["order_id"]: index / len(orders) for index, row in enumerate(orders)}
    labelled_positions = sorted(position[r["order_id"]] for r in truth_rows(generated_data) if r["order_id"]
                                and r["looks_like"] != "OVERSELL_ATTEMPT")
    assert labelled_positions[len(labelled_positions) // 2] < 0.75
    assert labelled_positions[0] < 0.25


def test_anomalies_and_decoys_are_spread_alike(generated_data):
    """If the anomalies sat earlier than their decoys, the date would separate those too."""
    orders = read_csv(generated_data / "orders.csv")
    position = {row["order_id"]: index / len(orders) for index, row in enumerate(orders)}
    truth = [r for r in truth_rows(generated_data) if r["order_id"] and r["looks_like"] != "OVERSELL_ATTEMPT"]
    anomalies = [position[r["order_id"]] for r in truth if r["expected_issue"] != "NONE"]
    decoy_positions = [position[r["order_id"]] for r in truth if r["expected_issue"] == "NONE"]
    assert abs(sum(anomalies) / len(anomalies) - sum(decoy_positions) / len(decoy_positions)) < 0.3


def test_sku_stops_getting_orders_on_its_stop_date(generated_data):
    placed_after = {}
    orders = {row["order_id"]: row["placed_at"] for row in read_csv(generated_data / "orders.csv")}
    for line in read_csv(generated_data / "order_line.csv"):
        placed_after.setdefault(line["sku_id"], []).append(orders[line["order_id"]])
    # Half of the SKUs stop at a random date, so plenty have no orders late in the period.
    quiet_late = [sku for sku, times in placed_after.items() if max(times) < "2026-08-01"]
    assert len(quiet_late) > 0


def test_reservation_file_is_sorted_by_id(generated_data):
    ids = [row["reservation_id"] for row in read_csv(generated_data / "reservation.csv")]
    assert ids == sorted(ids)


# ------------------------------------------------------------ reproducibility

def test_same_seed_gives_identical_files_in_every_folder(tmp_path, capsys):
    generate(small_scale(), TEST_SEED, tmp_path / "a")
    generate(small_scale(), TEST_SEED, tmp_path / "b")
    files_a = compare_runs.files_of_run(tmp_path / "a")
    files_b = compare_runs.files_of_run(tmp_path / "b")
    assert "ground_truth/ground_truth.csv" in files_a
    assert "ground_truth/atp_checkpoints.csv" in files_a
    assert "clean/inventory_movement.csv" in files_a
    assert "final/source_manifest.json" in files_a
    assert compare_runs.compare(files_a, files_b) == []


def test_compare_notices_a_changed_or_missing_file(tmp_path, capsys):
    generate(small_scale(), TEST_SEED, tmp_path / "a")
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    shutil.copytree(tmp_path / "a_clean", tmp_path / "b_clean")
    shutil.copytree(tmp_path / "ground_truth" / "a", tmp_path / "ground_truth" / "b")
    truth = tmp_path / "ground_truth" / "b" / "ground_truth.csv"
    truth.write_text(truth.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    (tmp_path / "b_clean" / "sku.csv").unlink()

    different = compare_runs.compare(compare_runs.files_of_run(tmp_path / "a"), compare_runs.files_of_run(tmp_path / "b"))

    assert different == ["clean/sku.csv", "ground_truth/ground_truth.csv"]


# ------------------------------------------------------------ the rules of placing a case

def test_lowest_from_here_looks_only_forward():
    assert inject.lowest_from_here([5, 2, 7, 3, 9]) == [2, 2, 3, 3, 9]


def history_of(times_and_sellable, blocked=0):
    times = [time for time, _sellable in times_and_sellable]
    sellable = [amount for _time, amount in times_and_sellable]
    return {0: {"times": times, "sellable": sellable, "blocked": [blocked] * len(times),
                "lowest_sellable": inject.lowest_from_here(sellable),
                "lowest_blocked": inject.lowest_from_here([blocked] * len(times))}}


T = datetime(2026, 3, 1, 10)


def test_stock_at_reads_the_last_row_up_to_that_moment():
    history = history_of([(T, 10), (T + timedelta(hours=1), 4)])
    assert inject.stock_at(history, 0, T + timedelta(minutes=59))[0] == 10
    assert inject.stock_at(history, 0, T + timedelta(hours=1))[0] == 4


def test_spare_is_what_can_leave_without_a_later_row_going_below_zero():
    history = history_of([(T, 10), (T + timedelta(hours=1), 2), (T + timedelta(hours=2), 8)])
    assert inject.spare_sellable(history, 0, T) == 2
    assert inject.spare_sellable(history, 0, T + timedelta(hours=2)) == 8


def test_is_still_sees_a_movement_inside_the_window():
    history = history_of([(T, 10), (T + timedelta(hours=1), 4)])
    assert not inject.is_still(history, 0, T, T + timedelta(hours=2))
    assert inject.is_still(history, 0, T + timedelta(hours=1), T + timedelta(hours=3))


def test_truck_overlap_counts_either_end_of_the_window():
    trucks = [(T, T + timedelta(hours=5))]
    assert inject.has_truck_on_the_road(trucks, T - timedelta(hours=1), T)
    assert inject.has_truck_on_the_road(trucks, T + timedelta(hours=5), T + timedelta(hours=9))
    assert not inject.has_truck_on_the_road(trucks, T + timedelta(hours=5, seconds=1), T + timedelta(hours=9))


def test_a_sku_with_a_hold_still_open_is_never_quiet():
    class Ledger:
        sku_ids = ["SKU-1"]
        holds = [{"sku_id": "SKU-1", "status": "CONFIRMED"}]
        reservation_events = [(T, 0)]
    assert inject.build_quiet_times(Ledger)["SKU-1"] == config.AS_OF


# ------------------------------------------------------------ the detectors

@pytest.mark.parametrize("issue", settings.ISSUE_TYPES)
def test_detector_finds_exactly_what_is_labelled(generated_data, final_cursor, issue):
    found = {e["record_id"] for e in detect.find_exceptions(final_cursor) if e["issue_type"] == issue}
    assert found == labelled(generated_data, issue)


@pytest.mark.parametrize("issue", settings.ISSUE_TYPES)
def test_detector_flags_no_decoy(generated_data, final_cursor, issue):
    found = {e["record_id"] for e in detect.find_exceptions(final_cursor) if e["issue_type"] == issue}
    assert decoys(generated_data, issue)
    assert not found & decoys(generated_data, issue)


def test_detectors_find_nothing_on_the_clean_baseline(cursor):
    assert detect.find_exceptions(cursor) == []


def test_detector_reports_the_labelled_expected_and_actual_values(generated_data, final_cursor):
    truth = {r["record_id"]: r for r in truth_rows(generated_data)}
    for exception in detect.find_exceptions(final_cursor):
        row = truth[exception["record_id"]]
        assert (exception["expected_value"], exception["actual_value"]) == (
            int(row["expected_value"]), int(row["actual_value"]))
        assert exception["recommended_action"] == row["expected_decision"]


def test_detectors_write_one_exception_per_finding_and_can_run_twice(final_cursor):
    exceptions = detect.find_exceptions(final_cursor)
    detect.write_exceptions(final_cursor, exceptions)
    detect.write_exceptions(final_cursor, exceptions)
    final_cursor.execute("SELECT COUNT(*) FROM inventory_exception")
    assert final_cursor.fetchone()[0] == 4 * SMALL_CASES_PER_TYPE


def test_each_exception_points_at_a_real_record(final_cursor):
    detect.write_exceptions(final_cursor, detect.find_exceptions(final_cursor))
    for table, column in (("inventory_balance", "balance_id"), ("reservation", "reservation_id"),
                          ("inventory_movement", "movement_id"), ("shipment", "shipment_id")):
        final_cursor.execute(f"""SELECT COUNT(*) FROM inventory_exception e JOIN {table} t USING ({column})""")
        assert final_cursor.fetchone()[0] == SMALL_CASES_PER_TYPE


def test_detector_catches_a_new_problem_nobody_labelled(generated_data, final_cursor):
    """It works from the data, not from a list. A new bad row is found like any other."""
    final_cursor.execute("""SELECT sku_id, location_id FROM inventory_balance
                            WHERE snapshot_type = 'CLOSING' AND blocked > 0 ORDER BY 1, 2 LIMIT 1""")
    sku_id, location_id = final_cursor.fetchone()
    final_cursor.execute(
        """INSERT INTO inventory_movement (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
                                           occurred_at, reason_code, source_doc)
           VALUES ('MOV-2026-99999999', %s, %s, 'SALE', 'BLOCKED', -1, '2026-09-30 23:00:00', 'TEST', 'TEST-1')""",
        (sku_id, location_id),
    )
    found = {e["record_id"] for e in detect.find_exceptions(final_cursor)
             if e["issue_type"] == settings.ISSUE_BLOCKED}
    assert found == labelled(generated_data, settings.ISSUE_BLOCKED) | {"MOV-2026-99999999"}


def second_hold_on_the_same_stock(orders_db, placed_at, second_at, stock_qty=5, first_qty=5, second_qty=1):
    """A real order, then a hold written straight in with no check, as a buggy system might."""
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, stock_qty)
    connection.close()
    connection = connect()
    order = orders.place_order(connection, "WEB", "CUST-1", "SOUTH", [{"sku_id": SKU_A, "qty": first_qty}], placed_at)
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO reservation (reservation_id, order_line_id, sku_id, location_id, qty, supply_type,
                                        created_at, expires_at, status)
               VALUES ('RES-SECOND', %s, %s, %s, %s, 'ON_HAND', %s, %s, 'ACTIVE')""",
            (order["lines"][0]["order_line_id"], SKU_A, WAREHOUSE_SOUTH, second_qty, second_at,
             second_at + timedelta(minutes=30)),
        )
        cursor.execute(
            """INSERT INTO reservation_event (event_id, reservation_id, to_status, occurred_at, reason_code)
               VALUES (nextval('event_number'), 'RES-SECOND', 'ACTIVE', %s, 'TEST')""",
            (second_at,),
        )
        exceptions = detect.find_exceptions(cursor)
    connection.close()
    return [(e["record_id"], e["expected_value"], e["actual_value"]) for e in exceptions]


def test_detector_counts_stock_already_held_by_someone_else(orders_db):
    """On its own the stock would cover this hold. It is the other hold that uses it up."""
    assert second_hold_on_the_same_stock(orders_db, T, T + timedelta(minutes=10)) == [("RES-SECOND", 0, 1)]


def test_detector_does_not_count_a_hold_that_has_already_expired(orders_db):
    assert second_hold_on_the_same_stock(orders_db, T, T + timedelta(minutes=30)) == []


def test_detector_allows_a_hold_of_exactly_what_is_left(orders_db):
    assert second_hold_on_the_same_stock(orders_db, T, T + timedelta(minutes=10), first_qty=4) == []


# ------------------------------------------------------------ what each problem actually does

def test_blocked_sale_lowers_on_hand_and_blocked_and_leaves_atp_alone(generated_data, final_cursor):
    movement_id = sorted(labelled(generated_data, settings.ISSUE_BLOCKED))[0]
    final_cursor.execute("""SELECT sku_id, location_id, occurred_at, quantity, stock_bucket
                            FROM inventory_movement WHERE movement_id = %s""", (movement_id,))
    sku_id, location_id, occurred_at, quantity, bucket = final_cursor.fetchone()
    assert bucket == "BLOCKED"
    before = atp.get_stock(final_cursor, sku_id, location_id, occurred_at - SECOND)
    after = atp.get_stock(final_cursor, sku_id, location_id, occurred_at)
    assert after == (before[0] + quantity, before[1] + quantity)
    assert after[0] - after[1] == before[0] - before[1]


def test_blocked_decoy_sells_from_the_shelf_where_blocked_stock_also_sits(generated_data, final_cursor):
    movement_id = sorted(decoys(generated_data, settings.ISSUE_BLOCKED))[0]
    final_cursor.execute("""SELECT sku_id, location_id, occurred_at, stock_bucket
                            FROM inventory_movement WHERE movement_id = %s""", (movement_id,))
    sku_id, location_id, occurred_at, bucket = final_cursor.fetchone()
    assert bucket == "SELLABLE"
    assert atp.get_stock(final_cursor, sku_id, location_id, occurred_at)[1] > 0


def transfer_times(cursor, shipment_id):
    cursor.execute("""SELECT movement_type, occurred_at, sku_id, quantity FROM inventory_movement
                      WHERE shipment_id = %s""", (shipment_id,))
    rows = {row[0]: row for row in cursor.fetchall()}
    return rows["TRANSFER_OUT"], rows["TRANSFER_IN"]


def test_double_counted_transfer_really_counts_at_both_ends_until_it_leaves(generated_data, final_cursor):
    shipment_id = sorted(labelled(generated_data, settings.ISSUE_TRANSFER))[0]
    out_row, in_row = transfer_times(final_cursor, shipment_id)
    sku_id, qty = in_row[2], in_row[3]
    assert in_row[1] < out_row[1]

    before = atp.get_network_on_hand(final_cursor, sku_id, in_row[1] - SECOND)
    during = atp.get_network_on_hand(final_cursor, sku_id, in_row[1])
    after = atp.get_network_on_hand(final_cursor, sku_id, out_row[1])
    assert during == before + qty
    assert after == before


def test_transfer_decoy_arrives_one_second_after_it_leaves(generated_data, final_cursor):
    for shipment_id in decoys(generated_data, settings.ISSUE_TRANSFER):
        out_row, in_row = transfer_times(final_cursor, shipment_id)
        assert in_row[1] - out_row[1] == SECOND


def test_changed_balance_does_not_change_atp(generated_data, final_cursor):
    """ATP is worked out from the ledger, so a wrong stored number can't affect a promise."""
    balance_id = sorted(labelled(generated_data, settings.ISSUE_BALANCE))[0]
    final_cursor.execute("SELECT sku_id, location_id, on_hand FROM inventory_balance WHERE balance_id = %s", (balance_id,))
    sku_id, location_id, stored = final_cursor.fetchone()
    assert atp.get_stock(final_cursor, sku_id, location_id, config.AS_OF)[0] != stored


def test_balance_decoy_really_nets_to_zero_and_really_moved(generated_data, final_cursor):
    for balance_id in decoys(generated_data, settings.ISSUE_BALANCE):
        final_cursor.execute("""SELECT c.sku_id, c.location_id, c.on_hand, o.on_hand
                                FROM inventory_balance c JOIN inventory_balance o
                                  ON o.sku_id = c.sku_id AND o.location_id = c.location_id AND o.snapshot_type = 'OPENING'
                                WHERE c.balance_id = %s""", (balance_id,))
        sku_id, location_id, closing, opening = final_cursor.fetchone()
        assert closing == opening
        final_cursor.execute("SELECT COUNT(*) FROM inventory_movement WHERE sku_id = %s AND location_id = %s",
                             (sku_id, location_id))
        assert final_cursor.fetchone()[0] > 0


def test_reservation_decoy_is_exactly_what_could_be_promised(generated_data):
    for row in truth_rows(generated_data):
        if row["expected_issue"] == "NONE" and row["looks_like"] == settings.ISSUE_RESERVATION:
            assert row["expected_value"] == row["actual_value"]


def test_labelled_reservation_is_above_what_could_be_promised(generated_data):
    for row in truth_rows(generated_data):
        if row["expected_issue"] == settings.ISSUE_RESERVATION:
            assert int(row["actual_value"]) > int(row["expected_value"])


def test_every_oversell_attempt_was_rejected(generated_data, final_cursor):
    attempts = [r["record_id"] for r in truth_rows(generated_data) if r["looks_like"] == "OVERSELL_ATTEMPT"]
    assert attempts
    final_cursor.execute("SELECT DISTINCT status FROM reservation WHERE reservation_id = ANY(%s)", (attempts,))
    assert final_cursor.fetchall() == [("REJECTED",)]


# ------------------------------------------------------------ scoring

TRUTH = [
    {"expected_issue": "FULFILLED_FROM_BLOCKED", "looks_like": "FULFILLED_FROM_BLOCKED", "record_id": "A"},
    {"expected_issue": "FULFILLED_FROM_BLOCKED", "looks_like": "FULFILLED_FROM_BLOCKED", "record_id": "B"},
    {"expected_issue": "NONE", "looks_like": "FULFILLED_FROM_BLOCKED", "record_id": "DECOY"},
]


def test_score_is_perfect_when_exactly_the_labelled_are_flagged():
    result = score.score_issue("FULFILLED_FROM_BLOCKED", {"A", "B"}, TRUTH)
    assert (result["precision"], result["recall"], result["f1"]) == (1.0, 1.0, 1.0)


def test_flagging_a_decoy_costs_precision_and_is_counted():
    result = score.score_issue("FULFILLED_FROM_BLOCKED", {"A", "B", "DECOY"}, TRUTH)
    assert result["false_positives"] == 1
    assert result["decoys_flagged"] == 1
    assert result["precision"] == round(2 / 3, 4)
    assert result["recall"] == 1.0


def test_missing_one_costs_recall():
    result = score.score_issue("FULFILLED_FROM_BLOCKED", {"A"}, TRUTH)
    assert result["false_negatives"] == 1
    assert result["recall"] == 0.5
    assert result["precision"] == 1.0


def test_a_detector_that_flags_nothing_has_no_precision_score():
    result = score.score_issue("FULFILLED_FROM_BLOCKED", set(), TRUTH)
    assert result["precision"] is None
    assert result["recall"] == 0.0


def test_scores_on_the_generated_data_come_from_real_detection(generated_data, final_cursor):
    exceptions = [(e["issue_type"], e["record_id"]) for e in detect.find_exceptions(final_cursor)]
    results = score.score(exceptions, truth_rows(generated_data))
    assert set(results) == set(settings.ISSUE_TYPES)
    for result in results.values():
        assert (result["labelled"], result["decoys"]) == (SMALL_CASES_PER_TYPE, SMALL_CASES_PER_TYPE)
        assert (result["precision"], result["recall"], result["decoys_flagged"]) == (1.0, 1.0, 0)


# ------------------------------------------------------------ expected and unexpected failures

BALANCE_CHECK = "closing balance does not reconcile to opening plus ledger"
NETWORK_CHECK = "network stock plus in transit differs from opening plus outside changes"
BLOCKED_CHECK = "order sale from the blocked bucket"
SAMPLE_CHECK = "sample of 200 of 900 order decisions"
OTHER_CHECK = "stock below zero at any point in the ledger"


def result_of(check, violations, records=None):
    result = validate.make_result(check, violations)
    if records is not None:
        result["records"] = records
    return result


def small_truth():
    """Two labelled records of each type, and one decoy that looks like a blocked sale."""
    rows = []
    for issue in settings.ISSUE_TYPES:
        for number in (1, 2):
            rows.append({"expected_issue": issue, "looks_like": issue, "record_id": f"{issue}-{number}",
                         "sku_id": f"SKU-{issue}-{number}", "order_id": f"ORD-{issue}-{number}"})
    rows.append({"expected_issue": "NONE", "looks_like": settings.ISSUE_BLOCKED, "record_id": "DECOY",
                 "sku_id": "SKU-DECOY", "order_id": "ORD-DECOY"})
    return rows


def ids_for(check, truth):
    issue, column = check_failures.EXPECTED_CHECKS[check]
    return [row[column] for row in truth if row["expected_issue"] == issue]


def full_results(truth=None):
    """A run where every required check is there, and exactly the labelled records fail."""
    truth = truth or small_truth()
    results = []
    for name in validate.required_check_names():
        if name in check_failures.EXPECTED_CHECKS:
            records = ids_for(name, truth)
            results.append(result_of(name, len(records), records))
        else:
            results.append(result_of(name, 0))
    results.append(result_of(SAMPLE_CHECK, 0, []))
    return results


def with_result(results, check, violations, records=None):
    return [result_of(check, violations, records) if r["check"] == check else r for r in results]


def names(entries):
    return [entry["check"] for entry in entries]


def unexpected_of(results, truth=None):
    return check_failures.classify(results, truth or small_truth())[2]


def test_failures_on_exactly_the_labelled_ids_are_expected():
    _passed, expected, unexpected = check_failures.classify(full_results(), small_truth())
    assert unexpected == []
    assert names(expected) == list(check_failures.EXPECTED_CHECKS)


def test_any_other_failure_is_unexpected():
    results = with_result(full_results(), OTHER_CHECK, 1)
    assert names(unexpected_of(results)) == [OTHER_CHECK]


def test_a_missed_label_and_an_unrelated_failure_do_not_cancel_out():
    """The count is right, 2 of 2, and it is still wrong. This is what counts alone would miss."""
    labelled = ids_for(BLOCKED_CHECK, small_truth())
    results = with_result(full_results(), BLOCKED_CHECK, 2, [labelled[0], "MOV-NOT-LABELLED"])
    unexpected = unexpected_of(results)
    assert names(unexpected) == [BLOCKED_CHECK]
    assert unexpected[0]["missed"] == [labelled[1]]
    assert unexpected[0]["unrelated"] == ["MOV-NOT-LABELLED"]


def test_one_more_record_than_labelled_is_unexpected():
    records = ids_for(BLOCKED_CHECK, small_truth()) + ["MOV-NOT-LABELLED"]
    results = with_result(full_results(), BLOCKED_CHECK, 3, records)
    assert unexpected_of(results)[0]["unrelated"] == ["MOV-NOT-LABELLED"]


def test_a_labelled_problem_the_validator_missed_is_unexpected():
    results = with_result(full_results(), BLOCKED_CHECK, 0, [])
    unexpected = unexpected_of(results)
    assert names(unexpected) == [BLOCKED_CHECK]
    assert unexpected[0]["missed"] == sorted(ids_for(BLOCKED_CHECK, small_truth()))


def test_a_flagged_decoy_is_unexpected():
    records = ids_for(BLOCKED_CHECK, small_truth())[:1] + ["DECOY"]
    results = with_result(full_results(), BLOCKED_CHECK, 2, records)
    assert unexpected_of(results)[0]["unrelated"] == ["DECOY"]


def test_the_network_check_is_compared_by_sku():
    """It adds up a whole SKU, so it can't name a balance row. A balance ID there is wrong."""
    balance_ids = ids_for(BALANCE_CHECK, small_truth())
    results = with_result(full_results(), NETWORK_CHECK, 2, balance_ids)
    assert names(unexpected_of(results)) == [NETWORK_CHECK]


def test_a_check_that_reports_no_ids_is_unexpected():
    results = with_result(full_results(), BLOCKED_CHECK, 2)
    unexpected = unexpected_of(results)
    assert names(unexpected) == [BLOCKED_CHECK]
    assert "the check did not report which records failed" in unexpected[0]["problems"]


def test_fewer_ids_than_violations_is_unexpected():
    labelled = ids_for(BLOCKED_CHECK, small_truth())
    results = with_result(full_results(), BLOCKED_CHECK, 3, labelled)
    assert "3 violations but 2 record IDs" in unexpected_of(results)[0]["problems"]


def test_the_same_id_twice_is_unexpected():
    labelled = ids_for(BLOCKED_CHECK, small_truth())
    results = with_result(full_results(), BLOCKED_CHECK, 3, labelled + labelled[:1])
    assert "the same record ID is reported twice" in unexpected_of(results)[0]["problems"]


def test_a_blank_reported_id_is_unexpected():
    labelled = ids_for(BLOCKED_CHECK, small_truth())
    results = with_result(full_results(), BLOCKED_CHECK, 3, labelled + [""])
    assert "a reported record ID is blank" in unexpected_of(results)[0]["problems"]


def test_a_labelled_row_with_no_id_is_unexpected():
    truth = small_truth()
    next(row for row in truth if row["expected_issue"] == settings.ISSUE_BLOCKED)["record_id"] = ""
    unexpected = unexpected_of(full_results(truth), truth)
    assert names(unexpected) == [BLOCKED_CHECK]
    assert "a labelled record has no ID" in unexpected[0]["problems"]


def test_nothing_labelled_for_a_check_is_unexpected():
    """Passing with no labels would mean this type was never injected."""
    truth = [row for row in small_truth() if row["expected_issue"] != settings.ISSUE_BLOCKED]
    results = with_result(full_results(truth), BLOCKED_CHECK, 0, [])
    assert "nothing is labelled for this check" in unexpected_of(results, truth)[0]["problems"]


@pytest.mark.parametrize("check", [BLOCKED_CHECK, OTHER_CHECK, "table has rows: orders",
                                   "file matches its manifest checksum: orders.csv",
                                   "duplicate primary key: orders", "value not allowed: orders.status"])
def test_a_required_check_missing_from_the_summary_is_unexpected(check):
    results = [r for r in full_results() if r["check"] != check]
    unexpected = unexpected_of(results)
    assert names(unexpected) == [check]
    assert unexpected[0]["problems"] == ["this check is missing from the summary"]


def test_an_empty_summary_lists_every_required_check_as_missing():
    assert len(unexpected_of([])) == len(validate.required_check_names()) + 1


def test_a_missing_decision_sample_is_unexpected():
    results = [r for r in full_results() if r["check"] != SAMPLE_CHECK]
    assert names(unexpected_of(results)) == ["sample of order decisions"]


def test_a_sampled_decision_differing_is_expected_only_for_a_labelled_order():
    labelled_line = f"ORD-{settings.ISSUE_RESERVATION}-1-L01"
    results = with_result(full_results(), SAMPLE_CHECK, 1, [labelled_line])
    assert unexpected_of(results) == []

    results = with_result(full_results(), SAMPLE_CHECK, 1, ["ORD-OTHER-L01"])
    assert names(unexpected_of(results)) == [SAMPLE_CHECK]


def test_a_sample_labelled_order_of_another_type_is_unexpected():
    """A blocked sale's order is labelled too, but its decision has no reason to be stale."""
    results = with_result(full_results(), SAMPLE_CHECK, 1, [f"ORD-{settings.ISSUE_BLOCKED}-1-L01"])
    assert names(unexpected_of(results)) == [SAMPLE_CHECK]


@pytest.mark.parametrize("records", [None, []])
def test_a_failing_sample_with_no_ids_is_unexpected(records):
    results = with_result(full_results(), SAMPLE_CHECK, 1, records)
    assert names(unexpected_of(results)) == [SAMPLE_CHECK]


def test_required_check_names_are_exactly_what_a_run_reports(cursor, loaded_database):
    manifest = manifest_of(loaded_database)
    results = validate.check_files_match_manifest(loaded_database, manifest) + validate.validate(cursor, manifest)
    reported = [r["check"] for r in results if not r["check"].startswith(check_failures.SAMPLE_CHECK_PREFIX)]
    assert sorted(reported) == sorted(validate.required_check_names())
    assert len(reported) == len(set(reported))


def test_validator_reports_exactly_the_labelled_ids(generated_data, final_cursor):
    records = {r["check"]: r.get("records") for r in validate.run_checks(final_cursor)}
    truth = truth_rows(generated_data)
    for check in check_failures.EXPECTED_CHECKS:
        assert sorted(records[check]) == sorted(ids_for(check, truth))


def run_command(module, data_dir, dbname=None, *extra):
    environment = dict(os.environ, DB_NAME=dbname or config.DB_NAME)
    return subprocess.run([sys.executable, "-m", module, "--data", str(data_dir), *extra],
                          cwd=config.BACKEND_DIR, env=environment, capture_output=True, text=True)


def test_clean_baseline_passes_with_exit_code_zero(loaded_database):
    result = run_command("scripts.validate", loaded_database)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "on the clean baseline data" in result.stdout


def test_final_data_fails_validation_and_says_so(final_database):
    """The failures aren't hidden. The command still exits 1."""
    result = run_command("scripts.validate", final_database, FINAL_DB)
    assert result.returncode == 1
    assert "FAIL" in result.stdout
    assert "on the final data" in result.stdout


def test_summary_says_which_data_and_how_many_decisions_were_recalculated(final_database):
    result = run_command("scripts.validate", final_database, FINAL_DB, "--decisions", "50")
    summary = json.loads((final_database / "validation_summary.json").read_text(encoding="utf-8"))
    assert (summary["dataset"], summary["seed"], summary["scale"]) == ("final", TEST_SEED, "starter")
    assert summary["decisions_checked"] == 50
    assert summary["decisions_total"] > 50
    assert f"only 50 of {summary['decisions_total']}" in result.stdout


def test_every_failure_on_the_final_data_is_explained(final_database):
    run_command("scripts.validate", final_database, FINAL_DB, "--decisions", "100000")
    result = run_command("eval.check_failures", final_database)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 unexpected failures" in result.stdout


def test_an_unexplained_failure_makes_the_command_fail(final_database, tmp_path):
    data_dir = tmp_path / "tiny"
    data_dir.mkdir()
    (tmp_path / "ground_truth").mkdir()
    shutil.copytree(export.truth_dir_of(final_database), tmp_path / "ground_truth" / "tiny")
    summary = {"checks": [result_of("stock below zero at any point in the ledger", 1)]}
    (data_dir / "validation_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    result = run_command("eval.check_failures", data_dir)

    assert result.returncode == 1
    assert "UNEXPECTED" in result.stdout


def copy_for_check_failures(final_database, tmp_path):
    """The real summary and answers in a folder of their own, so a test can change them."""
    run_command("scripts.validate", final_database, FINAL_DB)
    data_dir = tmp_path / "tiny"
    data_dir.mkdir()
    (tmp_path / "ground_truth").mkdir()
    shutil.copytree(export.truth_dir_of(final_database), tmp_path / "ground_truth" / "tiny")
    summary = json.loads((final_database / "validation_summary.json").read_text(encoding="utf-8"))
    return data_dir, summary


def write_summary(data_dir, summary):
    (data_dir / "validation_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def test_real_summary_with_one_id_swapped_makes_the_command_fail(final_database, tmp_path):
    data_dir, summary = copy_for_check_failures(final_database, tmp_path)
    check = next(r for r in summary["checks"] if r["check"] == BLOCKED_CHECK)
    check["records"][0] = "MOV-2026-99999999"
    write_summary(data_dir, summary)

    result = run_command("eval.check_failures", data_dir)

    assert result.returncode == 1
    assert "1 labelled records were not caught" in result.stdout
    assert "MOV-2026-99999999" in result.stdout


def test_real_summary_with_a_check_removed_makes_the_command_fail(final_database, tmp_path):
    data_dir, summary = copy_for_check_failures(final_database, tmp_path)
    summary["checks"] = [r for r in summary["checks"] if r["check"] != OTHER_CHECK]
    write_summary(data_dir, summary)

    result = run_command("eval.check_failures", data_dir)

    assert result.returncode == 1
    assert "this check is missing from the summary" in result.stdout


def test_recheck_sample_is_exactly_the_size_asked_for(cursor):
    """So "200 decisions" means 200, and not whatever the even spacing happens to land on."""
    result = validate.recheck_decisions(cursor, sample=50)[0]
    assert result["check"].startswith("sample of 50 of ")
