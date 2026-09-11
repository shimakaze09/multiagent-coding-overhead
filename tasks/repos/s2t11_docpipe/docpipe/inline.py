"""Inline markup: *emphasis*, **strong**, `code`, [links](href), [^note]
references and backslash escapes (docs/syntax.md)."""

from .nodes import Code, Emph, FootnoteRef, Link, Strong, Text

ESCAPABLE = set("\\`*_[]()#+-.!^")


def parse(text):
    return _merge(_parse(text))


def plain_text(nodes):
    """Text content: link text included, footnote references excluded."""
    out = []
    for n in nodes:
        if n.kind in ("text", "code"):
            out.append(n.text)
        elif n.kind in ("emph", "strong", "link"):
            out.append(plain_text(n.children))
    return "".join(out)


def _merge(nodes):
    out = []
    for n in nodes:
        if n.kind == "text" and out and out[-1].kind == "text":
            out[-1] = Text(out[-1].text + n.text)
        else:
            if n.kind in ("emph", "strong", "link"):
                n.children = _merge(n.children)
            out.append(n)
    return out


def _find_closer(text, start, delim):
    j = start
    while j < len(text):
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == "`":
            end = text.find("`", j + 1)
            j = end + 1 if end != -1 else j + 1
            continue
        if text.startswith(delim, j):
            if delim in ("*", "_") and text.startswith(delim * 2, j):
                j += 2
                continue
            return j
        j += 1
    return -1


def _link_at(text, i):
    depth, j = 0, i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    else:
        return None
    if not text.startswith("(", j + 1):
        return None
    depth, k = 0, j + 1          # the target may contain balanced parentheses
    while k < len(text):
        ch = text[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        k += 1
    else:
        return None
    return text[i + 1:j], text[j + 2:k].strip(), k + 1


def _parse(text):
    out, buf = [], []

    def flush():
        if buf:
            out.append(Text("".join(buf)))
            buf.clear()

    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text) and text[i + 1] in ESCAPABLE:
            buf.append(text[i + 1])
            i += 2
            continue
        if c == "`":
            end = text.find("`", i + 1)
            if end != -1:
                flush()
                out.append(Code(text[i + 1:end]))
                i = end + 1
                continue
        if text.startswith("**", i):
            end = _find_closer(text, i + 2, "**")
            if end > i + 2:
                flush()
                out.append(Strong(_parse(text[i + 2:end])))
                i = end + 2
                continue
        if c in "*_":
            end = _find_closer(text, i + 1, c)
            if end > i + 1:
                flush()
                out.append(Emph(_parse(text[i + 1:end])))
                i = end + 1
                continue
        if text.startswith("[^", i):
            end = text.find("]", i)
            label = text[i + 2:end] if end != -1 else ""
            if label and not any(ch.isspace() or ch == "[" for ch in label):
                flush()
                out.append(FootnoteRef(label))
                i = end + 1
                continue
        if c == "[":
            found = _link_at(text, i)
            if found:
                label, href, i = found
                flush()
                out.append(Link(_parse(label), href))
                continue
        buf.append(c)
        i += 1
    flush()
    return out
