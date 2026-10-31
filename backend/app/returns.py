"""
A customer brings back something they bought.

Every return names the SALE movement it came from, and goes back to the location
that sold it. A sale can come back in parts, but the total can't go above what was
sold. Two returns of the same sale could each see room for themselves, so this takes
the SKU-location lock first and only then counts what has already come back.
"""

from . import orders, rules
from .movements import add_movement


class ReturnRefused(Exception):
    """This return isn't allowed. Nothing was written."""


def get_sale(cursor, sale_movement_id):
    cursor.execute(
        """SELECT movement_type, sku_id, location_id, quantity, occurred_at
           FROM inventory_movement WHERE movement_id = %s""",
        (sale_movement_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ReturnRefused(f"no movement {sale_movement_id}")
    if row[0] != "SALE":
        raise ReturnRefused(f"{sale_movement_id} is a {row[0]}, not a sale")
    return {"sku_id": row[1], "location_id": row[2], "sold": -row[3], "occurred_at": row[4]}


def get_already_returned(cursor, sale_movement_id):
    """Returns (units, number of returns) so far against this sale."""
    cursor.execute(
        """SELECT COALESCE(SUM(quantity), 0), COUNT(*) FROM inventory_movement
           WHERE related_movement_id = %s AND movement_type = 'RETURN'""",
        (sale_movement_id,),
    )
    return cursor.fetchone()


def record_return_in_transaction(cursor, sale_movement_id, qty, condition, t,
                                 lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    orders.start_locked_transaction(cursor, lock_timeout_ms)

    if condition not in rules.RETURN_BUCKETS:
        raise ReturnRefused(f"unknown condition {condition}")
    sale = get_sale(cursor, sale_movement_id)
    if t <= sale["occurred_at"]:
        raise ReturnRefused(f"a return at {t} can't come before its sale at {sale['occurred_at']}")

    pair = (sale["sku_id"], sale["location_id"])
    orders.lock_pairs(cursor, [pair])
    returned_at = orders.decision_time(cursor, [pair], t)

    # Read after the lock, so it sees any return of this sale that just committed.
    already_returned, return_count = get_already_returned(cursor, sale_movement_id)
    if not rules.can_return(qty, sale["sold"], already_returned):
        raise ReturnRefused(f"can't return {qty} of {sale_movement_id}: {sale['sold']} sold, "
                            f"{already_returned} already returned")

    movement_id = add_movement(
        cursor, sale["sku_id"], sale["location_id"], "RETURN", qty, returned_at, f"RETURN_{condition}",
        f"RET-{sale_movement_id}-{return_count + 1}", bucket=rules.RETURN_BUCKETS[condition],
        related_movement_id=sale_movement_id,
    )
    return {"movement_id": movement_id, "returned_at": returned_at}


def record_return(connection, sale_movement_id, qty, condition, t,
                  lock_timeout_ms=orders.DEFAULT_LOCK_TIMEOUT_MS):
    return orders.run_in_transaction(connection, record_return_in_transaction, sale_movement_id, qty,
                                     condition, t, lock_timeout_ms)
