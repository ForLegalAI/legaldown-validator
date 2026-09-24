"""Specification 0.2: template questions (§15.2) and the placeholder and
frontmatter rules they tightened (§3.10, §10.7, §12.2)."""
from __future__ import annotations

import pytest

from legaldown import Amends, document_from_dict, document_to_dict, serialize_document
from legaldown.parser import parse_document
from legaldown.validator import validate_document

_SIDES = """sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
  - name: clients
    parties:
      - name: beta
        type: legal_entity
"""


def _validate(frontmatter: str = "", body: str = "Text."):
    source = f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n# Terms {{#terms}}\n\n{body}\n"
    return validate_document(parse_document(source, filename="t.lgd"))


# ── Placeholders (§10.7) ──────────────────────────────────────────


def test_duration_placeholder_is_valid():
    result = _validate(body="For {{placeholder: term, type=duration, unit=MO}}.")
    assert result.diagnostics == []


@pytest.mark.parametrize("unit", ["M", "MONTHS", ""])
def test_duration_placeholder_unit_follows_the_duration_rule(unit):
    result = _validate(body=f"For {{{{placeholder: term, type=duration, unit={unit}}}}}.")
    assert "duration-invalid-unit" in result.rules("error")


def test_unit_is_defined_only_for_duration_placeholders():
    result = _validate(body="For {{placeholder: term, type=money, unit=MO}}.")
    assert result.rules() == {"directive-unknown-param"}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("type=money, currency=EUR", "type=money, currency=USD"),
        ("type=duration, unit=MO", "type=duration, unit=Y"),
    ],
)
def test_one_blank_cannot_fix_two_currencies_or_units(first, second):
    result = _validate(body=f"{{{{placeholder: fee, {first}}}}} and {{{{placeholder: fee, {second}}}}}.")
    assert "placeholder-type-inconsistent" in result.rules("error")


def test_a_frontmatter_currency_counts_for_consistency():
    result = _validate(
        'subtitle: "Fee {{placeholder: fee, type=money, currency=EUR}}"\n',
        "Pay {{placeholder: fee, type=money, currency=USD}}.",
    )
    assert "placeholder-type-inconsistent" in result.rules("error")


def test_an_occurrence_may_leave_the_currency_open():
    result = _validate(
        body="{{placeholder: fee, type=money, currency=EUR}} and {{placeholder: fee, type=money}}."
    )
    assert result.diagnostics == []


# ── Questions and placeholder types (§15.2) ───────────────────────

_QUESTIONS = """questions:
  fee:
    type: money
    prompt: Fixed fee
  start:
    type: date
  forum:
    type: choice
    choices:
      courts: State courts
      arbitration: ICC arbitration
"""


def test_a_declared_value_question_gives_the_placeholder_its_type():
    """The declared type is the effective type, so a money placeholder
    takes currency without writing type=money."""
    result = _validate(_QUESTIONS, "Pay {{placeholder: fee, currency=EUR}} from {{placeholder: start}}.")
    assert "directive-unknown-param" not in result.rules()
    assert ("fee", "money") in result.inline_placeholders
    assert ("start", "date") in result.inline_placeholders


def test_inline_type_must_equal_the_declared_type():
    result = _validate(_QUESTIONS, "Pay {{placeholder: fee, type=date}}.")
    assert "placeholder-question-mismatch" in result.rules("error")


def test_a_placeholder_cannot_use_a_decision_question():
    result = _validate(_QUESTIONS, "Disputes go to {{placeholder: forum}}.")
    assert "placeholder-question-mismatch" in result.rules("error")


def test_well_formed_questions_are_valid():
    frontmatter = _QUESTIONS + """  vat:
    type: boolean
    default: false
  term:
    type: duration
    default:
      value: 12
      unit: MO
  client:
    type: text
    default: Beta Industries Inc.
"""
    result = _validate(frontmatter, "{{placeholder: fee}} {{placeholder: term}} {{placeholder: client}}")
    assert "question-invalid" not in result.rules()


@pytest.mark.parametrize(
    "questions",
    [
        "questions:\n  - fee\n",  # not a map
        "questions:\n  Fee:\n    type: text\n",  # not an identifier
        "questions:\n  yes:\n    type: boolean\n",  # read as a boolean
        "questions:\n  y:\n    type: boolean\n",
        "questions:\n  fee: money\n",  # not a declaration
        "questions:\n  fee:\n    prompt: Fee\n",  # no type
        "questions:\n  fee:\n    type: number\n",
        "questions:\n  forum:\n    type: choice\n    choices:\n      courts: Courts\n",
        "questions:\n  forum:\n    type: choice\n    choices:\n      Courts: A\n      b: B\n",
        "questions:\n  forum:\n    type: choice\n    choices:\n      on: A\n      b: B\n",
        "questions:\n  forum:\n    type: choice\n    choices:\n      a: A\n      b: ''\n",
        "questions:\n  vat:\n    type: boolean\n    choices:\n      a: A\n      b: B\n",
        "questions:\n  vat:\n    type: boolean\n    default: 'yes'\n",
        "questions:\n  forum:\n    type: choice\n    choices:\n      a: A\n      b: B\n    default: c\n",
        "questions:\n  client:\n    type: text\n    default: ' Beta'\n",
        "questions:\n  client:\n    type: text\n    default: ''\n",
        "questions:\n  start:\n    type: date\n    default: 2026-13-01\n",
        "questions:\n  fee:\n    type: money\n    default: '100.00'\n",  # no fixed currency
        "questions:\n  fee:\n    type: money\n    default:\n      amount: 100\n      currency: EUR\n",
        "questions:\n  fee:\n    type: money\n    default:\n      amount: '100'\n      currency: eur\n",
        "questions:\n  term:\n    type: duration\n    default:\n      value: 12\n      unit: M\n",
        "questions: {fee: {type: text}}\n",  # flow style
        "questions:\n  forum:\n    type: choice\n    choices: {a: A, b: B}\n",
    ],
)
def test_malformed_questions_are_reported(questions):
    assert "question-invalid" in _validate(questions).rules("error")


