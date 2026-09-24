"""Template assembly (LegalDown §15.7): a template and an answers set in, an
ordinary LegalDown document out — the Assembly capability (§17.6).

Two rules shape the module.

**Source in, source out.** §15.7.2 is an edit of the template *as written* —
"no other byte of the template changes" — so that two conforming
implementations produce byte-identical output. Parsing into the model and
serializing back would normalize spacing, quoting and blank lines, so the text
is edited line by line and the parsed model is only consulted to *decide*:
which units exist and where their markers are, which quotes are drafting notes,
which identifier a heading generates.

**Decide as the validator decides.** What a condition means, whether an answer
is acceptable, which placeholders fix a currency, whether a document is a
template and which identifier a heading gets are the validator's own rules,
called here rather than re-derived. Where each block lies in the source is
recorded by the parser's own walk (``parser._layout``), so assembly and
validation cannot disagree about a template's structure.

Assembly is given a template that validates without Errors (§15.7.2); the
guarantee that the output validates too (§15.7.4) rests on that. Include
fragments and LegalDown attachment files are read through ``load_file`` (a
Full capability, §17.4); without it such a template is refused rather than
assembled partially (§17.6), and so is a template with ``translations``,
whose linked templates are assembled with it (§15.7.2).
"""
from __future__ import annotations

import bisect
import itertools
import posixpath
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Any

from .directives import PLACEHOLDER_TYPE_PARAMS, Directive, format_value, lex
from .markdown import FENCE_OPEN_RE, HTML_BLOCK_START_RE, LINE_ENDING_RE, indent_width
from .markers import MARKER_RE, Marker, format_marker, parse_marker
from .models import Block, Document
from .parser import FRONTMATTER_RE, _BlockSpan, _Layout, _layout, _opens_paragraph, parse_document
from .validator import validate_document
from .validator.conditions import Condition
from .validator.core import is_template
from .validator.patterns import IDENTIFIER_RE, LEGALDOWN_EXTENSIONS, VALID_PLACEHOLDER_TYPES
from .validator.result import Diagnostic
from .validator.templates import (
    DECISION_QUESTION_TYPES,
    Blank,
    answer_problem,
    block_quotes,
    question_type,
)
from .validator.units import find_markers, is_include_only, own_presence

__all__ = [
    "AssemblyError",
    "AssemblyResult",
    "LoadFile",
    "Question",
    "assemble",
    "needed_questions",
    "template_questions",
]

#: Reads an include fragment or LegalDown attachment file by its path as the
#: template writes it (normalized); ``None`` when there is no such file.
LoadFile = Callable[[str], "str | None"]

#: A question with neither an answer nor a default.
_MISSING = object()


class AssemblyError(ValueError):
    """The template's source could not be mapped onto its parsed structure.

    Never expected for a template the parser reads; raised rather than
    guessing, because a wrong guess edits the wrong lines of a contract."""


@dataclass(slots=True)
class AssemblyResult:
    """What :func:`assemble` produced.

    ``output`` is the assembled template file; ``files`` the assembled include
    fragments and LegalDown attachment files that remain, by relative path — an
    emptied one is ``""``, written as zero bytes (§15.7.2 step 8). Both are
    empty when an Error in ``diagnostics`` stopped assembly (§15.7.2 step 1).
    """

    output: str = ""
    files: dict[str, str] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(d.level == "error" for d in self.diagnostics)


@dataclass(slots=True)
class Question:
    """One question the person filling a template is asked (§15.2): declared
    in ``questions``, or implicit — an undeclared placeholder, with no prompt
    and no default. ``type`` is the effective type; ``currency``/``unit`` is
    set when every placeholder for the question fixes the same one, which is
    when the bare amount or value is an acceptable answer (§15.7.1)."""

    id: str
    type: str
    prompt: str = ""
    default: Any = None
    choices: dict[str, str] = field(default_factory=dict)
    currency: str | None = None
    unit: str | None = None
    declared: bool = True

    @property
    def is_decision(self) -> bool:
        return self.type in DECISION_QUESTION_TYPES


# ── Where the parser's blocks lie in the source ──────────────────

# The parsed block kinds, by what assembly does with them.
_KINDS = {
    "paragraph": "paragraph", "definition": "paragraph", "ref": "paragraph", "term": "paragraph",
    "ordered_list": "list", "unordered_list": "list",
    "quote": "quote", "code": "code", "table": "table", "rule": "rule", "html": "html",
}


def _kind(span: _BlockSpan) -> str:
    return _KINDS.get(span.kind, span.kind)


def _pairs(layout: _Layout, document: Document) -> Iterator[tuple[int | None, int, _BlockSpan, Block]]:
    """Each source block with its model block: ``(section, index, source, model)``."""
    model = [document.preamble, *(section.blocks for section in document.sections)]
    spans = layout.containers()
    # The layout is recorded by the walk that builds the model, block for
    # block; anything else is a bug, and would edit the wrong lines.
    if len(spans) != len(model) or any(
        len(a) != len(b) or any(span.kind != block.kind for span, block in zip(a, b, strict=True))
        for a, b in zip(spans, model, strict=True)
    ):
        raise AssemblyError("The template's source does not match its parsed structure.")
    for container, (container_spans, blocks) in enumerate(zip(spans, model, strict=True)):
        for index, (span, block) in enumerate(zip(container_spans, blocks, strict=True)):
            yield (None if container == 0 else container - 1), index, span, block


def _trim(lines: list[str], start: int, end: int) -> int:
    """*end* moved back past blank lines: a unit runs through its last
    non-blank line (§15.7.2 step 2)."""
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return end


# ── What a file holds ────────────────────────────────────────────


@dataclass(slots=True)
class _Unit:
    """A conditional unit (§15.3): lines ``[start, end)``, present when *test*
    holds (``None``: an invalid condition, which the validator reports and
    reads as always present). *marker* is its marker as written, on
    *marker_line*, for step 3."""

    start: int
    end: int
    test: Condition | None
    marker_line: int
    marker: str


@dataclass(slots=True)
class _Occurrence:
    """A ``{{placeholder:}}`` or ``{{choose:}}``: columns ``[start, end)`` of
    *line*. In frontmatter, *quote* is the YAML quote around it."""

    directive: Directive
    line: int
    start: int
    end: int
    quote: str = ""
    in_table: bool = False  # in a table row, where ``\|`` is a pipe (GFM)

    @property
    def qid(self) -> str:
        return self.directive.positional or ""


@dataclass(slots=True)
class _Include:
    start: int
    end: int
    path: str


@dataclass(slots=True)
class _Source:
    """One LegalDown file of a template — the template's body, an include
    fragment, or a LegalDown attachment file — and what is in it."""

    path: str  # "" for the template itself
    lines: list[str]
    units: list[_Unit]
    notes: set[int]  # drafting-note lines
    includes: list[_Include]
    occurrences: list[_Occurrence]
    newline: str = "\n"
    #: The lines that begin a list item, as the parser read them: the item
    #: marker there is a container marker (§15.7.3).
    item_lines: frozenset[int] = frozenset()
    malformed: list[Directive] = field(default_factory=list)  # placeholders and choices
    nested: bool = False  # an ``{{include:}}`` is written in it


