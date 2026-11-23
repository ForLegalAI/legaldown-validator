"""A directive where §11.4 does not recognize one asks nothing and is no blank, as assembly leaves it as written
(#12): a ``{{choose:}}`` in frontmatter (§15.5), a ``{{placeholder:}}`` in a frontmatter field that takes none or
in a value that is not a quoted YAML string (§3.10), and either in a section's heading (§4.2), which no insertion
reaches either (§15.7.3). (The heading's own cases are in ``test_hidden_constructs.py``.)"""
from __future__ import annotations

from legaldown import Template, parse, validate
from legaldown.grammar import ALIAS_REMOVED_RULE, FRONTMATTER_PLACEHOLDER_UNQUOTED_RULE, IMPLEMENTATION_RULE_PREFIX

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


def test_an_unquoted_placeholder_gives_the_blank_no_type():
    # It is no blank, for the validator and for assembly alike: the body's sets the type.
    text = _source("title: Lease {{placeholder:q, type=date}}\n", body="# A\n\nPay {{placeholder: q}}.\n")
    assert "placeholder-type-inconsistent" not in _rules(text)
    assert [(q.id, q.type) for q in Template(text).questions] == [("q", "text")]


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


# ── Core and assembly agree on which frontmatter placeholders are blanks (§3.10) ─────────────────────────────

#: Frontmatter value fields as YAML writes them, each with whether §3.10 recognizes the placeholder in it.
_WRITTEN = [
    ('title: "Lease {{placeholder: q}}"\n', True),
    ("title: 'Lease {{placeholder: q}}'\n", True),
    ('title: "Lease of\n  {{placeholder: q}}"\n', True),
    ('title: &t "Lease {{placeholder: q}}"\n', True),
    ('title: !!str "Lease {{placeholder: q}}"\n', True),
    ('title: &t !!str "Lease {{placeholder: q}}"\n', True),
    ("title: Lease {{placeholder:q}}\n", False),
    ("title: |\n  Lease {{placeholder: q}}\n", False),
    ('title: |\n  "Lease {{placeholder: q}}"\n', False),
    ("title: >-\n  'Lease {{placeholder: q}}'\n", False),
    ('title: Lease "{{placeholder:q}}"\n', False),
]


def _blanks_and_fills(fields: str) -> tuple[bool, bool]:
    """Whether core counts the title's placeholder a blank (the final check reports it), and whether assembly
    fills it."""
    text = _source(fields, _Q)
    final = validate(parse(text), final=True)
    blank = any(d.rule == "placeholder-unfilled" for d in final.diagnostics)
    output = Template(text).form({"q": "Main St"}).assemble().output
    title = parse(output).metadata.title
    return blank, "Main St" in title and "{{placeholder" not in title


def test_core_counts_as_blanks_exactly_the_placeholders_assembly_fills():
    for fields, recognized in _WRITTEN:
        assert _blanks_and_fills(fields) == (recognized, recognized), fields


def test_an_alias_to_an_anchored_placeholder_is_filled_through_its_anchor():
    text = _source('title: &t "Lease {{placeholder: q}}"\nsubtitle: *t\n', _Q)
    final = validate(parse(text), final=True)
    assert [d.line for d in final.diagnostics if d.rule == "placeholder-unfilled"] == [2, 3]
    assert _unused(text) == set()
    assembled = parse(Template(text).form({"q": "Main St"}).assemble().output)
    assert (assembled.metadata.title, assembled.metadata.subtitle) == ("Lease Main St", "Lease Main St")


def test_a_quote_in_a_filled_value_is_escaped_for_its_own_style():
    for fields in ('title: &t "Lease {{placeholder: q}}"\n', "title: !!str 'Lease {{placeholder: q}}'\n"):
        output = Template(_source(fields, _Q)).form({"q": "O'Neil \"Jr\""}).assemble().output
        assert parse(output).metadata.title == "Lease O'Neil \"Jr\"", fields


# ── Every frontmatter position: core's blanks are what assembly fills ────────────────────────────────────────

