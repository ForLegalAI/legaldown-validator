"""Specification 0.1 behavior: faithful parsing, code spans, and directives.

Complements tests/conformance (which runs the specification's own fixtures
corpus) with cases the corpus does not cover: the parser's refusal to repair
its input, and the §11.4 recognition contexts.
"""
from __future__ import annotations

import random

import pytest
import yaml

from legaldown import (
    Block,
    collect_definitions,
    document_from_dict,
    document_to_dict,
    iter_directives,
    serialize_document,
)
from legaldown.directives import format_value, lex
from legaldown.markers import Marker, split_heading
from legaldown.parser import collect_source_directives, parse_document
from legaldown.validator import validate_document
from legaldown.validator.helpers import generate_identifier

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
    """Display names and unknown types survive the parse so §16.6 can report them.

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


@pytest.mark.parametrize(
    "text",
    [
        '"Fee" {{def: fee}} x. See {{term: fee, label=“Curly”}}.',  # the spec fixture
        '"Fee" {{def: fee}} x. See {{term: “fee”}}.',
        '"Fee" {{def: fee}} x. See {{term: fee, label=  „Low“}}.',
        '"Fee" {{def: fee}} x. See {{term: fee, label=«Guillemets»}}.',
        "Pay {{money: 5, currency=EUR, note=”closing”}}.",
        "Pay {{money: 5, currency=EUR, note=“a”, note=b}}.",
    ],
)
def test_an_unquoted_value_beginning_with_a_curly_quote_is_a_warning(text):
    result = _validate(text)
    assert [d.level for d in result.diagnostics if d.rule == "value-curly-quote"] == ["warning"]


@pytest.mark.parametrize(
    "text",
    [
        '"Fee" {{def: fee}} x. See {{term: fee, label="“Curly”"}}.',  # quoted
        '"Fee" {{def: fee}} x. See {{term: fee, label=x“y”}}.',  # not at the start
        '"Fee" {{def: fee}} x. See {{term: fee, label=’s-Hertogenbosch}}.',  # a single mark
        '"Fee" {{def: fee}} x. See {{term: fee, label=}}.',  # empty
        "See {{term: fee, label=“x, y”}} in {{ref: terms}}.",  # malformed: positional after named
    ],
)
def test_other_values_are_not_curly_quote_warnings(text):
    assert "value-curly-quote" not in _validate(text).rules()


def test_a_curly_quoted_reference_stays_paragraph_text():
    """A lifted {{ref:}} or {{term:}} is not lexed again, so a directive the
    validator warns about is not lifted."""
    document = parse_document(_FRONTMATTER + "See {{ref: “terms”}}.\n")
    assert document.sections[0].blocks[0].kind == "paragraph"
    assert parse_document(serialize_document(document)).sections == document.sections
    assert "value-curly-quote" in validate_document(document).rules("warning")


@pytest.mark.parametrize(
    "text",
    ['"Fee" {{def: fee}} x.\n\nSee {{term: fee, label="“Curly”"}}.', 'See {{ref: "“terms”"}}.', '"Fee" {{def: "«fee»"}} x.'],
)
def test_a_quoted_curly_value_stays_quoted_through_a_round_trip(text):
    document = parse_document(_FRONTMATTER + text + "\n")
    reparsed = parse_document(serialize_document(document))
    assert reparsed.sections == document.sections
    assert "value-curly-quote" not in validate_document(reparsed).rules()


def test_a_curly_quote_in_a_frontmatter_placeholder_is_a_warning():
    source = _FRONTMATTER.replace("title: Fixture", "title: '{{placeholder: t, note=“x”}}'")
    assert "value-curly-quote" in validate_document(parse_document(source + "Text.\n")).rules("warning")


def test_the_lexer_records_which_values_were_unquoted():
    (directive,) = lex('{{term: a, label="b", note= c , label=d, x=}}').directives
    assert directive.unquoted == (("", "a"), ("note", "c"), ("label", "d"))


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


@pytest.mark.parametrize(
    "body",
    [
        'Pay {{money: "5, {{date: 2026-13-01}} now.',
        'By {{party: acme, label="Acme" Inc and {{date: 2026-13-01}} end.',
    ],
)
def test_malformed_directive_ends_at_the_next_opener(body):
    """§11.4 opener commitment: the next {{name: begins a directive of its
    own, so it is checked even when the one before it is malformed."""
    assert {"directive-malformed", "date-invalid"} <= _validate(body).rules("error")


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


@pytest.mark.parametrize("comment", ["<!-->", "<!--->"])
def test_an_empty_comment_ends_at_its_own_closing_bracket(comment):
    """CommonMark 0.31: ``<!-->`` and ``<!--->`` are complete comments, not
    openers that run on to a later ``-->``."""
    lexed = lex(f"Text {comment} {{{{party: x}}}} more --> end")
    assert [d.source for d in lexed.directives] == ["{{party: x}}"]
    assert lexed.view.startswith("Text " + " " * len(comment) + " {{party: x}}")
    assert "ref-broken" in _validate(f"First. {comment}{{{{ref: nowhere}}}} shown -->").rules("error")


@pytest.mark.parametrize(
    "text",
    ["a <!----> {{ref: nope}}", "a <!-- x -- y --> {{ref: nope}}", "a <!--><!--> {{ref: nope}}", "a <!-- {{ref: nope}}"],
)
def test_comments_around_the_empty_forms_keep_their_extent(text):
    assert [d.source for d in lex(text).directives] == ["{{ref: nope}}"]


def test_an_empty_comment_in_a_quoted_value_is_part_of_the_value():
    (directive,) = lex('{{term: x, label="<!-->"}} and <!-- {{ref: y}} -->').directives
    assert directive.params["label"] == "<!-->"


def test_an_empty_comment_after_a_heading_marker_keeps_the_marker():
    assert split_heading("Scope {#scope} <!-->") == ("Scope <!-->", Marker("scope"))
    assert generate_identifier("Scope <!--> Terms") == ("scope-terms", False)


def test_text_after_an_empty_comment_is_not_a_comment():
    # The marker is followed by text, so it is not in an anchor position.
    rules = _validate("Deliver. {#delivery} <!--> tail -->\n\nSee {{ref: delivery}}.").rules()
    assert {"anchor-misplaced", "ref-broken"} <= rules
    # Text after the include, so the paragraph is not include-only (§12.2)
    # and its anchor is kept.
    assert _validate("{{include: parts/a.lgd}} <!--> more --> {#part-a}\n\nSee {{ref: part-a}}.").rules() == set()


def test_format_value_rejects_line_breaks_and_quotes_openers():
    with pytest.raises(ValueError):
        format_value("a\nb")
    (directive,) = iter_directives(f"{{{{term: x, label={format_value('see {{ref: y}}')}}}}}")
    assert directive.params["label"] == "see {{ref: y}}"


def test_underscore_emphasis_on_a_defined_term_is_a_warning():
    result = _validate('_"Term"_ {{def: t}} means x. {{term: t}}')
    assert result.rules() == {"def-emphasis"}


@pytest.mark.parametrize(
    "body",
    ["```x``` {{ref: nope}} `y`.", r"Use \` here {{ref: nope}} and \` there."],
)
def test_code_spans_follow_commonmark(body):
    """A triple-backtick span closes on three backticks; an escaped backtick
    opens nothing — either way the directive between is live."""
    assert "ref-broken" in _validate(body).rules("error")


def test_no_break_space_may_separate_term_and_def():
    result = _validate("« Services »\u00a0{{def: services}} means x. {{term: services}}")
    assert result.diagnostics == []


def test_underscore_inside_a_word_is_not_emphasis():
    result = _validate('snake_"Term" {{def: t}} means x. {{term: t}}')
    assert "def-emphasis" not in result.rules()


@pytest.mark.parametrize("body", ["A stray {{ opener.", '"Term" {{ def: term}} means x.'])
def test_stray_double_brace_is_a_warning(body):
    """§11.4: literal {{ is usually a typo, e.g. a space before the name."""
    assert "brace-stray" in _validate(body).rules("warning")


def test_escaped_or_directive_braces_are_not_stray():
    result = _validate(r'Write \{{ literally, or {{term: t, label="{{x"}}. "T" {{def: t}}')
    assert "brace-stray" not in result.rules()


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


# ── Diagnostics carry stable rule ids (§16.1/§16.9) ───────────────


def test_diagnostics_expose_rule_ids_and_levels():
    result = _validate("Broken Section {{ref: nowhere}}.")
    broken = [d for d in result.diagnostics if d.rule == "ref-broken"]
    assert broken and broken[0].level == "error"
    # The plain message lists stay in sync for existing callers.
    assert len(result.errors) == len([d for d in result.diagnostics if d.level == "error"])


# ── Index alignment and amendment severity ────────────────────────


def _numbers(headings: str) -> list[str]:
    """The section numbers of a document with *headings*; one that opens
    below level 1 is also a heading-skip, numbered all the same."""
    source = _FRONTMATTER.replace("# Terms {#terms}\n\n", "") + headings
    return [entry.number for entry in validate_document(parse_document(source)).sections]


@pytest.mark.parametrize(
    ("headings", "numbers"),
    [
        ("# A\n\n# B\n\n# Dispute\n\n### Deep\n\n## Sub Two\n", ["1", "2", "3", "3.1.1", "3.2"]),  # #38
        ("# A\n\n#### D\n\n## B\n\n### C\n", ["1", "1.1.1.1", "1.2", "1.2.1"]),
        ("### X\n\n# A\n", ["1.1.1", "2"]),  # a first heading deeper than a later one
        ("## A\n\n## B\n\n# C\n", ["1.1", "1.2", "2"]),
        ("###### Six\n\n# A\n", ["1.1.1.1.1", "2"]),  # level 6 is clamped to 5
    ],
)
def test_a_skipped_level_counts_as_its_first_so_numbers_stay_unique(headings, numbers):
    assert _numbers(headings) == numbers


def test_a_fragment_starting_at_level_two_is_numbered_from_one():
    """An attachment or include file has neither frontmatter nor a # heading
    (§12, §13.8)."""
    result = validate_document(parse_document("## A\n\n## B\n\n### B1\n\n## C\n"))
    assert [entry.number for entry in result.sections] == ["1", "2", "2.1", "3"]
    assert "heading-skip" not in result.rules()


@pytest.mark.parametrize(
    ("body", "level"),
    [("## A\n\nText.\n", 2), ("### A\n\nText.\n", 3), ("Preamble.\n\n## A\n\nText.\n", 2), ("## A\n\n# B\n", 2)],
)
def test_a_main_document_opening_below_level_one_skips_a_level(body, level):
    """§4.1: a document with frontmatter is a main document, whose first
    heading is at level 1."""
    result = validate_document(parse_document(_BARE + body))
    assert [d.message for d in result.diagnostics if d.rule == "heading-skip"] == [
        f"Heading levels must not skip. 'A' is at level {level}, but the document has no level-1 heading before it."
    ]


@pytest.mark.parametrize(
    ("source", "skips"),
    [
        ("---\n---\n\n## A\n", True),  # empty frontmatter is still frontmatter
        ("## A\n\n#### B\n", True),  # a fragment's own skips are still reported
        ("## A\n\n### B\n\n## C\n", False),
        ("---\ntitle: T\n---\n\n# A {#a when=x}\n\n## B\n", False),  # B goes with A (§15.3)
    ],
)
def test_where_a_first_heading_below_level_one_is_a_skip(source, skips):
    assert ("heading-skip" in validate_document(parse_document(source)).rules()) is skips


def test_numbers_are_unique_and_unchanged_without_a_skip():
    """Random heading sequences: every number is unique, and a document
    that skips no level and opens at its shallowest level is numbered as
    before (dotted counters, from that level)."""
    rng = random.Random(38)
    for _ in range(1000):
        levels = [rng.randint(1, 5) for _ in range(rng.randint(1, 9))]
        numbers = _numbers("".join(f"{'#' * level} H{index}\n\n" for index, level in enumerate(levels)))
        assert len(set(numbers)) == len(numbers), (levels, numbers)
        skips = any(b - a > 1 for a, b in zip(levels, levels[1:], strict=False))
        if not skips and levels[0] == min(levels):
            counters = [0] * 6
            expected = []
            for level in levels:
                counters[level] += 1
                counters[level + 1:] = [0] * (5 - level)
                expected.append(".".join(str(c) for c in counters[levels[0]:level + 1]))
            assert numbers == expected, (levels, numbers)


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


# ── Attachments (§16.10) ──────────────────────────────────────────
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


# ── Amendments (§16.8) ────────────────────────────────────────────


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


# ── Preamble (§4.4) ───────────────────────────────────────────────

_PREAMBLE_SOURCE = _FRONTMATTER.replace("# Terms {#terms}\n\n", "") + (
    'This Agreement (this "Agreement" {{def: agreement}}) is entered into\n'
    "between {{party: acme}} and {{party: beta}}.\n"
    "\n"
    "# Confidentiality {#confidentiality}\n"
    "\n"
    "The {{term: agreement}} binds both sides.\n"
)


def test_preamble_is_kept_out_of_the_numbered_sections():
    document = parse_document(_PREAMBLE_SOURCE)
    assert [s.identifier for s in document.sections] == ["confidentiality"]
    assert "entered into between" in document.preamble[0].text


def test_definition_in_the_preamble_is_document_wide():
    result = validate_document(parse_document(_PREAMBLE_SOURCE))
    assert result.definition_lookup == {"agreement": "Agreement"}
    assert result.diagnostics == []


def test_directives_in_the_preamble_are_checked():
    source = _PREAMBLE_SOURCE.replace("{{party: beta}}", "{{party: nobody}} on {{date: 2026-02-30}}")
    assert {"party-unknown", "date-invalid"} <= validate_document(parse_document(source)).rules("error")


def test_preamble_round_trips():
    document = parse_document(_PREAMBLE_SOURCE)
    reparsed = parse_document(serialize_document(document))
    assert reparsed.preamble == document.preamble
    assert reparsed.sections == document.sections


def test_document_of_only_a_preamble():
    source = _FRONTMATTER.replace("# Terms {#terms}\n\n", "") + "By {{party: nobody}}.\n"
    document = parse_document(source)
    assert document.sections == [] and len(document.preamble) == 1
    assert "party-unknown" in validate_document(document).rules("error")


@pytest.mark.parametrize("marker", ["{#intro}", "{#Bad_ID}"])
def test_preamble_paragraph_anchor_is_misplaced(marker):
    """§4.4: preamble paragraphs cannot carry anchors or be referenced."""
    source = _PREAMBLE_SOURCE.replace("{{party: beta}}.", "{{party: beta}}. " + marker) + (
        "See {{ref: intro}}.\n"
    )
    result = validate_document(parse_document(source))
    assert "anchor-misplaced" in result.rules("warning")
    assert "ref-broken" in result.rules("error")
    assert "anchor-format" not in result.rules()


def test_preamble_survives_the_dict_round_trip():
    document = parse_document(_PREAMBLE_SOURCE)
    assert document_from_dict(document_to_dict(document)) == document
    ref = next(r for r in collect_definitions(document) if r.id == "agreement")
    assert ref.section_index is None


# ── Item and paragraph anchor positions (§5.7) ────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "A marker {#stray} mid-paragraph.",
        "> Quoted text {#quoted}",
        "| A | B |\n|---|---|\n| cell {#cell} | x |",
    ],
)
def test_marker_outside_an_anchor_position_is_literal(body):
    marker = body.split("{#")[1].split("}")[0]
    result = _validate(body + f"\n\nSee {{{{ref: {marker}}}}}.")
    assert "anchor-misplaced" in result.rules("warning")
    assert "ref-broken" in result.rules("error")


@pytest.mark.parametrize(
    "body",
    [
        "A top-level paragraph. {#para}",
        "- a list item {#para}",
        "See {{ref: terms}} for more. {#para}",
    ],
)
def test_anchor_positions_are_ref_targets(body):
    result = _validate(body + "\n\nBack to {{ref: para}}.")
    assert result.diagnostics == []


def test_marker_inside_a_directive_value_is_not_an_anchor():
    result = _validate('Case {{field: "{#x}", type=code}} here.')
    assert result.diagnostics == []


def test_anchor_scan_agrees_with_the_lexer_about_code_spans():
    """A backtick inside a directive value does not open a code span."""
    result = _validate('P {{field: "a`b", type=code}} mid {#m} then `c` end.')
    assert "anchor-misplaced" in result.rules("warning")


def test_table_header_cells_are_body_text():
    result = _validate("| {{party: nobody}} {#h} | x |\n|---|---|\n| a | b |")
    assert "party-unknown" in result.rules("error")
    assert "anchor-misplaced" in result.rules("warning")


def test_escaped_anchor_marker_is_literal():
    result = _validate(r"Para \{#b} mid and end \{#c}" + "\n\nSee {{ref: c}}.")
    assert "anchor-misplaced" not in result.rules()
    assert "ref-broken" in result.rules("error")


def test_paragraph_anchor_colliding_with_an_attachment_id_is_reported():
    result = _validate_attachments(
        "  - id: sched\n    title: Schedule\n    file: s.pdf",
        "End {#sched}\n\nSee {{attach: sched}}.",
    )
    assert "attachment-id-collision" in result.rules("error")


@pytest.mark.parametrize(
    "source",
    [
        "---\n---\n# A {#a}\n\nText.\n",
        "---\n---\n# A {#a}\n\nText.\n\n---\n\n## B {#b}\n",
        "\ufeff---\ntitle: T\n---\n# A {#a}\n\nText.\n",
        "\ufeff# A {#a}\n\nText.\n",
    ],
)
def test_empty_frontmatter_and_byte_order_mark_are_not_body(source):
    """Empty frontmatter stops at its own closing ---, not at a later rule,
    and a byte-order mark never hides the first heading."""
    document = parse_document(source)
    assert document.preamble == []
    assert document.sections[0].identifier == "a"


# ── Frontmatter presence (§3.1, §3.2, §16.6) ──────────────────────


def _presence(source: str) -> tuple[bool, list[str], list[str], set[str]]:
    """Whether the frontmatter is absent, the preamble's block kinds, the
    section titles, and the rules reported."""
    document = parse_document(source)
    return (
        document.metadata.frontmatter_absent,
        [b.kind for b in document.preamble],
        [s.title for s in document.sections],
        validate_document(document).rules(),
    )


def test_a_document_without_frontmatter_draws_only_the_warning():
    """§16.6: not title-missing, not sides-absent."""
    assert _presence("# Scope {#scope}\n\nBody.\n") == (True, [], ["Scope"], {"frontmatter-absent"})
    result = validate_document(parse_document("# Scope\n\nBody.\n"))
    assert [d.level for d in result.diagnostics] == ["warning"]


def test_unclosed_frontmatter_is_absent_and_its_lines_are_body():
    absent, preamble, titles, rules = _presence("---\ntitle: T\n# A\n\nText {{ref: nope}}.\n")
    assert (absent, preamble, titles) == (True, ["rule", "paragraph"], ["A"])
    assert rules == {"frontmatter-absent", "ref-broken"}


@pytest.mark.parametrize(
    "block",
    ["\nThis Agreement is made today.\n", "just text", "- a\n- b", "'quoted'", "42"],
)
def test_a_block_whose_yaml_is_not_a_mapping_is_body(block):
    """A body that opens with a thematic break: everything is validated as
    body, the text directly above the second "---" as a setext heading."""
    absent, preamble, titles, rules = _presence(f"---\n{block}\n---\n\n# Terms\n\nSee {{{{ref: nope}}}}.\n")
    assert (absent, preamble[0], titles[-1]) == (True, "rule", "Terms")
    assert {"frontmatter-absent", "ref-broken"} <= rules
    assert not {"title-missing", "sides-absent"} & rules


@pytest.mark.parametrize("block", ["---\n---\n", "---\n# just a comment\n---\n", "---\n\n---\n"])
def test_empty_frontmatter_is_present(block):
    absent, _preamble, titles, rules = _presence(block + "\n# A\n\nText.\n")
    assert (absent, titles) == (False, ["A"])
    assert {"title-missing", "sides-absent"} <= rules and "frontmatter-absent" not in rules


def test_invalid_yaml_is_still_an_error():
    with pytest.raises(yaml.YAMLError):
        parse_document("---\ntitle: [unclosed\n---\n# A\n")


def test_a_mapping_of_another_type_is_not_frontmatter_fields():
    with pytest.raises(ValueError, match="mapping of fields"):
        parse_document("---\n!!set {title: T}\n---\n# A\n")


@pytest.mark.parametrize(
    "source",
    [
        "# Scope {#scope}\n\nBody.\n",
        "---\n\nThis Agreement.\n\n---\n# Terms\n\nx\n",
        "---\ntitle: T\n# A\n\nText.\n",
        "***\n\nNote: see below\n\n***\n\n# A\n",
    ],
)
def test_a_document_without_frontmatter_is_written_without_it(source):
    document = parse_document(source)
    written = serialize_document(document)
    assert not written.startswith("---")
    assert parse_document(written) == document
    assert validate_document(parse_document(written)).rules() <= {"frontmatter-absent"}


def test_metadata_set_on_a_bare_document_is_written_as_frontmatter():
    document = parse_document("# A\n\nText.\n")
    document.metadata.title = "Terms"
    assert serialize_document(document).startswith("---\ntitle: Terms\n")


def test_frontmatter_absent_is_not_read_from_frontmatter_or_a_dict():
    document = parse_document("---\ntitle: T\nfrontmatter_absent: true\n---\n\n# A\n")
    assert document.metadata.frontmatter_absent is False
    assert document_from_dict(document_to_dict(parse_document("# A\n"))).metadata.frontmatter_absent is False
    assert "frontmatter-absent" not in validate_document(document_from_dict({"sections": []})).rules()


def test_only_a_comment_may_follow_an_anchor():
    """A comment is not rendered (§8.6), so an anchor before one is still at
    the end of its paragraph; a code span is visible text."""
    result = _validate(
        "Deliver. {#delivery} <!-- drafting note -->\n\nSee {#a} `code`\n\n"
        "Per {{ref: delivery}}."
    )
    assert [d.rule for d in result.diagnostics] == ["anchor-misplaced"]
    assert "delivery" in result.section_lookup


def test_marker_after_a_malformed_directive_is_still_an_anchor():
    """A malformed directive has no value to hide the marker in."""
    result = _validate("Text {{ref: x more {#x}\n\nSee {{ref: x}}.")
    assert result.rules() == {"directive-malformed"}


# ── Heading and code-block recognition (§4.1, §11.4) ──────────────

_BARE = _FRONTMATTER.replace("# Terms {#terms}\n\n", "")


def _outline(source: str) -> list[tuple[str, int, str]]:
    """Each heading's text, level, and identifier: explicit or generated."""
    entries = validate_document(parse_document(source)).sections
    return [(entry.title, entry.level, entry.identifier) for entry in entries]


@pytest.mark.parametrize("line", [
    "# Title", "#\x0cTitle", "- item", " - item", "\x0c- item", "1234567890. item", "١. item",
])
def test_only_spaces_and_tabs_and_nine_digits_make_a_heading_or_an_item(line):
    """CommonMark: other whitespace and longer or non-ASCII numbers are
    paragraph text (cmark-gfm)."""
    document = parse_document(_FRONTMATTER + f"{line}\n")
    assert [(b.kind, b.text) for b in document.sections[0].blocks] == [("paragraph", line.strip())]
    if line == line.strip():  # the model strips a leading no-break space (#47)
        assert parse_document(serialize_document(document)) == document


@pytest.mark.parametrize(("line", "kind"), [
    ("#\tTitle", None), ("123456789. item", "ordered_list"), ("-\titem", "unordered_list"),
])
def test_a_tab_or_nine_digits_still_make_a_heading_or_an_item(line, kind):
    document = parse_document(_FRONTMATTER + f"{line}\n")
    if kind is None:
        assert [s.title for s in document.sections] == ["Terms", "Title"]
    else:
        assert [b.kind for b in document.sections[0].blocks] == [kind]


def test_setext_headings_are_headings():
    """§4.1: === and --- underlines make level-1 and level-2 headings."""
    source = _BARE + "Intro.\n\nPayment\nTerms {#pay}\n=======\n\nText.\n\nLate Fees\n---\n\nMore.\n"
    assert _outline(source) == [("Payment Terms", 1, "pay"), ("Late Fees", 2, "late-fees")]
    assert len(parse_document(source).preamble) == 1


@pytest.mark.parametrize("character", ["\x0c", "\x0b", "\x1c", "\x85", "\u2028"])
def test_only_lf_cr_and_crlf_end_a_line(character):
    """CommonMark's line endings are LF, CR and CRLF; the other characters
    str.splitlines() breaks at are text, so no heading starts after one."""
    source = _BARE + f"Intro.{character}# Not a heading\n\n# Heading\r\rText.\r\n"
    assert _outline(source) == [("Heading", 1, "heading")]


def test_an_unclosed_fence_at_the_end_holds_no_extra_line():
    document = parse_document(_FRONTMATTER + "```\ncode\n")
    assert document.sections[0].blocks[0].text == "```\ncode"


def test_nothing_in_an_unclosed_one_line_fence_is_checked():
    """The info string is code (§11.4), however the fence ends."""
    assert not _validate("~~~ {{ref: nowhere}}\n").errors


def test_frontmatter_with_cr_line_endings_is_frontmatter():
    document = parse_document("---\rtitle: X\r---\r\r# A\r")
    assert not document.metadata.frontmatter_absent
    assert document.metadata.title == "X"


def test_a_quote_round_trips_a_character_that_is_not_a_line_ending():
    document = parse_document(_FRONTMATTER + "> One\x0ctwo.\n> Three.\n")
    assert serialize_document(document).endswith("> One\x0ctwo.\n> Three.\n")


@pytest.mark.parametrize(
    "body",
    ["Text.\n\n---\n\nMore.\n", "- item\n---\n", "> quoted\n---\n", "| a | b |\n|---|---|\n| c | d |\n---\n"],
)
def test_dashes_not_under_a_paragraph_are_a_rule(body):
    document = parse_document(_FRONTMATTER + body)
    assert _outline(_FRONTMATTER + body) == [("Terms", 1, "terms")]
    assert "rule" in [b.kind for b in document.sections[0].blocks]


@pytest.mark.parametrize("hashes", ["#", "##"])
def test_a_signature_block_heading_is_an_ordinary_section(hashes):
    """§2.2: signature blocks are not LegalDown markup, so a heading named
    like one is a section, and what follows it is content."""
    source = (
        f"{_BARE}# A\n\nSee {{{{ref: signature-block}}}}.\n\n"
        f"{hashes} Signature Block {{#signature-block}}\n\nSigned by the parties. {{{{ref: nowhere}}}}\n\n# After\n\nText.\n"
    )
    document = parse_document(source)
    assert [s.title for s in document.sections] == ["A", "Signature Block", "After"]
    result = validate_document(document)
    assert [d.message for d in result.diagnostics if d.rule == "ref-broken"] == [
        "Broken section reference: 'nowhere'."
    ]
    serialized = serialize_document(document)
    assert "Signed by the parties." in serialized
    assert parse_document(serialized) == document


def test_a_second_signature_block_identifier_is_a_duplicate():
    source = _BARE + "# Signature Block {#signature-block}\n\nA.\n\n# Signature Block {#signature-block}\n\nB.\n"
    assert "anchor-duplicate" in validate_document(parse_document(source)).rules()


def test_setext_heading_round_trips_as_an_atx_heading():
    """Its generated identifier is not written: it stays generated."""
    source = _BARE + "Scope\n=====\n\nText.\n"
    assert "\n# Scope\n" in serialize_document(parse_document(source))


def test_fenced_code_is_one_literal_block():
    """§11.4: nothing inside a fence is a heading, directive, or anchor, and a
    blank line inside it does not end it."""
    fence = "```\n# not a heading\n\n{#z} {{ref: nope}} {{\n```"
    source = _FRONTMATTER + fence + "\n\n# Next {#next}\n"
    document = parse_document(source)
    assert _outline(source) == [("Terms", 1, "terms"), ("Next", 1, "next")]
    assert [(b.kind, b.text) for b in document.sections[0].blocks] == [("code", fence)]
    assert validate_document(document).diagnostics == []
    assert fence in serialize_document(document)


@pytest.mark.parametrize(
    ("fence", "closed"),
    [
        ("~~~~\n# x\n~~~\n# y\n~~~~", True),  # a shorter fence does not close it
        ("```\n# x\n~~~\n# y\n```", True),  # nor does the other character
        ("```\n# x\n# y", False),  # unclosed: runs to the end of the document
    ],
)
def test_fence_closes_only_on_a_matching_fence(fence, closed):
    source = _FRONTMATTER + fence + "\n\n# After {#after}\n"
    expected = [("Terms", 1, "terms")] + ([("After", 1, "after")] if closed else [])
    assert _outline(source) == expected


def test_inline_triple_backticks_do_not_open_a_fence():
    source = _FRONTMATTER + "```x``` and text\n\n# Next {#next}\n"
    assert _outline(source) == [("Terms", 1, "terms"), ("Next", 1, "next")]


def test_fence_interrupts_a_paragraph():
    document = parse_document(_FRONTMATTER + "Example:\n```\n{{ref: nope}}\n```\n")
    assert [b.kind for b in document.sections[0].blocks] == ["paragraph", "code"]
    assert validate_document(document).diagnostics == []


def _kinds(source: str) -> list[str]:
    return [b.kind for b in parse_document(source).sections[0].blocks]


@pytest.mark.parametrize(
    ("after_fence", "kinds"),
    [("---", ["code", "rule"]), ("===", ["code", "paragraph"])],
)
def test_underline_after_a_fence_is_not_a_setext_heading(after_fence, kinds):
    """A setext underline needs a paragraph above it; a code block is not one."""
    source = _FRONTMATTER + "```\ncode\n```\n" + after_fence + "\n"
    assert _outline(source) == [("Terms", 1, "terms")]
    assert _kinds(source) == kinds


def test_rule_before_setext_text_stays_a_rule():
    source = _FRONTMATTER + "Text.\n\n---\nFoo\n---\n"
    assert _outline(source) == [("Terms", 1, "terms"), ("Foo", 2, "foo")]
    assert _kinds(source) == ["paragraph", "rule"]


@pytest.mark.parametrize("body", ["- item\ncontinued\n---\n", "> quoted\nlazy\n---\n"])
def test_dashes_after_a_lazy_continuation_are_a_rule(body):
    assert _kinds(_FRONTMATTER + body)[-1] == "rule"


def test_fence_inside_a_list_item_does_not_swallow_the_section():
    body = "- Example:\n  ```\n  code\n\n  code2\n  ```\n\nSee {{ref: nope}}.\n"
    document = parse_document(_FRONTMATTER + body)
    assert [b.kind for b in document.sections[0].blocks] == ["unordered_list", "ref"]
    assert "ref-broken" in validate_document(document).rules("error")


def test_tab_indented_backticks_neither_open_nor_close_a_fence():
    """A tab is four columns (CommonMark), past a fence's three."""
    assert "ref-broken" in _validate("\t```\n{{ref: nope}}\n").rules("error")
    source = _FRONTMATTER + "```\n\t```\n# Inside\n```\n"
    assert _outline(source) == [("Terms", 1, "terms")]


def test_indented_fence_round_trips_unchanged():
    fence = "  ```\n  code\n  ```"
    assert fence in serialize_document(parse_document(_FRONTMATTER + "Para.\n\n" + fence + "\n"))


def test_paragraph_directly_above_dashes_is_a_setext_heading():
    """CommonMark, which §4.1 follows: a separator needs a blank line above it."""
    source = _FRONTMATTER + '"Buyer" {{def: buyer}} means X.\n\n---\n\nText.\n'
    assert "rule" in _kinds(source)
    heading = _FRONTMATTER + "Closing words\n---\n"
    assert _outline(heading)[-1] == ("Closing words", 2, "closing-words")


def test_fence_opening_on_a_list_marker_line_stays_in_the_item():
    body = "- ```\n  a\n\n  b\n  ```\n\n# Next {#next}\n\nSee {{ref: nope}}.\n"
    document = parse_document(_FRONTMATTER + body)
    assert _outline(_FRONTMATTER + body) == [("Terms", 1, "terms"), ("Next", 1, "next")]
    assert document.sections[0].blocks[0].items == ["```\na\n\nb\n```"]
    assert "ref-broken" in validate_document(document).rules("error")


def test_fence_in_a_list_item_is_literal_and_round_trips():
    body = "- Example:\n  ~~~\n  {{ref: nope}}\n\n  {#zz}\n  ~~~\n"
    document = parse_document(_FRONTMATTER + body)
    assert validate_document(document).diagnostics == []
    assert body.strip() in serialize_document(document)


def test_blank_line_ends_the_lazy_context_even_inside_a_list_fence():
    source = _FRONTMATTER + "- a\n  ```\n  code\n\nHeading\n---\n"
    assert _outline(source)[-1] == ("Heading", 2, "heading")


def test_indented_dashes_are_code():
    """Indented four columns, a line is code, not a rule (CommonMark)."""
    assert _kinds(_FRONTMATTER + "Text.\n\n    ---\n\nMore.\n") == ["paragraph", "code", "paragraph"]
    assert _kinds(_FRONTMATTER + "Text.\n\n   ---\n\nMore.\n") == ["paragraph", "rule", "paragraph"]


def test_a_single_pipe_line_is_a_paragraph_not_dropped():
    document = parse_document(_FRONTMATTER + "| lone {{ref: nope}}\n")
    assert _kinds(_FRONTMATTER + "| lone {{ref: nope}}\n") == ["ref"]
    assert "ref-broken" in validate_document(document).rules("error")


def test_fence_in_a_block_quote_is_literal():
    assert _validate("> ```\n> {{ref: nope}}\n> ```\n").diagnostics == []


def test_code_block_without_a_fence_is_checked_as_text():
    """Only a fenced block is literal; a hand-built one is not taken on trust."""
    document = parse_document(_FRONTMATTER + "Text.\n")
    document.sections[0].blocks.append(Block(kind="code", text="See {{ref: nope}}."))
    assert "ref-broken" in validate_document(document).rules("error")


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("The parties agree:\n- one\n- two\n---\n", ["paragraph", "unordered_list", "rule"]),
        ("Intro\n> quoted\n---\n", ["paragraph", "quote", "rule"]),
        ("The parties agree:\n1. one\n", ["paragraph", "ordered_list"]),
    ],
)
def test_list_or_quote_interrupts_a_paragraph(body, kinds):
    """CommonMark: a list (ordered only from 1) or a block quote may start
    without a blank line, so the paragraph above is not setext text."""
    source = _FRONTMATTER + body
    assert _outline(source) == [("Terms", 1, "terms")]
    assert _kinds(source) == kinds


def test_item_text_after_its_closed_fence_is_checked():
    document = parse_document(_FRONTMATTER + "- ```\n  code\n  ```\n  more {{ref: nowhere}}\n")
    assert document.sections[0].blocks[0].items == ["```\ncode\n```\nmore {{ref: nowhere}}"]
    assert "ref-broken" in validate_document(document).rules("error")
    assert parse_document(serialize_document(document)).sections == document.sections


def test_unclosed_fence_in_an_item_ends_at_the_next_item():
    document = parse_document(_FRONTMATTER + "1. ```\n   x\n2. b\n3. c\n")
    assert [(b.kind, b.items) for b in document.sections[0].blocks] == [
        ("ordered_list", ["```\nx", "b", "c"])
    ]


def test_tab_after_a_list_marker_counts_as_columns():
    document = parse_document(_FRONTMATTER + "-\t```\n\tx\n\t```\n")
    assert document.sections[0].blocks[0].items == ["```\nx\n```"]


def test_text_after_a_list_ending_in_a_fence_is_not_lazy():
    source = _FRONTMATTER + "- ```\n  x\n  ```\ntext\n---\n"
    assert _outline(source)[-1] == ("text", 2, "text")


def test_rescan_after_a_directive_starts_at_its_line():
    """Backticks right after a directive are not at a line start, so they do
    not open a fence."""
    text = 'a {{ref: x, label="`"}}```\n{{ref: nowhere}} `y`'
    assert [d.positional for d in iter_directives(text)] == ["x", "nowhere"]


def test_code_block_text_after_its_closing_fence_is_checked():
    document = parse_document(_FRONTMATTER + "Text.\n")
    document.sections[0].blocks.append(Block(kind="code", text="```\nx\n```\n{{ref: missing}}"))
    assert "ref-broken" in validate_document(document).rules("error")


def test_serializer_closes_an_unclosed_fence():
    document = parse_document(_FRONTMATTER + "Text.\n\n# Next {#next}\n")
    document.sections[0].blocks.append(Block(kind="code", text="```\nx"))
    reparsed = parse_document(serialize_document(document))
    assert [s.identifier for s in reparsed.sections] == ["terms", "next"]


def test_code_in_a_list_item_keeps_trailing_spaces():
    source = _FRONTMATTER + "- ```\n  keep  \n  ```\n"
    item = parse_document(source).sections[0].blocks[0].items[0]
    assert item == "```\nkeep  \n```"
    assert parse_document(serialize_document(parse_document(source))).sections[0].blocks[0].items[0] == item


@pytest.mark.parametrize(
    ("text", "targets"),
    [
        ('```{{ref: "a`b"}}\n{{ref: hidden}} and `code`', ["a`b", "hidden"]),
        ('{{ref: "a`b"}} {{ref: "c`d"}} {{ref: hidden}} `code`', ["a`b", "c`d", "hidden"]),
    ],
)
def test_backticks_in_directive_values_open_nothing(text, targets):
    """One left-to-right scan: a directive consumes its own backticks."""
    assert [d.positional for d in iter_directives(text)] == targets


@pytest.mark.parametrize("paragraph", ["    ~~~\n    x", "    # not a heading"])
def test_paragraph_that_would_open_a_block_stays_indented(paragraph):
    source = _FRONTMATTER + paragraph + "\n\n# Next {#next}\n\nBody.\n"
    reparsed = parse_document(serialize_document(parse_document(source)))
    assert reparsed.sections == parse_document(source).sections


def test_tabs_inside_list_item_code_are_kept():
    document = parse_document(_FRONTMATTER + "- item\n  ```\n  a\tb\n  ```\n")
    assert document.sections[0].blocks[0].items == ["item\n```\na\tb\n```"]


def test_serializer_closes_an_unclosed_fence_in_a_list_item():
    document = parse_document(_FRONTMATTER + "Text.\n")
    document.sections[0].blocks += [
        Block(kind="unordered_list", items=["x\n```\ncode"]),
        Block(kind="unordered_list", items=["y"]),
    ]
    blocks = parse_document(serialize_document(document)).sections[0].blocks
    assert [b.items for b in blocks[1:]] == [["x\n```\ncode\n```"], ["y"]]


def test_indentation_after_the_quote_marker_is_content():
    """Only one space after > is syntax; a fence line indented four columns
    inside quoted code is content, not a closing fence."""
    document = parse_document(_FRONTMATTER + "> ```\n>     ```\n> {{ref: missing}}\n> ```\n")
    assert validate_document(document).diagnostics == []


@pytest.mark.parametrize(
    ("second_line", "kinds", "headings"),
    [
        ("- ", [], ["Some paragraph"]),  # an empty item is a setext underline
        ("    - x", ["paragraph"], []),  # indented four: paragraph text
        ("2. two", ["paragraph"], []),  # ordered, not numbered 1
    ],
)
def test_only_some_list_items_interrupt_a_paragraph(second_line, kinds, headings):
    source = _FRONTMATTER + "Some paragraph\n" + second_line + "\n"
    assert _kinds(source) == kinds
    assert [title for title, _level, _id in _outline(source)[1:]] == headings


# ── List markers and thematic breaks (§8.2, CommonMark) ───────────


def _blocks(body: str) -> list[tuple[str, str | list[str]]]:
    document = parse_document(_FRONTMATTER + body)
    assert parse_document(serialize_document(document)).sections == document.sections
    return [(b.kind, b.items if b.kind.endswith("list") else b.text) for b in document.sections[0].blocks]


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("- one\n- two\n", "unordered_list"),
        ("* one\n* two\n", "unordered_list"),
        ("+ one\n+ two\n", "unordered_list"),
        ("1. one\n2. two\n", "ordered_list"),
        ("1) one\n2) two\n", "ordered_list"),
        ("5. one\n6. two\n", "ordered_list"),
    ],
)
def test_every_commonmark_list_marker_makes_a_list(body, kind):
    assert _blocks(body) == [(kind, ["one", "two"])]


