"""Held-out verifier: exchange-rate lookup must not depend on insertion order."""

import itertools
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from ledgerly import (Account, Chart, Entry, Ledger, Line, MissingRate, Money, RateTable,
                      importers, reports, store)

RATES = [
    (date(2024, 3, 1), "1.2650"), (date(2024, 3, 11), "1.2800"),
    (date(2024, 3, 18), "1.2710"), (date(2024, 3, 25), "1.2620"),
]


def expected_rate(on):
    best = None
    for d, r in RATES:
        if d <= on:
            best = Decimal(r)
    return best


def table_in(order):
    t = RateTable()
    for i in order:
        d, r = RATES[i]
        t.add(d, "GBP", "USD", Decimal(r))
    return t


def every_day():
    d = date(2024, 3, 1)
    while d <= date(2024, 4, 5):
        yield d
        d += timedelta(days=1)


def test_c1_lookup_is_independent_of_insertion_order():
    for order in itertools.permutations(range(len(RATES))):
        t = table_in(order)
        for on in every_day():
            assert t.rate_on(on, "GBP", "USD") == expected_rate(on), (order, on)


def test_c1_reverse_chronological_adds():
    t = table_in([3, 2, 1, 0])
    assert t.rate_on(date(2024, 3, 20), "GBP", "USD") == Decimal("1.2710")
    assert t.rate_on(date(2024, 3, 30), "GBP", "USD") == Decimal("1.2620")
    with pytest.raises(MissingRate):
        t.rate_on(date(2024, 2, 29), "GBP", "USD")


def test_c2_same_date_correction_replaces_whatever_the_order():
    t = table_in([0, 3, 1])
    t.add(date(2024, 3, 11), "GBP", "USD", Decimal("1.2811"))
    t.add(date(2024, 3, 18), "GBP", "USD", Decimal("1.2710"))
    t.add(date(2024, 3, 18), "GBP", "USD", Decimal("1.2705"))
    assert t.rate_on(date(2024, 3, 12), "GBP", "USD") == Decimal("1.2811")
    assert t.rate_on(date(2024, 3, 19), "GBP", "USD") == Decimal("1.2705")
    assert t.rate_on(date(2024, 3, 26), "GBP", "USD") == Decimal("1.2620")


def test_c3_history_is_chronological_with_one_rate_per_date():
    t = table_in([2, 0, 3, 1])
    t.add(date(2024, 3, 1), "GBP", "USD", Decimal("1.2651"))
    hist = t.history("GBP", "USD")
    assert [d for d, _ in hist] == [d for d, _ in RATES]
    assert hist[0] == (date(2024, 3, 1), Decimal("1.2651"))


def test_c3_saved_rates_are_oldest_first(tmp_path):
    chart = Chart([Account("1010", "Bank GBP", "asset"), Account("4000", "Sales", "income")])
    ledger = Ledger(chart, table_in([1, 3, 0, 2]))
    data = store.to_dict(ledger)
    assert [r["date"] for r in data["rates"]] == [d.isoformat() for d, _ in RATES]


def test_c4_inverse_and_triangulated_lookups_follow_corrections():
    t = RateTable()
    t.add(date(2024, 3, 1), "EUR", "USD", Decimal("1.0850"))
    t.add(date(2024, 3, 25), "EUR", "USD", Decimal("1.0810"))
    t.add(date(2024, 3, 11), "EUR", "USD", Decimal("1.0930"))
    for i in (3, 0, 2, 1):
        d, r = RATES[i]
        t.add(d, "GBP", "USD", Decimal(r))
    on = date(2024, 3, 20)
    assert t.rate_on(on, "USD", "EUR") == Decimal(1) / Decimal("1.0930")
    assert t.rate_on(on, "GBP", "EUR") == Decimal("1.2710") / Decimal("1.0930")
    assert t.convert(Money("1000", "GBP"), "EUR", on) == Money("1162.85", "EUR")


def _march_ledger(rates):
    chart = Chart([Account("1010", "Bank GBP", "asset"), Account("4000", "Sales", "income")])
    ledger = Ledger(chart, rates, reporting_currency="EUR")
    for eid, on, amount in (("R-1", date(2024, 3, 5), "5000"), ("R-2", date(2024, 3, 20), "3000"),
                            ("R-3", date(2024, 3, 27), "1000")):
        m = Money(amount, "GBP")
        ledger.post(Entry(eid, on, [Line("1010", m), Line("4000", -m)]))
    return ledger


def test_c5_rate_file_with_a_late_correction():
    text = Path("examples/rates_2024_03.csv").read_text(encoding="utf-8")
    ledger = _march_ledger(importers.parse_rates_csv(text))
    assert reports.format_money(ledger.balance_in("1010", date(2024, 3, 21))) == "9,318.06 EUR"
    assert ledger.balance_in("1010", date(2024, 3, 31)) == Money("10485.49", "EUR")
    tb = reports.trial_balance(ledger, date(2024, 3, 31))
    assert tb["total_debit"] == tb["total_credit"] == Money("10485.49", "EUR")


def test_r_chronological_tables_unchanged(tmp_path):
    t = table_in(range(len(RATES)))
    assert [t.rate_on(on, "GBP", "USD") for on in every_day()] == [expected_rate(on) for on in every_day()]
    ledger = _march_ledger(t)
    store.save(ledger, tmp_path / "l.json")
    back = store.load(tmp_path / "l.json")
    for on in every_day():
        assert back.rates.rate_on(on, "GBP", "USD") == expected_rate(on)
    with pytest.raises(ValueError):
        t.add(date(2024, 3, 1), "GBP", "USD", Decimal("0"))
