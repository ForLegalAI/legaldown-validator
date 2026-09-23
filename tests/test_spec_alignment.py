"""Specification 0.1 behavior: faithful parsing, code spans, and directives.

Complements tests/conformance (which runs the specification's own fixtures
corpus) with cases the corpus does not cover: the parser's refusal to repair
its input, and the §11.4 recognition contexts.
"""
from __future__ import annotations

import pytest

from legaldown import iter_directives, serialize_document
from legaldown.directives import format_value
from legaldown.parser import collect_source_directives, parse_document
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


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ("See {{term: nonexistent, colour=red}}.", "term-undefined"),
        ("See {{ref: no-such-section, colour=red}}.", "ref-broken"),
        ("On {{date: 2026-02-30, colour=red}}.", "date-invalid"),
        ("Pay {{money: -5, currency=USD, colour=red}}.", "money-invalid-amount"),
        ("For {{duration: 0, unit=D, colour=red}}.", "duration-invalid-value"),
        ("By {{party: nobody, colour=red}}.", "party-unknown"),
        ("By {{side: nobody, colour=red}}.", "side-unknown"),
        ("Ref {{field: x, colour=red}}.", "field-type-missing"),
        ("Rate {{placeholder: r, type=percentage, colour=red}}.", "placeholder-type-invalid"),
        ("Rate {{placeholder: r, label=Rate, type=percentage}}.", "placeholder-type-invalid"),
        ("See {{attach: nothing, colour=red}}.", "attach-undeclared"),
    ],
)
def test_unknown_parameter_is_a_warning_and_does_not_suppress_checks(body, rule):
    """§11.2: an unknown parameter is ignored, not the whole directive."""
    result = _validate(body)
    assert "directive-unknown-param" in result.rules("warning")
    assert rule in result.rules("error")


def test_unknown_parameter_alone_leaves_the_document_valid():
    result = _validate("On {{date: 2026-01-01, colour=red}}.")
    assert result.is_valid
    assert result.inline_dates == ["2026-01-01"]


@pytest.mark.parametrize(
    ("body", "rule", "level"),
    [
        ("Pay {{placeholder: x, currency=XXQ, type=money}}.", "placeholder-unknown-currency", "warning"),
        ("Pay {{placeholder: x, note=Fee, type=bogus}}.", "placeholder-type-invalid", "error"),
        ("Pay {{money: 5, note=Fee, currency=XXQ}}.", "money-unknown-currency", "warning"),
    ],
)
def test_named_parameters_are_order_insensitive(body, rule, level):
    """§11.2: a parameter after note= is neither swallowed nor lost."""
    result = _validate(body)
    assert rule in result.rules(level)
    assert "note-invalid" not in result.rules()


def test_parameters_in_any_order_are_valid():
    result = _validate(
        "Pay {{money: 5, note=Fee, currency=USD}} within "
        "{{duration: 5, note=Grace, unit=D}} to {{party: acme, note=Payee, label=Acme}} "
        "under {{field: A-1, note=Ref, type=case-id}}."
    )
    assert result.diagnostics == []
    assert ("5", "USD") in result.inline_money
    assert ("A-1", "case-id") in result.inline_fields


def test_quoted_values_are_decoded():
    """§11.3: a quoted value may hold commas and closing braces."""
    result = _validate(
        '{{field: "Smith, Jones v. Doe", type=case-name}} for '
        '{{party: acme, label="Acme, Inc."}} at {{money: 5, currency="USD", note="base, monthly"}} '
        'and {{field: "a}}b", type=code}}.'
    )
    assert result.diagnostics == []
    assert ("Smith, Jones v. Doe", "case-name") in result.inline_fields
    assert ("a}}b", "code") in result.inline_fields
    assert ("5", "USD") in result.inline_money


def test_quoted_values_are_still_checked():
    result = _validate('By {{party: nobody, label="Smith, Jones"}} on {{field: "a, b", type=Bad_Type}}.')
    assert {"party-unknown", "field-type-missing"} <= result.rules("error")


def test_duplicate_parameter_is_an_error():
    result = _validate('"Term" {{def: term}} means x. See {{term: term, label=A, label=B}}.')
    assert "directive-duplicate-param" in result.rules("error")


@pytest.mark.parametrize(
    "body",
    [
        "See {{ref: unterminated",
        "On {{date: 2026-01-01, oops}}.",
        "On {{date: note=x, 2026-01-01}}.",
        'By {{party: acme, label="Acme}}.',
        'By {{party: acme, label="Acme" Inc}}.',
    ],
)
def test_malformed_directive_is_an_error(body):
    assert "directive-malformed" in _validate(body).rules("error")


def test_parameters_on_ref_and_def_are_reported():
    """{{ref:}} and {{def:}} define no parameters. The parser lifts neither
    into block fields here, so the parameters stay in the text and survive
    a round trip."""
    source = (
        '"Foo" {{def: foo, colour=red}} means x.\n\n'
        "See {{ref: terms, format=long}} and {{term: foo}}."
    )
    result = _validate(source)
    assert [d.rule for d in result.diagnostics] == ["directive-unknown-param"] * 2
    assert result.definition_lookup["foo"] == "Foo"
    assert "{{ref: terms, format=long}}" in serialize_document(parse_document(_FRONTMATTER + source))


