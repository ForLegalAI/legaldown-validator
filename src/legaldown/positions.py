"""Where a parsed document's parts lie in its source, for diagnostics that
name their line (§16.9).

``parse`` gives each document a ``SourceMap``; a document built from
a dict has none, and its diagnostics carry no line.
"""
from __future__ import annotations

import bisect
import dataclasses
import json
import re
from array import array
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from itertools import accumulate
from typing import TYPE_CHECKING, Any, NamedTuple

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
    #: True for an entry merged in (``<<``) from another mapping (#133): its *line* is then that of the ``<<`` of
    #: the map that merges it, where the map reads it, and *merged_from* the line where it is written.
    merged: bool = False
    merged_from: int | None = None


@dataclass(frozen=True, slots=True)
class Ignored:
    """An *entry* of a ``STRIPPED_MAPS`` map that the model does not hold, because of *by*: the entry that
    repeats it, the entry the model keeps for the key instead, or the last entry written of the key, which the
    model *skipped* for the reason given (an empty or blank key, a null, empty or blank value); *skipped* is ""
    when *by* is not skipped. *kept* is the entry the model keeps for the key, None if it keeps none: when *by*
    is skipped and the model keeps an entry all the same, it is one that strips alike, written before *by* or
    after it (#133). *itself* is the reason the model skips *entry* itself, when it is the last of its key and loses
    to such an earlier entry; "" otherwise. *why*: ``"repeated"`` (the same YAML key written again: invalid
    YAML), ``"text"`` (a different YAML key that the model reads as the same text, as ``1`` and ``'1'``),
    ``"stripped"`` (padding) or ``"value"`` (keys that Python holds as one though their text differs, as ``1``
    and ``1.0``, or ``1`` and ``true``)."""

    entry: WrittenEntry
    by: WrittenEntry
    why: str
    skipped: str
    kept: WrittenEntry | None = None
    itself: str = ""


@dataclass(frozen=True, slots=True)
class StrippedMap:
    """What the model made of a ``STRIPPED_MAPS`` map, entry by entry (``read_maps``): *stays*, the entry
    that stays under each key, and *ignored*, each entry that does not."""

    stays: dict[str, WrittenEntry]
    ignored: tuple[Ignored, ...]


class _Written(NamedTuple):
    """An entry of a ``STRIPPED_MAPS`` map: its key node, the key's source text, its line and start, and whether
    it is merged in (``<<``) from another mapping."""

    node: yaml.Node
    text: str
    line: int
    start: int
    merged: bool = False
    merged_from: int | None = None


#: Where each mapping key written as an alias is (``parser._StrDateSafeLoader.alias_keys``): by the id of its
#: entry, the entry and the start and end index of the alias in the YAML's text.
AliasKeys = dict[int, tuple[tuple[yaml.Node, yaml.Node], int, int]]


def _key_span(entry: tuple[yaml.Node, yaml.Node], aliases: AliasKeys | None) -> tuple[int, int]:
    """The start and end index of the key of mapping entry *entry* where it is written: an aliased key at its
    alias (``*k``), as *aliases* has it, not at its anchor, whose node it shares (#133)."""
    held = aliases.get(id(entry)) if aliases else None
    if held is not None and held[0] is entry:
        return held[1], held[2]
    return entry[0].start_mark.index, entry[0].end_mark.index


def _key_text(block: str, key: yaml.Node) -> str:
    """The source text of the key node *key* in the YAML *block*: quotes, escapes and an anchor as written."""
    return block[key.start_mark.index : key.end_mark.index]


def stripped_nodes(root: yaml.Node) -> dict[str, yaml.Node]:
    """The value node of each ``STRIPPED_MAPS`` map written at the top of the frontmatter's YAML *root* (the last
    one, if the map is written twice, which is the one read)."""
    found: dict[str, yaml.Node] = {}
    for name, value in root.value if isinstance(root, yaml.MappingNode) else ():
        if isinstance(name, yaml.ScalarNode) and name.value in STRIPPED_MAPS:
            found[name.value] = value
    return found


