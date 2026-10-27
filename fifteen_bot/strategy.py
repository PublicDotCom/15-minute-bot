"""Per-market trading strategy and the pure decision logic behind it.

Nothing here does I/O, so the rules can be tested without the Public API.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

CENT = Decimal("0.01")

Side = Literal["YES", "NO"]


class Strategy(BaseModel):
    """When and what to buy in each 15-minute window of one market.

    Prices are what you pay per contract, in dollars, which is also the
    market's implied chance of that side winning (0.85 = 85%).
    """

    entry_seconds: int = Field(120, ge=5, le=900, description="Start looking once this many seconds are left.")
    cutoff_seconds: int = Field(10, ge=0, le=900, description="Stop looking once this many seconds are left.")
    min_price: Decimal = Field(Decimal("0.80"), ge=CENT, le=Decimal("0.99"))
    max_price: Decimal = Field(Decimal("0.90"), ge=CENT, le=Decimal("0.99"))
    side: Literal["EITHER", "YES", "NO"] = "EITHER"
    quantity: Decimal = Field(Decimal("1"), gt=0, le=Decimal("10000"), decimal_places=2)

    @model_validator(mode="after")
    def _check_ranges(self) -> "Strategy":
        if self.min_price > self.max_price:
            raise ValueError("the lowest chance must not be above the highest chance")
        if self.cutoff_seconds >= self.entry_seconds:
            raise ValueError("the stop time must be less than the start time")
        if self.min_price != self.min_price.quantize(CENT) or self.max_price != self.max_price.quantize(CENT):
            raise ValueError("chances must be whole percentages")
        return self


@dataclass(frozen=True)
class Quote:
    symbol: str
    ask: Optional[Decimal]


@dataclass(frozen=True)
class Pick:
    side: Side
    symbol: str
    price: Decimal  # limit price, whole cents


def in_entry_window(strategy: Strategy, seconds_left: float) -> bool:
    return strategy.cutoff_seconds < seconds_left <= strategy.entry_seconds


def limit_price(ask: Decimal) -> Decimal:
    """Orders are priced in whole cents; round the ask up so the order can fill."""
    return ask.quantize(CENT, rounding=ROUND_CEILING)


def pick_contract(strategy: Strategy, quotes: dict[Side, Quote]) -> Optional[Pick]:
    """The side to buy, or None when no allowed side is priced inside the range.

    If both sides qualify (only possible with a very wide range), the pricier
    one wins: it is the side the market thinks is more likely.
    """
    allowed: tuple[Side, ...] = ("YES", "NO") if strategy.side == "EITHER" else (strategy.side,)
    candidates = []
    for side in allowed:
        quote = quotes.get(side)
        if quote is None or quote.ask is None or quote.ask <= 0:
            continue
        price = limit_price(quote.ask)
        if strategy.min_price <= price <= strategy.max_price and price < 1:
            candidates.append(Pick(side=side, symbol=quote.symbol, price=price))
    return max(candidates, key=lambda p: p.price, default=None)


def settle_pnl(filled: Decimal, avg_price: Decimal, fees: Decimal, won: bool) -> Decimal:
    """Profit of a filled position once its window settles: each winning contract pays $1."""
    payout = filled if won else Decimal(0)
    return payout - filled * avg_price - fees