def test_a_money_default_may_be_the_amount_when_every_placeholder_fixes_the_currency():
    frontmatter = "questions:\n  fee:\n    type: money\n    default: '100.00'\n"
    fixed = _validate(frontmatter, "{{placeholder: fee, currency=EUR}}")
    assert "question-invalid" not in fixed.rules()
    partly = _validate(frontmatter, "{{placeholder: fee, currency=EUR}} {{placeholder: fee}}")
    assert "question-invalid" in partly.rules("error")


def test_a_default_must_agree_with_the_fixed_currency():
    frontmatter = (
        "questions:\n  fee:\n    type: money\n    default:\n"
        "      amount: '100.00'\n      currency: USD\n"
    )
    result = _validate(frontmatter, "{{placeholder: fee, currency=EUR}}")
    assert "question-invalid" in result.rules("error")


def test_a_text_default_filling_frontmatter_cannot_hold_an_opener():
    frontmatter = (
        'subtitle: "For {{placeholder: client}}"\n'
        "questions:\n  client:\n    type: text\n    default: 'A {{b'\n"
    )
    assert "question-invalid" in _validate(frontmatter).rules("error")
    body_only = frontmatter.replace('subtitle: "For {{placeholder: client}}"\n', "")
    assert "question-invalid" not in _validate(body_only, "{{placeholder: client}}").rules()


def test_a_templates_attachments_must_be_line_editable():
    attachments = "attachments:\n  - title: Schedule\n    id: schedule\n    file: s.pdf\n"
    body = "See {{attach: schedule}}."
    assert "question-invalid" not in _validate(attachments, body).rules()
    template = attachments + "questions:\n  client:\n    type: text\n"
    assert "question-invalid" in _validate(template, body).rules("error")


def test_a_templates_undeclared_placeholder_id_cannot_be_a_yaml_word():
    body = "Answer: {{placeholder: no}}."
    assert "question-invalid" not in _validate(body=body).rules()
    template = "questions:\n  client:\n    type: text\n"
    assert "question-invalid" in _validate(template, body).rules("error")


def test_questions_and_conditional_attachments_round_trip():
    frontmatter = (
        'legaldown: "0.2"\n' + _QUESTIONS
        + "attachments:\n  - id: dpa\n    title: DPA\n    file: dpa.lgd\n    when: personal-data\n"
    )
    source = f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n# Terms {{#terms}}\n\nText.\n"
    document = parse_document(source)
    again = parse_document(serialize_document(document))
    assert again.metadata == document.metadata
    assert again.metadata.legaldown == "0.2"
    assert again.metadata.attachments[0].when == "personal-data"


# ── Frontmatter placeholders (§3.10) ──────────────────────────────


@pytest.mark.parametrize(
    "frontmatter",
    [
        'legaldown: "{{placeholder: v}}"\n',
        'language: "{{placeholder: lang}}"\n',
        'authoritative: "{{placeholder: lang}}"\n',
        'translations:\n  fr: "{{placeholder: path}}"\n',
        'translations:\n  "{{placeholder: lang}}": doc-fr.lgd\n',
        'field_types:\n  invoice-id: "{{placeholder: kind}}"\n',
        'amends:\n  title: MSA\n  file: "{{placeholder: path}}"\n',
        'supersedes:\n  title: MSA\n  file: "{{placeholder: path}}"\n',
        'attachments:\n  - id: "{{placeholder: a}}"\n    title: A\n    file: a.pdf\n',
        'attachments:\n  - id: a\n    title: A\n    file: "{{placeholder: path}}"\n',
        'questions:\n  fee:\n    type: text\n    prompt: "{{placeholder: p}}"\n',
    ],
)
def test_placeholders_in_format_checked_fields_are_errors(frontmatter):
    result = _validate(frontmatter)
    assert "placeholder-in-structural-field" in result.rules("error")


def test_placeholders_in_titles_of_references_are_checked_value_fields():
    frontmatter = (
        'amends:\n  title: "{{placeholder: msa, type=bogus}}"\n'
        'supersedes:\n  title: "{{placeholder: nda, type=bogus}}"\n'
    )
    result = _validate(frontmatter)
    assert "placeholder-in-structural-field" not in result.rules()
    assert [d.rule for d in result.diagnostics if d.level == "error"] == [
        "placeholder-type-invalid",
        "placeholder-type-invalid",
    ]


@pytest.mark.parametrize(
    ("frontmatter", "valid"),
    [
        ('effective_date: "{{placeholder: start, type=date}}"\n', True),
        ('effective_date: "{{placeholder: start}}"\nquestions:\n  start:\n    type: date\n', True),
        ('effective_date: "{{placeholder: start}}"\n', False),  # text
        ('effective_date: "From {{placeholder: start, type=date}}"\n', False),
        ('adoption_date: "{{placeholder: start, type=money}}"\n', False),
    ],
)
def test_a_date_field_placeholder_is_the_whole_value_and_a_date(frontmatter, valid):
    result = _validate(frontmatter)
    assert ("metadata-date-invalid" not in result.rules()) == valid


def test_date_of_birth_placeholder_must_be_a_date():
    source = _SIDES.replace(
        "        type: legal_entity\n  - name: clients",
        '        type: natural_person\n        date_of_birth: "{{placeholder: dob}}"\n'
        "  - name: clients",
    )
    document = parse_document(f"---\ntitle: Fixture\n{source}---\n\n# Terms\n")
    assert "date-of-birth-invalid" in validate_document(document).rules("error")


