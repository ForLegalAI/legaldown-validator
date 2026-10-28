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
import copy
import posixpath
import re
import reprlib
import warnings
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import InitVar, dataclass, field
from datetime import date
from functools import cache
from itertools import islice
from typing import Any

from ._deprecation import LegaldownDeprecationWarning
from .definitions import list_fragments
from .directives import PLACEHOLDER_TYPE_PARAMS, Directive, format_value, lex
from .files import LoadFile, Reach
from .markdown import (
    FENCE_OPEN_RE,
    HTML_BLOCK_START_RE,
    LINE_ENDING_RE,
    ItemMarkers,
    indent_width,
    is_blank,
)
from .markers import MARKER_RE, Marker, format_marker, parse_marker
from .models import LIST_KINDS, Block, Document, frontmatter_message
from .parser import (
    FRONTMATTER_RE,
    LIST_ITEM_RE,
    MAX_QUOTE_DEPTH,
    FrontmatterError,
    _BlockSpan,
    _ItemSpan,
    _Layout,
    _layout,
    _may_interrupt,
    _parse_body,
    _row_width_end,
    parse,
    quote_content,
)
from .validator import validate
from .validator.conditions import Condition
from .validator.core import is_template
from .validator.fragments import FragmentFile, FragmentKind, fragment_findings, nested_includes, noun
from .validator.helpers import is_positive_numeric, is_valid_money_amount
from .validator.patterns import DURATION_UNITS, IDENTIFIER_RE, LEGALDOWN_EXTENSIONS, VALID_PLACEHOLDER_TYPES
from .validator.result import Diagnostic
from .validator.templates import (
    DECISION_QUESTION_TYPES,
    _BlankState,
    answer_problem,
    is_drafting_note,
    question_type,
)
from .validator.units import block_include, find_markers, include_key, own_presence

__all__ = [
    "frontmatter_diagnostic",
    "AssemblyError",
    "AssemblyResult",
    "LoadFile",
    "Question",
    "assemble",
    "needed_questions",
    "template_questions",
]

#: A question with neither an answer nor a default.
_MISSING = object()


class AssemblyError(ValueError):
    """The template's source could not be mapped onto its parsed structure.

    Never expected for a template the parser reads; raised rather than
    guessing, because a wrong guess edits the wrong lines of a contract."""


