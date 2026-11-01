"""
Adds the four labelled problems from the brief to a finished clean baseline, plus
decoys that look suspicious but are valid.

Each case has to cause only its own problem. Stock changes are the risk, because a row
added in the past changes the answer to every later question about that SKU. So:

    - Each SKU is used by one case at most, so cases can't interact.
    - A case only goes on a SKU after its last order activity, with no truck on the road,
      so no later order decision depends on the changed numbers.
    - Stock a case takes has to still be there at every later point in the ledger.

Nothing here reads a detector, and nothing in app/ reads what this writes.
"""

import bisect
from datetime import timedelta

from app import config, rules
from generator import export, master, order_flow, settings

ONE_SECOND = timedelta(seconds=1)
STORE_OPEN_HOURS = 9
STORE_CLOSE_HOURS = 21

# How many slots to try per case before giving up on a SKU.
SLOT_TRIES = 10

# A case goes within this many days of the SKU's last order, so the random stop dates set
# in the simulator spread the cases over the whole period.
SLOT_WINDOW_DAYS = 14

ONLINE_CHANNELS = ["WEB", "APP", "MARKETPLACE"]

RECOMMENDED_DECISIONS = {
    settings.ISSUE_BALANCE: "RECOUNT_AND_TRUST_THE_LEDGER",
    settings.ISSUE_RESERVATION: "CANCEL_OR_REALLOCATE",
    settings.ISSUE_BLOCKED: "QUARANTINE_AND_REVIEW",
    settings.ISSUE_TRANSFER: "EXCLUDE_FROM_ATP_UNTIL_RECEIVED",
}


# ------------------------------------------------------------ stock at a past time

def build_history(ledger):
    """
    For each pair: the time of each movement, the stock straight after it, and the lowest
    the stock gets from that point on. That last one says how much can safely be taken.
    """
    rows_of_pair = {index: [] for index in ledger.reserved}
    for row in ledger.movements:
        rows_of_pair[row[0]].append(row)

    history = {}
    for pair_index, rows in rows_of_pair.items():
        pair = ledger.pairs[pair_index]
        sellable = pair["opening_on_hand"] - pair["opening_blocked"]
        blocked = pair["opening_blocked"]
        times = [config.START_AT]
        sellable_after = [sellable]
        blocked_after = [blocked]
        for row in rows:
            if row[2] == "SELLABLE":
                sellable += row[3]
            else:
                blocked += row[3]
            times.append(row[4])
            sellable_after.append(sellable)
            blocked_after.append(blocked)
        history[pair_index] = {
            "times": times,
            "sellable": sellable_after,
            "blocked": blocked_after,
            "lowest_sellable": lowest_from_here(sellable_after),
            "lowest_blocked": lowest_from_here(blocked_after),
        }
    return history


def lowest_from_here(values):
    lowest = list(values)
    for index in range(len(lowest) - 2, -1, -1):
        lowest[index] = min(lowest[index], lowest[index + 1])
    return lowest


def position_in_history(pair_history, moment):
    return bisect.bisect_right(pair_history["times"], moment) - 1


def stock_at(history, pair_index, moment):
    """Returns (sellable, blocked) at this moment."""
    pair_history = history[pair_index]
    position = position_in_history(pair_history, moment)
    return pair_history["sellable"][position], pair_history["blocked"][position]


def spare_sellable(history, pair_index, moment):
    """The most that can leave at this moment without a later row going below zero."""
    pair_history = history[pair_index]
    return pair_history["lowest_sellable"][position_in_history(pair_history, moment)]


def spare_blocked(history, pair_index, moment):
    pair_history = history[pair_index]
    return pair_history["lowest_blocked"][position_in_history(pair_history, moment)]


def is_still(history, pair_index, start, end):
    """No movement on this pair between these two times."""
    times = history[pair_index]["times"]
    return bisect.bisect_right(times, start) == bisect.bisect_right(times, end)


