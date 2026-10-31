"""Link reference definitions and the insertion boundary (§15.7.3, #129).

An answer must not create, change or complete a link reference definition (§15.7.3). Whether one could is
not predicted from the template's text: that is the assembler's reading of the same text, written a second
time, and it never agreed with the first. It is asked of the assembler. Each region of the body that holds
both an insertion and a place a definition can begin is assembled by the real assembler over a finite set of
witness answers, and an insertion is reported when changing only its answer changes the link reference
definitions that the parser reads in the assembled region.

The witnesses are enough because assembly escapes ``\\`` `` ` `` ``*`` ``_`` ``[`` ``]`` ``<`` ``|`` ``{``
in an answer, and in a phrase of a choice, so a label, and the ``<`` of a destination, always come from the
template, and what an answer can still do to a definition is to be a destination, close or open a title, close a
destination and open a title, close or open the parentheses of a destination, or make a label a definition with
its colon (``TEXT_WITNESSES``, and ``_parentheses`` for the runs that the template's parentheses ask for). Every
insertion has all of them, and each is a question of its own: that is more combinations than a question can
have, never fewer. A region is enough because a definition lies within a paragraph, and the only lines that
join paragraphs are the ones that assembly removes (``regions``). ``tests/test_definition_hazards`` holds the
oracles for both. A region of more combinations than ``CAP`` (three text insertions are over it) or more work
than ``WORK_CAP`` (its lines and insertions, for each assembly), and every region once the validation has used
``BUDGET`` of work, is judged by the conservative rule instead: every insertion in it is reported, if a
definition can begin in it at all. A region of text insertions is first assembled once for each combination of
its decisions (at most ``DECISION_CAP``), with a sentinel for every text answer, and is clean if every label in
the output is dead (``_dead``): then no answer, and nothing after the label, can make it a definition (#137).
This is the one module that reads the assembler's private parts, so that it
is the one to change when assembly does.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from collections import ChainMap
from dataclasses import dataclass
from datetime import date
from itertools import product
from typing import TYPE_CHECKING, Any

import yaml

from ..definitions import nested_blocks
from ..directives import Directive, lex
from ..markdown import is_blank, is_drafting_marker, label_end, skip_blanks
from ..markers import MARKER_RE
from ..models import Document
from ..parser import parse
from ..positions import Locator
from .patterns import IDENTIFIER_RE, VALID_PLACEHOLDER_TYPES
from .result import _Recorder
from .templates import DECISION_QUESTION_TYPES, _InsertionScan, question_type, template_text

if TYPE_CHECKING:
    from .combined import Combined

#: The most combinations of witness answers that one region is assembled with; the most work for one region, which
#: is its lines and its insertions (and two for what an assembly costs besides them) for each combination; and the most work in one
#: validation, the document and the fragments it includes together (a template of thousands of definitions is not
#: assembled thousands of times). Past any of them the region is judged by the conservative rule. A region costs
#: ``REGION_WORK`` besides (reading it as a template of its own), and ``DECLARATION_WORK`` for each question it declares, and 240000 is some five seconds.
CAP = 4096
WORK_CAP = 24576
BUDGET = 240000
#: The most combinations of decisions (the answers to the choices and conditions of a region) that one region is
#: assembled with, for every text answer the sentinel, to show that no label in it can be a definition (``_dead``).
DECISION_CAP = 64
#: The text answer of that assembly: a character that no template holds (a region that holds it is enumerated).
SENTINEL = "\x01"
REGION_WORK = 100
#: What each question that the template declares costs besides: reading its YAML (about 0.45 ms).
DECLARATION_WORK = 20
#: The deepest run of parentheses an insertion is tried with (CommonMark §6.3: a bare destination nests 32).
MAX_DEPTH = 32

#: Every text answer an insertion is tried with, to a definition's destination and title: plain text (two, to tell a
#: different answer from the same); a title's opener, a closer and both together (a title with nothing in it is
#: none: `()`), alone and with text, and a whole title; a destination's closer ``>``, alone, with a title's opener
#: and with a whole title; the colon after a label that the template ends with ``]`` (alone, with a destination,
#: and with a destination and a title); and a destination with a title's opener and with a whole title. The
#: parentheses of a bare destination nest: the runs that close and open those that the template leaves open on the
#: line are added to an insertion's (``_parentheses``).
TEXT_WITNESSES: tuple[str, ...] = (
    "x", "y", '"', "'", "(", ")", '"t"', "'t'", "(t)", '"t', "'t", "(t", 't"', "t'", "t)",
    ">", '> "', "> '", "> (", '> "t"', "> 't'", "> (t)",
    ":", ": u", ': u "t"',
    'x "t', "x 't", "x (t", 'x "t"', "x 't'", "x (t)",
)
#: What may follow the parentheses that close a destination: the opening of a title, and a whole one.
_TITLES = (' "t', " 't", " (t", ' "t"', " 't'", " (t)")
#: Two values of a date, a money amount and a duration, which assemble to a ``{{date:}}``, ``{{money:}}`` or
#: ``{{duration:}}``: text that differs by its digits only.
_DATES = (date(2024, 1, 1), date(2025, 12, 31))
_AMOUNTS = ("100", "250.50")
_VALUES = ("12", "3")

#: Where a definition can begin: a ``[`` after what a line may open with (container markers, up to three spaces); and
#: what ends its label, with the colon or an insertion that could be one after it.
_LABEL_START_RE = re.compile(r"^[ \t>*+\-0-9.)]*\[", re.M)
_LABEL_END_RE = re.compile(r"\](?::|\{\{)")
#: A directive on a line, in the text that an insertion is read with.
_DIRECTIVE_RE = re.compile(r"\{\{[^\n]*?\}\}")
#: A line's list item markers and the blanks after them: how an item's marker line is repeated in the context.
_QUOTE_MARKER_RE = re.compile(r"[ \t]*>[ \t]?")
_QUOTE_ONLY_RE = re.compile(r"[ \t]*(?:>[ \t]*)+")
_MARKERS_RE = re.compile(r"(?:[ \t]*(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+|$))+")

#: A list item marker (a bullet, or a number: its digits) and what comes after it, as the first of a line.
_OPENER_RE = re.compile(r"[ \t]*(?:[-*+]|([0-9]{1,9})[.)])([ \t]+\S)?")
_ADVICE = "write '\\[' if the bracket is text, or put a blank line after the definition"
_FALLBACK = (
    "it is in a paragraph with a link reference definition, and there are too many answers to try to show "
    "that none completes it"
)


@dataclass(frozen=True, slots=True)
class Hazard:
    """An insertion of which an answer changes a link reference definition: its *line* (counted from 0 in the
    body), its source, and whether it is reported because the answers could not all be tried (*fallback*)."""

    line: int
    source: str
    fallback: bool


def check_definition_hazards(
    document: Document, result: _Recorder, *, where: Locator | None, template: bool,
    markers: list | None = None, combined: Combined | None = None, cache: dict | None = None,
) -> None:
    """Report, as insertion-boundary, each insertion in the body of *document* of which an answer could create,
    change or complete a link reference definition (§15.7.3), unless the character rules report it (they are of its
    line alone: ``_Assembler.hazards`` asks them of the line, so that no insertion is reported twice, whether its
    line is known or not). *where* gives the lines; without them the
    document is judged as it is written (``serialize``), and the findings have none. *markers* are the
    document's, when the caller has them.

    Each file is judged on its own text and layout: the document's own body, and, in the pass over the
    *combined* document (§16.11), the body of each fragment as the file it is, at its own lines (``Spot``); never the
    woven document, whose blocks are no lines of any file. The splice cannot join a definition across files: the
    include paragraph is a block of its own (§12.2), which the lines of a fragment follow as a file of their own, as
    assembly writes them (``combined``), and an insertion in a fragment is judged in the paragraph it is in there.
    A fragment is judged with the questions of the document that includes it, as assembly reads it, and with the
    document's budget. *cache* keeps what a file's judgement was, and what is left of the budget, for the pass that
    follows the other to take them. Nothing here raises: a file or a region the assembler cannot read is judged by
    the conservative rule."""
    files = [(document, where, None)] if combined is None else [
        (place.document, place.where, place) for place in combined.instances
    ]
    host = (document if combined is None else combined.document).metadata.questions
    declared = host if isinstance(host, dict) else {}
    budget = [BUDGET] if cache is None else cache.setdefault("hazards-budget", [BUDGET])
    for judged, locator, place in files:
        path = place.path if place else ""
        judgement = cache.get(("hazards", path)) if cache is not None else None
        if judgement is None:
            judgement = _judge_file(
                judged, locator, template, markers if combined is None else None, declared, budget
            )
            if cache is not None:
                cache["hazards", path] = judgement
        first, hazards = judgement
        for hazard in hazards:
            line = None if first is None else first + hazard.line
            reason = _FALLBACK if hazard.fallback else (
                "an answer could create, change or complete a link reference definition here"
            )
            result.error(
                "insertion-boundary",
                f"'{hazard.source}' is not kept apart from the text around it: {reason}; {_ADVICE} (§15.7.3).",
                line=place.spot(line) if place else line,
            )


def _judge_file(
    document: Document, where: Locator | None, template: bool, markers: list | None, declared: dict, budget: list[int]
) -> tuple[int | None, list[Hazard]]:
    """The file line of the first line of the body, and the hazards of *document*'s body. The file is read as
    written, then as ``serialize`` writes it; where it cannot be read, or an exception comes of judging it, the
    conservative rule judges its lines (``_conservative``), and none is reported of a file that has no lines."""
    for as_written in (True, False):
        try:
            lines, first, judged = _body(document, where if as_written else None)
        except Exception:  # noqa: BLE001 - a check that no template can stop: see ``check_definition_hazards``
            continue
        try:
            return first, definition_hazards(
                judged, lines, template=template, markers=markers if judged is document else None,
                declared=declared, budget=budget, characters=True,
            )
        except Exception:  # noqa: BLE001
            try:
                return first, _conservative(lines, judged.source_map.layout if judged.source_map is not None else None)
            except Exception:  # noqa: BLE001 - nothing is reported of a reading that no rule can read: the next is tried
                continue
    return None, []


class _Characters:
    """The character rules of the insertion boundary (``templates._InsertionScan``), which are rules of a line alone,
    asked of the lines of a body: an insertion that they report is reported by them, not again."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.scans: dict[int, tuple[Any, dict[int, Directive]]] = {}  # the reading of a line, once

    def reports(self, number: int, column: int, name: str) -> bool:
        """Whether the character rules report the insertion called *name* at *column* of line *number*."""
        if number not in self.scans:
            line = self.lines[number]
            lexed = lex(line)
            scan = _InsertionScan(line, template_text(line, lexed.directives), lexed.directives)
            self.scans[number] = (scan, {each.start: each for each in lexed.directives if not each.malformed})
        scan, directives = self.scans[number]
        found = directives.get(column)
        return found is not None and found.name == name and scan.problem(found) is not None


