"""Money: a Decimal amount in one currency.

Arithmetic keeps full precision. Amounts are rounded to the currency's minor
unit (ROUND_HALF_EVEN) only when asked (``rounded()``), and when a value is
allocated, stored or reported. See docs/conventions.md.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal

from .currency import minor_units, quantum


class CurrencyMismatch(ValueError):
    pass


def to_decimal(value):
    if isinstance(value, float):
        raise TypeError("use Decimal, int or str for money, not float")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        object.__setattr__(self, "amount", to_decimal(self.amount))
        minor_units(self.currency)  # rejects unsupported currencies

    @classmethod
    def zero(cls, currency):
        return cls(Decimal(0), currency)

    def _same(self, other):
        if not isinstance(other, Money):
            raise TypeError(f"cannot combine Money with {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatch(f"{self.currency} vs {other.currency}")

    def __add__(self, other):
        self._same(other)
        return Money(self.amount + other.amount, self.currency)

    def __radd__(self, other):
        if other == 0:  # sum()
            return self
        return self.__add__(other)

    def __sub__(self, other):
        self._same(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self):
        return Money(-self.amount, self.currency)

    def __mul__(self, factor):
        if isinstance(factor, (Money, float)):
            raise TypeError("multiply Money by an int or Decimal")
        return Money(self.amount * to_decimal(factor), self.currency)

    __rmul__ = __mul__

    def rounded(self):
        return Money(self.amount.quantize(quantum(self.currency), rounding=ROUND_HALF_EVEN),
                     self.currency)

    def is_zero(self):
        return self.amount == 0

    def allocate(self, ratios):
        """Split ``self.rounded()`` into parts proportional to ``ratios``,
        each rounded to the currency's minor unit."""
        ratios = [to_decimal(r) for r in ratios]
        if not ratios or any(r < 0 for r in ratios) or sum(ratios) == 0:
            raise ValueError("ratios must be non-negative and not all zero")
        total = self.rounded()
        whole = sum(ratios)
        parts = [Money(total.amount * r / whole, self.currency).rounded() for r in ratios]
        parts[-1] = parts[-1] + (total - sum(parts, Money.zero(self.currency)))
        return parts

    def __str__(self):
        return f"{self.rounded().amount} {self.currency}"