def positions_at(ledger, history, sku_id, channel_code, moment):
    """
    The same numbers as order_flow.get_positions, at a past moment. Slots are chosen so
    nothing is reserved and no truck is on the road, so both of those are zero here.
    """
    channel = ledger.channels[channel_code]
    positions = []
    for pair_index in ledger.pairs_of_sku[sku_id]:
        pair = ledger.pairs[pair_index]
        channel_location = ledger.channel_locations.get((channel_code, pair["location_id"]))
        if channel_location is None or moment.date() < channel_location["effective_from"]:
            continue
        if not master.is_pair_live(pair, moment.date()):
            continue
        sellable, blocked = stock_at(history, pair_index, moment)
        position = rules.position(rules.on_hand(sellable, blocked), blocked, 0, 0, pair["rule"]["safety_stock"],
                                  channel_location["buffer_units"], channel["counts_inbound"],
                                  channel["applies_safety_stock"])
        position["location_id"] = pair["location_id"]
        position["location_type"] = pair["location"]["location_type"]
        position["region"] = pair["location"]["region"]
        position["inbound_shipments"] = []
        position["pair_index"] = pair_index
        positions.append(position)
    return positions


# ------------------------------------------------------------ where a case can go

def build_quiet_times(ledger):
    """For each SKU: the time after which nothing about any order touches it."""
    quiet_from = {sku_id: config.START_AT for sku_id in ledger.sku_ids}
    for event in ledger.reservation_events:
        sku_id = ledger.holds[event[1]]["sku_id"]
        quiet_from[sku_id] = max(quiet_from[sku_id], event[0])
    for hold in ledger.holds:
        if hold["status"] in ("ACTIVE", "CONFIRMED"):
            quiet_from[hold["sku_id"]] = config.AS_OF
    return quiet_from


def build_truck_times(ledger):
    """For each SKU: when each of its trucks was on the road."""
    arrivals = {row[8]: row[4] for row in ledger.movements if row[1] == "TRANSFER_IN"}
    trucks = {}
    for index, shipment in enumerate(ledger.shipments):
        if shipment["shipment_type"] == "TRANSFER":
            sku_id = ledger.pairs[shipment["source_index"]]["sku_id"]
            arrived_at = arrivals.get(index, config.AS_OF)
            trucks.setdefault(sku_id, []).append((shipment["shipped_at"], arrived_at))
    return trucks


def has_truck_on_the_road(trucks_of_sku, start, end):
    return any(shipped_at <= end and start <= arrived_at for shipped_at, arrived_at in trucks_of_sku)


class Injector:
    """What every case needs to look at. A class only to avoid passing six values around."""

    def __init__(self, ledger, rng):
        self.ledger = ledger
        self.rng = rng
        self.history = build_history(ledger)
        self.quiet_from = build_quiet_times(ledger)
        self.trucks = build_truck_times(ledger)
        # SKUs whose orders stopped early come first. They are what spreads the cases out.
        stopped_early = [sku_id for sku_id in ledger.sku_ids if sku_id in ledger.orders_stop_on]
        the_rest = [sku_id for sku_id in ledger.sku_ids if sku_id not in ledger.orders_stop_on]
        rng.shuffle(stopped_early)
        rng.shuffle(the_rest)
        self.free_skus = stopped_early + the_rest
        self.cases = []

    def find_slot(self, sku_id, hours_needed):
        """A time in store hours after the SKU's last order activity, with no truck about."""
        sku = self.ledger.pairs[self.ledger.pairs_of_sku[sku_id][0]]["sku"]
        first_day = max(self.quiet_from[sku_id].date() + timedelta(days=1), sku["effective_from"])
        last_day = min(first_day + timedelta(days=SLOT_WINDOW_DAYS), config.AS_OF.date() - timedelta(days=2))
        if sku["effective_to"] is not None:
            last_day = min(last_day, sku["effective_to"] - timedelta(days=2))
        if first_day > last_day:
            return None
        trucks_of_sku = self.trucks.get(sku_id, [])
        for _ in range(SLOT_TRIES):
            day = first_day + timedelta(days=self.rng.randint(0, (last_day - first_day).days))
            seconds = self.rng.randint(STORE_OPEN_HOURS * 3600, STORE_CLOSE_HOURS * 3600)
            moment = master.day_start(day) + timedelta(seconds=seconds)
            if not has_truck_on_the_road(trucks_of_sku, moment - ONE_SECOND, moment + timedelta(hours=hours_needed)):
                return moment
        return None

    def place_case(self, label, try_sku):
        """Goes through the free SKUs until one fits this case. A SKU that fits is used up."""
        for position, sku_id in enumerate(self.free_skus):
            case = try_sku(sku_id)
            if case is not None:
                del self.free_skus[position]
                case["sku_id"] = sku_id
                self.cases.append(case)
                return
        raise RuntimeError(f"could not place {label}, no SKU left that it fits. Lower the count or add SKUs")