def written_maps(
    root: yaml.Node,
    block: str,
    breaks: list[int],
    first_line: int,
    nodes: dict[str, yaml.Node] | None = None,
    loader: Any = None,
) -> dict[str, list[_Written]]:
    """The entries of each ``STRIPPED_MAPS`` map written in the frontmatter's YAML *root* (the last one, if the
    map is written twice, which is the one read). *block* is the YAML's text, whose *breaks* are its line feeds
    (``line_breaks``); *nodes* are the maps found already (``stripped_nodes``). Without a *loader*, the entries
    written in the map, in the order of the source, an aliased key at its anchor's line and with its text. With
    the parser's *loader* (``parser._StrDateSafeLoader``), each map is flattened, and its entries are those of
    the mapping the reader builds the map from: the entries merged in (``<<``) first, each marked as merged, at
    the line of the ``<<`` that merges it into this map (the first, if more than one has it) and with the line
    where it is written, then those written in the map (#133); an aliased key is at its alias."""
    found: dict[str, list[_Written]] = {}
    for name, value in (stripped_nodes(root) if nodes is None else nodes).items():
        if not isinstance(value, yaml.MappingNode):
            continue
        own: set[int] | None = None
        aliases: AliasKeys | None = None
        merged_at: dict[int, int] = {}  # the line of the `<<` that merges each entry in, by the entry's id
        if loader is not None:
            loader.flatten_mapping(value)
            own = {id(entry) for entry in loader.merges.get(value, (value.value, []))[0]}
            aliases = loader.alias_keys
            for merge in loader.merge_keys.get(value, []):
                at = line_at(breaks, _key_span(merge, aliases)[0], first_line)
                sources = merge[1].value if isinstance(merge[1], yaml.SequenceNode) else [merge[1]]
                for source in sources:
                    for pair in source.value if isinstance(source, yaml.MappingNode) else ():
                        merged_at.setdefault(id(pair), at)
        entries = []
        for entry in value.value:
            key = entry[0]
            if not isinstance(key, yaml.ScalarNode) or key.tag == "tag:yaml.org,2002:merge":
                continue
            start, end = _key_span(entry, aliases)
            line = line_at(breaks, start, first_line)
            if own is not None and id(entry) not in own:
                entries.append(_Written(key, block[start:end], merged_at.get(id(entry), line), start, True, line))
            else:
                entries.append(_Written(key, block[start:end], line, start))
        found[name] = entries
    return found


def _why(text: str, other: str) -> str:
    """How two keys that the model holds as one differ: in nothing but how they are written (``"text"``), in
    padding, or in their value."""
    return "text" if text == other else "stripped" if text.strip() == other.strip() else "value"


def _judged(entry: WrittenEntry) -> bool:
    """Whether ``read_maps`` reports *entry*, which a later entry of its slot (the same key to the model's dict)
    takes the place of: not an entry merged in (``<<``), which is merge precedence then, as in every other mapping
    (``repeated_keys``), or a repeat in its source, which the source's check reports. Entries of two slots that
    the model reads as one once stripped or read as text are two keys to YAML, and always reported (#133)."""
    return not entry.merged


def read_maps(
    entries: dict[str, list[_Written]],
    construct: Callable[[yaml.Node], Any],
    metadata: dict[str, Any],
    resolved: dict[yaml.Node, str] | None = None,
) -> dict[str, StrippedMap]:
    """What the model makes of the maps ``written_maps`` found, replayed as the model reads them: a dict
    keyed by the key as read (keys that Python holds as one, as ``True`` and ``1`` do, take the last value, in
    the first's place), then ``models.str_dict_skip``, so that the entry that stays under each key the model
    holds is the one it holds. *construct* builds the Python value of a key node, as the YAML reader did,
    and *metadata* is what it made of the frontmatter, whose values are not built again (they may refer to
    themselves). *resolved* is the tag YAML gave each key that was read as text instead
    (``parser._StrDateSafeLoader.resolved``): the key's own tag otherwise."""
    from .models import str_dict_skip

    tags = resolved or {}

    maps: dict[str, StrippedMap] = {}
    for name, written in entries.items():
        held = metadata.get(name)
        if not isinstance(held, dict):
            continue
        skip_blank = STRIPPED_MAPS[name]
        raw: dict[Any, list[tuple[_Written, str]]] = {}  # the entries of each key as read, with their own text
        for each in written:
            slot = construct(each.node)
            raw.setdefault(slot, []).append((each, str(slot)))
        # The model holds a slot under the first key written for it (the one the dict keeps), as text, stripped.
        slots = {
            slot: [
                (
                    WrittenEntry(
                        each.text,
                        str(slot).strip(),
                        each.line,
                        each.start,
                        (tags.get(each.node, each.node.tag), each.node.value),
                        each.merged,
                        each.merged_from,
                    ),
                    text,
                )
                for each, text in found
            ]
            for slot, found in raw.items()
        }
        reasons = {slot: str_dict_skip(slot, held.get(slot), skip_blank=skip_blank) for slot in slots}  # "": kept
        order = {slot: place for place, slot in enumerate(slots)}  # the order in which the model reads the slots
        kept: dict[str, list[tuple[Any, WrittenEntry]]] = {}  # the slots the model keeps, by the key it holds them under
        for slot, found in slots.items():
            if not reasons[slot]:
                kept.setdefault(found[-1][0].key, []).append((slot, found[-1][0]))
        stays = {key: found[-1][1] for key, found in kept.items()}
        ignored: list[Ignored] = []
        for key, found in kept.items():  # a slot replaced by a later one that strips alike
            for slot, entry in found[:-1]:
                # Two slots are two keys to YAML, merged in or not: the model alone reads them as one.
                ignored.append(Ignored(entry, stays[key], _why(str(slot), str(found[-1][0])), "", stays[key]))
        for slot, found in slots.items():  # the entries of a slot replaced by its last
            last, last_text = found[-1]
            keeps = kept.get(last.key, [])
            # A slot whose last entry is skipped, after a slot that strips alike and that the model keeps: the
            # entry it would have replaced is read instead, and that last entry is reported (#133). A skipped last
            # entry before the slot kept is replaced by it anyway, and is no collision itself then (#116); the
            # entries of its slot before it are still reported below.
            loses = bool(reasons[slot]) and bool(keeps) and order[keeps[-1][0]] < order[slot]
            if loses:  # two slots, as above
                why = _why(str(slot), str(keeps[-1][0]))
                ignored.append(Ignored(last, stays[last.key], why, "", stays[last.key], reasons[slot]))
            # The slot's last entry, when skipped, is named with the entry the model keeps for the key, if any,
            # written before it or after (#133).
            skipped = reasons[slot]
            by = last if skipped else stays[last.key]  # the entry the model keeps for the key, else the last written
            for place, (entry, text) in enumerate(found[:-1]):
                # A key written again is named by the entry that repeats it, unless that is the skipped last entry
                # (or, when the model keeps no entry of the key, any entry of a slot whose last is skipped).
                again = next((each for each, _text in found[place + 1:] if each.resolved == entry.resolved), None)
                why = "repeated" if again else _why(text, last_text)
                named = again if again and (not skipped or (keeps and again is not last)) else by
                if _judged(entry):
                    ignored.append(Ignored(entry, named, why, skipped if named is last else "", stays.get(entry.key)))
        maps[name] = StrippedMap(stays, tuple(sorted(ignored, key=lambda each: (each.entry.line, each.entry.start))))
    return maps