def _unix(text: str) -> tuple[str, str]:
    """*text* without a byte-order mark and with LF line breaks — the line
    endings CommonMark and the parser know: LF, CR, CRLF — and the line
    break it was written with, its first one. A CRLF (or CR) file is
    assembled as LF and written back with it, so that no byte assembly does
    not edit changes."""
    text = text.removeprefix("\ufeff")
    first = LINE_ENDING_RE.search(text)
    newline = first.group(0) if first else "\n"
    return LINE_ENDING_RE.sub("\n", text), newline


def _test(condition: str, questions: Any) -> Condition | None:
    return next(iter(own_presence(condition, questions)), None)


def _read_body(
    path: str, body: str, document: Document, questions: Any, *, template: bool
) -> _Source:
    lines = body.split("\n")
    layout = _layout(lines)
    found: dict[tuple[int | None, int], list] = {}
    for marker in find_markers(document, cache(lex)):
        if marker.placed(template) and marker.marker is not None:
            found.setdefault((marker.section, marker.block), []).append(marker)
    source = _Source(path, lines, _section_units(layout, document, lines, questions), set(), [], [])
    tails = _tails(layout)
    for section, index, span, block in _pairs(layout, document):
        markers = found.get((section, index), [])
        if _kind(span) == "list":
            _read_list(source, span, block, markers, questions, tails.get(id(span), []))
        elif _kind(span) == "paragraph":
            _read_paragraph(source, span, block, markers, questions)
        elif _kind(span) == "quote":
            for first, last in _note_lines(block.text):
                source.notes.update(range(span.start + first, span.start + last + 1))
    source.occurrences, source.malformed, source.nested = _occurrences(layout, lines)
    source.item_lines = frozenset(
        first for blocks in layout.containers() for block in blocks for first, _raw in block.items
    )
    return source


def _tails(layout: _Layout) -> dict[int, list[_BlockSpan]]:
    """The paragraphs after each list (by ``id``) that CommonMark reads as
    part of its last item: indented, and read as paragraphs only because
    they follow the list (``_BlockSpan.tail``)."""
    tails: dict[int, list[_BlockSpan]] = {}
    for spans in layout.containers():
        for k, span in enumerate(spans):
            if _kind(span) == "list":
                tails[id(span)] = list(itertools.takewhile(lambda s: s.tail, spans[k + 1:]))
    return tails


def _content_column(line: str) -> int:
    """The column where the content of the list item beginning on *line*
    starts (CommonMark): after its marker and one to four columns of spacing,
    or one column when there are more, or when the item is empty."""
    marker = re.match(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])", line)
    after = len(line[:marker.end()].expandtabs(4))
    rest = line[marker.end():]
    if not rest.strip():
        return after + 1
    spacing = len(line[:len(line) - len(rest.lstrip(" \t"))].expandtabs(4)) - after
    return after + 1 if spacing > 4 else after + spacing


def _section_units(
    layout: _Layout, document: Document, lines: list[str], questions: Any
) -> list[_Unit]:
    """A conditional section runs to the next heading of the same or a higher
    level in its own file (§15.3)."""
    units = []
    headings = layout.headings
    for index, (heading, section) in enumerate(zip(headings, document.sections, strict=True)):
        if not section.condition:
            continue
        later = [h.start for h in headings[index + 1:] if h.level <= heading.level]
        end = later[0] if later else len(lines)
        written = Marker(section.identifier, section.condition)
        marker = _marker_source(lines[heading.marker_line], written)
        units.append(_Unit(heading.start, _trim(lines, heading.start, end),
                           _test(section.condition, questions), heading.marker_line, marker))
    return units


def _marker_source(line: str, marker: Marker) -> str:
    """How *marker* is written on heading *line*: its last match."""
    for match in reversed(list(MARKER_RE.finditer(line))):
        if parse_marker(match.group(0)) == marker:
            return match.group(0)
    return ""


def _marker_line(lines: list[str], start: int, end: int, marker: str) -> int:
    """The last line of ``[start, end)`` holding *marker*, which ends its unit."""
    return next((i for i in range(end - 1, start - 1, -1) if marker in lines[i]), end - 1)


def _read_paragraph(
    source: _Source, span: _BlockSpan, block: Block, markers: list, questions: Any
) -> None:
    """A conditional paragraph, and whether it is an include paragraph: one
    holding a single ``{{include:}}`` and at most its marker (§12.2)."""
    marker = markers[0] if markers else None  # a paragraph ends in one marker at most
    if marker is not None and marker.marker.condition:
        line = _marker_line(source.lines, span.start, span.end, marker.source)
        source.units.append(_Unit(span.start, span.end, _test(marker.marker.condition, questions),
                                  line, marker.source))
    if block.kind != "paragraph":
        return
    at = _last_marker_at(block.text, marker.source) if marker is not None else -1
    text = block.text[:at] if at >= 0 else block.text
    directives = lex(block.text).directives
    if is_include_only(text, directives):
        include = next(d for d in directives if d.name == "include")
        path = posixpath.normpath(include.positional)
        source.includes.append(_Include(span.start, span.end, path))


def _read_list(source: _Source, span: _BlockSpan, block: Block, markers: list, questions: Any,
               tails: list[_BlockSpan]) -> None:
    """Each item runs through its nested items; its marker ends its first
    paragraph (§5.7), which the parser joins onto the item text's first line.
    An item the list ends in also runs through the *tails* indented to its
    content, which CommonMark reads as its later paragraphs."""
    lines, items = source.lines, span.items
    starts = [first for first, _raw in items] + [span.end]
    listed = []  # the items the model keeps: it drops empty ones
    for k, (first, raw) in enumerate(items):
        own_end, indent = starts[k + 1], indent_width(lines[first])
        full_end = next(
            (s for s, _raw in items[k + 1:] if indent_width(lines[s]) <= indent), None
        )
        if full_end is None:
            full_end, column = span.end, _content_column(lines[first])
            for tail in tails:
                if indent_width(lines[tail.start]) < column:
                    break  # CommonMark closes the item here
                full_end = tail.end
        text_lines = raw.count("\n") + 1
        paragraph_end = own_end - (text_lines - 1)
        if raw.strip():
            listed.append((first, _trim(lines, first, full_end), paragraph_end))
        for note_first, note_last in _note_lines(raw, inside_list=True):
            source.notes.update(range(
                first if note_first == 0 else paragraph_end + note_first - 1,
                paragraph_end + note_last,
            ))
    for marker in markers:
        if marker.marker.condition and marker.fragment < len(listed):
            first, end, paragraph_end = listed[marker.fragment]
            line = _marker_line(lines, first, paragraph_end, marker.source)
            source.units.append(_Unit(first, end, _test(marker.marker.condition, questions),
                                      line, marker.source))


def _note_lines(text: str, *, inside_list: bool = False) -> Iterator[tuple[int, int]]:
    """The first and last line, within *text*, of each drafting note in a
    quote block's text or a list item's (§15.6), as the validator finds them."""
    block = Block(kind="unordered_list", items=[text]) if inside_list else Block(kind="quote", text=text)
    for quote in block_quotes(block):
        if quote.is_drafting_note:
            yield text.count("\n", 0, quote.start), text.count("\n", 0, quote.end)


