"""Rendering options."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Options:
    heading_offset: int = 0     # added to every heading level (capped at 6); ids are unaffected
    toc: bool = False           # prepend a table of contents (HTML)
    toc_max_level: int = 3
    safe_links: bool = True     # render only safe links as links (docs/security.md)
    wrap: int = 72              # text renderer line width
