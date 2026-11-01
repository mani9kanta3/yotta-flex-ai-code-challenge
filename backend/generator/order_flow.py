"""
Generated online orders.

These follow the same rules as app/orders.py, reservations.py, shipping.py and
late_trucks.py, on the simulator's running counts instead of the database. Both call
app/rules.py for every decision, and the validator recalculates a sample of the
decisions in SQL, so the two versions can't drift apart unnoticed.

Randomness only decides what a customer asks for and does. Everything after that
follows from a rule. Ask for more than exists and the line is rejected. Never pay and
the hold expires at exactly expires_at.

Payment, expiry and a truck's late notice happen at exact times, so they wait in
ledger.timed and run when the clock passes them. They never write a movement.
"""

import json
from datetime import timedelta
from decimal import Decimal

from app import config, rules
from generator import master, settings


# ------------------------------------------------------------ recording state changes

def set_hold_status(ledger, hold, to_status, moment, reason, promised=None):
    if not rules.can_transition(rules.RESERVATION_TRANSITIONS, hold["status"], to_status):
        raise RuntimeError(f"illegal transition {hold['status']} to {to_status}")
    ledger.reservation_events.append((moment, hold["index"], hold["status"], to_status, reason, promised))
    hold["status"] = to_status


def set_order_status(ledger, order, to_status, moment, reason):
    if not rules.can_transition(rules.ORDER_TRANSITIONS, order["status"], to_status):
        raise RuntimeError(f"illegal transition {order['status']} to {to_status}")
    ledger.order_events.append((moment, order["index"], None, order["status"], to_status, reason))
    order["status"] = to_status


def set_line_status(ledger, order, line, to_status, moment, reason):
    if not rules.can_transition(rules.LINE_TRANSITIONS, line["status"], to_status):
        raise RuntimeError(f"illegal transition {line['status']} to {to_status}")
    ledger.order_events.append((moment, order["index"], line["line_no"], line["status"], to_status, reason))
    line["status"] = to_status


def open_holds(order):
    return [hold for hold in order["holds"] if hold["status"] in ("ACTIVE", "CONFIRMED")]


# ------------------------------------------------------------ what can be promised

def inbound_trucks(ledger, pair_index, moment):
    """The same test as INBOUND_SQL: on the road, and due between now and the horizon."""
    horizon = moment + timedelta(hours=rules.INBOUND_HORIZON_HOURS)
    trucks = []
    for shipment_index in ledger.trucks_to.get(pair_index, []):
        shipment = ledger.shipments[shipment_index]
        if moment <= shipment["eta_at"] <= horizon:
            trucks.append({
                "shipment_id": shipment_index,
                "qty": shipment["qty"],
                "eta_at": shipment["eta_at"],
                "free_qty": shipment["qty"] - shipment["held"],
            })
    return sorted(trucks, key=lambda truck: (truck["eta_at"], truck["shipment_id"]))


def get_positions(ledger, sku_id, channel_code, moment):
    """The same numbers as atp.get_positions, for each location that can sell this SKU."""
    channel = ledger.channels[channel_code]
    day = moment.date()
    positions = []
    for pair_index in ledger.pairs_of_sku[sku_id]:
        pair = ledger.pairs[pair_index]
        channel_location = ledger.channel_locations.get((channel_code, pair["location_id"]))
        if channel_location is None or day < channel_location["effective_from"]:
            continue
        if not master.is_pair_live(pair, day):
            continue

        rule = pair["rule"]
        trucks = inbound_trucks(ledger, pair_index, moment) if rule["allow_inbound_atp"] else []
        sellable = ledger.sellable[pair_index]
        blocked = ledger.blocked[pair_index]
        position = rules.position(rules.on_hand(sellable, blocked), blocked, ledger.reserved[pair_index],
                                  sum(truck["qty"] for truck in trucks), rule["safety_stock"],
                                  channel_location["buffer_units"], channel["counts_inbound"],
                                  channel["applies_safety_stock"])
        position["location_id"] = pair["location_id"]
        position["location_type"] = pair["location"]["location_type"]
        position["region"] = pair["location"]["region"]
        position["inbound_shipments"] = trucks if channel["counts_inbound"] else []
        positions.append(position)
    return positions


