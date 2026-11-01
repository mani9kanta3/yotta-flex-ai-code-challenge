"""
The size of a generated dataset.

Both scales run the same code. Only these numbers change.
"""

SCALES = {
    "starter": {
        "skus": 300,
        "locations": 12,
        "movements": 20_000,
        "orders": 1_000,
        "store_assortment_share": 0.25,
        "anomalies_per_type": 5,
        "decoys_per_type": 5,
        "checkpoints_per_kind": 5,
    },
    "full": {
        "skus": 5_000,
        "locations": 100,
        "movements": 1_000_000,
        "orders": 50_000,
        "store_assortment_share": 0.09,
        "anomalies_per_type": 50,
        "decoys_per_type": 50,
        "checkpoints_per_kind": 25,
    },
}

# The four problems from the brief.
ISSUE_BALANCE = "BALANCE_NOT_RECONCILED"
ISSUE_RESERVATION = "RESERVATION_EXCEEDS_AVAILABLE"
ISSUE_BLOCKED = "FULFILLED_FROM_BLOCKED"
ISSUE_TRANSFER = "TRANSFER_DOUBLE_COUNTED"
ISSUE_TYPES = [ISSUE_BALANCE, ISSUE_RESERVATION, ISSUE_BLOCKED, ISSUE_TRANSFER]

# Parts of the brief this generator doesn't produce yet. The manifest and the
# validator both print this, so a pass can't be read as covering them.
NOT_GENERATED_YET = ["store-channel orders"]

# Roughly how many variants a product has.
SKUS_PER_PRODUCT = 3.3

# About 1 location in 11 is a warehouse, which gives 9 at full scale.
WAREHOUSE_SHARE = 0.09

# Share of SKUs that launch or stop partway through the period.
LATE_LAUNCH_SHARE = 0.05
DISCONTINUED_SHARE = 0.03

# Share of stores that also ship online orders.
SHIP_FROM_STORE_SHARE = 0.30

# Share of pairs that start with a little damaged stock.
OPENING_BLOCKED_SHARE = 0.08

# Rules from docs/PLAN.md: lead time in days and smallest batch by location type.
LEAD_TIME_DAYS = {"STORE": 2, "WAREHOUSE": 3, "NDC": 7}
MIN_BATCH = {"STORE": 5, "WAREHOUSE": 20, "NDC": 50}
SAFETY_DAYS = 3
COVER_DAYS = 14

# How the day's movements are shared out when the ledger is built.
DAMAGE_SHARE = 0.015
ADJUSTMENT_SHARE = 0.02

# Damaged stock is inspected this many days later.
INSPECTION_DAYS = (5, 15)
UNBLOCK_CHANCE = 0.4

# Some trucks miss their ETA, and the new ETA is known an hour after the old one passes.
# Delays go past the 3-day limit in app/rules.py, so some waiting orders get cancelled.
TRANSFER_LATE_CHANCE = 0.08
TRANSFER_DELAY_DAYS = (1, 5)
ETA_MISSED_NOTICE_HOURS = 1

# What an online order looks like.
ORDER_LINE_COUNTS = [1, 2, 3]
ORDER_LINE_COUNT_WEIGHTS = [65, 25, 10]
ORDER_CHANNEL_WEIGHTS = {"WEB": 50, "APP": 30, "MARKETPLACE": 20}

# What a customer does with a hold. Whatever is left over never pays, so the hold expires.
PAY_CHANCE = 0.80
CUSTOMER_CANCEL_CHANCE = 0.05

# Some orders ask for a quantity that tests a rule. The allocation rule still decides.
OVERSELL_ATTEMPT_SHARE = 0.03
SPLIT_ATTEMPT_SHARE = 0.05
INBOUND_ATTEMPT_SHARE = 0.10
# How many SKUs or trucks to look at for one of those, and the largest quantity to ask for.
ATTEMPT_TRIES = 20
ATTEMPT_MAX_QTY = 40
# Left to chance, too few paid orders would meet a late truck to test what happens next.
# So a truck someone is waiting on runs late more often than one nobody is waiting on.
WAITED_TRUCK_LATE_CHANCE = 0.35
# When a paid order is left waiting on a late truck, the NDC may rush a delivery in for the
# next morning. Then the daily look for another location has something to find.
RUSH_DELIVERY_CHANCE = 0.5

# This share of SKUs stops getting online orders at a random date. A labelled case can only
# go after a SKU's last order, so without this every case would land at the very end of
# the data, and the date alone would give it away.
ORDERS_STOP_SHARE = 0.50

# Budget held back for each order not placed yet: its sales, returns and what they trigger.
ROWS_PER_ORDER = 2

DELIVERY_DAYS = (1, 4)
ONLINE_RETURN_CHANCE = 0.10
# Starts after the longest delivery, because a parcel can't come back before it arrives.
ONLINE_RETURN_DAYS = (5, 25)

# About 1 walk-in sale in 17 comes back, most of it in good condition.
RETURN_CHANCE = 0.06
RETURN_DAYS = (1, 20)
RETURN_DAMAGED_CHANCE = 0.15
# When only part of a sale comes back, sometimes the rest follows later.
SECOND_RETURN_CHANCE = 0.3

# Estimated walk-in units in the ledger per movement. Only used to size stock.
UNITS_PER_MOVEMENT = 1.0


def get_scale(name, skus=None, locations=None, movements=None, orders=None, anomalies=None, decoys=None):
    """A preset, with any overrides given on the command line."""
    scale = dict(SCALES[name])
    scale["name"] = name
    if skus is not None:
        scale["skus"] = skus
    if locations is not None:
        scale["locations"] = locations
    if movements is not None:
        scale["movements"] = movements
    if orders is not None:
        scale["orders"] = orders
    if anomalies is not None:
        scale["anomalies_per_type"] = anomalies
    if decoys is not None:
        scale["decoys_per_type"] = decoys
    return scale


def clean_scale(scale):
    """
    The baseline leaves exactly enough room for what gets injected, so the final
    totals land on the target.

        balance:     changes a stored value, no new rows
        reservation: 1 order, no movement, because nothing ships
        blocked:     1 order, 1 SALE
        transfer:    2 movements, no order

    A decoy adds the same rows as the anomaly it looks like, except the balance decoy,
    which is a row that already exists.
    """
    injected_per_kind = scale["anomalies_per_type"] + scale["decoys_per_type"]
    clean = dict(scale)
    clean["movements"] = scale["movements"] - injected_per_kind * (1 + 2)
    clean["orders"] = scale["orders"] - injected_per_kind * (1 + 1)
    return clean
