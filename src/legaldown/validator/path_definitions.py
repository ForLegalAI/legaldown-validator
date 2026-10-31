"""The link reference definitions of a document as pandoc and markdown-it read them (§4.7, #121), for the path rules.

The model reads a definition as the specification does, and keeps it so: a destination with a control character is
paragraph text to it. pandoc and markdown-it read some definitions the specification does not (``markdown.Reading``),
and an image that names one is displayed all the same, so the path rules judge what each of the three reads.

The document's body is parsed once more under each reading that differs (``markdown.definitions_read_as``), by the
parser itself, so that the setext headings, list items, block quotes and tables that decide what a definition is are
the parser's, and not read again by a scan of the text. What is made of that reading is plain data (the label, the
destination and the line of each definition, by the top-level block of the model it lies in) before the context ends.
It needs the lines the document was parsed from (``Locator.body``): a document built in code, or changed since it was
parsed, has none, and keeps the specification's reading alone (CONFORMANCE.md, #121).

``definitions`` is the one place that turns it into the definitions of a document in document order, for the document
itself and for the combined document of its includes (§16.11, #117): there each file is read by itself, and the
definitions of the files are laid end to end in the order the files are spliced, as a splice of their text reads them.
"""
from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any, NamedTuple

from ..markdown import Reading, definitions_read_as
from ..models import Block, Document
from ..positions import Locator, _walk_spans
from .result import Line

if TYPE_CHECKING:
    from .combined import Combined

#: Where a definition is, in the model: its section (None: the preamble) and its top-level block, or -1 for the
#: heading of the section: a setext heading's lines are the model's heading and the reading's definitions.
Slot = tuple["int | None", int]

#: Markdown-it reads a link destination otherwise than the specification does where a backslash comes before a space,
#: a tab, a line break, a control character or DEL (``Reading.MDIT``): in an inline link and in a definition.
MDIT_BACKSLASH_RE = re.compile("\\\\[ \x01-\x1f\x7f]")

#: What makes a document worth reading again, besides a definition (``]:``): what the readings disagree on. pandoc
#: keeps a control character or DEL in a bare destination, and a backslash before a line break in ``<…>``; markdown-it
#: is ``MDIT_BACKSLASH_RE``. Each also takes a label of white space the specification does not as blank, so that
#: the line is text to it (``[`` and white space, the Unicode spaces and the ASCII ones it adds, but not a tab,
#: a line break or space alone).
_TRIGGERS: dict[Reading, re.Pattern[str]] = {
    Reading.PANDOC: re.compile(
        "[\x01-\x08\x0b-\x1f\x7f]|\\\\\n|\\[[ \t\n]*[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]"
    ),
    Reading.MDIT: re.compile(
        MDIT_BACKSLASH_RE.pattern + "|\\[[ \t\n]*[\x0b\x0c\x1c-\x1f\x85\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]"
    ),
}


class ReadDefinition(NamedTuple):
    """A link reference definition as a reading reads it: its *label* and *target*, raw as the model holds them, and
    the lines it takes in the file, from *line* to before *end*."""

    label: str
    target: str
    line: int
    end: int


class ReadText(NamedTuple):
    """Text that a reading reads as a paragraph (or a heading) where the model reads definitions and their titles: all
    of the paragraph's or heading's *text* (an image may begin in a title and end below it), the file *line* it
    begins on, and *counted*, the ranges of offsets in it that lie on lines the model has as definitions. An image
    that begins elsewhere is in text the model scans itself."""

    text: str
    line: int
    counted: tuple[tuple[int, int], ...]

    def counts(self, offset: int) -> bool:
        """Whether *offset* is on a counted line: the ranges are sorted and do not overlap, so a bisect finds it."""
        number = bisect_right(self.counted, (offset, float("inf"))) - 1
        return number >= 0 and offset < self.counted[number][1]


class Read(NamedTuple):
    """What one reading makes of a file: its definitions, and the text it has that the model has none of, each by
    where it is in the model (``Slot``)."""

    definitions: dict[Slot, list[ReadDefinition]]
    texts: dict[Slot, list[ReadText]]


class Origin(NamedTuple):
    """A file the document is made of: the document itself, or a fragment of its combined document: *document*, its
    *where* and *spot*, which gives a line of it as a finding names it (``combined.Instance.spot``)."""

    document: Document
    where: Locator
    spot: Callable[[int], int | None]


