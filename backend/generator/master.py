"""
Master data: products, SKUs, locations, channels, what is stocked where, the
replenishment rules and the opening balances.

These are created first because everything else points at them. Safety stock
and reorder points are calculated from the forecast, never picked at random.
"""

import math
from datetime import date, timedelta

from app import config
from generator import settings

CATEGORIES = {
    "APPAREL": ["Cotton Shirt", "Linen Kurta", "Denim Jacket", "Polo Tee", "Chino Trousers"],
    "FOOTWEAR": ["Running Shoe", "Canvas Sneaker", "Leather Sandal", "Trail Boot"],
    "HOME": ["Steel Bottle", "Cotton Towel", "Ceramic Mug", "Table Lamp", "Storage Box"],
    "ELECTRONICS": ["Phone Charger", "USB Cable", "Wired Earphones", "Power Bank"],
    "BEAUTY": ["Face Wash", "Hair Oil", "Sunscreen", "Body Lotion"],
}
SIZES = ["S", "M", "L", "XL", "XXL"]
COLOURS = ["Black", "White", "Navy", "Olive", "Grey", "Maroon", "Teal"]
PRICE_RANGE = {
    "APPAREL": (499, 2999),
    "FOOTWEAR": (799, 4999),
    "HOME": (199, 1999),
    "ELECTRONICS": (299, 2499),
    "BEAUTY": (149, 999),
}

# Five regions, so regional warehouses and stores can be matched to an order.
REGION_CITIES = {
    "SOUTH": ["Hyderabad", "Bengaluru", "Chennai", "Kochi", "Visakhapatnam"],
    "NORTH": ["Delhi", "Chandigarh", "Jaipur", "Lucknow", "Amritsar"],
    "WEST": ["Mumbai", "Pune", "Ahmedabad", "Surat", "Nashik"],
    "EAST": ["Kolkata", "Bhubaneswar", "Guwahati", "Patna", "Ranchi"],
    "CENTRAL": ["Nagpur", "Bhopal", "Indore", "Raipur", "Jabalpur"],
}
REGIONS = list(REGION_CITIES)

# code, is_online, counts_inbound, applies_safety_stock, hold_minutes
CHANNELS = [
    ("WEB", True, True, True, 30),
    ("APP", True, True, True, 30),
    # A marketplace penalises cancellations, so it never sells stock still on a truck.
    ("MARKETPLACE", True, False, True, 120),
    # Safety stock keeps online orders off the shelves, so it doesn't apply in store.
    ("STORE", False, False, False, None),
]

# Units each channel keeps back: warehouse buffer, store buffer.
CHANNEL_BUFFERS = {"WEB": (3, 1), "APP": (3, 1), "MARKETPLACE": (8, 3), "STORE": (0, 0)}