@pytest.mark.parametrize("body", ["- a\n* b\n", "* a\n+ b\n", "1. a\n1) b\n", "1) a\n- b\n"])
def test_a_change_of_marker_type_starts_a_new_list(body):
    assert [items for _kind, items in _blocks(body)] == [["a"], ["b"]]


@pytest.mark.parametrize("body", ["* --\n", "+ - -\n+ b\n", "* a\n* ---\n", "* a\n* - - -x\n"])
def test_an_item_beginning_with_dashes_is_not_written_as_a_rule(body):
    assert [kind for kind, _items in _blocks(body)] == ["unordered_list"]


def test_a_nested_item_of_another_type_stays_in_the_list():
    assert _blocks("- parent\n  * child\n  1) child\n- next\n") == [
        ("unordered_list", ["parent", "child", "child", "next"])
    ]


@pytest.mark.parametrize("rule", ["***", "* * *", "___", "_ _ _", "- - -", "---", "*\t*\t*"])
def test_a_thematic_break_is_a_rule_not_a_list(rule):
    assert _blocks(f"Intro.\n\n{rule}\n") == [("paragraph", "Intro."), ("rule", "")]
    assert _blocks(f"- a\n{rule}\n") == [("unordered_list", ["a"]), ("rule", "")]
    assert _blocks(f"* a\n{rule}\n") == [("unordered_list", ["a"]), ("rule", "")]


