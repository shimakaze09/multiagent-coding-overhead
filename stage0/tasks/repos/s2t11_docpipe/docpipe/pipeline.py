"""parse -> transforms -> render."""

from . import parser, render_html, render_text, transforms
from .cache import cache_key
from .options import Options

FORMATS = {"html": render_html.render, "text": render_text.render}


def parse(source):
    doc = parser.parse(source)
    transforms.assign_heading_ids(doc)
    transforms.number_footnotes(doc)
    return doc


def render(source, fmt="html", options=None, *, cache=None):
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    options = options or Options()

    def produce():
        doc = parse(source)
        out = FORMATS[fmt](doc, options)
        if options.toc and fmt == "html":
            out = render_html.render_toc_html(transforms.build_toc(doc, options.toc_max_level)) + "\n" + out
        return out

    if cache is None:
        return produce()
    return cache.get_or_render(cache_key(source, fmt, options), produce)


def check(source):
    """Warnings: footnote problems and broken in-document anchors."""
    doc = parse(source)
    return doc.warnings + transforms.check_links(doc)