# ------------------------------------------------------------ order cases

def add_order(ledger, position, channel_code, sku_id, qty, moment):
    """An order, one line, one hold, decided for this location. Returns the order and hold."""
    order = order_flow.new_order(ledger, channel_code, position["region"], moment)
    line = order_flow.new_line(ledger, order, 1, sku_id, qty, moment)
    part = rules.on_hand_part(position, qty)
    return order, line, part


def hold_end_time(ledger, channel_code, moment):
    return moment + timedelta(minutes=ledger.channels[channel_code]["hold_minutes"])


def try_reservation(injector, sku_id, is_decoy):
    """
    A hold above what could be promised, or a decoy of exactly what could. Both expire
    unpaid like thousands of other orders. The hold is only there for its short life,
    and the SKU has no later decision, so it changes no other answer.
    """
    ledger = injector.ledger
    channel_code = injector.rng.choice(ONLINE_CHANNELS)
    hours = ledger.channels[channel_code]["hold_minutes"] / 60
    moment = injector.find_slot(sku_id, hours)
    if moment is None:
        return None
    positions = positions_at(ledger, injector.history, sku_id, channel_code, moment)
    end = hold_end_time(ledger, channel_code, moment)

    for region in ledger.regions:
        ranked = rules.rank_candidates(positions, region)
        target = next((p for p in ranked if p["from_on_hand"] > 0), None)
        if target is None or not is_still(injector.history, target["pair_index"], moment, end):
            continue
        if is_decoy:
            qty = target["from_on_hand"]
            result = rules.allocate(qty, positions, region)
        else:
            qty = target["from_on_hand"] + injector.rng.randint(1, 3)
            # What a stale cache would have seen: enough stock, when there wasn't.
            stale = dict(target, atp=target["atp"] + qty, promisable=qty, from_on_hand=qty)
            stale_ranked = [stale if p is target else p for p in ranked]
            result = rules.decision("SINGLE", "COVERED_FROM_ON_HAND", [rules.on_hand_part(stale, qty)], stale_ranked)
        return write_reservation_case(ledger, target, sku_id, channel_code, region, qty, result, moment, end, is_decoy)
    return None


def write_reservation_case(ledger, target, sku_id, channel_code, region, qty, result, moment, end, is_decoy):
    order = order_flow.new_order(ledger, channel_code, region, moment)
    line = order_flow.new_line(ledger, order, 1, sku_id, qty, moment)
    order_flow.add_decision(ledger, order, line, qty, result, moment)
    hold = order_flow.add_active_hold(ledger, order, line, result["parts"][0], moment, end)
    order["total"] += qty * line["unit_price"]
    order_flow.set_line_status(ledger, order, line, "ALLOCATED", moment, result["reason_code"])
    order_flow.set_order_status(ledger, order, "ALLOCATED", moment, "ALLOCATED")
    order_flow.expire_order(ledger, order, end)
    return {
        "issue": settings.ISSUE_RESERVATION,
        "is_decoy": is_decoy,
        "record_type": "reservation",
        "hold": hold,
        "order": order,
        "expected_value": target["from_on_hand"],
        "actual_value": qty,
    }