@dataclass(slots=True)
class AssemblyResult:
    """What assembling a template produced (``Form.assemble``).

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


@dataclass(frozen=True)
class Question:
    """One question the person filling a template is asked (§15.2): declared
    in ``questions``, or implicit — an undeclared placeholder, with no prompt
    and no default. ``type`` is the effective type; ``currency``/``unit`` is
    set when every placeholder for the question fixes the same one, which is
    when the bare amount or value is an acceptable answer (§15.7.1).

    A question is a fixed value, copied from the template's declaration: it
    cannot be reassigned, and ``choices`` and a map-valued ``default`` (a money
    or duration default) are copies of the declaration's. Treat them as
    read-only: the question is shared by every form of its template."""

    id: str
    type: str
    prompt: str = ""
    default: Any = None
    choices: dict[str, str] = field(default_factory=dict)
    currency: str | None = None
    unit: str | None = None
    declared: bool = True
    #: What the placeholders of the question fix (``_BlankState``): every code, and
    #: whether one is in frontmatter, which ``problem`` needs. Not a field: it is
    #: not part of the value (``asdict``, ``replace`` and equality leave it out). A
    #: question made without it behaves as if its placeholders fix the ``currency``
    #: or ``unit`` it names, if any, and none is in frontmatter.
    _blank: InitVar[_BlankState | None] = None

    def __post_init__(self, _blank: _BlankState | None) -> None:
        object.__setattr__(self, "choices", dict(self.choices or {}))
        object.__setattr__(self, "default", copy.deepcopy(self.default))
        object.__setattr__(self, "_info", _blank)

    @property
    def is_decision(self) -> bool:
        return self.type in DECISION_QUESTION_TYPES

    def problem(self, answer: Any) -> str | None:
        """Why *answer* is not a valid answer to this question (§15.7.1), or
        None when it is."""
        return answer_problem(
            self.type, answer, choices=self.choices if self.type == "choice" else None, blank=self._placeholders()
        )

    def _placeholders(self) -> _BlankState | None:
        """What the placeholders fix: as recorded, else as ``currency``/``unit`` name."""
        if self._info is not None:
            return self._info
        fixed = self.currency if self.type == "money" else self.unit if self.type == "duration" else None
        return _BlankState(type=self.type, codes={fixed}) if fixed else None

    @property
    def _fixed(self) -> str | None:
        """The currency (money) or unit (duration) every placeholder fixes, if one:
        what ``problem`` goes by, so that the hint and ``from_text`` never differ from it."""
        blank = self._placeholders()
        if blank is None or self.type not in ("money", "duration"):
            return None
        return next(iter(blank.codes)) if len(blank.codes) == 1 and "" not in blank.codes else None

    @property
    def hint(self) -> str:
        """What to type to answer it, as a phrase to follow "Enter": ``yes or
        no``, ``an amount and a currency, like 5000 EUR``, ``one of: courts
        (State courts), arbitration`` — for a person, a web form or an agent."""
        if self.type == "boolean":
            return "yes or no"
        if self.type == "date":
            return "a date, YYYY-MM-DD"
        if self.type == "choice":
            if not self.choices:
                return "an answer (the question declares no choices)"
            return "one of: " + ", ".join(
                f"{key} ({label})" if label and label.casefold() != key.casefold() else key
                for key, label in self.choices.items()
            )
        if self.type == "money":
            if self._fixed:
                return f"an amount, like 5000 (in {self._fixed})"
            return "an amount and a currency, like 5000 EUR"
        if self.type == "duration":
            if self._fixed:
                return f"a number, like 30 (in {self._fixed})"
            return f"a number and a unit, like 30 D (units: {', '.join(DURATION_UNITS)})"
        if self._info is not None and self._info.in_frontmatter:
            return "text, without '{{'"
        return "text"

    @property
    def accepts(self) -> dict[str, Any]:
        """What the question accepts, for a form or an agent to build its input
        from: ``kind`` (the type), and what that kind needs — the words of a
        boolean, the choices, the currency or unit the placeholders fix and the
        units there are, whether the text fills frontmatter. ``hint`` says it in
        words."""
        if self.type == "boolean":
            return {"kind": "boolean", "words": {"true": ["yes", "y", "true"], "false": ["no", "n", "false"]}}
        if self.type == "date":
            return {"kind": "date", "format": "YYYY-MM-DD"}
        if self.type == "choice":
            return {"kind": "choice", "choices": [{"key": key, "label": label} for key, label in self.choices.items()]}
        if self.type == "money":
            return {"kind": "money", "currency": self._fixed}
        if self.type == "duration":
            return {"kind": "duration", "unit": self._fixed, "units": list(DURATION_UNITS)}
        return {"kind": "text", "frontmatter": bool(self._info is not None and self._info.in_frontmatter)}

    def to_text(self, answer: Any) -> str:
        """*answer* as a person would type it — the inverse of :meth:`from_text`:
        to show a default or a saved answer, or to fill in an input. ``yes`` or
        ``no`` for a boolean, ``5000 EUR`` for money, ``30 D`` for a duration, an
        ISO date; the empty text for no answer. Anything else as ``str`` has it."""
        if answer is None:
            return ""
        if isinstance(answer, bool):
            return "yes" if answer else "no"
        if isinstance(answer, date):
            return answer.isoformat()
        if isinstance(answer, dict):
            keys = ("amount", "currency") if self.type == "money" else ("value", "unit")
            if self.type in ("money", "duration") and set(answer) == set(keys):
                return f"{answer[keys[0]]} {answer[keys[1]]}"
        try:
            if isinstance(answer, (dict, list, tuple, set, frozenset)):
                return _DESCRIBE.repr(answer)[:200]  # not an answer it knows: described, within bounds
            return str(answer)
        except (ValueError, RecursionError):  # an integer too long for Python to write as text
            return ""

    def from_text(self, text: str) -> Any:
        """The answer that *text*, as a person types it, gives to this question:
        the shape an answers set holds (§15.7.1), which :meth:`problem` accepts — or ``None``
        for no answer (the empty text: the default applies, or the blank stays).
        Surrounding spaces are dropped. Strict, and not locale-aware:

        - ``text``: the text. ``date``: ``YYYY-MM-DD``.
        - ``boolean``: yes, no, true, false, y, n, in any case.
        - ``choice``: a choice's key, else its label, in any case; a label two
          choices share is ambiguous.
        - ``money``: ``5000 EUR`` (or ``5000EUR``), and the amount alone where the
          placeholders fix a currency. ``duration``: ``30 D``, and the number
          alone where they fix a unit. No grouping separators or symbols.

        Raises ``ValueError``, saying what to enter, for text that is no answer,
        and ``TypeError`` for what is not text.
        """
        if not isinstance(text, str):
            raise TypeError(f"the text of an answer is a string, not {type(text).__name__}")
        text = text.strip()
        if not text:
            return None
        value = self._answer_from(text)
        if self.type not in ("boolean", "choice", "date", "money", "duration"):
            # What a person types is held to more than the specification asks of an
            # answer (§15.7.1): no key's escape sequence, no byte that is no UTF-8.
            if _CONTROL_RE.search(value):
                raise ValueError(f"Enter {self.hint}. (it must not contain a control character)")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(f"Enter {self.hint}. (it must be text that can be written as UTF-8)") from None
        problem = self.problem(value)
        if problem:
            # Said as the question is, where what is wrong is not the shape of what was typed.
            detail = "" if self.type in ("boolean", "choice", "date") else f" ({problem})"
            raise ValueError(f"Enter {self.hint}.{detail}")
        return value

    def _answer_from(self, text: str) -> Any:
        shape = ValueError(f"Enter {self.hint}.")
        if self.type == "boolean":
            word = text.casefold()
            if word in ("yes", "y", "true"):
                return True
            if word in ("no", "n", "false"):
                return False
            raise shape
        if self.type == "choice":
            if text in self.choices:
                return text
            word = text.casefold()
            keys = [key for key in self.choices if key.casefold() == word]
            if keys:
                return keys[0]
            labels = [key for key, label in self.choices.items() if label.casefold() == word]
            if len(labels) == 1:
                return labels[0]
            if labels:
                raise ValueError(f"'{text}' is the label of more than one choice ({', '.join(labels)}); "
                                 f"enter {self.hint}.")
            raise shape
        if self.type in ("money", "duration"):
            found = re.fullmatch(r"(\S*?\d)\s*([A-Za-z]+)?", text)
            if found is None:
                raise shape
            amount, code = found.group(1), (found.group(2) or "").upper()
            if not (is_valid_money_amount(amount) if self.type == "money" else is_positive_numeric(amount)):
                what = "an amount" if self.type == "money" else "a positive number"
                shown = amount if len(amount) <= 40 else amount[:37] + "..."
                raise ValueError(f"'{shown}' is not {what}: digits and at most one decimal point, with no "
                                 f"separators or symbols. Enter {self.hint}.")
            if not code:
                if not self._fixed:
                    raise shape
                return amount
            names = ("amount", "currency") if self.type == "money" else ("value", "unit")
            return {names[0]: amount, names[1]: code}
        return text


#: C0 controls but the tab (line breaks are refused by ``problem``), and DEL: what a terminal's
#: keys and escape sequences say, not text.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _describer() -> reprlib.Repr:
    describer = reprlib.Repr()
    describer.maxlevel = 3
    describer.maxlist = describer.maxtuple = describer.maxdict = describer.maxset = describer.maxfrozenset = 4
    describer.maxstring = 40
    describer.maxother = 80
    return describer


#: How an answer of no known shape is described (``Question.to_text``): shallow and short.
_DESCRIBE = _describer()

# ── Where the parser's blocks lie in the source ──────────────────

# The parsed block kinds, by what assembly does with them.
_KINDS = {
    "paragraph": "paragraph", "definition": "paragraph", "ref": "paragraph", "term": "paragraph",
    "ordered_list": "list", "unordered_list": "list",
    "quote": "quote", "code": "code", "table": "table", "rule": "rule", "html": "html",
    # No text and no unit (§4.7, #92): its lines are left as they are.
    "link_definition": "link_definition",
}


def _kind(span: _BlockSpan) -> str:
    return _KINDS.get(span.kind, span.kind)


def _matches(spans: list[_BlockSpan], blocks: list[Block]) -> bool:
    """True if *spans* lie where *blocks* are, block for block, into list
    items too."""
    return len(spans) == len(blocks) and all(
        span.kind == block.kind and len(span.items) == len(block.items)
        and all(_matches(item_span.blocks, item.blocks) for item_span, item in zip(span.items, block.items, strict=True))
        for span, block in zip(spans, blocks, strict=True)
    )


def _content(
    spans: list[_BlockSpan] | tuple[_BlockSpan, ...], blocks: list[Block] | tuple[Block, ...],
    depth: int = 0, base: int = 0,
) -> Iterator[tuple[_BlockSpan, Block, int, bool]]:
    """Every block of *blocks*, of their list items, and of the block quotes
    among them, in document order, as the validator reads them
    (``definitions.block_fragments``): ``(span, block, base, leaf)`` — the
    block's lines are ``[base + span.start, base + span.end)``, and *leaf*
    tells whether its text is lexed as it stands, holding no blocks, nor
    code or raw HTML (§11.4). A quote's content is read as blocks
    (``parser.quote_content``), but for one in ``MAX_QUOTE_DEPTH`` items
    and quotes, whose text is one. *depth*: the items and quotes *blocks*
    are in."""
    for span, block in zip(spans, blocks, strict=True):
        read = block.kind == "quote" and depth < MAX_QUOTE_DEPTH
        leaf = not read and block.kind not in (*LIST_KINDS, "code", "rule", "html", "link_definition")
        yield span, block, base, leaf
        for item_span, item in zip(span.items, block.items, strict=True):
            yield from _content(item_span.blocks, item.blocks, depth + 1, base)
        if read:
            children, inner = quote_content(block.text, depth + 1)
            # The quote's text holds one line per source line.
            yield from _content(inner, children, depth + 1, base + span.start)


def _pairs(layout: _Layout, document: Document) -> Iterator[tuple[int | None, int, _BlockSpan, Block]]:
    """Each source block with its model block: ``(section, index, source, model)``."""
    model = [document.preamble, *(section.blocks for section in document.sections)]
    spans = layout.containers()
    # The layout is recorded by the walk that builds the model, block for
    # block; anything else is a bug, and would edit the wrong lines.
    if len(spans) != len(model) or not all(_matches(a, b) for a, b in zip(spans, model, strict=True)):
        raise AssemblyError("The template's source does not match its parsed structure.")
    for container, (container_spans, blocks) in enumerate(zip(spans, model, strict=True)):
        for index, (span, block) in enumerate(zip(container_spans, blocks, strict=True)):
            yield (None if container == 0 else container - 1), index, span, block


def _trim(lines: list[str], start: int, end: int) -> int:
    """*end* moved back past blank lines: a unit runs through its last
    non-blank line (§15.7.2 step 2)."""
    while end > start + 1 and is_blank(lines[end - 1]):
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
    source: str  # the directive as written


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
    malformed: list[Directive] = field(default_factory=list)  # placeholders and choices


def _unix(text: str) -> tuple[str, str]:
    """*text* without a byte-order mark and with LF line breaks — the line
    endings CommonMark and the parser know: LF, CR, CRLF — and the line
    break it was written with: its first LF or CRLF, or CR in a file without
    LF, where a lone CR is a stray. A CRLF (or CR) file is
    assembled as LF and written back with it, so that no byte assembly does
    not edit changes."""
    text = text.removeprefix("\ufeff")
    first = re.search(r"\r?\n", text)
    newline = first.group(0) if first else "\r" if "\r" in text else "\n"
    return LINE_ENDING_RE.sub("\n", text), newline


def _test(condition: str, questions: Any) -> Condition | None:
    return next(iter(own_presence(condition, questions)), None)


def _read_body(
    path: str, body: str, document: Document, questions: Any, *, template: bool
) -> _Source:
    lines = body.split("\n")
    if lines[-1] == "":
        lines.pop()  # the last line's ending, not a line -- as parse reads it
    layout = _layout(lines)
    found: dict[tuple[int | None, int], list] = {}
    for marker in find_markers(document, cache(lex)):
        if marker.placed(template) and marker.marker is not None:
            found.setdefault((marker.section, marker.block), []).append(marker)
    source = _Source(path, lines, _section_units(layout, document, lines, questions), set(), [], [])
    for section, index, span, block in _pairs(layout, document):
        markers = found.get((section, index), [])
        if _kind(span) == "list":
            _read_list(source, span, block, markers, questions)
        elif _kind(span) == "paragraph":
            _read_paragraph(source, span, block, markers, questions)
        for inner, inner_block, base, _leaf in _content([span], [block]):
            if inner_block.kind == "quote" and is_drafting_note(inner_block):
                source.notes.update(range(base + inner.start, base + inner.end))
    source.occurrences, source.malformed = _occurrences(layout, document, lines)
    return source


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
    # The validator's own reading of an include paragraph, of the same text, so that both agree
    # (#35). One that names no file (``{{include:}}``) has the path "", refused in ``_read_files``.
    include = block_include(block)
    if include is not None:
        source.includes.append(_Include(span.start, span.end, include_key(include), include.source))


def _read_list(source: _Source, span: _BlockSpan, block: Block, markers: list, questions: Any) -> None:
    """A conditional list item: it runs through its lines, the items nested
    in it and its later content included (§15.3); its marker ends its first
    paragraph (§5.7)."""
    lines = source.lines
    items: list[_ItemSpan] = []  # every item, nested ones too, numbered as list_fragments numbers them

    def walk(spans: list[_ItemSpan], model: list) -> None:
        for item_span, item in zip(spans, model, strict=True):
            items.append(item_span)
            for inner, inner_block in zip(item_span.blocks, item.blocks, strict=True):
                if inner_block.kind in LIST_KINDS:
                    walk(inner.items, inner_block.items)

    walk(span.items, block.items)
    paths = [path for _text, _position, path in list_fragments(block)]
    for marker in markers:
        if not marker.marker.condition or marker.fragment >= len(paths) or not paths[marker.fragment]:
            continue
        item = items[paths[marker.fragment][-1]]
        # Its first paragraph: link reference definitions before it are no text (#92).
        opening = next((block for block in item.blocks if block.kind != "link_definition"), None)
        first_end = opening.end if opening is not None else item.start + 1
        line = _marker_line(lines, item.start, first_end, marker.source)
        source.units.append(_Unit(item.start, _trim(lines, item.start, item.end),
                                  _test(marker.marker.condition, questions), line, marker.source))


def _occurrences(
    layout: _Layout, document: Document, lines: list[str]
) -> tuple[list[_Occurrence], list[Directive]]:
    """Every placeholder and choice in the body, lexed block by block as the
    validator lexes them (``_content``), so a code span or comment hides the
    same ones and one left open in a block does not run into the next. No
    directive is recognized in code or raw HTML (§11.4), in list items and
    block quotes too. Also the malformed ones."""
    found: list[_Occurrence] = []
    malformed: list[Directive] = []
    for _section, _index, span, top in _pairs(layout, document):
        for block, model, base, leaf in _content([span], [top]):
            if not leaf:
                continue
            start = base + block.start
            rows = lines[start:base + block.end]
            if _kind(block) == "table":
                # Cells past the header's width are dropped (GFM): no blank.
                width = len(model.headers)
                rows = rows[:2] + [row[:_row_width_end(row, width)] for row in rows[2:]]
            text = "\n".join(rows)
            offsets = [0] + [i + 1 for i, char in enumerate(text) if char == "\n"]
            for directive in lex(text).directives:
                row = bisect.bisect_right(offsets, directive.start) - 1
                if directive.name not in ("placeholder", "choose"):
                    continue
                if directive.malformed:
                    # One written across lines, say, which the validator
                    # reports too: it could not be filled here.
                    malformed.append(directive)
                    continue
                column = directive.start - offsets[row]
                found.append(_Occurrence(directive, start + row, column,
                                         column + directive.end - directive.start,
                                         in_table=_kind(block) == "table"))
    return found, malformed


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
    rows: int = 0  # how many lines it takes in the file: the offset of the body

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
        return _Frontmatter(source[:match.end()], [], "", rows=source[:match.end()].count("\n")), source[match.end():]
    head, tail = source[:match.start(1)], source[match.end(1):match.end()]
    rows = source[:match.end()].count("\n")
    return _Frontmatter(head, match.group(1).split("\n"), tail, rows=rows), source[match.end():]


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


def _scan_scalar(line: str, start: int, quote: str) -> int:
    """The offset of *quote*'s matching close, scanning *line* from *start*
    (just past the opening quote, or 0 for one carried from a previous line):
    ``""`` doubles for a literal ``'``, a backslash escapes for ``"``.
    ``len(line)`` when it does not close on this line."""
    end = start
    while end < len(line):
        if quote == '"' and line[end] == "\\":
            end += 2
            continue
        if line[end] == quote:
            if quote == "'" and line[end + 1:end + 2] == "'":
                end += 2
                continue
            return end
        end += 1
    return end


def _quoted_scalars(line: str, carry: str = "") -> tuple[list[tuple[int, int, str]], str]:
    """``(start, end, quote)`` of every quoted scalar on frontmatter *line*,
    in block and flow style alike (``- {name: a, legal_name: "..."}``), so a
    placeholder is filled wherever §3.10 lets it stand; a comment ends the
    scan. *carry* is the quote a previous line's scalar left open, which
    *line* then starts inside (``start`` ``-1``); the second of the pair
    returned is *line*'s own such quote, when its last scalar runs on."""
    spans: list[tuple[int, int, str]] = []
    index = 0
    if carry:
        end = _scan_scalar(line, 0, carry)
        spans.append((-1, end + 1, carry))
        if end >= len(line):
            return spans, carry
        index = end + 1
    while index < len(line):
        char = line[index]
        before = line[:index].rstrip(" \t")
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            break
        if char in "\"'" and (index == 0 or line[index - 1] in " \t[{,") and (
            not before or before[-1] in _SCALAR_OPENERS
        ):
            end = _scan_scalar(line, index + 1, char)
            spans.append((index, end + 1, char))
            if end >= len(line):
                return spans, char
            index = end + 1
            continue
        index += 1
    return spans, ""


