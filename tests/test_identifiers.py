"""Automatic identifier generation (§5.3) and its lossy-slug Warnings."""
from __future__ import annotations

import pytest

from legaldown.parser import parse_document
from legaldown.validator import validate_document
from legaldown.validator.helpers import generate_identifier, slugify_identifier


@pytest.mark.parametrize(
    ("text", "identifier"),
    [
        # The §5.3 examples.
        ("Confidential Information & Trade Secrets", "confidential-information-trade-secrets"),
        ("Définitions Générales", "definitions-generales"),
        ("Smluvní pokuta", "smluvni-pokuta"),
        ("Haftungsausschluß", "haftungsausschluss"),
        ("Определения", "section"),
        # Step 2: NFKD, then combining marks removed.
        ("Záruka a odpovědnost", "zaruka-a-odpovednost"),
        # Step 3: the transliteration table, after normalization.
        ("Straße", "strasse"),
        ("Œuvre", "oeuvre"),
        ("Łódź", "lodz"),
        ("Þing", "thing"),
        ("ǿ", "o"),
        # Steps 6-9: separators, runs of hyphens, and the ends.
        ("A -- B", "a-b"),
        ("snake_case name", "snake-case-name"),
        ("Services — Scope", "services-scope"),
        ("  -Scope-  ", "scope"),
        # Steps 10-11: truncation, then no trailing hyphen.
        ("a" * 63 + " b", "a" * 63),
        # Step 13: the prefix is not re-truncated.
        ("1. Scope", "section-1-scope"),
        ("9" * 70, "section-" + "9" * 64),
    ],
)
def test_the_section_5_3_algorithm(text, identifier):
    assert slugify_identifier(text) == identifier


@pytest.mark.parametrize(
    ("text", "lossy"),
    [
        ("Определения", True),
        ("Scope 範囲", True),
        ("Smluvní pokuta", False),  # transliterates
        ("Haftungsausschluß", False),
        ("Services — “Scope”", False),  # punctuation only
    ],
)
def test_a_slug_is_lossy_when_a_letter_or_digit_is_dropped(text, lossy):
    assert generate_identifier(text)[1] is lossy


def _rules(body: str) -> list[str]:
    document = parse_document(f"---\ntitle: Fixture\n---\n\n{body}\n")
    return [d.rule for d in validate_document(document).diagnostics]


def test_a_heading_that_loses_letters_is_a_warning():
    assert "anchor-lossy-slug" in _rules("# Определения\n\nText.")
    assert "anchor-lossy-slug" not in _rules("# Определения {#definitions}\n\nText.")
    assert "anchor-lossy-slug" not in _rules("# Smluvní pokuta\n\nText.")


def test_a_defined_term_that_loses_letters_is_a_warning():
    assert "def-lossy-slug" in _rules('# A\n\n"Определения" {{def:}} means x.')
    assert "def-lossy-slug" not in _rules('# A\n\n"Определения" {{def: definitions}} means x.')


def test_a_comment_is_not_part_of_a_generated_identifier():
    assert generate_identifier("Scope <!-- Определения -->") == ("scope", False)
    rules = _rules('# A\n\n"Services <!-- Определения -->" {{def:}} means x. The {{term: services}}.')
    assert "def-lossy-slug" not in rules and "term-undefined" not in rules


def test_a_reference_to_a_transliterated_heading_resolves():
    assert "ref-broken" not in _rules("# Smluvní pokuta\n\nText.\n\n# B\n\nSee {{ref: smluvni-pokuta}}.")
