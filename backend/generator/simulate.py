"""
Builds the ledger one day at a time, in time order, from a running stock count.

This version covers receipts, walk-in sales, damage, adjustments, transfers, returns
and online orders. A sale, a damage entry or a transfer can only use stock that
exists at that moment, so the count can never go below zero.

Hitting the exact movement target works like a budget:

    rows_left    = target - rows already written
    pending_rows = rows we have promised and must post, like a truck on the road
    held back    = rows for orders that haven't been placed yet

An optional event only runs if rows_left minus the other two has room for it. If
stock runs out, a sale is refused and counted as lost. It is never forced.

Stores reorder from their warehouse, warehouses from the NDC, and only the NDC
buys from a supplier. A truck that wouldn't arrive by the end of the period just
stays in transit, which is why some stock is on the road when the data ends.

Things due on a later day wait in the scheduled dict, by day. Things due at an exact
time, like a hold expiring, wait in the timed list, and tick() runs them as the clock
passes them. So every decision reads the stock as it was at that very moment.
"""

import bisect
from datetime import datetime, timedelta

from app import config, rules
from generator import master, order_flow, settings

STORE_OPEN_SECONDS = 9 * 3600
STORE_CLOSE_SECONDS = 21 * 3600

# Trucks are due by midday, which leaves room for the morning's deliveries to post.
TRUCK_DUE_SECONDS = 12 * 3600

# Rows nobody plans for the day, because they follow from something else happening.
TRIGGERED_TYPES = {"TRANSFER_OUT", "TRANSFER_IN", "RECEIPT", "RETURN", "UNBLOCK", "WRITE_OFF"}

# How many extra tries per missing row in the final top-up, before giving up.
TOP_UP_TRIES_PER_ROW = 50

QUANTITIES = [1, 2, 3, 4, 5, 6]
QUANTITY_WEIGHTS = [70, 20, 6, 2, 1, 1]


def day_weight(day):
    """Weekends are busier, and there is an end-of-season sale in early July."""
    weight = 1.4 if day.weekday() >= 5 else 1.0
    if day.month == 7 and day.day <= 15:
        weight *= 1.8
    return weight


