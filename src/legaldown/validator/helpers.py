"""Utility functions for LegalDown validation."""
from __future__ import annotations

import math
import re
from datetime import date as date_type


def slugify_identifier(value: str, *, fallback: str = "section") -> str:
    """Convert free-form text into a valid LegalDown section identifier.

    Rules: lowercase, starts with a letter, contains only [a-z0-9-], max 64 chars.
    """
    text = re.sub(r"[^a-z0-9\s_-]", "", (value or "").lower())
    text = re.sub(r"[\s_]+", "-", text).strip("-")[:64]
    if not text:
        return fallback
    if not text[0].isalpha():
        text = f"{fallback}-{text}"[:64].strip("-")
    return text or fallback


def ensure_unique_identifier(identifier: str, used: set[str]) -> tuple[str, bool]:
    """Deduplicate *identifier* against *used* set.

    Returns ``(final_id, was_changed)``. Registers the final id in *used*.
    """
    if identifier not in used:
        used.add(identifier)
        return identifier, False
    suffix = 2
    while f"{identifier}-{suffix}" in used:
        suffix += 1
    updated = f"{identifier}-{suffix}"
    used.add(updated)
    return updated, True


def format_section_number(counters: list[int], level: int) -> str:
    """Format hierarchical counters as a dotted number (e.g. ``1.2.3``)."""
    return ".".join(
        str(counters[idx]) for idx in range(1, level + 1) if counters[idx] > 0
    )


def is_valid_iso_date(value: str) -> bool:
    """Return True if *value* is a valid ``YYYY-MM-DD`` calendar date."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        y, m, d = (int(x) for x in value.split("-"))
        date_type(y, m, d)
        return True
    except (ValueError, IndexError):
        return False


def is_valid_numeric(value: str) -> bool:
    """Return True if *value* parses as a finite number."""
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def is_valid_money_amount(value: str) -> bool:
    """Return True if *value* is a valid ``{{money:}}`` amount (§10.3).

    The amount must be a non-negative finite number with no grouping
    separators, currency symbols, or whitespace; negative amounts are
    invalid — reductions are expressed in the surrounding prose.
    """
    if not re.fullmatch(r"\d+(?:\.\d+)?", value or ""):
        return False
    return is_valid_numeric(value)


def is_positive_numeric(value: str) -> bool:
    """Return True if *value* parses as a number > 0."""
    try:
        return float(value) > 0
    except ValueError:
        return False
