from docpipe import Options, RenderCache, check, render


def test_headings_paragraphs_and_inline_markup():
    out = render("# Getting started\n\nHello *world* and **bold** `code`.\n")
    assert '<h1 id="getting-started">Getting started</h1>' in out
    assert "<p>Hello <em>world</em> and <strong>bold</strong> <code>code</code>.</p>" in out


def test_duplicate_headings_get_numbered_ids():
    out = render("# Setup\n\n## Setup\n\n## Setup\n")
    assert 'id="setup"' in out and 'id="setup-1"' in out and 'id="setup-2"' in out


def test_heading_offset():
    assert '<h3 id="title">Title</h3>' in render("# Title\n", options=Options(heading_offset=2))


def test_nested_lists():
    out = render("- one\n- two\n  - two a\n")
    assert out == "<ul><li>one</li><li>two<ul><li>two a</li></ul></li></ul>"


def test_code_blocks_and_quotes_are_escaped():
    out = render("```python\nif a < b:\n    pass\n```\n\n> quoted <b>\n")
    assert '<pre><code class="language-python">if a &lt; b:\n    pass</code></pre>' in out
    assert "<blockquote><p>quoted &lt;b&gt;</p></blockquote>" in out


def test_relative_and_http_links():
    out = render("See [the docs](docs/intro.md) or [site](https://example.org/?a=1&b=2).\n")
    assert '<a href="docs/intro.md">the docs</a>' in out
    assert '<a href="https://example.org/?a=1&amp;b=2">site</a>' in out


def test_escapes():
    assert render("Use \\*args and \\[brackets\\].\n") == "<p>Use *args and [brackets].</p>"


def test_table_of_contents():
    out = render("# A\n\n## B\n\n### C\n\n#### D\n", options=Options(toc=True))
    assert out.startswith('<nav class="toc"><ul><li class="toc-h1"><a href="#a">A</a></li>'
                          '<li class="toc-h2"><a href="#b">B</a></li>'
                          '<li class="toc-h3"><a href="#c">C</a></li></ul></nav>')


def test_text_renderer():
    out = render("# Title\n\nSome *text* with a [link](http://x.org).\n\n- a\n- b\n", fmt="text")
    assert out == "Title\n=====\n\nSome text with a link (http://x.org).\n\n- a\n- b\n"


def test_anchor_check():
    assert check("# Intro\n\nSee [intro](#intro).\n") == []
    assert check("# Intro\n\nSee [x](#missing).\n") == ["broken anchor #missing"]


def test_cache_reuses_output():
    cache = RenderCache()
    a = render("# X\n", cache=cache)
    b = render("# X\n", cache=cache)
    assert a == b and (cache.hits, cache.misses) == (1, 1)
    render("# X\n", options=Options(heading_offset=1), cache=cache)
    assert cache.misses == 2