def _occurrences(
    layout: _Layout, lines: list[str]
) -> tuple[list[_Occurrence], list[Directive], bool]:
    """Every placeholder and choice in the body, lexed block by block as the
    validator lexes them, so a code span or comment hides the same ones. No
    directive is recognized in code or raw HTML (§11.4). Also the malformed
    ones, and whether an ``{{include:}}`` is written anywhere."""
    found: list[_Occurrence] = []
    malformed: list[Directive] = []
    includes = False
    for blocks in layout.containers():
        for block in blocks:
            if _kind(block) in ("code", "rule", "html"):
                continue
            text = "\n".join(lines[block.start:block.end])
            offsets = [0] + [i + 1 for i, char in enumerate(text) if char == "\n"]
            for directive in lex(text).directives:
                includes |= directive.name == "include"
                if directive.name not in ("placeholder", "choose"):
                    continue
                if directive.malformed:
                    # The parser joins a paragraph's lines with spaces, so
                    # one written across lines is well-formed to the
                    # validator — and could not be filled here.
                    malformed.append(directive)
                    continue
                row = bisect.bisect_right(offsets, directive.start) - 1
                column = directive.start - offsets[row]
                found.append(_Occurrence(directive, block.start + row, column,
                                         column + directive.end - directive.start,
                                         in_table=_kind(block) == "table"))
    return found, malformed, includes


# ── Frontmatter ──────────────────────────────────────────────────


@dataclass(slots=True)
class _Attachment:
    start: int
    end: int
    when: tuple[int, int] | None  # its `when` entry
    test: Condition | None
    file: str  # the normalized path of a LegalDown attachment file, else ""


@dataclass(slots=True)
class _Frontmatter:
    head: str  # through the opening delimiter
    lines: list[str]
    tail: str  # the closing delimiter
    questions: tuple[int, int] | None = None
    attachments: tuple[int, int] | None = None
    items: list[_Attachment] = field(default_factory=list)
    occurrences: list[_Occurrence] = field(default_factory=list)
    malformed: list[Directive] = field(default_factory=list)

    def text(self, lines: list[str] | None = None, drop: frozenset[int] = frozenset()) -> str:
        """The frontmatter, delimiters included, written from *lines* (its
        own by default) without the lines in *drop*."""
        lines = self.lines if lines is None else lines
        kept = [line for i, line in enumerate(lines) if i not in drop]
        return self.head + "\n".join(kept) + self.tail


def _split(source: str, document: Document) -> tuple[_Frontmatter | None, str]:
    """*source*'s frontmatter and body, split as the parser split them into
    *document*: a ``---`` block whose YAML is not a mapping is body."""
    match = FRONTMATTER_RE.match(source)
    if not match or document.metadata.frontmatter_absent:
        return None, source
    if match.group(1) is None:
        return _Frontmatter(source[:match.end()], [], ""), source[match.end():]
    head, tail = source[:match.start(1)], source[match.end(1):match.end()]
    return _Frontmatter(head, match.group(1).split("\n"), tail), source[match.end():]


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _entry_end(lines: list[str], index: int) -> int:
    """The end of the frontmatter entry at *index* (§15.7.2): the lines after
    it that are blank, comments, or indented more deeply — and a key's
    sequence items written at its own column — less trailing blank and
    comment lines."""
    indent = indent_width(lines[index])
    is_key = not lines[index].lstrip().startswith("-")
    end = last = index + 1
    while end < len(lines):
        line = lines[end]
        content = line.strip() and not _is_comment(line)
        if content and indent_width(line) <= indent and not (
            is_key and indent_width(line) == indent and line.lstrip().startswith("-")
        ):
            break
        end += 1
        if content:
            last = end
    return last


def _top_entry(lines: list[str], key: str) -> tuple[int, int] | None:
    pattern = re.compile(rf"{key}[ \t]*:")
    index = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    return None if index is None else (index, _entry_end(lines, index))


_WHEN_RE = re.compile(r"[ \t]+when[ \t]*:")
_DASH_RE = re.compile(r"[ \t]*-(?:[ \t]+|$)")
# What may stand before a quote that opens a quoted YAML scalar: the line
# start, a sequence dash, a key's colon, or a flow collection's bracket or
# comma. A quote anywhere else is a character of a plain scalar.
_SCALAR_OPENERS = frozenset("-:,[{?")


def _quoted_scalars(line: str) -> list[tuple[int, int, str]]:
    """``(start, end, quote)`` of every quoted scalar on frontmatter *line*,
    in block and flow style alike (``- {name: a, legal_name: "..."}``), so a
    placeholder is filled wherever §3.10 lets it stand. Stops at a comment."""
    spans: list[tuple[int, int, str]] = []
    index = 0
    while index < len(line):
        char = line[index]
        before = line[:index].rstrip(" \t")
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            break
        if char in "\"'" and (index == 0 or line[index - 1] in " \t[{,") and (
            not before or before[-1] in _SCALAR_OPENERS
        ):
            end = index + 1
            while end < len(line):
                if char == '"' and line[end] == "\\":
                    end += 2
                    continue
                if line[end] == char:
                    if char == "'" and line[end + 1:end + 2] == "'":
                        end += 2
                        continue
                    break
                end += 1
            spans.append((index, end + 1, char))
            index = end + 1
            continue
        index += 1
    return spans


def _read_frontmatter(front: _Frontmatter, document: Document, questions: Any) -> None:
    lines = front.lines
    front.questions = _top_entry(lines, "questions")
    front.attachments = _top_entry(lines, "attachments")
    if front.attachments is not None:
        front.items = _attachment_items(lines, front.attachments, document, questions)
    skip = set(range(*front.questions)) if front.questions else set()
    for index, line in enumerate(lines):
        if index in skip or _is_comment(line) or "{{" not in line:
            continue
        scalars = _quoted_scalars(line)
        for directive in lex(line).directives:
            if directive.name == "placeholder" and directive.malformed:
                front.malformed.append(directive)  # a YAML scalar across lines
            elif directive.name == "placeholder":
                # Only a quoted scalar can hold one (§3.10); any other is
                # left for the validator, never filled with unescaped text.
                quote = next((
                    q for start, end, q in scalars
                    if start < directive.start and directive.end < end
                ), "")
                front.occurrences.append(_Occurrence(
                    directive, index, directive.start, directive.end, quote,
                ))


def _attachment_items(lines: list[str], entry: tuple[int, int], document: Document,
                      questions: Any) -> list[_Attachment]:
    """The entries of a block-style ``attachments`` list (§15.2), each paired
    with the parsed attachment it declares."""
    starts, dash = [], None
    for index in range(entry[0] + 1, entry[1]):
        if _DASH_RE.match(lines[index]) and dash in (None, indent_width(lines[index])):
            dash = indent_width(lines[index])
            starts.append(index)
    if len(starts) != len(document.metadata.attachments):
        raise AssemblyError("The attachments entries do not match the parsed attachments.")
    items = []
    for start, attachment in zip(starts, document.metadata.attachments, strict=True):
        end = _entry_end(lines, start)
        field_indent = len(_DASH_RE.match(lines[start]).group(0))
        when = next((i for i in range(start + 1, end)
                     if _WHEN_RE.match(lines[i]) and indent_width(lines[i]) == field_indent), None)
        legaldown = attachment.file.endswith(LEGALDOWN_EXTENSIONS)
        items.append(_Attachment(
            start,
            end,
            None if when is None else (when, _entry_end(lines, when)),
            _test(attachment.when, questions) if attachment.when else None,
            posixpath.normpath(attachment.file) if legaldown else "",
        ))
    return items


