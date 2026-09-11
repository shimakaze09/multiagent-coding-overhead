from datetime import date
from decimal import Decimal

import pytest

from ledgerly import (Account, Chart, CurrencyMismatch, Entry, Ledger, Line, MissingRate, Money,
                      PeriodCalendar, PeriodClosed, RateTable, UnbalancedEntry, billing, importers,
                      reports, store)


def chart():
    return Chart([Account("1000", "Bank EUR", "asset"), Account("1010", "Bank USD", "asset"),
                  Account("2000", "Payables", "liability"), Account("4000", "Sales", "income"),
                  Account("4100", "Services", "income"), Account("6000", "Rent", "expense")])


def sale(entry_id, on, amount, currency="EUR", bank="1000"):
    m = Money(amount, currency)
    return Entry(entry_id, on, [Line(bank, debit=m), Line("4000", credit=m)])


def test_money_arithmetic_and_currency_checks():
    assert Money("1.10", "EUR") + Money("2.20", "EUR") == Money("3.30", "EUR")
    assert Money("5", "EUR") - Money("1.5", "EUR") == Money("3.5", "EUR")
    assert -Money("2", "USD") == Money("-2", "USD")
    assert Money("2.50", "EUR") * 3 == Money("7.50", "EUR")
    with pytest.raises(CurrencyMismatch):
        Money("1", "EUR") + Money("1", "USD")
    with pytest.raises(TypeError):
        Money(1.5, "EUR")


def test_rounding_is_half_even():
    assert Money("0.125", "EUR").rounded() == Money("0.12", "EUR")
    assert Money("0.135", "EUR").rounded() == Money("0.14", "EUR")
    assert str(Money("2.5", "USD")) == "2.50 USD"


def test_simple_allocations():
    assert Money("100.00", "EUR").allocate([1, 1]) == [Money("50.00", "EUR"), Money("50.00", "EUR")]
    assert Money("100.00", "EUR").allocate([1, 3]) == [Money("25.00", "EUR"), Money("75.00", "EUR")]
    with pytest.raises(ValueError):
        Money("1", "EUR").allocate([0, 0])


def test_rates_in_date_order():
    rates = RateTable()
    rates.add(date(2024, 1, 1), "EUR", "USD", Decimal("1.10"))
    rates.add(date(2024, 2, 1), "EUR", "USD", Decimal("1.08"))
    rates.add(date(2024, 1, 1), "GBP", "USD", Decimal("1.25"))
    assert rates.rate_on(date(2024, 1, 15), "EUR", "USD") == Decimal("1.10")
    assert rates.rate_on(date(2024, 2, 1), "EUR", "USD") == Decimal("1.08")
    assert rates.rate_on(date(2024, 1, 15), "USD", "EUR") == Decimal(1) / Decimal("1.10")
    assert rates.rate_on(date(2024, 1, 15), "GBP", "EUR") == Decimal("1.25") / Decimal("1.10")
    with pytest.raises(MissingRate):
        rates.rate_on(date(2023, 12, 31), "EUR", "USD")
    assert rates.convert(Money("10", "EUR"), "USD", date(2024, 1, 2)) == Money("11.00", "USD")


def test_entries_must_balance():
    with pytest.raises(UnbalancedEntry):
        Entry("E-1", date(2024, 1, 1), [Line("1000", debit=Money("10", "EUR")),
                                        Line("4000", credit=Money("9.99", "EUR"))]).validate()
    with pytest.raises(UnbalancedEntry):
        Entry("E-2", date(2024, 1, 1), [Line("1000", debit=Money("10", "EUR"))]).validate()
    with pytest.raises(ValueError):
        Line("1000", Money("0", "EUR"))