#: Where a definition can begin, in a paragraph in which each choice is a ``\x01``: a ``[`` after what a line may open with.
_START_RE = re.compile(r"^[ \t>*+\-0-9.)\x01]*\[", re.M)


def _conservative(lines: list[str], layout: Any = None) -> list[Hazard]:
    """The conservative rule on body *lines*, with nothing read of them but the lines and the *layout* the parser
    made of them: every insertion that the character rules do not report, in a paragraph in which a colon, or an
    insertion or a choice that may begin with one, follows a label that begins a line, read as the main path reads a
    label (``markdown.label_end``: an escaped ``]`` and a label over lines included, and a choice that may be empty
    before it). Drafting notes and code are none of it, and end a paragraph."""
    skip: set[int] = set()
    for span in _spans(layout) if layout is not None else ():
        if span.kind == "code" or (
            span.kind == "quote" and is_drafting_marker(_QUOTE_MARKER_RE.sub("", lines[span.start], count=1).strip())
        ):
            skip.update(range(span.start, span.end))
    characters = _Characters(lines)
    lexed: dict[int, list[Directive]] = {}  # the directives of a line, once

    def directives(number: int) -> list[Directive]:
        if number not in lexed:
            lexed[number] = lex(lines[number]).directives if "{{" in lines[number] else []
        return lexed[number]

    found: list[Hazard] = []
    paragraph: list[int] = []
    for number in [*range(len(lines)), len(lines)]:
        if number < len(lines) and number not in skip and not is_blank(lines[number]):
            paragraph.append(number)
            continue
        if paragraph:
            text = "\n".join(_blank_choices(lines[each], directives(each)) for each in paragraph)
            for opening in _START_RE.finditer(text):
                end = label_end(text, opening.end() - 1)
                if end is not None and (text[end + 1:end + 2] in (":", "\x01") or text.startswith("{{", end + 1)):
                    for each in paragraph:
                        found.extend(
                            Hazard(each, one.source, True)
                            for one in directives(each)
                            if one.name in ("placeholder", "choose")
                            and not one.malformed
                            and not characters.reports(each, one.start, one.name)
                        )
                    break
        paragraph = []
    return found