class Ledger:
    """
    The running state of one simulation.

    A class only to avoid passing the same eight values to every function.
    """

    def __init__(self, rng, pairs, target_rows, channel_locations=(), order_count=0):
        self.rng = rng
        self.pairs = pairs
        self.target_rows = target_rows

        self.sellable = {}
        self.blocked = {}
        self.reserved = {}
        self.on_order = {}
        self.index_of_pair = {}
        self.pairs_of_sku = {}
        self.unit_price_of = {}
        self.sku_ids = []
        self.sku_cumulative = []
        popularity_total = 0.0
        for index, pair in enumerate(pairs):
            self.sellable[index] = pair["opening_on_hand"] - pair["opening_blocked"]
            self.blocked[index] = pair["opening_blocked"]
            self.reserved[index] = 0
            self.on_order[index] = False
            self.index_of_pair[(pair["sku_id"], pair["location_id"])] = index
            if pair["sku_id"] not in self.pairs_of_sku:
                self.pairs_of_sku[pair["sku_id"]] = []
                self.unit_price_of[pair["sku_id"]] = pair["sku"]["unit_price"]
                self.sku_ids.append(pair["sku_id"])
                popularity_total += pair["sku"]["popularity"]
                self.sku_cumulative.append(popularity_total)
            self.pairs_of_sku[pair["sku_id"]].append(index)
        self.regions = sorted({pair["location"]["region"] for pair in pairs})

        self.channels = {}
        for code, is_online, counts_inbound, applies_safety_stock, hold_minutes in master.CHANNELS:
            self.channels[code] = {"is_online": is_online, "counts_inbound": counts_inbound,
                                   "applies_safety_stock": applies_safety_stock, "hold_minutes": hold_minutes}
        self.channel_locations = {(row["channel_code"], row["location_id"]): row for row in channel_locations}

        self.movements = []
        self.triggered_rows = 0
        self.shipments = []
        self.shipment_events = []
        self.trucks_to = {}
        self.trucks_on_road = []

        self.orders_left = order_count
        self.orders = []
        self.holds = []
        self.decisions = []
        self.order_events = []
        self.reservation_events = []
        self.next_allocation = 0
        # (order position, line number) of each line that deliberately asked for too much.
        self.oversell_attempts = set()
        # The date some SKUs stop getting online orders, by SKU.
        self.orders_stop_on = {}

        # Things due at an exact time, kept in time order: (time, number, action, detail).
        self.timed = []
        self.timed_count = 0

        # Units returned so far, by the position of the sale in self.movements.
        self.returned = {}
        self.clock = config.START_AT
        self.pending_rows = 0
        self.scheduled = {}
        self.next_group = 1
        self.counters = {
            "walk_in_refused": 0,
            "reorders_skipped_no_budget": 0,
            "transfers_short_of_stock": 0,
            "transfers_late": 0,
            "returns_good": 0,
            "returns_damaged": 0,
            "returns_refused": 0,
            "decisions_single": 0,
            "decisions_split": 0,
            "decisions_inbound": 0,
            "decisions_rejected": 0,
            "decisions_reallocated": 0,
            "orders_expired": 0,
            "orders_unshipped_no_budget": 0,
            "late_reallocated": 0,
            "late_repromised": 0,
            "late_cancelled": 0,
            "top_up_sales": 0,
            "top_up_receipts": 0,
        }
        self.doc_numbers = {}

    # ---------------------------------------------------------- the budget

    def rows_left(self):
        return self.target_rows - len(self.movements)

    def free_rows(self):
        return self.rows_left() - self.pending_rows - self.orders_left * settings.ROWS_PER_ORDER

    def release_rows(self, rows):
        # The top-up clears pending_rows first, and a late cancel can still arrive after it.
        self.pending_rows = max(0, self.pending_rows - rows)

    def triggered_share(self):
        """So far, the share of rows that followed from a sale or a damage entry."""
        if not self.movements:
            return 0.0
        return self.triggered_rows / len(self.movements)

    # ---------------------------------------------------------- writing rows

    def tick(self, moment):
        """
        Time only moves forward, and each event gets its own second. Anything due by
        then happens first, so this event sees the stock as it really is at this moment.
        """
        if moment <= self.clock:
            moment = self.clock + timedelta(seconds=1)
        self.clock = moment
        self.run_timed(moment)
        return moment

    def add_timed(self, at, action, detail):
        # The number comes before the action, so two equal times never compare actions.
        self.timed_count += 1
        bisect.insort(self.timed, (at, self.timed_count, action, detail))

    def run_timed(self, moment):
        while self.timed and self.timed[0][0] <= moment:
            at, _number, action, detail = self.timed.pop(0)
            action(self, detail, at)

    def next_doc(self, prefix):
        self.doc_numbers[prefix] = self.doc_numbers.get(prefix, 0) + 1
        return f"{prefix}-2026-{self.doc_numbers[prefix]:08d}"

    def add_row(self, pair_index, movement_type, bucket, quantity, moment, reason, source_doc, group=None,
                shipment_index=None, related_row=None, allocation_no=None):
        self.movements.append((pair_index, movement_type, bucket, quantity, moment, reason, source_doc, group,
                               shipment_index, related_row, allocation_no))
        if movement_type in TRIGGERED_TYPES:
            self.triggered_rows += 1

    def pick_sku(self):
        """More often when it is more popular."""
        point = self.rng.random() * self.sku_cumulative[-1]
        return self.sku_ids[bisect.bisect_left(self.sku_cumulative, point)]

    def pick_quantity(self):
        return pick_quantity(self.rng)

    def receive(self, pair_index, qty, moment, reason="SUPPLIER_DELIVERY"):
        self.sellable[pair_index] += qty
        self.add_row(pair_index, "RECEIPT", "SELLABLE", qty, moment, reason, self.next_doc("RCV"))

    def sell_walk_in(self, pair_index, qty, moment):
        """The only way a walk-in sale is written. Returns False if refused."""
        if not rules.can_sell_walk_in(qty, self.sellable[pair_index], self.reserved[pair_index]):
            self.counters["walk_in_refused"] += 1
            return False
        self.sellable[pair_index] -= qty
        self.add_row(pair_index, "SALE", "SELLABLE", -qty, moment, "WALK_IN", self.next_doc("POS"))
        return True

    def block(self, pair_index, qty, moment):
        """
        Move stock from sellable to blocked. Two rows that add up to zero, so
        total stock doesn't change. This is the only place a BLOCK is written.
        """
        self.sellable[pair_index] -= qty
        self.blocked[pair_index] += qty
        group = f"GRP-{self.next_group:08d}"
        self.next_group += 1
        doc = self.next_doc("DMG")
        self.add_row(pair_index, "BLOCK", "SELLABLE", -qty, moment, "DAMAGE", doc, group)
        self.add_row(pair_index, "BLOCK", "BLOCKED", qty, moment, "DAMAGE", doc, group)

    def unblock(self, pair_index, qty, moment):
        self.blocked[pair_index] -= qty
        self.sellable[pair_index] += qty
        group = f"GRP-{self.next_group:08d}"
        self.next_group += 1
        doc = self.next_doc("INS")
        self.add_row(pair_index, "UNBLOCK", "BLOCKED", -qty, moment, "PASSED_INSPECTION", doc, group)
        self.add_row(pair_index, "UNBLOCK", "SELLABLE", qty, moment, "PASSED_INSPECTION", doc, group)

    def write_off(self, pair_index, qty, moment):
        self.blocked[pair_index] -= qty
        self.add_row(pair_index, "WRITE_OFF", "BLOCKED", -qty, moment, "FAILED_INSPECTION", self.next_doc("INS"))

    def adjust(self, pair_index, quantity, moment):
        self.sellable[pair_index] += quantity
        self.add_row(pair_index, "ADJUSTMENT", "SELLABLE", quantity, moment, "CYCLE_COUNT", self.next_doc("CC"))

    # ---------------------------------------------------------- transfers

    def add_shipment_event(self, shipment_index, from_status, to_status, moment, reason, eta_at=None):
        self.shipment_events.append((moment, shipment_index, from_status, to_status, reason, eta_at))

    def ship_transfer(self, source_index, destination_index, qty, moment, eta_at):
        """
        Stock leaves the source now, and reaches no shelf until arrive() runs. This is
        the only place a TRANSFER_OUT is written. Returns None if the stock isn't free.
        """
        if not rules.can_ship_transfer(qty, self.sellable[source_index], self.reserved[source_index]):
            return None
        self.sellable[source_index] -= qty
        shipment_index = len(self.shipments)
        self.shipments.append({
            "shipment_type": "TRANSFER",
            "source_index": source_index,
            "destination_index": destination_index,
            "qty": qty,
            "shipped_at": moment,
            "original_eta_at": eta_at,
            "eta_at": eta_at,
            "status": "IN_TRANSIT",
            "last_event_at": moment,
            # What open holds have on this truck, and every hold ever placed on it.
            "held": 0,
            "holds": [],
        })
        self.trucks_to.setdefault(destination_index, []).append(shipment_index)
        self.trucks_on_road.append(shipment_index)
        self.add_shipment_event(shipment_index, None, "CREATED", moment, "TRANSFER_PLANNED")
        self.add_shipment_event(shipment_index, "CREATED", "IN_TRANSIT", moment, "DISPATCHED")
        self.add_row(source_index, "TRANSFER_OUT", "SELLABLE", -qty, moment, "REPLENISHMENT",
                     self.next_doc("TRO"), shipment_index=shipment_index)
        return shipment_index

    def miss_eta(self, shipment_index, new_eta_at):
        """
        Decided when the truck leaves, but nobody knows until the notice comes in. The
        first ETA stays on the shipment. The new one is only in the events.
        """
        shipment = self.shipments[shipment_index]
        self.counters["transfers_late"] += 1
        shipment["next_eta_at"] = new_eta_at
        notice_at = shipment["original_eta_at"] + timedelta(hours=settings.ETA_MISSED_NOTICE_HOURS)
        self.add_timed(notice_at, order_flow.truck_is_late, shipment_index)

    def hold_up_truck(self, shipment_index, pair_index, qty, day):
        """A truck someone is waiting on may not turn up today. Returns True if it doesn't."""
        shipment = self.shipments[shipment_index]
        if "next_eta_at" in shipment:
            return False
        if not any(hold["status"] == "CONFIRMED" for hold in shipment["holds"]):
            return False
        if self.rng.random() >= settings.WAITED_TRUCK_LATE_CHANCE:
            return False
        new_day = day + timedelta(days=self.rng.randint(*settings.TRANSFER_DELAY_DAYS))
        self.miss_eta(shipment_index, day_moment(new_day, TRUCK_DUE_SECONDS))
        self.schedule(new_day, "TRANSFER_IN", pair_index, qty, 1, shipment_index)
        return True

    def add_customer_shipment(self, order, location_id, moment):
        rng = self.rng
        shipment_index = len(self.shipments)
        shipment = {
            "shipment_type": "CUSTOMER",
            "order_index": order["index"],
            "location_id": location_id,
            "shipped_at": moment,
            "status": "IN_TRANSIT",
        }
        self.shipments.append(shipment)
        self.add_shipment_event(shipment_index, None, "CREATED", moment, "PACKED")
        self.add_shipment_event(shipment_index, "CREATED", "IN_TRANSIT", moment, "DISPATCHED")
        delivered_at = moment + timedelta(days=rng.randint(*settings.DELIVERY_DAYS))
        # Nothing can be known about a parcel after the data ends.
        if delivered_at <= config.AS_OF:
            self.add_shipment_event(shipment_index, "IN_TRANSIT", "DELIVERED", delivered_at, "DELIVERED")
            shipment["status"] = "DELIVERED"
        return shipment_index

    def arrive(self, shipment_index, moment):
        """
        The stock reaches the destination. The row and the event share one moment, so
        there is no time when a truck is both inbound and on the shelf.
        """
        shipment = self.shipments[shipment_index]
        if not rules.can_receive(shipment["status"], shipment["last_event_at"], moment):
            raise RuntimeError(f"can't receive shipment {shipment_index} at {moment}")
        destination_index = shipment["destination_index"]
        self.sellable[destination_index] += shipment["qty"]
        self.add_row(destination_index, "TRANSFER_IN", "SELLABLE", shipment["qty"], moment, "REPLENISHMENT",
                     self.next_doc("TRI"), shipment_index=shipment_index)
        self.add_shipment_event(shipment_index, "IN_TRANSIT", "RECEIVED", moment, "ARRIVED")
        shipment["status"] = "RECEIVED"
        shipment["last_event_at"] = moment
        self.trucks_to[destination_index].remove(shipment_index)
        self.trucks_on_road.remove(shipment_index)
        order_flow.ship_waiting_orders(self, shipment_index, moment)

    # ---------------------------------------------------------- returns

    def plan_returns(self, sale_row, pair_index, sold, day, chance, days):
        """Whether this sale comes back is decided now, and its rows are promised now."""
        if self.rng.random() >= chance:
            return
        first_qty = self.rng.randint(1, sold)
        self.plan_one_return(sale_row, pair_index, first_qty, day, days)
        if first_qty < sold and self.rng.random() < settings.SECOND_RETURN_CHANCE:
            self.plan_one_return(sale_row, pair_index, sold - first_qty, day, days)

    def plan_one_return(self, sale_row, pair_index, qty, day, days):
        due = day + timedelta(days=self.rng.randint(*days))
        condition = "DAMAGED" if self.rng.random() < settings.RETURN_DAMAGED_CHANCE else "GOOD"
        if self.free_rows() >= 1:
            self.schedule(due, "RETURN", pair_index, qty, 1, (sale_row, condition))

    def return_goods(self, sale_row, qty, condition, moment):
        """
        The only way a return is written. It goes back to the location of the sale,
        and all returns of one sale can't add up to more than was sold.
        """
        sale = self.movements[sale_row]
        if sale[1] != "SALE":
            self.counters["returns_refused"] += 1
            return False
        sold = -sale[3]
        already_returned = self.returned.get(sale_row, 0)
        if not rules.can_return(qty, sold, already_returned):
            self.counters["returns_refused"] += 1
            return False

        pair_index = sale[0]
        bucket = rules.RETURN_BUCKETS[condition]
        if bucket == "SELLABLE":
            self.sellable[pair_index] += qty
            self.counters["returns_good"] += 1
        else:
            self.blocked[pair_index] += qty
            self.counters["returns_damaged"] += 1
        self.returned[sale_row] = already_returned + qty
        self.add_row(pair_index, "RETURN", bucket, qty, moment, f"RETURN_{condition}", self.next_doc("RET"),
                     related_row=sale_row)
        return True

    # ---------------------------------------------------------- scheduling

    def schedule(self, day, action, pair_index, qty, rows, detail=None):
        """Promise rows for a later day. Those rows are held out of the budget."""
        if day > config.AS_OF.date():
            return False
        self.scheduled.setdefault(day, []).append((action, pair_index, qty, rows, detail))
        self.pending_rows += rows
        return True

    def maybe_reorder(self, pair_index, today):
        """Reorder once when stock reaches the reorder point, if the budget allows."""
        pair = self.pairs[pair_index]
        rule = pair["rule"]
        if self.on_order[pair_index]:
            return
        if self.sellable[pair_index] - self.reserved[pair_index] > rule["reorder_point"]:
            return
        # A discontinued SKU is not bought again.
        if not master.is_sku_live(pair, today):
            return
        if rule["source_location_id"] is None:
            self.order_from_supplier(pair_index, today)
        else:
            self.order_from_source(pair_index, today)

    def order_from_supplier(self, pair_index, today):
        rule = self.pairs[pair_index]["rule"]
        if self.free_rows() < 1:
            self.counters["reorders_skipped_no_budget"] += 1
            return
        due = today + timedelta(days=rule["lead_time_days"])
        if self.schedule(due, "RECEIPT", pair_index, rule["reorder_qty"], 1):
            self.on_order[pair_index] = True

    def rush_delivery(self, sku_id, at):
        """The next morning, at whichever location buys from a supplier, if the budget allows."""
        for pair_index in self.pairs_of_sku[sku_id]:
            pair = self.pairs[pair_index]
            if pair["rule"]["source_location_id"] is None and self.free_rows() >= 1:
                next_day = at.date() + timedelta(days=1)
                self.schedule(next_day, "RUSH_RECEIPT", pair_index, pair["rule"]["reorder_qty"], 1)

    def order_from_source(self, pair_index, today):
        """A store asks its warehouse and a warehouse asks the NDC. The truck leaves now."""
        pair = self.pairs[pair_index]
        rule = pair["rule"]
        source_index = self.index_of_pair.get((pair["sku_id"], rule["source_location_id"]))
        if source_index is None:
            return

        due = today + timedelta(days=rule["lead_time_days"])
        late = self.rng.random() < settings.TRANSFER_LATE_CHANCE
        arrival_day = due
        if late:
            arrival_day = due + timedelta(days=self.rng.randint(*settings.TRANSFER_DELAY_DAYS))
        elif self.rng.random() < settings.TRANSFER_EARLY_CHANCE:
            arrival_day = due - timedelta(days=1)

        # A truck that arrives after the data ends only ever writes its first row.
        rows_needed = 2 if arrival_day <= config.AS_OF.date() else 1
        if self.free_rows() < rows_needed:
            self.counters["reorders_skipped_no_budget"] += 1
            return

        source_rule = self.pairs[source_index]["rule"]
        qty = rules.transfer_quantity(rule["reorder_qty"], self.sellable[source_index],
                                      self.reserved[source_index], source_rule["safety_stock"])
        if qty == 0:
            self.counters["transfers_short_of_stock"] += 1
        else:
            eta_at = day_moment(due, TRUCK_DUE_SECONDS)
            shipment_index = self.ship_transfer(source_index, pair_index, qty, self.tick(self.clock), eta_at)
            if late:
                self.miss_eta(shipment_index, day_moment(arrival_day, TRUCK_DUE_SECONDS))
            self.schedule(arrival_day, "TRANSFER_IN", pair_index, qty, 1, shipment_index)
            self.on_order[pair_index] = True

        # Either way the source may now need stock of its own.
        self.maybe_reorder(source_index, today)

    def plan_inspection(self, pair_index, qty, day):
        """What happens at inspection is decided now, and its rows are promised now."""
        low, high = settings.INSPECTION_DAYS
        due = day + timedelta(days=self.rng.randint(low, high))
        passes = self.rng.random() < settings.UNBLOCK_CHANCE
        action, rows = ("UNBLOCK", 2) if passes else ("WRITE_OFF", 1)
        if self.free_rows() >= rows:
            self.schedule(due, action, pair_index, qty, rows)

    def run_scheduled(self, day):
        """Everything due today. These rows are promised, so they always post."""
        items = self.scheduled.pop(day, [])
        # Sorted first, because the clock only moves forward. Otherwise one late time
        # would push every row after it to the same hour. Shuffled, or a truck sent late
        # yesterday would always come in late today, and every trip would take the same time.
        self.rng.shuffle(items)
        times = sorted(self.rng.randint(0, STORE_OPEN_SECONDS) for _ in items)
        for (action, pair_index, qty, rows, detail), seconds in zip(items, times):
            moment = self.tick(day_moment(day, seconds))
            self.pending_rows -= rows
            if action == "RECEIPT":
                self.receive(pair_index, qty, moment)
                self.on_order[pair_index] = False
            elif action == "RUSH_RECEIPT":
                self.receive(pair_index, qty, moment)
            elif action == "TRANSFER_IN":
                if self.hold_up_truck(detail, pair_index, qty, day):
                    continue
                self.arrive(detail, moment)
                self.on_order[pair_index] = False
            elif action == "RETURN":
                sale_row, condition = detail
                if self.return_goods(sale_row, qty, condition, moment) and condition == "DAMAGED":
                    self.plan_inspection(pair_index, qty, day)
            elif action == "UNBLOCK":
                self.unblock(pair_index, qty, moment)
            elif action == "WRITE_OFF":
                self.write_off(pair_index, qty, moment)
            elif action == "SHIP_ORDER":
                order_flow.ship_order(self, detail, moment)


