"""Constructs that take content out of validation without a word (#12): ``ldv-fence-unclosed``, a fenced code
block that runs to the end of its file, and ``ldv-heading-directive``, a directive in heading text (§4.2).
The specification has no rule id for either, so they are this implementation's own (§16.1)."""
from __future__ import annotations

from legaldown import Template, parse, validate
from legaldown.grammar import FENCE_UNCLOSED_RULE, HEADING_DIRECTIVE_RULE, IMPLEMENTATION_RULE_PREFIX

FM = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)
_RULES = (FENCE_UNCLOSED_RULE, HEADING_DIRECTIVE_RULE)


def _at(text: str, needle: str, nth: int = 0) -> int:
    """The line (from 1) of the *nth* line of *text* that holds *needle*."""
    return [number for number, line in enumerate(text.split("\n"), 1) if needle in line][nth]


def _found(result, *rules: str) -> list[tuple[str, str, str, int | None]]:
    rules = rules or _RULES
    return [(d.rule, d.level, d.file, d.line) for d in result.diagnostics if d.rule in rules]


def _message(result, rule: str) -> str:
    (message,) = [d.message for d in result.diagnostics if d.rule == rule]
    return message


def test_the_ids_are_this_implementations_own():
    assert FENCE_UNCLOSED_RULE == "ldv-fence-unclosed"
    assert HEADING_DIRECTIVE_RULE == "ldv-heading-directive"
    assert all(rule.startswith(IMPLEMENTATION_RULE_PREFIX) for rule in _RULES)


# ── ldv-fence-unclosed ───────────────────────────────────────────────────────────────────────────────────────

def test_the_issues_unclosed_fence_is_reported_at_its_opening_line_with_what_it_holds():
    text = FM + "# A\n\n```\ncode\n\n# B\n\n{{ref: nope}}\n"
    result = validate(parse(text))
    # Section B and the broken reference are code: the Warning is the only word of them.
    assert [s.title for s in parse(text).sections] == ["A"]
    assert "ref-broken" not in {d.rule for d in result.diagnostics}
    assert _found(result) == [(FENCE_UNCLOSED_RULE, "warning", "", _at(text, "```"))]
    message = _message(result, FENCE_UNCLOSED_RULE)
    assert "end of the document" in message
    assert "5 lines with 1 heading and 1 directive" in message
    assert "'```'" in message


def test_a_closed_fence_is_not_reported():
    for text in (
        FM + "# A\n\n```\ncode\n```\n\n# B\n",
        FM + "# A\n\n~~~~ text\n# not a heading\n~~~~~\n",
        FM + "# A\n\n```\ncode\n```\n",
        FM + "# A\n\n    indented code\n",
    ):
        assert _found(validate(parse(text))) == [], text


def test_a_tilde_fence_and_a_lone_fence_at_the_end_are_reported():
    text = FM + "# A\n\nText.\n\n~~~\n"
    result = validate(parse(text))
    assert _found(result) == [(FENCE_UNCLOSED_RULE, "warning", "", _at(text, "~~~"))]
    assert "Nothing follows it" in _message(result, FENCE_UNCLOSED_RULE)


def test_backticks_after_an_unclosed_fence_are_code_too():
    # The fence's opening line is matched alone: a later backtick is no part of it.
    for text in (FM + "# A\n\n```\nsee `x`\n", FM + "# A\n\n```\ncode\n    ```\n"):
        assert _found(validate(parse(text))) == [(FENCE_UNCLOSED_RULE, "warning", "", _at(text, "```"))], text


def test_what_it_holds_is_counted_as_legaldown_reads_it():
    # A list item over '---' is no setext heading, and what a fence in it holds is code.
    text = FM + "# A\n\n```\n- item\n---\n~~~\n# not a heading {{ref: x}}\n~~~\n"
    message = _message(validate(parse(text)), FENCE_UNCLOSED_RULE)
    assert "5 lines, as code" in message


def test_a_setext_heading_it_holds_is_counted():
    text = FM + "# A\n\n```\nPayment\n=======\n\nText {{ref: x}}\n\n---\n\nAfter a rule.\n"
    assert "8 lines with 1 heading and 1 directive" in _message(validate(parse(text)), FENCE_UNCLOSED_RULE)


def test_text_a_model_puts_after_its_closing_fence_is_no_open_fence():
    from legaldown import Block

    document = parse(FM + "# A\n\nText.\n")
    document.sections[0].blocks.append(Block(kind="code", text="```\nx\n```\n{{ref: missing}}"))
    result = validate(document)
    assert "ref-broken" in result.rules("error")
    assert _found(result) == []