def ship_injected_order(ledger, order, hold, bucket, moment):
    """The same steps as order_flow.ship_order, from either bucket. Returns the SALE's position."""
    parcel = ledger.add_customer_shipment(order, hold["location_id"], moment)
    hold["customer_shipment"] = parcel
    if bucket == "SELLABLE":
        ledger.sellable[hold["pair_index"]] -= hold["qty"]
    else:
        ledger.blocked[hold["pair_index"]] -= hold["qty"]
    ledger.reserved[hold["pair_index"]] -= hold["qty"]
    # A damaged bin scanned by mistake looks exactly like a normal shipment on the row.
    ledger.add_row(hold["pair_index"], "SALE", bucket, -hold["qty"], moment, "ORDER_SHIPPED", ledger.next_doc("SALE"),
                   shipment_index=parcel, allocation_no=hold["allocation_no"])
    order_flow.set_hold_status(ledger, hold, "CONSUMED", moment, "SHIPPED")
    order_flow.set_line_status(ledger, order, hold["line"], "SHIPPED", moment, "SHIPPED")
    order_flow.set_order_status(ledger, order, "FULFILLED", moment, "SHIPPED")
    return len(ledger.movements) - 1


def try_blocked(injector, sku_id, is_decoy):
    """
    An order the rules allow, at a location holding damaged stock. The anomaly's SALE draws
    from the damaged stock. The decoy's SALE draws from the shelf, which is fine. Selling
    blocked stock lowers on-hand and blocked together, so ATP doesn't change.
    """
    ledger = injector.ledger
    channel_code = injector.rng.choice(ONLINE_CHANNELS)
    moment = injector.find_slot(sku_id, 36)
    if moment is None:
        return None
    positions = positions_at(ledger, injector.history, sku_id, channel_code, moment)
    # Paid inside the hold and shipped the next morning, the same as any other order.
    pay_at = moment + timedelta(minutes=injector.rng.randint(1, ledger.channels[channel_code]["hold_minutes"] - 1))
    ship_at = master.day_start(pay_at.date() + timedelta(days=1)) + timedelta(
        seconds=injector.rng.randint(0, STORE_OPEN_HOURS * 3600))

    for region in ledger.regions:
        result = rules.allocate(1, positions, region)
        if result["decision_code"] != "SINGLE":
            continue
        chosen = next(p for p in positions if p["location_id"] == result["parts"][0]["location_id"])
        pair_index = chosen["pair_index"]
        if not is_still(injector.history, pair_index, moment, ship_at):
            continue
        if spare_blocked(injector.history, pair_index, moment) < 1:
            continue
        if is_decoy and spare_sellable(injector.history, pair_index, moment) < 1:
            continue
        return write_blocked_case(ledger, chosen, sku_id, channel_code, region, result, moment, pay_at, ship_at, is_decoy)
    return None


def write_blocked_case(ledger, chosen, sku_id, channel_code, region, result, moment, pay_at, ship_at, is_decoy):
    order = order_flow.new_order(ledger, channel_code, region, moment)
    line = order_flow.new_line(ledger, order, 1, sku_id, 1, moment)
    order_flow.add_decision(ledger, order, line, 1, result, moment)
    hold = order_flow.add_active_hold(ledger, order, line, result["parts"][0], moment,
                                      hold_end_time(ledger, channel_code, moment))
    order["total"] += line["unit_price"]
    order_flow.set_line_status(ledger, order, line, "ALLOCATED", moment, result["reason_code"])
    order_flow.set_order_status(ledger, order, "ALLOCATED", moment, "ALLOCATED")
    order_flow.set_hold_status(ledger, hold, "CONFIRMED", pay_at, "PAYMENT_RECEIVED")
    order_flow.set_order_status(ledger, order, "CONFIRMED", pay_at, "PAYMENT_RECEIVED")
    bucket = "SELLABLE" if is_decoy else "BLOCKED"
    sale_row = ship_injected_order(ledger, order, hold, bucket, ship_at)
    return {
        "issue": settings.ISSUE_BLOCKED,
        "is_decoy": is_decoy,
        "record_type": "movement",
        "movement_row": sale_row,
        "order": order,
        "expected_value": 0,
        "actual_value": 0 if is_decoy else 1,
    }


