"""Where raw setting values come from.

A source maps a dotted key such as "db.port" to a raw STRING value, or to None
when it does not define the key. Sources never convert, split or trim values:
all conversion happens in one place (coerce.py), so every source behaves the
same way.
"""

import os


class DictSource:
    """Settings from a mapping, e.g. a parsed configuration file."""

    def __init__(self, values):
        self._values = {k: str(v) for k, v in values.items()}

    def get(self, key):
        return self._values.get(key)


class EnvSource:
    """Environment variables: "db.port" is read from APP_DB_PORT."""

    def __init__(self, environ=None, prefix="APP_"):
        self._environ = os.environ if environ is None else environ
        self._prefix = prefix

    def name_for(self, key):
        return self._prefix + key.upper().replace(".", "_")

    def get(self, key):
        return self._environ.get(self.name_for(key))
