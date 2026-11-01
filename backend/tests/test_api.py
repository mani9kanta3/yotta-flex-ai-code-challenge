"""
The API, through FastAPI's TestClient.

Reads run on the generated final data, in a database of their own. Changes run on the small
hand-written database, where the numbers are exact and writes can really commit.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, db, detect, main, trace
from eval.score import load_ground_truth
from generator import export, settings
from scripts import load_data
from tests.conftest import (NDC, ORDERS_DB, SKU_A, SKU_B, STORE_SOUTH, WAREHOUSE_NORTH, WAREHOUSE_SOUTH, add_stock,
                            add_transfer, connect, require_postgres)

API_DB = "inventory_test_api"
client = TestClient(main.app)


@pytest.fixture(scope="session")
def api_database(generated_data):
    """The final data, loaded once, with the detectors already run. Tests here only read it."""
    require_postgres()
    other_db = config.DB_NAME
    config.DB_NAME = API_DB
    try:
        load_data.load(generated_data)
        connection = db.get_connection(API_DB)
        with connection, connection.cursor() as cursor:
            detect.write_exceptions(cursor, detect.find_exceptions(cursor))
        connection.close()
    finally:
        config.DB_NAME = other_db
    return generated_data


@pytest.fixture
def reads(api_database, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", API_DB)
    return api_database


@pytest.fixture
def live(orders_db, monkeypatch):
    monkeypatch.setattr(config, "DB_NAME", ORDERS_DB)


def stock(sku_id, location_id, on_hand, **options):
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_stock(cursor, sku_id, location_id, on_hand, **options)
    connection.close()


def order_of(qty, sku_id=SKU_A, channel="WEB", region="SOUTH"):
    return {"channel_code": channel, "ship_to_region": region, "lines": [{"sku_id": sku_id, "qty": qty}]}


def place(qty, **options):
    response = client.post("/api/orders", json=order_of(qty, **options))
    assert response.status_code == 201, response.text
    return response.json()


def promisable_of(sku_id=SKU_A, channel="WEB", as_of=None):
    params = {"sku_id": sku_id, "channel": channel}
    if as_of:
        params["as_of"] = as_of
    return client.get("/api/atp", params=params).json()["network"]["promisable"]


# ------------------------------------------------------------ reading the generated data

def test_health_says_what_is_loaded(reads):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["row_counts"]["inventory_movement"] == settings.SCALES["starter"]["movements"]
    assert body["row_counts"]["orders"] == settings.SCALES["starter"]["orders"]


def test_overview_adds_up_order_statuses(reads):
    body = client.get("/api/overview").json()
    assert sum(row["rows"] for row in body["order_statuses"]) == body["row_counts"]["orders"]
    assert body["on_hand_units"] > 0
    assert body["blocked_units"] <= body["on_hand_units"]
    assert body["in_transit_trucks"] > 0


def test_sku_search_finds_by_id_and_by_name(reads):
    found = client.get("/api/skus", params={"search": "SKU-000001"}).json()
    assert [row["sku_id"] for row in found] == ["SKU-000001"]
    assert all("Shirt" in row["product_name"] for row in client.get("/api/skus", params={"search": "Shirt"}).json())


def test_sku_search_treats_input_as_text_and_not_sql(reads):
    response = client.get("/api/skus", params={"search": "'; DROP TABLE sku; --"})
    assert response.status_code == 200
    assert response.json() == []
    assert client.get("/api/skus").status_code == 200


def test_unknown_sku_is_404(reads):
    assert client.get("/api/skus/SKU-999999").status_code == 404
    assert client.get("/api/atp", params={"sku_id": "SKU-999999"}).status_code == 404


def test_atp_shows_every_term_of_the_formula(reads):
    body = client.get("/api/atp", params={"sku_id": "SKU-000001", "channel": "WEB"}).json()
    assert body["locations"]
    for row in body["locations"]:
        assert row["atp"] == row["on_hand"] - row["reserved"] - row["blocked"] + row["inbound"]
        assert row["promisable"] == max(0, row["channel_atp"] - row["safety_stock"] - row["buffer_units"])
    assert body["network"]["promisable"] == sum(row["promisable"] for row in body["locations"])


def test_atp_differs_at_a_past_time(reads):
    now = client.get("/api/atp", params={"sku_id": "SKU-000001"}).json()
    then = client.get("/api/atp", params={"sku_id": "SKU-000001", "as_of": "2026-01-02T12:00:00"}).json()
    assert then["as_of"] == "2026-01-02T12:00:00"
    assert then["locations"] != now["locations"]


def test_marketplace_doesnt_get_trucks_to_wait_for(reads):
    body = client.get("/api/atp", params={"sku_id": "SKU-000016", "channel": "MARKETPLACE"}).json()
    assert all(row["inbound_shipments"] == [] for row in body["locations"])


def test_bad_channel_is_refused(reads):
    assert client.get("/api/atp", params={"sku_id": "SKU-000001", "channel": "PHONE"}).status_code == 422


def test_order_list_filters_by_decision(reads):
    body = client.get("/api/orders", params={"decision": "SPLIT", "limit": 5}).json()
    assert body["total"] > 0
    assert len(body["orders"]) <= 5
    assert all("SPLIT" in row["decisions"] for row in body["orders"])


def test_order_list_pages(reads):
    first = client.get("/api/orders", params={"limit": 2}).json()["orders"]
    second = client.get("/api/orders", params={"limit": 2, "offset": 2}).json()["orders"]
    assert first[0]["order_id"] > first[1]["order_id"] > second[0]["order_id"]


def first_order_with(status):
    return client.get("/api/orders", params={"status": status, "limit": 1}).json()["orders"][0]["order_id"]


def test_trace_of_a_fulfilled_order_reaches_the_shelf(reads):
    body = client.get(f"/api/orders/{first_order_with('FULFILLED')}/trace").json()
    assert body["fully_traceable"] is True
    assert body["lines"] and body["decisions"] and body["reservations"]
    assert body["movements"] and all(row["movement_type"] == "SALE" for row in body["movements"])
    assert body["shipments"] and body["shipment_events"]
    assert body["order_events"][0]["to_status"] == "PLACED"


def test_trace_of_a_rejected_order_keeps_the_refusal(reads):
    body = client.get(f"/api/orders/{first_order_with('REJECTED')}/trace").json()
    assert all(row["status"] == "REJECTED" for row in body["reservations"])
    assert body["movements"] == []
    assert body["decisions"][0]["reason_code"]


def test_trace_of_an_unknown_order_is_404(reads):
    assert client.get("/api/orders/ORD-2026-999999/trace").status_code == 404


def test_every_order_in_the_data_is_fully_traceable(reads):
    connection = db.get_connection(API_DB)
    with connection.cursor() as cursor:
        cursor.execute("SELECT order_id FROM orders")
        order_ids = [row[0] for row in cursor.fetchall()]
    assert all(trace.get_order_trace(connection, order_id)["fully_traceable"] for order_id in order_ids)
    connection.close()


def a_pair():
    locations = client.get("/api/skus/SKU-000016").json()["stocked_at"]
    return "SKU-000016", locations[0]["location_id"]


def test_ledger_runs_from_opening_to_closing(reads):
    sku_id, location_id = a_pair()
    body = client.get("/api/movements", params={"sku_id": sku_id, "location_id": location_id, "limit": 5}).json()
    assert len(body["rows"]) == 5
    assert body["total_rows"] == len(body["history"])
    assert body["history"][-1]["on_hand"] == body["worked_out_on_hand"]
    newest, older = body["rows"][0], body["rows"][1]
    assert newest["on_hand_after"] == older["on_hand_after"] + newest["quantity"]


def test_ledger_of_a_pair_not_stocked_is_404(reads):
    assert client.get("/api/movements", params={"sku_id": "SKU-999999", "location_id": "LOC-000001"}).status_code == 404


def test_ledger_shows_when_a_labelled_balance_doesnt_reconcile(reads):
    """Found the way an operator would, from the exception, with no answers involved."""
    rows = client.get("/api/exceptions", params={"issue_type": settings.ISSUE_BALANCE}).json()["exceptions"]
    assert rows
    for row in rows:
        body = client.get("/api/movements", params={"sku_id": row["sku_id"], "location_id": row["location_id"]}).json()
        assert body["reconciles"] is False
        assert body["worked_out_on_hand"] == row["expected_value"]
        assert body["stored_closing"]["on_hand"] == row["actual_value"]


def test_exceptions_list_what_is_labelled_and_no_decoy(reads):
    truth = load_ground_truth(export.truth_dir_of(reads))
    body = client.get("/api/exceptions", params={"limit": 200}).json()
    found = {row["record_id"] for row in body["exceptions"]}
    assert found == {row["record_id"] for row in truth if row["expected_issue"] != "NONE"}
    assert not found & {row["record_id"] for row in truth if row["expected_issue"] == "NONE"}


def test_each_exception_says_what_to_do_and_why(reads):
    for row in client.get("/api/exceptions", params={"limit": 200}).json()["exceptions"]:
        assert row["recommended_action"] and row["explanation"] and row["sku_id"]
        assert row["expected_value"] != row["actual_value"]


def test_order_exceptions_lead_to_their_order(reads):
    for issue in (settings.ISSUE_RESERVATION, settings.ISSUE_BLOCKED):
        for row in client.get("/api/exceptions", params={"issue_type": issue}).json()["exceptions"]:
            assert client.get(f"/api/orders/{row['order_id']}/trace").status_code == 200


def test_trucks_list_the_ones_on_the_road(reads):
    body = client.get("/api/trucks", params={"limit": 3}).json()
    assert body["total"] > 0
    assert all(row["status"] == "IN_TRANSIT" for row in body["trucks"])
    etas = [row["current_eta_at"] for row in body["trucks"]]
    assert etas == sorted(etas)


def test_metrics_says_how_to_measure_when_nothing_is_measured(reads, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    response = client.get("/api/metrics")
    assert response.status_code == 404
    assert "eval.report" in response.json()["detail"]


def test_metrics_serves_the_last_report(reads, tmp_path, monkeypatch):
    folder = tmp_path / "starter"
    folder.mkdir()
    (folder / "source_manifest.json").write_text(json.dumps({"scale": {"skus": settings.SCALES["starter"]["skus"]}}),
                                                 encoding="utf-8")
    (folder / "metrics_summary.json").write_text(json.dumps({"measured": True}), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert client.get("/api/metrics").json() == {"measured": True}


# ------------------------------------------------------------ an order from start to finish

def test_order_is_held_then_shipped_with_a_full_trace(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    order = place(3)
    assert order["status"] == "ALLOCATED"
    assert order["lines"][0]["decision_code"] == "SINGLE"
    assert promisable_of() == 7

    order_id = order["order_id"]
    assert client.post(f"/api/orders/{order_id}/confirm").json()["order"]["status"] == "CONFIRMED"
    assert promisable_of() == 7

    shipped = client.post(f"/api/orders/{order_id}/ship").json()
    assert shipped["order"]["status"] == "FULFILLED"
    assert shipped["fully_traceable"] is True
    assert [row["quantity"] for row in shipped["movements"]] == [-3]
    assert promisable_of() == 7


def test_asking_for_too_much_is_rejected_with_the_reason_and_kept(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 2)
    order = place(5)
    assert order["status"] == "REJECTED"
    assert order["lines"][0]["reason_code"] == "INSUFFICIENT_ATP"
    assert promisable_of() == 2
    trace_of_it = client.get(f"/api/orders/{order['order_id']}/trace").json()
    assert trace_of_it["reservations"][0]["status"] == "REJECTED"


def test_a_line_can_split_across_two_locations(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 3)
    stock(SKU_A, WAREHOUSE_NORTH, 3)
    line = place(5)["lines"][0]
    assert line["decision_code"] == "SPLIT"
    assert sum(part["qty"] for part in line["allocations"]) == 5


def test_cancelling_gives_the_stock_back(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    order_id = place(4)["order_id"]
    assert promisable_of() == 6
    assert client.post(f"/api/orders/{order_id}/cancel").json()["order"]["status"] == "CANCELLED"
    assert promisable_of() == 10


def test_expiry_releases_the_hold_and_shows_atp_before_and_after(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    order = place(4)
    expires_at = order["lines"][0]["allocations"][0]["expires_at"]

    body = client.post("/api/reservations/expire", json={"as_of": expires_at}).json()

    assert body["orders_expired"] == 1
    assert body["units_released"] == 4
    released = body["released"][0]
    assert (released["atp_before"], released["atp_after"]) == (6, 10)
    assert client.get(f"/api/orders/{order['order_id']}/trace").json()["order"]["status"] == "CANCELLED"


def test_expiry_leaves_a_hold_that_hasnt_run_out(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    order = place(4)
    expires_at = datetime.fromisoformat(order["lines"][0]["allocations"][0]["expires_at"])
    body = client.post("/api/reservations/expire", json={"as_of": (expires_at - timedelta(seconds=1)).isoformat()}).json()
    assert body["orders_expired"] == 0
    assert body["released"] == []


def test_paying_after_expiry_is_refused(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    order = place(4)
    client.post("/api/reservations/expire", json={"as_of": order["lines"][0]["allocations"][0]["expires_at"]})
    response = client.post(f"/api/orders/{order['order_id']}/confirm")
    assert response.status_code == 409
    assert "CANCELLED" in response.json()["detail"]


@pytest.mark.parametrize("action", ["confirm", "cancel", "ship"])
def test_actions_on_an_unknown_order_are_refused(live, action):
    assert client.post(f"/api/orders/ORD-2026-999999/{action}").status_code == 409


def test_shipping_before_payment_is_refused(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    assert client.post(f"/api/orders/{place(1)['order_id']}/ship").status_code == 409


def test_store_orders_say_they_arent_built(live):
    stock(SKU_A, STORE_SOUTH, 10)
    response = client.post("/api/orders", json=order_of(1, channel="STORE"))
    assert response.status_code == 501
    assert "not built" in response.json()["detail"]


@pytest.mark.parametrize("body", [
    {"lines": []},
    {"lines": [{"sku_id": SKU_A, "qty": 0}]},
    {"lines": [{"sku_id": SKU_A, "qty": -1}]},
    {"channel_code": "PHONE", "lines": [{"sku_id": SKU_A, "qty": 1}]},
    {"ship_to_region": "MOON", "lines": [{"sku_id": SKU_A, "qty": 1}]},
])
def test_bad_orders_are_refused_and_write_nothing(live, body):
    assert client.post("/api/orders", json=body).status_code == 422
    assert client.get("/api/orders").json()["total"] == 0


def test_unknown_sku_on_an_order_is_refused(live):
    response = client.post("/api/orders", json=order_of(1, sku_id="SKU-999999"))
    assert response.status_code == 400
    assert client.get("/api/orders").json()["total"] == 0


# ------------------------------------------------------------ trucks and returns

def truck_for(qty, eta_hours=5):
    moment = config.AS_OF
    connection = connect()
    with connection, connection.cursor() as cursor:
        add_transfer(cursor, "SHP-2026-000900", SKU_A, WAREHOUSE_SOUTH, qty, moment, moment + timedelta(hours=eta_hours))
    connection.close()
    return "SHP-2026-000900"


def test_order_on_a_truck_waits_then_ships_when_it_arrives(live):
    # The whole NDC stock goes on the truck, so the truck is the only place left to hold it.
    stock(SKU_A, NDC, 6)
    stock(SKU_A, WAREHOUSE_SOUTH, 0)
    shipment_id = client.post("/api/transfers", json={"sku_id": SKU_A, "from_location_id": NDC,
                                                      "to_location_id": WAREHOUSE_SOUTH, "qty": 6,
                                                      "lead_time_days": 1}).json()["shipment_id"]
    order = place(4, region="SOUTH")
    assert order["lines"][0]["decision_code"] == "INBOUND"
    order_id = order["order_id"]
    client.post(f"/api/orders/{order_id}/confirm")

    assert client.post(f"/api/orders/{order_id}/ship").status_code == 409
    assert client.post(f"/api/transfers/{shipment_id}/receive").json()["shipped"] == [order_id]
    assert client.get(f"/api/orders/{order_id}/trace").json()["order"]["status"] == "FULFILLED"


def test_a_truck_can_only_be_received_once(live):
    stock(SKU_A, NDC, 20)
    stock(SKU_A, WAREHOUSE_SOUTH, 0)
    shipment_id = client.post("/api/transfers", json={"sku_id": SKU_A, "from_location_id": NDC,
                                                      "to_location_id": WAREHOUSE_SOUTH, "qty": 6}).json()["shipment_id"]
    assert client.post(f"/api/transfers/{shipment_id}/receive").status_code == 200
    assert client.post(f"/api/transfers/{shipment_id}/receive").status_code == 409


def test_a_transfer_cant_send_more_than_is_there(live):
    stock(SKU_A, NDC, 2)
    stock(SKU_A, WAREHOUSE_SOUTH, 0)
    response = client.post("/api/transfers", json={"sku_id": SKU_A, "from_location_id": NDC,
                                                   "to_location_id": WAREHOUSE_SOUTH, "qty": 5})
    assert response.status_code == 409


def test_a_delayed_truck_reports_what_happened_to_each_hold(live):
    stock(SKU_A, NDC, 20)
    stock(SKU_A, WAREHOUSE_SOUTH, 0)
    shipment_id = client.post("/api/transfers", json={"sku_id": SKU_A, "from_location_id": NDC,
                                                      "to_location_id": WAREHOUSE_SOUTH, "qty": 6}).json()["shipment_id"]
    response = client.post(f"/api/transfers/{shipment_id}/delay", json={"delay_days": 2})
    assert response.status_code == 200
    assert set(response.json()) == {"KEEP", "REALLOCATE", "CANCEL", "REPROMISE"}


def test_overdue_job_can_run_with_nothing_to_do(live):
    assert client.post("/api/trucks/handle-overdue").json() == {"handled": []}


def shipped_sale(qty=3):
    order_id = place(qty)["order_id"]
    client.post(f"/api/orders/{order_id}/confirm")
    return client.post(f"/api/orders/{order_id}/ship").json()


def test_a_return_goes_back_on_the_shelf(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    shipped = shipped_sale()
    sale_id = shipped["movements"][0]["movement_id"]
    assert client.post("/api/returns", json={"sale_movement_id": sale_id, "qty": 2}).status_code == 201
    assert promisable_of() == 9


def test_returning_more_than_was_sold_is_refused(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    sale_id = shipped_sale()["movements"][0]["movement_id"]
    assert client.post("/api/returns", json={"sale_movement_id": sale_id, "qty": 4}).status_code == 409
    assert client.post("/api/returns", json={"sale_movement_id": sale_id, "qty": 3}).status_code == 201
    assert client.post("/api/returns", json={"sale_movement_id": sale_id, "qty": 1}).status_code == 409


def test_a_parcel_can_be_delivered_once(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 10)
    shipment_id = shipped_sale()["shipments"][0]["shipment_id"]
    assert client.post(f"/api/shipments/{shipment_id}/deliver").status_code == 200
    assert client.post(f"/api/shipments/{shipment_id}/deliver").status_code == 409


# ------------------------------------------------------------ two customers at once

def post_order(body):
    return TestClient(main.app).post("/api/orders", json=body)


def test_twenty_orders_for_one_unit_through_the_api_gives_one_winner(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 1)
    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(post_order, [order_of(1)] * 20))
    assert all(response.status_code == 201 for response in responses), [response.text for response in responses]
    statuses = [response.json()["status"] for response in responses]
    assert statuses.count("ALLOCATED") == 1
    assert statuses.count("REJECTED") == 19
    assert promisable_of() == 0


def test_opposite_line_order_through_the_api_doesnt_deadlock(live):
    stock(SKU_A, WAREHOUSE_SOUTH, 50)
    stock(SKU_B, WAREHOUSE_SOUTH, 50)
    forward = {"lines": [{"sku_id": SKU_A, "qty": 1}, {"sku_id": SKU_B, "qty": 1}]}
    backward = {"lines": [{"sku_id": SKU_B, "qty": 1}, {"sku_id": SKU_A, "qty": 1}]}
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(post_order, [forward, backward] * 10))
    assert [response.status_code for response in responses] == [201] * 20
    assert promisable_of(SKU_A) == 30
    assert promisable_of(SKU_B) == 30


def test_sku_search_puts_skus_on_sale_with_the_most_stock_first(reads):
    """This is what a page opens on, so it shouldn't be a discontinued SKU with nothing to promise."""
    rows = client.get("/api/skus", params={"limit": 300}).json()
    statuses = [row["status"] for row in rows]
    assert statuses[0] == "ACTIVE"
    assert statuses == sorted(statuses)
    assert client.get("/api/atp", params={"sku_id": rows[0]["sku_id"]}).json()["network"]["on_hand"] > 0
