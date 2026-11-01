"""
One test or more for each finding in docs/FINAL_REVIEW.md, and the break scenarios it lists.
Each of these was written to fail on the code as it was reviewed, before the fix went in.
"""

import csv
import inspect
import json
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import psycopg2
import pytest

from app import atp, config, db, detect, orders, reservations, trace
from eval import check_unrelated, score
from generator import export
from scripts import export_seed, load_data, setup_database, validate
from tests.conftest import (NDC, SKU_A, SKU_B, WAREHOUSE_SOUTH, add_shipment_event, add_stock, add_transfer, connect)
from tests.test_api import client, live, order_of, place, promisable_of, stock

REPO_DIR = config.BACKEND_DIR.parent
T = datetime(2026, 3, 1, 10)
ONE_MINUTE = timedelta(minutes=1)
LOAD_DB = "inventory_test_load"


def expiry_of(order):
    return datetime.fromisoformat(order["lines"][0]["allocations"][0]["expires_at"])


def place_at(moment, sku_id=SKU_A, qty=1):
    """An order at an exact time, which the API can't ask for, because it reads the clock."""
    connection = connect()
    order = orders.place_order(connection, "WEB", "CUST-LATER", "SOUTH", [{"sku_id": sku_id, "qty": qty}], moment)
    connection.close()
    return order


def reserved_now(sku_id=SKU_A, location_id=WAREHOUSE_SOUTH):
    connection = connect()
    now = datetime.fromisoformat(client.get("/api/atp", params={"sku_id": sku_id}).json()["as_of"])
    with connection.cursor() as cursor:
        reserved = atp.get_reserved(cursor, sku_id, location_id, now)
    connection.close()
    return reserved


def status_of(order_id):
    return client.get(f"/api/orders/{order_id}/trace").json()["order"]["status"]


def last_event_time(order_id):
    events = client.get(f"/api/orders/{order_id}/trace").json()["order_events"]
    return datetime.fromisoformat(events[-1]["occurred_at"])


# ------------------------------------------------------------ F1: an expired hold can't come back

def test_f1_paying_for_a_hold_someone_else_has_since_taken_is_refused(live):
    """Stock 1. A's hold runs out, B takes the unit, and A's old payment comes in."""
    stock(SKU_A, WAREHOUSE_SOUTH, 1)
    first = place(1)
    second = place_at(expiry_of(first) + ONE_MINUTE)
    assert second["status"] == "ALLOCATED"

    response = client.post(f"/api/orders/{first['order_id']}/confirm")

    assert response.status_code == 409
    assert status_of(first["order_id"]) == "ALLOCATED"
    assert reserved_now() == 1


