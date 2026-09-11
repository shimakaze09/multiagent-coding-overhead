"""Errors raised while resolving settings."""


class SettingsError(ValueError):
    """A setting is missing or invalid.

    The message always starts with the setting's key, e.g.
    "db.port: not an integer: 'abc'", so operators can grep logs for it.
    """