class Definition(NamedTuple):
    """A definition of a document's list (``definitions``): its *label* and *target*, raw as the model holds
    them, *at*, where it lies among the findings, and its *line* as a finding names it."""

    label: str
    target: str
    at: tuple[int, ...]
    line: Line


def read_definitions(document: Document, where: Locator, reading: Reading) -> Read | None:
    """The link reference definitions of *document*, whose locator is *where*, as *reading* (pandoc's or
    markdown-it's) reads them, by the (section, block) of the model they lie in; None when they are the
    specification's: the text has nothing the reading differs on, or the document has no text to read. Also the
    text the reading has where the model has a definition (its title, say, with an image in it)."""
    body = where.body()
    if body is None:
        return None
    lines, first = body
    text = "\n".join(lines)
    if "]:" not in text or _TRIGGERS[reading].search(text) is None:
        return None
    layout = document.layout()
    source_map = document.source_map
    if layout is None or source_map is None:
        return None
    from ..parser import _Layout, _parse_body  # the parser builds on these modules

    # Where each part of the model starts, to find the one a line of the reading lies in.
    starts: list[int] = []
    slots: list[Slot] = []
    for index, span in enumerate(layout.preamble):
        starts.append(span.start)
        slots.append((None, index))
    for number, each in enumerate(layout.sections):
        starts.append(each.heading.start)
        slots.append((number, -1))
        for index, span in enumerate(each.blocks):
            starts.append(span.start)
            slots.append((number, index))

    def slot_of(line: int) -> Slot:
        return slots[max(bisect_right(starts, line) - 1, 0)]  # (a line before the first part is in the first)

    # The lines of the model's definitions: what its text scans never see.
    model_parts = [(source_map.layout.preamble, document.preamble)]
    model_parts += [(spans, each.blocks) for (_h, spans), each in zip(source_map.layout.sections, document.sections, strict=True)]
    defined: set[int] = set()
    for lying, blocks in model_parts:
        for span, block, _depth, base in _walk_spans(lying, blocks, 0, 0):
            if block.kind == "link_definition":
                defined.update(range(first + base + span.start, first + base + span.end))

    spans = _Layout()
    read: list[ReadDefinition] = []
    texts: list[ReadText] = []
    with definitions_read_as(reading):
        preamble, sections = _parse_body(lines, spans, tidy=False)  # (the destination as written, line breaks and all)
        # (The blocks of each part with where they lie, nested ones too: all read, within the context.)
        parts_read = [(spans.preamble, preamble)]
        parts_read += [(span, blocks) for (_heading, span), (_title, blocks) in zip(spans.sections, sections, strict=True)]
        for lying, blocks in parts_read:
            for span, block, base, frame in _walk_framed(lying, blocks, 0, 0, lines):
                begin, end = first + base + span.start, first + base + span.end
                if block.kind == "link_definition":
                    read.append(ReadDefinition(block.label, block.target, begin, end))
                elif block.kind in ("paragraph", "heading") and defined and not defined.isdisjoint(range(begin, end)):
                    made = _text_of(block.text, block.kind, begin, end, defined, frame[span.start:span.end])
                    if made is not None:
                        texts.append(made)
        for (title_span, _spans), (_heading, _blocks) in zip(spans.sections, sections, strict=True):
            begin, end = first + title_span.start, first + title_span.end
            if end - begin > 1 and not defined.isdisjoint(range(begin, end - 1)):  # (a setext heading: ATX has no definition)
                pieces = [each.strip(" \t") for each in lines[begin - first:end - 1 - first]]
                texts.append(_joined(" ".join(pieces), begin, [len(each) for each in pieces], defined))
    found = Read(defaultdict(list), defaultdict(list))
    for each in read:
        found.definitions[slot_of(each.line)].append(each)
    for each in texts:
        found.texts[slot_of(each.line)].append(each)
    return found


def _walk_framed(
    spans: Any, blocks: Any, depth: int, base: int, frame: list[str]
) -> Iterator[tuple[Any, Block, int, list[str]]]:
    """``positions._walk_spans`` with the lines the spans are counted in (*frame*: those of the body, or of a quote's
    content), each as ``(span, block, base, frame)``."""
    from ..models import LIST_KINDS
    from ..parser import MAX_QUOTE_DEPTH, quote_content

    for span, block in zip(spans, blocks, strict=True):
        yield span, block, base, frame
        if block.kind in LIST_KINDS:
            for item_span, item in zip(span.items, block.items, strict=True):
                yield from _walk_framed(item_span.blocks, item.blocks, depth + 1, base, frame)
        elif block.kind == "quote" and depth < MAX_QUOTE_DEPTH:
            children, inner = quote_content(block.text, depth + 1)
            yield from _walk_framed(inner, children, depth + 1, base + span.start, block.text.split("\n"))