@pytest.mark.parametrize("rule", ["***", "* * *", "___", "- - -"])
def test_a_thematic_break_interrupts_a_paragraph(rule):
    assert _blocks(f"Para\n{rule}\n") == [("paragraph", "Para"), ("rule", "")]


@pytest.mark.parametrize("text", ["**Bold** x", "*emph* x", "+1 vote", "2)x", "- * -", "--", "**", "Para\n    ***"])
def test_text_that_is_neither_a_list_nor_a_rule(text):
    assert [kind for kind, _ in _blocks(text + "\n")] == ["unordered_list" if text == "- * -" else "paragraph"]


@pytest.mark.parametrize(
    ("body", "kinds"),
    [("Text\n+ tax\n", ["paragraph", "unordered_list"]), ("Text\n1) x\n", ["paragraph", "ordered_list"]),
     ("Text\n2) x\n", ["paragraph"])],
)
def test_new_markers_interrupt_a_paragraph_as_commonmark_says(body, kinds):
    assert [kind for kind, _ in _blocks(body)] == kinds


def test_a_rule_in_a_quote_leaves_no_paragraph_open():
    assert [kind for kind, _ in _blocks("> * * *\nlazy\n")] == ["quote", "paragraph"]


def test_a_drafting_note_in_a_star_item_is_recognized():
    body = '* item\n  > [!DRAFTING]\n  > "Fee" {{def: fee}} means the fee.\n\nPay the {{term: fee}}.'
    assert _blocks(body)[0] == ("unordered_list", ['item\n> [!DRAFTING]\n> "Fee" {{def: fee}} means the fee.'])
    assert "drafting-note-def" in _validate(body).rules("error")


