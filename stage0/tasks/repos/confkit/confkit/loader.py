"""Resolve settings from an ordered list of sources (the first source wins)."""

from . import coerce
from .errors import SettingsError


def load(schema, sources):
    values = {}
    for field in schema.fields:
        raw = _first(sources, field.name)
        if raw is None:
            if field.has_default():
                values[field.name] = field.default
            elif field.required:
                raise SettingsError(f"{field.name}: required setting is missing")
            else:
                values[field.name] = None
            continue
        try:
            values[field.name] = coerce.convert(field, raw)
        except ValueError as exc:
            raise SettingsError(f"{field.name}: {exc}") from None
    return values


def _first(sources, key):
    for source in sources:
        raw = source.get(key)
        if raw is not None:
            return raw
    return None
