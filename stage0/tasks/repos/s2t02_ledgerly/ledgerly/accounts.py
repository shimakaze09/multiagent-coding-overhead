"""The chart of accounts."""

from dataclasses import dataclass

TYPES = ("asset", "liability", "equity", "income", "expense")
DEBIT_NORMAL = ("asset", "expense")


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    type: str

    def __post_init__(self):
        if self.type not in TYPES:
            raise ValueError(f"unknown account type {self.type!r}")

    @property
    def normal_side(self):
        return "debit" if self.type in DEBIT_NORMAL else "credit"


class Chart:
    def __init__(self, accounts=()):
        self._accounts = {}
        for account in accounts:
            self.add(account)

    def add(self, account):
        if account.code in self._accounts:
            raise ValueError(f"duplicate account {account.code}")
        self._accounts[account.code] = account

    def get(self, code):
        try:
            return self._accounts[code]
        except KeyError:
            raise KeyError(f"unknown account {code!r}") from None

    def __contains__(self, code):
        return code in self._accounts

    def __iter__(self):
        return iter(sorted(self._accounts.values(), key=lambda a: a.code))

    def __len__(self):
        return len(self._accounts)