# ── The template as a whole ──────────────────────────────────────


@dataclass(slots=True)
class _Template:
    front: _Frontmatter | None
    main: _Source
    subs: dict[str, _Source]
    declared: dict
    blanks: dict[str, Blank]
    types: dict[str, str]  # every question's effective type: declared, then implicit
    bom: str
    problems: list[Diagnostic]


def _read(template: str, load_file: LoadFile | None) -> _Template:
    bom = "\ufeff" if template.startswith("\ufeff") else ""
    source, newline = _unix(template)
    document = parse_document(source)
    declared = document.metadata.questions if isinstance(document.metadata.questions, dict) else {}
    template_like = is_template(document)
    front, body = _split(source, document)
    main = _read_body("", body, document, declared, template=template_like)
    main.newline = newline
    if front is not None:
        _read_frontmatter(front, document, declared)
    subs, problems = _read_files(main, front, load_file, declared, template_like)
    problems += _malformed(main, *subs.values(), front=front)
    for path in document.metadata.translations.values():
        # A translation group is assembled together (§15.7.2), which this
        # implementation does not do: it refuses rather than assemble one
        # template of the group (§17.6).
        problems.append(Diagnostic(
            "translation-file-missing", "error",
            f"The template links the translation '{path}', which is assembled with it; "
            f"assembling a translation group is not supported, so the template is not assembled.",
        ))
    occurrences = [(occ, True) for occ in (front.occurrences if front else [])] + [
        (occ, False) for src in (main, *subs.values()) for occ in src.occurrences
    ]
    blanks = _blanks(occurrences, declared)
    types = {qid: qtype for qid in declared if (qtype := question_type(declared, qid))}
    for pid, blank in blanks.items():
        types.setdefault(pid, blank.type or "text")
    return _Template(front, main, subs, declared, blanks, types, bom, problems)


def _read_files(main: _Source, front: _Frontmatter | None, load_file: LoadFile | None,
                declared: dict, template: bool) -> tuple[dict[str, _Source], list[Diagnostic]]:
    """Read every include fragment and LegalDown attachment file — the absent
    ones too: their placeholders are the template's (answer-unknown) and their
    headings take part in identifier generation (step 7)."""
    wanted = [(include.path, "include") for include in main.includes]
    items = front.items if front else []
    wanted += [(item.file, "attachment") for item in items if item.file]
    subs: dict[str, _Source] = {}
    problems: list[Diagnostic] = []
    for path, kind in wanted:
        if path in subs:
            continue
        text = load_file(path) if load_file is not None else None
        if text is None:
            reason = "cannot be read" if load_file else "needs the Full level (§17.6)"
            message = f"'{path}' {reason}; the template is not assembled."
            problems.append(Diagnostic(f"{kind}-file-missing", "error", message))
            continue
        text, newline = _unix(text)
        sub_document = parse_document(text)
        sub_front, body = _split(text, sub_document)
        subs[path] = _read_body(path, body, sub_document, declared, template=template)
        subs[path].newline = newline
        problems += _file_problems(path, kind, subs[path], sub_front, sub_document, template)
    return subs, problems


def _file_problems(path: str, kind: str, source: _Source, front: _Frontmatter | None,
                   document: Document, template: bool) -> list[Diagnostic]:
    """The Full checks on a loaded file (§16.10, §16.11, §16.12) that the
    assembled output depends on; the file is read here, so they are made
    here rather than left to a validator that may not read it."""
    what = "fragment" if kind == "include" else "attachment file"
    problems = []

    def error(rule: str, message: str) -> None:
        problems.append(Diagnostic(rule, "error", f"The {what} '{path}' {message}"))

    if front is not None:
        error(f"{kind}-has-frontmatter", "has frontmatter, which it cannot have; the template is "
                                         "not assembled.")
    if any(section.level == 1 for section in document.sections):
        error(f"{kind}-has-h1", "has a level 1 heading, which it cannot have; the template is "
                                "not assembled.")
    if source.nested:
        if template:
            error("template-fragment-invalid", "holds an {{include:}}; in a template an include "
                                               "belongs in the template's own body (§15.3).")
        else:
            error("include-file-missing", "holds an {{include:}}; an include in an included or "
                                          "attached file is not assembled, so the template is not "
                                          "assembled.")
    if template and kind == "include":
        # §15.3: a fragment holds no conditions or drafting notes and gives
        # every heading an explicit identifier. An attachment file may hold
        # conditions: its own, beneath its attachment's `when`.
        conditions = any(section.condition for section in document.sections) or any(
            marker.marker is not None and marker.marker.condition
            for marker in find_markers(document, cache(lex))
        )
        if conditions:
            error("template-fragment-invalid", "holds a condition; to make a fragment conditional, "
                                               "put the condition on its {{include:}} paragraph (§15.3).")
        if source.notes:
            error("template-fragment-invalid", "holds a drafting note, which belongs in the "
                                               "template's own body (§15.3).")
        if any(not section.identifier for section in document.sections):
            error("template-fragment-invalid", "has a heading without an explicit identifier, "
                                               "which every heading of a fragment has (§15.3).")
    return problems


def _malformed(*sources: _Source, front: _Frontmatter | None) -> list[Diagnostic]:
    """A placeholder or choice the lexer cannot read — one written across
    lines, which the parser joins into one — cannot be filled, so the
    template is refused rather than assembled with it unfilled."""
    found = [(source.path, d) for source in sources for d in source.malformed]
    found += [("", d) for d in (front.malformed if front else [])]
    return [
        Diagnostic("directive-malformed", "error",
                   f"'{directive.source[:57] + '...' if len(directive.source) > 60 else directive.source}'"
                   f"{f' in {path!r}' if path else ''} is not a well-formed directive on one line "
                   f"({directive.malformed}); the template is not assembled.")
        for path, directive in found
    ]


def _effective_type(directive: Directive, declared: dict) -> str:
    """A placeholder's effective type (§10.7, §15.2)."""
    qtype = question_type(declared, directive.positional or "")
    return qtype if qtype in VALID_PLACEHOLDER_TYPES else directive.params.get("type", "text")


def _blanks(occurrences: list[tuple[_Occurrence, bool]], declared: dict) -> dict[str, Blank]:
    """Each placeholder id's blank, recorded as the validator records it: the
    type of its first occurrence, the currency or unit each occurrence of
    that type fixes, and whether one is in frontmatter."""
    blanks: dict[str, Blank] = {}
    for occ, in_frontmatter in occurrences:
        if occ.directive.name != "placeholder" or not IDENTIFIER_RE.fullmatch(occ.qid):
            continue
        ptype = _effective_type(occ.directive, declared)
        blank = blanks.setdefault(occ.qid, Blank())
        blank.in_frontmatter |= in_frontmatter
        blank.type = blank.type or ptype
        if ptype == blank.type and ptype in PLACEHOLDER_TYPE_PARAMS:
            blank.codes.add(occ.directive.params.get(PLACEHOLDER_TYPE_PARAMS[ptype], ""))
    return blanks