def test_a_fence_in_the_preamble_is_reported():
    text = FM + "Intro.\n\n```\n# A\n"
    assert parse(text).sections == []
    assert _found(validate(parse(text))) == [(FENCE_UNCLOSED_RULE, "warning", "", _at(text, "```"))]


def test_a_fence_its_list_item_or_quote_closes_is_not_reported():
    # The container ends the code block (CommonMark), and what follows it is read: nothing is lost past it.
    for text in (
        FM + "# A\n\n- item\n  ```\n  code\n- next {{ref: nope}}\n\n# B\n",
        FM + "# A\n\n> ```\n> code\n\n# B\n\nSee {{ref: nope}}.\n",
        FM + "# A\n\n- item\n  ```\n  code\n\nAfter.\n",
    ):
        result = validate(parse(text))
        assert _found(result) == [], text
    assert "ref-broken" in {d.rule for d in validate(parse(FM + "# A\n\n> ```\n> code\n\n# B\n\nSee {{ref: nope}}.\n")).diagnostics}


def test_a_fence_in_a_container_that_runs_to_the_end_is_reported_at_its_line():
    # Nothing but the end of the file closes it: the item's (or the quote's) later lines are all code.
    for text in (
        FM + "# A\n\n- item\n  ```\n  code\n  # B\n",
        FM + "# A\n\n> Quote.\n>\n> ```\n> code\n",
        FM + "# A\n\n- one\n- two\n  > ```\n  > code\n",
        FM + "# A\n\n1. one\n   - nested\n     ```\n     code\n",
    ):
        assert _found(validate(parse(text))) == [(FENCE_UNCLOSED_RULE, "warning", "", _at(text, "```"))], text


def test_a_fence_followed_by_an_empty_item_is_closed_by_its_item():
    text = FM + "# A\n\n- ```\n  code\n-\n"
    assert _found(validate(parse(text))) == []


def test_a_document_built_in_code_is_judged_too_without_a_line():
    from dataclasses import replace

    document = parse(FM + "# A\n\n```\ncode\n")
    edited = replace(document, source_map=None)
    assert _found(validate(edited)) == [(FENCE_UNCLOSED_RULE, "warning", "", None)]


# ── ldv-heading-directive ────────────────────────────────────────────────────────────────────────────────────

def test_the_issues_setext_heading_is_reported_at_the_heading_naming_its_underline():
    text = FM + '# Terms\n\n"Buyer" {{def: buyer}} means X.\n---\n\nSee {{term: buyer}}.\n'
    result = validate(parse(text))
    # The use is still term-undefined; the cause is now named, at the heading.
    assert "term-undefined" in {d.rule for d in result.diagnostics}
    assert _found(result) == [(HEADING_DIRECTIVE_RULE, "error", "", _at(text, "{{def: buyer}}"))]
    message = _message(result, HEADING_DIRECTIVE_RULE)
    assert "'{{def: buyer}}'" in message and "§4.2" in message
    assert f"'---' line under it at line {_at(text, '---', 2)}" in message
    assert "setext" in message


def test_an_equals_underline_is_named_too():
    text = FM + "# Terms\n\nSee {{ref: terms}}\n===\n"
    message = _message(validate(parse(text)), HEADING_DIRECTIVE_RULE)
    assert "'===' line under it" in message


def test_a_directive_in_an_atx_heading_is_reported_once_for_the_heading():
    text = FM + "# Terms\n\n## Fees under {{ref: terms}} and {{term: x}} {#fees}\n\nText.\n"
    result = validate(parse(text))
    assert _found(result) == [(HEADING_DIRECTIVE_RULE, "error", "", _at(text, "## Fees"))]
    message = _message(result, HEADING_DIRECTIVE_RULE)
    assert "'{{ref: terms}}' and '{{term: x}}'" in message
    assert "setext" not in message


def test_a_malformed_directive_in_a_heading_is_reported():
    text = FM + "# Terms {{ref: }}\n"
    assert _found(validate(parse(text))) == [(HEADING_DIRECTIVE_RULE, "error", "", _at(text, "# Terms"))]


def test_what_is_no_directive_in_a_heading_is_not_reported():
    for text in (
        FM + "# Terms `{{ref: x}}`\n",
        FM + "# Terms \\{{ref: x}}\n",
        FM + "# Terms {#terms}\n",
        FM + "# Terms <!-- {{ref: x}} -->\n",
        FM + "# Terms\n\nA paragraph {{ref: terms}}.\n\n---\n\nAfter a rule.\n",
    ):
        assert _found(validate(parse(text))) == [], text


