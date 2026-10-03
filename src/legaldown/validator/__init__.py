"""legaldown.validator — Document validation and structural analysis.

``validate``, ``is_template`` and the result types, which ``legaldown``
exports too; and the condition, value and identifier rules and constants
the checks use, whose home for tools is ``legaldown.grammar`` (the same
objects). Part of the supported API, like ``legaldown``; its submodules are
internal.
"""
from __future__ import annotations

from .conditions import ALWAYS, Condition, Presence, condition_problem, exclusive, parse_condition
from .core import AttachmentDefinitionsImporter, DefinitionsImporter, is_template, validate, validate_document
from .helpers import (
    format_section_number,
    is_positive_numeric,
    is_valid_iso_date,
    is_valid_money_amount,
    is_valid_numeric,
    slugify_identifier,
)
from .patterns import (
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    RESERVED_VALUE_TYPES,
    VALID_DOC_TYPES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import Blank, Diagnostic, DocumentIndex, InlineValues, PlacedMarker, SectionIndexEntry, ValidationResult
from .templates import is_drafting_note

__all__ = [
    # Core
    "validate",
    "validate_document",  # deprecated since 0.4.0, removed in 0.5.0: use ``validate``
    "is_template",
    "is_drafting_note",
    "DefinitionsImporter",  # deprecated since 0.4.0, removed in 0.5.0
    "AttachmentDefinitionsImporter",  # deprecated since 0.4.0, removed in 0.5.0
    # Result types
    "ValidationResult",
    "DocumentIndex",
    "InlineValues",
    "SectionIndexEntry",
    "Blank",
    "Diagnostic",
    "PlacedMarker",
    # Conditions (§15.3, §15.4): a condition's presence, a set of them
    "parse_condition",
    "Condition",
    "condition_problem",
    "exclusive",
    "Presence",
    "ALWAYS",
    # Helpers
    "slugify_identifier",
    "format_section_number",
    "is_valid_iso_date",
    "is_valid_numeric",
    "is_valid_money_amount",
    "is_positive_numeric",
    # Patterns & constants
    "IDENTIFIER_RE",
    "RESERVED_VALUE_TYPES",
    "VALID_DOC_TYPES",
    "VALID_DURATION_UNITS",
    "VALID_PLACEHOLDER_TYPES",
    "KNOWN_CURRENCIES",
]
