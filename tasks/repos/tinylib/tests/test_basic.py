from tinylib import (
    is_palindrome,
    longest_palindrome,
    normalize,
    render_heading,
    render_report,
    slugify,
)


def test_normalize_collapses_whitespace():
    assert normalize("  Hello   World  ") == "hello world"


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"


def test_is_palindrome_simple():
    assert is_palindrome("racecar")
    assert is_palindrome("Never odd or even")
    assert not is_palindrome("hello")


def test_longest_palindrome():
    assert longest_palindrome(["abc", "level", "rotator"]) == "rotator"


def test_render_heading_keeps_punctuation():
    assert render_heading("Results: Q1, 2026") == '# results: q1, 2026 <a id="results-q1-2026"></a>'


def test_render_report():
    out = render_report("Notes!", ["First  item", "Second item"])
    assert out.splitlines()[0].startswith("# notes!")
    assert "- first item" in out