def test_f1_paying_after_the_clock_passes_expiry_is_refused_with_nobody_else_waiting(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    stock(SKU_B, WAREHOUSE_SOUTH, 5)
    first = place(1)
    place_at(expiry_of(first) + ONE_MINUTE, sku_id=SKU_B)
    assert client.post(f"/api/orders/{first['order_id']}/confirm").status_code == 409


def test_f1_paying_is_never_dated_before_the_clock(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    stock(SKU_B, WAREHOUSE_SOUTH, 5)
    first = place(1)
    later = datetime.fromisoformat(first["placed_at"]) + timedelta(minutes=10)
    place_at(later, sku_id=SKU_B)
    assert client.post(f"/api/orders/{first['order_id']}/confirm").status_code == 200
    assert last_event_time(first["order_id"]) > later


def test_f1_cancelling_is_never_dated_before_the_clock(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    stock(SKU_B, WAREHOUSE_SOUTH, 5)
    first = place(1)
    later = datetime.fromisoformat(first["placed_at"]) + timedelta(minutes=10)
    place_at(later, sku_id=SKU_B)
    assert client.post(f"/api/orders/{first['order_id']}/cancel").status_code == 200
    assert last_event_time(first["order_id"]) > later


def test_f1_cancelling_a_hold_that_has_run_out_is_refused(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    stock(SKU_B, WAREHOUSE_SOUTH, 5)
    first = place(1)
    place_at(expiry_of(first) + ONE_MINUTE, sku_id=SKU_B)
    assert client.post(f"/api/orders/{first['order_id']}/cancel").status_code == 409


def test_f1_a_normal_order_can_still_be_paid_shipped_and_cancelled(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    paid = place(1)["order_id"]
    assert client.post(f"/api/orders/{paid}/confirm").status_code == 200
    assert client.post(f"/api/orders/{paid}/ship").status_code == 200
    cancelled = place(1)["order_id"]
    assert client.post(f"/api/orders/{cancelled}/cancel").status_code == 200
    assert promisable_of() == 4


def confirm_in_a_thread(order_id, moment):
    connection = connect()
    try:
        reservations.confirm_order(connection, order_id, moment)
        return "confirmed"
    except reservations.OrderStateError:
        return "refused"
    finally:
        connection.close()


def test_f1_a_payment_waits_for_a_new_order_on_the_same_stock_and_then_sees_it(live):
    """
    The payment read the clock before the expiry, and the new order commits after the expiry.
    The payment has to wait for the lock, then look at the time again, or both would hold one unit.
    """
    stock(SKU_A, WAREHOUSE_SOUTH, 1)
    first = place(1)
    moment = expiry_of(first) + ONE_MINUTE

    other = connect()
    cursor = other.cursor()
    second = orders.place_order_in_transaction(cursor, "WEB", "CUST-B", "SOUTH", [{"sku_id": SKU_A, "qty": 1}], moment)
    assert second["status"] == "ALLOCATED"

    with ThreadPoolExecutor(max_workers=1) as pool:
        payment = pool.submit(confirm_in_a_thread, first["order_id"], datetime.fromisoformat(first["placed_at"]) + ONE_MINUTE)
        time.sleep(0.7)
        waited = not payment.done()
        other.commit()
        result = payment.result(timeout=15)
    other.close()

    assert waited
    assert result == "refused"
    assert reserved_now() == 1


def test_f1_a_payment_that_waits_and_is_still_in_time_goes_through_dated_after_the_order(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 2)
    first = place(1)
    placed_at = datetime.fromisoformat(first["placed_at"])
    moment = placed_at + timedelta(minutes=5)

    other = connect()
    cursor = other.cursor()
    orders.place_order_in_transaction(cursor, "WEB", "CUST-B", "SOUTH", [{"sku_id": SKU_A, "qty": 1}], moment)

    with ThreadPoolExecutor(max_workers=1) as pool:
        payment = pool.submit(confirm_in_a_thread, first["order_id"], placed_at + ONE_MINUTE)
        time.sleep(0.5)
        other.commit()
        result = payment.result(timeout=15)
    other.close()

    assert result == "confirmed"
    assert last_event_time(first["order_id"]) >= moment


# ------------------------------------------------------------ F2: the detector follows the ATP rule

def add_hold_on_truck(cursor, shipment_id, qty, moment=T, channel="WEB", order_no=1):
    """Written straight in with no check, as a buggy system might."""
    order_id = f"ORD-2026-{order_no:06d}"
    line_id = f"{order_id}-L01"
    reservation_id = f"RES-{order_id}-01"
    cursor.execute("""INSERT INTO orders VALUES (%s, %s, 'CUST-1', 'SOUTH', %s, 'ALLOCATED', 100)""", (order_id, channel, moment))
    cursor.execute("""INSERT INTO order_line VALUES (%s, %s, 1, %s, %s, 100, %s, 'ALLOCATED')""",
                   (line_id, order_id, SKU_A, qty, qty * 100))
    cursor.execute(
        """INSERT INTO reservation (reservation_id, order_line_id, sku_id, location_id, qty, supply_type,
                                    inbound_shipment_id, created_at, expires_at, status)
           VALUES (%s, %s, %s, %s, %s, 'INBOUND', %s, %s, %s, 'ACTIVE')""",
        (reservation_id, line_id, SKU_A, WAREHOUSE_SOUTH, qty, shipment_id, moment, moment + timedelta(minutes=30)),
    )
    cursor.execute("""INSERT INTO reservation_event (event_id, reservation_id, to_status, occurred_at, reason_code)
                      VALUES (nextval('event_number'), %s, 'ACTIVE', %s, 'TEST')""", (reservation_id, moment))
    return reservation_id


def flagged_holds(eta_at, channel="WEB", allow_inbound=True, change=None, qty=1):
    """Nothing on the shelf, a truck of 10 on its way, and a hold on that truck."""
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 0, allow_inbound=allow_inbound)
        add_transfer(cursor, "SHP-2026-000900", SKU_A, WAREHOUSE_SOUTH, 10, T - timedelta(days=2), eta_at)
        if change:
            change(cursor)
        add_hold_on_truck(cursor, "SHP-2026-000900", qty, channel=channel)
        found = [(row["record_id"], row["expected_value"]) for row in detect.find_exceptions(cursor)
                 if row["issue_type"] == "RESERVATION_EXCEEDS_AVAILABLE"]
    connection.close()
    return found


HOLD = "RES-ORD-2026-000001-01"


def test_f2_a_truck_due_beyond_the_horizon_doesnt_cover_a_hold(orders_db):
    assert flagged_holds(T + timedelta(days=7)) == [(HOLD, 0)]


def test_f2_an_overdue_truck_doesnt_cover_a_hold(orders_db):
    assert flagged_holds(T - timedelta(hours=1)) == [(HOLD, 0)]


def test_f2_marketplace_cant_hold_stock_on_a_truck(orders_db):
    assert flagged_holds(T + timedelta(hours=5), channel="MARKETPLACE") == [(HOLD, 0)]


def test_f2_a_rule_that_disallows_inbound_is_respected(orders_db):
    assert flagged_holds(T + timedelta(hours=5), allow_inbound=False) == [(HOLD, 0)]


def test_f2_a_later_eta_pushes_a_truck_out_of_the_horizon(orders_db):
    def delay(cursor):
        add_shipment_event(cursor, "SHP-2026-000900", "IN_TRANSIT", "IN_TRANSIT", T - timedelta(hours=1), T + timedelta(days=3))
    assert flagged_holds(T + timedelta(hours=5), change=delay) == [(HOLD, 0)]


def test_f2_a_truck_already_received_doesnt_count_as_inbound_too(orders_db):
    def receive(cursor):
        add_shipment_event(cursor, "SHP-2026-000900", "IN_TRANSIT", "RECEIVED", T - timedelta(hours=1))
    assert flagged_holds(T + timedelta(hours=5), change=receive) == [(HOLD, 0)]


def test_f2_a_channel_not_selling_from_there_yet_cant_hold_anything(orders_db):
    def not_yet(cursor):
        cursor.execute("UPDATE channel_location SET effective_from = '2026-06-01' WHERE channel_code = 'WEB'")
    assert flagged_holds(T + timedelta(hours=5), change=not_yet) == [(HOLD, 0)]


@pytest.mark.parametrize("eta_at", [T, T + timedelta(hours=5), T + timedelta(hours=24)])
def test_f2_a_truck_inside_the_horizon_is_a_valid_decoy(orders_db, eta_at):
    assert flagged_holds(eta_at) == []


def test_f2_a_later_eta_can_bring_a_truck_into_the_horizon(orders_db):
    def sooner(cursor):
        add_shipment_event(cursor, "SHP-2026-000900", "IN_TRANSIT", "IN_TRANSIT", T - timedelta(hours=1), T + timedelta(hours=2))
    assert flagged_holds(T + timedelta(days=9), change=sooner) == []


def test_f2_exactly_what_the_truck_can_cover_passes_and_one_more_fails(orders_db):
    assert flagged_holds(T + timedelta(hours=5), qty=10) == []


def test_f2_one_more_than_the_truck_can_cover_fails(orders_db):
    assert flagged_holds(T + timedelta(hours=5), qty=11) == [(HOLD, 10)]


def test_f2_detector_agrees_with_atp_on_what_was_promisable(orders_db):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 3, safety_stock=1)
        add_transfer(cursor, "SHP-2026-000900", SKU_A, WAREHOUSE_SOUTH, 10, T - timedelta(days=2), T + timedelta(hours=5))
        promisable = atp.get_positions(cursor, SKU_A, "WEB", T)[0]["promisable"]
        add_hold_on_truck(cursor, "SHP-2026-000900", promisable + 1)
        found = {row["record_id"]: row["expected_value"] for row in detect.find_exceptions(cursor)}
    connection.close()
    assert found == {HOLD: promisable}


# ------------------------------------------------------------ F3: a snapshot is judged at its own time

def stock_with_closing(closing_on_hand=2):
    """Opening 2 and a stored closing balance. Nothing has moved yet."""
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 2)
        cursor.execute("""INSERT INTO inventory_balance VALUES ('BAL-CLOSE', %s, %s, 'CLOSING', %s, %s, 0)""",
                       (SKU_A, WAREHOUSE_SOUTH, config.AS_OF, closing_on_hand))
    connection.close()


def sale_at(moment, doc="POS-TEST"):
    connection = connect()
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO inventory_movement (movement_id, sku_id, location_id, movement_type, stock_bucket, quantity,
                                               occurred_at, reason_code, source_doc)
               VALUES (%s, %s, %s, 'SALE', 'SELLABLE', -1, %s, 'WALK_IN', %s)""",
            (f"MOV-{doc}", SKU_A, WAREHOUSE_SOUTH, moment, doc),
        )
    connection.close()


def balance_exceptions():
    connection = connect()
    with connection, connection.cursor() as cursor:
        found = [row for row in detect.find_exceptions(cursor) if row["issue_type"] == "BALANCE_NOT_RECONCILED"]
    connection.close()
    return [(row["record_id"], row["expected_value"], row["actual_value"]) for row in found]


def ledger_of():
    return client.get("/api/movements", params={"sku_id": SKU_A, "location_id": WAREHOUSE_SOUTH}).json()


def test_f3_a_sale_after_the_snapshot_doesnt_make_it_wrong(live):
    stock_with_closing()
    sale_at(config.AS_OF + ONE_MINUTE)
    assert balance_exceptions() == []


def test_f3_ledger_shows_the_snapshot_reconciling_and_current_stock_apart(live):
    stock_with_closing()
    sale_at(config.AS_OF + ONE_MINUTE)
    body = ledger_of()
    assert body["reconciles"] is True
    assert body["at_snapshot_on_hand"] == 2
    assert body["current_on_hand"] == 1
    assert body["stored_closing"]["on_hand"] == 2


def test_f3_a_wrong_snapshot_is_still_found_with_live_sales_after_it(live):
    stock_with_closing(closing_on_hand=5)
    sale_at(config.AS_OF + ONE_MINUTE)
    assert balance_exceptions() == [("BAL-CLOSE", 2, 5)]
    assert ledger_of()["reconciles"] is False


def test_f3_a_sale_at_the_snapshot_second_counts_towards_it(live):
    stock_with_closing()
    sale_at(config.AS_OF)
    assert balance_exceptions() == [("BAL-CLOSE", 1, 2)]


def test_f3_validator_judges_the_snapshot_at_its_own_time_too(live):
    stock_with_closing()
    sale_at(config.AS_OF + ONE_MINUTE)
    connection = connect()
    with connection.cursor() as cursor:
        results = {row["check"]: row["violations"] for row in validate.run_checks(cursor)}
    connection.close()
    assert results["closing balance does not reconcile to opening plus ledger"] == 0


# ------------------------------------------------------------ F4: every table, through every link

def copy_of_run(generated_data, tmp_path):
    shutil.copytree(generated_data, tmp_path / "tiny")
    shutil.copytree(export.clean_dir_of(generated_data), tmp_path / "tiny_clean")
    shutil.copytree(export.truth_dir_of(generated_data), tmp_path / "ground_truth" / "tiny")
    return tmp_path / "tiny"


def read_rows(folder, table):
    with open(folder / f"{table}.csv", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_rows(folder, table, rows):
    with open(folder / f"{table}.csv", "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=export.TABLE_COLUMNS[table], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def case_orders_of(generated_data):
    return {row["order_id"] for row in score.load_ground_truth(export.truth_dir_of(generated_data)) if row["order_id"]}


def is_unrelated(row, case_orders):
    """A row that belongs to no labelled order, going by whichever order ID it mentions."""
    text = " ".join(str(value) for value in row.values())
    return not any(order_id in text for order_id in case_orders)


# Table, the column to change, and what to change it to.
ONE_ROW_CHANGES = [
    ("order_line", "qty", "999"),
    ("reservation", "qty", "999"),
    ("reservation", "promised_ship_by", "2026-01-01 00:00:00"),
    ("order_allocation", "location_id", "LOC-000002"),
    ("order_allocation", "allocated_at", "2026-01-01 00:00:00"),
    ("allocation_decision", "reason_code", "CHANGED"),
    ("allocation_decision", "candidates", "[]"),
    ("order_event", "reason_code", "CHANGED"),
    ("reservation_event", "reason_code", "CHANGED"),
    ("shipment_event", "reason_code", "CHANGED"),
]


@pytest.mark.parametrize("table, column, value", ONE_ROW_CHANGES)
def test_f4_a_changed_field_in_any_transaction_table_is_noticed(generated_data, tmp_path, table, column, value):
    final_dir = copy_of_run(generated_data, tmp_path)
    case_orders = case_orders_of(generated_data)
    rows = read_rows(final_dir, table)
    row = next(row for row in rows if is_unrelated(row, case_orders) and row[column] != value)
    row[column] = value
    write_rows(final_dir, table, rows)
    assert any(table in problem for problem in check_unrelated.compare_datasets(final_dir))


# Each link is pointed at another row that really exists, so a foreign key wouldn't notice.
RELINKS = [
    ("order_line", "order_id", "order_id"),
    ("reservation", "order_line_id", "order_line_id"),
    ("reservation", "inbound_shipment_id", "inbound_shipment_id"),
    ("order_allocation", "reservation_id", "reservation_id"),
    ("order_allocation", "shipment_id", "shipment_id"),
    ("inventory_movement", "related_movement_id", "related_movement_id"),
    ("inventory_movement", "allocation_id", "allocation_id"),
    ("inventory_movement", "shipment_id", "shipment_id"),
    ("shipment", "order_id", "order_id"),
    ("allocation_decision", "order_line_id", "order_line_id"),
    ("order_event", "order_id", "order_id"),
    ("reservation_event", "reservation_id", "reservation_id"),
    ("shipment_event", "shipment_id", "shipment_id"),
]


@pytest.mark.parametrize("table, column, _other", RELINKS)
def test_f4_a_link_pointed_at_the_wrong_row_is_noticed(generated_data, tmp_path, table, column, _other):
    final_dir = copy_of_run(generated_data, tmp_path)
    case_orders = case_orders_of(generated_data)
    rows = read_rows(final_dir, table)
    filled = [row for row in rows if row[column] and is_unrelated(row, case_orders)]
    first = filled[0]
    # From the far end, because neighbours can be identical apart from this link, like the two
    # holds of a split order, and swapping those wouldn't change anything at all.
    second = next(row for row in reversed(filled) if row[column] != first[column])
    first[column], second[column] = second[column], first[column]
    write_rows(final_dir, table, rows)
    assert check_unrelated.compare_datasets(final_dir) != []


def test_f4_every_exported_table_is_compared():
    assert set(check_unrelated.ROWS_TO_COMPARE) | set(check_unrelated.MASTER_TABLES) | {"inventory_balance"} == set(export.TABLE_COLUMNS)


def test_f4_two_rows_sharing_a_document_after_renumbering_is_noticed(generated_data, tmp_path):
    final_dir = copy_of_run(generated_data, tmp_path)
    rows = read_rows(final_dir, "inventory_movement")
    walk_ins = [row for row in rows if row["reason_code"] == "WALK_IN"]
    walk_ins[0]["source_doc"] = walk_ins[1]["source_doc"]
    write_rows(final_dir, "inventory_movement", rows)
    assert any("document" in problem for problem in check_unrelated.compare_datasets(final_dir))


def test_f4_a_balance_row_missing_from_the_final_data_is_noticed(generated_data, tmp_path):
    final_dir = copy_of_run(generated_data, tmp_path)
    rows = read_rows(final_dir, "inventory_balance")
    write_rows(final_dir, "inventory_balance", rows[1:])
    assert any("balance" in problem for problem in check_unrelated.compare_datasets(final_dir))


def test_f4_a_balance_row_only_the_final_data_has_is_noticed(generated_data, tmp_path):
    final_dir = copy_of_run(generated_data, tmp_path)
    rows = read_rows(export.clean_dir_of(final_dir), "inventory_balance")
    write_rows(export.clean_dir_of(final_dir), "inventory_balance", rows[1:])
    assert any("balance" in problem for problem in check_unrelated.compare_datasets(final_dir))


# ------------------------------------------------------------ F5: TRUNCATE is refused too

@pytest.mark.parametrize("table", ["inventory_movement", "inventory_balance", "allocation_decision", "order_event",
                                   "reservation_event", "shipment_event"])
def test_f5_history_cant_be_truncated(cursor, table):
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cursor.execute(f"TRUNCATE {table} CASCADE")


def test_f5_truncating_a_parent_cant_take_the_history_with_it(cursor):
    with pytest.raises(psycopg2.errors.RaiseException, match="append-only"):
        cursor.execute("TRUNCATE reservation CASCADE")


# ------------------------------------------------------------ F6: the page shows the real arithmetic

def test_f6_marketplace_promisable_comes_from_channel_atp_and_not_atp(live):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 44, safety_stock=25)
        add_transfer(cursor, "SHP-2026-000900", SKU_A, WAREHOUSE_SOUTH, 114, config.AS_OF - timedelta(days=1),
                     config.AS_OF + timedelta(hours=5))
        cursor.execute("UPDATE channel_location SET buffer_units = 8 WHERE channel_code = 'MARKETPLACE'")
    connection.close()
    row = client.get("/api/atp", params={"sku_id": SKU_A, "channel": "MARKETPLACE"}).json()["locations"][0]
    assert (row["atp"], row["channel_atp"], row["promisable"]) == (158, 44, 11)


def test_f6_availability_page_shows_the_channel_atp_step():
    text = (REPO_DIR / "frontend" / "src" / "pages" / "Availability.jsx").read_text(encoding="utf-8")
    assert "row.channel_atp" in text


# ------------------------------------------------------------ F7 and F8: setup

def test_f7_database_port_is_only_open_to_this_machine():
    text = (REPO_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:5434:5432"' in text


def test_f8_readme_states_the_node_versions_the_tools_accept():
    readme = (REPO_DIR / "README.md").read_text(encoding="utf-8")
    package = json.loads((REPO_DIR / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert "Node 20 or later" not in readme
    assert package["engines"]["node"] == "^20.19.0 || >=22.12.0"
    assert "20.19" in readme and "npm ci" in readme


# ------------------------------------------------------------ F9: a failed load keeps what was there

def count_in_load_db(table="orders"):
    connection = db.get_connection(LOAD_DB)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
    connection.close()
    return count


def copy_of_data(generated_data, tmp_path):
    folder = tmp_path / "copy"
    shutil.copytree(generated_data, folder)
    return folder


def rewrite_with_its_checksum(folder, table, rows):
    """A bad file with a matching manifest, so only the database itself can refuse it."""
    write_rows(folder, table, rows)
    manifest_path = folder / "source_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][f"{table}.csv"]["sha256"] = export.sha256_of(folder / f"{table}.csv")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def load_db(generated_data, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", LOAD_DB)
    load_data.load(generated_data)
    return generated_data


def test_f9_a_missing_file_stops_before_anything_is_reset(load_db, tmp_path):
    before = count_in_load_db()
    folder = copy_of_data(load_db, tmp_path)
    (folder / "shipment_event.csv").unlink()
    with pytest.raises(load_data.LoadRefused, match="shipment_event.csv"):
        load_data.load(folder)
    assert count_in_load_db() == before


def test_f9_a_file_that_differs_from_its_manifest_stops_before_anything_is_reset(load_db, tmp_path):
    before = count_in_load_db()
    folder = copy_of_data(load_db, tmp_path)
    with open(folder / "orders.csv", "a", encoding="utf-8") as file:
        file.write("\n")
    with pytest.raises(load_data.LoadRefused, match="orders.csv"):
        load_data.load(folder)
    assert count_in_load_db() == before


def test_f9_a_missing_manifest_stops_before_anything_is_reset(load_db, tmp_path):
    before = count_in_load_db()
    folder = copy_of_data(load_db, tmp_path)
    (folder / "source_manifest.json").unlink()
    with pytest.raises(load_data.LoadRefused, match="source_manifest.json"):
        load_data.load(folder)
    assert count_in_load_db() == before


def test_f9_a_bad_reference_rolls_the_whole_load_back(load_db, tmp_path):
    """COPY succeeds, the foreign key refuses it, and the data from before is still there."""
    before = count_in_load_db()
    folder = copy_of_data(load_db, tmp_path)
    rows = read_rows(folder, "order_line")
    rows[0]["order_id"] = "ORD-2026-999999"
    rewrite_with_its_checksum(folder, "order_line", rows[:len(rows) - 1])
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        load_data.load(folder)
    assert count_in_load_db() == before
    assert count_in_load_db("order_line") == len(rows)


def test_f9_a_good_load_still_has_its_foreign_keys_and_can_run_twice(load_db):
    load_data.load(load_db)
    connection = db.get_connection(LOAD_DB)
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE contype = 'f'")
        assert cursor.fetchone()[0] == len(validate.FOREIGN_KEYS)
    connection.close()


# ------------------------------------------------------------ F10: exports don't hold everything in memory

def test_f10_tables_are_read_one_row_at_a_time():
    assert inspect.isgeneratorfunction(export_seed.read_table)


def test_f10_json_is_still_valid_with_every_row(generated_data, tmp_path):
    export_seed.export_seed(generated_data, tmp_path)
    tables = json.loads((tmp_path / "seed.json").read_text(encoding="utf-8"))
    rows = read_rows(generated_data, "channel")
    assert len(tables["channel"]) == len(rows)
    assert tables["channel"][0]["channel_code"] == rows[0]["channel_code"]
    # An empty cell is NULL, the same as the loader treats it.
    assert any(row["hold_minutes"] is None for row in tables["channel"])


# ------------------------------------------------------------ the traceable badge means what it says

def hold_and_sale(**sale_changes):
    hold = {"status": "CONSUMED", "allocation_id": "ALC-1", "sku_id": SKU_A, "location_id": NDC, "qty": 3}
    sale = {"allocation_id": "ALC-1", "movement_type": "SALE", "shipment_id": "SHP-1", "sku_id": SKU_A, "location_id": NDC,
            "quantity": -3}
    return [hold], [dict(sale, **sale_changes)]


def test_trace_is_traceable_when_the_sale_matches_the_hold():
    assert trace.is_fully_traceable(*hold_and_sale()) is True


@pytest.mark.parametrize("change", [{"quantity": -2}, {"movement_type": "RETURN"}, {"shipment_id": None},
                                    {"sku_id": SKU_B}, {"location_id": WAREHOUSE_SOUTH}, {"allocation_id": "ALC-2"}])
def test_trace_isnt_traceable_when_the_sale_doesnt_match_the_hold(change):
    assert trace.is_fully_traceable(*hold_and_sale(**change)) is False


# ------------------------------------------------------------ the other break scenarios in the review

def test_scenario_8_a_cancelled_order_has_no_sale_to_return(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    order_id = place(2)["order_id"]
    client.post(f"/api/orders/{order_id}/cancel")
    assert client.get(f"/api/orders/{order_id}/trace").json()["movements"] == []
    response = client.post("/api/returns", json={"sale_movement_id": "MOV-2026-99999999", "qty": 1})
    assert response.status_code == 409
    assert promisable_of() == 5


def test_scenario_8_a_movement_that_isnt_a_sale_cant_be_returned(live):
    stock(SKU_A, NDC, 20)
    stock(SKU_A, WAREHOUSE_SOUTH, 0)
    client.post("/api/transfers", json={"sku_id": SKU_A, "from_location_id": NDC, "to_location_id": WAREHOUSE_SOUTH, "qty": 3})
    connection = connect()
    with connection.cursor() as cursor:
        cursor.execute("SELECT movement_id FROM inventory_movement WHERE movement_type = 'TRANSFER_OUT'")
        transfer_row = cursor.fetchone()[0]
    connection.close()
    assert client.post("/api/returns", json={"sale_movement_id": transfer_row, "qty": 1}).status_code == 409


def test_scenario_9_two_returns_of_the_last_unit_at_once_gives_one_success(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    order_id = place(3)["order_id"]
    client.post(f"/api/orders/{order_id}/confirm")
    sale_id = client.post(f"/api/orders/{order_id}/ship").json()["movements"][0]["movement_id"]
    assert client.post("/api/returns", json={"sale_movement_id": sale_id, "qty": 2}).status_code == 201

    def send(_number):
        return client.post("/api/returns", json={"sale_movement_id": sale_id, "qty": 1}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(send, range(2)))
    assert codes == [201, 409]
    assert promisable_of() == 10


def test_scenario_10_receiving_twice_gives_one_arrival_and_one_sale(live):
    stock(SKU_A, NDC, 6)
    stock(SKU_A, WAREHOUSE_SOUTH, 0)
    shipment_id = client.post("/api/transfers", json={"sku_id": SKU_A, "from_location_id": NDC, "to_location_id": WAREHOUSE_SOUTH,
                                                      "qty": 6, "lead_time_days": 1}).json()["shipment_id"]
    order_id = place(4)["order_id"]
    client.post(f"/api/orders/{order_id}/confirm")
    assert client.post(f"/api/orders/{order_id}/ship").status_code == 409
    assert client.post(f"/api/transfers/{shipment_id}/receive").status_code == 200
    assert client.post(f"/api/transfers/{shipment_id}/receive").status_code == 409

    connection = connect()
    with connection.cursor() as cursor:
        cursor.execute("""SELECT movement_type, COUNT(*) FROM inventory_movement
                          WHERE movement_type IN ('TRANSFER_IN', 'SALE') GROUP BY 1""")
        assert dict(cursor.fetchall()) == {"TRANSFER_IN": 1, "SALE": 1}
    connection.close()


def add_rule_version(cursor, effective_from, effective_to, safety_stock):
    cursor.execute(
        """INSERT INTO replenishment_rule (rule_id, sku_id, location_id, safety_stock, reorder_point, reorder_qty,
                                           lead_time_days, allow_inbound_atp, effective_from, effective_to)
           VALUES ('RUL-' || nextval('event_number'), %s, %s, %s, %s, 10, 2, TRUE, %s, %s)""",
        (SKU_A, WAREHOUSE_SOUTH, safety_stock, safety_stock, effective_from, effective_to),
    )


def two_rule_versions(first_ends, second_starts):
    """Stock of 50, with safety stock 5 on the first version and 9 on the second."""
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, SKU_A, WAREHOUSE_SOUTH, 50, safety_stock=5)
        cursor.execute("UPDATE replenishment_rule SET effective_to = %s", (first_ends,))
        add_rule_version(cursor, second_starts, None, 9)
    return connection


def test_scenario_11_adjacent_versions_give_exactly_one_policy_either_side_of_the_change(orders_db):
    connection = two_rule_versions("2026-03-01", "2026-03-01")
    with connection.cursor() as cursor:
        before = atp.get_positions(cursor, SKU_A, "WEB", datetime(2026, 2, 28, 23, 59, 59))
        after = atp.get_positions(cursor, SKU_A, "WEB", datetime(2026, 3, 1))
        results = {row["check"]: row["violations"] for row in validate.run_checks(cursor)}
    connection.close()
    assert [row["safety_stock"] for row in before] == [5]
    assert [row["safety_stock"] for row in after] == [9]
    assert results["replenishment rule versions that overlap"] == 0


def test_scenario_11_overlapping_rule_versions_fail_validation(orders_db):
    connection = two_rule_versions("2026-03-02", "2026-03-01")
    with connection.cursor() as cursor:
        results = {row["check"]: row["violations"] for row in validate.run_checks(cursor)}
    connection.close()
    assert results["replenishment rule versions that overlap"] == 1


def test_scenario_11_overlapping_channel_versions_fail_validation(cursor):
    cursor.execute("""INSERT INTO channel_location
                      SELECT 'CHL-OVER', channel_code, location_id, buffer_units, effective_from, effective_to
                      FROM channel_location ORDER BY channel_location_id LIMIT 1""")
    results = {row["check"]: row["violations"] for row in validate.run_checks(cursor)}
    assert results["channel buffer versions that overlap"] == 1


def test_scenario_12_repeating_a_request_places_another_order(live):
    """There are no idempotency keys, which the README states. This records that it is so."""
    stock(SKU_A, WAREHOUSE_SOUTH, 5)
    first = place(1)
    second = place(1)
    assert first["order_id"] != second["order_id"]
    assert promisable_of() == 3


def test_f7_the_app_connects_to_the_address_the_database_listens_on():
    """
    Found after the F7 fix. Asking for localhost tries IPv6 first on Windows, and the port is now
    only open on 127.0.0.1, so every new connection waited about 2 seconds for that to fail.
    """
    example = (config.BACKEND_DIR / ".env.example").read_text(encoding="utf-8")
    settings_file = (config.BACKEND_DIR / "app" / "config.py").read_text(encoding="utf-8")
    assert "DB_HOST=127.0.0.1" in example
    assert 'os.getenv("DB_HOST", "127.0.0.1")' in settings_file
