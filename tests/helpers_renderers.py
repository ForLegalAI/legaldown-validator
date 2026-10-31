"""What the tests that compare with other renderers share (markdown-it-py, pandoc)."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable

import pytest

try:
    import markdown_it
except ImportError:  # (a development dependency: the tests that ask it are skipped)
    markdown_it = None

needs_markdown_it = pytest.mark.skipif(markdown_it is None, reason="markdown-it-py is not installed")
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")

#: A blank line or a quote line ends the paragraph a link is in.
PARAGRAPH_END_RE = re.compile(r"\n[ \t]*(?:\n|>)")
#: markdown-it reads a backslash before any character as an escape pair, not only before punctuation (cmark and
#: pandoc do not): the model does not read that link, which the path rules do (``Reading.MDIT``, #121).
BACKSLASH_PAIR_RE = re.compile(r"\\(?![!-/:-@\[-`{-~])")
#: What markdown-it normalizes in a destination that looks like a URL with a scheme (``b:[r]<`` is ``b:r<``).
URL_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,31}:")


# ── Where each block is: what pandoc and markdown-it read of a document's containers ──────


def pandoc_blocks(source: str, *, sourcepos: bool = True) -> list | None:
    """pandoc's reading of *source* (``-f commonmark``) as the blocks of its JSON AST, with the source positions of
    each (``sourcepos``) in a ``data-pos`` attribute, or None if pandoc fails."""
    reader = "commonmark+sourcepos" if sourcepos else "commonmark"
    run = subprocess.run(["pandoc", "-f", reader, "-t", "json"], input=source.encode(), capture_output=True, check=False)
    return json.loads(run.stdout)["blocks"] if run.returncode == 0 else None


def _positions(attrs: list) -> list[list[tuple[int, int]]]:
    """The ``data-pos`` values of an attribute list: for each, its segments as ``(first line, last line)``, counted from 0."""
    out = []
    for key, value in attrs:
        if key == "data-pos" and value:
            segments = []
            for segment in value.split(";"):
                start, end = segment.split("-")
                segments.append((int(start.split(":")[0]) - 1, int(end.split(":")[0]) - 1))
            out.append(segments)
    return out


def _pandoc_nodes(blocks: list, visit: Callable[[dict, list[tuple[int, int]], bool], None]) -> None:
    """Call *visit* for each block pandoc makes of a document that *blocks* are: the block, where it lies, and
    whether it is the first in a list item. Pandoc wraps most blocks in a Div that carries their position, and a
    Header or CodeBlock carries its own; a block in an item has two (the item's, then its own), and a setext
    heading's reads "underline;whole"."""

    def sequence(items: list, in_item: bool) -> None:
        first = True
        for block in items:
            kind = block["t"]
            if kind == "Div":
                positions, inner = _positions(block["c"][0][2]), block["c"][1]
            elif kind == "Header":
                positions, inner = _positions(block["c"][1][2]), [block]
            elif kind == "CodeBlock":
                positions, inner = _positions(block["c"][0][2]), [block]
            else:
                continue
            if not positions:
                continue
            if in_item:
                own = positions[1] if len(positions) > 1 else positions[0]
                visit({"t": "Item"}, positions[0], first)
                first = False
            else:
                own = positions[0]
            for node in inner:
                walk(node, own)

    def walk(node: dict, own: list[tuple[int, int]]) -> None:
        kind = node["t"]
        if kind in ("BulletList", "OrderedList"):
            for item in node["c"] if kind == "BulletList" else node["c"][1]:
                sequence(item, True)
        elif kind == "BlockQuote":
            visit(node, own, False)
            sequence(node["c"], False)
        else:
            visit(node, own, False)

    sequence(blocks, False)


def pandoc_begun(blocks: list) -> dict[int, Counter[str]]:
    """What begins on each line of the document that pandoc read as *blocks* (with sourcepos): ``item``, ``quote``,
    ``heading``, ``setext`` (for the underline), ``code``, ``html`` and ``rule``."""
    out: dict[int, Counter[str]] = {}

    def visit(node: dict, own: list[tuple[int, int]], first: bool) -> None:
        kind = node["t"]
        if kind == "Item":
            found = "item" if first else None
        elif kind == "BlockQuote":
            found = "quote"
        elif kind == "Header":
            found = "heading" if len(own) == 1 and own[0][1] - own[0][0] <= 1 else "setext"
        else:
            found = {"CodeBlock": "code", "RawBlock": "html", "HorizontalRule": "rule"}.get(kind)
        if found:
            out.setdefault(own[0][0], Counter())[found] += 1

    _pandoc_nodes(blocks, visit)
    return out


def pandoc_words(blocks: list) -> dict[int, str]:
    """The words of each line of the paragraphs of the document that pandoc read as *blocks* (with sourcepos),
    single-spaced. A paragraph with markup in it (emphasis, a code span, a link) has none."""
    out: dict[int, str] = {}

    def flatten(inlines: list) -> list[dict]:
        """The inlines, without the Spans that carry their positions."""
        return [inner for inline in inlines for inner in (flatten(inline["c"][1]) if inline["t"] == "Span" else [inline])]

    def visit(node: dict, own: list[tuple[int, int]], _first: bool) -> None:
        if node["t"] not in ("Para", "Plain"):
            return
        words, parts = "", []
        for inline in flatten(node["c"]):
            if inline["t"] == "Str":
                words += inline["c"]
            elif inline["t"] == "Space":
                words += " "
            elif inline["t"] in ("SoftBreak", "LineBreak"):
                parts.append(words.strip())
                words = ""
            else:
                return
        parts.append(words.strip())
        out.update({own[0][0] + at: text for at, text in enumerate(parts)})

    _pandoc_nodes(blocks, visit)
    return out


def container_paths(blocks: list) -> dict[int, tuple[str, ...]]:
    """The containers each line of the document that pandoc read as *blocks* (with sourcepos) is in, outer first: ``q``
    for a block quote and ``i`` for a list item, for the lines of the blocks that are not containers."""
    out: dict[int, tuple[str, ...]] = {}

    def sequence(items: list, in_item: bool, path: tuple[str, ...]) -> None:
        for block in items:
            kind = block["t"]
            if kind == "Div":
                positions, inner = _positions(block["c"][0][2]), block["c"][1]
            elif kind == "Header":
                positions, inner = _positions(block["c"][1][2]), [block]
            elif kind == "CodeBlock":
                positions, inner = _positions(block["c"][0][2]), [block]
            else:
                continue
            if not positions:
                continue
            own = (positions[1] if len(positions) > 1 else positions[0]) if in_item else positions[0]
            for node in inner:
                walk(node, own, path)

    def walk(node: dict, own: list[tuple[int, int]], path: tuple[str, ...]) -> None:
        kind = node["t"]
        if kind in ("BulletList", "OrderedList"):
            for item in node["c"] if kind == "BulletList" else node["c"][1]:
                sequence(item, True, (*path, "i"))
        elif kind == "BlockQuote":
            sequence(node["c"], False, (*path, "q"))
        else:
            for line in range(own[0][0], own[0][1] + 1):
                if line not in out or len(path) >= len(out[line]):
                    out[line] = path

    sequence(blocks, False, ())
    return out


def markdown_it_lines(source: str) -> dict[int, tuple[tuple[str, ...], str, int, str]]:
    """What markdown-it reads each line of *source* as, for the lines of paragraphs, headings and code: the
    containers it is in, outer first (``q``, ``i``), its content after their markers, how many containers
    start on it, and ``text`` or ``code``. In a paragraph the content is the text of its inline token, whose
    lines after the first keep their own spacing (the first is trimmed)."""
    out: dict[int, tuple[tuple[str, ...], str, int, str]] = {}
    opened: Counter[int] = Counter()
    path: list[str] = []
    lines: list[tuple[int, tuple[str, ...], str, str]] = []
    for token in markdown_it.MarkdownIt("commonmark").parse(source):
        if token.type in ("blockquote_open", "list_item_open"):
            path.append("q" if token.type == "blockquote_open" else "i")
            opened[token.map[0]] += 1
        elif token.type in ("blockquote_close", "list_item_close"):
            path.pop()
        elif token.type == "inline" and token.map:
            lines += [(token.map[0] + at, tuple(path), text, "text") for at, text in enumerate(token.content.split("\n"))]
        elif token.type in ("fence", "code_block") and token.map:
            lines += [
                (token.map[0] + at, tuple(path), text, "code")
                for at, text in enumerate(token.content.split("\n")[: token.map[1] - token.map[0]])
            ]
    for line, within, text, kind in lines:
        out[line] = (within, text, opened[line], kind)
    return out