def _fixed(blank: Blank | None) -> str | None:
    """The currency or unit every placeholder of *blank* fixes, if one."""
    if blank is None or len(blank.codes) != 1 or "" in blank.codes:
        return None
    return next(iter(blank.codes))


def _questions(t: _Template) -> list[Question]:
    questions = []
    for qid, qtype in t.types.items():
        declaration = t.declared.get(qid)
        declaration = declaration if isinstance(declaration, dict) else {}
        choices = declaration.get("choices")
        fixed = _fixed(t.blanks.get(qid))
        questions.append(Question(
            id=qid,
            type=qtype,
            prompt=str(declaration.get("prompt") or ""),
            default=declaration.get("default"),
            choices=(
                {str(k): str(v) for k, v in choices.items()}
                if qtype == "choice" and isinstance(choices, dict)
                else {}
            ),
            currency=fixed if qtype == "money" else None,
            unit=fixed if qtype == "duration" else None,
            declared=qid in t.declared,
        ))
    return questions


# ── Step 1: answers and what is present ──────────────────────────


class _Answers:
    """The answer each question takes (§15.7.2 step 1): the answers set's,
    else its declaration's ``default``."""

    def __init__(self, answers: Mapping[str, Any], declared: dict) -> None:
        self._answers, self.declared = answers, declared

    def get(self, qid: str) -> Any:
        value = self._answers.get(qid)
        if value is None:
            declaration = self.declared.get(qid)
            value = declaration.get("default") if isinstance(declaration, dict) else None
        return _MISSING if value is None else value


def _answer_diagnostics(t: _Template, answers: Mapping[str, Any]) -> list[Diagnostic]:
    diagnostics = [
        Diagnostic("answer-unknown", "warning",
                   f"'{key}' is neither a question nor a placeholder of the template; its answer "
                   f"is ignored (§15.7.1).")
        for key in answers
        if not isinstance(key, str) or (key not in t.types and key not in t.declared)
    ]
    resolved = _Answers(answers, t.declared)
    for qid, qtype in t.types.items():
        answer = resolved.get(qid)
        if answer is _MISSING:
            continue
        declaration = t.declared.get(qid)
        choices = declaration.get("choices") if isinstance(declaration, dict) else None
        problem = answer_problem(qtype, answer, choices=choices, blank=t.blanks.get(qid))
        if problem:
            whose = "answer to" if answers.get(qid) is not None else "default of"
            diagnostics.append(Diagnostic("answer-invalid", "error",
                                          f"The {whose} '{qid}' {problem} (§15.7.1)."))
    return diagnostics


@dataclass(slots=True)
class _Decision:
    """Which lines assembly removes, and which questions it needs."""

    removed: dict[str, set[int]] = field(default_factory=dict)  # by file path
    files: dict[str, bool | None] = field(default_factory=dict)  # None: not decided yet
    attachments: list[bool | None] = field(default_factory=list)
    needed: dict[str, None] = field(default_factory=dict)  # an ordered set
    missing: dict[str, None] = field(default_factory=dict)


def _decide(t: _Template, answers: _Answers) -> _Decision:
    """Evaluate every condition that is reached (§15.7.2 steps 1–2). A unit
    inside a removed one is never reached, so its question is not needed; one
    under an unanswered condition is not reached *yet*."""
    decision = _Decision()

    def holds(test: Condition | None) -> bool | None:
        if test is None:
            return True
        decision.needed.setdefault(test.question)
        answer = answers.get(test.question)
        if answer is _MISSING:
            decision.missing.setdefault(test.question)
            return None
        return test.holds(answer)

    def use(occ: _Occurrence) -> None:
        decision.needed.setdefault(occ.qid)
        # A choice naming anything but a decision question is choose-invalid,
        # the validator's to report; it is left as written.
        decides = question_type(t.declared, occ.qid) in DECISION_QUESTION_TYPES
        if occ.directive.name == "choose" and decides and answers.get(occ.qid) is _MISSING:
            decision.missing.setdefault(occ.qid)

    front = t.front
    decision.attachments = [holds(item.test) for item in (front.items if front else [])]
    dropped = {
        line
        for item, kept in zip(front.items if front else [], decision.attachments, strict=True)
        if not kept
        for line in range(item.start, item.end)
    }
    for occ in front.occurrences if front else []:
        if occ.line not in dropped:  # a blank in a removed attachment's title
            use(occ)
    decision.removed[""], pending = _walk(t.main, holds, use)
    for include in t.main.includes:
        if include.start in decision.removed[""]:
            decision.files[include.path] = False
        else:
            decision.files[include.path] = None if include.start in pending else True
    for item, kept in zip(front.items if front else [], decision.attachments, strict=True):
        if item.file:
            decision.files[item.file] = kept
    for path, source in t.subs.items():
        if decision.files.get(path):
            decision.removed[path], _pending = _walk(source, holds, use)
    return decision


def _walk(source: _Source, holds: Callable, use: Callable) -> tuple[set[int], set[int]]:
    """The lines of *source* assembly removes — drafting notes first, then
    absent units — and those waiting on an unanswered question. Units and
    insertions are visited in source order, so an enclosing unit is decided
    before anything inside it."""
    removed, pending = set(source.notes), set()
    events = sorted(
        [(unit.start, 0, k) for k, unit in enumerate(source.units)]
        + [(occ.line, 1, k) for k, occ in enumerate(source.occurrences)]
    )
    for line, kind, k in events:
        if line in removed or line in pending:
            continue
        if kind == 1:
            use(source.occurrences[k])
            continue
        unit = source.units[k]
        verdict = holds(unit.test)
        if verdict is not True:
            (removed if verdict is False else pending).update(range(unit.start, unit.end))
    return removed, pending


# ── Steps 3–5: markers, choices, blanks ──────────────────────────


@dataclass(slots=True)
class _Line:
    """An output line: *origin* is the template line it came from."""

    text: str
    origin: int
    check: bool = False  # needs the line-start check (§15.7.3)
    first: int | None = None  # the column of its first insertion, as written


def _last_marker_at(text: str, marker: str) -> int:
    """Where the marker *marker* is in *text*: its last occurrence outside
    comments and code spans. A plain ``rfind`` found a copy inside a
    trailing comment (``{when=c} <!-- {when=c} -->``) and edited that,
    leaving the real marker in the contract."""
    return lex(text).view.rfind(marker)


def _without_condition(text: str, marker: str) -> str:
    """Step 3: *marker* on *text* without its ``when=``; an emptied marker
    goes with the spaces and tabs before it."""
    at = _last_marker_at(text, marker)
    parsed = parse_marker(marker)
    if at < 0 or parsed is None:
        return text
    kept = format_marker(Marker(parsed.identifier, ""))
    before = text[:at] if kept else text[:at].rstrip(" \t")
    return before + kept + text[at + len(marker):]