def _read_frontmatter(front: _Frontmatter, document: Document, questions: Any) -> None:
    lines = front.lines
    front.questions = _top_entry(lines, "questions")
    front.attachments = _top_entry(lines, "attachments")
    if front.attachments is not None:
        front.items = _attachment_items(lines, front.attachments, document, questions)
    skip = set(range(*front.questions)) if front.questions else set()
    carry = ""
    for index, line in enumerate(lines):
        entering = carry
        scalars, carry = _quoted_scalars(line, carry)
        if index in skip or (not entering and _is_comment(line)) or "{{" not in line:
            continue
        for directive in lex(line).directives:
            if directive.name == "placeholder" and directive.malformed:
                # One in a quoted scalar that runs on to the next line: YAML
                # joins the lines, so the validator reads it whole. Any other
                # is left as written: YAML drops one in a comment, and one
                # the scalar's own escapes (\") confuse here is not filled.
                if any(start < directive.start and end > len(line) for start, end, _q in scalars):
                    front.malformed.append(directive)
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
    blanks: dict[str, _BlankState]
    types: dict[str, str]  # every question's effective type: declared, then implicit
    bom: str
    problems: list[Diagnostic]
    document: Document
    #: The template's own heading identifiers (step 7), worked out when an
    #: assembly first needs them, then kept: they do not depend on the answers.
    identifiers: dict | None = None


