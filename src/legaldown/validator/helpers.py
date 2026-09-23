"""Utility functions for LegalDown validation."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date as date_type

from ..markers import HTML_COMMENT_RE

# The §5.3 transliteration table, exhaustive: exactly these mappings.
_TRANSLITERATION = str.maketrans({
    "ß": "ss", "ẞ": "ss",
    "æ": "ae", "Æ": "ae",
    "œ": "oe", "Œ": "oe",
    "ø": "o", "Ø": "o",
    "đ": "d", "Đ": "d",
    "ð": "d", "Ð": "d",
    "þ": "th", "Þ": "th",
    "ł": "l", "Ł": "l",
    "ħ": "h", "Ħ": "h",
    "ı": "i",
})


def _ascii_text(value: str) -> tuple[str, bool]:
    """Steps 2–4 of §5.3: *value* reduced to ASCII, and whether step 4
    removed a letter or digit (the lossy-slug Warning rule)."""
    decomposed = unicodedata.normalize("NFKD", value)
    text = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    text = text.translate(_TRANSLITERATION)
    lossy = any(not c.isascii() and unicodedata.category(c)[0] in "LN" for c in text)
    return "".join(c for c in text if c.isascii()), lossy


def generate_identifier(value: str) -> tuple[str, bool]:
    """The identifier §5.3 generates from heading or term text *value*
    (without its trailing marker), and whether a letter or digit without an
    ASCII form, such as Cyrillic or CJK text, was dropped: the identifier
    then lost information, and an explicit one is recommended.

    Deterministic, so that every conformant implementation generates the
    same identifier. Comments are not part of the rendered text (§8.6).
    """
    text, lossy = _ascii_text(HTML_COMMENT_RE.sub("", value or ""))
    text = re.sub(r"[ \t_]", "-", text.lower())
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    text = text[:64].rstrip("-")
    if not text:
        return "section", lossy
    # The prefix is exempt from the 64-character maximum: no re-truncation.
    return (text if "a" <= text[0] <= "z" else f"section-{text}"), lossy


def slugify_identifier(value: str) -> str:
    """The identifier §5.3 generates from *value* (see generate_identifier)."""
    return generate_identifier(value)[0]


def format_section_number(counters: list[int], level: int) -> str:
    """Format hierarchical counters as a dotted number (e.g. ``1.2.3``)."""
    return ".".join(
        str(counters[idx]) for idx in range(1, level + 1) if counters[idx] > 0
    )


def is_valid_iso_date(value: str) -> bool:
    """Return True if *value* is a valid ``YYYY-MM-DD`` calendar date."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
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


# An integer or a decimal with a period (§10.3, §10.5): ASCII digits only,
# with no sign, exponent, grouping separators, currency symbols, or whitespace.
_DECIMAL_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")


def is_valid_money_amount(value: str) -> bool:
    """Return True if *value* is a valid ``{{money:}}`` amount (§10.3).

    The amount must be a non-negative finite number with no grouping
    separators, currency symbols, or whitespace; negative amounts are
    invalid — reductions are expressed in the surrounding prose.
    """
    return bool(_DECIMAL_RE.fullmatch(value or "")) and is_valid_numeric(value)


def is_positive_numeric(value: str) -> bool:
    """Return True if *value* is a positive integer or decimal (§10.5): a
    period as the decimal separator, and no sign or exponent."""
    return bool(_DECIMAL_RE.fullmatch(value or "")) and float(value) > 0
