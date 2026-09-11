"""Save and load a ledger as JSON.

Schema 2 (current) stores each line's debit or credit as a positive decimal
string. Files written by older versions must keep loading
(docs/conventions.md).
"""

import json
from datetime import date
from pathlib import Path

from .accounts import Account, Chart
from .fx import RateTable
from .journal import Entry, Line
from .ledger import Ledger
from .money import Money, to_decimal
from .periods import PeriodCalendar

SCHEMA = 2
READABLE_SCHEMAS = (2,)


def to_dict(ledger):
    rates = []
    if ledger.rates is not None:
        for base, quote in ledger.rates.pairs():
            for on, rate in ledger.rates.history(base, quote):
                rates.append({"date": on.isoformat(), "base": base, "quote": quote,
                              "rate": str(rate)})
    return {
        "schema": SCHEMA,
        "reporting_currency": ledger.reporting_currency,
        "accounts": [{"code": a.code, "name": a.name, "type": a.type} for a in ledger.chart],
        "closed_periods": [list(p) for p in ledger.calendar.closed],
        "rates": rates,
        "entries": [
            {"id": e.id, "date": e.on.isoformat(), "description": e.description,
             "lines": [{"account": l.account,
                        "debit": str(l.debit.amount) if l.debit is not None else None,
                        "credit": str(l.credit.amount) if l.credit is not None else None,
                        "currency": l.currency, "memo": l.memo} for l in e.lines]}
            for e in ledger.entries
        ],
    }


def _line(data, schema):
    side = "debit" if data.get("debit") is not None else "credit"
    money = Money(to_decimal(data[side]), data["currency"])
    if side == "debit":
        return Line(data["account"], debit=money, memo=data.get("memo", ""))
    return Line(data["account"], credit=money, memo=data.get("memo", ""))


def from_dict(data):
    schema = data.get("schema")
    if schema not in READABLE_SCHEMAS:
        raise ValueError(f"unsupported ledger file schema {schema!r}")
    chart = Chart(Account(a["code"], a["name"], a["type"]) for a in data["accounts"])
    rates = RateTable()
    for r in data.get("rates", []):
        rates.add(date.fromisoformat(r["date"]), r["base"], r["quote"], to_decimal(r["rate"]))
    ledger = Ledger(chart, rates, PeriodCalendar(), data.get("reporting_currency", "EUR"))
    for e in data["entries"]:
        ledger.post(Entry(e["id"], date.fromisoformat(e["date"]),
                          [_line(l, schema) for l in e["lines"]], e.get("description", "")))
    # historical entries are loaded first; closing applies to new postings only
    ledger.calendar = PeriodCalendar(data.get("closed_periods", []))
    return ledger


def save(ledger, path):
    Path(path).write_text(json.dumps(to_dict(ledger), indent=2), encoding="utf-8")


def load(path):
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
