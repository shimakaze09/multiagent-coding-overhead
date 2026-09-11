"""HTML rendering (docs/extending.md, docs/security.md).

Rendering is a table of rule functions, one per node kind:
``rule(node, ctx) -> str``, where ``ctx.render(node)``,
``ctx.render_children(node)`` and ``ctx.options`` are available.
``register_html(kind, fn)`` adds or replaces a rule.
"""

import html
import re

from .options import Options

SAFE_SCHEMES = ("http", "https", "mailto")
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")


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


class Context:
    def __init__(self, options=None):
        self.options = options or Options()

    def render(self, node):
        rule = RULES.get(node.kind)
        if rule is None:
            raise ValueError(f"no HTML rule for node kind {node.kind!r}")
        return rule(node, self)

    def render_children(self, node):
        return "".join(self.render(c) for c in node.children)


# -- blocks -------------------------------------------------------------


def _document(node, ctx):
    body = "\n".join(ctx.render(c) for c in node.children)
    notes = _footnotes(node, ctx)
    return body + ("\n" + notes if notes else "")


def _heading(node, ctx):
    level = min(6, max(1, node.level + ctx.options.heading_offset))
    return f'<h{level} id="{escape(node.id)}">{ctx.render_children(node)}</h{level}>'


def _paragraph(node, ctx):
    return f"<p>{ctx.render_children(node)}</p>"


def _list(node, ctx):
    tag = "ol" if node.ordered else "ul"
    return f"<{tag}>" + "".join(ctx.render(i) for i in node.children) + f"</{tag}>"


def _item(node, ctx):
    parts = [ctx.render_children(c) if c.kind == "paragraph" else ctx.render(c)
             for c in node.children]
    return "<li>" + "".join(parts) + "</li>"


def _code_block(node, ctx):
    cls = f' class="language-{escape(node.lang)}"' if node.lang else ""
    return f"<pre><code{cls}>{escape(node.text)}</code></pre>"


def _quote(node, ctx):
    return "<blockquote>" + "".join(ctx.render(c) for c in node.children) + "</blockquote>"


# -- inline -------------------------------------------------------------


def _text(node, ctx):
    return escape(node.text)


def _emph(node, ctx):
    return f"<em>{ctx.render_children(node)}</em>"


def _strong(node, ctx):
    return f"<strong>{ctx.render_children(node)}</strong>"


def _code(node, ctx):
    return f"<code>{escape(node.text)}</code>"


def _link(node, ctx):
    href = safe_href(node.href) if ctx.options.safe_links else node.href
    if href is None:
        return ctx.render_children(node)
    return f'<a href="{escape(href)}">{ctx.render_children(node)}</a>'


def _footnote_ref(node, ctx):
    if node.number is None:
        return escape(f"[^{node.label}]")
    n = node.number
    ref_id = f"fnref-{n}" if node.occurrence == 1 else f"fnref-{n}-{node.occurrence}"
    return f'<sup class="footnote-ref"><a href="#fn-{n}" id="{ref_id}">{n}</a></sup>'


def _footnotes(doc, ctx):
    if not doc.footnotes:
        return ""
    items = "".join(
        f'<li id="fn-{f.number}">{"".join(ctx.render(c) for c in f.children)} '
        f'<a href="#fnref-{f.number}" class="footnote-back">↩</a></li>'
        for f in doc.footnotes)
    return f'<section class="footnotes"><ol>{items}</ol></section>'


DEFAULT_RULES = {
    "document": _document, "heading": _heading, "paragraph": _paragraph, "list": _list,
    "item": _item, "code_block": _code_block, "quote": _quote, "text": _text, "emph": _emph,
    "strong": _strong, "code": _code, "link": _link, "footnote_ref": _footnote_ref,
}
RULES = dict(DEFAULT_RULES)


def register_html(kind, fn):
    """Add or replace the rule for ``kind``: ``fn(node, ctx) -> str``."""
    RULES[kind] = fn


def unregister_html(kind):
    """Restore the built-in rule for ``kind`` (or drop a rule for a new kind)."""
    if kind in DEFAULT_RULES:
        RULES[kind] = DEFAULT_RULES[kind]
    else:
        RULES.pop(kind, None)


def render(doc, options=None):
    return Context(options).render(doc)


def render_toc_html(entries):
    items = "".join(f'<li class="toc-h{e.level}"><a href="#{escape(e.id)}">{escape(e.text)}</a></li>'
                    for e in entries)
    return f'<nav class="toc"><ul>{items}</ul></nav>'