# ── Tables (§9.1, GFM) ────────────────────────────────────────────


def _table(body: str) -> Block:
    [block] = parse_document(_FRONTMATTER + body).sections[0].blocks
    return block


def _round_trips(body: str) -> None:
    document = parse_document(_FRONTMATTER + body)
    assert parse_document(serialize_document(document)).sections == document.sections


def test_an_escaped_pipe_is_cell_text_and_alignment_is_kept():
    body = "| a \\| b | c | d | e |\n|:---|---:|:-:|---|\n| 1 | 2 | 3 | 4 |\n"
    block = _table(body)
    assert block.headers == ["a | b", "c", "d", "e"]
    assert block.align == ["left", "right", "center", ""]
    assert block.rows == [["1", "2", "3", "4"]]
    assert "| a \\| b | c | d | e |\n| :--- | ---: | :---: | --- |" in serialize_document(
        parse_document(_FRONTMATTER + body)
    )
    _round_trips(body)


def test_a_pipe_in_a_code_span_splits_the_row_unless_escaped():
    """GFM: a pipe is escaped "including inside other inline spans"."""
    assert _table("| a | b |\n|---|---|\n| `x|y` | 2 |\n").rows == [["`x", "y`"]]
    assert _table("| a | b |\n|---|---|\n| `x\\|y` | 2 |\n").rows == [["`x|y`", "2"]]