def _emit(source: _Source, removed: set[int], answers: _Answers) -> list[_Line]:
    """Steps 3–5 on the lines of *source* that step 2 keeps."""
    texts = list(source.lines)
    for unit in source.units:
        if unit.start not in removed and unit.marker:
            texts[unit.marker_line] = _without_condition(texts[unit.marker_line], unit.marker)
    by_line: dict[int, list[_Occurrence]] = {}
    for occ in source.occurrences:
        by_line.setdefault(occ.line, []).append(occ)
    lines: list[_Line] = []
    follows_deleted = False
    for index, text in enumerate(texts):
        if index in removed:
            continue
        line = _Line(text, index, check=follows_deleted)
        follows_deleted = False
        if index in by_line:
            line.text, line.first = _fill(text, by_line[index], answers)
            if text.strip() and not line.text.strip():
                follows_deleted = True  # emptied by steps 4–5: deleted (step 5)
                continue
            line.check |= line.first is not None
        lines.append(line)
    for k, line in enumerate(lines):
        if line.check:
            line.text = _line_start(line, lines[k - 1].text if k else None, source)
    return lines


def _fill(text: str, occurrences: list[_Occurrence], answers: _Answers) -> tuple[str, int | None]:
    """Steps 4–5 on one body line, right to left, so that each insertion is
    escaped against the text that finally follows it (§15.7.3). Returns the
    line and the column of its first insertion."""
    first = None
    for occ in sorted(occurrences, key=lambda o: o.start, reverse=True):
        following = text[occ.end:]
        replaced = _body_replacement(occ, answers, following)
        if replaced is None:
            continue
        new, inserted = replaced
        text = text[:occ.start] + new + following
        if inserted:
            first = occ.start
    return text, first


def _body_replacement(
    occ: _Occurrence, answers: _Answers, following: str
) -> tuple[str, bool] | None:
    """What replaces *occ* in the body, and whether it is an insertion; None
    to leave it as it is."""
    directive, ends_line = occ.directive, following == ""
    qtype = question_type(answers.declared, occ.qid)
    answer = answers.get(occ.qid)
    if directive.name == "choose":
        key = _choice_key(qtype, answer)
        params = _cell_params(directive) if occ.in_table else directive.params
        if key is None or key not in params:
            return None
        return _escape(_end_trimmed(params[key], ends_line), following), True
    if qtype in DECISION_QUESTION_TYPES:
        return None  # placeholder-question-mismatch: the validator's to report
    if answer is _MISSING:
        typed = _typed(directive, qtype)
        return None if typed is None else (typed, False)
    ptype = _effective_type(directive, answers.declared)
    if ptype == "text":
        return _escape(_end_trimmed(str(answer), ends_line), following), True
    return _value_directive(directive, ptype, answer), True


def _cell_params(directive: Directive) -> dict[str, str]:
    """A choice's values as the validator reads them in a table cell: GFM
    turns each ``\\|`` of the row into ``|`` before the cell's text is read
    (``parser._split_row``), so that ``"p\\|q"`` is ``p|q``."""
    cell = re.sub(r"\\\|", "|", directive.source)
    lexed = lex(cell).directives
    return lexed[0].params if len(lexed) == 1 and not lexed[0].malformed else directive.params


def _choice_key(qtype: str | None, answer: Any) -> str | None:
    if answer is _MISSING:
        return None
    if qtype == "boolean":
        return "true" if answer is True else "false"
    return answer if isinstance(answer, str) else None


def _end_trimmed(text: str, ends_line: bool) -> str:
    """An insertion that ends its line loses its trailing spaces and tabs,
    so that it cannot form a hard line break (§15.7.3)."""
    return text.rstrip(" \t") if ends_line else text


# Inserted text is literal (§15.7.3): these would form emphasis, links, code,
# table cells, directives, anchors or conditions.
_ESCAPED = frozenset("\\`*_[]<|{")


def _escape(text: str, following: str) -> str:
    """*text* escaped for insertion before *following* (§15.7.3). An ``&`` is
    escaped only where a character reference could form, even with the
    template text after it, so ``Smith & Co.`` is inserted unchanged."""
    out = []
    for index, char in enumerate(text):
        after = text[index + 1:index + 2] or following[:1]
        reference = after == "#" or (after.isascii() and after.isalpha())
        if char in _ESCAPED or (char == "&" and reference):
            out.append("\\")
        out.append(char)
    return "".join(out)


def _iso(answer: Any) -> str:
    return answer.isoformat()[:10] if isinstance(answer, date) else str(answer)


def _measure(ptype: str, directive: Directive, answer: Any) -> tuple[str, str]:
    """A money or duration answer's amount or value and its currency or unit:
    the placeholder's parameter, or else the answer's (§15.7.2 step 5)."""
    param = PLACEHOLDER_TYPE_PARAMS[ptype]
    amount_key = "amount" if ptype == "money" else "value"
    if isinstance(answer, dict):
        amount, code = answer.get(amount_key), answer.get(param)
    else:
        amount, code = answer, ""
    return str(amount), directive.params.get(param) or str(code)


def _value_directive(directive: Directive, ptype: str, answer: Any) -> str:
    """``{{date:}}``, ``{{money:}}`` or ``{{duration:}}`` for a filled blank,
    with the placeholder's ``note`` exactly as written."""
    if ptype == "date":
        arguments = [format_value(_iso(answer), positional=True)]
    else:
        amount, code = _measure(ptype, directive, answer)
        arguments = [format_value(amount, positional=True),
                     f"{PLACEHOLDER_TYPE_PARAMS[ptype]}={format_value(code)}"]
    note = next((directive.source[a:b] for a, b in _argument_spans(directive.source)
                 if directive.source.startswith("note=", a)), None)
    if note is not None:
        arguments.append(note)
    return f"{{{{{ptype}: {', '.join(arguments)}}}}}"


def _typed(directive: Directive, qtype: str | None) -> str | None:
    """An unanswered placeholder whose type came from its declaration, with
    ``, type=TYPE`` written after its id, so that the draft keeps the type
    once ``questions`` is gone (§15.7.2 step 5). None when nothing changes."""
    if qtype not in VALID_PLACEHOLDER_TYPES or "type" in directive.params:
        return None
    spans = _argument_spans(directive.source)
    if not spans:
        return None
    at = spans[0][1]
    return f"{directive.source[:at]}, type={qtype}{directive.source[at:]}"


# An argument's start up to its value: optional spacing, a parameter name and
# "=", spacing (§11.2).
_ARGUMENT_HEAD_RE = re.compile(r"[ \t]*(?:[a-z][a-z0-9-]*=)?[ \t]*")


def _argument_spans(source: str) -> list[tuple[int, int]]:
    """Where each argument of the well-formed directive *source* is written,
    without the spacing around it. The lexer decodes values; §15.7.2 keeps a
    ``note`` "exactly as written", so its spelling is read from here. Commas
    separate arguments except inside a quoted value (§11.3)."""
    spans: list[tuple[int, int]] = []
    pos, close = source.index(":") + 1, len(source) - 2
    while True:
        value = _ARGUMENT_HEAD_RE.match(source, pos).end()
        if source.startswith('"', value):
            value = _quoted_end(source, value)
        comma = source.find(",", value, close)
        end = close if comma < 0 else comma
        written = source[pos:end]
        start = pos + len(written) - len(written.lstrip(" \t"))
        stop = end - (len(written) - len(written.rstrip(" \t")))
        if stop > start:
            spans.append((start, stop))
        if comma < 0:
            return spans
        pos = comma + 1


