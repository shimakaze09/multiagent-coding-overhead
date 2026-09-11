"""Unit conversion for parcel weights and dimensions.

All internal calculations use grams and centimetres. These helpers are shared
with the label printer and the customs-declaration export.
"""

GRAMS_PER = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.349523125,
    "lb": 453.59237,
}

CM_PER = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "in": 0.3937,
}


def to_grams(value, unit):
    """Convert a weight to grams."""
    try:
        return value * GRAMS_PER[unit]
    except KeyError:
        raise ValueError(f"unknown weight unit: {unit!r}") from None


def to_cm(value, unit):
    """Convert a length to centimetres."""
    try:
        return value * CM_PER[unit]
    except KeyError:
        raise ValueError(f"unknown length unit: {unit!r}") from None
