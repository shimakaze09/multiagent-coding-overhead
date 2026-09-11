"""Held-out deterministic verifier for the `cart_invoice_rounding` task.

Injected into the workspace only AFTER the agent phase ends, identically for
every arm, and run on its own (`--noconftest`, not together with the visible
tests), so SOLVED is decided by these tests alone.
"""

import itertools
from decimal import Decimal

import pytest

from shopcart import Cart, Invoice, Money
from shopcart import currency, discounts


# --- the reported defect -------------------------------------------------


def test_task_example_usd():
    cart = Cart("USD").add("SKU-1", Money.of("1.05", "USD"), qty=3, discount_percent=15)
    assert cart.total() == Money(267, "USD")
    assert cart.total() == Invoice.from_cart(cart).total()


def test_task_example_jpy():
    cart = Cart("JPY").add("SKU-2", Money.of(1500, "JPY"), qty=2)
    assert cart.total_major() == Decimal("3000")


PRICES = ["0.99", "1.05", "2.49", "3.33", "10.01", "0.07"]
QUANTITIES = [1, 2, 3, 7]
PERCENTS = [0, 5, 10, 15, 33, 50]


@pytest.mark.parametrize(
    "price,qty,pct", list(itertools.product(PRICES, QUANTITIES, PERCENTS))
)
def test_cart_agrees_with_invoice_for_every_line(price, qty, pct):
    cart = Cart("USD").add("X", Money.of(price, "USD"), qty=qty, discount_percent=pct)
    assert cart.total() == Invoice.from_cart(cart).total()


def test_line_total_is_net_unit_price_times_quantity():
    cart = Cart("USD").add("X", Money.of("1.05", "USD"), qty=3, discount_percent=15)
    line = cart.lines[0]
    assert cart.line_total(line) == discounts.net_unit_price(line.unit_price, 15).times(3)


def test_multi_line_cart_agrees_with_invoice():
    cart = (
        Cart("EUR")
        .add("A", Money.of("1.05", "EUR"), qty=3, discount_percent=15)
        .add("B", Money.of("2.49", "EUR"), qty=7, discount_percent=33)
        .add("C", Money.of("10.01", "EUR"), qty=1)
    )
    assert cart.total() == Invoice.from_cart(cart).total()


@pytest.mark.parametrize(
    "code,amount,qty,expected",
    [
        ("USD", "1.05", 3, Decimal("3.15")),
        ("JPY", 1500, 2, Decimal("3000")),
        ("KRW", 12000, 1, Decimal("12000")),
        ("KWD", "1.250", 2, Decimal("2.500")),
        ("BHD", "0.105", 3, Decimal("0.315")),
    ],
)
def test_total_major_respects_currency_minor_units(code, amount, qty, expected):
    cart = Cart(code).add("X", Money.of(amount, code), qty=qty)
    assert cart.total_major() == expected
    assert cart.total_major() == Invoice.from_cart(cart).total().major()


def test_total_major_with_discount_in_a_zero_decimal_currency():
    cart = Cart("JPY").add("X", Money.of(999, "JPY"), qty=3, discount_percent=15)
    # per unit: 999 * 15% = 149.85 -> 150 off -> 849 net -> 2547
    assert cart.total() == Money(2547, "JPY")
    assert cart.total_major() == Decimal("2547")


# --- shared behaviour that must NOT change --------------------------------


def test_currency_table_unchanged():
    expected = {"USD": 2, "EUR": 2, "GBP": 2, "CAD": 2, "JPY": 0, "KRW": 0, "KWD": 3, "BHD": 3}
    assert {code: currency.minor_units(code) for code in expected} == expected
    with pytest.raises(currency.UnknownCurrency):
        currency.minor_units("XXX")


def test_currency_conversions_unchanged():
    assert currency.to_minor("1.005", "USD") == 101
    assert currency.to_minor("1.5", "JPY") == 2
    assert currency.to_major(267, "USD") == Decimal("2.67")
    assert str(currency.to_major(2500, "KWD")) == "2.500"


def test_money_unchanged():
    assert Money.of("1.05", "USD") == Money(105, "USD")
    assert str(Money(267, "USD")) == "2.67 USD"
    with pytest.raises(ValueError):
        Money(1, "USD") + Money(1, "EUR")
    with pytest.raises(ValueError):
        Money(1, "USD").times(-1)


def test_discount_contract_unchanged():
    assert discounts.unit_discount(Money(105, "USD"), 15) == Money(16, "USD")
    assert discounts.net_unit_price(Money(105, "USD"), 15) == Money(89, "USD")
    assert discounts.unit_discount(Money(99, "USD"), 50) == Money(50, "USD")
    assert discounts.unit_discount(Money(99, "USD"), 0) == Money(0, "USD")
    with pytest.raises(ValueError):
        discounts.unit_discount(Money(99, "USD"), 101)


def test_invoice_output_unchanged():
    invoice = (
        Invoice("USD")
        .add_line("A", Money.of("1.05", "USD"), 3, 15)
        .add_line("B", Money.of("2.00", "USD"), 1)
    )
    assert invoice.render() == (
        "INVOICE (USD)\nA  x3  @ 0.89  = 2.67\nB  x1  @ 2.00  = 2.00\nTOTAL 4.67"
    )


def test_cart_validation_unchanged():
    with pytest.raises(ValueError):
        Cart("USD").add("A", Money.of("1", "EUR"))
    with pytest.raises(ValueError):
        Cart("USD").add("A", Money.of("1", "USD"), qty=0)
    with pytest.raises(ValueError):
        Cart("USD").add("A", Money.of("1", "USD"), discount_percent=101)
    cart = Cart("USD").add("A", Money.of("1", "USD"), qty=2).add("B", Money.of("1", "USD"))
    assert cart.item_count() == 3