def build_products_and_skus(rng, scale):
    start = config.START_AT.date()
    end = config.AS_OF.date()
    period_days = (end - start).days

    product_count = max(1, round(scale["skus"] / settings.SKUS_PER_PRODUCT))
    categories = list(CATEGORIES)

    products = []
    for number in range(1, product_count + 1):
        category = categories[(number - 1) % len(categories)]
        name = f"{rng.choice(CATEGORIES[category])} {number}"
        products.append({
            "product_id": f"PRD-{number:06d}",
            "product_name": name,
            "category": category,
            # Invented labels, so no real brand appears in the data.
            "brand": f"Brand {rng.randint(1, 40):02d}",
            "launch_date": None,
            "status": "ACTIVE",
        })

    skus = []
    for number in range(1, scale["skus"] + 1):
        # Share SKUs out evenly so every product has at least one.
        product = products[(number - 1) % product_count]
        low, high = PRICE_RANGE[product["category"]]
        # Prices like 1299.00 look like a real price list.
        unit_price = rng.randint(low // 100, high // 100) * 100 - 1

        effective_from = date(2025, 6, 1)
        effective_to = None
        status = "ACTIVE"
        roll = rng.random()
        if roll < settings.LATE_LAUNCH_SHARE:
            effective_from = start + timedelta(days=rng.randint(30, period_days - 60))
        elif roll < settings.LATE_LAUNCH_SHARE + settings.DISCONTINUED_SHARE:
            effective_to = start + timedelta(days=rng.randint(60, period_days - 30))
            status = "DISCONTINUED"

        skus.append({
            "sku_id": f"SKU-{number:06d}",
            "product_id": product["product_id"],
            "size": rng.choice(SIZES),
            "colour": rng.choice(COLOURS),
            "unit_price": f"{unit_price}.00",
            "status": status,
            "effective_from": effective_from,
            "effective_to": effective_to,
        })

    # A product's dates and status come from its SKUs.
    by_product = {}
    for sku in skus:
        by_product.setdefault(sku["product_id"], []).append(sku)
    for product in products:
        variants = by_product[product["product_id"]]
        product["launch_date"] = min(sku["effective_from"] for sku in variants)
        if all(sku["status"] == "DISCONTINUED" for sku in variants):
            product["status"] = "DISCONTINUED"

    # Popularity is a long tail: a few SKUs sell a lot, most sell a little. The
    # ranks are shuffled so a low SKU number doesn't mean popular.
    ranks = list(range(1, scale["skus"] + 1))
    rng.shuffle(ranks)
    for sku, rank in zip(skus, ranks):
        sku["popularity"] = 1 / (rank ** 0.9)

    return products, skus


def build_locations(rng, scale):
    """Three levels: one NDC, then warehouses, then stores."""
    total = scale["locations"]
    warehouse_count = max(2, round(total * settings.WAREHOUSE_SHARE))
    store_count = total - 1 - warehouse_count
    if store_count < 1:
        raise ValueError("need at least 4 locations: an NDC, two warehouses and a store")

    start = config.START_AT.date()
    long_ago = date(2020, 4, 1)
    locations = [{
        "location_id": "LOC-000001",
        "location_name": "National DC Nagpur",
        "location_type": "NDC",
        "region": "CENTRAL",
        "parent_location_id": None,
        "ships_online": True,
        "opened_on": long_ago,
        "closed_on": None,
        "size_weight": 0.0,
    }]

    warehouses = []
    for index in range(warehouse_count):
        region = REGIONS[index % len(REGIONS)]
        city = REGION_CITIES[region][index // len(REGIONS) % 5]
        warehouse = {
            "location_id": f"LOC-{len(locations) + 1:06d}",
            "location_name": f"Warehouse {city}",
            "location_type": "WAREHOUSE",
            "region": region,
            "parent_location_id": "LOC-000001",
            "ships_online": True,
            "opened_on": long_ago,
            "closed_on": None,
            "size_weight": 0.0,
        }
        locations.append(warehouse)
        warehouses.append(warehouse)

    late_store_count = max(1, round(store_count * 0.02))
    for index in range(store_count):
        warehouse = warehouses[index % warehouse_count]
        cities = REGION_CITIES[warehouse["region"]]
        city = cities[index % len(cities)]
        opened_on = date(2021, 1, 1) + timedelta(days=rng.randint(0, 1400))
        # The last few stores open during the period, with nothing in stock until then.
        if index >= store_count - late_store_count:
            opened_on = start + timedelta(days=rng.randint(60, 150))
        locations.append({
            "location_id": f"LOC-{len(locations) + 1:06d}",
            "location_name": f"Store {index + 1:03d} {city}",
            "location_type": "STORE",
            "region": warehouse["region"],
            "parent_location_id": warehouse["location_id"],
            "ships_online": rng.random() < settings.SHIP_FROM_STORE_SHARE,
            "opened_on": opened_on,
            "closed_on": None,
            # Flagship stores sell far more than small ones.
            "size_weight": rng.choice([0.5, 0.7, 1.0, 1.0, 1.4, 2.5]),
        })

    return locations


def build_channel_locations(locations):
    rows = []
    for code, is_online, _inbound, _safety, _hold in CHANNELS:
        warehouse_buffer, store_buffer = CHANNEL_BUFFERS[code]
        for location in locations:
            is_store = location["location_type"] == "STORE"
            if is_online and not location["ships_online"]:
                continue
            if not is_online and not is_store:
                continue
            rows.append({
                "channel_location_id": f"CHL-{len(rows) + 1:06d}",
                "channel_code": code,
                "location_id": location["location_id"],
                "buffer_units": store_buffer if is_store else warehouse_buffer,
                "effective_from": max(location["opened_on"], date(2025, 6, 1)),
                "effective_to": None,
            })
    return rows


def build_pairs(rng, scale, skus, locations):
    """
    Decide what is stocked where. A pair is one SKU at one location.

    Stores carry a share of the range, leaning towards popular SKUs. Warehouses
    and the NDC carry everything their stores might need.
    """
    top_popularity = max(sku["popularity"] for sku in skus)
    share = scale["store_assortment_share"]
    stores = [loc for loc in locations if loc["location_type"] == "STORE"]
    total_store_weight = sum(store["size_weight"] for store in stores)

    # A rough walk-in demand target, used to size stock and reorder settings.
    period_days = (config.AS_OF.date() - config.START_AT.date()).days + 1
    daily_units = scale["movements"] * settings.UNITS_PER_MOVEMENT / period_days

    pairs = []
    children_by_parent = {}
    for store in stores:
        ranged = []
        for sku in skus:
            chance = share * (0.6 + 0.4 * sku["popularity"] / top_popularity)
            # The most popular SKUs are always stocked, so demand has somewhere to go.
            wanted = sku["popularity"] > 0.05 * top_popularity or rng.random() < chance
            # A store that opens after a SKU has stopped can never stock it.
            stopped = sku["effective_to"] is not None and sku["effective_to"] <= store["opened_on"]
            if wanted and not stopped:
                ranged.append(sku)

        store_daily = daily_units * store["size_weight"] / total_store_weight
        weight_total = sum(sku["popularity"] for sku in ranged)
        for sku in ranged:
            forecast = store_daily * sku["popularity"] / weight_total
            pairs.append(make_pair(sku, store, forecast))
            key = (sku["sku_id"], store["parent_location_id"])
            children_by_parent[key] = children_by_parent.get(key, 0) + forecast

    # A warehouse's forecast is what its stores are expected to need.
    warehouses = [loc for loc in locations if loc["location_type"] == "WAREHOUSE"]
    ndc = locations[0]
    ndc_totals = {}
    for warehouse in warehouses:
        for sku in skus:
            forecast = children_by_parent.get((sku["sku_id"], warehouse["location_id"]), 0.0)
            pairs.append(make_pair(sku, warehouse, max(forecast, 0.05)))
            ndc_totals[sku["sku_id"]] = ndc_totals.get(sku["sku_id"], 0.0) + forecast
    for sku in skus:
        pairs.append(make_pair(sku, ndc, max(ndc_totals[sku["sku_id"]], 0.05)))

    return pairs


def make_pair(sku, location, forecast):
    return {
        "sku": sku,
        "location": location,
        "sku_id": sku["sku_id"],
        "location_id": location["location_id"],
        "ranged_from": max(sku["effective_from"], location["opened_on"]),
        "forecast_daily": forecast,
    }


def build_rules(pairs):
    """Every number here is calculated from the forecast. See docs/PLAN.md."""
    rules = []
    for pair in pairs:
        location_type = pair["location"]["location_type"]
        forecast = pair["forecast_daily"]
        lead_time = settings.LEAD_TIME_DAYS[location_type]

        safety_stock = max(1, math.ceil(forecast * settings.SAFETY_DAYS))
        reorder_point = safety_stock + math.ceil(forecast * lead_time)
        reorder_qty = max(settings.MIN_BATCH[location_type], math.ceil(forecast * settings.COVER_DAYS))

        rule = {
            "rule_id": f"RUL-{len(rules) + 1:07d}",
            "sku_id": pair["sku_id"],
            "location_id": pair["location_id"],
            "source_location_id": pair["location"]["parent_location_id"],
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "reorder_qty": reorder_qty,
            "lead_time_days": lead_time,
            "allow_inbound_atp": location_type != "NDC",
            "effective_from": pair["ranged_from"],
            "effective_to": pair["sku"]["effective_to"],
        }
        rules.append(rule)
        pair["rule"] = rule
    return rules


def build_opening_stock(rng, pairs):
    """
    Opening stock is an input, set from the reorder point so nothing starts out
    of stock. A pair not yet ranged on day one starts at zero.
    """
    start = config.START_AT.date()
    for pair in pairs:
        if pair["ranged_from"] > start:
            pair["opening_on_hand"] = 0
            pair["opening_blocked"] = 0
            continue

        forecast = pair["forecast_daily"]
        cover = math.ceil(forecast * rng.uniform(7, 21))
        on_hand = pair["rule"]["reorder_point"] + cover + rng.randint(0, 3)
        blocked = 0
        if on_hand >= 5 and rng.random() < settings.OPENING_BLOCKED_SHARE:
            blocked = rng.randint(1, 3)
        pair["opening_on_hand"] = on_hand
        pair["opening_blocked"] = blocked
