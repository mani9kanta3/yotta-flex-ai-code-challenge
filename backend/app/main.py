"""
The API.

Run:  uvicorn app.main:app --reload --port 8000

Reads are in views.py and trace.py. Every change goes through the same functions the tests
cover, in orders.py, reservations.py, shipping.py, transfers.py, late_trucks.py and returns.py,
so this file only turns requests into calls and results into JSON.

There is no login, because this is a local demo of the rules. Anyone who can reach it can write.
"""

import json
from datetime import datetime, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import atp, config, db, detect, late_trucks, orders, reservations, returns, shipping, trace, transfers, views

app = FastAPI(title="Unified Inventory API", version="1.0.0",
              description="Stock, ATP, orders and exceptions across stores, warehouses and online channels.")

ONE_SECOND = timedelta(seconds=1)
ONE_MINUTE = timedelta(minutes=1)

Channel = Literal["WEB", "APP", "MARKETPLACE", "STORE"]
Region = Literal["NORTH", "SOUTH", "EAST", "WEST", "CENTRAL"]


def get_db():
    """One connection per request, closed however the request ends."""
    connection = db.get_connection()
    try:
        yield connection
    finally:
        connection.close()


def error_as(status_code):
    def handler(_request, error):
        return JSONResponse(status_code=status_code, content={"detail": str(error)})
    return handler


# What a function refuses to do comes back as a clear message. Anything else stays a real 500.
app.add_exception_handler(reservations.OrderStateError, error_as(409))
app.add_exception_handler(transfers.TransferError, error_as(409))
app.add_exception_handler(returns.ReturnRefused, error_as(409))
app.add_exception_handler(orders.StockBusy, error_as(503))
app.add_exception_handler(ValueError, error_as(400))
app.add_exception_handler(NotImplementedError, error_as(501))


def not_found(what):
    return HTTPException(status_code=404, detail=f"no {what}")


# ------------------------------------------------------------ reading

@app.get("/api/health")
def health(connection=Depends(get_db)):
    return {"status": "ok", "database": config.DB_NAME, "scale": loaded_scale(connection),
            "as_of": views.current_time(connection), "row_counts": views.get_row_counts(connection)}


@app.get("/api/overview")
def overview(connection=Depends(get_db)):
    return views.get_overview(connection)


@app.get("/api/options")
def options(connection=Depends(get_db)):
    return views.get_options(connection)


@app.get("/api/skus")
def skus(search: str = "", limit: int = Query(default=20, ge=1), connection=Depends(get_db)):
    return views.search_skus(connection, search, limit)


@app.get("/api/skus/{sku_id}")
def sku(sku_id: str, connection=Depends(get_db)):
    found = views.get_sku(connection, sku_id)
    if found is None:
        raise not_found(f"SKU {sku_id}")
    return found


@app.get("/api/atp")
def get_atp(sku_id: str, channel: Channel = "WEB", as_of: datetime | None = None, connection=Depends(get_db)):
    """Every term of the formula per location, plus the network total, at any time."""
    if views.get_sku(connection, sku_id) is None:
        raise not_found(f"SKU {sku_id}")
    moment = as_of or views.current_time(connection)
    with connection.cursor() as cursor:
        positions = atp.get_positions(cursor, sku_id, channel, moment)
        network_on_hand = atp.get_network_on_hand(cursor, sku_id, moment)
        in_transit = atp.get_in_transit(cursor, sku_id, moment)
    return {
        "sku_id": sku_id,
        "channel": channel,
        "as_of": moment,
        "locations": positions,
        "network": {
            "promisable": atp.network_promisable(positions),
            "atp": sum(position["atp"] for position in positions),
            "on_hand": network_on_hand,
            "in_transit": in_transit,
            "total_stock": network_on_hand + in_transit,
        },
    }


@app.get("/api/orders")
def get_orders(status: str | None = None, decision: str | None = None, search: str | None = None,
               limit: int = Query(default=25, ge=1), offset: int = Query(default=0, ge=0), connection=Depends(get_db)):
    return views.list_orders(connection, status, decision, search, limit, offset)


@app.get("/api/orders/{order_id}/trace")
def order_trace(order_id: str, connection=Depends(get_db)):
    found = trace.get_order_trace(connection, order_id)
    if found is None:
        raise not_found(f"order {order_id}")
    return found


@app.get("/api/movements")
def movements(sku_id: str, location_id: str, limit: int = Query(default=50, ge=1), connection=Depends(get_db)):
    found = views.get_ledger(connection, sku_id, location_id, limit)
    if found is None:
        raise not_found(f"stock of {sku_id} at {location_id}")
    return found


@app.get("/api/exceptions")
def exceptions(issue_type: str | None = None, limit: int = Query(default=50, ge=1), connection=Depends(get_db)):
    return views.list_exceptions(connection, issue_type, limit)


@app.get("/api/trucks")
def trucks(status: str = "IN_TRANSIT", sku_id: str | None = None, limit: int = Query(default=25, ge=1),
           connection=Depends(get_db)):
    return views.list_trucks(connection, status, sku_id, limit)


def loaded_scale(connection):
    """Which generated dataset is in the database, by the number of SKUs."""
    skus_loaded = views.get_row_counts(connection)["sku"]
    for scale in ("starter", "full"):
        manifest_path = config.DATA_DIR / scale / "source_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["scale"]["skus"] == skus_loaded:
                return scale
    return None


@app.get("/api/metrics")
def metrics(connection=Depends(get_db)):
    """The last measured results. eval/report.py wrote them, and live orders don't change them."""
    scale = loaded_scale(connection)
    path = config.DATA_DIR / str(scale) / "metrics_summary.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"nothing measured yet, run: python -m eval.report --scale {scale}")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------ orders