def two_best_on_hand(positions):
    best = sorted((position["from_on_hand"] for position in positions), reverse=True)[:2]
    return sum(amount for amount in best if amount > 0)


# ------------------------------------------------------------ what a customer asks for

def can_be_ordered(ledger, sku_id, moment, taken):
    """An order doesn't ask for one SKU twice, or for one that has stopped getting orders."""
    stops_on = ledger.orders_stop_on.get(sku_id)
    return sku_id not in taken and (stops_on is None or moment.date() < stops_on)


def pick_popular_sku(ledger, moment, taken):
    """More often when it is more popular."""
    while True:
        sku_id = ledger.pick_sku()
        if can_be_ordered(ledger, sku_id, moment, taken):
            return sku_id


def try_oversell(ledger, channel_code, moment, taken):
    """More than the whole network can promise, so the rule has to refuse it."""
    sku_id = pick_popular_sku(ledger, moment, taken)
    positions = get_positions(ledger, sku_id, channel_code, moment)
    network = sum(position["promisable"] for position in positions)
    ledger.oversell_attempts.add((len(ledger.orders) - 1, 1))
    return sku_id, network + ledger.rng.randint(1, 5)


def try_split(ledger, channel_code, moment, taken):
    """One more than the best location has, when a second location could cover the rest."""
    for _ in range(settings.ATTEMPT_TRIES):
        sku_id = ledger.rng.choice(ledger.sku_ids)
        if not can_be_ordered(ledger, sku_id, moment, taken):
            continue
        positions = get_positions(ledger, sku_id, channel_code, moment)
        best = max((position["from_on_hand"] for position in positions), default=0)
        wanted = best + 1
        if 1 <= best < settings.ATTEMPT_MAX_QTY and two_best_on_hand(positions) >= wanted:
            return sku_id, wanted
    return None


def try_inbound(ledger, channel_code, moment, taken):
    """More than two locations have on hand, when one truck due soon can cover it."""
    if not ledger.channels[channel_code]["counts_inbound"]:
        return None
    horizon = moment + timedelta(hours=rules.INBOUND_HORIZON_HOURS)
    due_soon = [index for index in ledger.trucks_on_road if moment <= ledger.shipments[index]["eta_at"] <= horizon]

    # The smallest quantity wins, so that stock arriving elsewhere can still rescue it.
    best_choice = None
    for index in ledger.rng.sample(due_soon, min(len(due_soon), settings.ATTEMPT_TRIES)):
        sku_id = ledger.pairs[ledger.shipments[index]["destination_index"]]["sku_id"]
        if not can_be_ordered(ledger, sku_id, moment, taken):
            continue
        positions = get_positions(ledger, sku_id, channel_code, moment)
        wanted = two_best_on_hand(positions) + 1
        if best_choice is not None and wanted >= best_choice[1]:
            continue
        for position in positions:
            enough_on_truck = any(truck["free_qty"] >= wanted for truck in position["inbound_shipments"])
            if enough_on_truck and position["promisable"] >= wanted:
                best_choice = (sku_id, wanted)
    return best_choice


def choose_first_line(ledger, channel_code, moment, taken):
    """Most lines are ordinary. Some deliberately ask for a quantity that tests a rule."""
    roll = ledger.rng.random()
    choice = None
    if roll < settings.OVERSELL_ATTEMPT_SHARE:
        choice = try_oversell(ledger, channel_code, moment, taken)
    elif roll < settings.OVERSELL_ATTEMPT_SHARE + settings.SPLIT_ATTEMPT_SHARE:
        choice = try_split(ledger, channel_code, moment, taken)
    elif roll < settings.OVERSELL_ATTEMPT_SHARE + settings.SPLIT_ATTEMPT_SHARE + settings.INBOUND_ATTEMPT_SHARE:
        choice = try_inbound(ledger, channel_code, moment, taken)
    return choice or ordinary_line(ledger, moment, taken)


