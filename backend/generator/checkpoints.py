"""
A few ATP answers worked out a second way, for moments no order decision looks at.

The decision recheck in scripts/validate.py only asks about the second an order was decided.
ATP also changes when nobody is ordering: a hold expires, a truck arrives, a hold is
cancelled. So each checkpoint asks about one second before such an event, and the second
it happens.

These numbers come from walking through the generator's own lists in plain Python, with
no SQL. eval/check_atp.py then asks app/atp.py the same question on the loaded database,
and the two have to agree. The formula is written out here on purpose rather than taken
from app/rules.py, or a mistake there would be in both answers.
"""

from datetime import timedelta

from generator import export, inject, master

ONE_SECOND = timedelta(seconds=1)
INBOUND_HORIZON = timedelta(hours=24)

KIND_EXPIRY = "EXPIRY"
KIND_ARRIVAL = "TRUCK_ARRIVAL"
KIND_CUSTOMER_CANCEL = "CUSTOMER_CANCEL"
KIND_LATE_CANCEL = "LATE_TRUCK_CANCEL"
KINDS = [KIND_EXPIRY, KIND_ARRIVAL, KIND_CUSTOMER_CANCEL, KIND_LATE_CANCEL]

# Asked about a truck, the channel doesn't matter for ATP. This one counts inbound stock.
ARRIVAL_CHANNEL = "WEB"


class Timeline:
    """The events of every hold and truck, grouped so one pair can be looked up quickly."""

    def __init__(self, ledger):
        self.history = inject.build_history(ledger)
        self.holds_of_pair = {}
        for hold in ledger.holds:
            if hold["status"] != "REJECTED":
                self.holds_of_pair.setdefault(hold["pair_index"], []).append(hold)
        self.hold_events = {}
        # A sort that keeps ties in the order written, the same order the event IDs get.
        for event in sorted(ledger.reservation_events, key=lambda event: event[0]):
            self.hold_events.setdefault(event[1], []).append(event)

        self.trucks_to_pair = {}
        for index, shipment in enumerate(ledger.shipments):
            if shipment["shipment_type"] == "TRANSFER":
                self.trucks_to_pair.setdefault(shipment["destination_index"], []).append(index)
        self.truck_events = {}
        for event in sorted(ledger.shipment_events, key=lambda event: event[0]):
            self.truck_events.setdefault(event[1], []).append(event)


def latest_status(events, moment):
    """Both kinds of event hold the new status in the same place."""
    status = None
    for event in events:
        if event[0] > moment:
            break
        status = event[3]
    return status


def reserved_at(timeline, pair_index, moment):
    reserved = 0
    for hold in timeline.holds_of_pair.get(pair_index, []):
        status = latest_status(timeline.hold_events[hold["index"]], moment)
        if status == "CONFIRMED" or (status == "ACTIVE" and moment < hold["expires_at"]):
            reserved += hold["qty"]
    return reserved


def eta_at(events, shipment, moment):
    eta = shipment["original_eta_at"]
    for event in events:
        if event[0] > moment:
            break
        if event[5] is not None:
            eta = event[5]
    return eta


def inbound_at(ledger, timeline, pair_index, moment):
    """On the road, and due from now to 24 hours from now. An overdue truck doesn't count."""
    if not ledger.pairs[pair_index]["rule"]["allow_inbound_atp"]:
        return 0
    inbound = 0
    for truck in timeline.trucks_to_pair.get(pair_index, []):
        events = timeline.truck_events[truck]
        if latest_status(events, moment) == "IN_TRANSIT":
            eta = eta_at(events, ledger.shipments[truck], moment)
            if moment <= eta <= moment + INBOUND_HORIZON:
                inbound += ledger.shipments[truck]["qty"]
    return inbound


def answer_at(ledger, timeline, pair_index, channel_code, moment):
    sellable, blocked = inject.stock_at(timeline.history, pair_index, moment)
    on_hand = sellable + blocked
    reserved = reserved_at(timeline, pair_index, moment)
    inbound = inbound_at(ledger, timeline, pair_index, moment)
    atp = on_hand - reserved - blocked + inbound

    pair = ledger.pairs[pair_index]
    channel = ledger.channels[channel_code]
    channel_atp = on_hand - reserved - blocked + (inbound if channel["counts_inbound"] else 0)
    safety = pair["rule"]["safety_stock"] if channel["applies_safety_stock"] else 0
    buffer_units = ledger.channel_locations[(channel_code, pair["location_id"])]["buffer_units"]
    return {
        "on_hand": on_hand,
        "blocked": blocked,
        "reserved": reserved,
        "inbound": inbound,
        "atp": atp,
        "promisable": max(0, channel_atp - safety - buffer_units),
    }


