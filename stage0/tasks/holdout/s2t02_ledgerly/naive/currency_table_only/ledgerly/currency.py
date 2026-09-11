"""The ISO 4217 currencies ledgerly supports."""

MINOR_UNITS = {"USD": 2, "EUR": 2, "GBP": 2, "CHF": 2, "JPY": 0, "KWD": 3}


class UnknownCurrency(ValueError):
    pass


def minor_units(code):
    try:
        return MINOR_UNITS[code]
    except KeyError:
        raise UnknownCurrency(f"unsupported currency {code!r}") from None