def ordinary_line(ledger, moment, taken):
    return pick_popular_sku(ledger, moment, taken), ledger.pick_quantity()


# ------------------------------------------------------------ placing an order

def add_decision(ledger, order, line, qty, result, moment):
    chosen = result["parts"][0]["location_id"] if result["parts"] else None
    atp_seen = None
    promisable_seen = None
    for candidate in result["ranked"]:
        if candidate["location_id"] == chosen:
            atp_seen = candidate["atp"]
            promisable_seen = candidate["promisable"]
    ledger.decisions.append({
        "order_index": order["index"],
        "line_no": line["line_no"],
        "decided_at": moment,
        "requested_qty": qty,
        "decision_code": result["decision_code"],
        "reason_code": result["reason_code"],
        "chosen_location_id": chosen,
        "atp_seen": atp_seen,
        "promisable_seen": promisable_seen,
        "candidates": json.dumps(rules.candidates_summary(result["ranked"])),
    })
    ledger.counters["decisions_" + result["decision_code"].lower()] += 1


def new_hold(ledger, order, line, location_id, qty, supply_type, shipment_index, moment, expires_at, promised):
    order["next_hold_no"] += 1
    hold = {
        "index": len(ledger.holds),
        "order_index": order["index"],
        "hold_no": order["next_hold_no"],
        "line": line,
        "sku_id": line["sku_id"],
        "location_id": location_id,
        "qty": qty,
        "supply_type": supply_type,
        "shipment_index": shipment_index,
        "created_at": moment,
        "expires_at": expires_at,
        "first_promise": promised,
        "promised": promised,
        "status": None,
        "allocation_no": None,
        "customer_shipment": None,
    }
    ledger.holds.append(hold)
    order["holds"].append(hold)
    return hold


def add_active_hold(ledger, order, line, part, moment, expires_at):
    """The hold and its allocation row go together, and this is where stock gets held."""
    promised = rules.promised_ship_by(moment, part["eta_at"])
    hold = new_hold(ledger, order, line, part["location_id"], part["qty"], part["supply_type"],
                    part["inbound_shipment_id"], moment, expires_at, promised)
    hold["pair_index"] = ledger.index_of_pair[(line["sku_id"], part["location_id"])]
    ledger.next_allocation += 1
    hold["allocation_no"] = ledger.next_allocation
    set_hold_status(ledger, hold, "ACTIVE", moment, "ALLOCATED", promised)

    ledger.reserved[hold["pair_index"]] += hold["qty"]
    if hold["shipment_index"] is not None:
        ledger.shipments[hold["shipment_index"]]["held"] += hold["qty"]
        ledger.shipments[hold["shipment_index"]]["holds"].append(hold)
    return hold


def release_hold(ledger, hold):
    ledger.reserved[hold["pair_index"]] -= hold["qty"]
    if hold["shipment_index"] is not None:
        ledger.shipments[hold["shipment_index"]]["held"] -= hold["qty"]


def add_rejected_hold(ledger, order, line, qty, result, moment):
    """A refused request is still kept, because overselling attempts are worth measuring."""
    location_id = result["ranked"][0]["location_id"] if result["ranked"] else None
    hold = new_hold(ledger, order, line, location_id, qty, "ON_HAND", None, moment, moment, None)
    set_hold_status(ledger, hold, "REJECTED", moment, result["reason_code"])


def new_order(ledger, channel_code, region, moment):
    order = {
        "index": len(ledger.orders),
        "channel_code": channel_code,
        "customer_ref": f"CUST-{ledger.rng.randint(1, 999999):06d}",
        "region": region,
        "placed_at": moment,
        "status": None,
        "total": Decimal("0.00"),
        "lines": [],
        "holds": [],
        "next_hold_no": 0,
        "rows_promised": 0,
        "no_budget": False,
    }
    ledger.orders.append(order)
    set_order_status(ledger, order, "PLACED", moment, "ORDER_RECEIVED")
    return order