def test_a_pipe_after_any_backslash_is_escaped():
    """cmark-gfm: a pipe directly after a backslash is cell text, and only
    that backslash is removed, however many precede it."""
    body = "| a | b | c |\n|---|---|---|\n| x\\\\| y | z\\\\\\| w |\n"
    assert _table(body).rows == [["x\\| y", "z\\\\| w", ""]]
    assert _table("| a \\\\| b |\n|---|\n").headers == ["a \\| b"]
    _round_trips(body)


def test_an_escaped_pipe_in_a_directive_value_reaches_the_directive():
    block = _table('| a |\n|---|\n| {{term: fee, label="x\\|y"}} |\n')
    (directive,) = iter_directives(block.rows[0][0])
    assert directive.params["label"] == "x|y"


def test_an_empty_header_cell_keeps_its_column():
    block = _table("| | b |\n|---|---|\n| 1 | 2 |\n")
    assert (block.headers, block.rows) == (["", "b"], [["1", "2"]])
    _round_trips("| | b |\n|---|---|\n| 1 | 2 |\n")


def test_rows_take_the_header_width_and_empty_rows_are_kept():
    body = "| a | b |\n|---|---|\n|  |  |\n| 1 | 2 | 3 |\n| 4\n| 5 | 6\n"
    assert _table(body).rows == [["", ""], ["1", "2"], ["4", ""], ["5", "6"]]
    _round_trips(body)


