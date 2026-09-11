"""Held-out verifier: allocation keeps every minor unit, by the documented rule."""

from datetime import date
from decimal import Decimal

import pytest

from ledgerly import Account, Chart, Ledger, Money, billing


def M(v, c="EUR"):
    return Money(v, c)


def test_c1_parts_always_sum_to_the_rounded_total():
    for cents in range(1, 400, 7):
        total = Money(Decimal(cents) / 100, "EUR")
        for ratios in ([1, 1, 1], [1, 2, 3, 4], [3, 7], [1] * 7, [5, 0, 2]):
            parts = total.allocate(ratios)
            assert len(parts) == len(ratios)
            assert sum(parts, Money.zero("EUR")) == total.rounded(), (cents, ratios)


def test_c2_leftover_units_go_to_largest_remainders_then_earlier_parts():
    assert M("1.00").allocate([1] * 7) == [M(v) for v in ("0.15", "0.15", "0.14", "0.14", "0.14",
                                                           "0.14", "0.14")]
    assert M("100.00").allocate([1, 1, 1]) == [M("33.34"), M("33.33"), M("33.33")]
    assert M("0.05").allocate([1, 1, 1]) == [M("0.02"), M("0.02"), M("0.01")]
    assert M("10.00").allocate([1, 1, 4]) == [M("1.67"), M("1.67"), M("6.66")]


def test_c3_negative_amounts_mirror_positive_ones():
    assert M("-100.00").allocate([1, 1, 1]) == [M("-33.34"), M("-33.33"), M("-33.33")]
    assert M("-1.00").allocate([1] * 7) == [-p for p in M("1.00").allocate([1] * 7)]


def test_c4_zero_ratios_receive_nothing():
    assert M("0.01").allocate([0, 1, 1]) == [M("0.00"), M("0.01"), M("0.00")]
    assert M("5.00").allocate([0, 1, 0, 1]) == [M("0"), M("2.50"), M("0"), M("2.50")]


def test_c5_other_minor_units():
    assert Money("1000", "JPY").allocate([1, 1, 1]) == [Money(v, "JPY") for v in ("334", "333", "333")]
    assert Money("1.000", "KWD").allocate([1, 1, 1]) == [
        Money(v, "KWD") for v in ("0.334", "0.333", "0.333")]


def test_c6_revenue_splits_always_post():
    chart = Chart([Account("1100", "Receivables", "asset")] +
                  [Account(c, f"Income {c}", "income") for c in ("4000", "4100", "4200")])
    ledger = Ledger(chart)
    for n, total in enumerate(("100.00", "0.10", "999.99", "-30.01", "1.00")):
        ledger.post(billing.split_invoice(f"INV-{n}", date(2024, 4, 1), M(total), "1100",
                                          {"4000": 1, "4100": 1, "4200": 1}))
    assert ledger.balance("4000") == {"EUR": M("-357.04")}


def test_r_rounding_policy_is_unchanged():
    assert M("0.125").rounded() == M("0.12")
    assert M("0.135").rounded() == M("0.14")
    assert M("10.005").allocate([1, 1]) == [M("5.00"), M("5.00")]
    with pytest.raises(ValueError):
        M("1").allocate([1, -1])
