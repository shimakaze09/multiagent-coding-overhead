"""Held-out verifier: footnotes through lexer, inline parser, transforms, both
renderers and the checker."""

from docpipe import Options, check, render

FN = ("Intro text[^b] and more[^a].\n\nAgain[^b].\n\n"
      "[^a]: Alpha *note*.\n[^b]: Beta.\n[^unused]: Nope.\n")


def ref(n, ref_id):
    return f'<sup class="footnote-ref"><a href="#fn-{n}" id="{ref_id}">{n}</a></sup>'


def test_c1_notes_are_numbered_by_first_reference():
    out = render(FN)
    assert f"<p>Intro text{ref(1, 'fnref-1')} and more{ref(2, 'fnref-2')}.</p>" in out
    assert f"<p>Again{ref(1, 'fnref-1-2')}.</p>" in out


def test_c2_the_notes_section_follows_the_document():
    out = render(FN)
    assert out.endswith(
        '<section class="footnotes"><ol>'
        '<li id="fn-1">Beta. <a href="#fnref-1" class="footnote-back">↩</a></li>'
        '<li id="fn-2">Alpha <em>note</em>. <a href="#fnref-2" class="footnote-back">↩</a></li>'
        '</ol></section>')
    assert "Nope" not in out


def test_c3_undefined_unused_and_duplicate_notes():
    out = render("See[^x].\n")
    assert out == "<p>See[^x].</p>"
    assert check("See[^x].\n") == ["undefined footnote [^x]"]
    assert "unused footnote definition [^unused]" in check(FN)
    dup = "A[^d].\n\n[^d]: first\n[^d]: second\n"
    assert "second" not in render(dup) and "first" in render(dup)
    assert "duplicate footnote definition [^d] ignored" in check(dup)


def test_c4_text_renderer():
    assert render(FN, fmt="text") == "Intro text[1] and more[2].\n\nAgain[1].\n\n[1] Beta.\n[2] Alpha note.\n"


def test_c5_definitions_headings_and_the_toc():
    src = "# Title[^a]\n\n## Next\n\n[^a]: About the title.\n"
    out = render(src, options=Options(toc=True))
    assert '<h1 id="title">Title' + ref(1, "fnref-1") + "</h1>" in out
    assert '<li class="toc-h1"><a href="#title">Title</a></li>' in out
    assert "<p>[^a]" not in out and "footnotes" in out
    assert out.count("<li") == 3


def test_c6_references_in_lists_quotes_and_emphasis():
    src = "- one[^z]\n- two\n\n> quoted[^y]\n\n*very[^x]* done[^z]\n\n[^x]: X\n[^y]: Y\n[^z]: Z\n"
    out = render(src)
    assert ref(1, "fnref-1") in out and ref(2, "fnref-2") in out and ref(3, "fnref-3") in out
    assert ref(1, "fnref-1-2") in out
    assert out.index('id="fn-1">Z') < out.index('id="fn-2">Y') < out.index('id="fn-3">X')


def test_r_ordinary_brackets_and_links_are_unaffected():
    assert render("[text](https://example.org) and [not a note] and a[^]b\n") == (
        '<p><a href="https://example.org">text</a> and [not a note] and a[^]b</p>')
