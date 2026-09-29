"""Validation result types."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


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
    reports, which a document parsed from source has (``parse_document``)
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
    its document (§5.7, §15.3), as ``validate_document`` places it — the
    marker of a top-level paragraph or of a list item's first paragraph. A
    section's own marker is its ``Section.identifier`` and ``condition``.

    It is in fragment *fragment* of ``block_fragments(block)``, block
    *block* of the preamble (*section* None) or of section *section*, at
    *offset* in that fragment's text: ``text[offset:offset + len(source)]``
    is *source*. That text is the *field* of the block holding it: ``text``
    (a paragraph's or a definition's, a list item's first paragraph's), or
    ``suffix`` (the text after a ``{{ref:}}`` or ``{{term:}}`` lifted out of
    a paragraph). A fragment holds at most one placed marker, which ends it.
    In a list, *item* is the list item it marks, numbered in document order
    among all the list's items — nested ones and empty ones included — as
    ``list_fragments`` numbers them; None elsewhere.

    *identifier* is the ``#id`` that applies: ``""`` for the marker of a
    paragraph holding only an ``{{include:}}``, whose ``#id`` is ignored
    (§12.2, *include_only*). *line* is its line (§16.9), None for a document
    built in code or changed since it was parsed."""

    section: int | None
    block: int
    fragment: int
    offset: int
    source: str
    identifier: str
    condition: str
    field: str = "text"
    item: int | None = None
    include_only: bool = False
    line: int | None = None


#: A diagnostic's line (from 1), or a function giving it, called only when a
#: diagnostic is recorded at it: finding a line costs more than knowing where.
Line = int | None | Callable[[], "int | None"]


@dataclass(slots=True)
class ValidationResult:
    """Output of ``validate_document``: collected diagnostics and indices.

    ``diagnostics`` is the authoritative record (rule id + severity +
    message, §16.9). ``errors`` / ``warnings`` / ``infos`` remain as plain
    message lists for existing callers and stay in sync with it.
    """
    diagnostics: list[Diagnostic] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    infos: list[str] = field(default_factory=list)
    sections: list[SectionIndexEntry] = field(default_factory=list)
    section_lookup: dict[str, SectionIndexEntry] = field(default_factory=dict)
    definition_lookup: dict[str, str] = field(default_factory=dict)
    party_lookup: dict[str, str] = field(default_factory=dict)
    side_lookup: dict[str, str] = field(default_factory=dict)
    attachment_lookup: dict[str, str] = field(default_factory=dict)
    used_terms: set[str] = field(default_factory=set)
    inline_dates: list[str] = field(default_factory=list)
    inline_money: list[tuple[str, str]] = field(default_factory=list)
    inline_durations: list[tuple[str, str]] = field(default_factory=list)
    inline_fields: list[tuple[str, str]] = field(default_factory=list)
    inline_placeholders: list[tuple[str, str]] = field(default_factory=list)
    #: Whether the document is a template (§15.1): it declares questions,
    #: carries a condition, or holds a ``{{choose:}}``.
    is_template: bool = False
    #: The markers in body text that apply (``PlacedMarker``), in document
    #: order.
    placed_markers: list[PlacedMarker] = field(default_factory=list)
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
        self.errors.append(message)

    def warning(self, rule: str, message: str, *, line: Line = None) -> None:
        """Record a Warning-level diagnostic under stable rule id *rule*, at
        *line* (``at``)."""
        self._record(rule, "warning", message, line)
        self.warnings.append(message)

    def info(self, rule: str, message: str, *, line: Line = None) -> None:
        """Record an Info-level diagnostic under stable rule id *rule*, at
        *line* (``at``)."""
        self._record(rule, "info", message, line)
        self.infos.append(message)

    def rules(self, level: str | None = None) -> set[str]:
        """Rule ids present in the result, optionally filtered by *level*."""
        return {
            d.rule for d in self.diagnostics if level is None or d.level == level
        }

    @property
    def is_valid(self) -> bool:
        """True if no errors were found."""
        return len(self.errors) == 0
