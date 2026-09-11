"""Shopping cart.

A cart holds lines (SKU, unit price, quantity, optional percentage discount) in
a single currency and reports totals. The invoicing service renders invoices
from the same lines (see ``Invoice.from_cart``), so a cart total must always
agree with the invoice total.
"""

from decimal import Decimal

from .money import Money


class Line:
    __slots__ = ("sku", "unit_price", "qty", "discount_percent")

    def __init__(self, sku, unit_price, qty, discount_percent=0):
        self.sku = sku
        self.unit_price = unit_price
        self.qty = qty
        self.discount_percent = discount_percent

    def __repr__(self):
        return "Line(%r, %r, qty=%d, discount_percent=%r)" % (
            self.sku,
            self.unit_price,
            self.qty,
            self.discount_percent,
        )


class Cart:
    def __init__(self, currency):
        self.currency = currency.upper()
        self.lines = []

    def add(self, sku, unit_price, qty=1, discount_percent=0):
        if unit_price.currency != self.currency:
            raise ValueError(
                "cart is in %s, got a %s price" % (self.currency, unit_price.currency)
            )
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError("quantity must be a positive integer")
        if not 0 <= discount_percent <= 100:
            raise ValueError("discount_percent must be between 0 and 100")
        self.lines.append(Line(sku, unit_price, qty, discount_percent))
        return self

    def item_count(self):
        return sum(line.qty for line in self.lines)

    def line_total(self, line):
        """Net total for one line."""
        gross = line.unit_price.amount_minor * line.qty
        discount = gross * line.discount_percent // 100
        return Money(gross - discount, self.currency)

    def total(self):
        total = Money.zero(self.currency)
        for line in self.lines:
            total = total + self.line_total(line)
        return total

    def total_major(self):
        """Cart total in major units (e.g. dollars), for display."""
        return Decimal(self.total().amount_minor) / 100