@dataclass(frozen=True, slots=True)
class Repeated:
    """A key of a frontmatter mapping that the model does not hold, because a later key of the mapping takes
    its place (#131): its *written* text, *line* and *start*, in the mapping at *path* (``""`` for the root).
    *why*: ``"repeated"`` (the same YAML key written again, of the same YAML type: invalid YAML), ``"text"`` (a
    different YAML key that the model reads as the same text, as ``1`` and ``'1'``), ``"value"`` (keys of
    other types that Python holds as one, in a question's ``default``, where the YAML types stay: ``1`` and
    ``1.0``) or ``"merge"`` (a merge key ``<<`` written again in the mapping, which the reader merges all the
    same, #133). *by_written* and *by_line* are the key that repeats it, or the one the model holds (always a line: an
    aliased key is at its alias, #133). An aliased
    key is where its alias is written (#133). *field* is the top-level frontmatter key the mapping is under (the
    key itself in the root), whose part of the model the finding describes (``Locator.repeats``, #133), None
    if it is no text or if the frontmatter shares a collection (an alias, a ``<<`` source), when it may be held
    under more than one field and is judged against the whole metadata; *mapping_line* and *mapping_column* are where the mapping starts, which tells two mappings
    apart whose paths are cut alike (#133)."""

    path: str
    written: str
    line: int
    start: int
    why: str
    by_written: str
    by_line: int
    field: str | None = None
    mapping_line: int | None = None
    mapping_column: int | None = None


_PLAIN_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


