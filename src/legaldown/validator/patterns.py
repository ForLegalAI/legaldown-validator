"""Regex patterns and constants for LegalDown validation."""
from __future__ import annotations

import re

# ── Identifier format ─────────────────────────────────────────────
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# ── Domain constants ──────────────────────────────────────────────
# Reserved value-type names (§3.2): field_types keys must not collide with
# the built-in field specs or placeholder types.
RESERVED_VALUE_TYPES: frozenset[str] = frozenset({"date", "money", "duration", "party", "text"})
VALID_DOC_TYPES: frozenset[str] = frozenset({"contract", "unilateral_act", "collective_act"})
# §10.5: the bare unit "M" is deliberately undefined (ISO 8601 ambiguity);
# validators reject it with a hint suggesting MIN (minutes) or MO (months).
# In the order §10.5 lists them, for diagnostics.
VALID_DURATION_UNITS: tuple[str, ...] = ("S", "MIN", "H", "D", "W", "MO", "Y")
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

