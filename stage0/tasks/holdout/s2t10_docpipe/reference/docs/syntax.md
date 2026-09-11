# Syntax

Blocks: `#`..`######` headings, paragraphs (consecutive lines), `-`/`*`/`+`
and `1.` list items (nested by indentation), `>` quotes, and fenced code
blocks between ```` ``` ```` lines.

Inline: `*emphasis*` or `_emphasis_`, `**strong**`, `` `code` ``,
`[text](href)` links, and backslash escapes for `` \ ` * _ [ ] ( ) # + - . ! ^ ``.

## Footnotes

A reference is `[^label]` (no spaces in the label). A definition is a line of
its own: `[^label]: text`. Notes are numbered 1, 2, 3... in the order of their
*first* reference; a note referenced twice keeps its number. A reference to an
undefined note is left as written (with a warning); a definition that is never
referenced is dropped (with a warning); a second definition of a label is
ignored (with a warning).

HTML: a reference renders as
`<sup class="footnote-ref"><a href="#fn-N" id="fnref-N">N</a></sup>`, with
ids `fnref-N-2`, `fnref-N-3`... for later references to the same note. The notes follow the document as
`<section class="footnotes"><ol><li id="fn-N">text <a href="#fnref-N" class="footnote-back">↩</a></li>...</ol></section>`.
Text: a reference renders as `[N]`, and the notes follow the last block as `[N] text` lines.
Footnotes never appear in the table of contents.

## Heading ids

A heading's id is the slug of its *plain text*: its visible text, including
link text and code, excluding footnote references and markup. Slugs are case
folded, keep letters, digits, `-` and `_`, and join words with `-`. A repeated
slug gets `-1`, `-2`... in document order. The table of contents, the HTML
renderer and the anchor check (`docpipe.check`) all use the same ids.
`heading_offset` changes levels, never ids.