def test_supersedes_object_form_is_modelled_and_its_title_required():
    result = _validate("supersedes:\n  file: old.pdf\n")
    assert "supersedes-title-empty" in result.rules("error")
    document = parse_document(
        f"---\ntitle: Fixture\n{_SIDES}supersedes:\n  title: Old NDA\n  file: old.pdf\n---\n"
    )
    assert document.metadata.supersedes == Amends(title="Old NDA", file="old.pdf")
    assert parse_document(serialize_document(document)).metadata == document.metadata


# ── Include-only paragraphs (§12.2) ───────────────────────────────


@pytest.mark.parametrize(
    "paragraph",
    ["{{include: parts/a.lgd}} {#part-a}", "{{include: parts/a.lgd}} <!-- A --> {#part-a}"],
)
def test_an_anchor_on_an_include_only_paragraph_is_ignored(paragraph):
    result = _validate(body=f"{paragraph}\n\nSee {{{{ref: part-a}}}}.")
    assert "anchor-misplaced" in result.rules("warning")
    assert "ref-broken" in result.rules("error")


def test_an_anchor_on_a_list_item_holding_an_include_is_kept():
    result = _validate(body="- {{include: parts/a.lgd}} {#part-a}\n\nSee {{ref: part-a}}.")
    assert result.diagnostics == []


# ── Review follow-ups ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "questions",
    [
        'questions:\n  "abc\\n":\n    type: text\n',
        'questions:\n  forum:\n    type: choice\n    choices:\n      "a\\n": A\n      b: B\n',
    ],
)
def test_an_id_ending_in_a_line_break_is_not_an_identifier(questions):
    assert "question-invalid" in _validate(questions).rules("error")


def test_keys_yaml_reads_as_booleans_stay_distinct_and_as_written():
    frontmatter = "questions:\n  yes:\n    type: text\n  true:\n    type: date\n"
    result = _validate(frontmatter)
    messages = [d.message for d in result.diagnostics if d.rule == "question-invalid"]
    assert len(messages) == 2
    assert any("'yes'" in message for message in messages)
    source = f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n"
    document = parse_document(source)
    assert list(document.metadata.questions) == ["yes", "true"]
    assert parse_document(serialize_document(document)).metadata == document.metadata


def test_an_empty_attachments_key_in_a_template_is_line_editable():
    frontmatter = "attachments:\nquestions:\n  client:\n    type: text\n"
    assert "question-invalid" not in _validate(frontmatter, "{{placeholder: client}}").rules()


def test_the_block_style_finding_describes_the_parsed_source_only():
    """A document rebuilt from a dict has no source; serialized, it is
    written in block style. A frontmatter key cannot set the finding."""
    source = f"---\ntitle: Fixture\n{_SIDES}questions: {{fee: {{type: text}}}}\n---\n"
    parsed = parse_document(source)
    assert parsed.metadata.not_line_editable == ["questions"]
    rebuilt = document_from_dict(document_to_dict(parsed))
    assert rebuilt.metadata.not_line_editable == []
    injected = "not_line_editable:\n  - questions\nquestions:\n  fee:\n    type: text\n"
    assert "question-invalid" not in _validate(injected, "{{placeholder: fee}}").rules()


@pytest.mark.parametrize("value", ["1e3", "inf", "+5", "-5", "1.5.2"])
def test_a_duration_value_is_an_integer_or_decimal(value):
    body = f"For {{{{duration: {value}, unit=H}}}}."
    assert "duration-invalid-value" in _validate(body=body).rules("error")
    default = f"questions:\n  term:\n    type: duration\n    default:\n      value: '{value}'\n      unit: H\n"
    assert "question-invalid" in _validate(default, "{{placeholder: term}}").rules("error")


@pytest.mark.parametrize(
    ("version", "newer"),
    [("0.3", True), ("1.0", True), ("0.2.1", True), ("0.2", False), ("0.2.0", False), ("0.1", False), ("draft", False)],
)
def test_a_newer_declared_version_is_a_warning_and_softens_unknown_directives(version, newer):
    result = _validate(f'legaldown: "{version}"\n', "{{frobnicate: x}}")
    assert ("legaldown-version-newer" in result.rules("warning")) == newer
    assert ("directive-unknown" in result.rules("warning" if newer else "error"))
    assert ("directive-unknown" in result.rules("error")) != newer


def test_yaml_merge_keys_still_merge():
    source = (
        "---\ntitle: Fixture\nbase: &base\n  name: acme\n  type: legal_entity\n"
        "sides:\n  - name: providers\n    parties:\n      - <<: *base\n---\n"
    )
    party = parse_document(source).metadata.sides[0].parties[0]
    assert (party.name, party.type) == ("acme", "legal_entity")


def test_an_unquoted_version_is_read_as_written():
    result = _validate("legaldown: 0.10\n")
    assert "legaldown-version-newer" in result.rules("warning")
    source = f"---\ntitle: Fixture\n{_SIDES}legaldown: 0.10\n---\n"
    assert parse_document(source).metadata.legaldown == "0.10"


@pytest.mark.parametrize("frontmatter", ["questions: {}\n", "attachments: []\nquestions: {}\n"])
def test_empty_flow_collections_have_nothing_to_edit(frontmatter):
    assert "question-invalid" not in _validate(frontmatter).rules()


def test_two_currencies_on_one_blank_are_reported_once():
    body = (
        "{{placeholder: fee, type=money, currency=USD}} {{placeholder: fee, type=money, currency=EUR}}"
        " {{placeholder: fee, type=money, currency=EUR}}"
    )
    rules = [d.rule for d in _validate(body=body).diagnostics]
    assert rules.count("placeholder-type-inconsistent") == 1


