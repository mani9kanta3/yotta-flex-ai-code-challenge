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
    },
    "full": {
        "skus": 5_000,
        "locations": 100,
        "movements": 1_000_000,
        "orders": 50_000,
        "store_assortment_share": 0.09,
    },
}

# Parts of the brief this generator doesn't produce yet. The manifest and the
# validator both print this, so a pass can't be read as covering them.
NOT_GENERATED_YET = [
    "orders", "order lines", "reservations", "allocations", "shipments", "transfers",
    "returns", "labelled anomalies", "ground truth",
]

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
OPENING_BLOCKED_SHARE = 0.01

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

# Estimated walk-in units in the ledger per movement. Only used to size stock.
UNITS_PER_MOVEMENT = 1.0


def get_scale(name, skus=None, locations=None, movements=None, orders=None):
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
    return scale