def _joined(text: str, begin: int, sizes: list[int], defined: set[int]) -> ReadText:
    """The *text* of lines that begin on file line *begin* and are *sizes* long, a space or a line break between."""
    counted = []
    at = 0
    for number, size in enumerate(sizes):
        if begin + number in defined:
            counted.append((at, at + size + 1))
        at += size + 1
    return ReadText(text, begin, tuple(counted))


def _text_of(text: str, kind: str, begin: int, end: int, defined: set[int], source: list[str]) -> ReadText | None:
    """The text of a paragraph or a heading that lies on file lines ``[begin, end)`` (its *source* lines, of which a setext
    heading's last is its underline), for ``_joined``; None when its lines are not told apart."""
    if kind == "paragraph":
        pieces = text.split("\n")
        return _joined(text, begin, [len(each) for each in pieces], defined) if len(pieces) == end - begin else None
    if end - begin < 2:
        return None  # (an ATX heading: one line, no definition)
    # A setext heading's text is its lines stripped, joined by spaces, after the marker of the item it may be in:
    # the lengths of the lines are counted from the end.
    pieces = [each.strip(" \t") for each in source[:-1]]
    rest = " ".join(pieces[1:])
    first_size = len(text) - len(rest) - (1 if rest else 0)
    if first_size < 0 or not text.endswith(rest) or (rest and text[first_size] != " "):
        return None
    return _joined(text, begin, [first_size, *(len(each) for each in pieces[1:])], defined)


def parts(document: Document, host: Origin, combined: Combined | None) -> list[tuple[Slot, Origin, Slot]]:
    """The parts of *document* in order, each as ``(slot, origin, slot in the origin's file)``: the document's
    (*host*), or, of its combined document (*combined*), the file each is spliced from."""
    found: list[tuple[Slot, Origin, Slot]] = []
    if combined is None:
        found += [((None, index), host, (None, index)) for index in range(len(document.preamble))]
        for number, section in enumerate(document.sections):
            found.append(((number, -1), host, (number, -1)))
            found += [((number, index), host, (number, index)) for index in range(len(section.blocks))]
        return found
    origins = {place: Origin(place.document, place.where, place.spot) for place in combined.instances}
    for index in range(len(document.preamble)):
        place, local, block = combined.of_block(None, index)
        found.append(((None, index), origins[place], (local, block)))
    for number, section in enumerate(document.sections):
        place, local = combined.section_origin[number]
        found.append(((number, -1), origins[place], (local, -1)))
        for index in range(len(section.blocks)):
            place, local, block = combined.of_block(number, index)
            found.append(((number, index), origins[place], (local, block)))
    return found


def definitions(
    reading: Reading,
    layout: list[tuple[Slot, Origin, Slot]],
    model: dict[Slot, list[Definition]],
    reads: dict[int, Read | None],
) -> list[Definition]:
    """The definitions of a document in document order as *reading* reads them. *layout* is ``parts``; *reads* is
    what the reading makes of each file of it (``read_definitions``), by ``id`` of its document, and *model* the
    model's definitions by part. A file that is not read again gives the model's definitions of its parts, so that
    a document of several files is one list."""
    found: list[Definition] = []
    for slot, origin, local in layout:
        got = reads[id(origin.document)]
        if got is None:
            found.extend(model.get(slot, ()))
            continue
        # (A definition read here has no fragment, and is at the line it begins on: it is read from the
        # lines of its file, where the model has an ordinal among the definitions of its block.)
        found.extend(
            Definition(each.label, each.target, (-1 if slot[0] is None else slot[0], slot[1], 0, -1, each.line), origin.spot(each.line))
            for each in got.definitions.get(local, ())
        )
    return found


def texts(
    layout: list[tuple[Slot, Origin, Slot]], reads: dict[int, Read | None]
) -> list[tuple[Slot, Origin, ReadText]]:
    """The text a reading has where the model has definitions (``Read.texts``) in a document, in document order, each
    with its part and file. *layout* and *reads* are as for ``definitions``."""
    found: list[tuple[Slot, Origin, ReadText]] = []
    for slot, origin, local in layout:
        got = reads[id(origin.document)]
        if got is not None:
            found.extend((slot, origin, each) for each in got.texts.get(local, ()))
    return found
