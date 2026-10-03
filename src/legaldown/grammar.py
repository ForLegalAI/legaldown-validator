"""legaldown.grammar — The language's constants and rules that need no document.

The vocabulary the specification fixes: directive names and parameters
(§11), placeholder, duration and question types (§10, §15.2), party types
(§3.4), the identifier format (§5.3), currencies, file extensions (§2.1),
and the limits the parser and validator apply; and the small rules over them:
the §5.3 identifier of a text, the value formats of §10, and template
conditions (§15.3, §15.4).

This module is part of the supported API and covered by semantic versioning.
Every name here is the validator's own object, not a copy: the validator and
a tool built on this module cannot disagree about the language. Import them
from here; the modules they are defined in are internal and may be
reorganised.

    from legaldown.grammar import VALID_PLACEHOLDER_TYPES, is_valid_iso_date

    VALID_PLACEHOLDER_TYPES        # frozenset({"text", "date", "money", "duration"})
    is_valid_iso_date("2026-02-30")  # False
"""
from __future__ import annotations

from .definitions import DELIMITER_PAIRS
from .directives import DIRECTIVE_PARAMS, KNOWN_DIRECTIVES, PLACEHOLDER_TYPE_PARAMS
from .models import LIST_KINDS
from .parser import MAX_LIST_DEPTH, MAX_QUOTE_DEPTH
from .specification import SPEC_VERSION
from .validator.conditions import ALWAYS, Condition, Presence, condition_problem, exclusive, parse_condition
from .validator.core import FINAL_CHECK_RULES
from .validator.helpers import (
    format_section_number,
    is_positive_numeric,
    is_valid_iso_date,
    is_valid_money_amount,
    is_valid_numeric,
    slugify_identifier,
)
from .validator.patterns import (
    DURATION_UNITS,
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    LEGALDOWN_EXTENSIONS,
    MAX_SECTION_LEVEL,
    PARTY_TYPES,
    RESERVED_VALUE_TYPES,
    VALID_DOC_TYPES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .validator.templates import (
    DECISION_QUESTION_TYPES,
    DRAFTING_MARKER,
    QUESTION_TYPES,
    VALUE_QUESTION_TYPES,
    choose_problem,
)

__all__ = [
    # The specification
    "SPEC_VERSION",
    "LEGALDOWN_EXTENSIONS",
    # Directives (§11)
    "KNOWN_DIRECTIVES",
    "DIRECTIVE_PARAMS",
    "PLACEHOLDER_TYPE_PARAMS",
    # Identifiers and values (§3, §5.3, §10)
    "IDENTIFIER_RE",
    "RESERVED_VALUE_TYPES",
    "VALID_DOC_TYPES",
    "PARTY_TYPES",
    "VALID_PLACEHOLDER_TYPES",
    "DURATION_UNITS",
    "VALID_DURATION_UNITS",
    "KNOWN_CURRENCIES",
    "DELIMITER_PAIRS",
    # Structure and limits
    "LIST_KINDS",
    "MAX_SECTION_LEVEL",
    "MAX_QUOTE_DEPTH",
    "MAX_LIST_DEPTH",
    # Templates (§15)
    "VALUE_QUESTION_TYPES",
    "DECISION_QUESTION_TYPES",
    "QUESTION_TYPES",
    "DRAFTING_MARKER",
    "FINAL_CHECK_RULES",
    # Rules over them
    "slugify_identifier",
    "format_section_number",
    "is_valid_iso_date",
    "is_valid_numeric",
    "is_valid_money_amount",
    "is_positive_numeric",
    "parse_condition",
    "Condition",
    "condition_problem",
    "exclusive",
    "Presence",
    "ALWAYS",
    "choose_problem",
]