def _read(template: str, load_file: LoadFile | None, reach: Reach | None = None) -> _Template:
    bom = "\ufeff" if template.startswith("\ufeff") else ""
    source, newline = _unix(template)
    document = parse(source)
    declared = document.metadata.questions if isinstance(document.metadata.questions, dict) else {}
    template_like = is_template(document)
    front, body = _split(source, document)
    main = _read_body("", body, document, declared, template=template_like)
    main.newline = newline
    if front is not None:
        _read_frontmatter(front, document, declared)
    subs, problems = _read_files(main, front, load_file, declared, template_like, reach or Reach())
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
    sources = (main, *subs.values())
    occurrences = [(occ, True) for occ in (front.occurrences if front else [])]
    noted: list[_Occurrence] = []
    for src in sources:
        for occ in src.occurrences:
            if occ.line in src.notes:
                noted.append(occ)
            else:
                occurrences.append((occ, False))
    blanks = _blanks(occurrences, noted, declared)
    types = {qid: qtype for qid in declared if (qtype := question_type(declared, qid))}
    for pid, blank in blanks.items():
        types.setdefault(pid, blank.type or "text")
    return _Template(front, main, subs, declared, blanks, types, bom, problems, document)


def _read_files(main: _Source, front: _Frontmatter | None, load_file: LoadFile | None,
                declared: dict, template: bool, reach: Reach) -> tuple[dict[str, _Source], list[Diagnostic]]:
    """Read every include fragment and LegalDown attachment file — the absent
    ones too: their placeholders are the template's (answer-unknown) and their
    headings take part in identifier generation (step 7). *reach* says which paths
    may be asked for: those within the template's directory, and, given a document
    root, those that lead out of it and stay within the root (§2.3, #109)."""
    wanted: list[tuple[str, FragmentKind]] = [(include.path, "include") for include in main.includes if include.path]
    items = front.items if front else []
    wanted += [(item.file, "attachment") for item in items if item.file]
    subs: dict[str, _Source] = {}
    # An include paragraph that names no file, at its line (the validator's include-file-missing).
    rows = front.rows if front else 0  # the body's lines are the file's, after the frontmatter's
    problems: list[Diagnostic] = [
        Diagnostic(
            "include-file-missing", "error",
            f"'{include.source}' names no file: an include paragraph names the LegalDown file it takes in "
            f"(§12.2); the template is not assembled.",
            line=include.start + 1 + rows,
        )
        for include in main.includes if not include.path
    ]
    unreadable: set[str] = set()  # reported once, however often it is referenced
    for path, kind in wanted:
        relative = reach.relative(path)
        if path in subs or relative in unreadable:
            continue
        # Only a path within the template's directory, or its document root (§2.3), is asked
        # for: a loader is not handed ``../x`` without one, an absolute path or a drive letter.
        text = load_file(path) if load_file is not None and relative is not None else None
        if text is None:
            reason = "cannot be read" if load_file else "needs the Full level (§17.6)"
            message = f"'{path}' {reason}; the template is not assembled."
            problems.append(Diagnostic(f"{kind}-file-missing", "error", message))
            continue
        text, newline = _unix(text)
        try:
            sub_document = parse(text)
        except FrontmatterError as exc:
            # What plain `validate` reports of a document with this frontmatter, with
            # its message and line, in the file it is in. (`validate(fragment=True)`
            # reports include-has-frontmatter instead, by design: a fragment must
            # have none.) A text was read only for a path that `relative_path` accepts: *relative*.
            unreadable.add(relative)
            problems.append(frontmatter_diagnostic(exc, file=relative))
            continue
        _front, body = _split(text, sub_document)
        subs[path] = _read_body(path, body, sub_document, declared, template=template)
        subs[path].newline = newline
        problems += _file_problems(path, kind, sub_document, template)
    return subs, problems