def test_a_frontmatter_occurrence_counts_even_with_the_wrong_type():
    frontmatter = (
        'subtitle: "{{placeholder: client, type=date}}"\n'
        "questions:\n  client:\n    type: text\n    default: 'A {{b'\n"
    )
    result = _validate(frontmatter, "{{placeholder: client}}")
    assert "question-invalid" in result.rules("error")


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ("{{duration: ٣, unit=D}}", "duration-invalid-value"),
        ("{{money: ١٠٠, currency=USD}}", "money-invalid-amount"),
        ("{{date: ٢٠٢٦-01-01}}", "date-invalid"),
    ],
)
def test_numbers_and_dates_take_ascii_digits_only(body, rule):
    assert rule in _validate(body=body).rules("error")


def test_a_float_duration_default_is_rejected_with_the_reason():
    frontmatter = "questions:\n  term:\n    type: duration\n    default:\n      value: 1.5\n      unit: D\n"
    result = _validate(frontmatter, "{{placeholder: term}}")
    assert any("YAML float" in d.message for d in result.diagnostics if d.rule == "question-invalid")


def test_merged_keys_are_read_as_written_too():
    frontmatter = "base: &base\n  yes:\n    type: text\nquestions:\n  <<: *base\n"
    messages = [d.message for d in _validate(frontmatter).diagnostics if d.rule == "question-invalid"]
    assert any("'yes'" in message for message in messages)


@pytest.mark.parametrize("when", ["no", "on", "true"])
def test_an_attachment_condition_is_read_as_written(when):
    source = (
        f"---\ntitle: Fixture\n{_SIDES}attachments:\n"
        f"  - id: dpa\n    title: DPA\n    file: dpa.lgd\n    when: {when}\n---\n"
    )
    document = parse_document(source)
    assert document.metadata.attachments[0].when == when
    assert parse_document(serialize_document(document)).metadata == document.metadata


def test_choice_labels_are_read_as_written():
    frontmatter = "questions:\n  agree:\n    type: choice\n    choices:\n      accept: Yes\n      reject: No\n"
    assert "question-invalid" not in _validate(frontmatter).rules()


def test_a_boolean_default_keeps_its_yaml_type():
    frontmatter = "questions:\n  vat:\n    type: boolean\n    default: true\n"
    assert "question-invalid" not in _validate(frontmatter).rules()


def test_a_placeholder_with_an_invalid_type_is_still_checked_against_its_question():
    result = _validate(_QUESTIONS, "{{placeholder: forum, type=bogus}}")
    assert {"placeholder-type-invalid", "placeholder-question-mismatch"} <= result.rules("error")
    template = _validate("questions:\n  client:\n    type: text\n", "{{placeholder: yes, type=bogus}}")
    assert {"placeholder-type-invalid", "question-invalid"} <= template.rules("error")


def test_an_invalid_unit_fixes_nothing():
    body = "{{placeholder: p, type=duration, unit=M}} {{placeholder: p, type=duration, unit=D}}"
    assert "placeholder-type-inconsistent" not in _validate(body=body).rules()
    frontmatter = "questions:\n  p:\n    type: duration\n    default: 3\n"
    result = _validate(frontmatter, "{{placeholder: p, unit=M}}")
    assert "question-invalid" in result.rules("error")


def test_a_version_takes_ascii_digits_only():
    assert "legaldown-version-newer" not in _validate('legaldown: "\uff10.\uff13"\n').rules()


def test_a_default_shared_through_an_alias_keeps_its_type():
    frontmatter = (
        "x-base: &base\n  type: boolean\n  default: false\n"
        "questions:\n  agree:\n    <<: *base\n"
    )
    assert "question-invalid" not in _validate(frontmatter).rules()


def test_self_referencing_aliases_parse():
    frontmatter = "x: &a [*a]\nquestions: &q\n  a:\n    type: text\n    label: *q\n"
    assert "question-invalid" not in _validate(frontmatter, "{{placeholder: a}}").rules()


def test_nested_aliases_are_walked_once():
    levels = ["l0: &l0 [x, x, x, x, x, x, x, x, x, x]"]
    for depth in range(1, 25):
        levels.append(f"l{depth}: &l{depth} [{', '.join([f'*l{depth - 1}'] * 10)}]")
    source = "---\ntitle: Fixture\n" + "\n".join(levels) + "\n---\n"
    assert parse_document(source).metadata.title == "Fixture"


def test_an_attachment_entry_is_judged_as_written_not_as_merged():
    frontmatter = (
        "x-common: &common\n  title: Annex\n"
        "attachments:\n  - id: annex\n    file: annex.lgd\n    <<: *common\n"
        "questions:\n  client:\n    type: text\n"
    )
    result = _validate(frontmatter, "{{placeholder: client}} {{attach: annex}}")
    assert "question-invalid" not in result.rules()


@pytest.mark.parametrize("default", ["", " null"])
def test_an_empty_default_is_absent(default):
    frontmatter = f"questions:\n  vat:\n    type: boolean\n    default:{default}\n"
    assert "question-invalid" not in _validate(frontmatter).rules()


def test_a_currency_this_implementation_does_not_know_is_a_valid_answer_form():
    frontmatter = (
        "questions:\n  fee:\n    type: money\n    default:\n"
        "      amount: '10'\n      currency: XYZ\n"
    )
    result = _validate(frontmatter, "{{placeholder: fee, currency=XYZ}}")
    assert "question-invalid" not in result.rules()
    assert "placeholder-unknown-currency" in result.rules("warning")


