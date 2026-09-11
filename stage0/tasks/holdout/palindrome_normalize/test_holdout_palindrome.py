"""Held-out deterministic verifier for the `palindrome_punctuation` task.

Injected into the workspace only AFTER the agent phase ends, identically for
every arm. Not present while agents work.
"""

from tinylib import (
    is_palindrome,
    longest_palindrome,
    normalize,
    render_heading,
    render_report,
    slugify,
)


# --- the reported defect -------------------------------------------------

def test_palindrome_ignores_punctuation():
    assert is_palindrome("A man, a plan, a canal: Panama!")
    assert is_palindrome("No 'x' in Nixon")
    assert is_palindrome("Madam, I'm Adam")


def test_palindrome_still_rejects_non_palindromes():
    assert not is_palindrome("hello, world")
    assert not is_palindrome("A man, a plan, a canal: Suez!")
    assert not is_palindrome("")
    assert not is_palindrome(None)


def test_longest_palindrome_with_punctuation():
    assert longest_palindrome(["abc", "Madam, I'm Adam", "level"]) == "Madam, I'm Adam"


# --- existing behaviour that must NOT regress ---------------------------

def test_normalize_still_preserves_punctuation():
    assert normalize("Hello,   World!") == "hello, world!"
    assert normalize("  Results: Q1, 2026 ") == "results: q1, 2026"


def test_slugify_unchanged():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("Results: Q1, 2026") == "results-q1-2026"


def test_report_headings_keep_punctuation():
    assert render_heading("Results: Q1, 2026") == '# results: q1, 2026 <a id="results-q1-2026"></a>'
    out = render_report("Notes!", ["First  item"])
    assert out.splitlines()[0].startswith("# notes!")
    assert "- first item" in out