def _file_problems(path: str, kind: FragmentKind, document: Document, template: bool) -> list[Diagnostic]:
    """The Full checks on a loaded file (§16.10, §16.11, §16.12) that the
    assembled output depends on; the file is read here, so they are made
    here rather than left to a validator that may not read it. They are the
    validator's own (``fragment_findings``, #35), one for each offender, at
    its line in the file: this refuses the template, the validator reports them."""
    fragment = FragmentFile(path, document)
    lexed = cache(lex)
    problems = [
        Diagnostic(
            finding.rule, "error",
            # Frontmatter and a level 1 heading are why the file is not read, and so
            # why the template is not assembled; the others say what to do.
            finding.message if finding.rule == "template-fragment-invalid"
            else f"{finding.message} The template is not assembled.",
            line=finding.line, file=path,
        )
        for finding in fragment_findings(fragment, kind=kind, template=template, lex_fragment=lexed)
    ]
    # Outside a template an include in a fragment or attachment file is allowed (§12.2), but
    # assembly does not assemble it, and refuses (an include in a drafting note is removed
    # with it, unread, §15.6). In a template it is template-fragment-invalid (reported above).
    if not template:
        problems += [
            Diagnostic(
                "include-file-missing", "error",
                f"{noun(kind, 'the').capitalize()} holds '{source}'; an include in an included "
                f"or attached file is not assembled, so the template is not assembled.",
                line=line, file=path,
            )
            for source, line in nested_includes(fragment, lexed)
        ]
    return problems


def _malformed(*sources: _Source, front: _Frontmatter | None) -> list[Diagnostic]:
    """A placeholder or choice the lexer cannot read — one written across
    lines, say — cannot be filled, so the template is refused rather than
    assembled with it unfilled."""
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


def _blanks(
    occurrences: list[tuple[_Occurrence, bool]], noted: list[_Occurrence], declared: dict
) -> dict[str, _BlankState]:
    """Each placeholder id's blank, recorded as the validator records it: the
    type of its first occurrence, the currency or unit each occurrence of
    that type fixes, and whether one is in frontmatter, in the order of the first
    occurrence. *occurrences* are those outside drafting notes, which assembly removes
    before anything is filled (§15.7.2 step 2); the *noted* ones are no part of a blank
    and fix neither its type nor its currency or unit. An id only notes use is still a
    question (a text one, with no type to take), as it always was: after every other, in the
    order its notes first use it (#112)."""
    blanks: dict[str, _BlankState] = {}
    for occ, in_frontmatter in occurrences:
        if occ.directive.name != "placeholder" or not IDENTIFIER_RE.fullmatch(occ.qid):
            continue
        ptype = _effective_type(occ.directive, declared)
        blank = blanks.setdefault(occ.qid, _BlankState())
        blank.in_frontmatter |= in_frontmatter
        blank.type = blank.type or ptype
        if ptype == blank.type and ptype in PLACEHOLDER_TYPE_PARAMS:
            blank.codes.add(occ.directive.params.get(PLACEHOLDER_TYPE_PARAMS[ptype], ""))
    for occ in noted:
        if occ.directive.name == "placeholder" and IDENTIFIER_RE.fullmatch(occ.qid):
            blanks.setdefault(occ.qid, _BlankState())
    return blanks


def _fixed(blank: _BlankState | None) -> str | None:
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
            _blank=t.blanks.get(qid),
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


def _answer_problem(t: _Template, qid: str, answer: Any) -> str | None:
    """Why *answer* is not a valid answer to question *qid* of *t*, or None."""
    qtype = t.types.get(qid)
    if qtype is None:
        return None
    declaration = t.declared.get(qid)
    choices = declaration.get("choices") if isinstance(declaration, dict) else None
    return answer_problem(qtype, answer, choices=choices, blank=t.blanks.get(qid))


def _answer_pairs(t: _Template, answers: Mapping[str, Any]) -> Iterator[tuple[str, Diagnostic]]:
    """What is wrong with *answers*, as ``(question id, diagnostic)``: a key
    that is no question (its own id), and every answer that is not valid (the
    answer to a question not reached included)."""
    for key in answers:
        if not isinstance(key, str) or (key not in t.types and key not in t.declared):
            yield str(key), Diagnostic(
                "answer-unknown", "warning",
                f"'{key}' is neither a question nor a placeholder of the template; its answer "
                f"is ignored (§15.7.1).")
    resolved = _Answers(answers, t.declared)
    for qid in t.types:
        answer = resolved.get(qid)
        if answer is _MISSING:
            continue
        problem = _answer_problem(t, qid, answer)
        if problem:
            whose = "answer to" if answers.get(qid) is not None else "default of"
            yield qid, Diagnostic("answer-invalid", "error",
                                  f"The {whose} '{qid}' {problem} (§15.7.1).")


def _missing_diagnostic(qid: str) -> Diagnostic:
    return Diagnostic("answer-missing", "error",
                      f"'{qid}' decides what the assembled document holds, and has neither an "
                      f"answer nor a default (§15.7.2 step 1).")


class _FormAnswers(_Answers):
    """The answers of a form: as ``_Answers``, except that an answer that is
    not valid counts as no answer — it does not steer which units are present,
    and the default does not stand in for it. It is reported on its own
    (``answer-invalid``)."""

    def __init__(self, answers: Mapping[str, Any], t: _Template) -> None:
        super().__init__(answers, t.declared)
        self._t = t
        self._valid: dict[str, bool] = {}

    def get(self, qid: str) -> Any:
        explicit = self._answers.get(qid)
        if explicit is not None:
            if qid not in self._valid:
                self._valid[qid] = _answer_problem(self._t, qid, explicit) is None
            if not self._valid[qid]:
                return _MISSING
        return super().get(qid)


def _answer_diagnostics(t: _Template, answers: Mapping[str, Any]) -> list[Diagnostic]:
    return [diagnostic for _qid, diagnostic in _answer_pairs(t, answers)]


@dataclass(slots=True)
class _Decision:
    """Which lines assembly removes, and which questions it needs."""

    removed: dict[str, set[int]] = field(default_factory=dict)  # by file path
    files: dict[str, bool | None] = field(default_factory=dict)  # None: not decided yet
    attachments: list[bool | None] = field(default_factory=list)
    needed: dict[str, None] = field(default_factory=dict)  # an ordered set
    missing: dict[str, None] = field(default_factory=dict)


def _decide(t: _Template, answers: _Answers, *, inline: bool = False) -> _Decision:
    """Evaluate every condition that is reached (§15.7.2 steps 1–2). A unit
    inside a removed one is never reached, so its question is not needed; one
    under an unanswered condition is not reached *yet*. With *inline*, an
    include fragment is read where its ``{{include:}}`` is, so that the
    questions are in the order they are reached; otherwise after the body.
    Which lines are removed, and which files, is the same either way (a file
    included twice is read at its last include, which decides it)."""
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
    walked: set[str] = set()

    # A file included twice is kept or removed as its last include says.
    last = {include.path: include for include in t.main.includes}

    def enter(include: _Include) -> None:
        source = t.subs.get(include.path)
        if source is not None and last[include.path] is include and include.path not in walked:
            walked.add(include.path)
            decision.removed[include.path], _pending = _walk(source, holds, use)

    decision.removed[""], pending = _walk(t.main, holds, use, enter if inline else None)
    for include in t.main.includes:
        if include.start in decision.removed[""]:
            decision.files[include.path] = False
        else:
            decision.files[include.path] = None if include.start in pending else True
    for item, kept in zip(front.items if front else [], decision.attachments, strict=True):
        if item.file:
            decision.files[item.file] = kept
    for path, source in t.subs.items():
        if decision.files.get(path) and path not in walked:
            decision.removed[path], _pending = _walk(source, holds, use)
    return decision


