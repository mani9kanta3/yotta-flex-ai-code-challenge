"""The stock rules. No database, so these run in milliseconds."""

from app import rules


def test_on_hand_includes_blocked():
    assert rules.on_hand(sellable=95, blocked=5) == 100


def test_walk_in_available_leaves_out_reserved():
    assert rules.walk_in_available(sellable=95, reserved=12) == 83


def test_can_sell_exactly_what_is_available():
    assert rules.can_sell_walk_in(qty=3, sellable=5, reserved=2)


def test_cannot_sell_one_more_than_available():
    assert not rules.can_sell_walk_in(qty=4, sellable=5, reserved=2)


def test_cannot_sell_stock_held_for_an_online_order():
    assert not rules.can_sell_walk_in(qty=1, sellable=5, reserved=5)
