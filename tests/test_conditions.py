"""Specification 0.2: conditions, alternatives, and reference safety
(§5.5, §5.7, §15.3, §15.4)."""
from __future__ import annotations

import pytest

from legaldown import serialize_document
from legaldown.markers import Marker, parse_marker, split_heading
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
_QUESTIONS = """questions:
  vat:
    type: boolean
  forum:
    type: choice
    choices:
      courts: State courts
      arbitration: ICC arbitration
"""


def _parse(body: str, frontmatter: str = _QUESTIONS):
    return parse_document(f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n{body}\n")


def _rules(body: str, frontmatter: str = _QUESTIONS, **options) -> set[str]:
    return validate_document(_parse(body, frontmatter), **options).rules()


# ── Markers (§15.3) ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        ("{#a}", Marker("a", "")),
        ("{when=q}", Marker("", "q")),
        ("{#a when=!q:v}", Marker("a", "!q:v")),
        ("{when=q #a}", Marker("a", "q")),
        ("{#a #b}", None),  # an attribute twice
        ("{when=q when=r}", None),
        ("{#a class=x}", None),  # not an attribute
    ],
)
def test_parse_marker(text, marker):
    assert parse_marker(text) == marker


def test_a_heading_marker_is_split_from_its_text():
    assert split_heading("Non-Solicitation {#non-solicit when=non-solicit}") == (
        "Non-Solicitation",
        Marker("non-solicit", "non-solicit"),
    )
    assert split_heading("Scope {when=vat}") == ("Scope", Marker("", "vat"))
    assert split_heading("Scope {#a #b}") == ("Scope {#a #b}", Marker())


def test_a_conditional_heading_round_trips_and_its_identifier_is_generated():
    document = _parse("# Services {when=vat}\n\nText.")
    section = document.sections[0]
    assert (section.title, section.identifier, section.condition) == ("Services", "", "vat")
    assert "\n# Services {when=vat}\n" in serialize_document(document)
    result = validate_document(document)
    assert result.sections[0].identifier == "services"
    assert document.sections[0].identifier == ""  # validation leaves the document as it was


# ── condition-invalid (§15.3) ─────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "# A {when=missing}\n\nText.",  # not declared
        "# A {when=vat:yes}\n\nText.",  # a value on a boolean
        "# A {when=forum}\n\nText.",  # no value on a choice
        "# A {when=forum:mediation}\n\nText.",  # not a declared choice
        "# A {when=Forum}\n\nText.",  # not in the identifier format
        "# A\n\nText. {when=missing}",
        "# A\n\n- item {#i when=missing}",
    ],
)
def test_an_invalid_condition_is_reported(body):
    assert "condition-invalid" in _rules(body)


def test_an_attachment_condition_is_checked():
    frontmatter = _QUESTIONS + "attachments:\n  - id: dpa\n    title: DPA\n    file: dpa.pdf\n    when: missing\n"
    assert "condition-invalid" in _rules("# A\n\nSee {{attach: dpa}}.", frontmatter)


def test_valid_conditions_are_valid():
    body = (
        "# A {when=vat}\n\nText. {when=forum:courts}\n\n- item {#i when=!forum:arbitration}\n"
        "- other {when=forum:courts}"
    )
    assert _rules(body) == set()


# ── condition-never-true (§15.4) ──────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "# A {when=vat}\n\n## B {when=!vat}\n\nText.",
        "# A {when=forum:courts}\n\n- item {#i when=forum:arbitration}",
        "# A {when=forum:courts}\n\nText. {when=!forum:courts}",
    ],
)
def test_a_unit_that_can_never_appear_is_a_warning(body):
    assert "condition-never-true" in _rules(body)


def test_only_the_contradicting_unit_is_reported():
    body = "# A {when=vat}\n\n## B {when=!vat}\n\n### C {when=forum:courts}\n\nText."
    result = validate_document(_parse(body))
    assert [d.rule for d in result.diagnostics].count("condition-never-true") == 1


def test_compatible_conditions_are_fine():
    body = "# A {when=vat}\n\n## B {when=forum:courts}\n\nText. {when=!forum:arbitration}"
    assert "condition-never-true" not in _rules(body)