def day_moment(day, seconds):
    return datetime(day.year, day.month, day.day) + timedelta(seconds=seconds)


def build_store_index(pairs):
    """For each store: its pairs, plus running popularity totals for picking one."""
    stores = {}
    for index, pair in enumerate(pairs):
        if pair["location"]["location_type"] != "STORE":
            continue
        location_id = pair["location_id"]
        if location_id not in stores:
            stores[location_id] = {
                "weight": pair["location"]["size_weight"],
                "pair_indexes": [],
                "cumulative": [],
                "total": 0.0,
            }
        store = stores[location_id]
        store["total"] += pair["sku"]["popularity"]
        store["pair_indexes"].append(index)
        store["cumulative"].append(store["total"])
    return stores


def pick_pair(rng, store):
    """Pick a SKU in this store, more often when it is more popular."""
    point = rng.random() * store["total"]
    return store["pair_indexes"][bisect.bisect_left(store["cumulative"], point)]


def pick_quantity(rng):
    return rng.choices(QUANTITIES, QUANTITY_WEIGHTS)[0]


def do_walk_in(ledger, stores, store_ids, store_weights, day, moment):
    store = stores[ledger.rng.choices(store_ids, store_weights)[0]]
    pair_index = pick_pair(ledger.rng, store)
    pair = ledger.pairs[pair_index]
    if not master.is_pair_live(pair, day) or day < pair["location"]["opened_on"]:
        ledger.counters["walk_in_refused"] += 1
        return False
    if ledger.free_rows() < 1:
        return False
    qty = pick_quantity(ledger.rng)
    if not ledger.sell_walk_in(pair_index, qty, ledger.tick(moment)):
        # The source may have been empty last time, so a refused sale asks again.
        ledger.maybe_reorder(pair_index, day)
        return False
    ledger.plan_returns(len(ledger.movements) - 1, pair_index, qty, day, settings.RETURN_CHANCE, settings.RETURN_DAYS)
    ledger.maybe_reorder(pair_index, day)
    return True


