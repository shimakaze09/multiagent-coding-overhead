"""Exchange rates with effective dates.

A rate for a currency pair applies from its effective date until the next rate
for the same pair. Adding a rate for a pair and date that already has one
replaces it (a correction). A pair with no stored rate uses the inverse of the
opposite pair, or is triangulated through the pivot currency. See
docs/conventions.md.
"""

import bisect
from decimal import Decimal

from .money import Money, to_decimal

PIVOT = "USD"
_AFTER_ANY_RATE = Decimal("Infinity")


class MissingRate(LookupError):
    pass


class RateTable:
    def __init__(self):
        self._series = {}  # (base, quote) -> [(date, rate)], ascending by date

    def add(self, on, base, quote, rate):
        rate = to_decimal(rate)
        if rate <= 0:
            raise ValueError("a rate must be positive")
        if base == quote:
            raise ValueError("base and quote must differ")
        series = self._series.setdefault((base, quote), [])
        for i, (existing, _old) in enumerate(series):
            if existing == on:
                series[i] = (on, rate)
                return
        series.append((on, rate))

    def pairs(self):
        return sorted(self._series)

    def history(self, base, quote):
        """The stored rates of one pair as (date, rate), oldest first."""
        return list(self._series.get((base, quote), []))

    def _stored(self, on, base, quote):
        series = self._series.get((base, quote))
        if not series:
            return None
        i = bisect.bisect_right(series, (on, _AFTER_ANY_RATE))
        return series[i - 1][1] if i else None

    def rate_on(self, on, base, quote):
        if base == quote:
            return Decimal(1)
        rate = self._stored(on, base, quote)
        if rate is not None:
            return rate
        inverse = self._stored(on, quote, base)
        if inverse is not None:
            return Decimal(1) / inverse
        if PIVOT not in (base, quote):
            try:
                return self.rate_on(on, base, PIVOT) * self.rate_on(on, PIVOT, quote)
            except MissingRate:
                pass
        raise MissingRate(f"no {base}/{quote} rate on {on}")

    def convert(self, money, to, on):
        """Convert at the rate effective on ``on``; the result is rounded."""
        return Money(money.amount * self.rate_on(on, money.currency, to), to).rounded()