def test_a_heading_in_a_list_item_or_a_quote_is_reported_at_its_line():
    text = FM + "# Terms\n\n- Item.\n- ## Inner {{ref: terms}}\n\n> Quote.\n>\n> ### Quoted {{ref: terms}}\n"
    assert _found(validate(parse(text))) == [
        (HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "## Inner")),
        (HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "### Quoted")),
    ]


def test_a_setext_heading_in_a_list_item_or_a_quote_names_its_underline():
    text = FM + "# Terms\n\n- Item {{ref: terms}}\n  ---\n\n> Quoted {{ref: terms}}\n> ===\n"
    result = validate(parse(text))
    assert _found(result) == [
        (HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "- Item")),
        (HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "> Quoted")),
    ]
    first, second = (d.message for d in result.diagnostics if d.rule == HEADING_DIRECTIVE_RULE)
    assert f"'---' line under it at line {_at(text, '  ---')}" in first
    assert f"'===' line under it at line {_at(text, '> ===')}" in second


def test_a_choose_in_a_heading_is_left_to_choose_invalid():
    text = FM.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n") + (
        '# Terms {{choose: x, true="A", false="B"}}\n\nText.\n'
    )
    result = validate(parse(text))
    assert "choose-invalid" in {d.rule for d in result.diagnostics}
    assert _found(result) == []


def test_a_heading_in_a_list_item_says_this_validator_reads_it_and_a_choose_there_is_reported():
    # (choose-invalid looks at the headings of sections only.)
    text = FM.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n") + (
        '# Terms\n\n- ## Inner {{ref: nope}}\n- ## Other {{choose: x, true="A", false="B"}}\n'
    )
    result = validate(parse(text))
    assert "ref-broken" in result.rules("error")  # read as body text
    assert "choose-invalid" not in result.rules("error")
    assert _found(result) == [
        (HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "## Inner")),
        (HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "## Other")),
    ]
    message = next(d.message for d in result.diagnostics if d.rule == HEADING_DIRECTIVE_RULE)
    assert "this validator reads it as body text" in message and "not done" not in message


def test_a_heading_in_a_drafting_note_is_not_reported():
    text = FM + "# Terms\n\n> [!DRAFTING]\n> ## Note {{ref: terms}}\n>\n> > ### Deeper {{ref: terms}}\n\n> ## Kept {{ref: terms}}\n"
    assert _found(validate(parse(text))) == [(HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "## Kept"))]


def test_a_heading_after_a_drafting_note_deeper_than_the_walk_is_reported():
    # The note's quotes run past MAX_QUOTE_DEPTH: the place of what follows it is counted as the walk counts it.
    from legaldown.parser import MAX_QUOTE_DEPTH

    deep = "".join("  " + "> " * level + "q\n" for level in range(2, MAX_QUOTE_DEPTH + 4))
    text = FM + "# A\n\n- x\n\n  > [!DRAFTING]\n  >\n" + deep + "\n  ## After {{ref: a}}\n"
    assert _found(validate(parse(text))) == [(HEADING_DIRECTIVE_RULE, "warning", "", _at(text, "## After"))]


def test_a_line_that_is_no_lf_is_no_line_of_the_count():
    text = FM + "# A\n\n```\na\x0cb\x1cc\n"
    assert "1 line," in _message(validate(parse(text)), FENCE_UNCLOSED_RULE)


def test_a_placeholder_in_a_templates_heading_is_reported_and_assembly_goes_on():
    # Assembly does not fill it (§4.2: no directive in a heading), so the finished document would keep it.
    text = FM.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n") + (
        "# Lease of {{placeholder: address}}\n\nText. {when=x}\n"
    )
    template = Template(text)
    assert _found(template.validation) == [(HEADING_DIRECTIVE_RULE, "error", "", _at(text, "# Lease"))]
    assert template.problems == ()
    assembled = template.form({"x": True}).assemble()
    assert "{{placeholder: address}}" in assembled.output


# ── Fragments and the combined document ──────────────────────────────────────────────────────────────────────

def _run(text: str, files: dict[str, str]):
    return validate(parse(text, filename="main.lgd"), resolve=files.get)


def test_a_fragment_on_its_own_says_fragment():
    part = "## Part\n\n```\ncode\n"
    result = validate(parse(part), fragment=True)
    assert _found(result) == [(FENCE_UNCLOSED_RULE, "warning", "", 3)]
    assert "end of the fragment" in _message(result, FENCE_UNCLOSED_RULE)
    assert "splices the fragment's text" in _message(result, FENCE_UNCLOSED_RULE)
    attachment = validate(parse(part), fragment=True, fragment_kind="attachment")
    assert "end of the attachment file (CommonMark)" in _message(attachment, FENCE_UNCLOSED_RULE)


