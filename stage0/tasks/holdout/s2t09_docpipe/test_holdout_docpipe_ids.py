"""Held-out verifier: one heading id per heading, shared by the TOC, the HTML
and the anchor check."""

import re

from docpipe import HtmlRenderer, Options, RenderCache, check, parse, render


def ids_in(html):
    return re.findall(r'<h\d id="([^"]*)"', html)


def toc_targets(html):
    return re.findall(r'<li class="toc-h\d"><a href="#([^"]*)"', html)


DOCUMENTS = [
    "# [Install](install.md) notes\n\n## Usage\n",
    "# Setup[^1]\n\n## Setup\n\n[^1]: first time only\n",
    "# Use `run` & *go*!\n\n## Use run go\n",
    "# Build\n\n## [Build](b.md)\n\n### Build\n",
    "# \\*Stars\\* and **bold**\n\n## Café au lait\n",
    "## Configuring [the CLI](cli.md)\n\n## Configuring the CLI\n\n# Next\n",
]


def test_c1_every_toc_entry_points_at_its_heading():
    for src in DOCUMENTS:
        out = render(src, options=Options(toc=True, toc_max_level=6))
        assert toc_targets(out) == ids_in(out), src


def test_c1_ids_follow_the_documented_rule():
    out = render("# [Install](install.md) notes\n\n# Setup[^n]\n\n# Setup\n\n"
                 "# Use `run` & *go*!\n\n[^n]: x\n")
    assert ids_in(out) == ["install-notes", "setup", "setup-1", "use-run-go"]


def test_c2_deeper_headings_still_count_for_duplicates():
    out = render("# Intro\n\n#### Intro\n\n## Intro\n", options=Options(toc=True, toc_max_level=2))
    assert ids_in(out) == ["intro", "intro-1", "intro-2"]
    assert toc_targets(out) == ["intro", "intro-2"]


def test_c3_heading_offset_changes_levels_not_ids():
    src = "# [Install](install.md) notes\n\n## Setup\n\n## Setup\n"
    base = ids_in(render(src))
    for offset in (1, 2, 5):
        out = render(src, options=Options(heading_offset=offset, toc=True))
        assert ids_in(out) == base == toc_targets(out)
    assert render("# A\n", options=Options(heading_offset=9)).startswith('<h6 id="a">')


def test_c4_the_anchor_check_agrees_with_the_html():
    src = "# [Install](install.md) notes\n\nSee [the notes](#install-notes).\n"
    assert check(src) == [] and 'id="install-notes"' in render(src)
    src = "# Setup[^1]\n\nSee [setup](#setup).\n\n[^1]: n\n"
    assert check(src) == [] and 'id="setup"' in render(src)
    src = "# Build\n\n## [Build](b.md)\n\nSee [second](#build-1).\n"
    assert check(src) == [] and 'id="build-1"' in render(src)


def test_c5_ids_are_stable_across_renders():
    src = "# A\n\n# A\n"
    cache = RenderCache()
    outs = [render(src, cache=cache), render(src), render(src, cache=cache)]
    assert all(ids_in(o) == ["a", "a-1"] for o in outs)
    doc = parse(src)
    renderer = HtmlRenderer()
    assert ids_in(renderer.render(doc)) == ids_in(renderer.render(doc)) == ["a", "a-1"]


def test_r_plain_headings_and_text_output_unchanged():
    assert ids_in(render("# Hello World\n\n## Hello-World\n")) == ["hello-world", "hello-world-1"]
    assert render("# Title[^1]\n\n[^1]: n\n", fmt="text").startswith("Title[1]\n")
