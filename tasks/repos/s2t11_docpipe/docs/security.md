# Rendering untrusted documents

docpipe renders user-supplied documents, so HTML output must be safe to embed.

* All text and attribute values are HTML-escaped.
* With `Options(safe_links=True)` (the default) a link is rendered as an
  `<a>` element only if its target is relative (no scheme: `docs/a.md`,
  `#install`, `../x`, `//cdn.example.org/y`) or uses `http`, `https` or
  `mailto`. Any other target is rendered as the link's text only, with no
  `<a>` element.
* Schemes are case-insensitive, and whitespace or control characters anywhere
  in the target are ignored when working out its scheme, as browsers do.
  (`JaVaScRiPt:`, ` javascript:` and `java\tscript:` are all `javascript:`.)
  A colon after the first `/`, `?` or `#` does not start a scheme
  (`docs/a:b.md` is relative).
* The target itself is written unchanged, apart from escaping.
* `safe_links=False` is for trusted documents only and keeps every link.
* The text renderer never produces markup and is unaffected.
