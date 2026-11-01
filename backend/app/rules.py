"""
The stock rules, as plain functions with no database.

The generator, the order flow and the tests all use these, so there is one
written version of each rule. docs/PLAN.md has the reasoning.
"""

from datetime import timedelta

# How far ahead a shipment's ETA can be and still count as inbound.
INBOUND_HORIZON_HOURS = 24

# We promise to ship within this long of having the stock.
PROMISE_HOURS = 24

# A customer waits at most this long past their first promise for a late truck.
LATE_LIMIT_DAYS = 3

# Warehouses ship online orders first, then the NDC, and stores last so that
# their shelves are kept for walk-in customers.
LOCATION_TYPE_ORDER = {"WAREHOUSE": 0, "NDC": 1, "STORE": 2}

# Which status can follow which. None is the first event of a record.
ORDER_TRANSITIONS = {
    None: {"PLACED"},
    "PLACED": {"ALLOCATED", "REJECTED"},
    "ALLOCATED": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"FULFILLED", "CANCELLED"},
}
LINE_TRANSITIONS = {
    None: {"PENDING"},
    "PENDING": {"ALLOCATED", "REJECTED"},
    "ALLOCATED": {"SHIPPED", "CANCELLED"},
}
RESERVATION_TRANSITIONS = {
    None: {"ACTIVE", "REJECTED"},
    "ACTIVE": {"CONFIRMED", "EXPIRED", "CANCELLED"},
    # CONFIRMED to CONFIRMED only records a new promised date.
    "CONFIRMED": {"CONFIRMED", "CONSUMED", "CANCELLED"},
}
SHIPMENT_TRANSITIONS = {
    None: {"CREATED"},
    "CREATED": {"IN_TRANSIT", "CANCELLED"},
    # IN_TRANSIT to IN_TRANSIT only records a new ETA.
    "IN_TRANSIT": {"IN_TRANSIT", "DELIVERED", "RECEIVED"},
}


def can_transition(transitions, from_status, to_status):
    return to_status in transitions.get(from_status, set())


# ------------------------------------------------------------ walk-in sales

def on_hand(sellable, blocked):
    """Blocked units are still physically here, so they count as on hand."""
    return sellable + blocked


def walk_in_available(sellable, reserved):
    """
    What a walk-in customer can buy: on the shelf, not damaged, not held for an
    online order. Written out, this is on_hand - blocked - reserved.
    """
    return sellable - reserved


def can_sell_walk_in(qty, sellable, reserved):
    # A negative quantity would pass the second test and add stock.
    return qty > 0 and qty <= walk_in_available(sellable, reserved)


# ------------------------------------------------------------ reservations

def state_at(events, t):
    """
    The status of a record at time t, from its events. None if it had no event yet.

    Each event is (occurred_at, event_id, to_status). The event_id breaks a tie
    when two events share a timestamp.
    """
    latest = None
    for event in events:
        occurred_at, event_id, _to_status = event
        if occurred_at > t:
            continue
        if latest is None or (occurred_at, event_id) > (latest[0], latest[1]):
            latest = event
    return latest[2] if latest else None


def holds_stock(state, t, expires_at):
    """
    An ACTIVE hold stops holding at exactly expires_at, whether or not the
    expiry job has run yet. A CONFIRMED hold has been paid for, so expiry no
    longer applies.
    """
    if state == "CONFIRMED":
        return True
    return state == "ACTIVE" and t < expires_at


def reserved_at(reservations, t):
    """Each reservation is a dict with qty, expires_at and events."""
    total = 0
    for reservation in reservations:
        state = state_at(reservation["events"], t)
        if holds_stock(state, t, reservation["expires_at"]):
            total += reservation["qty"]
    return total


def can_confirm(state, t, expires_at):
    return state == "ACTIVE" and t < expires_at


# ------------------------------------------------------------ ATP

def available_to_promise(on_hand_qty, reserved, blocked, inbound):
    """The brief's formula, exactly as written."""
    return on_hand_qty - reserved - blocked + inbound


def promisable(atp, safety_stock, buffer_units):
    return max(0, atp - safety_stock - buffer_units)


def position(on_hand_qty, blocked, reserved, inbound, safety_stock, buffer_units,
             counts_inbound, applies_safety_stock):
    """
    Every number for one channel at one location.

    atp always includes approved inbound, as the brief says. channel_atp drops
    it for a channel that can't sell stock still on a truck.
    """
    safety = safety_stock if applies_safety_stock else 0
    inbound_counted = inbound if counts_inbound else 0
    channel_atp = available_to_promise(on_hand_qty, reserved, blocked, inbound_counted)
    return {
        "on_hand": on_hand_qty,
        "blocked": blocked,
        "reserved": reserved,
        "inbound": inbound,
        "atp": available_to_promise(on_hand_qty, reserved, blocked, inbound),
        "channel_atp": channel_atp,
        "safety_stock": safety,
        "buffer_units": buffer_units,
        "promisable": promisable(channel_atp, safety, buffer_units),
        # What can ship today, with no help from inbound stock.
        "from_on_hand": promisable(available_to_promise(on_hand_qty, reserved, blocked, 0),
                                   safety, buffer_units),
    }


# ------------------------------------------------------------ allocation

def rank_candidates(candidates, region):
    """Same region, then warehouse before store, then most promisable, then lowest ID."""
    def sort_key(candidate):
        return (
            0 if candidate["region"] == region else 1,
            LOCATION_TYPE_ORDER[candidate["location_type"]],
            -candidate["promisable"],
            candidate["location_id"],
        )
    return sorted(candidates, key=sort_key)