# ── Alternatives (§15.4) ──────────────────────────────────────────


def test_alternatives_may_share_an_identifier():
    body = (
        "# Disputes {#disputes when=forum:courts}\n\nCourts.\n\n"
        "# Disputes {#disputes when=forum:arbitration}\n\nArbitration.\n\n"
        "# Relief\n\nSee {{ref: disputes}}."
    )
    assert _rules(body) == {"question-unused"}  # vat is declared but not used here


def test_declarations_that_can_appear_together_may_not():
    body = (
        "# Disputes {#disputes when=forum:courts}\n\nCourts.\n\n"
        "# Disputes {#disputes when=vat}\n\nVAT."
    )
    assert "anchor-duplicate" in _rules(body)


@pytest.mark.parametrize(
    "body",
    [
        "# A\n\n- one {#x when=vat}\n- two {#x when=!vat}",
        "# A\n\nOne. {#x when=vat}\n\nTwo. {#x when=!vat}",
    ],
)
def test_item_and_paragraph_alternatives_may_share_an_anchor(body):
    assert "anchor-duplicate" not in _rules(body)


def test_alternative_definitions_may_share_an_identifier():
    body = (
        '# A {when=vat}\n\n"Fee" {{def: fee}} means the fee with VAT.\n\n'
        '# B {when=!vat}\n\n"Fee" {{def: fee}} means the fee.\n\n'
        "# C\n\nPay the {{term: fee}}."
    )
    assert not {"def-duplicate-id", "condition-reference-unsafe"} & _rules(body)


def test_alternative_attachments_may_share_an_id():
    frontmatter = _QUESTIONS + (
        "attachments:\n"
        "  - id: dpa\n    title: DPA\n    file: dpa-a.pdf\n    when: vat\n"
        "  - id: dpa\n    title: DPA\n    file: dpa-b.pdf\n    when: '!vat'\n"
    )
    assert not {"attachment-id-duplicate", "condition-reference-unsafe"} & _rules(
        "# A\n\nSee {{attach: dpa}}.", frontmatter
    )


# ── Generated identifiers (§5.5) ──────────────────────────────────


def test_headings_that_never_appear_together_do_not_collide():
    body = "# Services {when=vat}\n\nA.\n\n# Services {when=!vat}\n\nB."
    result = validate_document(_parse(body))
    assert [entry.identifier for entry in result.sections] == ["services", "services"]
    assert "anchor-autogen-collision" not in result.rules()


def test_an_explicit_identifier_always_wins_even_later():
    body = "# Scope\n\nA.\n\n# Other {#scope}\n\nB."
    result = validate_document(_parse(body, ""))
    assert [entry.identifier for entry in result.sections] == ["scope-2", "scope"]
    assert "anchor-autogen-collision" in result.rules("warning")


def test_two_generated_identifiers_collide_as_a_warning():
    result = validate_document(_parse("# Scope\n\nA.\n\n# Scope\n\nB.", ""))
    assert [entry.identifier for entry in result.sections] == ["scope", "scope-2"]
    assert result.rules() == {"anchor-autogen-collision"}


# ── Reference safety (§15.4) ──────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "unsafe"),
    [
        ("# A {#a when=vat}\n\nText.\n\n# B\n\nSee {{ref: a}}.", True),
        ("# A {#a when=vat}\n\nText.\n\n# B\n\nSee {{ref: a}}. {when=vat}", False),
        ("# A {#a when=vat}\n\nSee {{ref: a}}.", False),
        ("# A {#a when=forum:courts}\n\nText.\n\n# B\n\nSee {{ref: a}}. {when=!forum:arbitration}", False),
        ("# A\n\n- item {#i when=vat}\n\n# B\n\nSee {{ref: i}}.", True),
        ("# A {#a when=vat}\n\nText.\n\n# B\n\n> [!DRAFTING]\n> See {{ref: a}}.", False),
    ],
)
def test_a_reference_must_resolve_whenever_it_is_present(body, unsafe):
    assert ("condition-reference-unsafe" in _rules(body)) == unsafe


