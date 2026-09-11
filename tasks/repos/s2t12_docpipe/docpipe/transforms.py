"""Tree transforms: heading ids, table of contents, footnote numbering and the
in-document anchor check."""

from dataclasses import dataclass

from .inline import plain_text
from .nodes import Footnote
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
    """Every inline node in document order (footnote bodies excluded)."""
    def walk(nodes):
        for n in nodes:
            if n.kind in ("heading", "paragraph", "list", "item", "quote", "document"):
                yield from walk(n.children)
            elif n.kind != "code_block":
                yield n
                yield from walk(n.children)
    yield from walk(doc.children)


def number_footnotes(doc):
    """Number notes by their first reference. References to undefined notes
    stay unnumbered; definitions never referenced are dropped."""
    numbers, seen = {}, {}
    doc.footnotes = []
    for node in inline_nodes(doc):
        if node.kind != "footnote_ref":
            continue
        if node.label not in doc.footnote_defs:
            doc.warnings.append(f"undefined footnote [^{node.label}]")
            continue
        if node.label not in numbers:
            numbers[node.label] = len(numbers) + 1
            doc.footnotes.append(Footnote(numbers[node.label], node.label,
                                          doc.footnote_defs[node.label]))
        seen[node.label] = seen.get(node.label, 0) + 1
        node.number = numbers[node.label]
        node.occurrence = seen[node.label]
    for label in doc.footnote_defs:
        if label not in numbers:
            doc.warnings.append(f"unused footnote definition [^{label}]")


def check_links(doc):
    """Warnings for in-document links whose anchor matches no heading id."""
    ids = {h.id for h in headings(doc)}
    return [f"broken anchor {n.href}" for n in inline_nodes(doc)
            if n.kind == "link" and n.href.startswith("#") and n.href[1:] not in ids]