def can_ask(ledger, pair_index, channel_code, moment):
    """app/atp.py only answers for a pair this channel could sell from at that time."""
    pair = ledger.pairs[pair_index]
    channel_location = ledger.channel_locations.get((channel_code, pair["location_id"]))
    if channel_location is None or (moment - ONE_SECOND).date() < channel_location["effective_from"]:
        return False
    return master.is_pair_live(pair, (moment - ONE_SECOND).date()) and master.is_pair_live(pair, moment.date())


def evenly_spaced(items, count):
    """Spread over the whole period, and the same every run."""
    step = max(1, len(items) // count)
    return items[::step][:count]


def hold_events_to_check(ledger, used_skus, count):
    """The holds that ended by expiring or being cancelled, by kind."""
    picked = {KIND_EXPIRY: [], KIND_CUSTOMER_CANCEL: [], KIND_LATE_CANCEL: []}
    for event in sorted(ledger.reservation_events, key=lambda event: event[0]):
        moment, hold_index, _from_status, to_status, reason, _promised = event
        hold = ledger.holds[hold_index]
        if hold["sku_id"] in used_skus:
            continue
        channel_code = ledger.orders[hold["order_index"]]["channel_code"]
        if to_status == "EXPIRED":
            kind = KIND_EXPIRY
        elif to_status == "CANCELLED" and reason == "CUSTOMER_CANCELLED":
            kind = KIND_CUSTOMER_CANCEL
        elif to_status == "CANCELLED":
            kind = KIND_LATE_CANCEL
        else:
            continue
        if can_ask(ledger, hold["pair_index"], channel_code, moment):
            picked[kind].append((kind, moment, hold["pair_index"], channel_code, export.reservation_id_of(hold),
                                 reason, hold["qty"]))
    return [chosen for kind in picked for chosen in evenly_spaced(picked[kind], count)]


def arrivals_to_check(ledger, timeline, used_skus, count):
    """Trucks that arrived while they still counted as inbound, which is what could double count."""
    picked = []
    for event in sorted(ledger.shipment_events, key=lambda event: event[0]):
        moment, truck, _from_status, to_status, reason, _eta = event
        shipment = ledger.shipments[truck]
        if to_status != "RECEIVED" or shipment["shipment_type"] != "TRANSFER":
            continue
        pair_index = shipment["destination_index"]
        if ledger.pairs[pair_index]["sku_id"] in used_skus or not can_ask(ledger, pair_index, ARRIVAL_CHANNEL, moment):
            continue
        if inbound_at(ledger, timeline, pair_index, moment - ONE_SECOND) >= shipment["qty"]:
            picked.append((KIND_ARRIVAL, moment, pair_index, ARRIVAL_CHANNEL, export.shipment_id_of(truck), reason,
                           shipment["qty"]))
    return evenly_spaced(picked, count)


def build_checkpoints(ledger, cases, count_per_kind):
    """
    One row before and one row at each chosen event. Run after the final numbering, so the
    IDs are the final ones. SKUs with a labelled case or decoy are left out, so this file
    can't point at one, and every checkpoint describes normal behaviour.
    """
    used_skus = {case["sku_id"] for case in cases}
    timeline = Timeline(ledger)
    chosen = hold_events_to_check(ledger, used_skus, count_per_kind) \
        + arrivals_to_check(ledger, timeline, used_skus, count_per_kind)

    rows = []
    for kind, moment, pair_index, channel_code, record_id, reason, qty in chosen:
        pair = ledger.pairs[pair_index]
        for when, asked_at in (("BEFORE", moment - ONE_SECOND), ("AT", moment)):
            rows.append({
                "checkpoint_id": f"CHK-{len(rows) + 1:05d}",
                "kind": kind,
                "moment": when,
                "record_id": record_id,
                "reason_code": reason,
                "event_qty": qty,
                "sku_id": pair["sku_id"],
                "location_id": pair["location_id"],
                "channel_code": channel_code,
                "as_of_at": asked_at,
                **answer_at(ledger, timeline, pair_index, channel_code, asked_at),
            })
    return rows
