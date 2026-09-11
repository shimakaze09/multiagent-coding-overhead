"""A render cache, keyed by the source, the format and every option."""

import dataclasses
import hashlib
import json


def cache_key(source, fmt, options):
    blob = json.dumps([source, fmt, dataclasses.asdict(options)], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class RenderCache:
    def __init__(self):
        self._store = {}
        self.hits = 0
        self.misses = 0

    def get_or_render(self, key, produce):
        if key in self._store:
            self.hits += 1
            return self._store[key]
        self.misses += 1
        value = self._store[key] = produce()
        return value

    def clear(self):
        self._store.clear()