def test_terms_and_attachments_are_checked_too():
    frontmatter = _QUESTIONS + "attachments:\n  - id: dpa\n    title: DPA\n    file: dpa.pdf\n    when: vat\n"
    body = '# A {when=vat}\n\n"Fee" {{def: fee}} means the fee.\n\n# B\n\nPay the {{term: fee}} under {{attach: dpa}}.'
    result = validate_document(_parse(body, frontmatter))
    messages = [d.message for d in result.diagnostics if d.rule == "condition-reference-unsafe"]
    assert len(messages) == 2


# ── Misplaced markers (§5.7, §12.2, §15.3) ────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "# A\n\nText {when=vat} and more.",
        "# A\n\n> Quoted. {when=vat}",
        "# A\n\n| x | y |\n|---|---|\n| a {when=vat} | b |",
        "# A\n\nText. {#a #b}",
        "# A {#a #b}\n\nText.",
    ],
)
def test_a_marker_out_of_place_is_literal(body):
    assert "anchor-misplaced" in _rules(body)


def test_a_preamble_condition_needs_a_template():
    assert "anchor-misplaced" not in _rules("Preamble. {when=vat}\n\n# A\n\nText.")
    assert "anchor-misplaced" in _rules("Preamble. {when=vat}\n\n# A\n\nText.", "")
    assert "anchor-misplaced" in _rules("Preamble. {#p when=vat}\n\n# A\n\nText.")


def test_an_include_paragraphs_condition_applies_and_its_id_is_ignored():
    rules = _rules("# A\n\n{{include: part.lgd}} {#part when=nonsense}")
    assert {"anchor-misplaced", "condition-invalid"} <= rules


# ── Templates and the final check ─────────────────────────────────


def test_a_condition_alone_makes_a_template():
    # The only template construct is a condition: undeclared, so invalid.
    assert "condition-invalid" in _rules("# A {when=vat}\n\nText.", "")


def test_the_final_check_reports_conditions():
    body = "# A {when=vat}\n\nText. {when=!vat}\n\n- item {#i when=vat}"
    result = validate_document(_parse(body), final=True)
    rules = [d.rule for d in result.diagnostics]
    # the questions key and three conditions
    assert rules.count("template-construct-present") == 4


# ── question-unused (§15.2) ───────────────────────────────────────


def test_a_question_used_by_a_condition_is_used():
    body = "# A {when=vat}\n\nText {{choose: forum, courts=a, arbitration=b}}."
    assert "question-unused" not in _rules(body)


def test_an_unused_question_is_a_warning():
    assert "question-unused" in _rules("# A {when=vat}\n\nText.")


def test_a_question_may_be_used_in_a_fragment_the_validator_does_not_read():
    assert "question-unused" not in _rules("# A\n\n{{include: parts/a.lgd}}")


def test_a_unit_that_can_never_appear_is_reported_once():
    body = "# A {when=vat}\n\n- item {when=!vat} and {when=!vat}"
    result = validate_document(_parse(body))
    assert [d.rule for d in result.diagnostics].count("condition-never-true") == 1


# ── Review follow-ups ─────────────────────────────────────────────

_ALTERNATIVE_ATTACHMENTS = _QUESTIONS + (
    "attachments:\n"
    "  - id: sched\n    title: Schedule\n    file: cz.lgd\n    when: forum:courts\n"
    "  - id: sched\n    title: Schedule\n    file: uk.lgd\n    when: forum:arbitration\n"
)


def _with_attachment_definitions(body: str):
    return validate_document(
        _parse(body, _ALTERNATIVE_ATTACHMENTS),
        import_attachment_definitions=lambda path: {"fee": f"Fee ({path})"},
    )


def test_alternative_attachments_may_define_the_same_term():
    result = _with_attachment_definitions("# A\n\nSee {{attach: sched}}. {when=forum:courts}")
    assert "def-duplicate-id" not in result.rules()