def _blank_choices(line: str, directives: list[Directive]) -> str:
    """*line* with each ``{{choose:}}`` of its *directives* a ``\x01``: it may be empty, or a colon (the spans are the
    lexer's, so that a ``}}`` in a phrase does not end one)."""
    return _without(line, [each for each in directives if each.name == "choose" and not each.malformed], "\x01")


def _body(document: Document, where: Locator | None) -> tuple[list[str], int | None, Document]:
    """The lines of the body that assembly reads, the file line of the first (None when they are not the
    file's) and the document they were parsed into."""
    found = where.body() if where is not None else None
    if found is not None:
        return found[0], found[1], document
    from ..serializer import serialize  # the serializer builds on the model, as the validator does

    written = parse(serialize(document))
    body = Locator(written).body()
    assert body is not None
    return body[0], None, written


def definition_hazards(
    document: Document, lines: list[str], *, template: bool = True, markers: list | None = None,
    declared: dict | None = None, budget: list[int] | None = None, characters: bool = False,
) -> list[Hazard]:
    """The insertions in body *lines* of *document* that an answer could make part of a link reference
    definition, or change one by, in order of line. Nothing is read unless a line holds ``]:`` or a ``]`` that an
    insertion or a choice directly follows (the answer or the phrase may begin with the colon): a label's end, and
    nothing else but an answer or a phrase can be what follows it (§15.7.3 escapes the brackets, in a phrase too). The questions are
    *declared*'s, if given (a fragment's are its document's), and *budget* is what is left of the work, in a list
    of one that the caller shares. With *characters*, the insertions that the character rules report are left out:
    they are reported by those."""
    text = "\n".join(lines) + "\n" if lines else ""
    if "{{" not in text or ("]:" not in text and "]{{" not in text):
        return []
    return _Assembler(document, text, template, markers, declared, budget).hazards(characters)


