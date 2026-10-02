"""Validation result types."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal


@dataclass(slots=True)
class SectionIndexEntry:
    """A section's position in the document's numbered index.

    ``number`` is the decimal number (§13.1), counted from the document's
    shallowest heading level. A level a heading skips (heading-skip) counts
    as 1, so ``# A``, ``### B``, ``## C`` are 1, 1.1.1, 1.2: no two sections
    share a number, except alternatives and what they contain (§15.8).
    ``path`` joins the identifiers of the section and its ancestors."""
    title: str
    identifier: str
    path: str
    level: int
    number: str


@dataclass(slots=True, frozen=True)
class Diagnostic:
    """A single validation finding.

    Carries the **stable rule id** defined by specification §16.1 — the only
    part of a diagnostic that is stable across implementations and spec
    revisions (§16.9) — plus the severity level and human-readable message,
    and where it is (§16.9): the file, and the line (from 1) of what it
    reports, which a document parsed from source has (``parse``)
    and one built from a dict has not (None). Compare diagnostics by their
    fields, not with one built without a line.
    """
    rule: str
    level: str  # "error" | "warning" | "info"
    message: str
    line: int | None = None
    file: str = ""


@dataclass(slots=True, frozen=True, kw_only=True)
class PlacedMarker:
    """A marker in body text that is in a marker position and applies in
    its document (§5.7, §15.3), as ``validate`` places it — the
    marker of a top-level paragraph or of a list item's first paragraph. A
    section's own marker is its ``Section.identifier`` and ``condition``.

    It is in fragment *fragment* of ``block_fragments(block)``, block
    *block* of the preamble (*section* None) or of section *section*, at
    *offset* in that fragment's text: ``text[offset:offset + len(source)]``
    is *source*. That text is the *field* of the block holding it: ``text``
    (a paragraph's or a definition's, a list item's first paragraph's), or
    ``suffix`` (the text after a ``{{ref:}}`` or ``{{term:}}`` lifted out of
    a paragraph). A fragment holds at most one placed marker, which ends it.
    In a list, *item* is the list item it marks, numbered in pre-order among
    all the list's items — an item before the items nested in it, empty ones
    included — as ``list_fragments`` numbers them; None elsewhere.

    *identifier* is the ``#id`` that applies: ``""`` for the marker of a
    paragraph holding only an ``{{include:}}``, whose ``#id`` is ignored
    (§12.2, *include_only*). Both are as written: an invalid one is
    reported (anchor-format, condition-invalid), so check the result's
    ``is_valid`` before relying on them. *line* is its line (§16.9), None
    for a document built in code or changed since it was parsed."""

    section: int | None
    block: int
    fragment: int
    offset: int
    source: str
    identifier: str
    condition: str
    field: Literal["text", "suffix"] = "text"
    item: int | None = None
    include_only: bool = False
    line: int | None = None


#: A diagnostic's line (from 1), or a function giving it, called only when a
#: diagnostic is recorded at it: finding a line costs more than knowing where.
Line = int | None | Callable[[], "int | None"]


@dataclass(slots=True)
class InlineValues:
    """The field-spec values found in the body, as written, in document order:
    ``dates`` are the date values alone, the others ``(value, qualifier)``."""
    dates: list[str] = field(default_factory=list)
    #: ``(amount, currency)``
    money: list[tuple[str, str]] = field(default_factory=list)
    #: ``(amount, unit)``
    durations: list[tuple[str, str]] = field(default_factory=list)
    #: ``(value, field type)``
    fields: list[tuple[str, str]] = field(default_factory=list)
    #: ``(placeholder id, type)``
    placeholders: list[tuple[str, str]] = field(default_factory=list)


@dataclass(slots=True)
class DocumentIndex:
    """What validating a document resolved, besides what it found: the numbered
    sections, the display text the references resolve to, the values in the
    body, and the template facts. A renderer or a UI reuses it instead of
    deriving it again. Values and markers are as written: one that is invalid
    is reported in the diagnostics too, so check ``ValidationResult.is_valid``
    before relying on them."""
    #: The numbered section index, in document order (positionally paired with
    #: ``Document.sections``).
    sections: list[SectionIndexEntry] = field(default_factory=list)
    #: The sections by the identifier or path a ``{{ref:}}`` names.
    section_lookup: dict[str, SectionIndexEntry] = field(default_factory=dict)
    #: Display text, by definition id, party name, side name, attachment id.
    definition_lookup: dict[str, str] = field(default_factory=dict)
    party_lookup: dict[str, str] = field(default_factory=dict)
    side_lookup: dict[str, str] = field(default_factory=dict)
    attachment_lookup: dict[str, str] = field(default_factory=dict)
    values: InlineValues = field(default_factory=InlineValues)
    #: Whether the document is a template (§15.1): it declares questions,
    #: carries a condition, or holds a ``{{choose:}}``.
    is_template: bool = False
    #: The markers in body text that apply (``PlacedMarker``), in document
    #: order.
    placed_markers: list[PlacedMarker] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ValidationResult:
    """Output of ``validate``: the diagnostics found, and the index built.

    ``diagnostics`` is the authoritative record (rule id + severity +
    message, §16.9); ``errors`` / ``warnings`` / ``infos`` are its messages by
    severity. ``index`` is everything else validating resolved (``DocumentIndex``).
    """
    diagnostics: list[Diagnostic] = field(default_factory=list)
    index: DocumentIndex = field(default_factory=DocumentIndex)

    def _messages(self, level: str) -> list[str]:
        return [d.message for d in self.diagnostics if d.level == level]

    @property
    def errors(self) -> list[str]:
        """The messages of the Error-level diagnostics."""
        return self._messages("error")

    @property
    def warnings(self) -> list[str]:
        """The messages of the Warning-level diagnostics."""
        return self._messages("warning")

    @property
    def infos(self) -> list[str]:
        """The messages of the Info-level diagnostics."""
        return self._messages("info")

    def rules(self, level: str | None = None) -> set[str]:
        """Rule ids present in the result, optionally filtered by *level*."""
        return {
            d.rule for d in self.diagnostics if level is None or d.level == level
        }

    @property
    def is_valid(self) -> bool:
        """True if no errors were found."""
        return not any(d.level == "error" for d in self.diagnostics)


@dataclass(slots=True)
class _Recorder(ValidationResult):
    """A ``ValidationResult`` as the validator builds it: it records the
    diagnostics, and what only the checks need. ``result()`` is what the
    caller gets."""
    #: The ``{{term:}}`` targets met, for the unreferenced-definition check.
    used_terms: set[str] = field(default_factory=set)
    #: The lines diagnostics are recorded at by default (``at``), innermost last.
    _lines: list[Line] = field(default_factory=list, repr=False, compare=False)

    @contextmanager
    def at(self, line: Line) -> Iterator[None]:
        """Record the diagnostics made inside the block at *line*, unless
        they give their own."""
        self._lines.append(line)
        try:
            yield
        finally:
            self._lines.pop()

    def _record(self, rule: str, level: str, message: str, line: Line) -> None:
        if line is None and self._lines:
            line = self._lines[-1]
        if callable(line):
            line = line()
        self.diagnostics.append(Diagnostic(rule=rule, level=level, message=message, line=line))

    def error(self, rule: str, message: str, *, line: Line = None) -> None:
        """Record an Error-level diagnostic under stable rule id *rule*, at
        *line* (``at``)."""
        self._record(rule, "error", message, line)

    def warning(self, rule: str, message: str, *, line: Line = None) -> None:
        """Record a Warning-level diagnostic under stable rule id *rule*, at
        *line* (``at``)."""
        self._record(rule, "warning", message, line)

    def info(self, rule: str, message: str, *, line: Line = None) -> None:
        """Record an Info-level diagnostic under stable rule id *rule*, at
        *line* (``at``)."""
        self._record(rule, "info", message, line)

    def result(self) -> ValidationResult:
        """What was recorded, as the plain ``ValidationResult``."""
        return ValidationResult(diagnostics=self.diagnostics, index=self.index)