def do_damage(ledger, day, moment):
    """
    Damage only hits stock on the shelf that isn't promised to anyone. Damage to held
    stock would be a short pick, which the generator doesn't produce.
    """
    if ledger.free_rows() < 2:
        return
    pair_index = ledger.rng.randrange(len(ledger.pairs))
    if not master.is_pair_live(ledger.pairs[pair_index], day):
        return
    available = rules.walk_in_available(ledger.sellable[pair_index], ledger.reserved[pair_index])
    if available < 1:
        return

    qty = ledger.rng.randint(1, min(3, available))
    ledger.block(pair_index, qty, ledger.tick(moment))
    ledger.plan_inspection(pair_index, qty, day)


def do_adjustment(ledger, day, moment):
    if ledger.free_rows() < 1:
        return
    pair_index = ledger.rng.randrange(len(ledger.pairs))
    if not master.is_pair_live(ledger.pairs[pair_index], day):
        return
    quantity = ledger.rng.choice([-2, -1, -1, 1, 1, 2])
    # A count can't go below what is promised to online orders either.
    free = rules.walk_in_available(ledger.sellable[pair_index], ledger.reserved[pair_index])
    if quantity < 0 and free + quantity < 0:
        return
    ledger.adjust(pair_index, quantity, ledger.tick(moment))