def new_line(ledger, order, line_no, sku_id, qty, moment):
    unit_price = Decimal(ledger.unit_price_of[sku_id])
    line = {"line_no": line_no, "sku_id": sku_id, "qty": qty, "unit_price": unit_price, "status": None}
    order["lines"].append(line)
    set_line_status(ledger, order, line, "PENDING", moment, "ORDER_RECEIVED")
    return line


def place_order(ledger, moment):
    rng = ledger.rng
    channel_code = rng.choices(list(settings.ORDER_CHANNEL_WEIGHTS), list(settings.ORDER_CHANNEL_WEIGHTS.values()))[0]
    channel = ledger.channels[channel_code]
    order = new_order(ledger, channel_code, rng.choice(ledger.regions), moment)
    expires_at = moment + timedelta(minutes=channel["hold_minutes"])

    line_count = rng.choices(settings.ORDER_LINE_COUNTS, settings.ORDER_LINE_COUNT_WEIGHTS)[0]
    taken = set()
    for line_no in range(1, line_count + 1):
        if line_no == 1:
            sku_id, qty = choose_first_line(ledger, channel_code, moment, taken)
        else:
            sku_id, qty = ordinary_line(ledger, moment, taken)
        taken.add(sku_id)
        line = new_line(ledger, order, line_no, sku_id, qty, moment)

        # Fresh numbers for every line, so a later line sees what an earlier one held.
        positions = get_positions(ledger, sku_id, channel_code, moment)
        result = rules.allocate(qty, positions, order["region"])
        add_decision(ledger, order, line, qty, result, moment)

        if result["parts"]:
            for part in result["parts"]:
                add_active_hold(ledger, order, line, part, moment, expires_at)
            order["total"] += qty * line["unit_price"]
            set_line_status(ledger, order, line, "ALLOCATED", moment, result["reason_code"])
        else:
            add_rejected_hold(ledger, order, line, qty, result, moment)
            set_line_status(ledger, order, line, "REJECTED", moment, result["reason_code"])

    if not open_holds(order):
        set_order_status(ledger, order, "REJECTED", moment, "NOTHING_AVAILABLE")
        return order
    set_order_status(ledger, order, "ALLOCATED", moment, "ALLOCATED")
    decide_what_the_customer_does(ledger, order, moment, expires_at)
    return order


def decide_what_the_customer_does(ledger, order, moment, expires_at):
    rng = ledger.rng
    roll = rng.random()
    minutes = rng.randint(1, ledger.channels[order["channel_code"]]["hold_minutes"] - 1)
    acts_at = moment + timedelta(minutes=minutes)
    if roll < settings.PAY_CHANCE:
        promise_shipping_rows(ledger, order)
        ledger.add_timed(acts_at, confirm_order, order)
    elif roll < settings.PAY_CHANCE + settings.CUSTOMER_CANCEL_CHANCE:
        ledger.add_timed(acts_at, customer_cancels, order)
    else:
        ledger.add_timed(expires_at, expire_order, order)


def promise_shipping_rows(ledger, order):
    """A paid order is going to write a SALE per hold, so those rows are held from now."""
    rows = len(open_holds(order))
    if ledger.rows_left() - ledger.pending_rows < rows:
        order["no_budget"] = True
        ledger.counters["orders_unshipped_no_budget"] += 1
        return
    ledger.pending_rows += rows
    order["rows_promised"] = rows


# ------------------------------------------------------------ actions at an exact time

def confirm_order(ledger, order, at):
    for hold in open_holds(order):
        set_hold_status(ledger, hold, "CONFIRMED", at, "PAYMENT_RECEIVED")
    set_order_status(ledger, order, "CONFIRMED", at, "PAYMENT_RECEIVED")
    # The truck may have run late between placing the order and paying for it.
    for hold in open_holds(order):
        if hold["shipment_index"] is not None and ledger.shipments[hold["shipment_index"]]["status"] == "IN_TRANSIT":
            handle_late_hold(ledger, hold, at)
    plan_shipment(ledger, order, at)


