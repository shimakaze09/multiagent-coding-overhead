"""Held-out verifier: class-based renderers, with the 1.x rule functions kept
working (deprecated)."""

import warnings

import pytest

import docpipe
from docpipe import Options, RenderCache, check, render

SAMPLE = "# Title\n\nSome *text*, `code` and a [link](docs/a.md).\n\n> quoted\n\n- item\n"
EXPECTED_HTML = ('<h1 id="title">Title</h1>\n<p>Some <em>text</em>, <code>code</code> and a '
                 '<a href="docs/a.md">link</a>.</p>\n<blockquote><p>quoted</p></blockquote>\n'
                 "<ul><li>item</li></ul>")


def aside_renderer():
    class Aside(docpipe.HtmlRenderer):
        def visit_quote(self, node):
            return f"<aside>{self.render_children(node)}</aside>"
    return Aside


def legacy_html(kind, fn):
    with pytest.warns(DeprecationWarning):
        docpipe.register_html(kind, fn)


def test_c1_subclass_overrides_one_kind():
    Aside = aside_renderer()
    assert render("> hi\n", renderer=Aside) == "<aside><p>hi</p></aside>"
    assert render("> hi\n") == "<blockquote><p>hi</p></blockquote>"

    class Shifted(docpipe.HtmlRenderer):
        def visit_heading(self, node):
            return f"<h{node.level + 1}>{self.render_children(node)}|{self.options.wrap}</h{node.level + 1}>"
    assert render("# *T*\n", "html", Options(wrap=40), renderer=Shifted) == "<h2><em>T</em>|40</h2>"


def test_c2_legacy_rule_functions_still_work():
    legacy_html("code", lambda node, ctx: f"<kbd>{node.text}</kbd>")
    legacy_html("emph", lambda node, ctx: "<i>" + ctx.render_children(node) + "</i>")
    try:
        assert render("`x` *y*\n") == "<p><kbd>x</kbd> <i>y</i></p>"
    finally:
        docpipe.unregister_html("code")
        docpipe.unregister_html("emph")
    assert render("`x` *y*\n") == "<p><code>x</code> <em>y</em></p>"


def test_c3_a_subclass_method_beats_a_legacy_rule():
    legacy_html("code", lambda node, ctx: "LEGACY")
    legacy_html("quote", lambda node, ctx: "QUOTE")

    class Own(docpipe.HtmlRenderer):
        def visit_code(self, node):
            return "OWN"
    try:
        assert render("`x`\n\n> q\n", renderer=Own) == "<p>OWN</p>\nQUOTE"
        assert render("`x`\n") == "<p>LEGACY</p>"
    finally:
        docpipe.unregister_html("code")
        docpipe.unregister_html("quote")


def test_c4_the_cache_never_mixes_renderers_or_rules():
    cache = RenderCache()
    Aside = aside_renderer()
    plain = render("> hi\n", cache=cache)
    aside = render("> hi\n", cache=cache, renderer=Aside)
    assert (plain, aside) == ("<blockquote><p>hi</p></blockquote>", "<aside><p>hi</p></aside>")
    legacy_html("quote", lambda node, ctx: "RULE")
    try:
        assert render("> hi\n", cache=cache) == "RULE"
    finally:
        docpipe.unregister_html("quote")
    assert render("> hi\n", cache=cache) == plain


def test_c5_text_renderer_has_the_same_api():
    class Upper(docpipe.TextRenderer):
        def visit_heading(self, node):
            return self.render_children(node).upper()
    assert render("# Title\n\nx\n", fmt="text", renderer=Upper) == "TITLE\n\nx\n"
    with pytest.warns(DeprecationWarning):
        docpipe.register_text("code", lambda node, ctx: f"<{node.text}>")
    try:
        assert render("a `b`\n", fmt="text") == "a <b>\n"
    finally:
        docpipe.unregister_text("code")


def test_c6_docpipe_never_uses_the_deprecated_api():
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert render(SAMPLE) == EXPECTED_HTML
        render(SAMPLE, fmt="text", options=Options(wrap=20))
        render(SAMPLE, options=Options(toc=True), cache=RenderCache())
        check(SAMPLE)


def test_r_default_output_and_call_signature_unchanged():
    assert render(SAMPLE, "html", Options()) == EXPECTED_HTML
    assert render(SAMPLE, "text").startswith("Title\n=====\n\nSome text, code and a link (docs/a.md).")