# ------------------------------------------------------------ transfer cases

def try_transfer(injector, sku_id, is_decoy):
    """
    The anomaly arrives before it leaves, so for those hours the stock counts at both ends.
    The decoy arrives one second after leaving, which looks odd but is valid.
    """
    ledger = injector.ledger
    moment = injector.find_slot(sku_id, 8)
    if moment is None:
        return None
    pairs = list(ledger.pairs_of_sku[sku_id])
    injector.rng.shuffle(pairs)
    for destination_index in pairs:
        destination = ledger.pairs[destination_index]
        source_index = ledger.index_of_pair.get((sku_id, destination["rule"]["source_location_id"]))
        if source_index is None or not master.is_pair_live(destination, moment.date()):
            continue
        spare = spare_sellable(injector.history, source_index, moment)
        if spare < 1:
            continue
        qty = injector.rng.randint(1, min(spare, destination["rule"]["reorder_qty"]))
        return write_transfer_case(ledger, source_index, destination_index, qty, moment, is_decoy, injector.rng)
    return None


def write_transfer_case(ledger, source_index, destination_index, qty, moment, is_decoy, rng):
    shipment_index = len(ledger.shipments)
    if is_decoy:
        left_at = moment
        arrived_at = moment + ONE_SECOND
    else:
        arrived_at = moment
        left_at = moment + timedelta(hours=rng.randint(1, 6))

    # The events look normal. Only the two ledger rows are in the wrong order.
    dispatched_at = moment if is_decoy else moment - ONE_SECOND
    ledger.shipments.append({
        "shipment_type": "TRANSFER",
        "source_index": source_index,
        "destination_index": destination_index,
        "qty": qty,
        "shipped_at": dispatched_at,
        "original_eta_at": moment + timedelta(days=2),
        "eta_at": moment + timedelta(days=2),
        "status": "RECEIVED",
        "last_event_at": arrived_at,
        "held": 0,
        "holds": [],
    })
    ledger.add_shipment_event(shipment_index, None, "CREATED", dispatched_at, "TRANSFER_PLANNED")
    ledger.add_shipment_event(shipment_index, "CREATED", "IN_TRANSIT", dispatched_at, "DISPATCHED")
    ledger.add_shipment_event(shipment_index, "IN_TRANSIT", "RECEIVED", arrived_at, "ARRIVED")
    ledger.sellable[source_index] -= qty
    ledger.sellable[destination_index] += qty
    ledger.add_row(source_index, "TRANSFER_OUT", "SELLABLE", -qty, left_at, "REPLENISHMENT", ledger.next_doc("TRO"),
                   shipment_index=shipment_index)
    ledger.add_row(destination_index, "TRANSFER_IN", "SELLABLE", qty, arrived_at, "REPLENISHMENT", ledger.next_doc("TRI"),
                   shipment_index=shipment_index)
    return {
        "issue": settings.ISSUE_TRANSFER,
        "is_decoy": is_decoy,
        "record_type": "shipment",
        "shipment_index": shipment_index,
        "order": None,
        "expected_value": 0,
        "actual_value": 0 if is_decoy else qty,
    }


# ------------------------------------------------------------ balance cases

def try_balance(injector, sku_id):
    """A stored closing balance changed by a small amount. No movement is touched."""
    ledger = injector.ledger
    if injector.quiet_from[sku_id] >= config.AS_OF:
        return None
    pairs = list(ledger.pairs_of_sku[sku_id])
    injector.rng.shuffle(pairs)
    for pair_index in pairs:
        on_hand = ledger.sellable[pair_index] + ledger.blocked[pair_index]
        difference = injector.rng.choice([-3, -2, -1, 1, 2, 3])
        # The change must stay a value the schema allows, or it wouldn't even load.
        if on_hand + difference >= ledger.blocked[pair_index]:
            return {
                "issue": settings.ISSUE_BALANCE,
                "is_decoy": False,
                "record_type": "balance",
                "pair_index": pair_index,
                "order": None,
                "expected_value": on_hand,
                "actual_value": on_hand + difference,
            }
    return None