def end_unpaid_order(ledger, order, hold_status, at, reason):
    for hold in open_holds(order):
        release_hold(ledger, hold)
        set_hold_status(ledger, hold, hold_status, at, reason)
    for line in order["lines"]:
        if line["status"] == "ALLOCATED":
            set_line_status(ledger, order, line, "CANCELLED", at, reason)
    set_order_status(ledger, order, "CANCELLED", at, reason)


def customer_cancels(ledger, order, at):
    end_unpaid_order(ledger, order, "CANCELLED", at, "CUSTOMER_CANCELLED")


def expire_order(ledger, order, at):
    """Dated expires_at, because that is when the stock became free."""
    ledger.counters["orders_expired"] += 1
    end_unpaid_order(ledger, order, "EXPIRED", at, "HOLD_EXPIRED")


def truck_is_late(ledger, shipment_index, at):
    shipment = ledger.shipments[shipment_index]
    shipment["eta_at"] = shipment["next_eta_at"]
    shipment["last_event_at"] = at
    ledger.add_shipment_event(shipment_index, "IN_TRANSIT", "IN_TRANSIT", at, "ETA_MISSED", shipment["eta_at"])
    handle_holds_on_late_truck(ledger, shipment_index, at)


def handle_holds_on_late_truck(ledger, shipment_index, at):
    """Runs when the notice comes in, then once a day until the truck arrives."""
    shipment = ledger.shipments[shipment_index]
    if shipment["status"] != "IN_TRANSIT":
        return
    for hold in list(shipment["holds"]):
        if hold["status"] == "CONFIRMED":
            handle_late_hold(ledger, hold, at)
    ledger.add_timed(at + timedelta(days=1), handle_holds_on_late_truck, shipment_index)


# ------------------------------------------------------------ late trucks

def handle_late_hold(ledger, hold, at):
    """The same decision as app/late_trucks.py. Stock is read before the old hold is released."""
    shipment = ledger.shipments[hold["shipment_index"]]
    order = ledger.orders[hold["order_index"]]
    positions = get_positions(ledger, hold["sku_id"], order["channel_code"], at)
    target = rules.choose_reallocation(hold["qty"], positions, order["region"])
    new_promise = rules.late_promise(shipment["eta_at"], at)
    action = rules.late_truck_action(target is not None, hold["first_promise"], hold["promised"], new_promise)

    if action == "REALLOCATE":
        move_hold(ledger, order, hold, target, positions, at)
    elif action == "CANCEL":
        cancel_late_hold(ledger, order, hold, at)
    elif action == "REPROMISE":
        set_hold_status(ledger, hold, "CONFIRMED", at, "INBOUND_LATE_REPROMISED", new_promise)
        hold["promised"] = new_promise
        ledger.counters["late_repromised"] += 1
        if ledger.rng.random() < settings.RUSH_DELIVERY_CHANCE:
            ledger.rush_delivery(hold["sku_id"], at)


def move_hold(ledger, order, hold, target, positions, at):
    reason = "INBOUND_LATE_REALLOCATED"
    release_hold(ledger, hold)
    set_hold_status(ledger, hold, "CANCELLED", at, reason)

    part = rules.on_hand_part(target, hold["qty"])
    expires_at = at + timedelta(minutes=ledger.channels[order["channel_code"]]["hold_minutes"])
    moved = add_active_hold(ledger, order, hold["line"], part, at, expires_at)
    # The customer has already paid, so the new hold starts out confirmed.
    set_hold_status(ledger, moved, "CONFIRMED", at, reason)

    ranked = rules.rank_candidates(positions, order["region"])
    add_decision(ledger, order, hold["line"], hold["qty"], rules.decision("REALLOCATED", reason, [part], ranked), at)
    ledger.counters["late_reallocated"] += 1
    plan_shipment(ledger, order, at)


