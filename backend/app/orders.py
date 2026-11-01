"""
Place an order so that two customers can't both get the last unit.

The order of the steps is the whole point:

    1. Find the candidate SKU-location rows from master data, reading no stock.
    2. Sort them and lock them with SELECT ... FOR UPDATE.
    3. Only then read stock, in new statements. At READ COMMITTED each statement
       sees everything committed before it started, so it sees what the previous
       lock holder just reserved.
    4. Decide, write everything, commit. Committing releases the locks.

STORE orders are NOT BUILT YET. They place no hold and need a SALE movement at the
same moment. See docs/STATUS.md.
"""

import json
from datetime import timedelta
from decimal import Decimal

import psycopg2

from . import atp, rules

LOCK_SQL = """
SELECT sku_id, location_id
FROM sku_location
WHERE (sku_id, location_id) IN %(pairs)s
ORDER BY sku_id, location_id
FOR UPDATE
"""

LATEST_ACTIVITY_SQL = """
SELECT GREATEST(
    (SELECT MAX(e.occurred_at)
     FROM reservation r JOIN reservation_event e USING (reservation_id)
     WHERE (r.sku_id, r.location_id) IN %(pairs)s),
    (SELECT MAX(m.occurred_at) FROM inventory_movement m
     WHERE (m.sku_id, m.location_id) IN %(pairs)s)
)
"""

DEFAULT_LOCK_TIMEOUT_MS = 5000


class StockBusy(Exception):
    """Couldn't get the locks in time. Nothing was written, so it is safe to try again."""


def next_id(cursor, sequence):
    # The name comes from our own code only, never from input.
    cursor.execute(f"SELECT nextval('{sequence}')")
    return cursor.fetchone()[0]


def start_locked_transaction(cursor, lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS):
    """
    Must be the first statement. Reading fresh numbers after a lock depends on this
    level, so it is stated here rather than left to a server default.
    """
    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
    cursor.execute("SET LOCAL lock_timeout = %s", (f"{lock_timeout_ms}ms",))


def run_in_transaction(connection, work, *args, **kwargs):
    """One transaction. It all commits, or nothing is written."""
    try:
        with connection, connection.cursor() as cursor:
            return work(cursor, *args, **kwargs)
    except psycopg2.errors.LockNotAvailable as error:
        raise StockBusy("these items are busy, please try again") from error


def add_order_event(cursor, order_id, order_line_id, from_status, to_status, occurred_at, reason):
    if order_line_id is None:
        transitions = rules.ORDER_TRANSITIONS
    else:
        transitions = rules.LINE_TRANSITIONS
    if not rules.can_transition(transitions, from_status, to_status):
        raise ValueError(f"illegal transition {from_status} to {to_status}")
    cursor.execute(
        """INSERT INTO order_event (event_id, order_id, order_line_id, from_status, to_status,
                                    occurred_at, reason_code)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (next_id(cursor, "event_number"), order_id, order_line_id, from_status, to_status,
         occurred_at, reason),
    )


def add_reservation_event(cursor, reservation_id, from_status, to_status, occurred_at, reason,
                          promised_ship_by=None):
    if not rules.can_transition(rules.RESERVATION_TRANSITIONS, from_status, to_status):
        raise ValueError(f"illegal transition {from_status} to {to_status}")
    cursor.execute(
        """INSERT INTO reservation_event (event_id, reservation_id, from_status, to_status,
                                          occurred_at, reason_code, promised_ship_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (next_id(cursor, "event_number"), reservation_id, from_status, to_status, occurred_at,
         reason, promised_ship_by),
    )


def lock_pairs(cursor, pairs):
    """Every order locks in the same sorted order, so two orders can't deadlock."""
    if pairs:
        cursor.execute(LOCK_SQL, {"pairs": tuple(pairs)})
        cursor.fetchall()


def decision_time(cursor, pairs, placed_at):
    """
    Time only moves forward for a locked pair. Without this, an order stamped one
    second earlier than a hold that already committed would look back to before
    that hold existed, and take the same stock.
    """
    if not pairs:
        return placed_at
    cursor.execute(LATEST_ACTIVITY_SQL, {"pairs": tuple(pairs)})
    latest = cursor.fetchone()[0]
    return max(placed_at, latest) if latest else placed_at


def get_unit_price(cursor, sku_id):
    cursor.execute("SELECT unit_price FROM sku WHERE sku_id = %s", (sku_id,))
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"unknown SKU {sku_id}")
    return row[0]


