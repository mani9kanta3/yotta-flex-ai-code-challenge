"""
Moving stock from one location to another.

    ship_transfer     TRANSFER_OUT. Stock leaves the source now.
    delay_transfer    Records a new ETA. No stock moves.
    receive_transfer  TRANSFER_IN. Stock reaches the destination now.

Between shipping and receiving the stock is on neither location's on_hand, and the
shipment is the only thing that counts it. The TRANSFER_IN row and the RECEIVED event
share one timestamp and one transaction, so at no moment is a truck counted as inbound
and as on hand at once. That is what stops stock being counted twice.
"""

from . import atp, orders, rules
from .movements import add_movement


class TransferError(Exception):
    """The transfer can't do this. Nothing was written."""


def add_shipment_event(cursor, shipment_id, from_status, to_status, occurred_at, reason, eta_at=None):
    if not rules.can_transition(rules.SHIPMENT_TRANSITIONS, from_status, to_status):
        raise ValueError(f"illegal transition {from_status} to {to_status}")
    cursor.execute(
        """INSERT INTO shipment_event (event_id, shipment_id, from_status, to_status, occurred_at,
                                       reason_code, eta_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (orders.next_id(cursor, "event_number"), shipment_id, from_status, to_status, occurred_at,
         reason, eta_at),
    )


def get_shipment_state(cursor, shipment_id, t):
    """From the events, so asking about a past time gives the answer it gave then."""
    cursor.execute(
        """SELECT to_status FROM shipment_event
           WHERE shipment_id = %s AND occurred_at <= %s
           ORDER BY occurred_at DESC, event_id DESC LIMIT 1""",
        (shipment_id, t),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def lock_transfer(cursor, shipment_id):
    """Stops two people receiving the same truck at the same moment."""
    cursor.execute(
        """SELECT status, transfer_sku_id, to_location_id, transfer_qty
           FROM shipment WHERE shipment_id = %s AND shipment_type = 'TRANSFER' FOR UPDATE""",
        (shipment_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise TransferError(f"no transfer {shipment_id}")
    return {"status": row[0], "sku_id": row[1], "to_location_id": row[2], "qty": row[3]}


def get_last_event_time(cursor, shipment_id):
    cursor.execute("SELECT MAX(occurred_at) FROM shipment_event WHERE shipment_id = %s", (shipment_id,))
    return cursor.fetchone()[0]


def is_stocked(cursor, sku_id, location_id):
    cursor.execute("SELECT 1 FROM sku_location WHERE sku_id = %s AND location_id = %s",
                   (sku_id, location_id))
    return cursor.fetchone() is not None


def ship_transfer_in_transaction(cursor, sku_id, from_location_id, to_location_id, qty, t, eta_at,
                                 lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    """The same order of steps as placing an order: lock first, then read the stock."""
    orders.start_locked_transaction(cursor, lock_timeout_ms)

    if qty <= 0:
        raise TransferError(f"quantity must be above zero, got {qty}")
    if from_location_id == to_location_id:
        raise TransferError("a transfer needs two different locations")
    if not is_stocked(cursor, sku_id, to_location_id):
        raise TransferError(f"{to_location_id} doesn't stock {sku_id}")

    # A transfer reduces what the source can promise, so it takes the same lock as
    # an order does. Otherwise both could take the same last unit.
    pair = (sku_id, from_location_id)
    orders.lock_pairs(cursor, [pair])
    shipped_at = orders.decision_time(cursor, [pair], t)
    if eta_at <= shipped_at:
        raise TransferError(f"the ETA {eta_at} has to be after shipping at {shipped_at}")

    on_hand, blocked = atp.get_stock(cursor, sku_id, from_location_id, shipped_at)
    reserved = atp.get_reserved(cursor, sku_id, from_location_id, shipped_at)
    sellable = on_hand - blocked
    if not rules.can_ship_transfer(qty, sellable, reserved):
        free = rules.walk_in_available(sellable, reserved)
        raise TransferError(f"{from_location_id} has {free} free of {sku_id}, can't ship {qty}")

    shipment_id = f"SHP-2026-{orders.next_id(cursor, 'shipment_number'):06d}"
    cursor.execute(
        """INSERT INTO shipment (shipment_id, shipment_type, status, from_location_id, to_location_id,
                                 created_at, original_eta_at, transfer_sku_id, transfer_qty)
           VALUES (%s, 'TRANSFER', 'IN_TRANSIT', %s, %s, %s, %s, %s, %s)""",
        (shipment_id, from_location_id, to_location_id, shipped_at, eta_at, sku_id, qty),
    )
    add_shipment_event(cursor, shipment_id, None, "CREATED", shipped_at, "TRANSFER_PLANNED")
    add_shipment_event(cursor, shipment_id, "CREATED", "IN_TRANSIT", shipped_at, "DISPATCHED")
    add_movement(cursor, sku_id, from_location_id, "TRANSFER_OUT", -qty, shipped_at, "REPLENISHMENT",
                 f"TRO-{shipment_id}", shipment_id=shipment_id)
    return {"shipment_id": shipment_id, "shipped_at": shipped_at}


def receive_transfer_in_transaction(cursor, shipment_id, t, lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    orders.start_locked_transaction(cursor, lock_timeout_ms)
    transfer = lock_transfer(cursor, shipment_id)

    # Checked on the time asked for. A truck can't arrive before it left, and moving
    # the time forward below must not hide that.
    last_event_at = get_last_event_time(cursor, shipment_id)
    if not rules.can_receive(transfer["status"], last_event_at, t):
        raise TransferError(f"can't receive {shipment_id} at {t}, it is {transfer['status']} "
                            f"and its last event was at {last_event_at}")

    pair = (transfer["sku_id"], transfer["to_location_id"])
    orders.lock_pairs(cursor, [pair])
    received_at = orders.decision_time(cursor, [pair], t)

    add_movement(cursor, transfer["sku_id"], transfer["to_location_id"], "TRANSFER_IN", transfer["qty"],
                 received_at, "REPLENISHMENT", f"TRI-{shipment_id}", shipment_id=shipment_id)
    add_shipment_event(cursor, shipment_id, "IN_TRANSIT", "RECEIVED", received_at, "ARRIVED")
    cursor.execute("UPDATE shipment SET status = 'RECEIVED' WHERE shipment_id = %s", (shipment_id,))
    return {"shipment_id": shipment_id, "received_at": received_at}


def delay_transfer_in_transaction(cursor, shipment_id, new_eta_at, t, reason="ETA_MISSED",
                                  lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    """The first ETA stays on the shipment. The new one is an event, so history shows both."""
    orders.start_locked_transaction(cursor, lock_timeout_ms)
    transfer = lock_transfer(cursor, shipment_id)
    if transfer["status"] != "IN_TRANSIT":
        raise TransferError(f"can't change the ETA of {shipment_id}, it is {transfer['status']}")
    if t < get_last_event_time(cursor, shipment_id):
        raise TransferError(f"{t} is before the last event on {shipment_id}")
    if new_eta_at <= t:
        raise TransferError(f"the new ETA {new_eta_at} has to be after {t}")
    add_shipment_event(cursor, shipment_id, "IN_TRANSIT", "IN_TRANSIT", t, reason, new_eta_at)


def ship_transfer(connection, sku_id, from_location_id, to_location_id, qty, t, eta_at,
                  lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    return orders.run_in_transaction(connection, ship_transfer_in_transaction, sku_id, from_location_id,
                                     to_location_id, qty, t, eta_at, lock_timeout_ms)


def receive_transfer(connection, shipment_id, t, lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    return orders.run_in_transaction(connection, receive_transfer_in_transaction, shipment_id, t,
                                     lock_timeout_ms)


def delay_transfer(connection, shipment_id, new_eta_at, t, reason="ETA_MISSED"):
    return orders.run_in_transaction(connection, delay_transfer_in_transaction, shipment_id, new_eta_at,
                                     t, reason)