def test_directive_in_code_span_is_not_lifted_or_checked():
    """§11.4: the parser must not lift a code-span {{ref:}} into a ref block."""
    document = parse_document(_FRONTMATTER + "Write `{{ref: nope}}` or `{{term: nope}}`.")
    assert document.sections[0].blocks[0].kind == "paragraph"
    assert validate_document(document).diagnostics == []


def test_lifted_term_label_round_trips():
    source = _FRONTMATTER + '"Svc" {{def: svc}} x.\n\nSee {{term: svc, label="Services, as amended"}}.\n'
    block = parse_document(source).sections[0].blocks[1]
    assert (block.kind, block.target, block.label) == ("term", "svc", "Services, as amended")
    assert '{{term: svc, label="Services, as amended"}}' in serialize_document(parse_document(source))


def test_placeholder_currency_is_defined_only_for_money():
    """§13.5 placeholder rule 7: a type-specific parameter for another type."""
    result = _validate("Pay {{placeholder: x, type=text, currency=USD}}.")
    assert "directive-unknown-param" in result.rules("warning")
    assert result.is_valid


def test_frontmatter_placeholder_with_unknown_parameter_is_checked():
    source = _FRONTMATTER.replace(
        "title: Fixture", 'title: "{{placeholder: t, type=percentage, colour=red}}"'
    )
    result = validate_document(parse_document(source + "Text.\n", filename="t.lgd"))
    assert "placeholder-type-invalid" in result.rules("error")
    assert "directive-unknown-param" in result.rules("warning")


def test_collect_source_directives_sees_every_parameter_shape():
    document = parse_document(
        _FRONTMATTER + "See {{ref: a, colour=red}} and {{term: b, label=\"x, y\"}}.\n"
    )
    assert collect_source_directives(document) == ({"a"}, {"b"})


def test_explicitly_empty_label_round_trips():
    source = _FRONTMATTER + '"X" {{def: x}} y.\n\nSee {{term: x, label=""}}.\n'
    assert '{{term: x, label=""}}' in serialize_document(parse_document(source))


def test_malformed_directive_with_unknown_name_is_malformed():
    """§11.5 reserves directive-unknown for well-formed directives."""
    rules = _validate("See {{foo: bar").rules()
    assert "directive-malformed" in rules
    assert "directive-unknown" not in rules


def test_explicitly_empty_placeholder_type_is_invalid():
    assert "placeholder-type-invalid" in _validate("{{placeholder: p, type=}}").rules("error")


def test_missing_ref_and_term_targets_are_reported_as_missing():
    result = _validate("See {{ref:}} and {{term:}}.")
    messages = {d.rule: d.message for d in result.diagnostics}
    assert "has no target" in messages["ref-broken"]
    assert "has no target" in messages["term-undefined"]
    assert "" not in result.used_terms


@pytest.mark.parametrize(
    ("text", "reason", "source"),
    [
        ("{{money: 5, currency=USD,}}.", "empty argument", "{{money: 5, currency=USD,}}"),
        ('{{term: x, label="a"', "not closed", '{{term: x, label="a"'),
        ('{{term: x, label="a" b}}', "text after a quoted value", '{{term: x, label="a" b}}'),
        ("{{date: a, b}}", "more than one positional value", "{{date: a, b}}"),
    ],
)
def test_malformed_directive_reason_and_source(text, reason, source):
    (directive,) = iter_directives(text)
    assert reason in directive.malformed
    assert directive.source == source


def test_escaped_opener_is_literal_text():
    """§11.4: \\{{ is a literal brace; \\\\{{ is a literal backslash, then a directive."""
    escaped = _validate(r"Literal \{{ref: nope}} here.")
    assert escaped.diagnostics == []
    assert "ref-broken" in _validate(r"Path C:\\{{ref: nope}}.").rules("error")


def test_definition_in_code_span_or_comment_is_not_registered():
    """§11.4: a {{def:}} in literal text neither defines a term nor is checked."""
    result = _validate(
        'Example: `"Foo" {{def: foo}}` shows syntax. <!-- {{def:}} --> Use {{term: foo}}.'
    )
    assert "term-undefined" in result.rules("error")
    assert "def-no-quoted-span" not in result.rules()


def test_def_opener_with_inner_whitespace_is_not_a_definition():
    """§11.2: no whitespace between {{ and the directive name."""
    result = _validate('"Foo" {{ def: foo}} means x. Use {{term: foo}}.')
    assert "foo" not in result.definition_lookup


def test_defined_term_is_the_nearest_quoted_span():
    """§7.2: scan back from the closing mark to the nearest opening mark."""
    result = _validate('Between "A" and "B" {{def: b}} use {{term: b}}.')
    assert result.definition_lookup["b"] == "B"


