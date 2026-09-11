"""Held-out verifier: JPY and KWD follow their own minor units end to end."""

from datetime import date
from decimal import Decimal

import pytest

from ledgerly import (Account, Chart, Entry, Ledger, Line, Money, RateTable, billing, importers,
                      reports, store)
from ledgerly.currency import UnknownCurrency


def test_c1_rounding_uses_the_currency_minor_unit():
    assert Money("2.5", "JPY").rounded() == Money("2", "JPY")
    assert Money("3.5", "JPY").rounded() == Money("4", "JPY")
    assert Money("1.0005", "KWD").rounded() == Money("1.000", "KWD")
    assert Money("1.0015", "KWD").rounded() == Money("1.002", "KWD")
    assert str(Money("1234.5", "JPY")) == "1234 JPY"
    assert str(Money("7.1", "KWD")) == "7.100 KWD"


def test_c2_allocation_in_whole_minor_units():
    assert Money("1000", "JPY").allocate([1, 1, 1]) == [Money(v, "JPY") for v in ("334", "333", "333")]
    assert Money("1.000", "KWD").allocate([1, 1, 1]) == [
        Money(v, "KWD") for v in ("0.334", "0.333", "0.333")]
    parts = Money("10", "JPY").allocate([1, 2, 4])
    assert sum(parts, Money.zero("JPY")) == Money("10", "JPY")
    assert all(p.amount == p.amount.to_integral_value() for p in parts)


def test_c3_import_respects_minor_units():
    assert importers.parse_amount("1,234.567", "KWD") == Decimal("1234.567")
    assert importers.parse_amount("1,234", "JPY") == Decimal("1234")
    for bad, cur in (("1,234.5", "JPY"), ("12.3456", "KWD"), ("12.345", "USD")):
        with pytest.raises(ValueError):
            importers.parse_amount(bad, cur)
    entries = importers.parse_bank_csv('date,description,amount\n2024-05-02,Deposit,"250,000"\n',
                                       bank_account="1020", contra_account="4000", currency="JPY")
    assert entries[0].lines[0].amount == Money("250000", "JPY")


def test_c4_reports_format_with_the_currency_minor_unit():
    assert reports.format_money(Money("1234.6", "JPY")) == "1,235 JPY"
    assert reports.format_money(Money("12.3454", "KWD")) == "12.345 KWD"
    assert reports.format_money(Money("1234.5", "EUR")) == "1,234.50 EUR"


def test_c5_conversion_rounds_to_the_target_currency():
    rates = RateTable()
    rates.add(date(2024, 5, 1), "USD", "JPY", Decimal("151.237"))
    rates.add(date(2024, 5, 1), "KWD", "USD", Decimal("3.2513"))
    on = date(2024, 5, 2)
    assert rates.convert(Money("10.00", "USD"), "JPY", on) == Money("1512", "JPY")
    assert rates.convert(Money("100.00", "USD"), "KWD", on) == Money("30.757", "KWD")


def _chart():
    return Chart([Account("1020", "Bank JPY", "asset"), Account("1030", "Bank KWD", "asset"),
                  Account("4000", "Sales", "income"), Account("4100", "Services", "income")])


def test_c6_ledger_store_and_reports_in_jpy(tmp_path):
    rates = RateTable()
    rates.add(date(2024, 5, 1), "KWD", "USD", Decimal("3.2513"))
    rates.add(date(2024, 5, 1), "USD", "JPY", Decimal("151.237"))
    ledger = Ledger(_chart(), rates, reporting_currency="JPY")
    ledger.post(billing.split_invoice("INV-1", date(2024, 5, 3), Money("1000", "JPY"), "1020",
                                      {"4000": 1, "4100": 2}))
    k = Money("12.345", "KWD")
    ledger.post(Entry("K-1", date(2024, 5, 4), [Line("1030", k), Line("4000", -k)]))
    tb = reports.trial_balance(ledger)
    assert tb["total_debit"] == tb["total_credit"]
    assert tb["total_debit"].amount == tb["total_debit"].amount.to_integral_value()
    store.save(ledger, tmp_path / "jp.json")
    back = store.load(tmp_path / "jp.json")
    assert back.balance("1030") == {"KWD": Money("12.345", "KWD")}
    assert back.balance("4100") == {"JPY": Money("-667", "JPY")}


def test_r_existing_currencies_unchanged():
    assert Money("0.125", "EUR").rounded() == Money("0.12", "EUR")
    assert Money("100.00", "USD").allocate([1, 1, 1]) == [
        Money(v, "USD") for v in ("33.34", "33.33", "33.33")]
    assert importers.parse_amount("12.34", "GBP") == Decimal("12.34")
    with pytest.raises(UnknownCurrency):
        Money("1", "XXX")