def test_ledger_posting_and_balances():
    ledger = Ledger(chart(), calendar=PeriodCalendar([(2023, 12)]))
    ledger.post(sale("S-1", date(2024, 1, 5), "120.00"))
    ledger.post(sale("S-2", date(2024, 1, 9), "30.50"))
    assert ledger.balance("1000") == {"EUR": Money("150.50", "EUR")}
    assert ledger.balance("4000", as_of=date(2024, 1, 6)) == {"EUR": Money("-120.00", "EUR")}
    with pytest.raises(PeriodClosed):
        ledger.post(sale("S-3", date(2023, 12, 30), "1.00"))
    with pytest.raises(ValueError):
        ledger.post(sale("S-1", date(2024, 1, 10), "1.00"))


def test_trial_balance_in_reporting_currency():
    rates = RateTable()
    rates.add(date(2024, 1, 1), "EUR", "USD", Decimal("1.25"))
    ledger = Ledger(chart(), rates, reporting_currency="EUR")
    ledger.post(sale("S-1", date(2024, 1, 5), "100.00"))
    ledger.post(sale("S-2", date(2024, 1, 6), "50.00", "USD", bank="1010"))
    tb = reports.trial_balance(ledger)
    assert tb["total_debit"] == tb["total_credit"] == Money("140.00", "EUR")
    assert [r["code"] for r in tb["rows"]] == ["1000", "1010", "4000"]
    assert "140.00 EUR" in reports.render_trial_balance(tb)
    assert reports.format_money(Money("1234.5", "EUR")) == "1,234.50 EUR"


def test_income_statement():
    ledger = Ledger(chart())
    ledger.post(sale("S-1", date(2024, 1, 5), "100.00"))
    ledger.post(Entry("P-1", date(2024, 1, 7), [Line("6000", debit=Money("40", "EUR")),
                                                Line("1000", credit=Money("40", "EUR"))]))
    ledger.post(sale("S-2", date(2024, 2, 1), "70.00"))
    ist = reports.income_statement(ledger, date(2024, 1, 1), date(2024, 1, 31))
    assert ist["net_income"] == Money("60.00", "EUR")


def test_bank_import():
    assert importers.parse_amount("1,234.56", "EUR") == Decimal("1234.56")
    assert importers.parse_amount("(12.00)", "EUR") == Decimal("-12.00")
    assert importers.parse_amount("-7.5", "EUR") == Decimal("-7.5")
    with pytest.raises(ValueError):
        importers.parse_amount("12.345", "EUR")
    text = 'date,description,amount\n2024-01-03,Invoice 7,"1,200.00"\n01/04/2024,Fee,(2.50)\n'
    entries = importers.parse_bank_csv(text, bank_account="1000", contra_account="4000",
                                       currency="EUR")
    assert [e.on for e in entries] == [date(2024, 1, 3), date(2024, 1, 4)]
    ledger = Ledger(chart())
    for e in entries:
        ledger.post(e)
    assert ledger.balance("1000") == {"EUR": Money("1197.50", "EUR")}


def test_store_round_trip(tmp_path):
    rates = RateTable()
    rates.add(date(2024, 1, 1), "EUR", "USD", Decimal("1.1"))
    ledger = Ledger(chart(), rates, PeriodCalendar())
    ledger.post(sale("S-1", date(2024, 1, 5), "10.10"))
    ledger.calendar.close(2024, 1)
    store.save(ledger, tmp_path / "l.json")
    back = store.load(tmp_path / "l.json")
    assert back.balance("1000") == ledger.balance("1000")
    assert back.calendar.closed == [(2024, 1)]
    assert back.rates.rate_on(date(2024, 3, 1), "EUR", "USD") == Decimal("1.1")


def test_invoice_split_two_ways():
    entry = billing.split_invoice("INV-1", date(2024, 1, 5), Money("100.00", "EUR"), "1000",
                                  {"4000": 1, "4100": 1})
    ledger = Ledger(chart())
    ledger.post(entry)
    assert ledger.balance("4100") == {"EUR": Money("-50.00", "EUR")}
