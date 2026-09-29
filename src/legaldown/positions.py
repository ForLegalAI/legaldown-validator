"""Where a parsed document's parts lie in its source, for diagnostics that
name their line (§16.9).

``parse_document`` gives each document a ``SourceMap``; a document built from
a dict has none, and its diagnostics carry no line.
"""
from __future__ import annotations

import bisect
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from .directives import is_escaped
from .markdown import paragraph_text

if TYPE_CHECKING:
    from .models import Block, Document


def _block_shape(block: Block) -> tuple[Any, ...]:
    """What a source map describes of *block*: its kind, the length of each
    of its texts, and its items' blocks."""
    return (
        block.kind,
        *(len(text) for text in (block.text, block.prefix, block.suffix, block.term, block.target)),
        tuple(len(cell) for row in [block.headers, *block.rows] for cell in row),
        tuple(tuple(_block_shape(child) for child in getattr(item, "blocks", ())) for item in block.items),
    )


def document_shape(document: Document) -> tuple[Any, ...]:
    """The shape of *document*'s body — its blocks, their items, the length
    of their texts — which a source map describes and a document changed
    after parsing may no longer have."""
    return tuple(
        tuple(_block_shape(block) for block in blocks)
        for blocks in (document.preamble, *(section.blocks for section in document.sections))
    )


def frontmatter_keys(root: yaml.Node, first_line: int) -> dict[tuple[Any, ...], int]:
    """The line of each node in the frontmatter's YAML *root*, by its path:
    a mapping key's line, and a sequence entry's (its ``-`` line). A
    sequence's entries are counted as the model counts them, mappings only
    (``models``: a side, a party, an attachment, a representative or a
    custom field that is not a mapping is left out), so a path indexes the
    model's lists. *first_line* is the file line of the YAML's first line.
    A merged or aliased node is where it is written, not where it is used."""
    keys: dict[tuple[Any, ...], int] = {}

    def walk(node: yaml.Node, path: tuple[Any, ...], seen: frozenset[int]) -> None:
        if id(node) in seen:
            return  # an alias to a node already being walked
        seen = seen | {id(node)}
        if isinstance(node, yaml.MappingNode):
            merged: list[yaml.Node] = []
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.tag == "tag:yaml.org,2002:merge":
                    merged.append(value)  # its keys are this mapping's, below its own
                elif isinstance(key, yaml.ScalarNode):
                    inner = (*path, key.value)
                    keys.setdefault(inner, key.start_mark.line + first_line)
                    walk(value, inner, seen)
            for value in merged:
                for source in value.value if isinstance(value, yaml.SequenceNode) else [value]:
                    walk(source, path, seen)
        elif isinstance(node, yaml.SequenceNode):
            entries = [entry for entry in node.value if isinstance(entry, yaml.MappingNode)]
            for index, entry in enumerate(entries):
                inner = (*path, index)
                keys.setdefault(inner, entry.start_mark.line + first_line)
                walk(entry, inner, seen)

    walk(root, (), frozenset())
    return keys