def _walk(
    source: _Source, holds: Callable, use: Callable, enter: Callable | None = None
) -> tuple[set[int], set[int]]:
    """The lines of *source* assembly removes — drafting notes first, then
    absent units — and those waiting on an unanswered question. Units and
    insertions are visited in source order, so an enclosing unit is decided
    before anything inside it. *enter*, when given, is called with each include
    that is present, after the units that start on its line."""
    removed, pending = set(source.notes), set()
    events = sorted(
        [(unit.start, 0, k) for k, unit in enumerate(source.units)]
        + [(occ.line, 1, k) for k, occ in enumerate(source.occurrences)]
        + ([(include.start, 2, k) for k, include in enumerate(source.includes)] if enter else [])
    )
    for line, kind, k in events:
        if line in removed or line in pending:
            continue
        if kind == 2:
            enter(source.includes[k])
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
    plain: str = ""  # the line as the template reads it: each inserted text a word
    slot: int = 0  # where that reading lies among the template's kept lines
    after: bool = False  # directly follows a line that step 5 deleted


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
    plains: list[str] = []  # the template's reading of every kept line, the deleted ones too
    follows_deleted = False
    for index, text in enumerate(texts):
        if index in removed:
            continue
        line = _Line(text, index, check=follows_deleted, plain=text, slot=len(plains), after=follows_deleted)
        follows_deleted = False
        if index in by_line:
            line.text, line.first, line.plain = _fill(text, by_line[index], answers)
            if not is_blank(text) and is_blank(line.text):
                plains.append(line.plain)
                follows_deleted = True  # emptied by steps 4–5: deleted (step 5)
                continue
            line.check |= line.first is not None
        plains.append(line.plain)
        lines.append(line)
    _line_starts(lines, plains)
    return lines


_WORD = "zzz"  # an inserted text in the template's reading of a line (see ``_Reading``)


def _fill(text: str, occurrences: list[_Occurrence], answers: _Answers) -> tuple[str, int | None, str]:
    """Steps 4–5 on one body line, right to left, so that each insertion is
    escaped against the text that finally follows it (§15.7.3). Returns the
    line, the column of its first insertion, and the line as the template
    reads it, each text inserted a word."""
    first = None
    plain = text
    for occ in sorted(occurrences, key=lambda o: o.start, reverse=True):
        following = text[occ.end:]
        replaced = _body_replacement(occ, answers, following)
        if replaced is None:
            continue
        new, inserted = replaced
        text = text[:occ.start] + new + following
        literal = inserted and (occ.directive.name == "choose" or _effective_type(occ.directive, answers.declared) == "text")
        plain = plain[:occ.start] + (_WORD if literal else new) + plain[occ.end:]
        if inserted:
            first = occ.start
    return text, first, plain


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

_ITEM_MARKER_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+|$)")  # a bare marker too, as the parser reads it
_ATX_RE = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
_BLOCK_QUOTE_RE = re.compile(r" {0,3}>")
_BREAK_RE = re.compile(r" {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_SETEXT_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*$")
_BULLET_RE = re.compile(r" {0,3}[-+*](?:[ \t]+(?P<rest>.*))?$")
_ORDERED_RE = re.compile(r" {0,3}(?P<number>[0-9]{1,9})(?P<delim>[.)])(?:[ \t]+(?P<rest>.*))?$")


def _construct(content: str, *, in_paragraph: bool) -> str | None:
    """The block construct *content* begins, as CommonMark reads it — after
    paragraph text (*in_paragraph*) only those that can interrupt one."""
    if is_blank(content):
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
    # A list item interrupts paragraph text only as the parser allows: with
    # content and, when ordered, numbered 1.
    item = LIST_ITEM_RE.match(content)
    interrupts = item is not None and _may_interrupt(content, item)
    if _BULLET_RE.match(content) and (not in_paragraph or interrupts):
        return "list"
    if _ORDERED_RE.match(content) and (not in_paragraph or interrupts):
        return "ordered"
    return None




_QUOTE_MARKER_RE = re.compile(r"[ \t]{0,3}>[ \t]?")
_SIMPLE_RE = re.compile(r"(?:[ \t]{0,3}>[ \t]?|[ \t]{0,3}(?:[-+*]|1[.)])[ \t]+)*")
_PLAINISH_RE = re.compile(r"[ \t>*+\-0-9.)]*")
_ITEM_UNIT_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])[ \t]+")
_ITEM_RUN_RE = re.compile(rf"(?:{_ITEM_UNIT_RE.pattern})+")
_CONTEXT_RE = re.compile(rf"{_QUOTE_MARKER_RE.pattern}|[ \t]*(?:[-*+]|[0-9]{{1,9}}[.)])(?:[ \t]+|$)")
_BARE_ATX_RE = re.compile(r" {0,3}#{1,6}[ \t]*$")
_PUNCTUATION = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")  # what a backslash can escape


def _could_begin(text: str) -> bool:
    """False if no reading of *text*, behind any run of container markers, can begin a block."""
    pos = 0
    while True:
        rest = text[pos:]
        if _construct(rest, in_paragraph=False) is not None or _SETEXT_RE.match(rest) or _BARE_ATX_RE.match(rest):
            return True
        found = _CONTEXT_RE.match(text, pos)
        if found is None or found.end() == pos:
            return False
        pos = found.end()


def _marker_end(text: str, items: int, limit: int | None) -> int:
    """Where the content of *text* begins: after its block quote markers and the
    *items* list item markers that it starts, never past *limit*."""
    pos = 0
    while True:
        found = _QUOTE_MARKER_RE.match(text, pos)
        if found is None and items:
            found = _ITEM_MARKER_RE.match(text, pos)
            if found:
                items -= 1
        if found is None or (limit is not None and found.end() > limit):
            return pos
        pos = found.end()


def _item_column(line: str, count: int) -> int:
    """The column where the content of the *count*-th list item that *line* opens begins."""
    column = 0
    for found in islice(ItemMarkers(line), count):
        column = found.column
    return column


