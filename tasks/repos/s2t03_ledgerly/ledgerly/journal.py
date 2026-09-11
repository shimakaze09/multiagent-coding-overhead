"""Journal entries: dated, balanced sets of lines.

Each line has exactly one of ``debit`` or ``credit``: a positive Money amount
(see docs/conventions.md).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .money import Money


class UnbalancedEntry(ValueError):
    pass


@dataclass(frozen=True)
class Line:
    account: str
    debit: Optional[Money] = None
    credit: Optional[Money] = None
    memo: str = ""

    def __post_init__(self):
        if (self.debit is None) == (self.credit is None):
            raise ValueError("a line has exactly one of debit or credit")
        amount = self.debit if self.debit is not None else self.credit
        if not isinstance(amount, Money) or amount.amount <= 0:
            raise ValueError("debit/credit amounts must be positive Money")

    @property
    def currency(self):
        return (self.debit if self.debit is not None else self.credit).currency

    @property
    def signed(self):
        """The line as one signed amount: debits positive, credits negative."""
        return self.debit if self.debit is not None else -self.credit


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
            out[line.currency] = out.get(line.currency, Money.zero(line.currency)) + line.signed
        return out

    def validate(self):
        if len(self.lines) < 2:
            raise UnbalancedEntry(f"{self.id}: an entry needs at least two lines")
        for currency, total in self.net().items():
            if not total.is_zero():
                raise UnbalancedEntry(f"{self.id}: {currency} lines do not balance ({total})")