def try_balance_decoy(injector, sku_id):
    """A pair with real movements that happen to net to exactly zero. Already valid."""
    ledger = injector.ledger
    for pair_index in ledger.pairs_of_sku[sku_id]:
        pair_history = injector.history[pair_index]
        moved = len(pair_history["times"]) > 1
        if moved and pair_history["sellable"][0] == pair_history["sellable"][-1] \
                and pair_history["blocked"][0] == pair_history["blocked"][-1]:
            on_hand = pair_history["sellable"][-1] + pair_history["blocked"][-1]
            return {
                "issue": settings.ISSUE_BALANCE,
                "is_decoy": True,
                "record_type": "balance",
                "pair_index": pair_index,
                "order": None,
                "expected_value": on_hand,
                "actual_value": on_hand,
            }
    return None


# ------------------------------------------------------------ putting it together

def inject(ledger, rng, scale):
    """Adds every case to the finished baseline and returns them. Rows are renumbered after."""
    injector = Injector(ledger, rng)
    anomalies = scale["anomalies_per_type"]
    decoys = scale["decoys_per_type"]

    # The hardest to place go first, so they get the first pick of the SKUs.
    for _ in range(decoys):
        injector.place_case("a balance decoy", lambda sku_id: try_balance_decoy(injector, sku_id))
    for is_decoy, count in ((False, anomalies), (True, decoys)):
        for _ in range(count):
            injector.place_case("a blocked stock case", lambda sku_id: try_blocked(injector, sku_id, is_decoy))
    for is_decoy, count in ((False, anomalies), (True, decoys)):
        for _ in range(count):
            injector.place_case("a reservation case", lambda sku_id: try_reservation(injector, sku_id, is_decoy))
    for is_decoy, count in ((False, anomalies), (True, decoys)):
        for _ in range(count):
            injector.place_case("a transfer case", lambda sku_id: try_transfer(injector, sku_id, is_decoy))
    for _ in range(anomalies):
        injector.place_case("a balance case", lambda sku_id: try_balance(injector, sku_id))
    return injector.cases


def renumber(ledger):
    """
    Numbers everything in time order, as if injected rows had always been there, so a high
    ID can't give one away. Returns where each old movement and shipment ended up.
    """
    order_map = {order["index"]: new for new, order in
                 enumerate(sorted(ledger.orders, key=lambda order: order["placed_at"]))}
    ledger.orders.sort(key=lambda order: order["placed_at"])
    for order in ledger.orders:
        order["index"] = order_map[order["index"]]
    for hold in ledger.holds:
        hold["order_index"] = order_map[hold["order_index"]]
    for decision in ledger.decisions:
        decision["order_index"] = order_map[decision["order_index"]]
    ledger.order_events = [(e[0], order_map[e[1]]) + e[2:] for e in ledger.order_events]
    ledger.oversell_attempts = {(order_map[order_index], line_no) for order_index, line_no in ledger.oversell_attempts}

    old_shipments = sorted(range(len(ledger.shipments)), key=lambda index: ledger.shipments[index]["shipped_at"])
    shipment_map = {old: new for new, old in enumerate(old_shipments)}
    ledger.shipments = [ledger.shipments[old] for old in old_shipments]
    for shipment in ledger.shipments:
        if shipment["shipment_type"] == "CUSTOMER":
            shipment["order_index"] = order_map[shipment["order_index"]]
    ledger.shipment_events = [(e[0], shipment_map[e[1]]) + e[2:] for e in ledger.shipment_events]

    allocation_map = {}
    for new, hold in enumerate(sorted((h for h in ledger.holds if h["allocation_no"] is not None),
                                      key=lambda hold: hold["created_at"]), start=1):
        allocation_map[hold["allocation_no"]] = new
    for hold in ledger.holds:
        if hold["allocation_no"] is not None:
            hold["allocation_no"] = allocation_map[hold["allocation_no"]]
        if hold["shipment_index"] is not None:
            hold["shipment_index"] = shipment_map[hold["shipment_index"]]
        if hold["customer_shipment"] is not None:
            hold["customer_shipment"] = shipment_map[hold["customer_shipment"]]

    old_rows = sorted(range(len(ledger.movements)), key=lambda position: ledger.movements[position][4])
    movement_map = {old: new for new, old in enumerate(old_rows)}
    doc_map = {}
    doc_counts = {}
    new_movements = []
    for old in old_rows:
        row = ledger.movements[old]
        pair_index, movement_type, bucket, quantity, moment, reason, doc, group, shipment_index, related_row, allocation_no = row
        if doc not in doc_map:
            # Both rows of a BLOCK share one document, so they stay together here too.
            prefix = doc.split("-")[0]
            doc_counts[prefix] = doc_counts.get(prefix, 0) + 1
            doc_map[doc] = f"{prefix}-2026-{doc_counts[prefix]:08d}"
        new_movements.append((
            pair_index, movement_type, bucket, quantity, moment, reason, doc_map[doc], group,
            None if shipment_index is None else shipment_map[shipment_index],
            None if related_row is None else movement_map[related_row],
            None if allocation_no is None else allocation_map[allocation_no],
        ))
    ledger.movements = new_movements
    ledger.decisions.sort(key=lambda decision: decision["decided_at"])
    return {"movements": movement_map, "shipments": shipment_map}


