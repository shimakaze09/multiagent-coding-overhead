"""Currency minor units (ISO 4217).

Every conversion between major and minor units must use this table: most
currencies have two decimal places, but not all of them do.
"""

from decimal import Decimal, ROUND_HALF_UP

_MINOR_UNITS = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "BHD": 3,
}


class UnknownCurrency(ValueError):
    pass


def minor_units(code):
    """Number of decimal places the currency uses."""
    try:
        return _MINOR_UNITS[code.upper()]
    except KeyError:
        raise UnknownCurrency(code) from None


def to_minor(amount, code):
    """Major-unit amount (str, int or Decimal) -> integer minor units, half up."""
    scaled = Decimal(str(amount)).scaleb(minor_units(code))
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_major(amount_minor, code):
    """Integer minor units -> exact Decimal in major units."""
    return Decimal(int(amount_minor)).scaleb(-minor_units(code))
