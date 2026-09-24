"""Validation result types."""
from __future__ import annotations

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
    revisions (§16.9) — plus the severity level and human-readable message.
    """
    rule: str
    level: str  # "error" | "warning" | "info"
    message: str


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

    def error(self, rule: str, message: str) -> None:
        """Record an Error-level diagnostic under stable rule id *rule*."""
        self.diagnostics.append(Diagnostic(rule=rule, level="error", message=message))
        self.errors.append(message)

    def warning(self, rule: str, message: str) -> None:
        """Record a Warning-level diagnostic under stable rule id *rule*."""
        self.diagnostics.append(Diagnostic(rule=rule, level="warning", message=message))
        self.warnings.append(message)

    def info(self, rule: str, message: str) -> None:
        """Record an Info-level diagnostic under stable rule id *rule*."""
        self.diagnostics.append(Diagnostic(rule=rule, level="info", message=message))
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
