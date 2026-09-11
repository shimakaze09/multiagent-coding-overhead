"""Percentage discounts.

Contract (the invoicing service depends on it):

* A discount is computed on the UNIT price and rounded half up to the
  currency's minor unit, BEFORE the quantity is applied.
* So a line's net total is always ``net_unit_price(...).times(qty)``: an
  invoice line can be reproduced from its unit price alone, and every line on
  an invoice is an exact multiple of its net unit price.
"""

from decimal import Decimal, ROUND_HALF_UP

from .money import Money


def unit_discount(unit_price, percent):
    """Discount on ONE unit, rounded half up to the minor unit."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    raw = Decimal(unit_price.amount_minor) * Decimal(percent) / Decimal(100)
    return Money(
        int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP)), unit_price.currency
    )


def net_unit_price(unit_price, percent):
    """Unit price after the discount."""
    return unit_price - unit_discount(unit_price, percent)