class _Assembler:
    """The assembler's reading of a body, and the assemblies of its regions. All of the assembler's private
    parts that this check uses are used here."""

    def __init__(
        self, document: Document, text: str, template: bool, markers: list | None = None,
        declared: dict | None = None, budget: list[int] | None = None,
    ) -> None:
        # The assembler imports the validator, and so this module: imported when used.
        from .. import assembly
        from ..parser import _layout

        self.assembly = assembly
        if declared is None:
            own = document.metadata.questions
            declared = own if isinstance(own, dict) else {}
        self.declared: dict = declared
        # The layout that the parser made of the same lines, when the document has it, and its markers: not made again.
        layout = document.source_map.layout if document.source_map is not None else None
        self.source = assembly._read_body(
            "", text, document, self.declared, template=template, layout=layout, markers=markers
        )
        self.layout = layout or _layout(self.source.lines)
        # The blank lines in code and raw HTML, which separate nothing, and the lines of tables, which hold no definition.
        self.solid: set[int] = set()
        self.tables: set[int] = set()
        for span in _spans(self.layout):
            if span.kind in ("code", "html"):
                self.solid.update(range(span.start, span.end))
            elif span.kind == "table":
                self.tables.update(range(span.start, span.end))
        self.by_line = _by_line(self.source.occurrences)
        self.reads: dict[str, tuple] = {}  # the definitions of an assembled text, once
        self.characters = _Characters(self.source.lines)
        # The question of each condition by the line of its marker, which the region that holds the line asks.
        self.tests = sorted((unit.marker_line, unit.test.question) for unit in self.source.units if unit.test)
        self.heading_starts = set(self.source.headings)
        self.left = [BUDGET] if budget is None else budget  # the work that the validation may still use
        self.memo: dict[tuple, frozenset[str] | None] = {}  # by the region's shape: the questions of the hazards

    def hazards(self, characters: bool = False) -> list[Hazard]:
        source = self.source
        found = []
        by_line = self.by_line
        items = _items(self.layout)
        for start, end, context in self.regions(items, by_line):
            insertions = [
                occ
                for line in range(start, end)
                if line not in source.notes
                for occ in by_line.get(line, ())
            ]
            if not insertions or not self._may_hold(start, end):
                continue
            flags, fallback = self.judge(start, end, context, insertions)
            found.extend(
                Hazard(occ.line, occ.directive.source, fallback)
                for occ, flag in zip(insertions, flags, strict=True)
                if flag and not (characters and self.characters.reports(occ.line, occ.start, occ.directive.name))
            )
        return sorted(found, key=lambda hazard: hazard.line)

    # ── Regions ───────────────────────────────────────────────────

    def regions(self, items: list, by_line: dict) -> list[tuple[int, int, list[str]]]:
        """The body's regions, ``(start, end, context)``: lines ``[start, end)`` and the opening of the list
        items that hold them, as lines to put before them. A definition lies within one paragraph, and a
        paragraph ends at a blank line, at a list item's marker and at an ATX heading, which are where a region
        ends. What can change that is what assembly removes or makes empty (§15.7.2): a drafting note, a conditional
        unit, an empty ``{{choose:}}`` that leaves a bare marker. They are ``removable``: a blank line in them is no
        separator. And a line that follows what is removed can join the paragraph before it, unless it ends one
        (``_interrupts``): where it does not, there is no end between the two."""
        source = self.source
        lines = source.lines
        removable = set(source.notes)
        # What is removed or made empty, each by itself: a note, a unit, a line of nothing but choices (deleted when
        # their phrases are empty, step 5), an item that can be a bare marker.
        extents = _runs(source.notes)
        headings = set(source.headings)
        for unit in source.units:
            # A conditional section runs to its last non-blank line and leaves the blank lines around it; it
            # can join what is on both sides of it only where its heading directly follows a line.
            if unit.start not in headings or (unit.start and not is_blank(lines[unit.start - 1])):
                removable.update(range(unit.start, unit.end))
                extents.append((unit.start, unit.end))
        vanishing = {
            line for line in self.by_line
            if line not in self.tables and not is_blank(lines[line]) and is_blank(self._bare(line))
        }
        removable |= vanishing
        extents += _runs(vanishing)
        # A line of a quote's markers and choices only is an empty quote line if the phrases are empty, which ends the
        # paragraph in the quote, and not otherwise: what follows is read one way or the other.
        extents += _runs({
            line for line in self.by_line if line not in self.tables and _QUOTE_ONLY_RE.fullmatch(self._bare(line))
        })
        # An item whose marker line holds nothing but empty-able choices can be a bare marker, and an item
        # ends at a bare marker and a blank line: everything it held is read in another structure. It is one
        # region, with the items inside it.
        bare: set[int] = set()
        interior: set[int] = set()
        reach = 0
        for item in items:
            if item.start >= reach and self._is_bare(item, by_line):
                bare.add(item.start)
                removable.update(range(item.start, item.end))
                interior.update(range(item.start + 1, item.end))
                extents.append((item.start, item.end))
                reach = item.end
        atx = {heading.start for heading in self.layout.headings if heading.start == heading.marker_line}
        starts = {item.start for item in items if item.start not in interior} | atx
        ends: dict[int, int] = {}  # where the items that begin at a line end
        for item in items:
            ends[item.start] = max(ends.get(item.start, 0), item.end)
        joined: dict[tuple[int, bool], int] = {}  # where what joins the paragraph at a line ends, once found
        merged: list[tuple[int, int]] = []
        for first, after in sorted(set(extents)):
            # What is removed joins the line before it to what follows, if that line is a paragraph's; what stays of
            # a bare marker joins it too.
            before = (first > 0 and not is_blank(lines[first - 1])) or bool(bare.intersection(range(first, after)))
            weak = before and any(line in starts and not self._interrupts(line, bare, atx) for line in range(first, after))
            # The lines after it that join the paragraph: an item that does is text, with what it holds. And, after
            # blank lines, those that are indented: what is removed may be what ends an item (a line that is not
            # its own), or what is made empty may keep it open, and they are its content or not.
            joins, passed, crossed = after, [], False
            while joins < len(lines):
                if is_blank(lines[joins]):
                    following = joins
                    while following < len(lines) and is_blank(lines[following]):
                        following += 1
                    if following == len(lines) or lines[following][0] not in " \t":
                        break
                    joins, crossed = following, True
                elif (lines[joins][0] not in " \t") if crossed else self._interrupts(joins, bare, atx):
                    break
                else:
                    if (joins, crossed) in joined:
                        joins = joined[joins, crossed]
                        break
                    passed.append((joins, crossed))  # (as it was when the walk came to the line)
                    joins = ends.get(joins, joins + 1)
            joined.update(dict.fromkeys(passed, joins))
            if weak or joins > after:
                merged.append((first, joins))
        # One region each, to the blank lines inside: an item that is text holds its later paragraphs as text.
        for first, joins in _merge(merged):
            starts.difference_update(range(first, joins))
            removable.update(range(first, joins))
        found: list[tuple[int, int]] = []
        begin = None
        for index, line in enumerate(lines):
            if is_blank(line) and index not in removable and index not in self.solid:
                if begin is not None:
                    found.append((begin, index))
                    begin = None
                continue
            if index in starts and begin is not None:
                found.append((begin, index))
                begin = None
            if begin is None:
                begin = index
        if begin is not None:
            found.append((begin, len(lines)))
        contexts = self._contexts(items, found)
        return [(start, end, context) for (start, end), context in zip(found, contexts, strict=True)]

    def _may_hold(self, start: int, end: int) -> bool:
        """Whether a definition can begin in the region ``[start, end)``, whatever the answers are: a ``[`` that opens
        a line (after container markers and any ``{{choose:}}``, whose phrase may be empty: an answer or a phrase
        escapes a bracket, so the ``[`` is the template's), and, from there on, the end of a label with a colon, or
        with an insertion or a choice, whose answer or phrase may begin with the colon, after it. A table row holds
        none, and a note is removed. Where a definition cannot begin, nothing is judged and nothing is reported."""
        bare = "\n".join(self._bare(line) for line in range(start, end))
        opening = _LABEL_START_RE.search(bare)
        if opening is None:
            return False
        row = bare.count("\n", 0, opening.start())
        marked = "\n".join(self._bare(line, marked=True) for line in range(start + row, end))
        return _LABEL_END_RE.search(marked) is not None

    def _bare(self, line: int, *, marked: bool = False) -> str:
        """The line as it can be read at its least: nothing of a note or a table row, and each ``{{choose:}}``
        nothing, as its phrase may be empty (*marked*: the opening of a directive, which an answer or a phrase
        may begin the colon of a label after)."""
        source = self.source
        if line in source.notes or line in self.tables:
            return ""
        chooses = [occ for occ in self.by_line.get(line, ()) if occ.directive.name == "choose"]
        return _without(source.lines[line], chooses, "{{" if marked else "")

    def _interrupts(self, line: int, bare: set[int], atx: set[int]) -> bool:
        """Whether *line* ends the paragraph before it whatever assembly does to it: an ATX heading, or the marker
        of a list item that begins with something and is a bullet or the number 1 (CommonMark §5.3). A bare
        marker (an empty ``{{choose:}}`` leaves one), the marker of another number, and any other line may
        join it."""
        if line in atx:
            return True
        found = _OPENER_RE.match(self.source.lines[line])
        return (
            line not in bare
            and found is not None
            and found.group(2) is not None
            and (found.group(1) is None or int(found.group(1)) == 1)
        )

    def _is_bare(self, item: Any, by_line: dict) -> bool:
        """Whether the marker line of *item* holds nothing but ``{{choose:}}`` directives, condition markers (assembly
        deletes them, step 3) and list markers."""
        chooses = [occ for occ in by_line.get(item.start, ()) if occ.directive.name == "choose"]
        if not chooses:
            return False
        line = _without(self.source.lines[item.start], chooses)
        return self.assembly._BARE_MARKERS_RE.fullmatch(MARKER_RE.sub("", line)) is not None

    def _contexts(self, items: list, regions: list[tuple[int, int]]) -> list[list[str]]:
        """For each region, the lines that make it read as it does in the document where it lies in list
        items: for each of the items that hold it (outermost first), its marker line with the text ``x``, then a
        blank line. A region that begins at an item's marker line has the items around that one."""
        lines = self.source.lines
        contexts: list[list[str]] = []
        open_items: list = []
        following = 0
        for start, _end in regions:
            while following < len(items) and items[following].start <= start:
                while open_items and open_items[-1].end <= items[following].start:
                    open_items.pop()
                open_items.append(items[following])
                following += 1
            while open_items and open_items[-1].end <= start:
                open_items.pop()
            markers: list[int] = []  # the lines of the markers, once each (an item can begin on its parent's line)
            for item in open_items:
                if item.start < start and (not markers or markers[-1] != item.start):
                    markers.append(item.start)
            # (A marker with nothing after it, as `10.` is, has its text on the next line: the `x` is set off.)
            context = []
            for number in markers:
                found = _MARKERS_RE.match(lines[number])
                opening = found.group(0) if found else "- "
                context.append(opening + ("x" if opening[-1] in " \t" else " x"))
            contexts.append([*context, ""] if context else [])
        return contexts

    # ── The verdict of a region ───────────────────────────────────

    def judge(self, start: int, end: int, context: list[str], insertions: list) -> tuple[list[bool], bool]:
        """For each of the *insertions* of the region ``[start, end)``, whether an answer makes it part of a
        definition or changes one by; and whether that is the conservative rule's verdict (every insertion that
        an answer fills), because the answers could not all be tried. The region is assembled alone, as a
        template of its own: each insertion is a question of its own, with every witness (a question asked in
        two places is tried with combinations of answers that it cannot have: more flags, never fewer); the
        conditions of the region are questions with every value."""
        declared: dict[str, Any] = {}
        keys: dict[str, list] = {}
        first = bisect_left(self.tests, (start, ""))
        last = bisect_left(self.tests, (end, ""))
        for question in sorted({question for _line, question in self.tests[first:last]}):
            declaration = self._declaration(question)
            if declaration is not None:
                declared[question] = declaration
                keys[question] = _decisions(declaration)
        conditions = set(declared)  # the questions of the region's conditions
        names: list[str | None] = []
        texts: list[str] = []  # the text questions
        single: dict[str, str] = {}  # the name of each kind of question with one answer
        made = 0
        for occ in insertions:
            asked = self._question(occ)
            if asked is None:
                names.append(None)
                continue
            declaration, values = asked
            if occ.directive.name == "choose" and declared.get(occ.qid) == declaration:
                names.append(occ.qid)  # a condition of the region tests it too: one question, answered once
                continue
            # A question with one answer does not vary, so those that are alike are one question: it is declared once.
            alike = repr((occ.directive.name, declaration)) if values is not None and len(values) == 1 else None
            if alike in single:
                names.append(single[alike])
                continue
            made += 1
            name = f"w{made}"
            while name in self.declared:
                name += "x"
            declared[name], keys[name] = declaration, values if values is not None else []
            names.append(name)
            if values is None:
                texts.append(name)
            if alike is not None:
                single[alike] = name
        if not any(name is not None for name in names):
            return [False] * len(insertions), False
        everything = [name is not None for name in names]
        lines = self.source.lines[start:end]
        # A question with one answer does not vary, and an insertion of which none does is clean, whatever else varies;
        # it is declared in code, after the template is read (``_enumerate``), and not in the template.
        undeclared = {
            name: declared[name]
            for name, values in keys.items()
            if len(values) == 1 and name not in texts and name not in conditions
        }
        if not any(name in texts or len(keys[name]) > 1 for name in names if name is not None):
            return [False] * len(insertions), False
        # What an assembly costs: the characters of the region (80 to a line's worth) and its lines and insertions.
        weight = (
            sum(map(len, lines)) + sum(map(len, context))
        ) // 80 + end - start + len(context) + len(insertions) + 2
        # A region costs ``fixed`` besides, once: it is paid with the first assembly of it, whichever that is.
        fixed = REGION_WORK + DECLARATION_WORK * (len(declared) - len(undeclared))
        # The cutoff (#137) tries the decisions alone: a typed question has its first answer, a text its sentinel.
        deciding = {
            name: values for name, values in keys.items()
            if len(values) > 1 and name not in texts and not _typed(declared[name])
        }
        decisions = 1
        for values in deciding.values():
            decisions *= len(values)
        cost = decisions * weight + fixed
        too_many = len(TEXT_WITNESSES) ** len(texts) > CAP  # (over the cap whatever the places)
        cutoff = (
            bool(texts) and decisions <= DECISION_CAP and cost <= WORK_CAP
            and SENTINEL not in "".join([*context, *lines]) and not any(map(_hopeless, lines))
        )
        if too_many and not cutoff:
            return everything, True
        for name, occ in zip(names, insertions, strict=True):
            if name in texts:
                keys[name] = self._text_witnesses(occ, lines, start)
        size = 1
        for values in keys.values():
            size *= len(values)
        shape = (
            tuple(lines), tuple(context), tuple(names), tuple((name, repr(each)) for name, each in declared.items()),
            tuple((name, tuple(keys[name])) for name in texts),
        )

        def template() -> Any:
            return self._template(
                self._mini(start, end, context, insertions, names, {n: d for n, d in declared.items() if n not in undeclared}),
                undeclared,
            )

        if shape not in self.memo:
            # The work is spent before the template is made (that is, the YAML of its questions is not written
            # for a region that is past what is left), and spent whether or not the first answers decide it.
            mini, charged = None, False
            if cutoff and cost <= self.left[0]:
                self.left[0] -= cost
                fixed, charged = 0, True
                try:
                    mini = template()
                    if mini is None:
                        self.memo[shape] = None
                    elif self._settled(mini, deciding, {
                        **{name: values[0] for name, values in keys.items() if name not in texts and name not in deciding},
                        **dict.fromkeys(texts, SENTINEL),
                    }):
                        self.memo[shape] = frozenset()
                except Exception:  # noqa: BLE001 - the assembler cannot read the region: the conservative rule judges it
                    self.memo[shape] = None
            if shape not in self.memo:
                work = size * weight + fixed
                if too_many or size > CAP or work > WORK_CAP or work > self.left[0]:
                    if charged:  # (the cutoff was paid for: once for each shape)
                        self.memo[shape] = None
                    return everything, True
                self.left[0] -= work
                try:
                    mini = mini or template()
                    self.memo[shape] = None if mini is None else self._enumerate(
                        mini, keys, {name for name in names if name is not None}
                    )
                except Exception:  # noqa: BLE001 - the assembler cannot read the region: the conservative rule judges it
                    self.memo[shape] = None
        flagged = self.memo[shape]
        if flagged is None:
            return everything, True
        return [name in flagged for name in names], False

    def _mini(self, start: int, end: int, context: list[str], insertions: list, names: list, declared: dict) -> str:
        """The region as a template of its own: the *context*, then its lines with the question of each
        insertion renamed to its *name*, after the frontmatter that declares *declared*."""
        by_line: dict[int, list[tuple[int, int, str]]] = {}
        for occ, name in zip(insertions, names, strict=True):
            if name is not None and name != occ.qid:
                low, high = occ.directive.positional_span
                shift = occ.start - occ.directive.start  # the span is in the lexed block, not in the line
                by_line.setdefault(occ.line, []).append((low + shift, high + shift, name))
        lines = list(self.source.lines[start:end])
        for number, renames in by_line.items():
            line, pieces, at = lines[number - start], [], 0
            for low, high, name in renames:  # in order of column
                pieces += [line[at:low], name]
                at = high
            lines[number - start] = "".join([*pieces, line[at:]])
        front = "---\n" + yaml.safe_dump({"title": "x", "questions": declared}, sort_keys=False) + "---\n\n"
        # A marker is in a marker position in a section, and not in the preamble (§5.7): the region has the heading
        # it is under, unless it is the heading.
        headings = self.source.headings
        under = bool(headings) and headings[0] <= start and start not in self.heading_starts
        return front + ("# x\n\n" if under else "") + "\n".join([*context, *lines]) + "\n"

    def _declaration(self, question: str) -> dict | None:
        """The declaration of decision question *question* as the mini template repeats it, or None."""
        qtype = question_type(self.declared, question)
        if qtype == "boolean":
            return {"type": "boolean"}
        choices = self.declared[question].get("choices") if qtype == "choice" else None
        if isinstance(choices, dict) and choices:
            return {"type": "choice", "choices": {str(key): str(label) for key, label in choices.items()}}
        return None

    def _question(self, occ: Any) -> tuple[dict, list | None] | None:
        """The question that insertion *occ* asks, as the mini template declares it, and the answers it is tried
        with (None for a text question: they are those its places allow, ``_text_witnesses``); None if no answer
        fills it (the question is not one assembly asks, or is not valid: it is left as written)."""
        directive = occ.directive
        if not IDENTIFIER_RE.fullmatch(occ.qid) or directive.positional_span is None:
            return None
        qtype = question_type(self.declared, occ.qid)
        if directive.name == "choose":
            if occ.qid in self.declared:
                declaration = self._declaration(occ.qid)
            else:  # a fragment's host declares it: its phrases are its answers
                declaration = {"type": "choice", "choices": {phrase: phrase for phrase in directive.params}}
            if declaration is None or not _decisions(declaration):
                return None
            return declaration, _decisions(declaration)
        if qtype in DECISION_QUESTION_TYPES:
            return None
        qtype = qtype or directive.params.get("type", "text")
        declaration = {"type": qtype if qtype in VALID_PLACEHOLDER_TYPES else "text"}
        return declaration, (None if declaration["type"] == "text" else _witnesses(declaration["type"], directive))

    def _text_witnesses(self, occ: Any, lines: list[str], start: int) -> list[str]:
        """The text answers that insertion *occ* of the region (*lines*, from line *start*) is tried with: all of
        ``TEXT_WITNESSES``, and the runs of parentheses that the template leaves open or closed around it on its
        line (``_parentheses``)."""
        line = lines[occ.line - start]
        return [*TEXT_WITNESSES, *_parentheses(line[:occ.start], line[occ.end:])]

    def _template(self, text: str, undeclared: dict[str, dict]) -> Any:
        """The mini template *text* as the assembler reads it, with the questions of one answer declared in code;
        or None if the assembler finds a problem in the region. (Reading it is dear: a region is read once.)"""
        mini = self.assembly._read(text, None)
        if mini.problems:
            return None
        # The questions with one answer are declared here, not in the template (reading their YAML is dear).
        mini.declared.update(undeclared)
        return mini

    def _assemble(self, mini: Any, chosen: dict, fixed: dict) -> list[str] | None:
        """The lines of the output when the answers are *chosen* and *fixed*; None where an answer is missing."""
        answers = self.assembly._Answers(ChainMap(chosen, fixed), mini.declared)
        decision = self.assembly._decide(mini, answers, inline=True)
        if decision.missing:
            return None
        return [line.text for line in self.assembly._emit(mini.main, decision.removed[""], answers)]

    def _settled(self, mini: Any, deciding: dict[str, list], fixed: dict) -> bool:
        """Whether no definition can be read in the region whatever its text answers: for every combination of the
        answers to the *deciding* questions, with the others *fixed* (the texts answered by ``SENTINEL``, a typed
        question by its first answer: it is read no further than its ``{{``), every label in the output is dead
        (``_all_dead``). The output is the template's text up to the first answer, which is all that ``_dead``
        reads of it."""
        for combo in product(*deciding.values()):
            lines = self._assemble(mini, dict(zip(deciding, combo, strict=True)), fixed)
            if lines is not None and not _all_dead(lines):
                return False
        return True

    def _enumerate(self, mini: Any, keys: dict[str, list], insertions: set[str]) -> frozenset[str]:
        """The *insertions* of the mini template *mini* of which changing the answer alone changes a definition, over
        every combination of the answers to its *keys*. An insertion is decided by the first combination that
        shows it, and the region once all are."""
        # A question with one answer does not vary: it is fixed in the answers, and in no combination.
        names = [name for name, values in keys.items() if len(values) > 1]
        fixed = {name: values[0] for name, values in keys.items() if len(values) == 1}
        seen: dict[tuple[int, ...], tuple | None] = {}

        def read(combo: tuple[int, ...]) -> tuple | None:
            """The definitions read when the answers are *combo*: None where an answer is missing."""
            if combo not in seen:
                lines = self._assemble(mini, {name: keys[name][at] for name, at in zip(names, combo, strict=True)}, fixed)
                if lines is None:
                    seen[combo] = None
                else:
                    # (Step 8 only collapses blank lines, which separate the same however many there are.)
                    output = "\n".join(lines)
                    if output not in self.reads:
                        self.reads[output] = _definitions(output)
                    seen[combo] = self.reads[output]
            return seen[combo]

        flagged: set[str] = set()
        deciding = insertions & set(names)
        for number, name in enumerate(names):
            if name not in insertions:
                continue
            others = [range(len(keys[each])) for each in names if each != name]
            for rest in product(*others):
                reads = {read((*rest[:number], at, *rest[number:])) for at in range(len(keys[name]))} - {None}
                if len(reads) > 1:
                    flagged.add(name)
                    break
            if flagged >= deciding:
                break
        return frozenset(flagged)


