"""Plain-text rendering (docs/extending.md).

``TextRenderer`` mirrors ``HtmlRenderer``: one ``visit_<kind>(node)`` method
per node kind, returning text.
"""

import textwrap
import warnings

from .options import Options

_LEGACY_RULES = {}
_legacy_version = [0]


def register_text(kind, fn):
    """Deprecated docpipe 1.x plugin API, like ``register_html``."""
    warnings.warn("register_text() is deprecated; subclass TextRenderer instead",
                  DeprecationWarning, stacklevel=2)
    _LEGACY_RULES[kind] = fn
    _legacy_version[0] += 1


def unregister_text(kind):
    if _LEGACY_RULES.pop(kind, None) is not None:
        _legacy_version[0] += 1


def legacy_version():
    return _legacy_version[0]


class TextRenderer:
    def __init__(self, options=None):
        self.options = options or Options()

    def render(self, doc):
        return self.render_node(doc)

    def render_node(self, node):
        name = f"visit_{node.kind}"
        own = getattr(type(self), name, None)
        overridden = own is not None and own is not getattr(TextRenderer, name, None)
        legacy = _LEGACY_RULES.get(node.kind)
        if legacy is not None and not overridden:
            return legacy(node, self)
        if own is None:
            raise ValueError(f"no text rule for node kind {node.kind!r}")
        return getattr(self, name)(node)

    def render_children(self, node):
        return "".join(self.render_node(c) for c in node.children)

    def visit_document(self, node):
        blocks = [self.render_node(c) for c in node.children]
        return "\n\n".join(blocks) + "\n"

    def visit_heading(self, node):
        text = self.render_children(node)
        return text + "\n" + ("=" if node.level == 1 else "-") * len(text)

    def visit_paragraph(self, node):
        return textwrap.fill(self.render_children(node), width=self.options.wrap)

    def visit_list(self, node, depth=0):
        lines = []
        for n, item in enumerate(node.children, start=1):
            bullet = f"{n}." if node.ordered else "-"
            first = True
            for child in item.children:
                if child.kind == "list":
                    lines.extend("  " + l for l in self.visit_list(child).split("\n"))
                else:
                    text = self.render_children(child) if child.kind == "paragraph" else self.render_node(child)
                    lines.append((f"{bullet} " if first else "  ") + text)
                    first = False
        return "\n".join(lines)

    def visit_item(self, node):
        return self.render_children(node)

    def visit_code_block(self, node):
        return "\n".join("    " + l for l in node.text.split("\n"))

    def visit_quote(self, node):
        inner = "\n\n".join(self.render_node(c) for c in node.children)
        return "\n".join("> " + l for l in inner.split("\n"))

    def visit_text(self, node):
        return node.text

    def visit_emph(self, node):
        return self.render_children(node)

    def visit_strong(self, node):
        return self.render_children(node)

    def visit_code(self, node):
        return node.text

    def visit_link(self, node):
        text = self.render_children(node)
        return text if node.href in ("", text) else f"{text} ({node.href})"