def allocate(qty, candidates, region):
    """
    Decide where one order line comes from, trying these in order:

        1. one location, from on-hand stock
        2. two locations, from on-hand stock
        3. one location, from one inbound shipment
        4. reject

    A line is either covered in full or rejected. Never partly filled.
    """
    if qty <= 0:
        raise ValueError(f"quantity must be above zero, got {qty}")

    ranked = rank_candidates(candidates, region)

    for candidate in ranked:
        if candidate["from_on_hand"] >= qty:
            parts = [on_hand_part(candidate, qty)]
            return decision("SINGLE", "COVERED_FROM_ON_HAND", parts, ranked)

    for first_index, first in enumerate(ranked):
        if first["from_on_hand"] <= 0:
            continue
        for second in ranked[first_index + 1:]:
            if second["from_on_hand"] > 0 and first["from_on_hand"] + second["from_on_hand"] >= qty:
                parts = [
                    on_hand_part(first, first["from_on_hand"]),
                    on_hand_part(second, qty - first["from_on_hand"]),
                ]
                return decision("SPLIT", "SPLIT_ACROSS_TWO_LOCATIONS", parts, ranked)

    for candidate in ranked:
        if candidate["promisable"] < qty:
            continue
        # Soonest arrival first, so the customer waits as little as possible.
        for shipment in candidate["inbound_shipments"]:
            if shipment["free_qty"] >= qty:
                part = {
                    "location_id": candidate["location_id"],
                    "qty": qty,
                    "supply_type": "INBOUND",
                    "inbound_shipment_id": shipment["shipment_id"],
                    "eta_at": shipment["eta_at"],
                }
                return decision("INBOUND", "COVERED_FROM_INBOUND", [part], ranked)

    reason = "INSUFFICIENT_ATP" if ranked else "NO_CANDIDATE_LOCATION"
    return decision("REJECTED", reason, [], ranked)


def on_hand_part(candidate, qty):
    return {
        "location_id": candidate["location_id"],
        "qty": qty,
        "supply_type": "ON_HAND",
        "inbound_shipment_id": None,
        "eta_at": None,
    }


def decision(decision_code, reason_code, parts, ranked):
    return {"decision_code": decision_code, "reason_code": reason_code, "parts": parts, "ranked": ranked}


def candidates_summary(ranked):
    """The top three, kept as evidence of why this decision was made."""
    summary = []
    for candidate in ranked[:3]:
        summary.append({
            "location_id": candidate["location_id"],
            "atp": candidate["atp"],
            "promisable": candidate["promisable"],
            "from_on_hand": candidate["from_on_hand"],
            "safety_stock": candidate["safety_stock"],
            "buffer_units": candidate["buffer_units"],
        })
    return summary


def promised_ship_by(decided_at, eta_at):
    """From when we have the stock: now if on hand, or when the truck is due."""
    return (eta_at or decided_at) + timedelta(hours=PROMISE_HOURS)


def sorted_pairs(sku_location_pairs):
    """
    The order every transaction locks in. If everyone asks for the same rows in
    the same order, no two transactions can each hold what the other needs.
    """
    return sorted(set(sku_location_pairs))


# ------------------------------------------------------------ transfers

def can_ship_transfer(qty, sellable, reserved):
    """A truck can only take what is on the shelf and not held for an order."""
    return qty > 0 and qty <= walk_in_available(sellable, reserved)


def transfer_quantity(wanted, sellable, reserved, safety_stock):
    """
    How much a source sends when a location asks for stock. It keeps its own safety
    stock back, so one store's reorder can't empty the warehouse for everyone else.
    """
    spare = walk_in_available(sellable, reserved) - safety_stock
    return max(0, min(wanted, spare))


def can_receive(status, last_event_at, t):
    """Only a truck on the road can arrive, and only after everything already recorded."""
    return can_transition(SHIPMENT_TRANSITIONS, status, "RECEIVED") and t > last_event_at


def can_ship_hold(supply_type, shipment_state):
    """Stock on a truck can't go to a customer until the truck has been received."""
    return supply_type == "ON_HAND" or shipment_state == "RECEIVED"


# ------------------------------------------------------------ late trucks

def late_promise(eta_at, t):
    """A truck that is overdue with no new ETA can't arrive any earlier than now."""
    return max(eta_at, t) + timedelta(hours=PROMISE_HOURS)


def choose_reallocation(qty, candidates, region):
    """The best-ranked location that can cover the whole hold from on-hand stock, or None."""
    for candidate in rank_candidates(candidates, region):
        if candidate["from_on_hand"] >= qty:
            return candidate
    return None


def late_truck_action(can_move, first_promise, current_promise, new_promise):
    """
    What to do with a paid hold on a late truck, in this order:

        1. the first promise can still be kept: leave it alone
        2. another location has the stock: move the hold there
        3. past the limit after the first promise: cancel, which releases the hold
        4. the promise has slipped again: update it
        5. otherwise: leave it, and look again next time

    The limit counts from the first promise, so repeated small delays can't stretch it.
    A hold keeps looking for another location for as long as it waits.
    """
    if new_promise <= first_promise:
        return "KEEP"
    if can_move:
        return "REALLOCATE"
    if new_promise > first_promise + timedelta(days=LATE_LIMIT_DAYS):
        return "CANCEL"
    if new_promise > current_promise:
        return "REPROMISE"
    return "KEEP"


# ------------------------------------------------------------ returns

# A good item goes back on the shelf. A damaged one is still physically here, so it
# counts as on hand, but it can't be sold.
RETURN_BUCKETS = {"GOOD": "SELLABLE", "DAMAGED": "BLOCKED"}


def can_return(qty, sold, already_returned):
    """A sale can come back in parts, but never more in total than was sold."""
    return qty > 0 and qty <= sold - already_returned