def stock_new_arrivals(ledger, day):
    """A SKU or store that goes live today gets its first delivery today."""
    for index, pair in enumerate(ledger.pairs):
        if pair["ranged_from"] != day or day == config.START_AT.date():
            continue
        if not master.is_sku_live(pair, day) or ledger.free_rows() < 1:
            continue
        ledger.receive(index, pair["rule"]["reorder_qty"], ledger.tick(day_moment(day, 0)), "FIRST_STOCK")


def top_up(ledger, stores, store_ids, store_weights, day):
    """
    Close any small gap after the last day, using only valid events.

    First more walk-in sales through the same stock check. If those keep being
    refused, a warehouse delivery, which is real and needs no stock. If neither
    works, stop with an error instead of writing a bad row.
    """
    # Every delivery day has passed, so rows still promised to waiting orders can't post.
    ledger.pending_rows = 0
    tries_left = ledger.rows_left() * TOP_UP_TRIES_PER_ROW
    moment = ledger.clock
    while ledger.rows_left() > 0 and tries_left > 0:
        before = len(ledger.movements)
        do_walk_in(ledger, stores, store_ids, store_weights, day, moment)
        if len(ledger.movements) > before:
            ledger.counters["top_up_sales"] += 1
        tries_left -= 1

    live = [i for i, pair in enumerate(ledger.pairs)
            if pair["location"]["location_type"] == "WAREHOUSE" and master.is_pair_live(pair, day)]
    while ledger.rows_left() > 0 and live:
        pair_index = ledger.rng.choice(live)
        ledger.receive(pair_index, ledger.pairs[pair_index]["rule"]["reorder_qty"], ledger.tick(moment))
        ledger.counters["top_up_receipts"] += 1

    if ledger.rows_left() != 0:
        raise RuntimeError(f"could not reach the target, {ledger.rows_left()} rows short")
    if ledger.clock > config.AS_OF:
        raise RuntimeError(f"top-up ran past AS_OF, clock is {ledger.clock}")


