"""Regex patterns and constants for LegalDown inline directive validation."""
from __future__ import annotations

import re

# ── Identifier format ─────────────────────────────────────────────
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# ── Inline directive patterns ─────────────────────────────────────
REF_RE = re.compile(r"\{\{ref:\s*([^,}]+)(?:,\s*format=([^}]+))?}}")
TERM_RE = re.compile(r"\{\{term:\s*([^,}]+)(?:,\s*label=([^}]+))?}}")
DATE_RE = re.compile(r"\{\{date:\s*([^,}]+?)(?:,\s*note=[^}]*)?\}\}")
MONEY_RE = re.compile(
    r"\{\{money:\s*([^,}]+?)(?:,\s*currency=([^,}]+?))?(?:,\s*note=[^}]*)?\}\}"
)
DURATION_RE = re.compile(
    r"\{\{duration:\s*([^,}]+?)(?:,\s*unit=([^,}]+?))?(?:,\s*note=[^}]*)?\}\}"
)
PARTY_RE = re.compile(
    r"\{\{party:\s*([^,}]+?)(?:,\s*label=([^,}]+?))?(?:,\s*note=[^}]*)?\}\}"
)
FIELD_RE = re.compile(
    r"\{\{field:\s*([^,}]+?)(?:,\s*type=([^,}]+?))?(?:,\s*note=[^}]*)?\}\}"
)
PLACEHOLDER_RE = re.compile(
    r"\{\{placeholder:\s*([^,}]+?)(?:,\s*type=([^,}]+?))?(?:,\s*currency=([^,}]+?))?(?:,\s*note=[^}]*)?\}\}"
)
ATTACH_RE = re.compile(r"\{\{attach:\s*([^,}]+?)(?:,\s*label=[^}]*)?\}\}")
SIDE_RE = re.compile(
    r"\{\{side:\s*([^,}]+?)(?:,\s*label=([^,}]+?))?(?:,\s*note=[^}]*)?\}\}"
)
NOTE_RE = re.compile(r"\{\{\w+:[^}]*,\s*note=([^}]*)\}\}")

# Any well-formed-looking directive opener, for unknown-name detection (§11.5).
DIRECTIVE_NAME_RE = re.compile(r"\{\{([a-z]+):")

# ── Domain constants ──────────────────────────────────────────────
# Directive vocabulary defined by spec §11.1.
KNOWN_DIRECTIVES: frozenset[str] = frozenset({
    "ref", "def", "term", "date", "money", "duration", "party", "side",
    "field", "placeholder", "include", "attach",
})
# Reserved value-type names (§3.2): field_types keys must not collide with
# the built-in field specs or placeholder types.
RESERVED_VALUE_TYPES: frozenset[str] = frozenset({"date", "money", "duration", "party", "text"})
# Backward-compatible alias (pre-0.1 name; lacked "text").
BUILTIN_DIRECTIVES: frozenset[str] = RESERVED_VALUE_TYPES
VALID_DOC_TYPES: frozenset[str] = frozenset({"contract", "unilateral_act", "collective_act"})
# §10.5: the bare unit "M" is deliberately undefined (ISO 8601 ambiguity);
# validators reject it with a hint suggesting MIN (minutes) or MO (months).
VALID_DURATION_UNITS: frozenset[str] = frozenset({"S", "MIN", "H", "D", "W", "MO", "Y"})
VALID_PLACEHOLDER_TYPES: frozenset[str] = frozenset({"text", "date", "money"})

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

