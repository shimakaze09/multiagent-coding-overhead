"""Tree transforms: heading ids, table of contents and the in-document anchor
check."""

from dataclasses import dataclass

from .inline import plain_text
from .slug import Slugger


@dataclass(frozen=True)
class TocEntry:
    level: int
    text: str
    id: str


def headings(doc):
    return [n for n in doc.children if n.kind == "heading"]


def assign_heading_ids(doc):
    """Give every heading its id, once, in document order. The table of
    contents, both renderers and the anchor check all use these ids."""
    slugger = Slugger()
    for h in headings(doc):
        h.id = slugger.slug(plain_text(h.children))


def build_toc(doc, max_level=3):
    return [TocEntry(h.level, plain_text(h.children), h.id) for h in headings(doc)
            if h.level <= max_level]


def inline_nodes(doc):
    """Every inline node in document order."""
    def walk(nodes):
        for n in nodes:
            if n.kind in ("heading", "paragraph", "list", "item", "quote", "document"):
                yield from walk(n.children)
            elif n.kind != "code_block":
                yield n
                yield from walk(n.children)
    yield from walk(doc.children)


def check_links(doc):
    """Warnings for in-document links whose anchor matches no heading id."""
    ids = {h.id for h in headings(doc)}
    return [f"broken anchor {n.href}" for n in inline_nodes(doc)
            if n.kind == "link" and n.href.startswith("#") and n.href[1:] not in ids]