def write_reservation(cursor, order_id, sequence_no, order_line_id, sku_id, part, channel, decided_at):
    reservation_id = f"RES-{order_id}-{sequence_no:02d}"
    expires_at = decided_at + timedelta(minutes=channel["hold_minutes"])
    promised = rules.promised_ship_by(decided_at, part["eta_at"])
    cursor.execute(
        """INSERT INTO reservation (reservation_id, order_line_id, sku_id, location_id, qty,
                                    supply_type, inbound_shipment_id, created_at, expires_at,
                                    promised_ship_by, status)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE')""",
        (reservation_id, order_line_id, sku_id, part["location_id"], part["qty"], part["supply_type"],
         part["inbound_shipment_id"], decided_at, expires_at, promised),
    )
    add_reservation_event(cursor, reservation_id, None, "ACTIVE", decided_at, "ALLOCATED", promised)

    allocation_id = f"ALC-2026-{next_id(cursor, 'allocation_number'):07d}"
    cursor.execute(
        """INSERT INTO order_allocation (allocation_id, order_line_id, location_id, qty,
                                         reservation_id, allocated_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (allocation_id, order_line_id, part["location_id"], part["qty"], reservation_id, decided_at),
    )
    return {
        "reservation_id": reservation_id,
        "allocation_id": allocation_id,
        "location_id": part["location_id"],
        "qty": part["qty"],
        "supply_type": part["supply_type"],
        "inbound_shipment_id": part["inbound_shipment_id"],
        "expires_at": expires_at,
        "promised_ship_by": promised,
    }


def write_rejected_reservation(cursor, order_id, sequence_no, order_line_id, sku_id, qty, ranked,
                               decided_at, reason):
    """A refused request is still kept, because overselling attempts are worth measuring."""
    reservation_id = f"RES-{order_id}-{sequence_no:02d}"
    location_id = ranked[0]["location_id"] if ranked else None
    cursor.execute(
        """INSERT INTO reservation (reservation_id, order_line_id, sku_id, location_id, qty,
                                    supply_type, created_at, expires_at, status)
           VALUES (%s, %s, %s, %s, %s, 'ON_HAND', %s, %s, 'REJECTED')""",
        (reservation_id, order_line_id, sku_id, location_id, qty, decided_at, decided_at),
    )
    add_reservation_event(cursor, reservation_id, None, "REJECTED", decided_at, reason)
    return reservation_id


def write_decision(cursor, order_line_id, qty, result, decided_at):
    chosen = result["parts"][0]["location_id"] if result["parts"] else None
    atp_seen = None
    promisable_seen = None
    for candidate in result["ranked"]:
        if candidate["location_id"] == chosen:
            atp_seen = candidate["atp"]
            promisable_seen = candidate["promisable"]
    decision_id = f"DEC-2026-{next_id(cursor, 'decision_number'):07d}"
    cursor.execute(
        """INSERT INTO allocation_decision (decision_id, order_line_id, decided_at, requested_qty,
                                            decision_code, reason_code, chosen_location_id,
                                            atp_seen, promisable_seen, candidates)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (decision_id, order_line_id, decided_at, qty, result["decision_code"], result["reason_code"],
         chosen, atp_seen, promisable_seen, json.dumps(rules.candidates_summary(result["ranked"]))),
    )


