"""Held-out verifier: only safe link targets become links (docs/security.md)."""

from docpipe import Options, render


def link(href, text="x", **kw):
    return render(f"[{text}]({href})\n", **kw)


def assert_not_a_link(href):
    out = link(href)
    assert "<a" not in out and out == "<p>x</p>", (href, out)


def test_c1_javascript_is_never_a_link():
    for href in ("javascript:alert(1)", "JaVaScRiPt:alert(document.cookie)",
                 "JAVASCRIPT:void(0)"):
        assert_not_a_link(href)


def test_c2_whitespace_and_control_characters_do_not_hide_a_scheme():
    for href in (" javascript:alert(1)", "java\tscript:alert(1)", "java script:alert(1)",
                 "\x01javascript:alert(1)", "jav\x7fascript:alert(1)"):
        assert_not_a_link(href)


def test_c3_only_http_https_and_mailto_schemes_are_allowed():
    for href in ("vbscript:msgbox(1)", "data:text/html;base64,PHNjcmlwdD4=", "file:///etc/passwd",
                 "ftp://example.org/x", "tel:+15555550100"):
        assert_not_a_link(href)


def test_c4_safe_targets_stay_links_and_are_written_unchanged():
    cases = {"HTTP://EXAMPLE.ORG/A": "HTTP://EXAMPLE.ORG/A", "mailto:team@example.org": "mailto:team@example.org",
             "docs/a.md": "docs/a.md", "#install": "#install", "../up.md": "../up.md",
             "//cdn.example.org/x.js": "//cdn.example.org/x.js", "docs/a:b.md": "docs/a:b.md",
             "?q=1:2": "?q=1:2", "https://example.org/?a=1&b=2": "https://example.org/?a=1&amp;b=2"}
    for href, written in cases.items():
        assert link(href) == f'<p><a href="{written}">x</a></p>', href


def test_c5_unsafe_link_text_keeps_its_markup():
    assert render("[**bold** `c`](javascript:x)\n") == "<p><strong>bold</strong> <code>c</code></p>"


def test_c6_trusted_mode_and_text_output():
    assert link("javascript:alert(1)", options=Options(safe_links=False)) == (
        '<p><a href="javascript:alert(1)">x</a></p>')
    assert link("javascript:alert(1)", fmt="text") == "x (javascript:alert(1))\n"


def test_r_attribute_escaping_unchanged():
    assert link('a"b.md') == '<p><a href="a&quot;b.md">x</a></p>'
    assert link("docs/<x>.md") == '<p><a href="docs/&lt;x&gt;.md">x</a></p>'