@dataclass(slots=True)
class SourceMap:
    """Where a parsed document's frontmatter keys, headings and blocks lie:
    file lines, counted from 1. It describes the source as parsed: a
    document changed afterwards no longer fits it (``fits``)."""

    #: The source's lines, after its line endings were read as LF.
    lines: list[str]
    #: The index in ``lines`` of the body's first line.
    body_start: int
    #: Each frontmatter node's line, by its path (``frontmatter_keys``); the
    #: empty path is the opening ``---`` line. Empty without frontmatter.
    keys: dict[tuple[Any, ...], int]
    #: Where the parser found the body's headings and blocks (``parser._Layout``).
    layout: Any
    #: ``document_shape`` of the document as parsed.
    shape: tuple[Any, ...]

    def fits(self, document: Document) -> bool:
        """True if *document* still has the shape it was parsed with."""
        return self.shape == document_shape(document)

    def key(self, *path: Any) -> int | None:
        """The line of the frontmatter node at *path*, or of the nearest
        node above it that is written: the key that holds a missing one.
        None without frontmatter."""
        while path:
            if path in self.keys:
                return self.keys[path]
            path = path[:-1]
        return self.keys.get(())

    def first_key(self) -> int | None:
        """The frontmatter's first key line, or its opening line when it has
        none; None without frontmatter."""
        lines = [line for path, line in self.keys.items() if len(path) == 1]
        return min(lines) if lines else self.keys.get(())

    def heading(self, section: int) -> int:
        """The first line of section *section*'s heading."""
        return self.body_start + self.layout.headings[section].start + 1

    def span(self, section: int | None, index: int) -> Any:
        """Where top-level block *index* of the preamble (*section* None) or
        of a section lies (``parser._BlockSpan``, body lines from 0)."""
        spans = self.layout.preamble if section is None else self.layout.sections[section][1]
        return spans[index]

    def block(self, section: int | None, index: int) -> int:
        """The first line of a top-level block."""
        return self.body_start + self.span(section, index).start + 1

    def find(
        self,
        start: int,
        end: int,
        needle: str,
        texts: list[str],
        text: int | None,
        offset: int,
        nth: int,
        cache: dict[Any, Any] | None = None,
    ) -> int:
        """The line, among file lines ``[start, end)``, of *needle* — the
        source of a directive or marker — as the occurrence it is in *texts*,
        the model's texts of those lines in order: the one at *offset* in
        ``texts[text]``, or without a text, occurrence *nth* (from 0). A
        needle is looked for by its start, and then by its opener alone,
        since a text may join or unescape what the source splits or escapes;
        one not found is placed at *start*. *cache* keeps what is found of
        the same lines and texts (``Locator``): each is read once, so a block
        holding many directives is not read once for each."""
        lines = self.lines[start - 1:end - 1]
        cache = {} if cache is None else cache
        for candidate in _candidates(needle):
            if not candidate.startswith("{"):
                # Not a directive or marker (a quote's first line, raw HTML):
                # few in a block, looked for as they are.
                before = nth
                if text is not None:
                    before = sum(_count(earlier, candidate) for earlier in texts[:text])
                    before += _count(texts[text][:offset], candidate)
                line = _nth(lines, candidate, before)
            else:
                size = len(candidate)
                before = nth
                if text is not None:
                    places = _braces(cache, ("texts", id(texts), size), texts).get(candidate, [])
                    before = bisect.bisect_left(places, (text, offset))
                rows = _braces(cache, ("lines", start, end, size), lines).get(candidate, [])
                line = rows[before][0] if before < len(rows) else None
            if line is not None:
                return start + line
        return start


def _candidates(needle: str) -> list[str]:
    """What *needle* is looked for as: its start (24 characters, with a table
    cell's pipes escaped as the source writes them), then its opener."""
    head = needle[:24]
    found = [head]
    if "|" in head:
        found.append(head.replace("|", "\\|"))
    # A directive's opener is its name as written (``{{ ref``); a marker's, ``{``
    # and the character after it.
    colon = needle.find(":", 0, 40)
    opener = needle[:colon] if needle.startswith("{{") and colon > 2 else needle[:2]
    if opener and opener not in found:
        found.append(opener)
    return [candidate for candidate in found if candidate]


def _braces(cache: dict[Any, Any], key: tuple[Any, ...], texts: list[str]) -> dict[str, list[tuple[int, int]]]:
    """Where each unescaped ``{`` in *texts* starts which text of
    ``key[-1]`` characters: ``(text index, offset)`` in order, by that text.
    Kept in *cache* under *key*."""
    if key not in cache:
        size = key[-1]
        found: dict[str, list[tuple[int, int]]] = {}
        for number, text in enumerate(texts):
            at = text.find("{")
            while at >= 0:
                if not is_escaped(text, at):
                    found.setdefault(text[at:at + size], []).append((number, at))
                at = text.find("{", at + 1)
        cache[key] = found
    return cache[key]


