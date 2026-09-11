"""Text normalization helpers.

`normalize` is the shared entry point used by both the report renderer and the
palindrome helpers. It collapses whitespace and lowercases, and it deliberately
preserves punctuation because report headings need it.
"""

import re

_WS = re.compile(r"\s+")


def normalize(text):
    """Collapse whitespace and lowercase. Punctuation is preserved."""
    if text is None:
        return ""
    return _WS.sub(" ", str(text)).strip().lower()


def slugify(text):
    """Turn arbitrary text into a url-safe slug."""
    base = normalize(text)
    base = re.sub(r"[^a-z0-9]+", "-", base)
    return base.strip("-")