def test_quoted_value_may_contain_backticks():
    """§11.3: once a directive opens, its value is lexed as written."""
    result = _validate('Case {{field: "a`b", type=code}} and `c` here.')
    assert result.diagnostics == []
    assert ("a`b", "code") in result.inline_fields


def test_lifted_values_are_kept_as_written():
    source = _FRONTMATTER + '"X" {{def: x}} y.\n\nSee {{term: x, label="a `b` c"}}.\n'
    assert parse_document(source).sections[0].blocks[1].label == "a `b` c"
    reparsed = parse_document(serialize_document(parse_document(source)))
    assert reparsed.sections[0].blocks[1].label == "a `b` c"


def test_code_in_a_note_is_markdown():
    assert "note-invalid" in _validate("On {{date: 2026-01-01, note=see `x` here}}.").rules("error")


def test_text_inside_a_malformed_directive_is_not_lexed():
    rules = _validate('Pay {{money: "5, {{date: 2026-13-01}} now.').rules()
    assert "directive-malformed" in rules
    assert "date-invalid" not in rules


@pytest.mark.parametrize(
    "paragraph",
    ['“The "Best" Co” {{def: best}} means x.', "„Käufer“ {{def: best}} means x."],
)
def test_definition_with_other_quotation_marks_round_trips(paragraph):
    """The serializer writes straight quotes, so other delimiters stay text."""
    source = _FRONTMATTER + paragraph + "\n"
    assert paragraph in serialize_document(parse_document(source))


def test_ref_inside_a_defined_term_is_not_lifted():
    result = _validate('The "Buyer {{ref: terms}} Co" {{def: buyer}} means x. {{term: buyer}}')
    assert result.diagnostics == []


def test_bold_italic_defined_term_is_recognized():
    result = _validate('***"Buyer"*** {{def: buyer}} means x. {{term: buyer}}')
    assert result.rules() == {"def-emphasis"}


def test_placeholder_currency_is_unknown_whatever_the_type():
    for body in ("{{placeholder: x, currency=USD}}", "{{placeholder: x, type=bogus, currency=USD}}"):
        assert "directive-unknown-param" in _validate(body).rules("warning")


def test_directive_is_unhashable_like_other_models():
    (directive,) = iter_directives("{{ref: x}}")
    with pytest.raises(TypeError):
        hash(directive)


def test_definition_after_a_backtick_in_a_quoted_value_is_registered():
    """The defined-term scan sees literal regions exactly as the lexer does."""
    result = _validate(
        'Pay {{field: "a`b", type=code}} where "Fee" {{def: fee}} per `x` rule. '
        "The {{term: fee}} applies."
    )
    assert result.diagnostics == []


def test_unclosed_directive_does_not_swallow_the_next():
    """§11.4 opener commitment: a new opener ends an unclosed unquoted value."""
    result = _validate("See {{ref: s and {{term: undefined-thing}} here.")
    assert {"directive-malformed", "term-undefined"} <= result.rules("error")
    assert "ref-broken" not in result.rules()


@pytest.mark.parametrize("paragraph", ['"" {{def: foo}} means x.', '" Foo " {{def: foo}} means x.'])
def test_definition_the_block_cannot_hold_stays_text(paragraph):
    source = _FRONTMATTER + paragraph + "\n"
    assert paragraph in serialize_document(parse_document(source))


def test_escaped_placeholder_in_metadata_is_not_a_placeholder():
    source = _FRONTMATTER.replace(
        "title: Fixture", 'title: Fixture\neffective_date: "\\\\{{placeholder: d}} soon"'
    )
    result = validate_document(parse_document(source + "Text.\n"))
    assert "metadata-date-invalid" in result.rules("error")


def test_comment_opener_inside_code_span_is_code():
    result = _validate("Use `<!--` to open. {{ref: nope}} and `-->` closes.")
    assert "ref-broken" in result.rules("error")


def test_format_value_rejects_line_breaks_and_quotes_openers():
    with pytest.raises(ValueError):
        format_value("a\nb")
    (directive,) = iter_directives(f"{{{{term: x, label={format_value('see {{ref: y}}')}}}}}")
    assert directive.params["label"] == "see {{ref: y}}"


def test_underscore_emphasis_on_a_defined_term_is_a_warning():
    result = _validate('_"Term"_ {{def: t}} means x. {{term: t}}')
    assert result.rules() == {"def-emphasis"}


def test_iter_directives_decodes_escapes():
    (directive,) = iter_directives(r'{{field: "say \"hi\" C:\path\\", type=t}}')
    assert directive.positional == 'say "hi" C:\\path\\'
    assert directive.params == {"type": "t"}


@pytest.mark.parametrize(
    "value", ["plain", "a, b", "a}}b", "ends}", '"quoted"', " padded ", "C:\\dir", "type=x", ""]
)
def test_format_value_is_the_inverse_of_the_lexer(value):
    source = f"{{{{field: {format_value(value, positional=True)}, type=t}}}}"
    (directive,) = iter_directives(source)
    assert directive.positional == value
    assert not directive.malformed


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
