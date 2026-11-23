"""A directive where §11.4 does not recognize one asks nothing and is no blank, as assembly leaves it as written
(#12): a ``{{choose:}}`` in frontmatter (§15.5), a ``{{placeholder:}}`` in a frontmatter field that takes none or
in a value that is not a quoted YAML string (§3.10), and either in a section's heading (§4.2), which no insertion
reaches either (§15.7.3). (The heading's own cases are in ``test_hidden_constructs.py``.)"""
from __future__ import annotations

from legaldown import Template, parse, validate
from legaldown.grammar import FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE, IMPLEMENTATION_RULE_PREFIX

_SIDES = (
    "document_type: contract\nsides:\n"
    "  - name: providers\n    parties:\n      - name: acme\n        type: legal_entity\n        legal_name: Acme\n"
    "  - name: clients\n    parties:\n      - name: beta\n        type: legal_entity\n        legal_name: Beta\n"
)
_Q = "questions:\n  q:\n    type: text\n"
_X = "questions:\n  x:\n    type: boolean\n"


def _source(fields: str, questions: str = "", body: str = "# A\n\nText.\n") -> str:
    return f"---\n{fields}{_SIDES}{questions}---\n\n{body}"


def _unused(text: str) -> set[str]:
    """The questions validate reports question-unused."""
    return {
        d.message.split("'")[1] for d in validate(parse(text)).diagnostics if d.rule == "question-unused"
    }


def _rules(text: str, **options) -> list[str]:
    return [d.rule for d in validate(parse(text), **options).diagnostics]


def test_the_id_is_this_implementations_own():
    assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE == "ldv-frontmatter-placeholder-unquoted"
    assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE.startswith(IMPLEMENTATION_RULE_PREFIX)


# ── A {{choose:}} in frontmatter ────────────────────────────────────────────────────────────────────────────

def test_a_question_only_a_frontmatter_choose_names_is_unused():
    text = _source('title: "Lease {{choose: x, true=\\"A\\", false=\\"B\\"}}"\n', _X)
    assert "choose-invalid" in _rules(text)
    assert _unused(text) == {"x"}


def test_a_body_choose_still_asks():
    text = _source('title: "Lease {{choose: x, true=\\"A\\", false=\\"B\\"}}"\n', _X,
                   '# A\n\nPay {{choose: x, true="now", false="later"}}.\n')
    assert _unused(text) == set()


# ── A placeholder in a field that takes none ─────────────────────────────────────────────────────────────────

def test_a_placeholder_in_a_structural_field_asks_nothing_and_is_no_blank():
    text = _source('title: T\namends:\n  title: Original\n  file: "{{placeholder: q}}.lgd"\n', _Q)
    assert "placeholder-in-structural-field" in _rules(text)
    assert _unused(text) == {"q"}
    final = _rules(text, final=True)
    assert "placeholder-in-structural-field" in final and "placeholder-unfilled" not in final


# ── A placeholder in a value that is not a quoted string ─────────────────────────────────────────────────────

def test_an_unquoted_placeholder_is_reported_asks_nothing_and_is_no_blank():
    text = _source("title: |\n  Lease {{placeholder: q}}\n", _Q)
    result = validate(parse(text))
    (found,) = [d for d in result.diagnostics if d.rule == FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE]
    assert (found.level, found.line) == ("error", 2)
    assert "'{{placeholder: q}}'" in found.message and "§3.10" in found.message
    assert _unused(text) == {"q"}
    final = _rules(text, final=True)
    assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE in final and "placeholder-unfilled" not in final
    # ... as assembly agrees: it leaves the value as written.
    assembled = Template(text).form({"q": "Main St"}).assemble()
    assert "Lease {{placeholder: q}}" in assembled.output


def test_a_plain_and_a_folded_scalar_are_unquoted_too():
    for fields in ("title: Lease {{placeholder:q}}\n", "title: T\ngoverning_law: >-\n  Law of {{placeholder: q}}\n"):
        text = _source(fields, _Q)
        assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE in _rules(text), fields
        assert _unused(text) == {"q"}, fields


