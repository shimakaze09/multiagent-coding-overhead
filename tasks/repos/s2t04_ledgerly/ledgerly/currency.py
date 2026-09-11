"""ISO 4217 minor units for the currencies ledgerly supports."""

from decimal import Decimal

MINOR_UNITS = {"USD": 2, "EUR": 2, "GBP": 2, "CHF": 2, "JPY": 0, "KWD": 3}


class UnknownCurrency(ValueError):
    pass


def minor_units(code):
    try:
        return MINOR_UNITS[code]
    except KeyError:
        raise UnknownCurrency(f"unsupported currency {code!r}") from None


def quantum(code):
    """The smallest representable amount, e.g. Decimal('0.01') for USD."""
    return Decimal(1).scaleb(-minor_units(code))
