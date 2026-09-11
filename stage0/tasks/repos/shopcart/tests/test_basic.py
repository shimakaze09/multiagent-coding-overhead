from decimal import Decimal

import pytest

from shopcart import Cart, Invoice, Money
from shopcart import currency


def test_add_and_item_count():
    cart = Cart("USD").add("A", Money.of("1.00", "USD"), qty=2).add("B", Money.of("0.50", "USD"))
    assert cart.item_count() == 3


def test_total_without_discount():
    cart = Cart("USD").add("A", Money.of("2.50", "USD"), qty=2)
    assert cart.total() == Money(500, "USD")
    assert cart.total_major() == Decimal("5.00")


def test_single_unit_discount():
    cart = Cart("USD").add("A", Money.of("2.00", "USD"), qty=1, discount_percent=25)
    assert cart.total() == Money(150, "USD")


def test_cart_rejects_currency_mismatch():
    with pytest.raises(ValueError):
        Cart("USD").add("A", Money.of("1", "EUR"))


def test_money_arithmetic():
    assert Money.of("1.10", "USD") + Money.of("2.20", "USD") == Money.of("3.30", "USD")
    assert Money.of("1.25", "USD").times(3) == Money(375, "USD")


def test_minor_units():
    assert currency.minor_units("USD") == 2
    assert currency.minor_units("JPY") == 0


def test_invoice_render():
    invoice = Invoice("USD").add_line("A", Money.of("1.00", "USD"), 2)
    assert invoice.render() == "INVOICE (USD)\nA  x2  @ 1.00  = 2.00\nTOTAL 2.00"
