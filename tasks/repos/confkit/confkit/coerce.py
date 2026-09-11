"""Conversion of raw string values to Python values.

Every converter takes the raw string and returns the converted value, or raises
ValueError with a short reason. The loader adds the key name to the message.
"""

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def to_bool(raw):
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def to_int(raw):
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(f"not an integer: {raw!r}") from None


def to_str(raw):
    return raw.strip()


CONVERTERS = {bool: to_bool, int: to_int, str: to_str}


def convert(field, raw):
    try:
        converter = CONVERTERS[field.type]
    except KeyError:
        raise ValueError(f"unsupported type {field.type!r}") from None
    return converter(raw)