def simulate(rng, pairs, target_rows, channel_locations=(), order_count=0):
    ledger = Ledger(rng, pairs, target_rows, channel_locations, order_count)
    stores = build_store_index(pairs)
    store_ids = list(stores)
    store_weights = [stores[store_id]["weight"] for store_id in store_ids]

    first_day = config.START_AT.date()
    last_day = config.AS_OF.date()
    days = [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)]
    weights_left = sum(day_weight(day) for day in days)
    damage_owed = 0.0
    adjustments_owed = 0.0

    for sku_id in rng.sample(ledger.sku_ids, round(len(ledger.sku_ids) * settings.ORDERS_STOP_SHARE)):
        ledger.orders_stop_on[sku_id] = rng.choice(days)

    for day in days:
        ledger.run_scheduled(day)
        stock_new_arrivals(ledger, day)

        # Whatever fell short yesterday is still in free_rows, so it rolls forward.
        quota = round(ledger.free_rows() * day_weight(day) / weights_left)
        # Each day takes its share of what is left, so the last day's share is all of it.
        order_count_today = round(ledger.orders_left * day_weight(day) / weights_left)
        weights_left -= day_weight(day)

        # Fractions roll forward, or a small dataset would round these to zero every day.
        damage_owed += quota * settings.DAMAGE_SHARE / 2
        adjustments_owed += quota * settings.ADJUSTMENT_SHARE
        damage_count = int(damage_owed)
        adjustment_count = int(adjustments_owed)
        damage_owed -= damage_count
        adjustments_owed -= adjustment_count

        # Sales also trigger trucks and returns, and those rows come out of the same
        # budget. Without this the early days would use up what later days need.
        triggered = round(quota * ledger.triggered_share())
        walk_in_count = max(0, quota - damage_count * 2 - adjustment_count - triggered)

        # Mix the day's events, then run them in time order.
        events = (["WALK_IN"] * walk_in_count + ["DAMAGE"] * damage_count
                  + ["ADJUSTMENT"] * adjustment_count + ["ORDER"] * order_count_today)
        seconds = sorted(rng.randint(STORE_OPEN_SECONDS, STORE_CLOSE_SECONDS) for _ in events)
        rng.shuffle(events)

        for kind, second in zip(events, seconds):
            moment = day_moment(day, second)
            if kind == "WALK_IN":
                do_walk_in(ledger, stores, store_ids, store_weights, day, moment)
            elif kind == "DAMAGE":
                do_damage(ledger, day, moment)
            elif kind == "ORDER":
                order_flow.do_order(ledger, moment)
            else:
                do_adjustment(ledger, day, moment)

    top_up(ledger, stores, store_ids, store_weights, last_day)
    # Anything due in the last hours of the period, after the final movement.
    ledger.run_timed(config.AS_OF)

    on_the_road = [shipment for shipment in ledger.shipments
                   if shipment["shipment_type"] == "TRANSFER" and shipment["status"] == "IN_TRANSIT"]
    ledger.counters["transfers_in_transit_at_end"] = len(on_the_road)
    ledger.counters["units_in_transit_at_end"] = sum(shipment["qty"] for shipment in on_the_road)
    ledger.counters["oversell_attempts"] = len(ledger.oversell_attempts)
    return ledger