class OrderLine(BaseModel):
    sku_id: str
    qty: int = Field(gt=0, le=10000)


class NewOrder(BaseModel):
    channel_code: Channel = "WEB"
    ship_to_region: Region = "SOUTH"
    # An opaque code, as in the generated data. This project holds no personal data.
    customer_ref: str = Field(default="CUST-LIVE", max_length=15)
    lines: list[OrderLine] = Field(min_length=1, max_length=20)


@app.post("/api/orders", status_code=201)
def place_order(order: NewOrder, connection=Depends(get_db)):
    """Returns the decision and the holds for each line, or the rejection and its reason."""
    lines = [{"sku_id": line.sku_id, "qty": line.qty} for line in order.lines]
    return orders.place_order(connection, order.channel_code, order.customer_ref, order.ship_to_region, lines,
                              views.current_time(connection))


@app.post("/api/orders/{order_id}/confirm")
def confirm_order(order_id: str, connection=Depends(get_db)):
    reservations.confirm_order(connection, order_id, views.next_order_time(connection, order_id))
    return trace.get_order_trace(connection, order_id)


@app.post("/api/orders/{order_id}/cancel")
def cancel_order(order_id: str, connection=Depends(get_db)):
    reservations.cancel_order(connection, order_id, views.next_order_time(connection, order_id))
    return trace.get_order_trace(connection, order_id)


@app.post("/api/orders/{order_id}/ship")
def ship_order(order_id: str, connection=Depends(get_db)):
    shipping.ship_order(connection, order_id, max(views.next_order_time(connection, order_id),
                                                  views.current_time(connection) + ONE_SECOND))
    return trace.get_order_trace(connection, order_id)


class ExpireRequest(BaseModel):
    as_of: datetime | None = None


def position_at(connection, hold, moment):
    with connection.cursor() as cursor:
        positions = atp.get_positions(cursor, hold["sku_id"], hold["channel_code"], moment)
    return next((position for position in positions if position["location_id"] == hold["location_id"]), None)


@app.post("/api/reservations/expire")
def expire_reservations(request: ExpireRequest, connection=Depends(get_db)):
    """Holds released, with ATP one second before and at the moment each one ran out."""
    as_of = request.as_of or views.current_time(connection)
    holds = views.get_due_holds(connection, as_of)
    summary = reservations.expire_due(connection, as_of)
    released = []
    for hold in holds:
        before = position_at(connection, hold, hold["expires_at"] - ONE_SECOND)
        after = position_at(connection, hold, hold["expires_at"])
        released.append(dict(hold, atp_before=before and before["atp"], atp_after=after and after["atp"]))
    return dict(summary, as_of=as_of, released=released)


# ------------------------------------------------------------ trucks, parcels and returns

def next_time(connection):
    return views.current_time(connection) + ONE_SECOND


class NewTransfer(BaseModel):
    sku_id: str
    from_location_id: str
    to_location_id: str
    qty: int = Field(gt=0, le=100000)
    lead_time_days: int = Field(default=2, ge=1, le=30)


@app.post("/api/transfers", status_code=201)
def ship_transfer(transfer: NewTransfer, connection=Depends(get_db)):
    t = next_time(connection)
    return transfers.ship_transfer(connection, transfer.sku_id, transfer.from_location_id, transfer.to_location_id,
                                   transfer.qty, t, t + timedelta(days=transfer.lead_time_days))


@app.post("/api/transfers/{shipment_id}/receive")
def receive_transfer(shipment_id: str, connection=Depends(get_db)):
    """The stock reaches the shelf, and any paid order waiting on this truck ships."""
    t = next_time(connection)
    transfers.receive_transfer(connection, shipment_id, t)
    return dict(shipping.ship_waiting_orders(connection, shipment_id, t + ONE_SECOND), received_at=t)


class TruckDelay(BaseModel):
    delay_days: int = Field(gt=0, le=30)


@app.post("/api/transfers/{shipment_id}/delay")
def delay_transfer(shipment_id: str, delay: TruckDelay, connection=Depends(get_db)):
    """A new ETA for the truck. Each paid hold on it is kept, moved, re-promised or cancelled."""
    t = next_time(connection)
    return late_trucks.report_late_truck(connection, shipment_id, t + timedelta(days=delay.delay_days), t)


@app.post("/api/trucks/handle-overdue")
def handle_overdue_trucks(connection=Depends(get_db)):
    return {"handled": late_trucks.handle_overdue_trucks(connection, next_time(connection))}


@app.post("/api/shipments/{shipment_id}/deliver")
def deliver_shipment(shipment_id: str, connection=Depends(get_db)):
    t = next_time(connection)
    shipping.deliver_shipment(connection, shipment_id, t)
    return {"shipment_id": shipment_id, "delivered_at": t}


class NewReturn(BaseModel):
    sale_movement_id: str
    qty: int = Field(gt=0)
    condition: Literal["GOOD", "DAMAGED"] = "GOOD"


@app.post("/api/returns", status_code=201)
def record_return(item: NewReturn, connection=Depends(get_db)):
    return returns.record_return(connection, item.sale_movement_id, item.qty, item.condition, next_time(connection))


# ------------------------------------------------------------ detectors

@app.post("/api/exceptions/run")
def run_detectors(connection=Depends(get_db)):
    """Runs the four detectors again and replaces their last results. About a minute at full scale."""
    with connection, connection.cursor() as cursor:
        found = detect.find_exceptions(cursor)
        detect.write_exceptions(cursor, found)
    return {"found": len(found)}
