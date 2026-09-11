"""HTML rendering (docs/extending.md, docs/security.md).

``HtmlRenderer`` has one ``visit_<kind>(node)`` method per node kind. Subclass
it to change how a kind renders and pass the subclass to
``pipeline.render(..., renderer=...)``.
"""

import html
import re
import warnings

from .options import Options

SAFE_SCHEMES = ("http", "https", "mailto")
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_LEGACY_RULES = {}
_legacy_version = [0]


def escape(text):
    return html.escape(text, quote=True)


def safe_href(href):
    """``href`` if it may be rendered as a link, else None. Whitespace and
    control characters anywhere are ignored when finding the scheme, and
    schemes are case-insensitive. Links without a scheme are relative."""
    cleaned = "".join(ch for ch in href if ch > " " and ch != "\x7f")
    m = _SCHEME.match(cleaned)
    if m is None or m.group(1).lower() in SAFE_SCHEMES:
        return href.strip()
    return None


def register_html(kind, fn):
    """Deprecated docpipe 1.x plugin API: ``fn(node, ctx) -> str`` where ``ctx``
    offers ``render_children(node)`` and ``options``."""
    warnings.warn("register_html() is deprecated; subclass HtmlRenderer instead",
                  DeprecationWarning, stacklevel=2)
    _LEGACY_RULES[kind] = fn
    _legacy_version[0] += 1


def unregister_html(kind):
    if _LEGACY_RULES.pop(kind, None) is not None:
        _legacy_version[0] += 1


def legacy_version():
    return _legacy_version[0]


class HtmlRenderer:
    def __init__(self, options=None):
        self.options = options or Options()

    def render(self, doc):
        return self.render_node(doc)

    def render_node(self, node):
        name = f"visit_{node.kind}"
        own = getattr(type(self), name, None)
        overridden = own is not None and own is not getattr(HtmlRenderer, name, None)
        legacy = _LEGACY_RULES.get(node.kind)
        if legacy is not None and not overridden:
            return legacy(node, self)
        if own is None:
            raise ValueError(f"no HTML rule for node kind {node.kind!r}")
        return getattr(self, name)(node)

    def render_children(self, node):
        return "".join(self.render_node(c) for c in node.children)

    # -- blocks ---------------------------------------------------------

    def visit_document(self, node):
        return "\n".join(self.render_node(c) for c in node.children)

    def visit_heading(self, node):
        level = min(6, max(1, node.level + self.options.heading_offset))
        return f'<h{level} id="{escape(node.id)}">{self.render_children(node)}</h{level}>'

    def visit_paragraph(self, node):
        return f"<p>{self.render_children(node)}</p>"

    def visit_list(self, node):
        tag = "ol" if node.ordered else "ul"
        return f"<{tag}>" + "".join(self.render_node(i) for i in node.children) + f"</{tag}>"

    def visit_item(self, node):
        parts = [self.render_children(c) if c.kind == "paragraph" else self.render_node(c)
                 for c in node.children]
        return "<li>" + "".join(parts) + "</li>"

    def visit_code_block(self, node):
        cls = f' class="language-{escape(node.lang)}"' if node.lang else ""
        return f"<pre><code{cls}>{escape(node.text)}</code></pre>"

    def visit_quote(self, node):
        return "<blockquote>" + "".join(self.render_node(c) for c in node.children) + "</blockquote>"

    # -- inline ---------------------------------------------------------

    def visit_text(self, node):
        return escape(node.text)

    def visit_emph(self, node):
        return f"<em>{self.render_children(node)}</em>"

    def visit_strong(self, node):
        return f"<strong>{self.render_children(node)}</strong>"

    def visit_code(self, node):
        return f"<code>{escape(node.text)}</code>"

    def visit_link(self, node):
        href = safe_href(node.href) if self.options.safe_links else node.href
        if href is None:
            return self.render_children(node)
        return f'<a href="{escape(href)}">{self.render_children(node)}</a>'

def render_toc_html(entries):
    items = "".join(f'<li class="toc-h{e.level}"><a href="#{escape(e.id)}">{escape(e.text)}</a></li>'
                    for e in entries)
    return f'<nav class="toc"><ul>{items}</ul></nav>'
