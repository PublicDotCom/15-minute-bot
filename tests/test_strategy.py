from decimal import Decimal

import pytest
from pydantic import ValidationError

from fifteen_bot.strategy import Quote, Strategy, in_entry_window, pick_contract, settle_pnl

D = Decimal


def quotes(yes_ask, no_ask):
    return {
        "YES": Quote("KALSHI.X-1.Y", None if yes_ask is None else D(yes_ask)),
        "NO": Quote("KALSHI.X-1.N", None if no_ask is None else D(no_ask)),
    }


def test_defaults_match_the_example_strategy():
    s = Strategy()
    assert (s.entry_seconds, s.min_price, s.max_price, s.side) == (120, D("0.80"), D("0.90"), "EITHER")


@pytest.mark.parametrize(
    ("seconds_left", "expected"),
    [(121, False), (120, True), (60, True), (11, True), (10, False), (0, False)],
)
def test_entry_window(seconds_left, expected):
    assert in_entry_window(Strategy(), seconds_left) is expected


def test_picks_the_side_inside_the_range():
    pick = pick_contract(Strategy(), quotes("0.14", "0.86"))
    assert pick is not None and (pick.side, pick.price) == ("NO", D("0.86"))


def test_range_bounds_are_inclusive():
    assert pick_contract(Strategy(), quotes("0.80", "0.21")).side == "YES"
    assert pick_contract(Strategy(), quotes("0.90", "0.11")).side == "YES"


def test_nothing_when_favourite_is_too_cheap_or_too_expensive():
    assert pick_contract(Strategy(), quotes("0.75", "0.26")) is None
    assert pick_contract(Strategy(), quotes("0.95", "0.06")) is None


def test_sub_cent_ask_is_rounded_up_before_the_range_check():
    # 0.901 must be paid as 0.91, which is above the 90% cap.
    assert pick_contract(Strategy(), quotes("0.901", "0.10")) is None
    pick = pick_contract(Strategy(), quotes("0.795", "0.21"))
    assert pick.price == D("0.80")


def test_side_restriction():
    assert pick_contract(Strategy(side="YES"), quotes("0.14", "0.86")) is None
    assert pick_contract(Strategy(side="NO"), quotes("0.14", "0.86")).side == "NO"


def test_both_sides_in_a_wide_range_prefers_the_favourite():
    s = Strategy(min_price=D("0.40"), max_price=D("0.60"))
    assert pick_contract(s, quotes("0.45", "0.56")).side == "NO"


def test_missing_ask_is_skipped():
    assert pick_contract(Strategy(), quotes(None, "0.85")).side == "NO"
    assert pick_contract(Strategy(), quotes(None, None)) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_price": D("0.95"), "max_price": D("0.90")},
        {"entry_seconds": 60, "cutoff_seconds": 60},
        {"min_price": D("0.805")},
        {"quantity": D("0")},
    ],
)
def test_invalid_strategies_are_rejected(kwargs):
    with pytest.raises(ValidationError):
        Strategy(**kwargs)


def test_settle_pnl():
    assert settle_pnl(D("10"), D("0.85"), D("0.20"), won=True) == D("1.30")
    assert settle_pnl(D("10"), D("0.85"), D("0.20"), won=False) == D("-8.70")