def test_questions_merged_into_the_root_are_checked_for_block_style():
    frontmatter = "<<: {questions: {q: {type: text}}}\n"
    assert "question-invalid" in _validate(frontmatter, "{{placeholder: q}}").rules("error")


def test_the_serializer_writes_shared_declarations_in_full():
    source = (
        f"---\ntitle: Fixture\n{_SIDES}questions:\n  q: &a\n    type: text\n  r: *a\n---\n"
    )
    written = serialize_document(parse_document(source))
    assert "&" not in written and "*" not in written
    assert parse_document(written).metadata.questions == {"q": {"type": "text"}, "r": {"type": "text"}}


def test_an_invalid_type_on_a_declared_blank_is_one_error():
    result = _validate(_QUESTIONS, "Pay {{placeholder: fee, type=number}}.")
    assert [d.rule for d in result.diagnostics if d.level == "error"] == ["placeholder-type-invalid"]


# ── Inline choices (§15.5) ────────────────────────────────────────

_CHOICES = _QUESTIONS + "  vat:\n    type: boolean\n"


@pytest.mark.parametrize(
    "body",
    [
        'Pay{{choose: vat, true=", plus VAT", false=""}}.',
        "Before {{choose: forum, courts=a court, arbitration=an arbitrator}}.",
        "Before {{choose: forum, arbitration=an arbitrator, courts=a court}}.",
    ],
)
def test_a_choose_listing_every_answer_is_valid(body):
    # The other declared questions are unused here.
    assert _validate(_CHOICES, body).rules() == {"question-unused"}


@pytest.mark.parametrize(
    "body",
    [
        "{{choose: forum, courts=a court}}",  # an answer missing
        "{{choose: vat, true=x, false=y, maybe=z}}",  # not an answer
        "{{choose: forum, courts=a, arbitration=b, note=c}}",
        "{{choose: fee, a=x, b=y}}",  # a value question
        "{{choose: missing, true=x, false=y}}",  # not declared
        "{{choose: vat}}",
    ],
)
def test_a_choose_must_list_exactly_the_answers(body):
    result = _validate(_CHOICES, body)
    assert "choose-invalid" in result.rules("error")
    assert "directive-unknown-param" not in result.rules()


def test_a_choose_repeating_an_answer_is_a_duplicate_parameter():
    result = _validate(_CHOICES, "{{choose: vat, true=x, false=y, true=z}}")
    assert "directive-duplicate-param" in result.rules("error")


def test_a_choose_in_a_heading_or_frontmatter_is_invalid():
    source = (
        f"---\ntitle: Fixture\n{_SIDES}{_CHOICES}"
        'subtitle: "{{choose: vat, true=a, false=b}}"\n---\n\n'
        "# Fees {{choose: vat, true=a, false=b}}\n\nText.\n"
    )
    result = validate_document(parse_document(source))
    messages = [d.message for d in result.diagnostics if d.rule == "choose-invalid"]
    assert len(messages) == 2


def test_braces_in_a_choose_phrase_are_literal():
    result = _validate(_CHOICES, 'A {{choose: vat, true="{{ref: fees}}", false=""}}.')
    assert "brace-stray" in result.rules("warning")


def test_a_choose_makes_the_document_a_template():
    body = '{{choose: vat, true=a, false=b}} {{placeholder: no}}'
    frontmatter = "questions:\n  vat:\n    type: boolean\n"
    assert "question-invalid" in _validate(frontmatter, body).rules("error")


# ── Drafting notes (§15.6) ────────────────────────────────────────


@pytest.mark.parametrize("marker", ["[!DRAFTING]", "[!drafting]", "  [!Drafting]  "])
def test_a_drafting_note_takes_its_marker_in_any_case(marker):
    body = f'>{marker}\n> "Fee" {{{{def: fee}}}} means the fee.\n\nPay the {{{{term: fee}}}}.'
    assert "drafting-note-def" in _validate(body=body).rules("error")


@pytest.mark.parametrize("first_line", ["[!DRAFT]", "[!NOTE]", "[!DRAFTING] Use with care."])
def test_a_look_alike_marker_is_an_ordinary_quote(first_line):
    body = f'> {first_line}\n> "Fee" {{{{def: fee}}}} means the fee.\n\nPay the {{{{term: fee}}}}.'
    result = _validate(body=body)
    assert "drafting-note-unrecognized" in result.rules("warning")
    assert "drafting-note-def" not in result.rules()


def test_a_drafting_note_extends_over_lazy_continuation_lines():
    body = '> [!DRAFTING]\n> Guidance.\n"Fee" {{def: fee}} means the fee.\n\nPay the {{term: fee}}.'
    assert "drafting-note-def" in _validate(body=body).rules("error")


def test_references_in_a_drafting_note_are_checked():
    body = "> [!DRAFTING]\n> See {{ref: nowhere}}."
    assert "ref-broken" in _validate(body=body).rules("error")


# ── Terms, insertions, and fragments (§15.3, §15.5, §15.7.3) ─────


@pytest.mark.parametrize(
    "paragraph",
    [
        'The "{{placeholder: short}}" {{def: client}} is the Client.',
        'The "Client {{choose: vat, true=A, false=B}}" {{def: client}} is the Client.',
    ],
)
def test_a_blank_or_choice_in_a_defined_term_is_an_error(paragraph):
    result = _validate("questions:\n  vat:\n    type: boolean\n", paragraph + " {{term: client}}")
    assert "def-term-variable" in result.rules("error")


def test_a_blank_after_the_defined_term_is_fine():
    body = 'The "Client" {{def: client}} is {{placeholder: short}}. See {{term: client}}.'
    assert "def-term-variable" not in _validate(body=body).rules()