def _without(text: str, occurrences: list, marker: str = "") -> str:
    """*text* with each of the *occurrences* (in order of column) replaced by *marker*: one join, however many."""
    pieces, at = [], 0
    for occ in occurrences:
        pieces += [text[at:occ.start], marker]
        at = occ.end
    pieces.append(text[at:])
    return "".join(pieces)


def _parentheses(before: str, after: str) -> list[str]:
    """The runs of parentheses that a bare destination nests (CommonMark §6.3) which an insertion between *before*
    and *after*, the template text of its line, may close or open: as many ``)`` as *before* leaves open, to the
    number it leaves open, each alone and with the opening of a title or a whole one, and as many ``(`` as *after*
    leaves closed. The sources of directives are text that is not parentheses, and the parentheses of the
    phrases of a choice count (it may be chosen). Up to ``MAX_DEPTH``."""
    closing = min(MAX_DEPTH, _unmatched(before, opening=True))
    opening = min(MAX_DEPTH, _unmatched(after, opening=False))
    found = [")" * each for each in range(2, closing + 1)]
    found += [")" * each + title for each in range(1, closing + 1) for title in _TITLES]
    found += ["(" * each for each in range(2, opening + 1)]
    return [each for each in dict.fromkeys(found) if each not in TEXT_WITNESSES]


