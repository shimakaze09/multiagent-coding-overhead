"""Held-out deterministic verifier for the `shipping_inch_dimensions` task.

Injected into the workspace only AFTER the agent phase ends, identically for
every arm, and run on its own (`--noconftest`, not together with the visible
tests), so SOLVED is decided by these tests alone.
"""

from decimal import Decimal

import pytest

from shipkit import Parcel, dimension_label, quote
from shipkit import parcel as parcel_mod
from shipkit import rates, units, zones


# --- the reported defect -------------------------------------------------


def test_task_example():
    box = Parcel(weight=1, weight_unit="lb", length=10, width=8, height=4, dim_unit="in")
    assert quote(box) == Decimal("9.00")


def test_the_same_box_in_every_length_unit_agrees():
    inches = Parcel(1, "lb", 10, 8, 4, "in")
    cm = Parcel(1, "lb", 25.4, 20.32, 10.16, "cm")
    mm = Parcel(1, "lb", 254, 203.2, 101.6, "mm")
    assert quote(inches) == quote(cm) == quote(mm) == Decimal("9.00")
    assert inches.volumetric_grams() == pytest.approx(cm.volumetric_grams(), rel=1e-4)


@pytest.mark.parametrize("value,expected", [(1, 2.54), (12, 30.48), (0.5, 1.27)])
def test_inches_to_centimetres(value, expected):
    assert units.to_cm(value, "in") == pytest.approx(expected, rel=1e-4)


def test_large_parcel_in_inches_to_zone_2():
    p = Parcel(2, "lb", 20, 16, 12, "in")
    assert quote(p, zone=2) == Decimal("48.75")


def test_label_for_inch_dimensions():
    assert dimension_label(10, 8, 4, "in") == "25.4 x 20.3 x 10.2 cm"


# --- shared behaviour that must NOT change --------------------------------


def test_metric_length_units_unchanged():
    assert units.CM_PER["mm"] == 0.1
    assert units.CM_PER["cm"] == 1.0
    assert units.CM_PER["m"] == 100.0
    assert units.to_cm(35, "mm") == pytest.approx(3.5)


def test_weight_units_unchanged():
    assert units.GRAMS_PER == {"g": 1.0, "kg": 1000.0, "oz": 28.349523125, "lb": 453.59237}


def test_rate_card_unchanged():
    assert rates.BANDS == [(500, 500), (2000, 900), (5000, 1500)]
    assert rates.EXTRA_PER_KG_CENTS == 300
    assert [rates.band_price_cents(g) for g in (500, 501, 2000, 5000, 5001, 7000.5)] == [
        500, 900, 900, 1500, 1800, 2400]


def test_zones_unchanged():
    assert zones.ZONE_PERCENT == {1: 100, 2: 125, 3: 160, 4: 220}
    assert zones.apply_zone(900, 2) == 1125
    assert zones.apply_zone(999, 3) == 1598


def test_volumetric_rule_unchanged():
    assert parcel_mod.VOLUMETRIC_DIVISOR == 5000
    p = Parcel(100, "g", 10, 10, 10)
    assert p.volumetric_grams() == pytest.approx(200)
    assert p.billable_grams() == pytest.approx(200)
    assert Parcel(3, "kg", 10, 10, 10).billable_grams() == pytest.approx(3000)


def test_invalid_input_is_rejected():
    with pytest.raises(ValueError):
        units.to_cm(1, "ft")
    with pytest.raises(ValueError):
        zones.apply_zone(100, 9)
    with pytest.raises(ValueError):
        rates.band_price_cents(0)