@pytest.mark.parametrize(
    "text",
    [
        "Pay {{placeholder: p}}.",
        "Pay ({{placeholder: p}}), then",
        "“{{placeholder: p}}”",
        "«{{placeholder: p}}»",
        "a/{{placeholder: p}}/b",
        "x-{{placeholder: p}}-y",
        "{{placeholder: p}}{{placeholder: q}}",
        "Smith{{placeholder: p}}son",
        "A&B {{placeholder: p}}",  # the & is not in the run before it
        "[link](url) {{placeholder: p}}",
        "See [link]({{ref: terms}}) and {{placeholder: p}}",
    ],
)
def test_insertions_kept_apart_from_markdown_are_fine(text):
    assert "insertion-boundary" not in _validate(body=text).rules()


@pytest.mark.parametrize(
    "text",
    [
        "AT&{{placeholder: p}}",
        "*{{placeholder: p}}*",
        "_{{placeholder: p}}",
        "[{{placeholder: p}}]",
        "<{{placeholder: p}}",
        "a\\b{{placeholder: p}}",
        "a&b{{placeholder: p}}",
        "x]({{placeholder: p}}",
        "[text]({{placeholder: p}})",
        "[text](https://x.test/{{placeholder: p}})",
        "![alt](img/{{placeholder: p}}.png)",
        "{{placeholder: p}}*",
        "{{placeholder: p}}]",
        "[label]: {{placeholder: p}}",
        "`{{placeholder: p}}",
    ],
)
def test_insertions_touching_markdown_punctuation_are_errors(text):
    assert "insertion-boundary" in _validate(body=text).rules("error")


@pytest.mark.parametrize(
    "body",
    [
        "- {{placeholder: p}} is due.",
        "1. {{placeholder: p}} is due.",
        "> {{placeholder: p}} is due.",
        "| A | B |\n|---|---|\n| {{placeholder: p}} | x |",
    ],
)
def test_container_content_starts_are_line_starts(body):
    assert "insertion-boundary" not in _validate(body=body).rules()


def test_choose_insertions_are_checked_too():
    result = _validate(_CHOICES, "*{{choose: vat, true=a, false=b}}")
    assert "insertion-boundary" in result.rules("error")


_TEMPLATE = "questions:\n  vat:\n    type: boolean\n"


def test_a_fragment_is_included_once_in_a_template():
    body = "{{include: parts/a.lgd}}\n\n{{include: ./parts/a.lgd}}"
    assert "template-fragment-invalid" in _validate(_TEMPLATE, body).rules("error")
    assert "template-fragment-invalid" not in _validate(body=body).rules()


def test_an_include_in_a_templates_drafting_note_is_invalid():
    body = "> [!DRAFTING]\n> {{include: parts/a.lgd}}"
    assert "template-fragment-invalid" in _validate(_TEMPLATE, body).rules("error")
    assert "template-fragment-invalid" not in _validate(body=body).rules()


@pytest.mark.parametrize(
    ("attachments", "body"),
    [
        (
            "  - id: a\n    title: A\n    file: s.lgd\n  - id: b\n    title: B\n    file: ./s.lgd\n",
            "{{attach: a}} {{attach: b}}",
        ),
        ("  - id: a\n    title: A\n    file: parts/s.lgd\n", "{{attach: a}}\n\n{{include: parts/s.lgd}}"),
    ],
)
def test_an_attachment_file_has_one_place_in_a_template(attachments, body):
    frontmatter = _TEMPLATE + "attachments:\n" + attachments
    assert "template-fragment-invalid" in _validate(frontmatter, body).rules("error")


# ── The final check (§15.9) ───────────────────────────────────────


def test_the_final_check_rejects_blanks_and_template_constructs():
    frontmatter = (
        'subtitle: "For {{placeholder: client}}"\n' + _CHOICES
        + "attachments:\n  - id: dpa\n    title: DPA\n    file: dpa.pdf\n    when: vat\n"
    )
    body = (
        "Pay {{placeholder: fee, currency=EUR}}{{choose: vat, true=a, false=b}} under "
        "{{attach: dpa}}.\n\n> [!DRAFTING]\n> Check the fee."
    )
    source = f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n# Terms {{#terms}}\n\n{body}\n"
    document = parse_document(source)
    assert not {"placeholder-unfilled", "template-construct-present"} & validate_document(document).rules()
    final = [d.rule for d in validate_document(document, final=True).diagnostics]
    assert final.count("placeholder-unfilled") == 2
    # questions, the attachment condition, the choose, the drafting note
    assert final.count("template-construct-present") == 4


def test_a_final_document_passes_the_final_check():
    result = validate_document(
        parse_document(f"---\ntitle: Fixture\n{_SIDES}---\n\n# Terms\n\nText.\n"), final=True
    )
    assert result.diagnostics == []


# ── Lazy continuation lines (CommonMark) ──────────────────────────


def test_a_lazy_line_continues_its_list_item():
    document = parse_document("---\ntitle: T\n---\n\n# A\n\n- item\ncontinued {#item}\n")
    assert [b.items for b in document.sections[0].blocks] == [["item continued {#item}"]]
    result = validate_document(document)
    assert "anchor-misplaced" not in result.rules()
    assert "item" in result.section_lookup


def test_a_lazy_line_continues_its_quote():
    document = parse_document("---\ntitle: T\n---\n\n# A\n\n> quoted\ncontinued\n\nAfter.\n")
    assert [(b.kind, b.text) for b in document.sections[0].blocks] == [
        ("quote", "quoted\ncontinued"),
        ("paragraph", "After."),
    ]
    assert parse_document(serialize_document(document)).sections == document.sections


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("- item\n---\n", ["unordered_list", "rule"]),
        ("- item\n# B\n", ["unordered_list"]),
        ("> quote\n- item\n", ["quote", "unordered_list"]),
        ("> # Heading\ntext\n", ["quote", "paragraph"]),
        (">\ntext\n", ["quote", "paragraph"]),
    ],
)
def test_block_starts_are_not_lazy_lines(body, kinds):
    document = parse_document(f"---\ntitle: T\n---\n\n# A\n\n{body}")
    assert [b.kind for b in document.sections[0].blocks] == kinds