def place_order_in_transaction(cursor, channel_code, customer_ref, ship_to_region, lines, placed_at,
                               lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS):
    """All the work of placing an order. The caller commits, which releases the locks."""
    if not lines:
        raise ValueError("an order needs at least one line")
    for line in lines:
        if line["qty"] <= 0:
            raise ValueError(f"quantity must be above zero, got {line['qty']}")

    start_locked_transaction(cursor, lock_timeout_ms)

    channel = atp.get_channel(cursor, channel_code)
    if not channel["is_online"]:
        raise NotImplementedError("STORE orders are not built yet")

    # Step 1. Master data only, and no stock numbers are read here.
    wanted = []
    prices = {}
    for line in lines:
        prices[line["sku_id"]] = get_unit_price(cursor, line["sku_id"])
        for candidate in atp.get_candidates(cursor, line["sku_id"], channel_code, placed_at):
            wanted.append((line["sku_id"], candidate["location_id"]))
    pairs = rules.sorted_pairs(wanted)

    # Step 2. This statement waits if another order holds any of these rows.
    lock_pairs(cursor, pairs)
    locked = set(pairs)
    decided_at = decision_time(cursor, pairs, placed_at)

    order_id = f"ORD-2026-{next_id(cursor, 'order_number'):06d}"
    cursor.execute(
        """INSERT INTO orders (order_id, channel_code, customer_ref, ship_to_region, placed_at,
                               status, order_total)
           VALUES (%s, %s, %s, %s, %s, 'PLACED', 0)""",
        (order_id, channel_code, customer_ref, ship_to_region, decided_at),
    )
    add_order_event(cursor, order_id, None, None, "PLACED", decided_at, "ORDER_RECEIVED")

    order_total = Decimal("0.00")
    reservation_count = 0
    line_results = []

    for line_no, line in enumerate(lines, start=1):
        sku_id = line["sku_id"]
        qty = line["qty"]
        unit_price = prices[sku_id]
        order_line_id = f"{order_id}-L{line_no:02d}"
        cursor.execute(
            """INSERT INTO order_line (order_line_id, order_id, line_no, sku_id, qty, unit_price,
                                       line_amount, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'PENDING')""",
            (order_line_id, order_id, line_no, sku_id, qty, unit_price, qty * unit_price),
        )
        add_order_event(cursor, order_id, order_line_id, None, "PENDING", decided_at, "ORDER_RECEIVED")

        # Step 3. Fresh numbers, read after the lock. This also sees any earlier
        # line of this same order, because those rows were just written here.
        positions = atp.get_positions(cursor, sku_id, channel_code, decided_at)
        positions = [p for p in positions if (sku_id, p["location_id"]) in locked]

        # Step 4.
        result = rules.allocate(qty, positions, ship_to_region)
        write_decision(cursor, order_line_id, qty, result, decided_at)

        allocations = []
        if result["parts"]:
            for part in result["parts"]:
                reservation_count += 1
                allocations.append(write_reservation(cursor, order_id, reservation_count, order_line_id,
                                                     sku_id, part, channel, decided_at))
            line_status = "ALLOCATED"
            order_total += qty * unit_price
        else:
            reservation_count += 1
            write_rejected_reservation(cursor, order_id, reservation_count, order_line_id, sku_id, qty,
                                       result["ranked"], decided_at, result["reason_code"])
            line_status = "REJECTED"

        add_order_event(cursor, order_id, order_line_id, "PENDING", line_status, decided_at,
                        result["reason_code"])
        cursor.execute("UPDATE order_line SET status = %s WHERE order_line_id = %s",
                       (line_status, order_line_id))
        line_results.append({
            "order_line_id": order_line_id,
            "sku_id": sku_id,
            "qty": qty,
            "status": line_status,
            "decision_code": result["decision_code"],
            "reason_code": result["reason_code"],
            "allocations": allocations,
        })

    any_allocated = any(result["status"] == "ALLOCATED" for result in line_results)
    order_status = "ALLOCATED" if any_allocated else "REJECTED"
    add_order_event(cursor, order_id, None, "PLACED", order_status, decided_at,
                    "ALLOCATED" if any_allocated else "NOTHING_AVAILABLE")
    cursor.execute("UPDATE orders SET status = %s, order_total = %s WHERE order_id = %s",
                   (order_status, order_total, order_id))

    return {
        "order_id": order_id,
        "status": order_status,
        "order_total": order_total,
        "placed_at": decided_at,
        "lines": line_results,
    }


def place_order(connection, channel_code, customer_ref, ship_to_region, lines, placed_at,
                lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS):
    return run_in_transaction(connection, place_order_in_transaction, channel_code, customer_ref,
                              ship_to_region, lines, placed_at, lock_timeout_ms)
