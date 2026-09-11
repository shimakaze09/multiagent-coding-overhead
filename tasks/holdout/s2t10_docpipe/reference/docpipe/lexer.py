"""Split source text into block tokens (docs/syntax.md)."""

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_FENCE = re.compile(r"^```\s*([\w+-]*)\s*$")
_FOOTNOTE_DEF = re.compile(r"^\[\^([^\]\s]+)\]:\s?(.*)$")


@dataclass
class Token:
    kind: str
    text: str = ""
    level: int = 0
    lang: str = ""
    indent: int = 0
    ordered: bool = False
    label: str = ""


def tokenize(source):
    lines = source.replace("\r\n", "\n").split("\n")
    tokens = []
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            body = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            tokens.append(Token("fence", "\n".join(body), lang=fence.group(1)))
            i += 1
            continue
        if not line.strip():
            tokens.append(Token("blank"))
        elif m := _HEADING.match(line):
            tokens.append(Token("heading", m.group(2), level=len(m.group(1))))
        elif m := _FOOTNOTE_DEF.match(line):
            tokens.append(Token("footnote_def", m.group(2).strip(), label=m.group(1)))
        elif m := _ITEM.match(line):
            tokens.append(Token("item", m.group(3).strip(), indent=len(m.group(1).expandtabs(4)),
                                ordered=m.group(2)[0].isdigit()))
        elif m := _QUOTE.match(line):
            tokens.append(Token("quote", m.group(1)))
        else:
            tokens.append(Token("text", line.strip()))
        i += 1
    return tokens