def _unmatched(text: str, *, opening: bool) -> int:
    """How many ``(`` that *text* leaves open (*opening*), or ``)`` that it leaves closed, by the template's
    parentheses alone (the source of a directive is opaque, but for the phrases of a ``{{choose:}}``, whose
    parentheses all count)."""
    extra = 0
    letter = "(" if opening else ")"
    for found in _DIRECTIVE_RE.finditer(text):
        if found.group().startswith("{{choose:"):
            extra += found.group().count(letter)
    count = 0
    escaped = False
    for char in _DIRECTIVE_RE.sub("\0", text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            count += 1
        elif char == ")":
            if count:
                count -= 1
            elif not opening:
                extra += 1
    return count + extra if opening else extra


def _runs(lines: set[int]) -> list[tuple[int, int]]:
    """The maximal runs of consecutive numbers in *lines*, as ``(first, after)``."""
    found: list[tuple[int, int]] = []
    for line in sorted(lines):
        if found and found[-1][1] == line:
            found[-1] = (found[-1][0], line + 1)
        else:
            found.append((line, line + 1))
    return found


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The union of the intervals *spans*, as intervals that touch no other."""
    found: list[tuple[int, int]] = []
    for first, after in sorted(spans):
        if found and first <= found[-1][1]:
            found[-1] = (found[-1][0], max(found[-1][1], after))
        else:
            found.append((first, after))
    return found


def _by_line(occurrences: list) -> dict[int, list]:
    found: dict[int, list] = {}
    for occ in occurrences:
        found.setdefault(occ.line, []).append(occ)
    return found


def _items(layout: Any) -> list:
    """Every list item of the body, nested ones too, in order of their marker lines (an item before the
    items in it)."""
    found = []
    pending = [
        item for blocks in reversed(layout.containers()) for block in reversed(blocks) for item in reversed(block.items)
    ]
    while pending:
        item = pending.pop()
        found.append(item)
        pending.extend(inner for block in reversed(item.blocks) for inner in reversed(block.items))
    return found


def _spans(layout: Any) -> list:
    """Every block of the body, in list items too (``parser._BlockSpan``), in no order."""
    found = []
    pending = [block for blocks in layout.containers() for block in blocks]
    while pending:
        block = pending.pop()
        found.append(block)
        pending.extend(inner for item in block.items for inner in item.blocks)
    return found


def _decisions(declaration: dict) -> list:
    """Every answer to a decision question."""
    return [True, False] if declaration["type"] == "boolean" else list(declaration["choices"])


def _witnesses(kind: str, directive: Directive) -> list:
    """The answers a ``{{placeholder:}}`` of type *kind* is tried with."""
    if kind == "date":
        return list(_DATES)
    if kind in ("money", "duration"):
        # The currency or the unit is the placeholder's, or else the answer's (§15.7.2 step 5).
        amounts, field, code = (_AMOUNTS, "amount", "currency") if kind == "money" else (_VALUES, "value", "unit")
        if directive.params.get(code):
            return list(amounts)
        return [{field: each, code: "EUR" if kind == "money" else "MO"} for each in amounts]
    return list(TEXT_WITNESSES)


def _typed(declaration: dict) -> bool:
    """Whether the question *declaration* declares is a typed one: its answers differ by their digits only, and
    assemble to a ``{{date:}}``, ``{{money:}}`` or ``{{duration:}}``."""
    return declaration.get("type") in ("date", "money", "duration")


def _dead(line: str, at: int) -> bool:
    """Whether the link reference definition that the ``[`` at ``line[at]`` could open is none, whatever follows
    the template's text of its line: the label is closed on the line (``label_end``), a template ``:`` follows
    it, and after it, on the line and before the first answer (``SENTINEL``) or ``{{``, there are a bare
    destination (no ``<``, no parentheses, no backslash, no control character), a blank, and a character that
    opens no title. A label closed by a ``]`` that an answer could follow with a colon is not dead.

    Proof, of every branch of ``markdown._read_definition``, under all three readings: ``skip_line_space`` after
    the colon stops at the first character of the run, on the line; ``_scan_destination`` reads exactly the
    run, which ends at the blank; ``skip_line_space`` stops at the character that is no opener, on the same line,
    so that the title is none, and the destination's line is not ended by it: None. Nothing after that
    character was read, so no answer in the slots and no line after it changes that. A label that is not at the
    start of a paragraph is not read at all. ``tests/test_definition_hazards`` holds the oracle of it."""
    close = label_end(line, at)
    if close is None:
        return False
    after = line[close + 1:close + 2]
    if after != ":":
        return after != SENTINEL
    rest = line[close + 2:]
    rest = rest[:min([n for n in (rest.find(SENTINEL), rest.find("{{")) if n >= 0], default=len(rest))]
    i = skip_blanks(rest, 0)
    if i == len(rest) or rest[i] == "<":
        return False
    j = i
    while j < len(rest) and rest[j] not in " \t":
        if rest[j] in "()\\" or rest[j] <= " " or rest[j] == "\x7f":
            return False
        j += 1
    if j == len(rest):
        return False
    k = skip_blanks(rest, j)
    return k < len(rest) and rest[k] not in "\"'("


def _hopeless(line: str) -> bool:
    """Whether the *line* of the template, if a label begins it, is certainly no dead label (``_dead``) in any
    output, from its text up to its first ``{{`` alone: the cutoff is not tried for a region that holds one. It
    only decides whether to try, never that a region is clean. What follows that text is unknown, unless it is
    nothing (the line's end) or a ``{{placeholder:}}`` (an answer, or a typed value: both end what ``_dead``
    reads), and a marker ``{when=}`` at the end of the line is not in the output."""
    found = _LABEL_START_RE.match(line)
    if found is None or "{when=" in line or "{#" in line:
        return False
    cut = line.find("{{")
    head = line if cut < 0 else line[:cut]
    ends = cut < 0 or line.startswith("{{placeholder:", cut)  # (nothing more than the head is read)
    close = label_end(head, found.end() - 1)
    if close is None:
        return cut < 0  # (an answer may be in the label, and close it)
    if head[close + 1:close + 2] != ":":
        return False
    rest = head[close + 2:]
    i = skip_blanks(rest, 0)
    if i == len(rest):
        return ends
    if rest[i] == "<":
        return True
    j = i
    while j < len(rest) and rest[j] not in " \t":
        if rest[j] in "()\\" or rest[j] <= " " or rest[j] == "\x7f":
            return True
        j += 1
    k = skip_blanks(rest, j)
    return ends if k == len(rest) else rest[k] in "\"'("


def _all_dead(lines: list[str]) -> bool:
    """Whether every label that opens a line of the assembled *lines* is dead (``_dead``): a candidate is each
    ``[`` that a definition could begin with, which is more than the definitions there are."""
    for line in lines:
        found = _LABEL_START_RE.match(line)
        if found is not None and not _dead(line, found.end() - 1):
            return False
    return True


def _definitions(output: str) -> tuple[tuple[str, str, str], ...]:
    """The link reference definitions of assembled *output*, as the parser reads them, in order: a definition
    that repeats another is a change too, though the first one wins."""
    document = parse("\n" + output)  # a blank line first: a body is no frontmatter
    return tuple(
        (block.label, block.target, block.title)
        for top in [*document.preamble, *(each for section in document.sections for each in section.blocks)]
        for block in nested_blocks(top)
        if block.kind == "link_definition"
    )
