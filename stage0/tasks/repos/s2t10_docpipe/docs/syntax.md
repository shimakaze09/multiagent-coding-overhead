# Syntax

Blocks: `#`..`######` headings, paragraphs (consecutive lines), `-`/`*`/`+`
and `1.` list items (nested by indentation), `>` quotes, and fenced code
blocks between ```` ``` ```` lines.

Inline: `*emphasis*` or `_emphasis_`, `**strong**`, `` `code` ``,
`[text](href)` links, and backslash escapes for `` \ ` * _ [ ] ( ) # + - . ! ``.

## Heading ids

A heading's id is the slug of its *plain text*: its visible text, including
link text and code, excluding markup. Slugs are case
folded, keep letters, digits, `-` and `_`, and join words with `-`. A repeated
slug gets `-1`, `-2`... in document order. The table of contents, the HTML
renderer and the anchor check (`docpipe.check`) all use the same ids.
`heading_offset` changes levels, never ids.
