"""Netscape bookmarks HTML (the format every browser imports and exports), written and parsed
with the standard library only."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterator, List, Optional, Tuple

HEADER = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<!-- This is an automatically generated file.
     It will be read and overwritten.
     DO NOT EDIT! -->
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>"""


@dataclass
class BNode:
    title: str
    url: Optional[str] = None
    add_date: Optional[int] = None
    children: List["BNode"] = field(default_factory=list)

    @property
    def is_folder(self) -> bool:
        return self.url is None

    def folder(self, title: str, add_date: Optional[int] = None) -> "BNode":
        node = BNode(title, None, add_date)
        self.children.append(node)
        return node


def render(nodes: List[BNode]) -> str:
    out = [HEADER, "<DL><p>"]

    def emit(node: BNode, depth: int) -> None:
        pad = "    " * depth
        date = f' ADD_DATE="{int(node.add_date)}"' if node.add_date else ""
        if node.is_folder:
            out.append(f"{pad}<DT><H3{date}>{html.escape(node.title, quote=False)}</H3>")
            out.append(f"{pad}<DL><p>")
            for child in node.children:
                emit(child, depth + 1)
            out.append(f"{pad}</DL><p>")
        else:
            href = html.escape(node.url or "", quote=True)
            out.append(f'{pad}<DT><A HREF="{href}"{date}>{html.escape(node.title, quote=False)}</A>')

    for node in nodes:
        emit(node, 1)
    out.append("</DL><p>")
    return "\n".join(out) + "\n"


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = BNode("")
        self.stack: List[BNode] = []
        self.pending: Optional[BNode] = None
        self.capture: Optional[str] = None
        self.buf: List[str] = []
        self.href: Optional[str] = None
        self.add_date: Optional[int] = None

    def _current(self) -> BNode:
        return self.stack[-1] if self.stack else self.root

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "dl":
            if self.pending is not None:
                self.stack.append(self.pending)
            else:
                self.stack.append(self._current())
            self.pending = None
        elif tag in ("h3", "a"):
            self.capture = tag
            self.buf = []
            self.href = attrs.get("href")
            date = attrs.get("add_date")
            self.add_date = int(date) if date and date.isdigit() else None

    def handle_endtag(self, tag):
        if tag == "dl":
            if self.stack:
                self.stack.pop()
        elif tag == self.capture:
            text = "".join(self.buf)
            if tag == "h3":
                node = BNode(text, None, self.add_date)
                self._current().children.append(node)
                self.pending = node
            else:
                self._current().children.append(BNode(text, self.href or "", self.add_date))
                self.pending = None
            self.capture = None

    def handle_data(self, data):
        if self.capture:
            self.buf.append(data)


def parse(text: str) -> List[BNode]:
    parser = _Parser()
    parser.feed(text)
    parser.close()
    return parser.root.children


def iter_links(nodes: List[BNode], path: Tuple[str, ...] = ()) -> Iterator[Tuple[Tuple[str, ...], BNode]]:
    for node in nodes:
        if node.is_folder:
            yield from iter_links(node.children, path + (node.title,))
        else:
            yield path, node


def count_links(nodes: List[BNode]) -> int:
    return sum(1 for _ in iter_links(nodes))
