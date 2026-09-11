"""Document tree nodes. Every node has a ``kind`` and a list of ``children``."""


class Node:
    kind = "node"

    def __init__(self, children=None):
        self.children = list(children or [])

    def __repr__(self):
        return f"{type(self).__name__}({self.children!r})"


class Document(Node):
    kind = "document"

    def __init__(self, children=None):
        super().__init__(children)
        self.warnings = []


class Heading(Node):
    kind = "heading"

    def __init__(self, level, children, source=""):
        super().__init__(children)
        self.level = level
        self.source = source      # the heading's markup, as written
        self.id = None            # assigned by transforms.assign_heading_ids


class Paragraph(Node):
    kind = "paragraph"


class List(Node):
    kind = "list"

    def __init__(self, ordered, items=None):
        super().__init__(items)
        self.ordered = ordered


class Item(Node):
    kind = "item"


class CodeBlock(Node):
    kind = "code_block"

    def __init__(self, lang, text):
        super().__init__()
        self.lang = lang
        self.text = text


class Quote(Node):
    kind = "quote"


class Text(Node):
    kind = "text"

    def __init__(self, text):
        super().__init__()
        self.text = text

    def __repr__(self):
        return f"Text({self.text!r})"


class Emph(Node):
    kind = "emph"


class Strong(Node):
    kind = "strong"


class Code(Node):
    kind = "code"

    def __init__(self, text):
        super().__init__()
        self.text = text


class Link(Node):
    kind = "link"

    def __init__(self, children, href):
        super().__init__(children)
        self.href = href