def _children(value: Any) -> Any:
    """What a metadata value holds, for ``metadata_snapshot``: the fields of a model, the entries of a list, a
    tuple or a set, the keys and values of a dict; None for any other."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [getattr(value, each.name) for each in dataclasses.fields(value)]
    if isinstance(value, (list, tuple, set, frozenset)):
        return value
    if isinstance(value, dict):
        return [part for pair in value.items() for part in pair]
    return None


def metadata_snapshot(metadata: Any) -> Iterator[Any]:
    """The objects of *metadata* and the size of each container, in a fixed order: a value that is replaced, and
    a list, dict or model that gains or loses an entry, gives another sequence (``metadata_unchanged``). An object
    shared or referring to itself is entered once, and the work is linear in the objects held. Never reads the
    value of a scalar, which may be a number too large to write."""
    seen: set[int] = set()
    stack = [metadata]
    while stack:
        value = stack.pop()
        yield value
        children = _children(value)
        if children is not None and id(value) not in seen:
            seen.add(id(value))
            children = list(children)
            yield len(children)
            stack.extend(reversed(children))


def metadata_unchanged(metadata: Any, snapshot: list[Any]) -> bool:
    """True if *metadata* is as ``metadata_snapshot`` found it, kept in *snapshot*: every object is the same one
    (the snapshot holds them, so none can have been replaced by another at its address), and every container the
    same size."""
    held = iter(snapshot)
    for now in metadata_snapshot(metadata):
        then = next(held, held)
        if then is not now and not (type(then) is int and type(now) is int and then == now):
            return False
    return next(held, held) is held


#: The key of ``metadata_fields_snapshot`` that holds the snapshot of the whole metadata (no field is a number).
WHOLE_METADATA = 0


def metadata_fields_snapshot(metadata: Any, repeats: Sequence[Repeated]) -> dict[str | int | None, list[Any]]:
    """What ``Locator.repeats`` compares to tell whether the findings *repeats* still describe *metadata*
    (#133): under None, the metadata object itself; for each field of it that a finding is under
    (``Repeated.field``), ``metadata_snapshot`` of that field's value; and under ``WHOLE_METADATA``, when a
    finding is under no one field (``Repeated.field`` None), that of the whole metadata. A finding under a key
    that is no field of the model (a key it does not read) depends on the metadata object alone."""
    names = {each.name for each in dataclasses.fields(metadata)} if dataclasses.is_dataclass(metadata) else set()
    seen: dict[str | int | None, list[Any]] = {None: [metadata]}
    for each in repeats:
        if each.field is None:
            if WHOLE_METADATA not in seen:
                seen[WHOLE_METADATA] = list(metadata_snapshot(metadata))
        elif each.field in names and each.field not in seen:
            seen[each.field] = list(metadata_snapshot(getattr(metadata, each.field)))
    return seen


_SEGMENT_WIDTH = 40  # the most of a key that a step of a path writes out: a key may be as long as the frontmatter


def _segment(slot: Any, key: yaml.Node, block: str) -> str:
    """The step into the value of the entry whose key is the node *key*, held as *slot*, in a path
    (``repeated_keys``): ``.name`` for a plain name, else a text in brackets, escaped as JSON, and any other key
    (of a question's ``default``) as it is written, in braces, so that it is not taken for a position in a
    sequence, which is ``[1]``. A key that is not text is not written out from its value, which may be too large.
    A key longer than ``_SEGMENT_WIDTH`` is cut short (``…``) before it is escaped and the step closed, so that a
    long key is not copied into every finding under it, no escape is split and no bracket is left open. A letter
    stays as it is written, but a character that cannot be written out (a lone surrogate that an escape makes) is
    escaped."""
    text = slot if isinstance(slot, str) else key.value if isinstance(key, yaml.ScalarNode) else _key_text(block, key)
    head, more = text[:_SEGMENT_WIDTH], "…" if len(text) > _SEGMENT_WIDTH else ""
    if not isinstance(slot, str):
        step = "{" + head + more + "}"
    elif _PLAIN_KEY_RE.fullmatch(slot):
        step = "." + head + more
    else:
        step = '["' + json.dumps(head, ensure_ascii=False)[1:-1] + more + '"]'
    return step.encode("utf-8", "backslashreplace").decode("utf-8")


def _path_text(path: tuple[Any, ...] | None, block: str) -> str:
    """The path of a mapping as text. A path is the steps from the root, each linked to the one before it, so
    that a step is made for a mapping or sequence entered, not for each entry: ``(before, step)``, a step being
    ``("k", slot, key)``, ``("i", position)`` for a sequence entry or ``("m", position, merge)`` for a source of a
    merge, whose position is None unless the sources are a list, and *merge* the number of its ``<<`` (from 1)
    when the mapping writes more than one, None otherwise (``x.<<#2[0]``, #133)."""
    steps = []
    while path is not None:
        path, (kind, *step) = path
        if kind == "k":
            steps.append(_segment(step[0], step[1], block))
        elif kind == "i":
            steps.append(f"[{step[0]}]")
        else:
            number = "" if len(step) < 2 or step[1] is None else f"#{step[1]}"
            steps.append(f".<<{number}" if step[0] is None else f".<<{number}[{step[0]}]")
    return "".join(reversed(steps)).removeprefix(".")


_NO_SLOT = object()  # the slot of a key that cannot be held in a dict (in an ``!!omap`` or ``!!pairs``, where it can be)


def repeated_keys(
    root: yaml.Node,
    block: str,
    breaks: list[int],
    first_line: int,
    construct: Callable[[yaml.Node], Any],
    merges: dict[yaml.Node, tuple[list[tuple[yaml.Node, yaml.Node]], list[yaml.Node]]],
    resolved: dict[yaml.Node, str],
    skip: set[yaml.Node],
    aliases: AliasKeys | None = None,
    merge_keys: dict[yaml.Node, list[tuple[yaml.Node, yaml.Node]]] | None = None,
) -> tuple[Repeated, ...]:
    """Each key written in a mapping of the frontmatter's YAML *root* that a later key of the same mapping
    replaces (#131), as the model's dict does: the keys that ``construct`` builds equal are one, and the last
    stays. Run after the document is built, so every mapping reached has been flattened (``_read_as_written``
    and the reader's own recursion into a ``<<`` source). That puts the entries of the sources first, as the
    reader flattens them, and the mapping's own after, and the last entry of a key that the flattened mapping
    has is the one held: which entry of a source that is is read off the flattened mapping, not worked out,
    since PyYAML releases differ (6.0.4 reads a list of sources first to last and keeps a source's first
    entry of a key it has met; earlier ones read a list last to first and keep a source's last entry). *merges* holds, for each mapping that merges keys
    in, the entries written in it and the sources it merges (``parser._StrDateSafeLoader.flatten_mapping``). A
    source is judged as a mapping of its own, but only for the entries that are held in the mapping that merges
    it: an entry that a later one takes the place of, a key written in the mapping or a later source, is never
    read, and neither is its value. A key written over a merged one is no repeat. *resolved* is the tag YAML gave
    a scalar before it was read as text, which tells the same key written again from a different one. The own
    entries of the mappings in *skip* (``translations`` and ``field_types``, whose entries ``read_maps`` judges)
    are left out. An entry that is dropped is dropped whole: what its value holds is not looked at. A mapping
    shared through an alias is judged once, under the first path that reaches it. A key repeated many times is
    found in one pass over its entries, so the work is linear. The key of a pair in an ``!!omap`` or
    ``!!pairs`` may be a sequence or a mapping, which no dict holds: it is never a repeat. The path of a mapping
    names the keys and sequence positions above it, and ``<<`` for a source (``sides[0].parties[1]``,
    ``x.<<[0]``); it is written out for a mapping with a finding alone. A key written as an alias is where the
    alias is, as *aliases* has it (``parser._StrDateSafeLoader.alias_keys``), and a merge key written again in a
    mapping, as *merge_keys* has them (``parser._StrDateSafeLoader.merge_keys``), is reported too (#133)."""
    found: list[Repeated] = []
    reported: set[tuple[int, int]] = set()  # a dropped entry in its mapping, which may be walked for other owners too
    # A node is judged once as a mapping of its own, and once for each mapping that merges it.
    seen: set[tuple[int, int]] = set()
    slots: dict[int, Any] = {}  # the key each key node builds, once
    holds: dict[int, set[int]] = {}  # for a mapping that merges keys in: the entries it holds, by id

    def slot_of(key: yaml.Node) -> Any:
        if id(key) not in slots:
            slot = construct(key)
            try:
                hash(slot)
            except TypeError:
                slot = _NO_SLOT
            slots[id(key)] = slot
        return slots[id(key)]

    def held_entries(entries: list[tuple[yaml.Node, yaml.Node]]) -> set[int]:
        """The entries that a dict built from *entries* holds: the last of each key."""
        held: set[int] = set()
        taken: set[Any] = set()
        for entry in reversed(entries):
            slot = slot_of(entry[0])
            if slot is _NO_SLOT or slot not in taken:
                taken.add(slot)
                held.add(id(entry))
        return held

    # A collection reached twice (an alias, a `<<` source) is part of more than one field of the model.
    shared = bool(merges)
    Item = tuple[yaml.Node, "tuple[Any, ...] | None", "yaml.Node | None"]  # node, path, mapping it is merged into
    containers = (yaml.MappingNode, yaml.SequenceNode)
    stack: list[Item] = [(root, None, None)]
    while stack:
        node, path, owner = stack.pop()
        if (id(node), id(owner) if owner else 0) in seen or (owner and (id(node), 0) in seen):
            shared = True
            continue
        seen.add((id(node), id(owner) if owner else 0))
        children: list[Item] = []
        if isinstance(node, yaml.SequenceNode):
            children = [(entry, (path, ("i", index)), None) for index, entry in enumerate(node.value) if isinstance(entry, containers)]
        elif isinstance(node, yaml.MappingNode):
            own, sources = merges.get(node, (node.value, []))
            if owner is None and sources:
                owner = node
                holds[id(node)] = held_entries(node.value)
            held = holds[id(owner)] if owner else None
            groups: dict[Any, list[tuple[yaml.Node, yaml.Node]]] = {}
            for entry in own:
                slot = slot_of(entry[0])
                if slot is _NO_SLOT:
                    if isinstance(entry[1], containers):
                        children.append((entry[1], (path, ("k", slot, entry[0])), None))
                else:
                    groups.setdefault(slot, []).append(entry)
            mapping = _Mapping(node, path, block, breaks, first_line)
            merged_twice = (merge_keys or {}).get(node, [])
            for entry, again in zip(merged_twice, merged_twice[1:], strict=False):
                # `<<` written again: one key to YAML, which a strict reader rejects; this one merges both (#133).
                if (id(node), id(entry)) not in reported:
                    reported.add((id(node), id(entry)))
                    found.append(mapping.repeat(entry, "merge", again, aliases))
            for slot, group in groups.items():
                if held is not None:
                    # The entry the mapping that merges this one holds, as the reader flattened it: the last one
                    # the flattened mapping has of the key. PyYAML 6.0.4 keeps a source's first entry of a key
                    # it has met (by the key's text object) and reads a list of sources first to last; earlier
                    # releases read a list last to first and keep a source's last entry.
                    kept = [entry for entry in group if id(entry) in held]
                    if not kept:
                        continue  # an entry of a later source, or of the mapping, takes its place: not read
                    group = [entry for entry in group if entry is not kept[-1]] + kept[-1:]
                if len(group) > 1 and node not in skip:
                    for entry, repeat in _replaced(group, mapping, slot_of, resolved, aliases):
                        if (id(node), id(entry)) not in reported:
                            reported.add((id(node), id(entry)))
                            found.append(repeat)
                if isinstance(group[-1][1], containers):
                    children.append((group[-1][1], (path, ("k", slot, group[-1][0])), None))
            for number, source in enumerate(sources, 1):
                merge = number if len(sources) > 1 else None  # which `<<`, when it is written more than once
                for index, each in enumerate(source.value) if isinstance(source, yaml.SequenceNode) else [(None, source)]:
                    children.append((each, (path, ("m", index, merge)), owner))
        stack.extend(reversed(children))
    if shared:
        # What a finding describes may be held under other fields as well: it is judged against the whole metadata.
        return tuple(dataclasses.replace(each, field=None) for each in found)
    return tuple(found)


class _Mapping:
    """A mapping that ``repeated_keys`` judges: what a finding in it says of it, worked out once it has one."""

    def __init__(self, node: yaml.Node, path: tuple[Any, ...] | None, block: str, breaks: list[int], first_line: int):
        self.node, self.path, self.block, self.breaks, self.first_line = node, path, block, breaks, first_line
        self._where: tuple[str, str | None, int, int] | None = None
        self._texts: dict[int, str] = {}  # the text of each key named, by its start: cut from the source once

    def _text(self, start: int, end: int) -> str:
        if start not in self._texts:
            self._texts[start] = self.block[start:end]
        return self._texts[start]

    def where(self, slot: Any) -> tuple[str, str | None, int, int]:
        """The mapping's path as text, the top-level key it is under (*slot*, the key of the finding, for the
        root), and the line and column where it starts."""
        if self._where is None:
            steps = []
            path = self.path
            while path is not None:
                path, step = path
                steps.append(step)
            top = next((step[1] for step in reversed(steps) if step[0] == "k"), _NO_SLOT)
            start = self.node.start_mark.index
            self._where = (
                _path_text(self.path, self.block),
                top,  # type: ignore[assignment]
                line_at(self.breaks, start, self.first_line),
                start - self.block.rfind("\n", 0, start),
            )
        path, top, line, column = self._where
        field = slot if top is _NO_SLOT else top
        return path, field if isinstance(field, str) else None, line, column

    def repeat(
        self, entry: tuple[yaml.Node, yaml.Node], why: str, by: tuple[yaml.Node, yaml.Node], aliases: AliasKeys | None,
        slot: Any = None,
    ) -> Repeated:
        """The finding for *entry*, dropped for *why*, naming *by*: each at the key as written (an alias at its alias)."""
        start, end = _key_span(entry, aliases)
        by_start, by_end = _key_span(by, aliases)
        path, field, line, column = self.where(slot)
        return Repeated(
            path,
            self._text(start, end),
            line_at(self.breaks, start, self.first_line),
            start,
            why,
            self._text(by_start, by_end),
            line_at(self.breaks, by_start, self.first_line),
            field,
            line,
            column,
        )


def _replaced(
    group: list[tuple[yaml.Node, yaml.Node]],
    mapping: _Mapping,
    slot_of: Callable[[yaml.Node], Any],
    resolved: dict[yaml.Node, str],
    aliases: AliasKeys | None,
) -> list[tuple[tuple[yaml.Node, yaml.Node], Repeated]]:
    """The entries of *group*, the entries of a mapping that its dict holds as one, but the last, with what is
    found of each (``repeated_keys``). A key is written again when a later key of the group has the type YAML
    gave it (``resolved``) and the text it has, as ``read_maps`` judges the keys of ``translations``: ``fr`` and
    ``"fr"``, and an alias of it, which is the same node (named at the alias, #133). Otherwise the model reads
    different keys as one: as text, when both are text in the model, else by their value (``1`` and ``0x1``)."""
    last = group[-1]
    # By the type YAML gave each key and its text: the nearest later entry of the same, so that one pass from the
    # end finds, for each entry, the entry that repeats it.
    after: dict[tuple[str, str], tuple[yaml.Node, yaml.Node]] = {}
    out = []
    for index in range(len(group) - 1, -1, -1):
        entry = group[index]
        key = entry[0]
        read = (resolved.get(key, key.tag), key.value)
        if index < len(group) - 1:
            later = after.get(read)
            if later is not None:
                why, by = "repeated", later
            else:
                why = "text" if isinstance(slot_of(key), str) and isinstance(slot_of(last[0]), str) else "value"
                by = last
            out.append((entry, mapping.repeat(entry, why, by, aliases, slot_of(key))))
        after[read] = entry
    return out


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
    #: The keys of the other frontmatter mappings that a later key replaces (``repeated_keys``, #131), and,
    #: when there are any, ``metadata_fields_snapshot`` of the metadata as parsed: they describe its source text.
    repeats: tuple[Repeated, ...] = ()
    metadata_seen: dict[str | int | None, list[Any]] = field(default_factory=dict, compare=False, repr=False)
    #: The offset of each line of ``lines`` in the source as given to ``parse`` (``line_starts``, #150):
    #: given by ``parse`` when that source starts with a byte-order mark or ends a line at CR, so that
    #: ``lines`` joined with LF is not it; else worked out from ``lines`` once asked for.
    starts: Sequence[int] | None = field(default=None, compare=False, repr=False)

    def line_starts(self) -> Sequence[int]:
        """The offset of each of ``lines`` in the source as given to ``parse``, in characters (code
        points): a byte-order mark, and a CRLF line ending, count as they are written there."""
        if self.starts is None:
            self.starts = array("q", accumulate((len(line) + 1 for line in self.lines[:-1]), initial=0))
        return self.starts

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
                return stays.line if stays.merged_from is None else stays.merged_from  # where it is written
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


#: A ``|`` that ends a table cell: one not after a backslash (``parser._split_row``).
_CELL_BREAK_RE = re.compile(r"(?<!\\)\|")


def _written_end(line: str, start: int, needle: str, table: bool) -> int | None:
    """Where *needle*, as a block's text holds it, ends in source *line* if it is written there from
    *start*: in a table row, a ``|`` of it is written ``\\|``. None if it is not."""
    at = start
    for char in needle:
        if table and char == "|" and line.startswith("\\|", at):
            at += 1
        if line[at:at + 1] != char:
            return None
        at += 1
    return at


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
    #: The block's kind.
    kind: str = ""


@dataclass(slots=True)
class _MarkerPlaces:
    """Where the candidates for markers (``MARKER_RE``) are in a leaf, by their spelling: in its *texts*,
    each as (text, offset), and in its *lines*, each as (line from 0, column), a table cell's spelled as
    the cell reads it; and where every ``{`` is in either, in order."""

    texts: dict[str, list[tuple[int, int]]]
    lines: dict[str, list[tuple[int, int]]]
    text_braces: list[tuple[int, int]]
    line_braces: list[tuple[int, int]]


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
        # Per leaf, by its id, the marker candidates in its texts and in its lines (``_candidates_in``).
        self._marker_places: dict[int, _MarkerPlaces] = {}

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

    def repeats(self) -> list[Repeated]:
        """The keys of the frontmatter mappings (``translations`` and ``field_types`` apart: ``ignored``) that a
        later key of the same mapping replaces, in source order; none without a source map, or when the
        metadata was replaced since it was parsed, and none under a field of the metadata that was changed
        since (``metadata_fields_snapshot``, #133): an edit elsewhere leaves them be."""
        if not self._map or not self._map.repeats:
            return []
        metadata, seen = self._document.metadata, self._map.metadata_seen
        if seen.get(None, [None])[0] is not metadata:
            return []
        fresh: dict[str | int, bool] = {}

        def unchanged(name: str | None) -> bool:
            if name is None:
                if WHOLE_METADATA not in fresh:
                    fresh[WHOLE_METADATA] = metadata_unchanged(metadata, seen.get(WHOLE_METADATA, []))
                return fresh[WHOLE_METADATA]
            if name not in seen:
                return True  # under no field of the model
            if name not in fresh:
                fresh[name] = metadata_unchanged(getattr(metadata, name), seen[name])
            return fresh[name]

        return sorted((each for each in self._map.repeats if unchanged(each.field)), key=lambda each: (each.line, each.start))

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

    def body(self) -> tuple[list[str], int] | None:
        """The lines of the body as the parser read them (without the last line's ending), and the file line,
        counted from 1, of the first. None without a source map, or when the document no longer fits it: the
        lines are then not the document's."""
        if self._map is None:
            return None
        lines = self._map.lines[self._map.body_start:]
        if lines and lines[-1] == "":
            lines.pop()
        return lines, self._map.body_start + 1

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

    def marker_span(
        self, section: int | None, index: int, fragment: int, offset: int, needle: str
    ) -> tuple[int, int, int] | None:
        """Where the marker or look-alike *needle* (as its fragment holds it), at *offset* in fragment
        *fragment* of a top-level block's ``block_fragments``, is written in the source: ``(start, end,
        line)``, its offsets in the source as given to ``parse`` (code points, *end* exclusive) and its
        file line, counted from 1 (#150). A marker is on one line, and a block's texts hold each line's
        text as written; so the candidates for markers (``MARKER_RE``) in the texts of the block that holds
        the fragment, and those in its lines, are the same, in the same order: the marker is the candidate
        of its rank among those spelled alike, a literal one and a placed one apart. A marker that is no
        candidate as written (a comment or code span in it holds a brace) is the ``{`` of its rank among
        all of the block's. None without a source map, when the document no longer fits it, and when the
        texts and the lines disagree (a fenced code block holding the same spelling, which its text
        blanks; a table cell past the header's width). In a table cell, a ``|`` is written ``\\|``, which
        the span covers."""
        if self._map is None:
            return None
        leaf = self._leaf_for(section, index, fragment)
        if leaf is None or "\n" in needle:  # a comment blanked in it holds a line break: it is on two lines
            return None
        text, base = leaf.places[fragment]
        places = self._candidates_in(leaf)
        in_texts, in_lines = places.texts.get(needle), places.lines.get(needle, [])
        if in_texts is None:
            in_texts, in_lines = places.text_braces, places.line_braces
        rank = bisect.bisect_left(in_texts, (text, base + offset))
        if len(in_texts) != len(in_lines) or rank == len(in_texts) or in_texts[rank] != (text, base + offset):
            return None
        row, column = in_lines[rank]
        line = self._map.lines[leaf.start - 1 + row]
        end = _written_end(line, column, needle, leaf.kind == "table")
        if end is None:
            return None
        number = leaf.start + row
        start = self._map.line_starts()[number - 1]
        return start + column, start + end, number

    def _candidates_in(self, leaf: _Leaf) -> _MarkerPlaces:
        """Where the candidates for markers, and the braces, are in *leaf*, read once for it."""
        from .markers import MARKER_RE

        key = id(leaf)
        if key not in self._marker_places:
            assert self._map is not None
            places = _MarkerPlaces({}, {}, [], [])
            for number, text in enumerate(leaf.texts):
                for match in MARKER_RE.finditer(text):
                    places.texts.setdefault(match.group(), []).append((number, match.start()))
                places.text_braces.extend((number, at) for at in find_all(text, "{"))
            for number, line in enumerate(self._map.lines[leaf.start - 1:leaf.end - 1]):
                for match in MARKER_RE.finditer(line):
                    spelled = match.group()
                    if leaf.kind == "table":
                        if _CELL_BREAK_RE.search(spelled):
                            continue  # it runs over a cell's end: no cell holds it
                        spelled = spelled.replace("\\|", "|")
                    places.lines.setdefault(spelled, []).append((number, match.start()))
                places.line_braces.extend((number, at) for at in find_all(line, "{"))
            self._marker_places[key] = places
        return self._marker_places[key]

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
                    leaves.append(_Leaf(first, last, own, places, kind=block.kind))
                count += len(own)
        except ValueError:
            return None  # items or quote content that no longer match the source
        if count != len(block_fragments(top)):
            return None
        return leaves


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
    return _Leaf(start, end, [joined], places, lifted if lifted >= 0 else None, block.kind)


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


def written_line_starts(written: str) -> Sequence[int]:
    """The offset in *written*, a source as given to ``parse``, of each of its lines as the parser
    reads them (``SourceMap.line_starts``): after a byte-order mark, and after each line ending, LF, CR
    or CRLF."""
    first = 1 if written.startswith("\ufeff") else 0
    return array("q", [first, *(match.end() for match in LINE_ENDING_RE.finditer(written, first))])


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