def test_a_directive_in_a_leading_defined_term_stays_checked():
    body = '"{{placeholder: short}}" {{def: client}} means the Client. See {{term: client}}.'
    document = parse_document(f"---\ntitle: T\n{_SIDES}---\n\n# Terms\n\n{body}\n")
    assert document.sections[0].blocks[0].kind != "definition"
    assert "def-term-variable" in validate_document(document).rules("error")


# ── Review follow-ups (PR B) ──────────────────────────────────────


@pytest.mark.parametrize("quoted", ["> ```\n> code\n", ">     code\n"])
def test_a_line_after_quoted_code_is_not_lazy(quoted):
    document = parse_document(f"---\ntitle: T\n---\n\n# A\n\n{quoted}plain text\n")
    assert [b.kind for b in document.sections[0].blocks] == ["quote", "paragraph"]


@pytest.mark.parametrize(
    "paragraph",
    [
        'The "{{placeholder: name, note="Party name"}}" {{def: party}} is here.',
        'The "{{choose: vat, true="The Court", false="The Tribunal"}}" {{def: forum}} is here.',
    ],
)
def test_def_term_variable_sees_through_quoted_values(paragraph):
    result = _validate("questions:\n  vat:\n    type: boolean\n", paragraph)
    assert "def-term-variable" in result.rules("error")


def test_the_final_check_covers_headings():
    source = f"---\ntitle: T\n{_SIDES}---\n\n# Lease of {{{{placeholder: premises}}}}\n\nText.\n"
    result = validate_document(parse_document(source), final=True)
    assert "placeholder-unfilled" in result.rules("error")


@pytest.mark.parametrize(
    ("body", "kinds"),
    [
        ("- \nlazy text\n", ["unordered_list", "paragraph"]),
        ("> quote\n<!-- editor note -->\n", ["quote", "html"]),
    ],
)
def test_lines_that_cannot_continue_a_paragraph_are_not_lazy(body, kinds):
    document = parse_document(f"---\ntitle: T\n---\n\n# A\n\n{body}")
    assert [b.kind for b in document.sections[0].blocks] == kinds


