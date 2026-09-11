"""Held-out verifier: signed-amount journal lines with a compatible legacy API."""

import json
import warnings
from datetime import date
from decimal import Decimal

import pytest

from ledgerly import (Account, Chart, Entry, Ledger, Line, Money, RateTable, billing, importers,
                      reports, store)


def _chart():
    return Chart([Account("1000", "Bank EUR", "asset"), Account("4000", "Sales", "income"),
                  Account("4100", "Services", "income"), Account("6000", "Rent", "expense")])


def test_c1_signed_amount_api():
    d = Line("1000", Money("12.50", "EUR"))
    c = Line("4000", Money("-12.50", "EUR"), "memo")
    assert (d.side, d.debit, d.credit) == ("debit", Money("12.50", "EUR"), None)
    assert (c.side, c.debit, c.credit) == ("credit", None, Money("12.50", "EUR"))
    assert c.amount == Money("-12.50", "EUR") and c.memo == "memo" and c.currency == "EUR"
    with pytest.raises(ValueError):
        Line("1000", Money("0", "EUR"))
    with pytest.raises(TypeError):
        Line("1000", Decimal("1"))


def test_c2_legacy_keywords_still_work_and_warn():
    with pytest.warns(DeprecationWarning):
        old_debit = Line("1000", debit=Money("5", "EUR"))
    with pytest.warns(DeprecationWarning):
        old_credit = Line("4000", credit=Money("5", "EUR"))
    assert old_debit == Line("1000", Money("5", "EUR"))
    assert old_credit == Line("4000", Money("-5", "EUR"))
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError):
        Line("1000", debit=Money("5", "EUR"), credit=Money("5", "EUR"))
    with pytest.warns(DeprecationWarning), pytest.raises(ValueError):
        Line("1000", debit=Money("-5", "EUR"))


def test_c3_library_code_never_uses_the_deprecated_form(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        entries = importers.parse_bank_csv(
            "date,description,amount\n2024-01-03,In,100.00\n2024-01-04,Out,(40.00)\n",
            bank_account="1000", contra_account="4000", currency="EUR")
        ledger = Ledger(_chart())
        for e in entries:
            ledger.post(e)
        ledger.post(billing.split_invoice("INV-1", date(2024, 1, 5), Money("90", "EUR"), "1000",
                                          {"4000": 1, "4100": 2}))
        store.save(ledger, tmp_path / "l.json")
        back = store.load(tmp_path / "l.json")
        reports.trial_balance(back)
    assert back.balance("1000") == {"EUR": Money("150.00", "EUR")}


def test_c4_new_files_store_signed_amounts(tmp_path):
    assert store.SCHEMA == 3 and 2 in store.READABLE_SCHEMAS and 3 in store.READABLE_SCHEMAS
    ledger = Ledger(_chart())
    m = Money("7.25", "EUR")
    ledger.post(Entry("E-1", date(2024, 2, 1), [Line("6000", m), Line("1000", -m)], "rent"))
    data = store.to_dict(ledger)
    assert data["schema"] == 3
    assert data["entries"][0]["lines"] == [
        {"account": "6000", "amount": "7.25", "currency": "EUR", "memo": ""},
        {"account": "1000", "amount": "-7.25", "currency": "EUR", "memo": ""}]


def test_c4_files_from_the_current_release_still_load(tmp_path):
    legacy = {
        "schema": 2, "reporting_currency": "EUR",
        "accounts": [{"code": "1000", "name": "Bank EUR", "type": "asset"},
                     {"code": "4000", "name": "Sales", "type": "income"}],
        "closed_periods": [[2024, 1]],
        "rates": [{"date": "2024-01-01", "base": "EUR", "quote": "USD", "rate": "1.1"}],
        "entries": [{"id": "S-1", "date": "2024-01-05", "description": "sale", "lines": [
            {"account": "1000", "debit": "10.10", "credit": None, "currency": "EUR", "memo": ""},
            {"account": "4000", "debit": None, "credit": "10.10", "currency": "EUR", "memo": ""}]}],
    }
    (tmp_path / "old.json").write_text(json.dumps(legacy), encoding="utf-8")
    ledger = store.load(tmp_path / "old.json")
    assert ledger.balance("4000") == {"EUR": Money("-10.10", "EUR")}
    assert ledger.calendar.closed == [(2024, 1)]
    store.save(ledger, tmp_path / "new.json")
    again = store.load(tmp_path / "new.json")
    assert json.loads((tmp_path / "new.json").read_text(encoding="utf-8"))["schema"] == 3
    assert again.balance("1000") == {"EUR": Money("10.10", "EUR")}


def test_c5_entries_and_ledger_use_signed_totals():
    e = Entry("E-1", date(2024, 1, 2), [Line("1000", Money("10", "EUR")), Line("4000", Money("-4", "EUR")),
                                        Line("4100", Money("-6", "EUR"))])
    assert e.net() == {"EUR": Money("0", "EUR")}
    e.validate()
    rates = RateTable()
    rates.add(date(2024, 1, 1), "EUR", "USD", Decimal("1.25"))
    ledger = Ledger(_chart(), rates, reporting_currency="USD")
    ledger.post(e)
    assert ledger.balance("4100") == {"EUR": Money("-6", "EUR")}
    assert ledger.balance_in("4000") == Money("-5.00", "USD")
    ist = reports.income_statement(ledger, date(2024, 1, 1), date(2024, 1, 31))
    assert ist["net_income"] == Money("12.50", "USD")


def test_r_reports_unchanged():
    ledger = Ledger(_chart())
    for eid, amount in (("S-1", "100.00"), ("S-2", "20.00")):
        m = Money(amount, "EUR")
        ledger.post(Entry(eid, date(2024, 1, 5), [Line("1000", m), Line("4000", -m)]))
    tb = reports.trial_balance(ledger)
    assert [(r["code"], r["debit"], r["credit"]) for r in tb["rows"]] == [
        ("1000", Money("120.00", "EUR"), Money("0", "EUR")),
        ("4000", Money("0", "EUR"), Money("120.00", "EUR"))]
