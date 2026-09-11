"""Money values.

Amounts are held as integers in the currency's minor unit, so arithmetic is
exact. Conversion to and from major units goes through ``shopcart.currency``,
which knows how many minor units each currency has.
"""

from dataclasses import dataclass

from . import currency as _currency


@dataclass(frozen=True)
class Money:
    amount_minor: int
    currency: str

    @classmethod
    def of(cls, amount, code):
        """Build from a major-unit amount, e.g. Money.of("1.05", "USD")."""
        code = code.upper()
        return cls(_currency.to_minor(amount, code), code)

    @classmethod
    def zero(cls, code):
        return cls(0, code.upper())

    def __add__(self, other):
        self._check(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other):
        self._check(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def times(self, qty):
        """Multiply by a whole quantity."""
        if not isinstance(qty, int) or qty < 0:
            raise ValueError("quantity must be a non-negative integer")
        return Money(self.amount_minor * qty, self.currency)

    def major(self):
        """Amount in major units as an exact Decimal, e.g. Decimal('2.67')."""
        return _currency.to_major(self.amount_minor, self.currency)

    def __str__(self):
        return "%s %s" % (self.major(), self.currency)

    def _check(self, other):
        if not isinstance(other, Money):
            raise TypeError("can only combine Money with Money")
        if other.currency != self.currency:
            raise ValueError(
                "currency mismatch: %s vs %s" % (self.currency, other.currency)
            )