def test_a_quoted_placeholder_is_a_blank_as_assembly_fills_it():
    for fields in (
        'title: "Lease {{placeholder: q}}"\n',
        "title: 'Lease {{placeholder: q}}'\n",
        'title: "Lease of\n  {{placeholder: q}}"\n',  # a quoted string over two lines
    ):
        text = _source(fields, _Q)
        assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE not in _rules(text), fields
        assert _unused(text) == set(), fields
        assert "placeholder-unfilled" in _rules(text, final=True), fields
    assembled = Template(_source('title: "Lease {{placeholder: q}}"\n', _Q)).form({"q": "Main St"}).assemble()
    assert 'title: "Lease Main St"' in assembled.output


def test_party_fields_are_judged_by_their_own_quotes_in_block_and_flow_style():
    text = _source("title: T\n", _Q).replace(
        "legal_name: Acme\n", 'legal_name: "{{placeholder: q}}"\n        address: Street {{placeholder:q}}\n'
    )
    result = validate(parse(text))
    lines = [d.line for d in result.diagnostics if d.rule == FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE]
    assert lines == [text.split("\n").index("        address: Street {{placeholder:q}}") + 1]
    assert _unused(text) == set()  # the quoted one asks
    flow = _source("title: T\n", _Q).replace(
        "      - name: beta\n        type: legal_entity\n        legal_name: Beta\n",
        '      - {name: beta, type: legal_entity, legal_name: "{{placeholder: q}}"}\n',
    )  # (in flow style an unquoted {{ is no valid YAML at all)
    assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE not in _rules(flow) and _unused(flow) == set()
    assert "legal_name: \"Main St\"" in Template(flow).form({"q": "Main St"}).assemble().output


def test_an_unquoted_placeholders_type_is_still_checked_against_the_blank():
    # Assembly reads it for the blank's type, though it does not fill it.
    text = _source("title: Lease {{placeholder:q, type=date}}\n", body="# A\n\nPay {{placeholder: q}}.\n")
    assert "placeholder-type-inconsistent" in _rules(text)


def test_a_question_a_quoted_frontmatter_choose_names_says_so():
    text = _source('title: "Lease {{choose: x, true=\\"A\\", false=\\"B\\"}}"\n', _X)
    (message,) = [d.message for d in validate(parse(text)).diagnostics if d.rule == "question-unused"]
    assert "in a {{choose:}} in frontmatter (§15.5)" in message


def test_frontmatter_too_deep_to_read_again_is_still_a_diagnostic():
    text = "---\na: " + "[" * 3000 + "\n---\n\n# A\n"
    document = parse(text, on_frontmatter_error="keep")
    assert "frontmatter-invalid-yaml" in validate(document).rules()


def test_a_document_built_in_code_has_no_quotes_to_judge():
    from dataclasses import replace

    document = parse(_source('title: "Lease {{placeholder: q}}"\n', _Q))
    assert FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE not in validate(replace(document, source_map=None)).rules()


# ── An insertion in a section's heading ──────────────────────────────────────────────────────────────────────

def test_no_insertion_boundary_is_judged_in_a_section_heading():
    # No answer is inserted there (§11.4): the heading keeps the placeholder as written.
    text = _source("title: T\n", _Q, "# Fees&{{placeholder: q}}\n\nPay&{{placeholder: q}}.\n")
    found = [(d.rule, d.line) for d in validate(parse(text)).diagnostics if d.rule == "insertion-boundary"]
    assert found == [("insertion-boundary", text.split("\n").index("Pay&{{placeholder: q}}.") + 1)]


def test_insertion_boundary_is_still_judged_in_a_heading_in_a_list_item():
    text = _source("title: T\n", _Q, "# Fees\n\n- ## Of&{{placeholder: q}}\n")
    assert "insertion-boundary" in _rules(text)
