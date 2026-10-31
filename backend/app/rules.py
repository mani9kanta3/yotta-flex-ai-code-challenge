"""
The stock rules, as plain functions with no database.

The generator and the tests both use these, so there is one written version of
each rule. docs/PLAN.md has the reasoning. The full ATP and allocation rules
are added in the next step.
"""


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
    return qty <= walk_in_available(sellable, reserved)
