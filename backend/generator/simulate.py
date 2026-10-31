"""
Builds the ledger one day at a time, in time order, from a running stock count.

This first version covers receipts, walk-in sales, damage and adjustments. A
sale or a damage entry can only use stock that exists at that moment, so the
count can never go below zero.

Hitting the exact movement target works like a budget:

    rows_left    = target - rows already written
    pending_rows = rows we have promised and must post, like a due receipt

An optional event only runs if rows_left - pending_rows has room for it. If
stock runs out, a sale is refused and counted as lost. It is never forced.

Reorders here arrive straight from a supplier. Transfers replace that for
stores and warehouses in a later step.
"""

import bisect
from datetime import datetime, timedelta

from app import config, rules
from generator import settings

STORE_OPEN_SECONDS = 9 * 3600
STORE_CLOSE_SECONDS = 21 * 3600

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

    def __init__(self, rng, pairs, target_rows):
        self.rng = rng
        self.pairs = pairs
        self.target_rows = target_rows

        self.sellable = {}
        self.blocked = {}
        # Stays at zero until orders are added in a later step, but every sale
        # already goes through the check that respects it.
        self.reserved = {}
        self.on_order = {}
        for index, pair in enumerate(pairs):
            self.sellable[index] = pair["opening_on_hand"] - pair["opening_blocked"]
            self.blocked[index] = pair["opening_blocked"]
            self.reserved[index] = 0
            self.on_order[index] = False

        self.movements = []
        self.clock = config.START_AT
        self.pending_rows = 0
        self.scheduled = {}
        self.next_group = 1
        self.counters = {
            "walk_in_refused": 0,
            "reorders_skipped_no_budget": 0,
            "top_up_sales": 0,
            "top_up_receipts": 0,
        }
        self.doc_numbers = {}

    # ---------------------------------------------------------- the budget

    def rows_left(self):
        return self.target_rows - len(self.movements)

    def free_rows(self):
        return self.rows_left() - self.pending_rows

    # ---------------------------------------------------------- writing rows

    def tick(self, moment):
        """Time only moves forward, and each event gets its own second."""
        if moment <= self.clock:
            moment = self.clock + timedelta(seconds=1)
        self.clock = moment
        return moment

    def next_doc(self, prefix):
        self.doc_numbers[prefix] = self.doc_numbers.get(prefix, 0) + 1
        return f"{prefix}-2026-{self.doc_numbers[prefix]:08d}"

    def add_row(self, pair_index, movement_type, bucket, quantity, moment, reason, source_doc, group=None):
        self.movements.append((pair_index, movement_type, bucket, quantity, moment, reason, source_doc, group))

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

    # ---------------------------------------------------------- scheduling

    def schedule(self, day, action, pair_index, qty, rows):
        """Promise rows for a later day. Those rows are held out of the budget."""
        if day > config.AS_OF.date():
            return False
        self.scheduled.setdefault(day, []).append((action, pair_index, qty, rows))
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
        if not is_sku_live(pair, today):
            return
        if self.free_rows() < 1:
            self.counters["reorders_skipped_no_budget"] += 1
            return
        due = today + timedelta(days=rule["lead_time_days"])
        if self.schedule(due, "RECEIPT", pair_index, rule["reorder_qty"], 1):
            self.on_order[pair_index] = True

    def run_scheduled(self, day):
        """Everything due today. These rows are promised, so they always post."""
        for action, pair_index, qty, rows in self.scheduled.pop(day, []):
            moment = self.tick(day_moment(day, self.rng.randint(0, STORE_OPEN_SECONDS)))
            self.pending_rows -= rows
            if action == "RECEIPT":
                self.receive(pair_index, qty, moment)
                self.on_order[pair_index] = False
            elif action == "UNBLOCK":
                self.unblock(pair_index, qty, moment)
            elif action == "WRITE_OFF":
                self.write_off(pair_index, qty, moment)


def day_moment(day, seconds):
    return datetime(day.year, day.month, day.day) + timedelta(seconds=seconds)


def is_sku_live(pair, day):
    sku = pair["sku"]
    if day < sku["effective_from"]:
        return False
    return sku["effective_to"] is None or day < sku["effective_to"]


def is_pair_live(pair, day):
    return day >= pair["ranged_from"] and is_sku_live(pair, day)


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
    if not is_pair_live(pair, day) or day < pair["location"]["opened_on"]:
        ledger.counters["walk_in_refused"] += 1
        return False
    if ledger.free_rows() < 1:
        return False
    if not ledger.sell_walk_in(pair_index, pick_quantity(ledger.rng), ledger.tick(moment)):
        return False
    ledger.maybe_reorder(pair_index, day)
    return True


