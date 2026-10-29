"""Where a parsed document's parts lie in its source, for diagnostics that
name their line (§16.9).

``parse`` gives each document a ``SourceMap``; a document built from
a dict has none, and its diagnostics carry no line.
"""
from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from .directives import is_escaped
from .markdown import LINE_ENDING_RE, find_all, paragraph_text

if TYPE_CHECKING:
    from .models import Block, Document


def _block_shape(block: Block) -> tuple[Any, ...]:
    """What a source map describes of *block*: its kind, the length of each
    of its texts, and its items' blocks."""
    return (
        block.kind,
        *(len(text) for text in (block.text, block.prefix, block.suffix, block.term, block.target, block.label, block.title)),
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


#: The last element of a mapping's path in ``frontmatter_keys`` when it
#: merges keys in (``<<``): at the merge key's line.
MERGE = object()


def line_breaks(text: str) -> list[int]:
    """The index of each line feed in *text*: the only line end of this format
    (YAML also ends a line at CR, NEL, LS and PS, so ``yaml.Mark.line`` is not
    the file's)."""
    return find_all(text, "\n")


def line_at(breaks: list[int], index: int, first_line: int) -> int:
    """The file line of character *index* of a text whose line feeds are
    *breaks* (``line_breaks``), the text's first line being *first_line*."""
    return first_line + bisect.bisect_left(breaks, index)


def frontmatter_keys(root: yaml.Node, breaks: list[int], first_line: int) -> dict[tuple[Any, ...], int]:
    """The line of each node in the frontmatter's YAML *root*, by its path:
    a mapping key's line, and a sequence entry's (its ``-`` line). A
    sequence's entries are counted as the model counts them, mappings only
    (``models``: a side, a party, an attachment, a representative or a
    custom field that is not a mapping is left out), so a path indexes the
    model's lists. *breaks* are the line feeds of the YAML's text (``line_breaks``) and
    *first_line* the file line of its first line. Lines are counted from the character
    index of a mark, at each LF: YAML also ends a line at CR, NEL, LS and PS,
    which the format does not, so ``mark.line`` would shift what follows one.
    A merged or aliased node is where it is written, not where it is used;
    a mapping that merges keys in has ``(*path, MERGE)`` too."""
    keys: dict[tuple[Any, ...], int] = {}

    def line_of(mark: yaml.Mark) -> int:
        return line_at(breaks, mark.index, first_line)

    def walk(node: yaml.Node, path: tuple[Any, ...], seen: frozenset[int]) -> None:
        if id(node) in seen:
            return  # an alias to a node already being walked
        seen = seen | {id(node)}
        if isinstance(node, yaml.MappingNode):
            merged: list[yaml.Node] = []
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.tag == "tag:yaml.org,2002:merge":
                    merged.append(value)  # its keys are this mapping's, below its own
                    keys.setdefault((*path, MERGE), line_of(key.start_mark))
                elif isinstance(key, yaml.ScalarNode):
                    inner = (*path, key.value)
                    keys.setdefault(inner, line_of(key.start_mark))
                    walk(value, inner, seen)
            for value in merged:
                for source in value.value if isinstance(value, yaml.SequenceNode) else [value]:
                    walk(source, path, seen)
        elif isinstance(node, yaml.SequenceNode):
            entries = [entry for entry in node.value if isinstance(entry, yaml.MappingNode)]
            for index, entry in enumerate(entries):
                inner = (*path, index)
                keys.setdefault(inner, line_of(entry.start_mark))
                walk(entry, inner, seen)

    walk(root, (), frozenset())
    return keys


#: The frontmatter maps whose keys the model reads stripped (``models._parse_str_dict``), each with
#: whether it skips an entry that is blank once stripped: two keys that strip alike are one entry (#126).
STRIPPED_MAPS = {"translations": True, "field_types": False}


@dataclass(frozen=True, slots=True)
class WrittenEntry:
    """An entry of a ``STRIPPED_MAPS`` map as written: its key's source text *written* (quotes, escapes and
    padding as they are in the file), the *key* the model holds it under (read as text and stripped), its
    *line* and *start* (the index of its text, which two entries share if they are one node), and what YAML
    *resolves* the key to (its tag and text, before the keys are read as text): two
    entries written alike resolve alike, and so do ``fr`` and ``"fr"``, but not ``1`` and ``'1'``."""

    written: str
    key: str
    line: int
    start: int
    resolved: tuple[str, str]


@dataclass(frozen=True, slots=True)
class Ignored:
    """An *entry* of a ``STRIPPED_MAPS`` map that the model does not hold, because of *by*, the entry it keeps
    for the key instead, or, if it keeps none, the last entry written, which it *skipped* for the reason given
    (an empty or blank key, a null, empty or blank value); "" if it keeps one. *why*: ``"repeated"`` (the same
    YAML key written again: invalid YAML), ``"text"`` (a different YAML key that the model reads as the same
    text, as ``1`` and ``'1'``), ``"stripped"`` (padding) or ``"value"`` (keys that Python holds as one though
    their text differs, as ``1`` and ``1.0``, or ``1`` and ``true``)."""

    entry: WrittenEntry
    by: WrittenEntry
    why: str
    skipped: str


@dataclass(frozen=True, slots=True)
class StrippedMap:
    """What the model made of a ``STRIPPED_MAPS`` map, entry by entry (``read_maps``): *stays*, the entry
    that stays under each key, and *ignored*, each entry that does not."""

    stays: dict[str, WrittenEntry]
    ignored: tuple[Ignored, ...]


#: An entry written in a ``STRIPPED_MAPS`` map: its key node, the key's source text, its line and start, and
#: what YAML resolves the key to.
_Written = tuple[yaml.Node, str, int, int, tuple[str, str]]


def written_maps(root: yaml.Node, block: str, breaks: list[int], first_line: int) -> dict[str, list[_Written]]:
    """The entries of each ``STRIPPED_MAPS`` map written in the frontmatter's YAML *root* (the last one, if the
    map is written twice, which is the one read), in the order of the source. *block* is the YAML's text, whose
    *breaks* are its line feeds (``line_breaks``). Taken before the keys are read as text, which changes what
    YAML resolved them to. An aliased key is one node, at its anchor's line and with its text, as in
    ``frontmatter_keys``."""
    found: dict[str, yaml.Node] = {}
    for name, value in root.value if isinstance(root, yaml.MappingNode) else ():
        if isinstance(name, yaml.ScalarNode) and name.value in STRIPPED_MAPS:
            found[name.value] = value
    return {
        name: [
            (
                key,
                block[key.start_mark.index : key.end_mark.index],
                line_at(breaks, key.start_mark.index, first_line),
                key.start_mark.index,
                (key.tag, key.value),
            )
            for key, _item in value.value
            if isinstance(key, yaml.ScalarNode) and key.tag != "tag:yaml.org,2002:merge"
        ]
        for name, value in found.items()
        if isinstance(value, yaml.MappingNode)
    }


def _why(text: str, other: str) -> str:
    """How two keys that the model holds as one differ: in nothing but how they are written (``"text"``), in
    padding, or in their value."""
    return "text" if text == other else "stripped" if text.strip() == other.strip() else "value"


def read_maps(
    entries: dict[str, list[_Written]], construct: Callable[[yaml.Node], Any], metadata: dict[str, Any]
) -> dict[str, StrippedMap]:
    """What the model makes of the maps ``written_maps`` found, replayed as the model reads them: a dict
    keyed by the key as read (keys that Python holds as one, as ``True`` and ``1`` do, take the last value, in
    the first's place), then ``models.str_dict_skip``, so that the entry that stays under each key the model
    holds is the one it holds. *construct* builds the Python value of a key node, as the YAML reader did,
    and *metadata* is what it made of the frontmatter, whose values are not built again (they may refer to
    themselves)."""
    from .models import str_dict_skip

    maps: dict[str, StrippedMap] = {}
    for name, written in entries.items():
        held = metadata.get(name)
        if not isinstance(held, dict):
            continue
        skip_blank = STRIPPED_MAPS[name]
        raw: dict[Any, list[tuple[_Written, str]]] = {}  # the entries of each key as read, with their own text
        for each in written:
            slot = construct(each[0])
            raw.setdefault(slot, []).append((each, str(slot)))
        # The model holds a slot under the first key written for it (the one the dict keeps), as text, stripped.
        slots = {
            slot: [(WrittenEntry(w, str(slot).strip(), line, start, res), text) for (_n, w, line, start, res), text in found]
            for slot, found in raw.items()
        }
        kept: dict[str, list[tuple[Any, WrittenEntry]]] = {}  # the slots the model keeps, by the key it holds them under
        for slot, found in slots.items():
            if not str_dict_skip(slot, held.get(slot), skip_blank=skip_blank):
                kept.setdefault(found[-1][0].key, []).append((slot, found[-1][0]))
        stays = {key: found[-1][1] for key, found in kept.items()}
        ignored = []
        for key, found in kept.items():  # a slot replaced by a later one that strips alike
            ignored += [Ignored(entry, stays[key], _why(str(slot), str(found[-1][0])), "") for slot, entry in found[:-1]]
        for slot, found in slots.items():  # the entries of a slot replaced by its last
            last, last_text = found[-1]
            by = stays.get(last.key, last)  # the entry the model keeps for the key, else the last written
            skipped = "" if last.key in stays else str_dict_skip(slot, held.get(slot), skip_blank=skip_blank)
            for place, (entry, text) in enumerate(found[:-1]):
                again = any(each.resolved == entry.resolved for each, _text in found[place + 1:])
                ignored.append(Ignored(entry, by, "repeated" if again else _why(text, last_text), skipped))
        maps[name] = StrippedMap(stays, tuple(sorted(ignored, key=lambda each: (each.entry.line, each.entry.start))))
    return maps


@dataclass(frozen=True, slots=True)
class BlockSpan:
    """Where a block lies in the file: lines ``[start, end)``, counted from 1.
    *kind* is the block's. *items*: a list's items, each with where it lies
    (the items of the list itself; a nested list's are in its item's blocks)."""

    kind: str
    start: int
    end: int
    items: tuple[ItemSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class ItemSpan:
    """Where a list item lies in the file: lines ``[start, end)``, its marker's
    line first, and where each block of its content lies."""

    start: int
    end: int
    blocks: tuple[BlockSpan, ...]


@dataclass(frozen=True, slots=True)
class HeadingSpan:
    """Where a heading lies in the file: lines ``[start, end)``, and the line
    its marker is on (an ATX heading's line, or a setext heading's last text
    line, where a ``{#id}`` or ``{when=...}`` marker is written, §5.2, §15.3)."""

    start: int
    end: int
    marker_line: int


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """Where a section's heading lies, and its top-level blocks."""

    heading: HeadingSpan
    blocks: tuple[BlockSpan, ...]


@dataclass(frozen=True, slots=True)
class SourceLayout:
    """Where a parsed document's parts lie in its file (``Document.layout``),
    in lines counted from 1: each span is the lines ``[start, end)``, its end
    exclusive — the line after its last — and the blank lines between parts
    belong to none, but for those between a list's items, which the item
    before holds. The frontmatter is ``(start, end)`` with its ``---``
    lines, or None without one; then the preamble's blocks (§4.4); each
    section's heading and blocks, in the document's order, so that
    ``sections[n]`` is ``Document.sections[n]``'s."""

    frontmatter: tuple[int, int] | None
    preamble: tuple[BlockSpan, ...]
    sections: tuple[SectionSpan, ...]


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
    #: Each ``STRIPPED_MAPS`` map as the model read it, by name (``read_maps``).
    maps: dict[str, StrippedMap] = field(default_factory=dict)

    def fits(self, document: Document) -> bool:
        """True if *document* still has the shape it was parsed with."""
        return self.shape == document_shape(document)

    def key(self, *path: Any) -> int | None:
        """The line of the frontmatter node at *path*, or of the nearest
        node above it that is written: the key that holds a missing one.
        None without frontmatter. An entry of a ``STRIPPED_MAPS`` map is found by the key
        the model holds it under, stripped: at the line of the entry that stays (#126)."""
        if len(path) == 2 and path[0] in self.maps and isinstance(path[1], str):
            stays = self.maps[path[0]].stays.get(path[1])
            if stays is not None:
                return stays.line
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
        # Not ``layout.headings[section]``, which lists every heading for each
        # call: a document of thousands of duplicate titles would be quadratic (#94).
        return self.body_start + self.layout.sections[section][0].start + 1

    def span(self, section: int | None, index: int) -> Any:
        """Where top-level block *index* of the preamble (*section* None) or
        of a section lies (``parser._BlockSpan``, body lines from 0)."""
        spans = self.layout.preamble if section is None else self.layout.sections[section][1]
        return spans[index]

    def block(self, section: int | None, index: int) -> int:
        """The first line of a top-level block."""
        return self.body_start + self.span(section, index).start + 1

    def to_layout(self) -> SourceLayout:
        """``layout`` as the public ``SourceLayout``, in file lines."""
        frontmatter = None
        if () in self.keys:
            # The frontmatter's lines, its closing ``---`` too, which the
            # source may end with no line ending after.
            from .parser import FRONTMATTER_RE

            written = FRONTMATTER_RE.match("\n".join(self.lines))
            if written is not None:
                text = written.group()
                frontmatter = (1, 1 + text.count("\n") + (0 if text.endswith("\n") else 1))
        return _public_layout(self.layout, self.body_start + 1, frontmatter)

    def item(self, section: int | None, index: int, item: int) -> int:
        """The line of list item *item* of top-level block *index* of a
        section (or of the preamble): its marker's line. Items are numbered
        in pre-order among all the list's items, nested ones included
        (``definitions.list_fragments``)."""
        span = self.span(section, index)
        if not span.items:
            raise ValueError("not a list")
        found = [each for top in span.items for each in _preorder(top)]
        if not 0 <= item < len(found):
            raise IndexError(f"list item {item} out of range")
        return self.body_start + found[item].start + 1

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


def _preorder(item: Any) -> Iterator[Any]:
    """The item span *item*, then the item spans of the lists in its
    blocks, each in the same order."""
    yield item
    for block in item.blocks:
        for inner in block.items:
            yield from _preorder(inner)


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
        # Per top-level block, its leaves (None when the layout and the model disagree) and, by the number of
        # a fragment, the leaf that holds it.
        self._leaves: dict[tuple[int | None, int], tuple[list[_Leaf] | None, dict[int, _Leaf]]] = {}
        self._definition_lines: dict[tuple[int | None, int], list[int]] = {}
        self._cache: dict[Any, Any] = {}  # what SourceMap.find reads, once

    def start(self) -> int | None:
        """The document's first line."""
        return 1 if self._map else None

    def key(self, *path: Any) -> int | None:
        """A frontmatter node's line, or the nearest written one above it."""
        return self._map.key(*path) if self._map else None

    def ignored(self) -> list[tuple[str, Ignored]]:
        """The entries of the ``STRIPPED_MAPS`` maps that the model does not hold because another takes their
        place, each with its map's name, in source order; none without a source map."""
        if not self._map:
            return []
        found = [(name, each) for name, held in self._map.maps.items() for each in held.ignored]
        return sorted(found, key=lambda item: (item[1].entry.line, item[1].entry.start))

    def has(self, *path: Any) -> bool:
        """True if the frontmatter node at *path* is written."""
        return self._map is not None and path in self._map.keys

    def merges(self, *path: Any) -> bool:
        """True if the frontmatter mapping at *path* merges keys in (``<<``)."""
        return self._map is not None and (*path, MERGE) in self._map.keys

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

    def first_key(self) -> int | None:
        """The frontmatter's first key's line, or its opening line when it has
        no key; None without frontmatter."""
        return self._map.first_key() if self._map else None

    def heading(self, section: int) -> int | None:
        return self._map.heading(section) if self._map else None

    def setext_section(self, section: int) -> bool:
        """Whether the heading of section *section* is written in the setext form (§4.1): its first
        line is not an ATX heading's. False when that is not known (no source map, or the document
        no longer fits it): a model is written in the ATX form."""
        return self._map is not None and not _is_atx(self._map.lines[self._map.heading(section) - 1])

    def setext_fragment(self, section: int | None, index: int, fragment: int) -> bool:
        """Whether fragment *fragment* of top-level block *index*, the text of a ``heading`` block
        among its blocks (in a list item or a quote), is written in the setext form. False when
        that is not known, as for ``setext_section``."""
        if self._map is None:
            return False
        leaf = self._leaf_for(section, index, fragment)
        return leaf is not None and not _is_atx(self._map.lines[leaf.start - 1])

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
        if fragment is not None and (leaf := self._leaf_for(section, index, fragment)):
            text, base = leaf.places[fragment]
            return self._map.find(leaf.start, leaf.end, needle, leaf.texts, text, base + offset, 0, self._cache)
        return self._map.find(start, end, needle, [], None, 0, nth, self._cache)

    def definition(self, section: int | None, index: int, ordinal: int) -> int | None:
        """The first line of the link reference definition (#92) with *ordinal*
        (``definitions.PlacedLinkDefinition``) among the ``link_definition`` blocks
        of a top-level block, in an item or a quote too: the lines of its own
        block, not a search for its text. None without a source map, or when the
        document no longer fits it."""
        if self._map is None:
            return None
        key = (section, index)
        if key not in self._definition_lines:
            blocks = self._document.preamble if section is None else self._document.sections[section].blocks
            body = self._map.body_start
            try:
                self._definition_lines[key] = [
                    body + base + span.start + 1
                    for span, block, _depth, base in _walk_spans([self._map.span(section, index)], [blocks[index]], 0, 0)
                    if block.kind == "link_definition"
                ]
            except ValueError:
                self._definition_lines[key] = []  # items or quote content that no longer match the source
        lines = self._definition_lines[key]
        return lines[ordinal] if ordinal < len(lines) else None

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

    def _leaf_for(self, section: int | None, index: int, fragment: int) -> _Leaf | None:
        """The leaf of top-level block *index* that holds fragment *fragment* of its ``block_fragments``,
        found by a map made once for the block: a list of thousands of items is not scanned for each
        fragment. None when the parsed layout and the model disagree."""
        return self._block_leaves(section, index)[1].get(fragment)

    def _leaves_of(self, section: int | None, index: int) -> list[_Leaf] | None:
        """The blocks of a top-level block that hold its ``block_fragments``,
        in order, as ``definitions.block_fragments`` reads them; None when
        the parsed layout and the model disagree."""
        return self._block_leaves(section, index)[0]

    def _block_leaves(
        self, section: int | None, index: int
    ) -> tuple[list[_Leaf] | None, dict[int, _Leaf]]:
        """``_leaves_of``, and the leaf of each fragment by its number, read once for the block."""
        key = (section, index)
        if key not in self._leaves:
            leaves = self._read_leaves(section, index)
            self._leaves[key] = (leaves, {number: leaf for leaf in leaves or () for number in leaf.places})
        return self._leaves[key]

    def _read_leaves(self, section: int | None, index: int) -> list[_Leaf] | None:
        # The parser, and so this module, builds on these modules.
        from .definitions import _own_fragments, block_fragments
        from .models import LIST_KINDS
        from .parser import MAX_QUOTE_DEPTH

        assert self._map is not None
        blocks = self._document.preamble if section is None else self._document.sections[section].blocks
        top = blocks[index]
        body = self._map.body_start

        leaves: list[_Leaf] = []
        count = 0
        try:
            for span, block, depth, base in _walk_spans([self._map.span(section, index)], [top], 0, 0):
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


# A line that opens an ATX heading (§4.1), after the block quote and list item markers it may be in.
_ATX_LINE_RE = re.compile(r"(?:[ \t]*(?:>|(?:[-*+]|[0-9]{1,9}[.)])(?=[ \t])))*[ \t]*#{1,6}(?:[ \t]|$)")


def _is_atx(line: str) -> bool:
    return _ATX_LINE_RE.match(line) is not None


def _walk_spans(spans: Any, blocks: Any, depth: int, base: int) -> Iterator[tuple[Any, Block, int, int]]:
    """Every block of *blocks* and of their list items and block quotes, in the order
    ``definitions.nested_blocks`` gives, each as ``(span, block, depth, base)``: its
    span (``parser._BlockSpan``), the items and quotes it is in, and the line
    offset *base* its span is counted from (a quote's content is counted from the
    quote's first line)."""
    from .models import LIST_KINDS
    from .parser import MAX_QUOTE_DEPTH, quote_content

    for span, block in zip(spans, blocks, strict=True):
        yield span, block, depth, base
        if block.kind in LIST_KINDS:
            for item_span, item in zip(span.items, block.items, strict=True):
                yield from _walk_spans(item_span.blocks, item.blocks, depth + 1, base)
        elif block.kind == "quote" and depth < MAX_QUOTE_DEPTH:
            children, inner = quote_content(block.text, depth + 1)
            yield from _walk_spans(inner, children, depth + 1, base + span.start)


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


def _public_layout(layout: Any, base: int, frontmatter: tuple[int, int] | None) -> SourceLayout:
    """The parser's *layout* (body lines from 0) as a ``SourceLayout`` whose
    lines are counted from *base*, the line of the body's first."""

    def block(span: Any) -> BlockSpan:
        return BlockSpan(span.kind, base + span.start, base + span.end, tuple(item(each) for each in span.items))

    def item(span: Any) -> ItemSpan:
        return ItemSpan(base + span.start, base + span.end, tuple(block(each) for each in span.blocks))

    return SourceLayout(
        frontmatter,
        tuple(block(each) for each in layout.preamble),
        tuple(
            SectionSpan(
                HeadingSpan(base + heading.start, base + heading.end, base + heading.marker_line),
                tuple(block(each) for each in blocks),
            )
            for heading, blocks in layout.sections
        ),
    )


def body_layout(text: str) -> SourceLayout:
    """Where the parts of *text* lie, read as a document body alone: a
    ``SourceLayout`` of lines counted from 1 at the start of *text*, with no
    frontmatter — a ``---`` first line is a thematic break, not the opening
    of one. For text a tool holds apart from the file it came from, such as
    an editor's field; ``Document.layout`` is the layout of a parsed
    document. Line endings are LF, CR or CRLF, as the parser reads them."""
    from .parser import _layout  # the parser builds on this module

    lines = LINE_ENDING_RE.sub("\n", text).split("\n")
    if lines[-1] == "":
        lines.pop()  # the last line's ending, not a line
    return _public_layout(_layout(lines), 1, None)


def source_layout(document: Document) -> SourceLayout | None:
    """``Document.layout``: where *document*'s parts lie in the source it was
    parsed from; None without a source map, or when the document no longer
    has the shape it was parsed with."""
    source_map = document.source_map
    if source_map is None or not source_map.fits(document):
        return None
    return source_map.to_layout()


def line_of(document: Document, section: int | None, block: int | None, item: int | None) -> int | None:
    """``Document.line_of``."""
    from .models import LIST_KINDS, list_items

    if section is not None and not 0 <= section < len(document.sections):
        raise IndexError(f"section {section} out of range")
    if block is None and item is not None:
        raise ValueError("a list item is named by its block")
    if block is None and section is None:
        raise ValueError("the preamble has no heading")
    if block is not None:
        blocks = document.preamble if section is None else document.sections[section].blocks
        if not 0 <= block < len(blocks):
            raise IndexError(f"block {block} out of range")
        if item is not None:
            if blocks[block].kind not in LIST_KINDS:
                raise ValueError("not a list")

            def count(items: list[Any]) -> int:
                return sum(
                    1 + sum(count(list_items(child)) for child in each.blocks if child.kind in LIST_KINDS)
                    for each in items
                )

            if not 0 <= item < count(list_items(blocks[block])):
                raise IndexError(f"list item {item} out of range")
    source_map = document.source_map
    if source_map is None or not source_map.fits(document):
        return None
    if block is None:
        return source_map.heading(section)  # type: ignore[arg-type]
    if item is None:
        return source_map.block(section, block)
    return source_map.item(section, block, item)
