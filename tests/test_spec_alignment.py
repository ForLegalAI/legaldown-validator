"""Specification 0.1 behavior: legacy migration, code spans, and directives.

Complements tests/conformance (which runs the specification's own fixtures
corpus) with cases the corpus does not cover: the pre-0.1 migration helpers
and the §11.4 recognition contexts.
"""
from __future__ import annotations

from legaldown.definitions import migrate_legacy_directives
from legaldown.parser import parse_document
from legaldown.validator import validate_document

_FRONTMATTER = """---
title: Fixture
document_type: contract
sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
        legal_name: Acme Corporation
  - name: clients
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Industries Inc.
---

# Terms {#terms}

"""


def _validate(body: str):
    return validate_document(parse_document(_FRONTMATTER + body, filename="t.lgd"))


# ── Legacy directive migration ────────────────────────────────────


def test_legacy_pct_migrates_to_field():
    assert migrate_legacy_directives("Rate {{pct: 1.5}}.") == (
        "Rate {{field: 1.5%, type=percentage}}."
    )


def test_legacy_pct_keeps_note_and_existing_sign():
    assert migrate_legacy_directives("{{pct: 5%, note=annual}}") == (
        "{{field: 5%, type=percentage, note=annual}}"
    )


def test_legacy_duration_unit_m_becomes_min():
    assert migrate_legacy_directives("{{duration: 30, unit=M}}") == (
        "{{duration: 30, unit=MIN}}"
    )
    # MO must not be touched by the M rewrite.
    assert migrate_legacy_directives("{{duration: 3, unit=MO}}") == (
        "{{duration: 3, unit=MO}}"
    )


def test_migration_leaves_code_regions_untouched():
    source = "Live {{pct: 2}} and `{{pct: 9}}` plus\n\n```\n{{duration: 1, unit=M}}\n```\n"
    migrated = migrate_legacy_directives(source)
    assert "{{field: 2%, type=percentage}}" in migrated
    assert "`{{pct: 9}}`" in migrated          # inline code span preserved
    assert "{{duration: 1, unit=M}}\n```" in migrated  # fenced block preserved


def test_legacy_document_still_validates_after_migration():
    """A pre-0.1 stored document must not become unsaveable.

    The migration is an explicit, opt-in helper — applications call it at
    their own storage boundary when loading stored content. The parser never
    applies it implicitly, so it is applied explicitly here too.
    """
    body = "Rate {{pct: 1.5}} over {{duration: 30, unit=M}}."
    source = migrate_legacy_directives(_FRONTMATTER + body)
    result = validate_document(parse_document(source, filename="t.lgd"))
    assert result.errors == []
    assert ("30", "MIN") in result.inline_durations
    assert ("1.5%", "percentage") in result.inline_fields


def test_parser_does_not_silently_migrate_legacy_spellings():
    """The parser stays faithful: a bare unit=M must reach the validator."""
    result = _validate("Within {{duration: 30, unit=M}}.")
    assert "duration-invalid-unit" in result.rules("error")


# ── §11.4 recognition contexts ────────────────────────────────────


def test_directive_like_text_in_code_span_is_not_a_diagnostic():
    result = _validate("Write `{{nope: x}}` and `{#not-an-anchor}` literally.")
    assert result.errors == []


# ── Directive vocabulary ──────────────────────────────────────────


def test_unknown_directive_is_an_error():
    result = _validate("A {{trem: services}} typo.")
    assert "directive-unknown" in result.rules("error")


def test_side_directive_resolves_and_reports_unknown():
    ok = _validate("The {{side: clients}} shall pay.")
    assert "side-unknown" not in ok.rules()
    assert ok.side_lookup["clients"] == "Clients"

    bad = _validate("The {{side: nobody}} shall pay.")
    assert "side-unknown" in bad.rules("error")


