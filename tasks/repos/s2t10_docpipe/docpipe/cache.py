"""A render cache. A key covers everything that can change the output: the
source, the format, every option, the renderer class and the deprecated
plugin rules in effect."""

import dataclasses
import hashlib
import json

from . import render_html, render_text


def cache_key(source, fmt, options, renderer):
    legacy = render_html.legacy_version() if fmt == "html" else render_text.legacy_version()
    blob = json.dumps([source, fmt, dataclasses.asdict(options),
                       f"{renderer.__module__}.{renderer.__qualname__}", legacy], sort_keys=True)
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