def do_damage(ledger, day, moment):
    """Damage can only hit stock that is on the shelf."""
    if ledger.free_rows() < 2:
        return
    pair_index = ledger.rng.randrange(len(ledger.pairs))
    if not is_pair_live(ledger.pairs[pair_index], day):
        return
    available = ledger.sellable[pair_index]
    if available < 1:
        return

    qty = ledger.rng.randint(1, min(3, available))
    ledger.block(pair_index, qty, ledger.tick(moment))

    # What happens at inspection is decided now, and its rows are promised now.
    low, high = settings.INSPECTION_DAYS
    due = day + timedelta(days=ledger.rng.randint(low, high))
    passes = ledger.rng.random() < settings.UNBLOCK_CHANCE
    action, rows = ("UNBLOCK", 2) if passes else ("WRITE_OFF", 1)
    if ledger.free_rows() >= 2 + rows:
        ledger.schedule(due, action, pair_index, qty, rows)


def do_adjustment(ledger, day, moment):
    if ledger.free_rows() < 1:
        return
    pair_index = ledger.rng.randrange(len(ledger.pairs))
    if not is_pair_live(ledger.pairs[pair_index], day):
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
        if not is_sku_live(pair, day) or ledger.free_rows() < 1:
            continue
        ledger.receive(index, pair["rule"]["reorder_qty"], ledger.tick(day_moment(day, 0)), "FIRST_STOCK")


def top_up(ledger, stores, store_ids, store_weights, day):
    """
    Close any small gap after the last day, using only valid events.

    First more walk-in sales through the same stock check. If those keep being
    refused, a warehouse delivery, which is real and needs no stock. If neither
    works, stop with an error instead of writing a bad row.
    """
    tries_left = ledger.rows_left() * TOP_UP_TRIES_PER_ROW
    moment = ledger.clock
    while ledger.rows_left() > 0 and tries_left > 0:
        before = len(ledger.movements)
        do_walk_in(ledger, stores, store_ids, store_weights, day, moment)
        if len(ledger.movements) > before:
            ledger.counters["top_up_sales"] += 1
        tries_left -= 1

    live = [i for i, pair in enumerate(ledger.pairs)
            if pair["location"]["location_type"] == "WAREHOUSE" and is_pair_live(pair, day)]
    while ledger.rows_left() > 0 and live:
        pair_index = ledger.rng.choice(live)
        ledger.receive(pair_index, ledger.pairs[pair_index]["rule"]["reorder_qty"], ledger.tick(moment))
        ledger.counters["top_up_receipts"] += 1

    if ledger.rows_left() != 0:
        raise RuntimeError(f"could not reach the target, {ledger.rows_left()} rows short")
    if ledger.clock > config.AS_OF:
        raise RuntimeError(f"top-up ran past AS_OF, clock is {ledger.clock}")


def simulate(rng, pairs, target_rows):
    ledger = Ledger(rng, pairs, target_rows)
    stores = build_store_index(pairs)
    store_ids = list(stores)
    store_weights = [stores[store_id]["weight"] for store_id in store_ids]

    first_day = config.START_AT.date()
    last_day = config.AS_OF.date()
    days = [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)]
    weights_left = sum(day_weight(day) for day in days)

    for day in days:
        ledger.run_scheduled(day)
        stock_new_arrivals(ledger, day)

        # Whatever fell short yesterday is still in free_rows, so it rolls forward.
        quota = round(ledger.free_rows() * day_weight(day) / weights_left)
        weights_left -= day_weight(day)

        damage_count = round(quota * settings.DAMAGE_SHARE / 2)
        adjustment_count = round(quota * settings.ADJUSTMENT_SHARE)
        walk_in_count = max(0, quota - damage_count * 2 - adjustment_count)

        # Mix the day's events, then run them in time order.
        events = (["WALK_IN"] * walk_in_count + ["DAMAGE"] * damage_count
                  + ["ADJUSTMENT"] * adjustment_count)
        seconds = sorted(rng.randint(STORE_OPEN_SECONDS, STORE_CLOSE_SECONDS) for _ in events)
        rng.shuffle(events)

        for kind, second in zip(events, seconds):
            moment = day_moment(day, second)
            if kind == "WALK_IN":
                do_walk_in(ledger, stores, store_ids, store_weights, day, moment)
            elif kind == "DAMAGE":
                do_damage(ledger, day, moment)
            else:
                do_adjustment(ledger, day, moment)

    top_up(ledger, stores, store_ids, store_weights, last_day)
    return ledger