def _quoted_end(source: str, at: int) -> int:
    """The offset after the quoted value opening at *at* (§11.3 escapes)."""
    index = at + 1
    while index < len(source):
        if source[index] == "\\" and source[index + 1:index + 2] in ('"', "\\"):
            index += 2
        elif source[index] == '"':
            return index + 1
        else:
            index += 1
    return index


# ── Line starts (§15.7.3) ────────────────────────────────────────

_QUOTE_MARKERS_RE = re.compile(r"(?:[ \t]*>[ \t]?)*")
_ITEM_MARKER_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])[ \t]+")
_ATX_RE = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
_BLOCK_QUOTE_RE = re.compile(r" {0,3}>")
_BREAK_RE = re.compile(r" {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_SETEXT_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*$")
_BULLET_RE = re.compile(r" {0,3}[-+*](?:[ \t]+(?P<rest>.*))?$")
_ORDERED_RE = re.compile(r" {0,3}(?P<number>[0-9]{1,9})(?P<delim>[.)])(?:[ \t]+(?P<rest>.*))?$")


def _construct(content: str, *, in_paragraph: bool) -> str | None:
    """The block construct *content* begins, as CommonMark reads it — after
    paragraph text (*in_paragraph*) only those that can interrupt one."""
    if not content.strip():
        return None
    if indent_width(content) >= 4:
        return None if in_paragraph else "code"
    if _ATX_RE.match(content):
        return "heading"
    if _BLOCK_QUOTE_RE.match(content):
        return "quote"
    if FENCE_OPEN_RE.match(content):
        return "fence"
    if in_paragraph and _SETEXT_RE.match(content):
        return "setext"
    if _BREAK_RE.match(content):
        return "break"
    if HTML_BLOCK_START_RE.match(content):
        return "html"
    # A list item interrupts paragraph text only when it has content, and,
    # when ordered, only when it starts at 1.
    bullet = _BULLET_RE.match(content)
    if bullet and (not in_paragraph or (bullet.group("rest") or "").strip()):
        return "list"
    if ordered := _ORDERED_RE.match(content):
        first = int(ordered.group("number")) == 1 and (ordered.group("rest") or "").strip()
        if not in_paragraph or first:
            return "ordered"
    return None


def _paragraph_open(line: str | None) -> bool:
    """True if *line* leaves paragraph text open, which the next line would
    continue — the parser's own reading of a line's content."""
    return (
        line is not None
        and bool(line.strip())
        and not FENCE_OPEN_RE.match(line)
        and not re.fullmatch(r" {0,3}=+[ \t]*", line)
        and _opens_paragraph(line)
    )


def _containers(written: str, in_paragraph: bool, limit: int | None, *, item: bool) -> tuple[int, bool]:
    """Where the content of template line *written* begins — after its block
    quote markers and the list item marker it really has in its context —
    and whether that is a list item marker. Never past its first insertion.
    *item*: the parser read the line as beginning a list item, so its marker
    is one whatever the line before it — an item numbered 2 continues its
    list even though it could not interrupt a paragraph. Inside a block
    quote, which the parser does not read into items, the marker is judged
    from the line before."""
    end = _QUOTE_MARKERS_RE.match(written).end()
    listed = False
    marker = _ITEM_MARKER_RE.match(written, end)
    if marker and (
        (item and end == 0)
        or _construct(written[end:], in_paragraph=in_paragraph) in ("list", "ordered")
    ):
        end, listed = marker.end(), True
    if limit is not None and limit < end:
        return limit, False
    return end, listed


def _line_start(line: _Line, previous: str | None, source: _Source) -> str:
    """The line-start check (§15.7.3) of *line*, which follows *previous* in
    the output."""
    template = source.lines
    written = template[line.origin]
    written_previous = template[line.origin - 1] if line.origin else None
    was_open = _paragraph_open(written_previous)
    prefix, listed = _containers(written, was_open, line.first, item=line.origin in source.item_lines)
    content = line.text[prefix:]
    if line.first is not None and not written[prefix:line.first].strip(" \t"):
        content = content.lstrip(" \t")
    was = _construct(written[prefix:], in_paragraph=was_open and not listed)
    now_open = _paragraph_open(previous) and not listed
    return line.text[:prefix] + _escape_start(content, now_open, was)


def _escape_start(content: str, in_paragraph: bool, was: str | None) -> str:
    """*content* kept from beginning a block construct the template line did
    not begin: a backslash before its first character, or before the ``.``
    or ``)`` of an ordered-list number; indented code loses its indentation."""
    kind = _construct(content, in_paragraph=in_paragraph)
    if kind is None or kind == was:
        return content
    if kind == "code":
        return _escape_start(content.lstrip(" \t"), in_paragraph, was)
    at = (
        _ORDERED_RE.match(content).start("delim")
        if kind == "ordered"
        else len(content) - len(content.lstrip(" \t"))
    )
    return content[:at] + "\\" + content[at:]


# ── Frontmatter steps (2, 3, 5, 6) ───────────────────────────────


def _frontmatter_value(occ: _Occurrence, answers: _Answers) -> str | None:
    """What replaces a frontmatter placeholder: the answer as plain text,
    escaped for its quoted scalar (§15.7.2 step 5) — or, unanswered, the
    placeholder with its declared type written in."""
    answer = answers.get(occ.qid)
    qtype = question_type(answers.declared, occ.qid)
    if qtype in DECISION_QUESTION_TYPES or not occ.quote:
        return None
    if answer is _MISSING:
        return _typed(occ.directive, qtype)
    ptype = _effective_type(occ.directive, answers.declared)
    if ptype in ("money", "duration"):
        text = " ".join(_measure(ptype, occ.directive, answer))
    else:
        text = _iso(answer) if ptype == "date" else str(answer)
    if occ.quote == '"':
        return text.replace("\\", "\\\\").replace('"', '\\"')
    return text.replace("'", "''")


def _emit_frontmatter(front: _Frontmatter, decision: _Decision, answers: _Answers) -> str:
    lines = list(front.lines)
    for occ in sorted(front.occurrences, key=lambda o: (o.line, -o.start)):
        value = _frontmatter_value(occ, answers)
        if value is not None:
            lines[occ.line] = lines[occ.line][:occ.start] + value + lines[occ.line][occ.end:]
    drop: set[int] = set()
    for item, kept in zip(front.items, decision.attachments, strict=True):
        if not kept:
            drop.update(range(item.start, item.end))
        elif item.when is not None:
            drop.update(range(*item.when))
    if front.attachments is not None and front.items and not any(decision.attachments):
        drop.update(range(*front.attachments))
    if front.questions is not None:
        drop.update(range(*front.questions))
    return front.text(lines, frozenset(drop))


# ── Step 7: identifiers ──────────────────────────────────────────

_Key = tuple[str, int]  # (file path, line index)
_Row = tuple[str, "_Key | None"]


def _attachment_rows(files: list[tuple[str, list[str], str]], taken: str) -> list[_Row]:
    """LegalDown attachment files after the body, each under a synthetic
    level-1 heading carrying the attachment's condition: presence is decided
    per file (§15.3), so a file's sections must not fall under the body's
    last section. The heading's identifier is explicit and unused elsewhere."""
    rows: list[_Row] = []
    for number, (path, lines, when) in enumerate(files, 1):
        anchor = f"attachment-{number}"
        while f"#{anchor}" in taken:
            anchor += "-x"
        condition = f" when={when}" if when else ""
        rows += [("", None), (f"# Attachment {{#{anchor}{condition}}}", None), ("", None)]
        rows += [(text, (path, index)) for index, text in enumerate(lines)]
    return rows


