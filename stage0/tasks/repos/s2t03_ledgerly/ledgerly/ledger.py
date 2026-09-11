"""The ledger: posted entries and account balances."""

from .money import Money
from .periods import PeriodCalendar, PeriodClosed


class Ledger:
    def __init__(self, chart, rates=None, calendar=None, reporting_currency="EUR"):
        self.chart = chart
        self.rates = rates
        self.calendar = calendar or PeriodCalendar()
        self.reporting_currency = reporting_currency
        self.entries = []

    def post(self, entry):
        entry.validate()
        for line in entry.lines:
            if line.account not in self.chart:
                raise KeyError(f"{entry.id}: unknown account {line.account!r}")
        if not self.calendar.is_open(entry.on):
            raise PeriodClosed(f"{entry.id}: period {entry.on:%Y-%m} is closed")
        if any(e.id == entry.id for e in self.entries):
            raise ValueError(f"duplicate entry id {entry.id}")
        self.entries.append(entry)

    def lines_for(self, code, as_of=None):
        for entry in self.entries:
            if as_of is not None and entry.on > as_of:
                continue
            for line in entry.lines:
                if line.account == code:
                    yield entry, line

    def balance(self, code, as_of=None):
        """Net debit balance per currency (credits negative), full precision."""
        out = {}
        for _entry, line in self.lines_for(code, as_of):
            out[line.currency] = out.get(line.currency, Money.zero(line.currency)) + line.signed
        return out

    def convert_line(self, entry, line, currency):
        """A line's signed amount in ``currency`` at the rate effective on the
        entry's date, unrounded."""
        if line.currency == currency:
            return line.signed
        if self.rates is None:
            raise LookupError("this ledger has no rate table")
        rate = self.rates.rate_on(entry.on, line.currency, currency)
        return Money(line.signed.amount * rate, currency)

    def balance_in(self, code, as_of=None, currency=None):
        """Net debit balance in ``currency`` (default: the reporting currency).
        Lines convert at their own entry's rate; the total is rounded once."""
        currency = currency or self.reporting_currency
        total = Money.zero(currency)
        for entry, line in self.lines_for(code, as_of):
            total += self.convert_line(entry, line, currency)
        return total.rounded()