def tamper_balances(balance_rows, cases):
    """Done on the finished rows, because the closing balance is otherwise worked out correctly."""
    for case in cases:
        if case["issue"] == settings.ISSUE_BALANCE and not case["is_decoy"]:
            row = balance_rows[case["closing_row"]]
            row["on_hand"] = case["actual_value"]


def attach_closing_rows(ledger, balance_rows, cases):
    closing_row_of = {}
    for position, row in enumerate(balance_rows):
        if row["snapshot_type"] == "CLOSING":
            closing_row_of[(row["sku_id"], row["location_id"])] = position
    for case in cases:
        if case["issue"] == settings.ISSUE_BALANCE:
            pair = ledger.pairs[case["pair_index"]]
            case["closing_row"] = closing_row_of[(pair["sku_id"], pair["location_id"])]


def record_id_of(case, maps, balance_rows):
    if case["record_type"] == "balance":
        return balance_rows[case["closing_row"]]["balance_id"]
    if case["record_type"] == "reservation":
        return export.reservation_id_of(case["hold"])
    if case["record_type"] == "movement":
        return export.movement_id_of(maps["movements"][case["movement_row"]])
    return export.shipment_id_of(maps["shipments"][case["shipment_index"]])


def ground_truth_rows(ledger, cases, maps, balance_rows):
    """
    Every case, labelled by the rule that made it. A decoy's expected issue is NONE, so a
    detector that flags one counts as a false positive. Oversell attempts are here too,
    with the decision the rules should have reached.
    """
    rows = []
    for case in sorted(cases, key=lambda case: (case["issue"], case["is_decoy"], record_id_of(case, maps, balance_rows))):
        order = case["order"]
        rows.append({
            "expected_issue": "NONE" if case["is_decoy"] else case["issue"],
            "looks_like": case["issue"],
            "record_type": case["record_type"],
            "record_id": record_id_of(case, maps, balance_rows),
            "sku_id": case["sku_id"],
            "order_id": None if order is None else export.order_id_of(order["index"]),
            "expected_value": case["expected_value"],
            "actual_value": case["actual_value"],
            "expected_decision": "NO_ACTION" if case["is_decoy"] else RECOMMENDED_DECISIONS[case["issue"]],
        })
    for hold in sorted(ledger.holds, key=export.reservation_id_of):
        if (hold["order_index"], hold["line"]["line_no"]) in ledger.oversell_attempts:
            rows.append({
                "expected_issue": "NONE",
                "looks_like": "OVERSELL_ATTEMPT",
                "record_type": "reservation",
                "record_id": export.reservation_id_of(hold),
                "sku_id": hold["sku_id"],
                "order_id": export.order_id_of(hold["order_index"]),
                "expected_value": 0,
                "actual_value": hold["qty"],
                "expected_decision": "REJECT",
            })
    return rows
