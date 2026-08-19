"""Specification 0.1 behavior: faithful parsing, code spans, and directives.

Complements tests/conformance (which runs the specification's own fixtures
corpus) with cases the corpus does not cover: the parser's refusal to repair
its input, and the §11.4 recognition contexts.
"""
from __future__ import annotations

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


# ── Faithful parsing ──────────────────────────────────────────────


def test_parser_does_not_silently_correct_an_invalid_duration_unit():
    """A bare unit=M must reach the validator, not be repaired on the way."""
    result = _validate("Within {{duration: 30, unit=M}}.")
    assert "duration-invalid-unit" in result.rules("error")


def test_parser_does_not_silently_correct_party_metadata():
    """Display names and unknown types survive the parse so §15.6 can report them.

    The party carrying the malformed name and the one carrying the bad type are
    deliberately separate: the validator stops checking a party once its name is
    rejected, so a single party would only ever report the name.
    """
    source = """---
title: Fixture
document_type: contract
sides:
  - name: Providing Party
    parties:
      - name: Acme Corporation Ltd.
        type: company
  - name: clients
    parties:
      - name: beta
        type: corporation
        legal_name: Beta Industries Inc.
---

# Scope {#scope}

Text.
"""
    document = parse_document(source, filename="t.lgd")
    side = document.metadata.sides[0]
    assert side.name == "Providing Party"           # preserved verbatim
    assert side.parties[0].type == "company"        # not mapped onto legal_entity

    rules = validate_document(document).rules("error")
    assert "side-party-name-format" in rules        # the display name
    assert "party-type-invalid" in rules            # the unknown type


def test_a_party_omitting_the_required_type_is_reported():
    """§3.4 makes `type` REQUIRED, so an absent one must not default to a value."""
    source = """---
title: Fixture
document_type: contract
sides:
  - name: providers
    parties:
      - name: acme
        legal_name: Acme Corporation
  - name: clients
    parties:
      - name: beta
        type: legal_entity
---

# Scope {#scope}

Text.
"""
    document = parse_document(source, filename="t.lgd")
    assert document.metadata.sides[0].parties[0].type == ""
    assert "party-type-invalid" in validate_document(document).rules("error")


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


# ── Inline value indices ──────────────────────────────────────────


def test_inline_value_indices_collect_field_spec_values():
    """ValidationResult exposes the values the checks indexed, for reuse."""
    result = _validate(
        "Rate {{field: 1.5%, type=percentage}} over {{duration: 30, unit=MIN}} "
        "from {{date: 2026-06-01}} for {{money: 5000, currency=EUR}}."
    )
    assert ("1.5%", "percentage") in result.inline_fields
    assert ("30", "MIN") in result.inline_durations
    assert "2026-06-01" in result.inline_dates
    assert ("5000", "EUR") in result.inline_money


# ── Attachments (§15.10) ──────────────────────────────────────────
#
# The corpus fixtures for these rules span several files, so the
# single-document conformance harness skips them; they are exercised here.

_ATTACHMENT_FRONTMATTER = """---
title: Fixture
document_type: contract
sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
  - name: clients
    parties:
      - name: beta
        type: legal_entity
attachments:
{attachments}
---

# Scope {{#scope}}

{body}
"""


def _validate_attachments(attachments: str, body: str = "Text."):
    source = _ATTACHMENT_FRONTMATTER.format(attachments=attachments, body=body)
    return validate_document(parse_document(source, filename="t.lgd"))


def test_duplicate_attachment_id_is_reported():
    result = _validate_attachments(
        "  - id: schedule-a\n    title: Schedule A\n    file: a.lgd\n"
        "  - id: schedule-a\n    title: Schedule A again\n    file: b.lgd",
        body="See {{attach: schedule-a}}.",
    )
    assert "attachment-id-duplicate" in result.rules("error")


def test_attachment_without_a_title_is_reported():
    result = _validate_attachments(
        "  - id: schedule-a\n    file: a.lgd",
        body="See {{attach: schedule-a}}.",
    )
    assert "attachment-title-empty" in result.rules("error")


def test_attachment_id_colliding_with_a_section_anchor_is_reported():
    """An attachment id and a section identifier share one namespace."""
    result = _validate_attachments(
        "  - id: scope\n    title: Scope Schedule\n    file: a.lgd",
        body="See {{attach: scope}}.",
    )
    assert "attachment-id-collision" in result.rules("error")


def test_declared_but_unreferenced_attachment_is_a_warning():
    result = _validate_attachments(
        "  - id: schedule-a\n    title: Schedule A\n    file: a.lgd"
    )
    assert "attachment-unreferenced" in result.rules("warning")


# ── Amendments (§15.8) ────────────────────────────────────────────


def test_amendment_redefining_an_imported_term_is_a_warning():
    """amend-def-override: the amendment redeclares a term the original defines."""
    source = """---
title: First Amendment
document_type: contract
amends:
  title: Services Agreement
  file: services-agreement.lgd
sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
  - name: clients
    parties:
      - name: beta
        type: legal_entity
---

# Definitions {#definitions}

"Services" {{def: services}} means the amended services.

# Scope {#scope}

The {{term: services}} are amended.
"""
    document = parse_document(source, filename="first-amendment.lgd")
    result = validate_document(
        document, import_definitions=lambda *_: {"services": "Services"}
    )
    assert "amend-def-override" in result.rules("warning")