@pytest.mark.parametrize(
    "body",
    [
        "| a | b |\n| c | d |\n",  # no delimiter row
        "| a | b |\n|---|\n| c | d |\n",  # fewer delimiter cells than headers
        "| a |\n|---|---|\n| c |\n",  # more
        "| a | b |\n|---|-x-|\n",  # not a delimiter cell
    ],
)
def test_rows_without_a_matching_delimiter_row_are_a_paragraph(body):
    block = _table(body)
    assert block.kind == "paragraph"
    assert block.text == " ".join(body.split("\n")).strip()
    _round_trips(body)


def test_table_cells_are_positional_in_the_model():
    block = document_from_dict(
        {"sections": [{"title": "A", "blocks": [
            {"kind": "table", "headers": ["", "b"], "rows": [["", ""], ["1"], ["1", "2", "3"]], "align": ["RIGHT", "x"]}
        ]}]}
    ).sections[0].blocks[0]
    assert (block.headers, block.rows, block.align) == (["", "b"], [["", ""], ["1", ""], ["1", "2"]], ["right", ""])
    assert document_from_dict(document_to_dict(parse_document(
        _FRONTMATTER + "| a | b |\n|:--|--:|\n"
    ))).sections[0].blocks[0].align == ["left", "right"]
    assert document_from_dict({"sections": [{"blocks": [{"kind": "table"}]}]}).sections[0].blocks[0].rows == [["", ""]]