def test_a_drafting_note_in_a_list_item_is_recognized():
    body = (
        '- item\n  > [!DRAFTING]\n  > "Fee" {{def: fee}} means the fee.\n'
        "- other\n  > [!DRAFT]\n  > guidance\n\nPay the {{term: fee}}."
    )
    document = parse_document(f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n{body}\n")
    result = validate_document(document, final=True)
    assert "drafting-note-def" in result.rules("error")
    assert "drafting-note-unrecognized" in result.rules("warning")
    assert "template-construct-present" in result.rules("error")
    assert parse_document(serialize_document(document)).sections == document.sections


def test_a_quote_whose_first_line_is_blank_is_not_a_drafting_note():
    body = '>\n> [!DRAFTING]\n> "Fee" {{def: fee}} means the fee.\n\nPay the {{term: fee}}.'
    document = parse_document(f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n{body}\n")
    assert not {"drafting-note-def", "template-construct-present"} & validate_document(
        document, final=True
    ).rules()
    assert parse_document(serialize_document(document)).sections == document.sections


@pytest.mark.parametrize(
    "text",
    ["Name: <!-- n -->{{placeholder: p}}.", "Name: a<!--n-->b{{placeholder: p}}.", "Name: `a&`b{{placeholder: p}}."],
)
def test_comments_and_code_are_template_text_at_a_boundary(text):
    assert "insertion-boundary" in _validate(body=text).rules("error")


def test_a_lazy_line_after_a_quote_in_a_list_item_stays_in_the_quote():
    body = '- item\n  > [!DRAFTING]\n  > note\n"Fee" {{def: fee}} means the fee.\n\nPay the {{term: fee}}.'
    document = parse_document(f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n{body}\n")
    assert "drafting-note-def" in validate_document(document).rules("error")
    assert parse_document(serialize_document(document)).sections == document.sections


# ── Quote extents, one scanner for every quote (second review) ────

_DEF_LINE = '"Fee" {{def: fee}} means money.'


@pytest.mark.parametrize(
    ("body", "in_note"),
    [
        # A closed quote paragraph: the line ends the list and stands alone.
        (f"- Item\n  > [!DRAFTING]\n  >\n{_DEF_LINE}", False),
        # An indented line continues the item's quote paragraph.
        (f"- Item\n  > [!DRAFTING]\n  > note line\n  {_DEF_LINE}", True),
        # A lazy line continues a list item's or a nested quote's paragraph.
        (f"> [!DRAFTING]\n> - keep twelve\n{_DEF_LINE}", True),
        (f"> [!DRAFTING]\n> > nested\n{_DEF_LINE}", True),
        # A heading or quoted code holds no paragraph to continue.
        (f"> [!DRAFTING]\n> # Heading\n{_DEF_LINE}", False),
        (f"> [!DRAFTING]\n> ```\n> code\n> ```\n{_DEF_LINE}", False),
    ],
)
def test_a_drafting_note_extends_as_its_quote_does(body, in_note):
    document = parse_document(f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n{body}\n\nPay the {{{{term: fee}}}}.\n")
    assert ("drafting-note-def" in validate_document(document).rules()) == in_note
    assert parse_document(serialize_document(document)).sections == document.sections


def test_quote_lines_in_an_items_code_are_code():
    body = "- Example:\n  ```\n  > [!NOTE]\n  > [!DRAFTING]\n  ```"
    result = validate_document(
        parse_document(f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n{body}\n"), final=True
    )
    assert not {"drafting-note-unrecognized", "template-construct-present"} & result.rules()


def test_blanks_in_a_drafting_note_are_never_inserted():
    body = "> [!DRAFTING]\n> Use **{{placeholder: x}}** here."
    assert "insertion-boundary" not in _validate(body=body).rules()


def test_a_repeated_answer_is_choose_invalid():
    result = _validate(_CHOICES, '{{choose: forum, courts="a", courts="b", arbitration="c"}}')
    assert {"choose-invalid", "directive-duplicate-param"} <= result.rules("error")


def test_an_empty_questions_key_is_a_template_construct():
    document = parse_document(f"---\ntitle: T\n{_SIDES}questions:\n---\n\n# A\n\nText.\n")
    assert document.metadata.questions == {}
    assert "template-construct-present" in validate_document(document, final=True).rules()


# ── Third review ──────────────────────────────────────────────────


@pytest.mark.parametrize("first", ["> # T", ">", "> ---", "> <!-- c -->"])
def test_an_items_text_after_a_closed_quote_is_not_quoted(first):
    document = parse_document(f"---\ntitle: T\n---\n\n# A\n\n- {first}\n  text after quote\n")
    item = document.sections[0].blocks[0].items[0]
    assert item.split("\n")[-1] == "text after quote"
    assert parse_document(serialize_document(document)).sections == document.sections


@pytest.mark.parametrize(
    ("text", "flagged"),
    [
        ("[label]: https://example.com The Client is {{placeholder: client}}.", False),
        ("[label]: https://example.com/{{placeholder: path}}", True),
        ('[label]: https://example.com "Title {{placeholder: t}}"', True),
    ],
)
def test_only_a_link_reference_definition_itself_is_off_limits(text, flagged):
    assert ("insertion-boundary" in _validate(body=text).rules()) == flagged


def test_insertions_in_headings_are_checked():
    source = f"---\ntitle: T\n{_SIDES}---\n\n# Agreement {{{{placeholder: party}}}}(x)\n\nText.\n"
    assert "insertion-boundary" in validate_document(parse_document(source)).rules("error")


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ("> > [!DRAFTING]\n> > note", "template-construct-present"),
        ("> > [!NOTE]\n> > note", "drafting-note-unrecognized"),
        ("- > > [!NOTE]\n  > > note", "drafting-note-unrecognized"),
    ],
)
def test_nested_quotes_are_checked(body, rule):
    source = f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n{body}\n"
    assert rule in validate_document(parse_document(source), final=True).rules()


def test_a_nested_drafting_note_holds_no_definition():
    body = '> Quoted.\n>\n> > [!DRAFTING]\n> > "Fee" {{def: fee}} means money.\n\nPay the {{term: fee}}.'
    assert "drafting-note-def" in _validate(body=body).rules("error")


def test_a_setext_heading_may_follow_a_quote_with_no_open_paragraph():
    body = "> [!DRAFTING]\n> ```\n> code\n> ```\nClause heading\n--------------\n\nText.\n"
    document = parse_document(f"---\ntitle: T\n---\n\n# A\n\n{body}")
    assert [s.title for s in document.sections] == ["A", "Clause heading"]


def test_malformed_choices_draw_no_choose_errors():
    frontmatter = "questions:\n  forum:\n    type: choice\n    choices:\n      Courts: A\n      Arb: B\n"
    result = _validate(frontmatter, "{{choose: forum, courts=a, arb=b}}")
    assert "question-invalid" in result.rules()
    assert "choose-invalid" not in result.rules()


def test_a_malformed_choose_still_makes_a_template():
    source = f"---\ntitle: T\n{_SIDES}---\n\n# A\n\nPay {{{{choose: vat, true=x\n"
    result = validate_document(parse_document(source), final=True)
    assert {"directive-malformed", "template-construct-present"} <= result.rules("error")


# ── Fourth review ─────────────────────────────────────────────────


def test_a_paragraph_holding_a_blank_is_checked_whole():
    """Not split around a lifted {{ref:}}: the `&` before it is in the run."""
    assert "insertion-boundary" in _validate(body="See x &{{ref: terms}}{{placeholder: x}} now.").rules()


def test_an_item_anchor_may_precede_a_note_in_the_item():
    body = "- Pay the fee. {#fee}\n  > [!DRAFTING]\n  > Negotiable.\n\nSee {{ref: fee}}."
    result = _validate(body=body)
    assert not {"anchor-misplaced", "ref-broken"} & result.rules()


@pytest.mark.parametrize(
    "continuation",
    ["<https://intranet/policy> and", "<b>bold</b> text and"],
)
def test_an_autolink_or_inline_tag_continues_a_drafting_note(continuation):
    body = f'> [!DRAFTING]\n> See the policy at\n{continuation} "Foo" {{{{def: foo}}}} x\n\nUse {{{{term: foo}}}}.'
    assert "drafting-note-def" in _validate(body=body).rules("error")


@pytest.mark.parametrize("html", ["<div>", "<!-- note -->", "</table>"])
def test_an_html_block_start_ends_a_quote(html):
    document = parse_document(f"---\ntitle: T\n---\n\n# A\n\n> quote\n{html}\n")
    assert [b.kind for b in document.sections[0].blocks] == ["quote", "html"]


def test_text_after_a_link_reference_label_is_a_paragraph():
    assert "insertion-boundary" not in _validate(body="[Note]: {{placeholder: x}} shall pay.").rules()
