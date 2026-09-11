"""Build the document tree from block tokens."""

from . import inline, lexer
from .nodes import CodeBlock, Document, Heading, Item, List, Paragraph, Quote


def parse(source):
    doc = Document()
    doc.children = _blocks(lexer.tokenize(source), doc)
    return doc


def _blocks(tokens, doc):
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.kind == "blank":
            i += 1
        elif t.kind == "heading":
            out.append(Heading(t.level, inline.parse(t.text), source=t.text))
            i += 1
        elif t.kind == "fence":
            out.append(CodeBlock(t.lang, t.text))
            i += 1
        elif t.kind == "quote":
            lines = []
            while i < len(tokens) and tokens[i].kind == "quote":
                lines.append(tokens[i].text)
                i += 1
            out.append(Quote(_blocks(lexer.tokenize("\n".join(lines)), doc)))
        elif t.kind == "item":
            lst, i = _list(tokens, i, t.indent)
            out.append(lst)
        else:
            lines = []
            while i < len(tokens) and tokens[i].kind == "text":
                lines.append(tokens[i].text)
                i += 1
            out.append(Paragraph(inline.parse(" ".join(lines))))
    return out


def _list(tokens, i, indent):
    lst = List(tokens[i].ordered)
    while i < len(tokens):
        t = tokens[i]
        if t.kind != "item" or t.indent < indent:
            break
        if t.indent > indent and lst.children:
            sub, i = _list(tokens, i, t.indent)
            lst.children[-1].children.append(sub)
            continue
        lst.children.append(Item([Paragraph(inline.parse(t.text))]))
        i += 1
    return lst, i
