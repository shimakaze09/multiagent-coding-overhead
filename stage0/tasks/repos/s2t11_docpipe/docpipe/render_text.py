"""Plain-text rendering (docs/extending.md).

Like the HTML renderer, a table of rule functions, one per node kind:
``rule(node, ctx) -> str``. ``register_text(kind, fn)`` adds or replaces a rule.
"""

import textwrap

from .options import Options


class Context:
    def __init__(self, options=None):
        self.options = options or Options()

    def render(self, node):
        rule = RULES.get(node.kind)
        if rule is None:
            raise ValueError(f"no text rule for node kind {node.kind!r}")
        return rule(node, self)

    def render_children(self, node):
        return "".join(self.render(c) for c in node.children)


def _document(node, ctx):
    blocks = [ctx.render(c) for c in node.children]
    if node.footnotes:
        blocks.append("\n".join(f"[{f.number}] " + "".join(ctx.render(c) for c in f.children)
                                for f in node.footnotes))
    return "\n\n".join(blocks) + "\n"


def _heading(node, ctx):
    text = ctx.render_children(node)
    return text + "\n" + ("=" if node.level == 1 else "-") * len(text)


def _paragraph(node, ctx):
    return textwrap.fill(ctx.render_children(node), width=ctx.options.wrap)


def _list(node, ctx):
    lines = []
    for n, item in enumerate(node.children, start=1):
        bullet = f"{n}." if node.ordered else "-"
        first = True
        for child in item.children:
            if child.kind == "list":
                lines.extend("  " + l for l in _list(child, ctx).split("\n"))
            else:
                text = ctx.render_children(child) if child.kind == "paragraph" else ctx.render(child)
                lines.append((f"{bullet} " if first else "  ") + text)
                first = False
    return "\n".join(lines)


def _item(node, ctx):
    return ctx.render_children(node)


def _code_block(node, ctx):
    return "\n".join("    " + l for l in node.text.split("\n"))


def _quote(node, ctx):
    inner = "\n\n".join(ctx.render(c) for c in node.children)
    return "\n".join("> " + l for l in inner.split("\n"))


def _text(node, ctx):
    return node.text


def _children(node, ctx):
    return ctx.render_children(node)


def _code(node, ctx):
    return node.text


def _link(node, ctx):
    text = ctx.render_children(node)
    return text if node.href in ("", text) else f"{text} ({node.href})"


def _footnote_ref(node, ctx):
    return f"[^{node.label}]" if node.number is None else f"[{node.number}]"


DEFAULT_RULES = {
    "document": _document, "heading": _heading, "paragraph": _paragraph, "list": _list,
    "item": _item, "code_block": _code_block, "quote": _quote, "text": _text,
    "emph": _children, "strong": _children, "code": _code, "link": _link,
    "footnote_ref": _footnote_ref,
}
RULES = dict(DEFAULT_RULES)


def register_text(kind, fn):
    """Add or replace the rule for ``kind``: ``fn(node, ctx) -> str``."""
    RULES[kind] = fn


def unregister_text(kind):
    """Restore the built-in rule for ``kind`` (or drop a rule for a new kind)."""
    if kind in DEFAULT_RULES:
        RULES[kind] = DEFAULT_RULES[kind]
    else:
        RULES.pop(kind, None)


def render(doc, options=None):
    return Context(options).render(doc)
