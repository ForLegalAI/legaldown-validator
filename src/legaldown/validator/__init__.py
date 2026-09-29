"""legaldown.validator — Document validation and structural analysis."""
from __future__ import annotations

from .conditions import ALWAYS, Condition, Presence, condition_problem, exclusive, parse_condition
from .core import AttachmentDefinitionsImporter, DefinitionsImporter, is_template, validate_document
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
from .result import Diagnostic, PlacedMarker, SectionIndexEntry, ValidationResult

__all__ = [
    # Core
    "validate_document",
    "is_template",
    "DefinitionsImporter",
    "AttachmentDefinitionsImporter",
    # Result types
    "ValidationResult",
    "SectionIndexEntry",
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