def _combined(main: list[str], includes: dict[int, tuple[int, str, list[str]]],
              attachments: list[_Row]) -> list[_Row]:
    """The combined document (§15.7.2 step 7): each fragment spliced in at its
    ``{{include:}}`` paragraph, the attachment files after the body."""
    rows: list[_Row] = []
    index = 0
    while index < len(main):
        if index in includes:
            end, path, lines = includes[index]
            rows += [("", None), *((text, (path, k)) for k, text in enumerate(lines)), ("", None)]
            index = end
            continue
        rows.append((main[index], ("", index)))
        index += 1
    return rows + attachments


def _identifiers(head: str, rows: list[_Row]) -> dict[_Key, tuple[str, bool]]:
    """Each heading's identifier in the combined document, and whether it is
    explicit — generated by the validator, which knows alternatives (§5.5)."""
    lines = [text for text, _key in rows]
    document = parse_document(head + "\n".join(lines))
    entries = validate_document(document).sections
    headings = _layout(lines).headings
    if len(headings) != len(entries):
        raise AssemblyError("The combined document's headings do not match its parsed sections.")
    return {
        rows[heading.marker_line][1]: (entry.identifier, bool(section.identifier))
        for heading, entry, section in zip(headings, entries, document.sections, strict=True)
        if rows[heading.marker_line][1] is not None
    }


def _template_identifiers(t: _Template) -> dict[_Key, tuple[str, bool]]:
    front = t.front
    files = [(item.file, t.subs[item.file].lines, str(item.test) if item.test else "")
             for item in (front.items if front else []) if item.file in t.subs]
    includes = {inc.start: (inc.end, inc.path, t.subs[inc.path].lines)
                for inc in t.main.includes if inc.path in t.subs}
    taken = "\n".join(t.main.lines)
    rows = _combined(t.main.lines, includes, _attachment_rows(files, taken))
    return _identifiers(front.text() if front else "", rows)


def _preserve_identifiers(t: _Template, head: str, main: list[_Line],
                          subs: dict[str, list[_Line]], decision: _Decision) -> None:
    """Step 7: a heading without an explicit identifier whose generated one
    changed with assembly gets the template's written after it."""
    template_ids = _template_identifiers(t)
    outputs = {"": main, **subs}
    texts = {path: [line.text for line in lines] for path, lines in outputs.items()}
    includes = {}
    for include in t.main.includes:
        if include.path in subs:
            kept = [k for k, line in enumerate(main) if include.start <= line.origin < include.end]
            if kept:
                includes[kept[0]] = (kept[-1] + 1, include.path, texts[include.path])
    files = [(item.file, texts[item.file], "")
             for item, kept in zip(t.front.items if t.front else [], decision.attachments, strict=True)
             if kept and item.file in subs]
    taken = "\n".join(texts[""])
    rows = _combined(texts[""], includes, _attachment_rows(files, taken))
    for (path, index), (identifier, explicit) in _identifiers(head, rows).items():
        line = outputs[path][index]
        was = template_ids.get((path, line.origin))
        if not explicit and was is not None and was[0] != identifier:
            line.text += f" {{#{was[0]}}}"


# ── Step 8: blank lines ──────────────────────────────────────────


def _code_lines(lines: list[str]) -> set[int]:
    """Lines whose blankness is content: fenced and indented code — and a
    list's own lines, since a list's blank lines are those of the fenced code
    inside its items (the parser ends a list at any other blank line). Blank
    lines in raw HTML are not exempt: step 8 names code blocks only."""
    kept: set[int] = set()
    for blocks in _layout(lines).containers():
        for block in blocks:
            if _kind(block) in ("code", "list"):
                kept.update(range(block.start, block.end))
    return kept


def _finish(head: str, lines: list[_Line]) -> str:
    """Step 8: every run of blank lines outside code becomes one empty line,
    and the file ends with exactly one line break — or, left with nothing,
    is empty."""
    texts = [line.text for line in lines]
    code = _code_lines(texts)
    out: list[str] = []
    for index, text in enumerate(texts):
        if text.strip() or index in code:
            out.append(text)
        elif not out or out[-1] != "" or (index - 1) in code:
            out.append("")
    while out and not out[-1].strip():
        out.pop()
    if not out:
        return head if not head or head.endswith("\n") else head + "\n"
    return head + "\n".join(out) + "\n"


# ── Public API ───────────────────────────────────────────────────


def assemble(
    template: str,
    answers: Mapping[str, Any],
    *,
    load_file: LoadFile | None = None,
) -> AssemblyResult:
    """Assemble *template* with *answers* (§15.7.2).

    A draft or an ordinary document goes through the same steps: its drafting
    notes are removed, its answered blanks filled and its blank lines
    collapsed. *load_file* reads the include fragments and LegalDown
    attachment files; the assembled ones are returned in ``files``.
    """
    t = _read(template, load_file)
    if t.problems:
        return AssemblyResult(diagnostics=t.problems)
    resolved = _Answers(answers, t.declared)
    decision = _decide(t, resolved)
    diagnostics = _answer_diagnostics(t, answers) + [
        Diagnostic("answer-missing", "error",
                   f"'{qid}' decides what the assembled document holds, and has neither an "
                   f"answer nor a default (§15.7.2 step 1).")
        for qid in decision.missing
    ]
    if any(d.level == "error" for d in diagnostics):
        return AssemblyResult(diagnostics=diagnostics)
    head = _emit_frontmatter(t.front, decision, resolved) if t.front is not None else ""
    main = _emit(t.main, decision.removed[""], resolved)
    subs = {
        path: _emit(source, decision.removed[path], resolved)
        for path, source in t.subs.items()
        if decision.files.get(path)
    }
    _preserve_identifiers(t, head, main, subs, decision)
    return AssemblyResult(
        output=t.bom + _finish(head, main).replace("\n", t.main.newline),
        files={
            path: _finish("", lines).replace("\n", t.subs[path].newline)
            for path, lines in subs.items()
        },
        diagnostics=diagnostics,
    )


def template_questions(
    template: str, *, load_file: LoadFile | None = None
) -> list[Question]:
    """Every question of *template* (§15.2): the declared ones in declaration
    order, then each undeclared placeholder, in the order first written."""
    return _questions(_read(template, load_file))


def needed_questions(
    template: str,
    answers: Mapping[str, Any],
    *,
    load_file: LoadFile | None = None,
) -> list[Question]:
    """The questions to ask now, given *answers* so far, in the order they
    are reached: a decision question when a condition or ``{{choose:}}``
    using it lies in a present unit outside drafting notes (§15.7.2 step 1),
    a value question when one of its placeholders does. A unit under a
    condition whose question is still unanswered is not reached yet, so the
    form grows as decisions are made."""
    t = _read(template, load_file)
    decision = _decide(t, _Answers(answers, t.declared))
    by_id = {question.id: question for question in _questions(t)}
    return [by_id[qid] for qid in decision.needed if qid in by_id]