def test_the_serializer_escapes_pipes_in_model_built_cells():
    def written(block: Block) -> list[str]:
        document = document_from_dict({"sections": [{"title": "A", "blocks": [block]}]})
        return serialize_document(document).split("# A\n\n")[1].splitlines()

    assert written({"kind": "table", "headers": ["a|b"], "rows": [["c|d"]]}) == [
        "| a\\|b |", "| --- |", "| c\\|d |"
    ]
    # A backslash before a pipe is text too: one more is written, and the
    # parser removes exactly that one.
    assert written({"kind": "table", "headers": ["a\\|b"], "rows": []}) == ["| a\\\\|b |", "| --- |"]
    # Without a header row, one is written as wide as the widest row.
    assert written({"kind": "table", "headers": [], "rows": [["1", "2"]]}) == ["|  |  |", "| --- | --- |", "| 1 | 2 |"]
    assert written({"kind": "table", "headers": [], "rows": [[]]}) == ["|  |", "| --- |", "|  |"]
    block = {"kind": "table", "headers": ["a\\|b", "c\\\\|"], "rows": [["\\", "|"]]}
    document = document_from_dict({"sections": [{"title": "A", "blocks": [block]}]})
    assert parse_document(serialize_document(document)).sections == document.sections
    # Nothing to take a width from: one empty column, still a table.
    document = document_from_dict({"sections": [{"title": "A", "blocks": [{"kind": "table", "headers": [], "rows": [[]]}]}]})
    assert parse_document(serialize_document(document)).sections[0].blocks[0].kind == "table"


def test_default_headers_widen_to_the_widest_row():
    block = document_from_dict({"sections": [{"blocks": [{"kind": "table", "rows": [["a", "b", "c"]]}]}]}).sections[0].blocks[0]
    assert (block.headers, block.rows) == (["Column 1", "Column 2", "Column 3"], [["a", "b", "c"]])


@pytest.mark.parametrize(
    ("body", "kinds"),
    [("Text\n| a |\n|---|\n", ["paragraph", "table"]), ("| a |\n| b |\n|---|\n| c |\n", ["paragraph", "table"]),
     ("Text\n| a |\n| b |\n", ["paragraph"])],
)
def test_a_table_interrupts_a_paragraph(body, kinds):
    """GFM: a paragraph's last line and a delimiter row under it start a table."""
    document = parse_document(_FRONTMATTER + body)
    assert [b.kind for b in document.sections[0].blocks] == kinds
    _round_trips(body)


# ── HTML blocks (§8.6, §8.7, CommonMark 4.6) ──────────────────────


def _html(body: str, *, bare: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """Section titles, and the (kind, text) of every block, preamble first."""
    source = (_BARE if bare else _FRONTMATTER) + body
    document = parse_document(source)
    assert parse_document(serialize_document(document)) == document
    blocks = [(b.kind, b.text) for _section, _index, b in document.iter_blocks()]
    return [s.title for s in document.sections], blocks


def test_a_heading_inside_a_multi_line_comment_is_not_a_section():
    titles, blocks = _html("Zero.\n\n<!--\n# Old clause\n-->\n\n# Next\n\nText.\n")
    assert titles == ["Terms", "Next"]
    assert ("html", "<!--\n# Old clause\n-->") in blocks


def test_an_html_block_runs_to_a_blank_line():
    assert _html("<div>\n# X\n{{ref: nope}}\n</div>\n")[0] == ["Terms"]
    titles, blocks = _html("<div>\n\n# X\n\n</div>\n")
    assert titles == ["Terms", "X"]
    assert blocks == [("html", "<div>"), ("html", "</div>")]


@pytest.mark.parametrize(
    "html",
    [
        "<!-- a -->", "<!-->", "<!--->", "<?php echo 1; ?>", "<!DOCTYPE html>", "<![CDATA[ x ]]>",
        "<script>\nlet a;\n\n# not a heading\n</script>", "<?\n# x\n?>", "<!X\n# x\n>", "<![CDATA[\n# x\n]]>",
    ],
)
def test_html_block_kinds_one_to_five_end_at_their_marker(html):
    titles, blocks = _html(f"{html}\nAfter.\n")
    assert titles == ["Terms"]
    assert blocks == [("html", html), ("paragraph", "After.")]


@pytest.mark.parametrize("opener", ["<!--", "<?", "<!DOCTYPE", "<![CDATA[", "<pre>"])
def test_an_unclosed_html_block_runs_to_the_end(opener):
    titles, blocks = _html(f"Intro.\n\n# A\n\n{opener}\n# B\n\nText.\n", bare=True)
    assert titles == ["A"]
    assert blocks == [("paragraph", "Intro."), ("html", f"{opener}\n# B\n\nText.")]
    assert _html(f"{opener}\n# A\n", bare=True) == ([], [("html", f"{opener}\n# A")])


def test_a_whole_line_after_a_comment_is_raw_html():
    """CommonMark: the line that ends a comment block belongs to it, so its
    text is not rendered and its directives are not recognized (§11.4)."""
    source = _FRONTMATTER + "<!-- TODO --> The Buyer pays {{money: 5}} under {{ref: nope}}.\n"
    result = validate_document(parse_document(source))
    assert result.diagnostics == [] and result.inline_money == []


def test_nothing_in_an_html_block_is_validated():
    source = _FRONTMATTER + '<div>{{ref: nope}} {{bogus: x}} {{ "Fee" {{def: fee}}\n{#anchor}</div>\n\nSee {{ref: anchor}}.\n'
    assert validate_document(parse_document(source)).rules() == {"ref-broken"}


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("Text\n<div>\n", ["paragraph", "html"]),
        ("Text\n<!-- note -->\n", ["paragraph", "html"]),
        ('"Fee" {{def: fee}} means x.\n<div>\n', ["definition", "html"]),
        ("Text\n<span>\n", ["paragraph"]),  # kind 7 cannot interrupt a paragraph
        ("<span>\n", ["html"]),
        ('<a href="x" title=\'y\'>\n', ["html"]),
        ("</span>\n", ["html"]),
        ("<https://example.com>\n", ["paragraph"]),  # an autolink
        ("<b>bold</b> text\n", ["paragraph"]),  # an inline tag
        ("    <div>\n", ["code"]),  # indented four columns
        ("| a |\n|---|\n<span>\n", ["table", "html"]),
        ("<pre-x>\n## Not a heading\n", ["html"]),  # only the exact names are excluded
        ("<style-guide x>\n", ["html"]),
    ],
)
def test_which_lines_start_an_html_block(body, kinds):
    assert [kind for kind, _text in _html(body)[1]] == kinds


def test_a_fence_inside_an_html_block_is_raw_html():
    titles, blocks = _html("<div>\n```\n# X\n</div>\n\n# Y\n")
    assert titles == ["Terms", "Y"]
    assert blocks == [("html", "<div>\n```\n# X\n</div>")]


def test_an_html_block_in_the_model_keeps_its_indentation():
    document = document_from_dict(
        {"sections": [{"title": "A", "blocks": [{"kind": "html", "text": "\n\n   <div>\n  x\n</div>  \n\n"}]}]}
    )
    assert document.sections[0].blocks[0].text == "   <div>\n  x\n</div>"
    assert parse_document(serialize_document(document)).sections == document.sections


@pytest.mark.parametrize("prefix", ["<div> see", "# see", "```"])
def test_a_model_built_reference_that_would_open_a_block_is_escaped(prefix):
    """The parser never lifts such text; a model can hold it. A backslash
    keeps it text, and renders as nothing (CommonMark)."""
    document = document_from_dict(
        {"sections": [{"title": "A", "identifier": "sec-a", "blocks": [
            {"kind": "ref", "prefix": f"{prefix} ", "target": "sec-a", "suffix": " here"},
            {"kind": "paragraph", "text": f"{prefix} text"},
        ]}]}
    )
    reparsed = parse_document(serialize_document(document)).sections[0].blocks
    assert [(b.kind, b.prefix or b.text) for b in reparsed] == [("ref", f"\\{prefix} "), ("paragraph", f"\\{prefix} text")]


# ── Indented code blocks (§11.4, CommonMark 4.4) ──────────────────


def _code_blocks(body: str) -> list[tuple[str, str]]:
    document = parse_document(_FRONTMATTER + body)
    assert parse_document(serialize_document(document)) == document
    return [(b.kind, b.text) for b in document.sections[0].blocks]


def test_directives_in_indented_code_are_literal():
    body = "Text.\n\n    {{ref: nope}} {#anchor} \"Fee\" {{def: fee}}\n    {{bogus: x}}\n\nSee {{ref: anchor}}.\n"
    result = validate_document(parse_document(_FRONTMATTER + body))
    assert result.rules() == {"ref-broken"}
    assert [d.message for d in result.diagnostics] == ["Broken section reference: 'anchor'."]


