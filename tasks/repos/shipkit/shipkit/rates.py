"""Carrier rate card (zone 1), in cents.

Bands include their upper bound: a parcel of exactly 500 g is in the first
band. Above the last band, every started kilogram adds EXTRA_PER_KG_CENTS.
The card is published by the carrier; do not edit it by hand.
"""

import math

BANDS = [
    (500, 500),    # up to 500 g: 5.00
    (2000, 900),   # up to 2 kg:  9.00
    (5000, 1500),  # up to 5 kg: 15.00
]
EXTRA_PER_KG_CENTS = 300


def band_price_cents(grams):
    if grams <= 0:
        raise ValueError("billable weight must be positive")
    for upper, cents in BANDS:
        if grams <= upper:
            return cents
    extra_kg = math.ceil((grams - BANDS[-1][0]) / 1000)
    return BANDS[-1][1] + extra_kg * EXTRA_PER_KG_CENTS
