"""Utility functions for LegalDown validation."""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date as date_type

from ..markdown import strip_comments
from ..models import Metadata
from .patterns import ISO_639_1_CODES

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


def generate_identifier(value: str, fallback: str = "section") -> tuple[str, bool]:
    """The identifier §5.3 generates from heading or term text *value*
    (without its trailing marker), or *fallback* when the text yields none,
    and whether a letter or digit without an
    ASCII form, such as Cyrillic or CJK text, was dropped: the identifier
    then lost information, and an explicit one is recommended.

    Deterministic, so that every conformant implementation generates the
    same identifier. Comments are not part of the rendered text (§8.6).
    """
    text, lossy = _ascii_text(strip_comments(value or ""))
    text = re.sub(r"[ \t_]", "-", text.lower())
    text = re.sub(r"[^a-z0-9-]", "", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    text = text[:64].rstrip("-")
    if not text:
        return fallback, lossy
    # The prefix is exempt from the 64-character maximum: no re-truncation.
    return (text if "a" <= text[0] <= "z" else f"section-{text}"), lossy


def slugify_identifier(value: str, *, fallback: str = "section") -> str:
    """The identifier §5.3 generates from heading or term text *value*:
    lower case, ASCII, hyphen-separated, at most 64 characters, and prefixed
    ``section-`` when it would begin with a digit.

    *fallback* is returned when the text yields no identifier at all (it
    holds no letter or digit with an ASCII form), which §5.3 names
    ``section``; pass ``""`` to tell that case from real text.
    """
    return generate_identifier(value, fallback)[0]


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


def declared_language(meta: Metadata) -> str:
    """The document's ``language`` if it says one, else ``""``: §3.2 has no default, and the
    ``"en"`` the model holds for a document that does not say (``Metadata.language_declared``) is
    its own, so it is no language said."""
    return meta.language if meta.language_declared or meta.language != "en" else ""


def identifier_rule(meta: Metadata) -> tuple[str | None, str]:
    """The rule that asks the document's own headings and ``{{def:}}`` for explicit identifiers
    (§14.2, §16.7; Core, §17.2), or None, and then why not.

    ``translation-authoritative-absent`` when it links ``translations`` and declares no
    ``authoritative`` (the primary cannot be told from its translations). ``translation-implicit-id``
    when it is itself a translation: it links ``translations``, and its ``language`` and
    ``authoritative`` are declared, valid ISO 639-1 codes that differ, ``authoritative`` being one of
    the group's languages, a ``translations`` key (else ``authoritative-not-declared`` is the finding,
    and a typo in it is not the primary). A value that is no code is no language."""
    if not meta.translations:
        return None, "it links no translations"
    if not meta.authoritative:
        return "translation-authoritative-absent", ""
    language = declared_language(meta)
    if not language:
        return None, "its language is not declared"
    if language not in ISO_639_1_CODES or meta.authoritative not in ISO_639_1_CODES:
        return None, "its language or authoritative is not an ISO 639-1 code"
    if language == meta.authoritative:
        return None, "it is the primary: its language is its authoritative"
    if meta.authoritative not in meta.translations:
        return None, "its authoritative is not one of its translations"
    return "translation-implicit-id", ""