def test_a_fragments_open_fence_is_reported_once_in_its_file_and_the_document_goes_on():
    text = FM + "# Main\n\n{{include: part.lgd}}\n\nSee {{ref: nope}}.\n\n{{include: part.lgd}}\n"
    part = "## Part {{ref: main}}\n\nText.\n\n```\ncode\n"
    result = _run(text, {"part.lgd": part})
    # Once, though the fragment is in two places; what follows its include is read (a broken reference).
    assert _found(result) == [
        (HEADING_DIRECTIVE_RULE, "error", "part.lgd", 1),
        (FENCE_UNCLOSED_RULE, "warning", "part.lgd", _at(part, "```")),
    ]
    assert "end of the fragment" in _message(result, FENCE_UNCLOSED_RULE)
    assert ("ref-broken", "main.lgd") in {(d.rule, d.file) for d in result.diagnostics}


def test_the_documents_own_findings_are_made_once_with_its_fragments_in():
    text = FM + "# Main {{ref: main}}\n\n{{include: part.lgd}}\n\nSee {{ref: part}}.\n\n```\ncode\n"
    result = _run(text, {"part.lgd": "## Part\n\nText.\n"})
    assert "ref-broken" not in {d.rule for d in result.diagnostics}  # the fragment is in: the combined pass ran
    assert _found(result) == [
        (HEADING_DIRECTIVE_RULE, "error", "main.lgd", _at(text, "# Main")),
        (FENCE_UNCLOSED_RULE, "warning", "main.lgd", _at(text, "```")),
    ]
    assert "end of the document" in _message(result, FENCE_UNCLOSED_RULE)


def test_a_choose_in_a_fragments_heading_is_reported_when_the_document_is_no_template():
    # The combined pass of a document that is no template collects no {{choose:}} (choose-invalid): this rule does.
    text = FM + "# Main\n\n{{include: part.lgd}}\n"
    part = '## Part {{choose: x, true="A", false="B"}}\n\nText.\n'
    assert _found(_run(text, {"part.lgd": part})) == [(HEADING_DIRECTIVE_RULE, "error", "part.lgd", 1)]


def test_an_attachment_file_is_judged_as_one():
    front = FM.replace("---\n\n", "attachments:\n  - id: annex\n    title: Annex\n    file: annex.lgd\n---\n\n")
    text = front + "# Main\n\nSee {{attach: annex}}.\n"
    annex = "## Annex\n\n```\ncode\n"
    result = _run(text, {"annex.lgd": annex})
    assert _found(result) == [(FENCE_UNCLOSED_RULE, "warning", "annex.lgd", _at(annex, "```"))]
    assert "end of the attachment file" in _message(result, FENCE_UNCLOSED_RULE)


# ── A directive in a section's heading asks nothing, as assembly leaves it as written ────────────────────────

_ADDRESS = FM.replace("---\n\n", "questions:\n  address:\n    type: text\n---\n\n")


def test_a_question_only_a_section_headings_placeholder_uses_is_unused():
    text = _ADDRESS + "# Lease of {{placeholder: address}}\n\nText.\n"
    template = Template(text)
    (unused,) = [d.message for d in template.validation.diagnostics if d.rule == "question-unused"]
    assert "a section's heading names it" in unused
    # ... as assembly agrees: it leaves the heading as written.
    assert "{{placeholder: address}}" in template.form({"address": "Main St"}).assemble().output


def test_a_section_headings_placeholder_is_no_unfilled_blank_of_the_final_check():
    text = FM + "# Lease of {{placeholder: address}}\n\nText.\n"
    result = validate(parse(text), final=True)
    assert [d.rule for d in result.diagnostics if d.line == _at(text, "# Lease")] == [HEADING_DIRECTIVE_RULE]


def test_a_choose_in_a_section_heading_asks_nothing_either():
    text = FM.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n") + (
        '# Terms {{choose: x, true="A", false="B"}}\n\nText.\n'
    )
    rules = {d.rule for d in validate(parse(text)).diagnostics}
    assert {"choose-invalid", "question-unused"} <= rules


def test_a_placeholder_in_a_heading_in_a_list_item_is_a_blank_as_assembly_fills_it():
    text = _ADDRESS + "# Lease\n\n- ## Of {{placeholder: address}}\n"
    template = Template(text)
    assert "question-unused" not in {d.rule for d in template.validation.diagnostics}
    assert "Of Main St" in template.form({"address": "Main St"}).assemble().output
    final = validate(parse(FM + "# Lease\n\n- ## Of {{placeholder: address}}\n"), final=True)
    assert "placeholder-unfilled" in {d.rule for d in final.diagnostics}
