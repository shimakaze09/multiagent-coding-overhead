"""Journal entries: dated, balanced sets of lines.

A line carries a signed amount: positive is a debit, negative is a credit (see
docs/conventions.md). The ledgerly 1.x keyword forms ``Line(account,
debit=...)`` and ``Line(account, credit=...)`` are still accepted but
deprecated.
"""

import warnings
from dataclasses import dataclass, field
from datetime import date

from .money import Money


class UnbalancedEntry(ValueError):
    pass


class Line:
    __slots__ = ("account", "amount", "memo")

    def __init__(self, account, amount=None, memo="", *, debit=None, credit=None):
        if debit is not None or credit is not None:
            warnings.warn("Line(debit=..., credit=...) is deprecated; pass a signed amount",
                          DeprecationWarning, stacklevel=2)
            if amount is not None or (debit is not None and credit is not None):
                raise ValueError("give a signed amount, or exactly one of debit/credit")
            given = debit if debit is not None else credit
            if not isinstance(given, Money) or given.amount <= 0:
                raise ValueError("debit/credit amounts must be positive Money")
            amount = debit if debit is not None else -credit
        if not isinstance(amount, Money):
            raise TypeError("a line amount must be Money")
        if amount.is_zero():
            raise ValueError("a line amount cannot be zero")
        self.account = account
        self.amount = amount
        self.memo = memo

    @property
    def side(self):
        return "debit" if self.amount.amount > 0 else "credit"

    @property
    def debit(self):
        return self.amount if self.amount.amount > 0 else None

    @property
    def credit(self):
        return -self.amount if self.amount.amount < 0 else None

    @property
    def currency(self):
        return self.amount.currency

    def __eq__(self, other):
        return isinstance(other, Line) and (self.account, self.amount, self.memo) == (
            other.account, other.amount, other.memo)

    def __repr__(self):
        return f"Line({self.account!r}, {self.amount!r}, memo={self.memo!r})"


@dataclass
class Entry:
    id: str
    on: date
    lines: list = field(default_factory=list)
    description: str = ""

    def net(self):
        """Net signed amount per currency (zero for a balanced entry)."""
        out = {}
        for line in self.lines:
            out[line.currency] = out.get(line.currency, Money.zero(line.currency)) + line.amount
        return out

    def validate(self):
        if len(self.lines) < 2:
            raise UnbalancedEntry(f"{self.id}: an entry needs at least two lines")
        for currency, total in self.net().items():
            if not total.is_zero():
                raise UnbalancedEntry(f"{self.id}: {currency} lines do not balance ({total})")
