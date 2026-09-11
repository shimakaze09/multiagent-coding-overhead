"""Shipping quotes shown at checkout."""

from decimal import Decimal

from . import rates, zones


def quote(parcel, zone=1):
    """Price, in dollars, to ship `parcel` to `zone`."""
    cents = rates.band_price_cents(parcel.billable_grams())
    cents = zones.apply_zone(cents, zone)
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))