def test_a_term_from_a_conditional_attachment_is_checked_for_safety():
    covered = _with_attachment_definitions(
        "# A\n\nSee {{attach: sched}} and the {{term: fee}}. {when=vat}"
    )
    assert "condition-reference-unsafe" not in covered.rules()  # one of the two is always present
    frontmatter = _QUESTIONS + "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n    when: vat\n"
    result = validate_document(
        _parse("# A\n\nSee {{attach: s}}. {when=vat}\n\nThe {{term: fee}}.", frontmatter),
        import_attachment_definitions=lambda path: {"fee": "Fee"},
    )
    assert "condition-reference-unsafe" in result.rules("error")


@pytest.mark.parametrize("heading", ["# Fees \\{#x}", '# Fees {{placeholder: p, note="{#x}"}}'])
def test_escaped_or_quoted_markers_in_a_heading_are_not_look_alikes(heading):
    assert "anchor-misplaced" not in _rules(f"{heading}\n\nText.", "")


def test_a_term_from_the_amended_original_is_always_present():
    frontmatter = _QUESTIONS + (
        "amends:\n  title: Original\n  file: original.lgd\n"
        "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n    when: vat\n"
    )
    result = validate_document(
        _parse("# A\n\nSee {{attach: s}}. {when=vat}", frontmatter),
        import_definitions=lambda *_: {"fee": "Fee"},
        import_attachment_definitions=lambda path: {"fee": "Fee"},
    )
    assert "def-duplicate-id" in result.rules("error")


def test_a_question_used_by_a_placeholder_in_a_heading_is_used():
    frontmatter = "questions:\n  name:\n    type: text\n"
    assert "question-unused" not in _rules("# Fees for {{placeholder: name}}\n\nText.", frontmatter)


def test_a_preamble_list_item_carries_no_condition():
    assert "anchor-misplaced" in _rules("- item {when=vat}\n\n# A\n\nText.")


def test_a_term_redefined_under_a_condition_stays_defined_by_the_original():
    frontmatter = _QUESTIONS + "amends:\n  title: Original\n  file: original.lgd\n"
    body = '# A\n\n"Services" {{def: services}} means x. {when=vat}\n\nWe provide {{term: services}}.'
    result = validate_document(_parse(body, frontmatter), import_definitions=lambda *_: {"services": "Services"})
    assert "condition-reference-unsafe" not in result.rules()


def test_a_preamble_list_items_condition_is_explained():
    result = validate_document(_parse("- item {when=vat}\n\n# A\n\nText."))
    [message] = [d.message for d in result.diagnostics if d.rule == "anchor-misplaced"]
    assert "only a paragraph may carry a condition" in message


def test_alternative_attachments_are_unreferenced_once():
    result = validate_document(_parse("# A\n\nText.", _ALTERNATIVE_ATTACHMENTS))
    assert [d.rule for d in result.diagnostics].count("attachment-unreferenced") == 1


def test_a_comment_may_follow_a_heading_marker():
    document = _parse("# Termination {when=vat} <!-- optional -->\n\nText.")
    [section] = document.sections
    assert (section.title, section.condition) == ("Termination <!-- optional -->", "vat")
    assert parse_document(serialize_document(document)).sections[0].condition == "vat"


def test_a_dotted_path_is_not_a_reference():
    assert "ref-broken" in _rules("# A\n\n## B\n\nSee {{ref: a.b}}.", "")


def test_a_comment_in_a_heading_is_not_part_of_its_identifier():
    result = validate_document(_parse("# Title {when=vat} <!-- internal note -->\n\nSee {{ref: title}}."))
    assert result.sections[0].identifier == "title"
    assert "ref-broken" not in result.rules()


def test_prose_in_braces_is_not_a_marker_look_alike():
    assert "anchor-misplaced" not in _rules("# A\n\nIssues {#1 and #2} were fixed. Use {# comment } here.", "")


def test_alternatives_share_a_number():
    body = (
        "# Disputes {#disputes when=forum:courts}\n\n## Venue\n\nText.\n\n"
        "# Disputes {#disputes when=forum:arbitration}\n\n## Seat\n\nText.\n\n# Notices\n\nText."
    )
    result = validate_document(_parse(body))
    assert [s.number for s in result.sections] == ["1", "1.1", "1", "1.1", "2"]
