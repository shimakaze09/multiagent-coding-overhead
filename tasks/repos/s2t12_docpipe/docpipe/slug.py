"""Heading ids (docs/syntax.md)."""

import re
import unicodedata


def slugify(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text or "section"


class Slugger:
    """Unique ids in document order: the second "Setup" becomes "setup-1"."""

    def __init__(self):
        self._used = set()

    def slug(self, text):
        base = slugify(text)
        candidate, n = base, 0
        while candidate in self._used:
            n += 1
            candidate = f"{base}-{n}"
        self._used.add(candidate)
        return candidate
