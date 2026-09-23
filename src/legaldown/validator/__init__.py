"""legaldown.validator — Document validation and structural analysis."""
from __future__ import annotations

from .core import AttachmentDefinitionsImporter, DefinitionsImporter, validate_document
from .helpers import (
    ensure_unique_identifier,
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
from .result import Diagnostic, SectionIndexEntry, ValidationResult

__all__ = [
    # Core
    "validate_document",
    "DefinitionsImporter",
    "AttachmentDefinitionsImporter",
    # Result types
    "ValidationResult",
    "SectionIndexEntry",
    "Diagnostic",
    # Helpers
    "slugify_identifier",
    "ensure_unique_identifier",
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
