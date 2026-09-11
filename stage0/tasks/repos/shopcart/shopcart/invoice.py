"""Invoices, as rendered by the invoicing service.

Each line is priced per unit via ``discounts.net_unit_price`` and multiplied by
the quantity afterwards. The totals printed here are what the customer is
charged; anything else that shows a total to the customer must agree with them.
"""

from .discounts import net_unit_price
from .money import Money


class Invoice:
    def __init__(self, currency):
        self.currency = currency.upper()
        self.lines = []  # (sku, qty, net_unit, line_total)

    def add_line(self, sku, unit_price, qty, discount_percent=0):
        if unit_price.currency != self.currency:
            raise ValueError(
                "invoice is in %s, got a %s price" % (self.currency, unit_price.currency)
            )
        net_unit = net_unit_price(unit_price, discount_percent)
        self.lines.append((sku, qty, net_unit, net_unit.times(qty)))
        return self

    @classmethod
    def from_cart(cls, cart):
        invoice = cls(cart.currency)
        for line in cart.lines:
            invoice.add_line(line.sku, line.unit_price, line.qty, line.discount_percent)
        return invoice

    def total(self):
        total = Money.zero(self.currency)
        for _sku, _qty, _net_unit, line_total in self.lines:
            total = total + line_total
        return total

    def render(self):
        out = ["INVOICE (%s)" % self.currency]
        for sku, qty, net_unit, line_total in self.lines:
            out.append(
                "%s  x%d  @ %s  = %s" % (sku, qty, net_unit.major(), line_total.major())
            )
        out.append("TOTAL %s" % self.total().major())
        return "\n".join(out)