def cancel_late_hold(ledger, order, hold, at):
    reason = "INBOUND_LATE_CANCELLED"
    release_hold(ledger, hold)
    set_hold_status(ledger, hold, "CANCELLED", at, reason)
    set_line_status(ledger, order, hold["line"], "CANCELLED", at, reason)
    ledger.counters["late_cancelled"] += 1

    if order["rows_promised"] > 0:
        ledger.release_rows(1)
        order["rows_promised"] -= 1
    if open_holds(order):
        plan_shipment(ledger, order, at)
    else:
        set_order_status(ledger, order, "CANCELLED", at, reason)


# ------------------------------------------------------------ shipping

def order_is_ready(ledger, order):
    """Nothing the order holds is still on a truck."""
    for hold in open_holds(order):
        state = None
        if hold["shipment_index"] is not None:
            state = ledger.shipments[hold["shipment_index"]]["status"]
        if not rules.can_ship_hold(hold["supply_type"], state):
            return False
    return True


def plan_shipment(ledger, order, at):
    """A paid order with nothing on a truck goes out the next morning."""
    if order["status"] != "CONFIRMED" or order["rows_promised"] == 0 or not order_is_ready(ledger, order):
        return
    rows = order["rows_promised"]
    ledger.release_rows(rows)
    order["rows_promised"] = 0
    # An order paid on the last day is still waiting to go out when the data ends.
    ledger.schedule(at.date() + timedelta(days=1), "SHIP_ORDER", None, None, rows, order)


def ship_order(ledger, order, moment):
    """One parcel per location, all at one moment. This is the only place an order SALE is written."""
    parcels = {}
    for hold in open_holds(order):
        pair_index = hold["pair_index"]
        if ledger.sellable[pair_index] < hold["qty"]:
            raise RuntimeError(f"a promised hold has no stock behind it at {moment}")

        location_id = hold["location_id"]
        if location_id not in parcels:
            parcels[location_id] = ledger.add_customer_shipment(order, location_id, moment)
        hold["customer_shipment"] = parcels[location_id]

        ledger.sellable[pair_index] -= hold["qty"]
        ledger.reserved[pair_index] -= hold["qty"]
        ledger.add_row(pair_index, "SALE", "SELLABLE", -hold["qty"], moment, "ORDER_SHIPPED", ledger.next_doc("SALE"),
                       shipment_index=parcels[location_id], allocation_no=hold["allocation_no"])
        set_hold_status(ledger, hold, "CONSUMED", moment, "SHIPPED")
        ledger.plan_returns(len(ledger.movements) - 1, pair_index, hold["qty"], moment.date(),
                            settings.ONLINE_RETURN_CHANCE, settings.ONLINE_RETURN_DAYS)

    for line in order["lines"]:
        if line["status"] == "ALLOCATED":
            set_line_status(ledger, order, line, "SHIPPED", moment, "SHIPPED")
    set_order_status(ledger, order, "FULFILLED", moment, "SHIPPED")


def ship_waiting_orders(ledger, shipment_index, moment):
    """A truck just arrived. Paid orders that were waiting for it go out, oldest first."""
    shipped = []
    for hold in ledger.shipments[shipment_index]["holds"]:
        order = ledger.orders[hold["order_index"]]
        if hold["status"] != "CONFIRMED" or order["index"] in shipped:
            continue
        if order["rows_promised"] > 0 and order_is_ready(ledger, order):
            ledger.release_rows(order["rows_promised"])
            order["rows_promised"] = 0
            ship_order(ledger, order, moment)
            shipped.append(order["index"])


def do_order(ledger, moment):
    moment = ledger.tick(moment)
    order = place_order(ledger, moment)
    ledger.orders_left -= 1
    for hold in open_holds(order):
        ledger.maybe_reorder(hold["pair_index"], moment.date())
    return order