#: Frontmatter written around a placeholder, at a position §3.10 recognizes (True) or not (False).
_POSITIONS = [
    ('title: "Lease {{placeholder: q}}"\n', True),
    ('title: &t !!str "Lease {{placeholder: q}}"\n', True),
    ('title: T\nkeywords: ["{{placeholder: q}}"]\n', False),
    ('title: T\nkeywords:\n  - "{{placeholder: q}}"\n', False),
    ('title: T\ndate: "{{placeholder: q}}"\n', False),
    ('title: T\n"{{placeholder: q}}": x\n', False),
    ('title: T\n? "{{placeholder: q}}"\n: x\n', False),
    ('title: T\nextra: {a: "{{placeholder: q}}"}\n', False),
    ('title: T\n# "{{placeholder: q}}"\n', False),
    ("title: |\n  \"Lease {{placeholder: q}}\"\n", False),
]


def _frontmatter_lines(text: str) -> list[str]:
    return text.split("---\n")[1].split("\n")


def _core_blank_lines(text: str) -> set[int]:
    final = validate(parse(text), final=True)
    return {d.line for d in final.diagnostics if d.rule == "placeholder-unfilled"}


def _assembly_filled_lines(text: str) -> set[int]:
    template = Template(text)
    assert template.problems == (), text
    output = template.form({"q": "Main St"}).assemble().output
    before, after = _frontmatter_lines(text), _frontmatter_lines(output)
    assert len(before) == len(after), text
    return {number + 2 for number, (old, new) in enumerate(zip(before, after, strict=True)) if old != new}


def test_core_blanks_are_what_assembly_fills_at_every_frontmatter_position():
    for fields, recognized in _POSITIONS:
        text = _source(fields)
        line = next(number for number, row in enumerate(text.split("\n"), 1) if "{{placeholder" in row)
        expected = {line} if recognized else set()
        assert (_core_blank_lines(text), _assembly_filled_lines(text)) == (expected, expected), fields


def test_a_placeholder_in_a_structural_field_is_refused_by_both():
    text = _source('title: T\namends:\n  title: Original\n  file: "{{placeholder: q}}.lgd"\n')
    assert "placeholder-in-structural-field" in {d.rule for d in Template(text).problems}
    assert _core_blank_lines(text) == set()


# ── A placeholder written across two lines of a quoted value ─────────────────────────────────────────────────

def test_a_placeholder_across_two_lines_is_malformed_for_both():
    # §11.5: a directive does not span lines. YAML folds the quoted value onto one, but the source is two.
    text = _source('title: "Lease {{placeholder:\n  q}}"\n', _Q)
    result = validate(parse(text), final=True)
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule == "directive-malformed"] == [
        ("directive-malformed", 2)
    ]
    assert "placeholder-unfilled" not in result.rules()
    assert "directive-malformed" in {d.rule for d in Template(text).problems}


# ── An alias to an anchor that assembly removes ──────────────────────────────────────────────────────────────

_ATTACHED = (
    'title: T\nquestions:\n  x:\n    type: boolean\nattachments:\n  - id: one\n    title: &t "Annex"\n'
    "    file: one.pdf\n    when: x\nsubtitle: *t\n"
)


def test_an_alias_to_a_removable_anchor_is_reported_by_both_at_the_alias():
    text = _source(_ATTACHED, body="# A\n\nSee {{attach: one}}.\n")
    alias = text.split("\n").index("subtitle: *t") + 1
    found = [(d.rule, d.level, d.line) for d in validate(parse(text)).diagnostics if d.rule == ALIAS_REMOVED_RULE]
    assert found == [(ALIAS_REMOVED_RULE, "error", alias)]
    # Template.problems refuses it up front, and assembly on its own does not raise.
    assert ALIAS_REMOVED_RULE in {d.rule for d in Template(text).problems}
    result = Template(text, _check=False).form({"x": False}).assemble()
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule == ALIAS_REMOVED_RULE] == [(ALIAS_REMOVED_RULE, alias)]
    assert result.output == ""


def test_an_alias_to_an_anchor_in_questions_is_reported_and_one_kept_is_not():
    text = _source('title: T\nquestions:\n  x:\n    type: text\n    prompt: &p "Name"\nsubtitle: *p\n',
                   body="# A\n\n{{placeholder: x}}\n")
    assert ALIAS_REMOVED_RULE in validate(parse(text)).rules()
    kept = _source('title: &t "T"\nsubtitle: *t\nquestions:\n  x:\n    type: text\n', body="# A\n\n{{placeholder: x}}\n")
    assert ALIAS_REMOVED_RULE not in validate(parse(kept)).rules()
    assert parse(Template(kept).form({"x": "a"}).assemble().output).metadata.subtitle == "T"