def _count(text: str, needle: str) -> int:
    """How many unescaped occurrences of *needle* *text* holds."""
    count, at = 0, text.find(needle)
    while at >= 0:
        if not is_escaped(text, at):
            count += 1
        at = text.find(needle, at + len(needle))
    return count


def _nth(lines: list[str], needle: str, n: int) -> int | None:
    """The index in *lines* of the unescaped occurrence *n* (from 0) of
    *needle*, or None."""
    for number, line in enumerate(lines):
        at = line.find(needle)
        while at >= 0:
            if not is_escaped(line, at):
                if n == 0:
                    return number
                n -= 1
            at = line.find(needle, at + len(needle))
    return None


@dataclass(slots=True)
class _Leaf:
    """A block among a top-level block's blocks that holds text fragments:
    its file lines ``[start, end)``, its texts in source order, and where
    each of its ``block_fragments`` is: by number, the text it is in and its
    offset there."""

    start: int
    end: int
    texts: list[str]
    places: dict[int, tuple[int, int]]
    #: For a paragraph the parser lifted a directive out of (a definition's
    #: leading anchor, a ``{{ref:}}``, a ``{{term:}}``): the directive's
    #: offset in the paragraph's one text, else None.
    lifted: int | None = None


class Locator:
    """The lines of a document's parts, for its diagnostics (§16.9): None
    for every part when the document has no source map, or no longer fits
    the one it was parsed with."""

    def __init__(self, document: Document) -> None:
        source_map = document.source_map
        self._map: SourceMap | None = source_map if source_map is not None and source_map.fits(document) else None
        self._document = document
        self._leaves: dict[tuple[int | None, int], list[_Leaf] | None] = {}
        self._cache: dict[Any, Any] = {}  # what SourceMap.find reads, once

    def start(self) -> int | None:
        """The document's first line."""
        return 1 if self._map else None

    def key(self, *path: Any) -> int | None:
        """A frontmatter node's line, or the nearest written one above it."""
        return self._map.key(*path) if self._map else None

    def has(self, *path: Any) -> bool:
        """True if the frontmatter node at *path* is written."""
        return self._map is not None and path in self._map.keys

    def children(self, *path: Any) -> list[str]:
        """The keys written in the frontmatter mapping at *path*, as written
        (a padded key keeps its spaces), in the order of the source."""
        if self._map is None:
            return []
        n = len(path)
        return [k[n] for k in self._map.keys if len(k) == n + 1 and k[:n] == path and isinstance(k[n], str)]

    def field(self, name: str) -> int | None:
        """A top-level frontmatter key's line, or the frontmatter's first
        key's when it is not written: where it would go."""
        if self._map is None:
            return None
        return self._map.keys.get((name,)) or self._map.first_key()

    def heading(self, section: int) -> int | None:
        return self._map.heading(section) if self._map else None

    def block(self, section: int | None, index: int) -> int | None:
        return self._map.block(section, index) if self._map else None

    def find(
        self,
        section: int | None,
        index: int,
        needle: str,
        fragment: int | None = None,
        offset: int = 0,
        *,
        nth: int = 0,
    ) -> int | None:
        """The line of *needle* — a directive's or a marker's source — at
        *offset* in fragment *fragment* of a top-level block's
        ``block_fragments``, looked for in the lines of the block that holds
        that fragment; without a fragment, its occurrence *nth* (from 0) in
        the top-level block."""
        if self._map is None:
            return None
        span = self._map.span(section, index)
        start = self._map.body_start + span.start + 1
        end = self._map.body_start + span.end + 1
        if fragment is not None:
            for leaf in self._leaves_of(section, index) or ():
                if fragment in leaf.places:
                    text, base = leaf.places[fragment]
                    return self._map.find(
                        leaf.start, leaf.end, needle, leaf.texts, text, base + offset, 0, self._cache
                    )
        return self._map.find(start, end, needle, [], None, 0, nth, self._cache)

    def lifted(self, section: int | None, index: int, needle: str) -> int | None:
        """The line of the directive the parser lifted out of a top-level
        paragraph into its fields (a ``{{ref:}}``, a ``{{term:}}``, a
        definition's anchor): *needle* is its opener."""
        if self._map is None:
            return None
        leaves = self._leaves_of(section, index)
        if leaves and leaves[0].lifted is not None:
            leaf = leaves[0]
            return self._map.find(leaf.start, leaf.end, needle, leaf.texts, 0, leaf.lifted, 0, self._cache)
        return self.find(section, index, needle)

    def _leaves_of(self, section: int | None, index: int) -> list[_Leaf] | None:
        """The blocks of a top-level block that hold its ``block_fragments``,
        in order, as ``definitions.block_fragments`` reads them; None when
        the parsed layout and the model disagree."""
        key = (section, index)
        if key not in self._leaves:
            self._leaves[key] = self._read_leaves(section, index)
        return self._leaves[key]

    def _read_leaves(self, section: int | None, index: int) -> list[_Leaf] | None:
        # The parser, and so this module, builds on these modules.
        from .definitions import _own_fragments, block_fragments
        from .models import LIST_KINDS
        from .parser import MAX_QUOTE_DEPTH, quote_content

        assert self._map is not None
        blocks = self._document.preamble if section is None else self._document.sections[section].blocks
        top = blocks[index]
        body = self._map.body_start

        def walk(spans: Any, blocks: Any, depth: int, base: int) -> Iterator[tuple[Any, Block, int, int]]:
            for span, block in zip(spans, blocks, strict=True):
                yield span, block, depth, base
                if block.kind in LIST_KINDS:
                    for item_span, item in zip(span.items, block.items, strict=True):
                        yield from walk(item_span.blocks, item.blocks, depth + 1, base)
                elif block.kind == "quote" and depth < MAX_QUOTE_DEPTH:
                    children, inner = quote_content(block.text, depth + 1)
                    yield from walk(inner, children, depth + 1, base + span.start)

        leaves: list[_Leaf] = []
        count = 0
        try:
            for span, block, depth, base in walk([self._map.span(section, index)], [top], 0, 0):
                if block.kind in LIST_KINDS or (block.kind == "quote" and depth < MAX_QUOTE_DEPTH):
                    continue  # its blocks hold its text
                own = [text for text, _position in _own_fragments(block)]
                first, last = body + base + span.start + 1, body + base + span.end + 1
                if block.kind in ("definition", "ref", "term"):
                    leaf = _lifted(block, own, count, self._map.lines[first - 1:last - 1], first, last)
                    if leaf is None:
                        return None
                    leaves.append(leaf)
                elif own:
                    places = {count + k: (k, 0) for k in range(len(own))}
                    leaves.append(_Leaf(first, last, own, places))
                count += len(own)
        except ValueError:
            return None  # items or quote content that no longer match the source
        if count != len(block_fragments(top)):
            return None
        return leaves


def _lifted(block: Block, own: list[str], first: int, lines: list[str], start: int, end: int) -> _Leaf | None:
    """The leaf of a top-level paragraph the parser lifted a directive out
    of: its one text is its lines joined as the parser joins them, which
    holds the lifted directive as written between its fragments (a ref's or
    term's prefix and suffix; a definition's text, after its anchor). None
    when the fragments do not fit the source."""
    joined = paragraph_text(lines)
    places: dict[int, tuple[int, int]] = {}
    if block.kind == "definition":
        body = own[0] if own else ""
        if not joined.endswith(body):
            return None
        head = joined[:len(joined) - len(body)]
        if body:
            places[first] = (0, len(head))
        lifted = head.rfind("{{")
    else:
        prefix, suffix = block.prefix, block.suffix
        if not (joined.startswith(prefix) and joined.endswith(suffix)):
            return None
        number = first
        if block.text:  # not written by the parser
            return None
        if prefix:
            places[number] = (0, 0)
            number += 1
        if suffix:
            places[number] = (0, len(joined) - len(suffix))
        lifted = len(prefix)
    return _Leaf(start, end, [joined], places, lifted if lifted >= 0 else None)
