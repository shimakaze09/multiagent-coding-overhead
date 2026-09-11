"""parse -> transforms -> render."""

from . import parser, transforms
from .cache import cache_key
from .options import Options
from .render_html import HtmlRenderer, render_toc_html
from .render_text import TextRenderer

FORMATS = {"html": HtmlRenderer, "text": TextRenderer}


def parse(source):
    doc = parser.parse(source)
    transforms.assign_heading_ids(doc)
    return doc


def render(source, fmt="html", options=None, *, cache=None, renderer=None):
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    options = options or Options()
    cls = renderer or FORMATS[fmt]

    def produce():
        doc = parse(source)
        out = cls(options).render(doc)
        if options.toc and fmt == "html":
            out = render_toc_html(transforms.build_toc(doc, options.toc_max_level)) + "\n" + out
        return out

    if cache is None:
        return produce()
    return cache.get_or_render(cache_key(source, fmt, options, cls), produce)


def check(source):
    """Warnings: broken in-document anchors."""
    doc = parse(source)
    return doc.warnings + transforms.check_links(doc)