def test_an_indented_code_block_keeps_its_lines():
    body = "Text.\n\n    a\n\n\t  b\n      c\n\n\nAfter.\n"
    assert _code_blocks(body) == [("paragraph", "Text."), ("code", "    a\n\n\t  b\n      c"), ("paragraph", "After.")]


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("Text.\n    more {{ref: nope}}\n", ["ref"]),  # continues the paragraph
        ("- item\n\n    more\n", ["unordered_list", "paragraph"]),  # CommonMark keeps it in the item
        ("> quote\n\n    code\n", ["quote", "code"]),
        ("> quote\n    lazy\n", ["quote"]),  # a lazy line of the quote
        ("| a |\n|---|\n    code\n", ["table", "code"]),
        ("    - x\n", ["code"]),
        ("    > q\n", ["code"]),
        ("    | a |\n    |---|\n", ["code"]),
        ("   # Heading\n", []),  # up to three spaces: a heading
        ("Text.\n    > q\n", ["paragraph"]),  # not a quote: continues the paragraph
        ("Text.\n    | a |\n    |---|\n", ["paragraph"]),  # not a table
    ],
)
def test_where_indented_code_starts(body, kinds):
    assert [kind for kind, _text in _code_blocks(body)] == kinds


def test_a_heading_indented_up_to_three_spaces_interrupts_a_paragraph():
    document = parse_document(_FRONTMATTER + "Text.\n   ## Sub\n")
    assert [s.title for s in document.sections] == ["Terms", "Sub"]


def test_a_document_can_open_with_indented_code():
    document = parse_document("    code\n\n# A\n")
    assert [(b.kind, b.text) for b in document.preamble] == [("code", "    code")]
    assert parse_document(serialize_document(document)) == document


def test_model_built_indented_code_after_a_list_is_written_fenced():
    document = document_from_dict({"sections": [{"title": "A", "blocks": [
        {"kind": "unordered_list", "items": ["one"]},
        {"kind": "code", "text": "    x = `y`\n\n      z"},
    ]}]})
    written = serialize_document(document)
    assert "- one\n\n```\nx = `y`\n\n  z\n```" in written
    assert [b.kind for b in parse_document(written).sections[0].blocks] == ["unordered_list", "code"]


def test_every_indented_paragraph_after_a_list_stays_a_paragraph():
    """CommonMark keeps them all in the list's last item, so their
    directives are checked; an unindented paragraph ends the run."""
    body = "1. Clause.\n\n    Second.\n\n    Pay {{placeholder: fee}}.\n\nThird.\n\n    code {{ref: nope}}\n"
    assert [kind for kind, _text in _code_blocks(body)] == ["ordered_list", "paragraph", "paragraph", "paragraph", "code"]
    result = validate_document(parse_document(_FRONTMATTER + body), final=True)
    assert "placeholder-unfilled" in result.rules("error") and "ref-broken" not in result.rules()


@pytest.mark.parametrize("opener", ["# foo", "<div>", "```", "~~~", "<!-- c -->"])
def test_an_indented_paragraph_after_a_list_round_trips_whatever_it_begins_with(opener):
    body = f"- a\n\n    b\n\n    {opener}\n\nd\n\n    code\n"
    assert _code_blocks(body) == [
        ("unordered_list", ""), ("paragraph", "b"), ("paragraph", opener), ("paragraph", "d"), ("code", "    code")
    ]
    assert "\n    b\n\n    " in serialize_document(parse_document(_FRONTMATTER + body))


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("Text.\n    | a |\n|---|\n| 1 |\n", ["paragraph", "table"]),  # the delimiter row decides
        ("Text.\n    | a |\n    |---|\n", ["paragraph"]),
        ("{{ref: x}} y\n      <div>x</div>\n      | a | b |\n|---|---|\n", ["ref", "table"]),
    ],
)
def test_a_table_with_an_indented_header_interrupts_a_paragraph(body, kinds):
    assert [kind for kind, _text in _code_blocks(body)] == kinds


@pytest.mark.parametrize(
    "before", ["# H", "***", "<!-- c -->", "Title\n===", "```\nx\n```"],
)
def test_an_indented_header_row_after_another_block_is_code(before):
    """Only an open paragraph can be interrupted by a table whose header row
    is indented; elsewhere that row is indented code (CommonMark)."""
    document = parse_document(f"{_BARE}{before}\n    | {{{{ref: nope}}}} |\n|---|\n")
    assert "table" not in [b.kind for _s, _i, b in document.iter_blocks()]
    assert "ref-broken" not in validate_document(document).rules()


def test_code_after_the_indented_run_after_a_list_stays_code():
    # The paragraph is written indented, to stay a paragraph after the list,
    # so the code after it is written fenced, to stay code.
    document = parse_document(_FRONTMATTER + "- a\n\n<a\nhref='x'>\n\n    code {{ref: nope}}\n")
    reparsed = parse_document(serialize_document(document))
    assert [(b.kind, b.text) for b in reparsed.sections[0].blocks] == [
        ("unordered_list", ""), ("paragraph", "<a href='x'>"), ("code", "```\ncode {{ref: nope}}\n```")
    ]
    assert "ref-broken" not in validate_document(reparsed).rules()
    document = document_from_dict({"sections": [{"title": "A", "blocks": [
        {"kind": "unordered_list", "items": ["a"]}, {"kind": "paragraph", "text": "# foo"}, {"kind": "code", "text": "    x"},
    ]}]})
    blocks = parse_document(serialize_document(document)).sections[0].blocks
    assert [(b.kind, b.text) for b in blocks] == [("unordered_list", ""), ("paragraph", "# foo"), ("code", "```\nx\n```")]


def test_the_indented_run_stops_at_text_that_reads_as_another_block():
    document = document_from_dict({"sections": [{"title": "A", "blocks": [
        {"kind": "unordered_list", "items": ["a"]}, {"kind": "paragraph", "text": "> q"},
        {"kind": "paragraph", "text": "# foo"},
    ]}]})
    blocks = parse_document(serialize_document(document)).sections[0].blocks
    assert (blocks[-1].kind, blocks[-1].text) == ("paragraph", "\\# foo")


# ── Review follow-ups: table rows, kind-7 tags, the indented run ──


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("| a |\n    |---|\n| {{ref: terms}} |\n", ["ref"]),  # an indented delimiter row: no table
        ("| a |\n|---|\n    | {{ref: nope}} |\n", ["table", "code"]),  # an indented row is code
        ("| a |\n|---|\n\t| b |\n", ["table", "code"]),
        ("| a |\n|---|\n  | b |\n", ["table"]),  # up to three columns: a row
        ("Text\n|\n|---|\n", ["paragraph"]),  # a lone | is no header
        ("| a |\n|---|\n|\n| b |\n", ["table", "paragraph"]),  # a lone | ends the table
    ],
)
def test_table_rows_are_indented_at_most_three_columns_and_have_cells(body, kinds):
    assert [kind for kind, _text in _code_blocks(body)] == kinds
    assert "ref-broken" not in validate_document(parse_document(_FRONTMATTER + body)).rules()


@pytest.mark.parametrize("tag", ["<pre/>", "<SCRIPT/>", "<style />", "<textarea/>"])
def test_a_self_closing_raw_text_tag_is_an_html_block(tag):
    """cmark-gfm reads these as kind 7, which kind 1 does not take."""
    assert _html(f"{tag}\n# x\n") == (["Terms"], [("html", f"{tag}\n# x")])


@pytest.mark.parametrize("first", ["| b", "| {{ref: terms}}", "| x |"])
def test_a_pipe_paragraph_does_not_end_the_indented_run_after_a_list(first):
    body = f"- a\n\n    {first}\n\n    # foo\n"
    assert [kind for kind, _text in _code_blocks(body)][-1] == "paragraph"
    assert _code_blocks(body)[-1] == ("paragraph", "# foo")


def test_an_empty_named_value_is_written_unquoted():
    assert format_value("") == ""
    assert format_value("“x”") == '"“x”"'
