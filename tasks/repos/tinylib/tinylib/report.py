"""Very small report renderer.

Headings must keep their punctuation, which is why `normalize` does not strip it.
"""

from .text import normalize, slugify


def render_heading(title, level=1):
    """Render a markdown heading with a stable anchor."""
    text = normalize(title)
    return "%s %s <a id=\"%s\"></a>" % ("#" * level, text, slugify(title))


def render_report(title, rows):
    """Render a heading plus one bullet per row."""
    lines = [render_heading(title)]
    for row in rows or []:
        lines.append("- %s" % normalize(row))
    return "\n".join(lines)