def _signatures(
    lines: list[str], last: int | None = None
) -> tuple[defaultdict[int, Counter[str]], dict[int, int]]:
    """What the parser reads *lines* as beginning, line by line, of the constructs
    §15.7.3 names: ``item``, ``quote``, ``heading`` (ATX), ``setext`` (for the underline
    of a heading that began on an earlier line), ``code``, ``rule`` and ``html``; and the
    content column of the list item each line lies in. Only line *last* if it is given."""
    layout = _Layout()
    blocks, sections = _parse_body(lines, layout)
    begun: defaultdict[int, Counter[str]] = defaultdict(Counter)
    columns: dict[int, int] = {}

    def mark(start: int, end: int, kind: str) -> None:
        """Record the construct of *kind* on lines ``[start, end)``: where it begins, or for
        a heading of several lines (setext) its underline and the lines before it."""
        if kind == "heading" and end - start > 1:
            lines_kinds = [(line, "setext") for line in range(start + 1, end)]
        elif kind in ("heading", "quote", "code", "rule", "html"):
            lines_kinds = [(start, kind)]
        else:
            lines_kinds = []
        for line, found in lines_kinds:
            if last is None or line == last:
                begun[line][found] += 1

    def walk(spans: list[_BlockSpan], model: list[Block], base: int, depth: int, opened: tuple[int, ...] | None) -> None:
        """*opened*: the lines the items around these blocks start on, or None in a quote."""
        for span, block in zip(spans, model, strict=True):
            start, end = base + span.start, base + span.end
            if last is not None and not start <= last < end:
                continue
            mark(start, end, span.kind)
            for item_span, item in zip(span.items, block.items, strict=True):
                first, stop = base + item_span.start, base + item_span.end
                if last is not None and not first <= last < stop:
                    continue
                if last is None or first == last:
                    begun[first]["item"] += 1
                inside = None if opened is None else (*opened, first)
                if inside is not None and last is None:
                    column = _item_column(lines[first], inside.count(first))
                    columns.update(dict.fromkeys(range(first, stop), column))
                walk(item_span.blocks, item.blocks, base, depth + 1, inside)
            if block.kind == "quote" and depth < MAX_QUOTE_DEPTH:
                children, inner = quote_content(block.text, depth + 1)
                walk(inner, children, start, depth + 1, None)

    for heading in layout.headings:
        mark(heading.start, heading.end, "heading")
    walk(layout.preamble, blocks, 0, 0, ())
    for (_heading, section_blocks), (_span, spans) in zip(sections, layout.sections, strict=True):
        walk(spans, section_blocks, 0, 0, ())
    return begun, columns


def _tail(lines: list[str]) -> Counter[str]:
    """What the parser reads the last of *lines* as beginning."""
    last = len(lines) - 1
    return _signatures(lines, last)[0][last]


def _last_block(lines: list[str]) -> int:
    """Where the last top-level block of *lines* starts (after any link definitions that
    precede it): the lines before it are closed, and later lines cannot change their reading."""
    layout = _Layout()
    _parse_body(lines, layout)
    spans = sorted(
        [(h.start, "heading") for h in layout.headings]
        + [(sp.items[-1].start if sp.items else sp.start, sp.kind) for c in layout.containers() for sp in c]
    )
    at = 0
    for index in range(len(spans) - 1, -1, -1):
        at = spans[index][0]
        if index == 0 or spans[index - 1][1] != "link_definition":
            break
    return at


class _Reading:
    """The template as the parser reads it, each inserted text a word: what every line
    begins, and the content column of the item it lies in. It is parsed once, when a line
    first needs it. The lines whose insertion begins their content have the spaces and
    tabs before it removed here too (§15.7.3), so that the template is read as it is
    written."""

    def __init__(self, plains: list[str], checked: list[_Line]) -> None:
        self.plains = plains
        self._checked = checked
        self._begun: defaultdict[int, Counter[str]] | None = None
        self._columns: dict[int, int] = {}
        self._starts: dict[int, tuple[int, bool]] = {}

    def begun(self, slot: int) -> Counter[str]:
        if self._begun is None:
            self._read()
        assert self._begun is not None
        return self._begun[slot]

    def column(self, slot: int) -> int:
        """The content column of the list item that line *slot* lies in; 0 if none."""
        self.begun(slot)
        return self._columns.get(slot, 0)

    def start(self, line: _Line) -> tuple[int, bool]:
        """Where the content of *line* begins in the template's reading of it (after
        its quote markers and item markers, never past its first insertion), and
        whether an insertion begins it."""
        self.begun(line.slot)
        if line.slot not in self._starts:
            self._starts[line.slot] = self._measure(line, self.begun(line.slot))
        return self._starts[line.slot]

    def _measure(self, line: _Line, begun: Counter[str]) -> tuple[int, bool]:
        plain = self.plains[line.slot]
        prefix = _marker_end(plain, begun["item"], line.first)
        return prefix, line.first is not None and not plain[prefix:line.first].strip(" \t")

    def _read(self) -> None:
        self._begun, self._columns = _signatures(self.plains)
        changed = False
        for line in self._checked:
            if line.first is None or not _PLAINISH_RE.fullmatch(line.plain[:line.first]):
                continue  # text before the insertion: nothing is removed
            prefix, begins = self._starts[line.slot] = self._measure(line, self._begun[line.slot])
            plain = self.plains[line.slot]
            stripped = plain[:prefix] + plain[prefix:].lstrip(" \t") if begins else plain
            if stripped != plain:
                self.plains[line.slot] = stripped
                changed = True
        if changed:
            self._begun, self._columns = _signatures(self.plains)


def _line_starts(lines: list[_Line], plains: list[str]) -> None:
    """The line-start check (§15.7.3) of every line that needs it, in order: each is read in
    the output as written so far. A line the parser need not be asked about is checked by
    its text; the others by what the parser reads them as beginning, compared with what it
    reads the template line as beginning (``_Reading``)."""
    checked = [line for line in lines if line.check]
    if not checked:
        return
    reading = _Reading(plains, checked)
    sync = 0  # where the last top-level block of the output so far starts
    for k, line in enumerate(lines):
        if not line.check:
            continue
        simple = _simple_start(line, reading)
        if simple is not None:
            line.text = simple
            continue
        sync += _last_block([earlier.text for earlier in lines[sync:k]])
        line.text = _line_start(line, [earlier.text for earlier in lines[sync:k]], reading)


def _simple_start(line: _Line, reading: _Reading) -> str | None:
    """The checked *line* when the parser need not be asked: what it begins cannot start a
    block, and the markers before the insertion are the template's own. None if it must be
    read. Only the template's ``-``, ``+``, ``*``, ``1.``, ``1)`` and ``>`` are known to be
    markers without asking the parser; any other run of item markers is, if the parser reads
    the template line as beginning that many items and no line before it was deleted."""
    text, first = line.text, line.first
    if first is None:
        return None if _could_begin(text) else text
    head = line.plain[:first]
    if not _PLAINISH_RE.fullmatch(head):
        return None if _could_begin(text) else text  # text precedes the insertion: nothing is removed
    prefix = _SIMPLE_RE.match(head).end()  # type: ignore[union-attr]
    if head[prefix:].strip(" \t"):
        run = _ITEM_RUN_RE.fullmatch(head)
        if run is None or line.after or reading.begun(line.slot)["item"] != len(_ITEM_UNIT_RE.findall(head)):
            return None
        prefix = len(head)
    content = text[prefix:].lstrip(" \t")
    return text[:prefix] + content if not _could_begin(content) else None


def _line_start(line: _Line, before: list[str], reading: _Reading) -> str:
    """The line-start check (§15.7.3) of *line*, which follows the output lines *before* it
    (from the last top-level block on): the line as assembled, with the whitespace that the
    insertion at its start replaces removed, kept from beginning a block construct that the
    template line did not."""
    text = line.text
    plain = reading.plains[line.slot]
    begun = reading.begun(line.slot)
    prefix, begins = reading.start(line)
    content = text[prefix:]
    if begins:
        content = content.lstrip(" \t")
    candidate = text[:prefix] + content
    if not content.strip(" \t"):
        return candidate
    if _kept(candidate, before, begun, plain, prefix):
        return candidate
    # A line the template placed in an item stays in it when its indentation goes.
    column = reading.column(line.slot) if not begins and not prefix and not begun["item"] else 0
    return _fix(candidate, prefix, before, begun, plain, column)


