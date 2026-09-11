from decimal import Decimal

import pytest

from shipkit import Parcel, dimension_label, quote
from shipkit import rates, units


def test_small_parcel():
    p = Parcel(weight=300, weight_unit="g", length=20, width=15, height=5)
    assert quote(p) == Decimal("5.00")


def test_bulky_parcel_is_billed_by_volume():
    p = Parcel(weight=1, weight_unit="kg", length=40, width=30, height=20)
    assert quote(p) == Decimal("15.00")


def test_zone_multiplier():
    p = Parcel(weight=1.5, weight_unit="kg", length=10, width=10, height=10)
    assert quote(p, zone=3) == Decimal("14.40")


def test_weight_units():
    assert units.to_grams(2, "kg") == 2000
    assert units.to_grams(16, "oz") == pytest.approx(453.59237)


def test_rate_bands():
    assert rates.band_price_cents(250) == 500
    assert rates.band_price_cents(1200) == 900


def test_label_shows_centimetres():
    assert dimension_label(20, 15, 5, "cm") == "20.0 x 15.0 x 5.0 cm"


def test_unknown_units_are_rejected():
    with pytest.raises(ValueError):
        units.to_grams(1, "stone")