def test_duration_unit_m_is_rejected_with_hint():
    # Written directly in a directive the migration does not reach (already
    # canonical spelling context), the bare unit M is an error.
    result = validate_document(
        parse_document(
            _FRONTMATTER + "Within {{duration: 5, unit=Q}}.", filename="t.lgd"
        )
    )
    assert "duration-invalid-unit" in result.rules("error")


def test_negative_money_amount_is_rejected():
    result = _validate("Credit of {{money: -50, currency=USD}}.")
    assert "money-invalid-amount" in result.rules("error")


def test_ref_to_attachment_id_suggests_attach():
    body = "See Section {{ref: schedule-a}}."
    source = _FRONTMATTER.replace(
        "document_type: contract",
        "document_type: contract\nattachments:\n"
        "  - id: schedule-a\n    title: Schedule A\n    file: schedule-a.pdf",
    )
    result = validate_document(parse_document(source + body, filename="t.lgd"))
    assert "ref-targets-attachment" in result.rules("error")


def test_item_anchor_is_a_valid_ref_target():
    body = "- First item {#first-item}\n\nAs in Section {{ref: first-item}}.\n"
    result = _validate(body)
    assert "ref-broken" not in result.rules()


# ── Diagnostics carry stable rule ids (§15.1/§15.9) ───────────────


def test_diagnostics_expose_rule_ids_and_levels():
    result = _validate("Broken Section {{ref: nowhere}}.")
    broken = [d for d in result.diagnostics if d.rule == "ref-broken"]
    assert broken and broken[0].level == "error"
    # The plain message lists stay in sync for existing callers.
    assert len(result.errors) == len([d for d in result.diagnostics if d.level == "error"])


# ── Index alignment and amendment severity ────────────────────────


def test_out_of_range_heading_keeps_its_index_entry():
    """result.sections stays positionally paired with document.sections.

    Consumers zip the two; dropping an entry for an invalid heading level
    shifts every later section's number and loses the last one entirely.
    """
    body = "# One {#one}\n\nText.\n\n###### Deep {#deep}\n\nText.\n\n# Two {#two}\n\nText.\n"
    source = _FRONTMATTER.replace("# Terms {#terms}\n\n", "") + body
    document = parse_document(source, filename="t.lgd")
    result = validate_document(document)
    assert "heading-depth" in result.rules("error")
    assert len(result.sections) == len(document.sections)
    assert [e.title for e in result.sections] == [s.title for s in document.sections]


def test_amend_term_is_an_error_when_original_defines_nothing():
    """A consulted original with zero definitions still yields an Error."""
    source = _FRONTMATTER.replace(
        "document_type: contract",
        "document_type: contract\namends:\n  title: Original\n  file: original.lgd",
    ) + "Uses {{term: missing}}.\n"
    document = parse_document(source, filename="amendment.lgd")
    result = validate_document(document, import_definitions=lambda *_: {})
    assert "amend-term-undefined" in result.rules("error")
    assert "amend-term-unresolvable" not in result.rules()


# ── Legacy metadata repair ────────────────────────────────────────


def test_repair_legacy_metadata_normalizes_names_and_types():
    from legaldown import repair_legacy_metadata

    source = """---
title: Legacy
document_type: contract
sides:
  - name: Providing Party
    parties:
      - name: Acme Corporation Ltd.
        type: company
  - name: clients
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Industries Inc.
---

# Scope {#scope}

Text.
"""
    document = parse_document(source, filename="legacy.lgd")
    # Faithful parse: the violations are visible to the validator.
    assert "side-party-name-format" in validate_document(document).rules("error")

    repair_legacy_metadata(document)
    side = document.metadata.sides[0]
    assert side.name == "providing-party"
    assert side.label == "Providing Party"       # display text preserved
    party = side.parties[0]
    assert party.name == "acme-corporation-ltd"
    assert party.legal_name == "Acme Corporation Ltd."
    assert party.type == "legal_entity"          # "company" mapped
    repaired = validate_document(document)
    assert "side-party-name-format" not in repaired.rules()
    assert "party-type-invalid" not in repaired.rules()
