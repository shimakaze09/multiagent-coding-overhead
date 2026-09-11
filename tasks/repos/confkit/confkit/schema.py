"""Declared settings."""

from dataclasses import dataclass
from typing import Any

SUPPORTED_TYPES = (str, int, bool)

_MISSING = object()


@dataclass(frozen=True)
class Field:
    name: str
    type: type = str
    default: Any = _MISSING
    required: bool = False

    def has_default(self):
        return self.default is not _MISSING


class Schema:
    def __init__(self, *fields):
        names = [f.name for f in fields]
        if len(set(names)) != len(names):
            raise ValueError("duplicate setting names")
        for f in fields:
            if f.type not in SUPPORTED_TYPES:
                raise ValueError(f"{f.name}: unsupported type {f.type!r}")
        self.fields = fields
