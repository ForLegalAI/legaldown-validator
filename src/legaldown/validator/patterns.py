"""Regex patterns and constants for LegalDown validation."""
from __future__ import annotations

import re

# ── Identifier format ─────────────────────────────────────────────
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# ── Domain constants ──────────────────────────────────────────────
# File extensions of LegalDown source (§2.1).
LEGALDOWN_EXTENSIONS: tuple[str, ...] = (".lgd", ".legaldown", ".legal.md")
# Reserved value-type names (§3.2): field_types keys must not collide with
# the built-in field specs or placeholder types.
RESERVED_VALUE_TYPES: frozenset[str] = frozenset({"date", "money", "duration", "party", "text"})
VALID_DOC_TYPES: frozenset[str] = frozenset({"contract", "unilateral_act", "collective_act"})
# The `type` a party may have (§3.4).
PARTY_TYPES: tuple[str, ...] = ("legal_entity", "natural_person")
# The deepest heading level LegalDown supports (§4.1).
MAX_SECTION_LEVEL = 5
# §10.5: the bare unit "M" is deliberately undefined (ISO 8601 ambiguity);
# validators reject it with a hint suggesting MIN (minutes) or MO (months).
# In the order §10.5 lists them, for diagnostics.
DURATION_UNITS: tuple[str, ...] = ("S", "MIN", "H", "D", "W", "MO", "Y")
VALID_DURATION_UNITS: frozenset[str] = frozenset(DURATION_UNITS)
# The rules of the final check (§15.9): the only ones it reports.
_UNFILLED = "placeholder-unfilled"
_CONSTRUCT_PRESENT = "template-construct-present"
FINAL_CHECK_RULES: frozenset[str] = frozenset({_UNFILLED, _CONSTRUCT_PRESENT})
# The prefix of the rule ids this implementation defines for checks of its own (§16.1: a
# prefix ending in a hyphen, so that no id the specification assigns later collides), and
# the one it reports for a check category it did not perform (§17.5).
IMPLEMENTATION_RULE_PREFIX = "ldv-"
SKIPPED_RULE = "ldv-check-skipped"
# And the Error of assembly, not of validation, for a document whose includes cannot all be
# followed: they nest deeper, or run to more includes and headings, than it goes (#118).
INCLUDE_LIMIT_RULE = IMPLEMENTATION_RULE_PREFIX + "include-limit"
# And the one for a frontmatter key that another replaces: the same key written twice in a mapping, or keys that
# the model reads as one (`1` and `'1'`; in `translations` and `field_types`, `fr` and `' fr'`) (#126, #131). An
# Error for the same key written twice, a Warning for different keys (the specification only warns of a padded
# language code).
FRONTMATTER_KEY_DUPLICATE_RULE = IMPLEMENTATION_RULE_PREFIX + "frontmatter-key-duplicate"
# §10.7 placeholder types — also the value question types of §15.2.
VALID_PLACEHOLDER_TYPES: frozenset[str] = frozenset({"text", "date", "money", "duration"})

KNOWN_CURRENCIES: frozenset[str] = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
    "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
    "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
    "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF", "IDR", "ILS",
    "INR", "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR",
    "KMF", "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD",
    "LSL", "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU",
    "MUR", "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK",
    "NPR", "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG",
    "QAR", "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK",
    "SGD", "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL",
    "THB", "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH",
    "UGX", "USD", "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD",
    "XOF", "XPF", "YER", "ZAR", "ZMW", "ZWL",
})

# ISO 639-1 (§3.2): the language codes `language`, `authoritative` and the `translations` keys
# are written in, lowercase as the registry has them.
ISO_639_1_CODES: frozenset[str] = frozenset({
    "aa", "ab", "ae", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az", "ba", "be", "bg", "bh",
    "bi", "bm", "bn", "bo", "br", "bs", "ca", "ce", "ch", "co", "cr", "cs", "cu", "cv", "cy", "da",
    "de", "dv", "dz", "ee", "el", "en", "eo", "es", "et", "eu", "fa", "ff", "fi", "fj", "fo", "fr",
    "fy", "ga", "gd", "gl", "gn", "gu", "gv", "ha", "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz",
    "ia", "id", "ie", "ig", "ii", "ik", "io", "is", "it", "iu", "ja", "jv", "ka", "kg", "ki", "kj",
    "kk", "kl", "km", "kn", "ko", "kr", "ks", "ku", "kv", "kw", "ky", "la", "lb", "lg", "li", "ln",
    "lo", "lt", "lu", "lv", "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my", "na", "nb",
    "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny", "oc", "oj", "om", "or", "os", "pa", "pi",
    "pl", "ps", "pt", "qu", "rm", "rn", "ro", "ru", "rw", "sa", "sc", "sd", "se", "sg", "si", "sk",
    "sl", "sm", "sn", "so", "sq", "sr", "ss", "st", "su", "sv", "sw", "ta", "te", "tg", "th", "ti",
    "tk", "tl", "tn", "to", "tr", "ts", "tt", "tw", "ty", "ug", "uk", "ur", "uz", "ve", "vi", "vo",
    "wa", "wo", "xh", "yi", "yo", "za", "zh", "zu",
})