def _kept(line: str, before: list[str], template: Counter[str], plain: str, prefix: int) -> bool:
    """True if *line*, after the output lines *before*, begins no construct that *template*
    (what the parser reads the template line, *plain*, as beginning) does not. *prefix*: where
    the template's content begins, after its container markers."""
    read = _tail([*before, line])
    # The content is read after the markers that the output has: where a template marker is
    # text in the output (a ``3.`` that no item holds), what follows it may begin a block.
    return not read - template and not _free(line[_marker_end(line, read["item"], prefix):], plain[prefix:])


_FREE = ("heading", "quote", "fence")


def _free(content: str, template: str) -> bool:
    """*content* begins a construct that opens whatever the context, which *template* does
    not: the two the parser does not read, a bare ``#`` and a ``>`` on a lazy line, and a fence."""
    kind = _construct(content, in_paragraph=True)
    return kind in _FREE and _construct(template, in_paragraph=True) != kind


def _fix(candidate: str, prefix: int, before: list[str], template: Counter[str], plain: str, column: int) -> str:
    """*candidate* with the first form that begins nothing new: its content without leading
    spaces and tabs (keeping the *column* of the item it is in), or with a backslash before
    its first character or before the ``.`` or ``)`` of an ordered-list number, or both."""
    head, body = candidate[:prefix], candidate[prefix:]

    def escaped(body: str) -> str | None:
        ordered = _ORDERED_RE.match(body)
        at = ordered.start("delim") if ordered else len(body) - len(body.lstrip(" \t"))
        if not body[at:at + 1] or body[at] not in _PUNCTUATION:
            return None  # a backslash before a letter or digit is text
        return body[:at] + "\\" + body[at:]

    stripped = body.lstrip(" \t")
    if column and indent_width(body) >= column:
        stripped = " " * column + stripped
    forms = [stripped, escaped(body), escaped(stripped)] if stripped != body else [escaped(body)]
    forms = [form for form in forms if form is not None]
    for form in forms:
        if _kept(head + form, before, template, plain, prefix):
            return head + form
    return head + (forms[-1] if forms else body)  # unfixable: the template's own markers begin the construct


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
    document = parse(head + "\n".join(lines))
    entries = validate(document).index.sections
    headings = _layout(lines).headings
    if len(headings) != len(entries):
        raise AssemblyError("The combined document's headings do not match its parsed sections.")
    return {
        rows[heading.marker_line][1]: (entry.identifier, bool(section.identifier))
        for heading, entry, section in zip(headings, entries, document.sections, strict=True)
        if rows[heading.marker_line][1] is not None
    }


def _template_identifiers(t: _Template) -> dict[_Key, tuple[str, bool]]:
    if t.identifiers is None:
        t.identifiers = _compute_template_identifiers(t)
    return t.identifiers


def _compute_template_identifiers(t: _Template) -> dict[_Key, tuple[str, bool]]:
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
    """Lines whose blankness is content: fenced and indented code, list
    items' included. Blank lines in raw HTML are not exempt: step 8 names
    code blocks only. A list's other blank lines separate an item's blocks
    (§5.7): they collapse like any others."""
    kept: set[int] = set()

    def walk(spans: list[_BlockSpan]) -> None:
        for span in spans:
            if _kind(span) == "code":
                kept.update(range(span.start, span.end))
            for item in span.items:
                walk(item.blocks)

    for blocks in _layout(lines).containers():
        walk(blocks)
    return kept


def _finish(head: str, lines: list[_Line]) -> str:
    """Step 8: every run of blank lines outside code becomes one empty line,
    and the file ends with exactly one line break — or, left with nothing,
    is empty."""
    texts = [line.text for line in lines]
    code = _code_lines(texts)
    out: list[str] = []
    for index, text in enumerate(texts):
        if not is_blank(text) or index in code:
            out.append(text)
        elif not out or out[-1] != "" or (index - 1) in code:
            out.append("")
    while out and is_blank(out[-1]):
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

    Deprecated since 0.4.0, removed in 0.5.0: ``parse_template(text,
    resolve=...).form(answers).assemble()``, which reads the template once.

    A draft or an ordinary document goes through the same steps: its drafting
    notes are removed, its answered blanks filled and its blank lines
    collapsed. *load_file* reads the include fragments and LegalDown
    attachment files; the assembled ones are returned in ``files``.
    """
    warnings.warn(
        "legaldown.assemble() is deprecated since 0.4.0 and will be removed in 0.5.0; "
        "use legaldown.load_template() or parse_template(), then .form(answers).assemble()",
        LegaldownDeprecationWarning,
        stacklevel=2,
    )
    try:
        t = _read(template, load_file)
    except FrontmatterError as exc:
        return AssemblyResult(diagnostics=[frontmatter_diagnostic(exc)])
    if t.problems:
        return AssemblyResult(diagnostics=list(t.problems))
    resolved = _Answers(answers, t.declared)
    decision = _decide(t, resolved)
    diagnostics = _answer_diagnostics(t, answers) + [_missing_diagnostic(qid) for qid in decision.missing]
    return _emit_result(t, resolved, decision, diagnostics)


def frontmatter_diagnostic(exc: FrontmatterError, file: str | None = None) -> Diagnostic:
    """The error assembly reports for frontmatter that cannot be read: the one
    ``validate`` reports for it, with the same message and line (#43). *file* is
    the normalised path of the include fragment or attachment file it is in,
    when it is not the template's own."""
    return Diagnostic(
        "frontmatter-invalid-yaml", "error", frontmatter_message(exc), line=exc.line or 1, file=file or ""
    )


def _emit_result(
    t: _Template, resolved: _Answers, decision: _Decision, diagnostics: list[Diagnostic]
) -> AssemblyResult:
    """Steps 3–8 on a template whose answers have been decided; nothing is
    emitted while *diagnostics* hold an Error (§15.7.2 step 1)."""
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
    order, then each undeclared placeholder, in the order first written.
    Raises ``FrontmatterError`` when the template's frontmatter cannot be
    read.

    Deprecated since 0.4.0, removed in 0.5.0: ``parse_template(...).questions``."""
    warnings.warn(
        "legaldown.template_questions() is deprecated since 0.4.0 and will be removed in 0.5.0; "
        "use legaldown.load_template() or parse_template(), then .questions",
        LegaldownDeprecationWarning,
        stacklevel=2,
    )
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
    form grows as decisions are made. Raises ``FrontmatterError`` when the
    template's frontmatter cannot be read.

    Deprecated since 0.4.0, removed in 0.5.0: ``parse_template(...).form(answers).questions``."""
    warnings.warn(
        "legaldown.needed_questions() is deprecated since 0.4.0 and will be removed in 0.5.0; "
        "use legaldown.load_template() or parse_template(), then .form(answers).questions",
        LegaldownDeprecationWarning,
        stacklevel=2,
    )
    t = _read(template, load_file)
    decision = _decide(t, _Answers(answers, t.declared))
    by_id = {question.id: question for question in _questions(t)}
    return [by_id[qid] for qid in decision.needed if qid in by_id]
