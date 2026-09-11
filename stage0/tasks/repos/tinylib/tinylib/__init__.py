"""tinylib - a very small text utility library."""

from .text import normalize, slugify
from .palindrome import is_palindrome, longest_palindrome
from .report import render_heading, render_report

__all__ = [
    "normalize",
    "slugify",
    "is_palindrome",
    "longest_palindrome",
    "render_heading",
    "render_report",
]
