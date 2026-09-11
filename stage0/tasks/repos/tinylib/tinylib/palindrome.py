"""Palindrome helpers.

These build on tinylib.text.normalize so that casing and spacing are handled
consistently across the library.
"""

from .text import normalize


def is_palindrome(text):
    """Return True when `text` reads the same forwards and backwards."""
    cleaned = normalize(text).replace(" ", "")
    return bool(cleaned) and cleaned == cleaned[::-1]


def longest_palindrome(words):
    """Return the longest palindromic entry in `words`, or None."""
    best = None
    for word in words or []:
        if is_palindrome(word) and (best is None or len(word) > len(best)):
            best = word
    return best
