"""The combined document (§12.2, §15.7.2 step 7, §16.11, #117): the document with the fragments its include
paragraphs take in, validated as the one document it is, so that what refers across a fragment and the
document is judged by what both declare, each finding at its line in its own file.

A fragment is spliced in as it was parsed, not as text (``validator.combined``): the tests pin what that means for a
fragment that is malformed, for presence in a template (§15.3, §15.4), and for what is left to the validation of
the document alone (``HOST_ONLY``)."""
from __future__ import annotations

import json
import random
import re
import time
from collections import Counter

import pytest

from legaldown import Document, Template, load, parse, validate
from legaldown.cli import EXIT_DIAGNOSTICS, main
from legaldown.grammar import SKIPPED_RULE
from legaldown.validator import core
from legaldown.validator.combined import HOST_ONLY, WHOLE_DOCUMENT_RULES
from legaldown.validator.fragments import MAX_INCLUDE_DEPTH

_FRONT = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    label: Provider\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    label: Client\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)
_QUESTIONS = "questions:\n  x:\n    type: boolean\n  z:\n    type: boolean\n"
_TEMPLATE_FRONT = _FRONT.replace("---\n\n", _QUESTIONS + "---\n\n")
_SCOPE = "# Scope {#scope}\n\n"


class _Loader:
    """A loader over a dict that remembers what it was asked for."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.asked: list[str] = []

    def __call__(self, path: str) -> str | None:
        self.asked.append(path)
        return self.files.get(path)


def _run(text: str, files: dict[str, str] | None = None, **kwargs):
    return validate(parse(text, filename="main.lgd"), resolve=_Loader(files or {}), **kwargs)


def _found(result, *rules: str) -> list[tuple[str, str, int | None]]:
    """``(rule, file, line)`` of the findings of *rules* (every rule by default), in the order of the result."""
    return [(d.rule, d.file, d.line) for d in result.diagnostics if not rules or d.rule in rules]


def _at(text: str, needle: str, nth: int = 0) -> int:
    """The line (from 1) of the *nth* line of *text* that holds *needle*."""
    return [number for number, line in enumerate(text.split("\n"), 1) if needle in line][nth]


def _warning(result):
    return next((d for d in result.diagnostics if d.rule == SKIPPED_RULE), None)


# ── The false findings of the issue: a reference or a term across a fragment and the document ──────────────

def test_a_reference_into_a_fragment_is_not_broken_and_one_into_nothing_is():
    text = _FRONT + _SCOPE + "See {{ref: part-sec}} and {{ref: absent}}.\n\n{{include: part.lgd}}\n\nBack to {{ref: scope}}.\n"
    part = "## Part {#part-sec}\n\nSee {{ref: scope}}, {{ref: part-sec}} and {{ref: nowhere}}.\n"
    result = _run(text, {"part.lgd": part})
    # The target is in the fragment (or the document): resolved. The rest are at their lines in their files.
    assert _found(result, "ref-broken") == [
        ("ref-broken", "main.lgd", _at(text, "{{ref: absent}}")),
        ("ref-broken", "part.lgd", _at(part, "{{ref: nowhere}}")),
    ]


def test_a_term_defined_in_a_fragment_is_defined_and_the_other_way_round():
    text = (
        _FRONT + _SCOPE + '"Fee" {{def: fee}} is paid for {{term: service}} and {{term: nothing}}.\n\n'
        "{{include: part.lgd}}\n\nThe {{term: price}} applies.\n"
    )
    part = '## Part {#part}\n\n"Service" {{def: service}} costs the {{term: fee}}; "Price" {{def: price}}.\n\nSee {{term: lost}}.\n'
    result = _run(text, {"part.lgd": part})
    assert _found(result, "term-undefined") == [
        ("term-undefined", "main.lgd", _at(text, "{{term: nothing}}")),
        ("term-undefined", "part.lgd", _at(part, "{{term: lost}}")),
    ]


def test_a_definition_the_other_file_references_is_referenced():
    text = _FRONT + _SCOPE + '"Fee" {{def: fee}} is a fee.\n\n{{include: part.lgd}}\n'
    part = '## Part {#part}\n\nThe {{term: fee}} is due. "Lonely" {{def: lonely}} is never used.\n'
    result = _run(text, {"part.lgd": part})
    assert _found(result, "def-unreferenced") == [("def-unreferenced", "part.lgd", _at(part, "lonely"))]


def test_an_auto_generated_identifier_the_fragment_and_the_document_share_is_adjusted():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n## Terms\n\nText.\n"
    part = "## Terms\n\nFragment text.\n"
    result = _run(text, {"part.lgd": part})
    # The later of the two (the document's) is the one that is adjusted, as in the combined document.
    assert _found(result, "anchor-autogen-collision") == [("anchor-autogen-collision", "main.lgd", _at(text, "## Terms"))]
    assert "adjusted to 'terms-2'" in next(d.message for d in result.diagnostics if d.rule == "anchor-autogen-collision")


def test_two_fragments_that_share_an_auto_generated_identifier_collide_in_the_later_one():
    text = _FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: b.lgd}}\n"
    result = _run(text, {"a.lgd": "## Terms\n\nA.\n", "b.lgd": "Text.\n\n## Terms\n\nB.\n"})
    assert _found(result, "anchor-autogen-collision") == [("anchor-autogen-collision", "b.lgd", 3)]


def test_an_explicit_identifier_declared_twice_is_include_anchor_duplicate_alone():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "## Again {#scope}\n\nText.\n"})
    assert _found(result, "include-anchor-duplicate", "anchor-duplicate") == [("include-anchor-duplicate", "part.lgd", 1)]


def test_a_marker_anchor_in_a_fragment_that_the_document_declares_is_anchor_duplicate():
    text = _FRONT + _SCOPE + "A paragraph. {#clause}\n\n{{include: part.lgd}}\n"
    part = "## Part {#part}\n\nAnother paragraph. {#clause}\n"
    result = _run(text, {"part.lgd": part})
    assert _found(result, "anchor-duplicate") == [("anchor-duplicate", "part.lgd", _at(part, "{#clause}"))]


def test_a_definition_declared_in_the_fragment_and_the_document_twice_is_duplicate():
    text = _FRONT + _SCOPE + '"Fee" {{def: fee}} is a fee, {{term: fee}}.\n\n{{include: part.lgd}}\n'
    part = '## Part {#part}\n\n"Fee" {{def: fee}} again.\n'
    result = _run(text, {"part.lgd": part})
    assert _found(result, "def-duplicate-id") == [("def-duplicate-id", "part.lgd", _at(part, "{{def: fee}}"))]


def test_definitions_that_explain_each_other_across_files_are_circular_and_a_use_before_is_named_by_file():
    text = _FRONT + _SCOPE + '"Fee" {{def: fee}} is the {{term: extra}}.\n\n{{include: part.lgd}}\n'
    part = '## Part {#part}\n\n"Extra" {{def: extra}} is the {{term: fee}}.\n'
    result = _run(text, {"part.lgd": part})
    assert _found(result, "definition-circular") == [("definition-circular", "part.lgd", _at(part, "{{def: extra}}"))]
    # `extra` is used in the document (line of the term) before its declaration in the fragment.
    (early,) = (d for d in result.diagnostics if d.rule == "definition-used-before-declaration")
    assert (early.file, early.line) == ("main.lgd", _at(text, "{{term: extra}}"))
    assert f"before its declaration at line {_at(part, '{{def: extra}}')} of part.lgd" in early.message


# ── Fragment findings that the document alone never made ──────────────────────────────────────────────────

def test_a_blank_not_kept_apart_from_text_in_a_fragment_is_found_at_its_line_in_the_fragment():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\nAfter the fragment.\n"
    part = "## Part {#part}\n\nPay *{{placeholder: p}}* now.\n"
    result = _run(text, {"part.lgd": part})
    assert _found(result, "insertion-boundary") == [("insertion-boundary", "part.lgd", _at(part, "{{placeholder: p}}"))]
    # A fragment that opens with a blank, after a paragraph of the document: apart by the paragraph break.
    clean = _run(text, {"part.lgd": "{{placeholder: p}} is due.\n\n## Part {#part}\n\nText.\n"})
    assert _found(clean, "insertion-boundary") == []


def test_what_a_fragment_holds_that_a_complete_document_may_not_is_reported_in_the_fragment():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    part = "## Part {#part}\n\nOn {{date: 2026-13-45}} <b>bold</b> {{unknown: x}}.\n"
    result = _run(text, {"part.lgd": part})
    line = _at(part, "date")
    assert sorted(_found(result, "date-invalid", "raw-html", "directive-unknown")) == [
        ("date-invalid", "part.lgd", line), ("directive-unknown", "part.lgd", line), ("raw-html", "part.lgd", line),
    ]


def test_the_final_check_covers_the_blanks_of_a_fragment():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    part = "## Part {#part}\n\nPay {{placeholder: amount, type=money}}.\n"
    assert _found(_run(text, {"part.lgd": part}), "placeholder-unfilled") == []
    assert _found(_run(text, {"part.lgd": part}, final=True), "placeholder-unfilled") == [
        ("placeholder-unfilled", "part.lgd", _at(part, "{{placeholder"))
    ]


def test_a_question_only_a_fragment_uses_is_used():
    front = _FRONT.replace("---\n\n", "questions:\n  amount:\n    type: money\n---\n\n")
    text = front + _SCOPE + "{{include: part.lgd}}\n"
    part = "## Part {#part}\n\nPay {{placeholder: amount, type=money}}.\n"
    assert _found(_run(text, {"part.lgd": part}), "question-unused") == []
    # Used by nothing, in the fragments too: reported, as it is in a document with no include.
    result = _run(text, {"part.lgd": "## Part {#part}\n\nText.\n"})
    assert [(r, f) for r, f, _ in _found(result, "question-unused")] == [("question-unused", "main.lgd")]
    # A LegalDown attachment file is not part of the combined document: a question may be used there.
    attached = front.replace("---\n\n", "attachments:\n  - id: a\n    title: A\n    file: a.lgd\n---\n\n")
    quiet = _run(attached + _SCOPE + "{{include: part.lgd}}\n\n{{attach: a}}\n", {"part.lgd": "## Part {#part}\n\nText.\n"})
    assert _found(quiet, "question-unused") == []


# ── Which file and which line ─────────────────────────────────────────────────────────────────────────────────

def test_a_finding_after_an_include_is_at_its_own_line_in_the_document():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\nAfter {{ref: missing}}.\n\n## Next\n\nAlso {{ref: missing-too}}.\n"
    result = _run(text, {"part.lgd": "## Part {#part}\n\n### Sub {#sub}\n\nText.\n"})
    assert _found(result, "ref-broken") == [
        ("ref-broken", "main.lgd", _at(text, "{{ref: missing}}")), ("ref-broken", "main.lgd", _at(text, "missing-too")),
    ]


def test_a_nested_fragment_is_named_by_its_path_from_the_documents_directory():
    text = _FRONT + _SCOPE + "{{include: sub/a.lgd}}\n"
    files = {
        "sub/a.lgd": "## A {#a}\n\n{{include: b.lgd}}\n",
        "sub/b.lgd": "### B {#b}\n\nSee {{ref: gone}}.\n\n#### C {#c}\n\nSee {{ref: a}}, {{ref: scope}} and {{ref: c}}.\n",
    }
    result = _run(text, files)
    assert _found(result) == [("ref-broken", "sub/b.lgd", 3)]


def test_a_fragment_included_twice_is_reported_once():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n## Again {#again}\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "### Part\n\nSee {{ref: gone}}.\n"})
    assert _found(result, "ref-broken") == [("ref-broken", "part.lgd", 3)]


def test_lines_of_a_fragment_written_with_crlf_and_setext_headings():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    part = "Intro {{ref: gone}}.\r\n\r\nPart\r\n----\r\n\r\nSee {{ref: gone-too}}.\r\n"
    result = _run(text, {"part.lgd": part})
    assert _found(result, "ref-broken") == [("ref-broken", "part.lgd", 1), ("ref-broken", "part.lgd", 6)]


def test_a_document_built_in_code_has_the_fragments_lines_and_none_of_its_own():
    document = Document.from_dict(parse(_FRONT + _SCOPE + "See {{ref: part}} and {{ref: gone}}.\n\n{{include: part.lgd}}\n").to_dict())
    assert document.source_map is None
    part = "## Part {#part}\n\nSee {{ref: gone-too}}.\n"
    result = validate(document, resolve={"part.lgd": part}.get)
    assert _found(result, "ref-broken") == [("ref-broken", "", None), ("ref-broken", "part.lgd", 3)]


def test_the_command_line_reports_the_file_and_line_of_a_finding_in_a_fragment(tmp_path, capsys):
    (tmp_path / "main.lgd").write_text(_FRONT + _SCOPE + "{{include: part.lgd}}\n", encoding="utf-8")
    (tmp_path / "part.lgd").write_text("## Part {#part}\n\nSee {{ref: gone}}.\n", encoding="utf-8")
    assert main(["validate", str(tmp_path / "main.lgd")]) == EXIT_DIAGNOSTICS
    out = capsys.readouterr().out
    assert "part.lgd:3" in out and "ref-broken" in out
    # ... and once when the fragment is a target too.
    assert main(["validate", str(tmp_path / "main.lgd"), str(tmp_path / "part.lgd"), "--format", "json"]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["file"], d["line"]) for d in records if d["rule"] == "ref-broken"] == [(str(tmp_path / "part.lgd"), 3)]


# ── Templates: presence per file, and the include's own (§15.3, §15.4) ────────────────────────────────────────

def test_a_reference_into_a_conditional_fragment_is_unsafe_unless_it_is_as_conditional():
    text = (
        _TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}} {when=x}\n\nUnsafe {{ref: disputes}}.\n\n"
        "Safe {{ref: disputes}} {when=x}\n\nSafe too: {{term: fee}} {when=x}\n\nUnsafe: {{term: fee}}.\n"
    )
    part = '## Disputes {#disputes}\n\n"Fee" {{def: fee}}, paid. See {{ref: scope}}.\n'
    result = _run(text, {"part.lgd": part})
    assert _found(result, "condition-reference-unsafe", "ref-broken", "term-undefined") == [
        ("condition-reference-unsafe", "main.lgd", _at(text, "Unsafe {{ref")),
        ("condition-reference-unsafe", "main.lgd", _at(text, "Unsafe: {{term")),
    ]


def test_a_conditional_include_in_a_conditional_section_has_both_conditions():
    text = (
        _TEMPLATE_FRONT + "# Scope {#scope when=z}\n\n{{include: part.lgd}} {when=x}\n\nInside {{ref: part}} {when=x}\n\n"
        "# Elsewhere {#elsewhere}\n\nOutside {{ref: part}} {when=x}\n"
    )
    result = _run(text, {"part.lgd": "## Part {#part}\n\nText.\n"})
    # Inside the section the reference is as present as the fragment; outside it can be present without it.
    assert [(r, n) for r, _, n in _found(result, "condition-reference-unsafe")] == [
        ("condition-reference-unsafe", _at(text, "Outside"))
    ]


def test_a_block_of_the_document_after_an_include_has_the_presence_of_its_own_file():
    # The last section of the fragment is conditional, but it ends with its file: the paragraph after the include
    # is not in it, so its reference to the conditional section's twin is judged as the document's.
    text = (
        _TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}}\n\nAfter: {{ref: cond}}.\n"
    )
    part = "## Cond {#cond when=x}\n\nText.\n"
    result = _run(text, {"part.lgd": part})
    assert [(r, n) for r, _, n in _found(result, "condition-reference-unsafe")] == [("condition-reference-unsafe", _at(text, "After"))]
    # ... and a reference in the fragment's own section has the section's presence.
    inside = _run(
        text.replace("After: {{ref: cond}}.\n", ""), {"part.lgd": part + "\nSee {{ref: cond}}.\n"}
    )
    assert _found(inside, "condition-reference-unsafe") == []


def test_fragments_that_are_alternatives_may_share_an_identifier():
    text = (
        _TEMPLATE_FRONT + _SCOPE + "{{include: a.lgd}} {when=x}\n\n{{include: b.lgd}} {when=!x}\n\nSee {{ref: disputes}}.\n"
    )
    files = {"a.lgd": "## Disputes {#disputes}\n\nCourts.\n", "b.lgd": "## Disputes {#disputes}\n\nArbitration.\n"}
    result = _run(text, files)
    assert _found(result, "anchor-duplicate", "include-anchor-duplicate", "ref-broken", "condition-reference-unsafe") == []
    # A third declaration that can be there with either is a duplicate.
    third = _run(text + "\n{{include: c.lgd}}\n", {**files, "c.lgd": "## Disputes {#disputes}\n\nThird.\n"})
    assert _found(third, "include-anchor-duplicate") == [("include-anchor-duplicate", "c.lgd", 1)]
    # Auto-generated identifiers of alternatives are the same one, not collisions.
    auto = _run(text, {"a.lgd": "## Disputes\n\nCourts.\n", "b.lgd": "## Disputes\n\nArbitration.\n"})
    assert _found(auto, "anchor-autogen-collision", "anchor-duplicate") == []


def test_a_reference_to_an_alternative_declared_under_one_answer_only_is_unsafe():
    text = _TEMPLATE_FRONT + _SCOPE + "{{include: a.lgd}} {when=x}\n\n{{include: b.lgd}} {when=!x}\n\nSee {{ref: only-a}}.\n"
    result = _run(text, {"a.lgd": "## A {#only-a}\n\nA.\n", "b.lgd": "## B {#only-b}\n\nB.\n"})
    assert [(r, n) for r, _, n in _found(result, "condition-reference-unsafe")] == [("condition-reference-unsafe", _at(text, "See"))]


def test_a_condition_or_a_choice_only_a_fragment_holds_does_not_make_the_document_a_template():
    # §15.1 reads the document: the assembler decides from it alone, so the fragment's constructs are judged as the
    # document's status dictates, and a document that is no template gives what it gave before the fragments were read.
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    for part in (
        "## Part {#part}\n\n{{choose: q}}\n", "## Part {#part when=x}\n\nText.\n", "## Part {#part}\n\nText. {when=x}\n",
        "## Part {#part}\n\n> [!DRAFTING]\n> note\n\n{{choose: q}}\n",
    ):
        result = _run(text, {"part.lgd": part})
        assert not result.index.is_template
        assert not result.rules() & {"condition-invalid", "choose-invalid", "template-fragment-invalid"}
        assert _warning(result) is None
        template = Template(text, resolve={"part.lgd": part}.get)
        assert template.problems == ()
    # In a template they are judged, in the fragment.
    result = _run(_TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": "## Part {#part}\n\n{{choose: nope}}\n"})
    assert _found(result, "choose-invalid") == [("choose-invalid", "part.lgd", 3)]


def test_a_fragment_with_a_choice_is_no_trouble_to_a_document_whose_frontmatter_cannot_be_read():
    text = "---\ntitle: [unclosed\n---\n\n" + _SCOPE + "{{include: part.lgd}}\n"
    asked = _Loader({"part.lgd": "## Part {#part}\n\n{{choose: q}} See {{ref: scope}}.\n"})
    result = validate(parse(text, filename="main.lgd", on_frontmatter_error="keep"), resolve=asked)
    assert "frontmatter-invalid-yaml" in result.rules() and not result.index.is_template
    assert not result.rules() & {"choose-invalid", "ref-broken"}
    assert asked.asked == ["part.lgd"]


@pytest.mark.parametrize("part", [
    "## Part {#part when=x}\n\nText.\n", "## Part {#part}\n\n{{choose: q}}\n", "## Part {#part}\n\nText. {when=x}\n",
    "## Part {#part}\n\n- one. {when=x}\n- two\n",
])
def test_a_documents_fragment_constructs_are_not_template_constructs_of_a_document_that_is_none(part):
    # Under the final check too (§15.9): the combined pass does not take them up, as the document before did not.
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    for final in (False, True):
        result = _run(text, {"part.lgd": part}, final=final)
        assert _found(result) == [], (part, final)
    # The document is a template when it declares a question: then they are its constructs, in the fragment.
    result = _run(_TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part}, final=True)
    assert "template-construct-present" in result.rules()


def test_each_fragment_is_asked_for_once_whatever_it_holds():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{include: other.lgd}}\n"
    asked = _Loader({"part.lgd": "## Part {#part}\n\n{{choose: q}}\n", "other.lgd": "## Other {#other}\n\nText.\n"})
    validate(parse(text, filename="main.lgd"), resolve=asked)
    assert sorted(asked.asked) == ["other.lgd", "part.lgd"]
    asked = _Loader({"part.lgd": "## Part {#part when=x}\n\nText.\n", "other.lgd": "## Other {#other}\n\nText.\n"})
    assert Template(text, resolve=asked).validation.is_valid
    assert sorted(asked.asked) == ["other.lgd", "part.lgd"]


def test_a_blank_of_two_types_across_the_document_and_a_fragment_is_found_where_it_is_later():
    files = {"part.lgd": "## Part {#part}\n\nDue {{placeholder: due, type=money}}.\n"}
    first = _FRONT + _SCOPE + "Due {{placeholder: due, type=date}}.\n\n{{include: part.lgd}}\n"
    template = Template(first, resolve=files.get)
    assert _found(template.validation, "placeholder-type-inconsistent") == [("placeholder-type-inconsistent", "part.lgd", 3)]
    # A finding of the combined document stops assembly (§15.7.2), in a fragment as in the template.
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("placeholder-type-inconsistent", "part.lgd", 3)]
    # In the document, as the later of the two, it is the document's own finding and it does (§15.7.2).
    later = _FRONT + _SCOPE + "{{include: part.lgd}}\n\nDue {{placeholder: due, type=date}}.\n"
    template = Template(later, resolve=files.get)
    line = _at(later, "type=date")
    assert _found(template.validation, "placeholder-type-inconsistent") == [("placeholder-type-inconsistent", "", line)]
    assert [(d.rule, d.line) for d in template.problems] == [("placeholder-type-inconsistent", line)]


def test_a_template_rule_the_combined_document_finds_in_a_fragment_stops_assembly():
    text = _TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{placeholder: p}}\n"
    part = "## Part {#part}\n\n*{{placeholder: p}}*\n"
    template = Template(text, resolve={"part.lgd": part}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("insertion-boundary", "part.lgd", 3)]
    # ... an unsafe reference in a fragment to a section the document makes conditional, too (§15.4).
    text = _TEMPLATE_FRONT + "# Scope {#scope}\n\n## Opt {#opt when=x}\n\nText.\n\n# More {#more}\n\n{{include: part.lgd}}\n"
    template = Template(text, resolve={"part.lgd": "## Part {#part}\n\nSee {{ref: opt}}.\n"}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("condition-reference-unsafe", "part.lgd", 3)]
    assert template.form({"x": True}).assemble().ok is False


def test_a_reference_that_is_unsafe_when_the_walk_was_cut_short_does_not_stop_a_template(monkeypatch):
    # Without the combined document the validator cannot see what a fragment declares: advice, as it was.
    text = (
        _TEMPLATE_FRONT + "# One {#one}\n\nSee {{ref: local}}.\n\n{{include: frag.lgd}} {when=!x}\n\n"
        "# Local {#local when=x}\n\nText.\n"
    )
    files = {"frag.lgd": "## Other {#local}\n\nText.\n"}
    assert Template(text, resolve=files.get).problems == ()
    monkeypatch.setattr("legaldown.validator.fragments.MAX_INCLUDE_EXPANDED", 0)
    template = Template(text, resolve=files.get)
    assert any(d.rule == "condition-reference-unsafe" for d in template.validation.diagnostics)
    assert template.problems == ()
    monkeypatch.undo()
    # ... and with no fragment that declares it, the combined document says it is unsafe, and it stops.
    template = Template(text, resolve={"frag.lgd": "## Other {#other}\n\nText.\n"}.get)
    assert [d.rule for d in template.problems] == ["condition-reference-unsafe"]


# ── All or nothing: the combined document is built when every include was followed ───────────────────────────────

def _is_whole(result) -> bool:
    """Whether the Warning no longer names the combined-document checks."""
    warning = _warning(result)
    return warning is None or "combined-document checks" not in warning.message


def test_the_warning_goes_with_the_checks_it_named_when_every_fragment_was_read():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    assert _warning(_run(text, {"part.lgd": "## Part {#part}\n\nText.\n"})) is None
    # ... and stays, naming them, when it was not put together.
    for files in ({}, {"part.lgd": "---\ntitle: No\n---\n\nText.\n"}, {"part.lgd": "# One {#one}\n\nText.\n"}):
        assert "combined-document checks (§16.11)" in _warning(_run(text, files)).message


def test_a_cycle_leaves_the_document_as_it_was():
    text = _FRONT + _SCOPE + "See {{ref: part-sec}}.\n\n{{include: a.lgd}}\n"
    files = {"a.lgd": "## A {#part-sec}\n\n{{include: b.lgd}}\n", "b.lgd": "### B {#b}\n\n{{include: a.lgd}}\n"}
    result = _run(text, files)
    assert _found(result, "include-cycle") == [("include-cycle", "b.lgd", 3)]
    # No combined pass: the reference into a fragment is as broken as it was.
    assert [r for r, _, _ in _found(result, "ref-broken")] == ["ref-broken"]
    assert "combined-document checks" in _warning(result).message


def test_the_depth_and_size_caps_leave_the_document_as_it_was(monkeypatch):
    text = _FRONT + _SCOPE + "See {{ref: deep}}.\n\n{{include: f0.lgd}}\n"
    files = {f"f{n}.lgd": f"## F{n} {{#f{n}}}\n\n{{{{include: f{n + 1}.lgd}}}}\n" for n in range(4)}
    files["f4.lgd"] = "## Deep {#deep}\n\nText.\n"
    assert _is_whole(_run(text, files)) and _found(_run(text, files), "ref-broken") == []
    monkeypatch.setattr("legaldown.validator.fragments.MAX_INCLUDE_DEPTH", 3)
    capped = _run(text, files)
    assert [r for r, _, _ in _found(capped, "ref-broken")] == ["ref-broken"]
    assert "following includes of fragments deeper than 3 levels" in _warning(capped).message
    assert "combined-document checks" in _warning(capped).message
    monkeypatch.undo()
    monkeypatch.setattr("legaldown.validator.fragments.MAX_INCLUDE_EXPANDED", 3)
    capped = _run(text, files)
    assert [r for r, _, _ in _found(capped, "ref-broken")] == ["ref-broken"]
    assert "combined-document checks" in _warning(capped).message


def test_one_fragment_missing_leaves_the_others_unjudged_across_the_document():
    text = _FRONT + _SCOPE + "See {{ref: part-sec}}.\n\n{{include: part.lgd}}\n\n{{include: gone.lgd}}\n"
    result = _run(text, {"part.lgd": "## Part {#part-sec}\n\nText.\n"})
    assert _found(result, "include-file-missing") == [("include-file-missing", "main.lgd", _at(text, "gone.lgd"))]
    assert [r for r, _, _ in _found(result, "ref-broken")] == ["ref-broken"]
    assert "combined-document checks" in _warning(result).message


@pytest.mark.parametrize("kwargs", [{"resolve": None}, {"resolve": lambda path: None}])
def test_without_a_loader_or_with_a_loader_that_reads_nothing_nothing_is_combined(kwargs):
    text = _FRONT + _SCOPE + "See {{ref: part-sec}}.\n\n{{include: part.lgd}}\n"
    result = validate(parse(text, filename="main.lgd"), **kwargs)
    assert [r for r, _, _ in _found(result, "ref-broken")] == ["ref-broken"]
    assert "combined-document checks" in _warning(result).message


def test_a_path_that_leads_out_without_a_root_is_not_read_and_is_not_combined(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "out.lgd").write_text("## Out {#out}\n\nText.\n", encoding="utf-8")
    (tmp_path / "sub" / "main.lgd").write_text(_FRONT + _SCOPE + "See {{ref: out}}.\n\n{{include: ../out.lgd}}\n", encoding="utf-8")
    plain = validate(load(tmp_path / "sub" / "main.lgd"))
    assert [r for r, _, _ in _found(plain, "ref-broken")] == ["ref-broken"]
    rooted = validate(load(tmp_path / "sub" / "main.lgd"), document_root=tmp_path)
    assert _found(rooted, "ref-broken") == []


def test_a_fragment_that_is_not_one_is_reported_and_not_combined():
    text = _FRONT + _SCOPE + "See {{ref: one}}.\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "# One {#one}\n\nText.\n"})
    assert _found(result, "include-has-h1") == [("include-has-h1", "part.lgd", 1)]
    assert [r for r, _, _ in _found(result, "ref-broken")] == ["ref-broken"]


def test_an_include_in_a_templates_fragment_is_not_followed_and_nothing_is_combined():
    text = _TEMPLATE_FRONT + _SCOPE + "See {{ref: deep}}.\n\n{{include: a.lgd}}\n"
    result = _run(text, {"a.lgd": "## A {#a}\n\n{{include: b.lgd}}\n", "b.lgd": "### B {#deep}\n\nText.\n"})
    assert [r for r, _, _ in _found(result, "template-fragment-invalid")] == ["template-fragment-invalid"]
    assert "combined-document checks" in _warning(result).message


def test_attachment_files_are_not_part_of_the_combined_document_yet():
    attached = _FRONT.replace("---\n\n", "attachments:\n  - id: sched\n    title: Schedule\n    file: sched.lgd\n---\n\n")
    text = attached + _SCOPE + "See {{ref: in-schedule}} and {{attach: sched}}.\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "## Part {#part}\n\nText.\n", "sched.lgd": "## Inside {#in-schedule}\n\nText.\n"})
    # The reference into the attachment's section is as it was, and the Warning says so.
    assert [r for r, _, _ in _found(result, "ref-broken")] == ["ref-broken"]
    warning = _warning(result)
    assert "a reference into an attachment's section" in warning.message and "includes" not in warning.message.split("Not performed")[0]


def test_a_conditional_include_in_a_template_is_combined_and_leaves_no_warning():
    text = _TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}} {when=x}\n"
    result = _run(text, {"part.lgd": "## Part {#part}\n\nText.\n"})
    assert _warning(result) is None


# ── The two passes agree where they should ────────────────────────────────────────────────────────────────

_CASES = [
    (
        _FRONT + _SCOPE + 'A "Term" {{def: term}} here. See {{ref: part}}.\n\n{{include: a.lgd}}\n\n## Last\n\nBack {{term: inner}}.\n',
        {"a.lgd": '## Part {#part}\n\nInside "Inner" {{def: inner}} and {{term: term}}.\n\n{{include: b.lgd}}\n', "b.lgd": "### Deeper\n\nSee {{ref: last}}.\n"},
    ),
    (
        _FRONT + _SCOPE + "Pay *{{placeholder: p}}*.\n\n{{include: a.lgd}}\n",
        {"a.lgd": "Fragment {{money: abc, currency=EUR}} <i>x</i>.\n\n## Again {#again}\n\nText {{ref: nowhere}}.\n"},
    ),
    (
        _TEMPLATE_FRONT + _SCOPE + "{{include: a.lgd}} {when=x}\n\n{{include: b.lgd}} {when=!x}\n\nText {{placeholder: p}}.\n",
        {"a.lgd": "## A {#disputes}\n\nA {{choose: x}}\n", "b.lgd": "## B {#disputes}\n\nB.\n"},
    ),
]


@pytest.fixture
def passes(monkeypatch):
    """The two lists of diagnostics ``merge_diagnostics`` was given: the document's own pass and the combined one."""
    given = []
    real = core.merge_diagnostics

    def spy(host, whole, places, combined):
        assert len(places) == len(whole)  # a place for each finding of the combined pass
        given.append((host, whole))
        return real(host, whole, places, combined)

    monkeypatch.setattr(core, "merge_diagnostics", spy)
    return given


@pytest.mark.parametrize(("text", "files"), _CASES)
def test_every_rule_outside_the_classification_is_found_alike_at_the_documents_lines(text, files, passes):
    _run(text, files)
    ((host, whole),) = passes

    def own(diagnostics):
        return Counter(
            (d.rule, d.level, d.line, d.message) for d in diagnostics
            if d.file == "" and d.rule not in WHOLE_DOCUMENT_RULES and d.rule not in HOST_ONLY
        )

    assert own(whole) == own(host)


def test_the_classification_names_only_rules_the_validator_makes():
    from pathlib import Path

    source = "".join(path.read_text(encoding="utf-8") for path in Path(core.__file__).parent.glob("*.py"))
    for rule in WHOLE_DOCUMENT_RULES | HOST_ONLY:
        assert f'"{rule}"' in source, rule
    assert not WHOLE_DOCUMENT_RULES & HOST_ONLY


def _splice(text: str, files: dict[str, str]) -> str:
    """*text* with each include paragraph (a line of its own) replaced by its fragment, as assembly splices the
    text (a blank line around it), the fragments' own includes too."""
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("{{include:") and stripped.endswith("}}") and stripped.count("{{") == 1:
            lines += ["", _splice(files[stripped[len("{{include:"):-2].strip()], files).rstrip("\n"), ""]
        else:
            lines.append(line)
    return "\n".join(lines)


_ORACLE_CASES = [
    _CASES[0],
    _CASES[1],
    (
        _FRONT + _SCOPE + "{{include: a.lgd}}\n\n## Terms\n\nText.\n\n{{include: b.lgd}}\n",
        {"a.lgd": "## Terms\n\nA {{ref: terms-2}} {{ref: nope}}.\n", "b.lgd": "Lead.\n\n## Terms\n\n### Terms\n\nB.\n"},
    ),
    (
        _FRONT + '# Defs {#defs}\n\n"Alpha" {{def: alpha}} uses {{term: beta}}.\n\n{{include: a.lgd}}\n\nEnd {{term: gamma}}.\n',
        {"a.lgd": '## Part {#part}\n\n"Beta" {{def: beta}} uses {{term: alpha}}; "Gamma" {{def: gamma}}.\n'},
    ),
]


_ORACLE_CASES += [
    # A fragment that opens with a paragraph that has an anchor, spliced into a section of the document: the
    # paragraph is that section's, as a splice of the text reads it.
    (
        _FRONT + _SCOPE + "{{include: a.lgd}}\n\nSee {{ref: clause}}.\n",
        {"a.lgd": "A clause. {#clause}\n\n## Sub {#sub}\n\nText {{ref: clause}}.\n"},
    ),
    # Blocks of the document after a fragment that has headings are in its last section, also those of the preamble.
    (
        _FRONT + "{{include: a.lgd}}\n\nLate paragraph. {#late}\n\n# Main {#main}\n\nSee {{ref: late}} and {{ref: sub}}.\n",
        {"a.lgd": "## Sub {#sub}\n\nText.\n"},
    ),
    (
        _FRONT + _SCOPE + "{{include: a.lgd}}\n\nAfter one. {#after}\n\n- item {{ref: after}}\n\n{{include: b.lgd}}\n\nAfter two {{ref: b-sec}}.\n",
        {"a.lgd": "## A {#a-sec}\n\nText.\n", "b.lgd": "Lead {#lead}\n\n## B {#b-sec}\n\nSee {{ref: lead}}.\n"},
    ),
]


_ORACLE_CASES += [
    # A definition that only pandoc or markdown-it reads (#121), across the boundary of a fragment, in both orders:
    # the document's image names the fragment's, and the fragment's the document's.
    (_FRONT + _SCOPE + "See ![a][r].\n\n{{include: a.lgd}}\n", {"a.lgd": "Text.\n\n[r]: /etc/p\x7f\n"}),
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\n[r]: /etc/q\x01\n", {"a.lgd": "See ![a][r].\n"}),
    (_FRONT + _SCOPE + "[r]: /etc/p\x7f\n\n{{include: a.lgd}}\n", {"a.lgd": "See ![a][r].\n\n[r]: /etc/q\n"}),
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\nSee ![a][r].\n\n[r]: /etc/q\n", {"a.lgd": "[r]: /etc/p\\\tx\n\n## S\n"}),
    # First wins in the order of the splice: the fragment's definition that only they read comes before the document's
    # that all read, and the other way round, and each in the fragment's own text.
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\n[r]: /etc/q\n\n![a][r]\n", {"a.lgd": "[r]: ok.png\n\n[r]: /etc/p\x7f\n"}),
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\n[r]: ok.png\n\n![a][r]\n", {"a.lgd": "[r]: /etc/p\x7f\n\n[r]: /etc/q\n"}),
    (_FRONT + _SCOPE + "![a][r]\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n", {"a.lgd": "[r]: ok.png\n", "b.lgd": "[r]: /etc/p\x7f\n"}),
    # A definition read under a setext underline of the fragment, and a label matched by one reading.
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\nSee ![a][r].\n", {"a.lgd": "[r]: /etc/p\x7f\n---\n\n## S\n\ntext\n"}),
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\nSee ![a][a b].\n", {"a.lgd": "[a\u3000b]: /etc/p\n\n## S\n"}),
    # The same fragment twice, and a fragment in a fragment.
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: a.lgd}}\n\nSee ![a][r].\n", {"a.lgd": "Lead.\n\n[r]: /etc/p\x7f\n\n## S\n"}),
    (_FRONT + _SCOPE + "{{include: a.lgd}}\n\nSee ![a][r].\n", {"a.lgd": "{{include: b.lgd}}\n", "b.lgd": "[r]: /etc/p\x01\n\n## S\n"}),
]


@pytest.mark.parametrize(("text", "files"), _ORACLE_CASES)
def test_the_combined_pass_equals_validating_the_spliced_text(text, files, monkeypatch):
    """The differential test: the text of the fragments spliced in and validated as one document says the same
    (the rules, the messages and the identifiers of the sections), but for what is about the includes and files."""
    seen = []
    real = core._check

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        if kwargs.get("combined") is not None:
            seen.append(result)
        return result

    monkeypatch.setattr(core, "_check", spy)
    merged = _run(text, files)
    (combined,) = seen
    oracle = validate(parse(_splice(text, files)))

    def counted(diagnostics):
        # (A line of the spliced text is no line of any file: a message that names one is as it is in each, and a path
        # is relative to the file it is in: the fragment's own, which the spliced text has none of.)
        return Counter(
            (d.rule, d.level, re.sub(r"relative to the fragment", "relative to the document", re.sub(r" at line \d+( of \S+)?", "", d.message)))
            for d in diagnostics if d.rule not in HOST_ONLY
        )

    assert counted(combined.diagnostics) == counted(oracle.diagnostics)
    assert [e.identifier for e in combined.index.sections] == [e.identifier for e in oracle.index.sections]
    # ... and what the document reports is the combined pass's (but for the include rules the document's own makes).
    assert counted(merged.diagnostics) == counted(oracle.diagnostics)


def test_what_only_the_documents_own_pass_can_judge_is_reported_once():
    # The heading levels are judged by include-heading-skip, which tries every combination: the combined pass
    # does not say heading-skip as well. The paths of a fragment are its own, not judged from the document's directory.
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "#### Deep {#deep}\n\n![x](../shared/x.png)\n"})
    assert _found(result, "include-heading-skip", "heading-skip", "path-outside-root", "path-not-relative") == [
        ("include-heading-skip", "part.lgd", 1)
    ]


def _random_case(seed: int) -> tuple[str, dict[str, str]]:
    """A document that includes three fragments (one of them in another), of random blocks."""
    rng = random.Random(seed)
    titles = ["Terms", "Scope", "Fees", "Notice"]

    def blocks(depths: list[str]) -> list[str]:
        found = []
        for _ in range(rng.randint(2, 6)):
            kind = rng.choice(["term", "ref", "def", "head", "blank", "para", "head"])
            if kind == "term":
                found.append(f"Uses {{{{term: d{rng.randint(1, 5)}}}}}.")
            elif kind == "ref":
                found.append(f"See {{{{ref: s{rng.randint(1, 5)}}}}} and {{{{ref: {rng.choice(titles).lower()}}}}}.")
            elif kind == "def":
                k = rng.randint(1, 5)
                means = rng.choice(["x", f"{{{{term: d{rng.randint(1, 5)}}}}}"])
                found.append(f'"D{k}" {{{{def: d{k}}}}} means {means}.')
            elif kind == "head":
                level = rng.choice(depths)
                found.append(f"{level} Heading {{#s{rng.randint(1, 5)}}}" if rng.random() < 0.5 else f"{level} {rng.choice(titles)}")
            elif kind == "blank":
                found.append(f"Pay {{{{placeholder: p{rng.randint(1, 3)}, type={rng.choice(['money', 'date'])}}}}}.")
            else:
                found.append("Plain text. {{money: 5, currency=EUR}} <b>x</b>")
        return found

    files = {name: blocks(["##", "###"]) for name in ("a.lgd", "b.lgd", "c.lgd")}
    nested = rng.random() < 0.5
    if nested:
        files["a.lgd"].insert(rng.randint(0, len(files["a.lgd"])), "{{include: c.lgd}}")
    host = ["# Main {#main}", *blocks(["##"])]
    for name in ("a.lgd", "b.lgd") + (() if nested else ("c.lgd",)):
        host.insert(rng.randint(1, len(host)), f"{{{{include: {name}}}}}")
    return _FRONT + "\n\n".join(host) + "\n", {name: "\n\n".join(found) + "\n" for name, found in files.items()}


@pytest.mark.parametrize("seed", range(40))
def test_the_combined_pass_equals_validating_the_spliced_text_for_random_documents(seed, monkeypatch):
    text, files = _random_case(seed)
    seen = []
    real = core._check

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        if kwargs.get("combined") is not None:
            seen.append(result)
        return result

    monkeypatch.setattr(core, "_check", spy)
    merged = _run(text, files)
    (combined,) = seen
    oracle = validate(parse(_splice(text, files)))

    def counted(diagnostics, *, owned: bool = True):
        return Counter(
            (d.rule, d.level, re.sub(r" at line \d+( of \S+)?", "", d.message)) for d in diagnostics
            if d.rule not in HOST_ONLY and (owned or d.rule != "anchor-duplicate")
        )

    assert counted(combined.diagnostics) == counted(oracle.diagnostics)
    assert [e.identifier for e in combined.index.sections] == [e.identifier for e in oracle.index.sections]
    # What the document reports is that, but for the section identifiers that are include-anchor-duplicate's to report.
    assert counted(merged.diagnostics, owned=False) == counted(oracle.diagnostics, owned=False)


# ── Fragments that are malformed keep their own structure ─────────────────────────────────────────────────────

def test_a_fence_a_fragment_leaves_open_does_not_swallow_the_document_that_follows():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\nAfter {{ref: gone}}.\n"
    result = _run(text, {"part.lgd": "## Part {#part}\n\n```\ncode\n"})
    # A splice of the text would make `After` part of the fence, and the reference text: here it is a paragraph.
    assert _found(result, "ref-broken") == [("ref-broken", "main.lgd", _at(text, "After"))]
    spliced = validate(parse(_splice(text, {"part.lgd": "## Part {#part}\n\n```\ncode\n"})))
    assert "ref-broken" not in spliced.rules()


def test_a_list_a_fragment_ends_with_is_not_continued_by_the_document():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n- second {{ref: gone}}\n"
    result = _run(text, {"part.lgd": "## Part {#part}\n\n- first {{ref: gone-too}}\n"})
    assert _found(result, "ref-broken") == [
        ("ref-broken", "part.lgd", 3), ("ref-broken", "main.lgd", _at(text, "- second")),
    ]


# ── Cost ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_each_file_is_read_and_parsed_once_however_often_it_is_included(monkeypatch):
    import legaldown.parser as parser_module

    parsed = []
    real = parser_module.parse
    monkeypatch.setattr(parser_module, "parse", lambda text, *a, **k: parsed.append(text) or real(text, *a, **k))
    text = _FRONT + _SCOPE + "".join(f"{{{{include: a.lgd}}}}\n\n## Next {n} {{#next-{n}}}\n\n" for n in range(20))
    loader = _Loader({"a.lgd": "### Part\n\nSee {{ref: scope}}.\n"})
    validate(parse(text, filename="main.lgd"), resolve=loader)
    assert loader.asked.count("a.lgd") == 1
    assert parsed.count("### Part\n\nSee {{ref: scope}}.\n") == 1


def _seconds(function) -> float:
    started = time.perf_counter()
    function()
    return time.perf_counter() - started


@pytest.mark.scaling
def test_a_deep_chain_and_many_fragments_cost_what_the_same_content_in_one_document_costs():
    depth = MAX_INCLUDE_DEPTH - 1
    chain = {f"c{n}.lgd": f"{'#' * (min(n + 2, 6))} C{n} {{#c{n}}}\n\n{{{{include: c{n + 1}.lgd}}}}\n" for n in range(depth)}
    chain[f"c{depth}.lgd"] = "###### Last {#last}\n\nSee {{ref: c0}}.\n"
    chain = {path: text.replace("######## ", "###### ").replace("####### ", "###### ") for path, text in chain.items()}
    text = _FRONT + _SCOPE + "{{include: c0.lgd}}\n"
    result = _run(text, chain)
    assert _found(result, "ref-broken") == [] and _warning(result) is None

    many = {f"m{n}.lgd": f"## M{n} {{#m{n}}}\n\n" + "".join(f"Line {k} of {{{{ref: scope}}}}.\n\n" for k in range(50)) for n in range(300)}
    includes = "".join(f"{{{{include: m{n}.lgd}}}}\n\n" for n in range(300))
    host = _FRONT + _SCOPE + includes
    one_text = _FRONT + _SCOPE + "".join(many.values())
    apart = _seconds(lambda: _run(host, many))
    together = _seconds(lambda: validate(parse(one_text)))
    assert apart < 4 * together + 1.0

    diamond = _FRONT + _SCOPE + "".join(f"{{{{include: shared.lgd}}}}\n\n## Next {n} {{#next-{n}}}\n\n" for n in range(500))
    shared = {"shared.lgd": "### Shared\n\n" + "Text {{ref: scope}}.\n\n" * 20}
    assert _seconds(lambda: _run(diamond, shared)) < 20


# ── Size, order, dedup, once ──────────────────────────────────────────────────────────────────────────────

def test_a_fragment_of_many_blocks_included_many_times_is_not_combined_past_the_cap():
    part = "".join(f"Paragraph {k}.\n\n" for k in range(400))
    text = _FRONT + _SCOPE + "".join("{{include: part.lgd}}\n\n" for _ in range(400))
    started = time.perf_counter()
    result = _run(text, {"part.lgd": part})
    # 160,000 blocks past the cap: what the walk did is as it was (0.1 s), not the product of the two.
    assert time.perf_counter() - started < 10
    message = _warning(result).message
    assert "combined-document checks (§16.11)" in message and "building the combined document of more than 50,000 blocks" in message


def test_the_caps_on_the_combined_document_are_in_blocks_and_in_lines(monkeypatch):
    text = _FRONT + _SCOPE + "See {{ref: part-sec}}.\n\n{{include: part.lgd}}\n\n{{include: part.lgd}}\n"
    files = {"part.lgd": "## Part {#part-sec}\n\nOne.\n\nTwo.\n"}
    assert _warning(_run(text, files)) is None
    monkeypatch.setattr("legaldown.validator.fragments.MAX_SPLICED_BLOCKS", 3)  # 2 x 2 blocks
    assert "building the combined document of more than 3 blocks" in _warning(_run(text, files)).message
    monkeypatch.undo()
    monkeypatch.setattr("legaldown.validator.fragments.MAX_SPLICED_LINES", 11)  # 2 x 6 lines
    assert "lines of fragments" in _warning(_run(text, files)).message
    # Within the caps the combined document is made: a reference into the fragment is not broken.
    monkeypatch.undo()
    monkeypatch.setattr("legaldown.validator.fragments.MAX_SPLICED_BLOCKS", 2)
    assert _found(_run(text.replace("{{include: part.lgd}}\n\n{{include: part.lgd}}", "{{include: part.lgd}}"), files), "ref-broken") == []


def test_the_findings_are_in_the_order_of_the_combined_document_and_the_warning_is_last():
    skipping = "# One {#one}\n\n### Skips {#skips}\n\nText.\n\n"
    text = (
        _FRONT + skipping + "{{include: part.lgd}}\n\n" + "Text.\n\n" * 20 + "After {{ref: gone}}.\n\n"
        "## Two {#two}\n\n{{include: other.lgd}}\n\nThen {{ref: gone-too}}.\n"
    )
    files = {"part.lgd": "## Part {#part}\n\nSee {{ref: gone-part}}.\n", "other.lgd": "### Other {#other}\n\n{{ref: gone-other}}\n"}
    result = _run(text, files)
    found = [(d.rule, d.file, d.line) for d in result.diagnostics if d.rule != SKIPPED_RULE]
    assert [rule for rule, _, _ in found].count("heading-skip") == 1
    assert found == [
        ("heading-skip", "main.lgd", _at(text, "### Skips")),
        ("ref-broken", "part.lgd", 3),
        ("ref-broken", "main.lgd", _at(text, "After")),
        ("ref-broken", "other.lgd", 3),
        ("ref-broken", "main.lgd", _at(text, "Then")),
    ]
    assert result.diagnostics[-1].rule == SKIPPED_RULE or _warning(result) is None


def test_two_findings_on_one_line_of_a_fragment_are_two_and_a_fragment_included_twice_makes_each_once():
    part = "## Part {#part}\n\nSee {{ref: gone}} and {{ref: gone}}.\n"
    once = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(once, "ref-broken") == [("ref-broken", "part.lgd", 3)] * 2
    twice = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n\n## Again {#again}\n\n{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(twice, "ref-broken") == [("ref-broken", "part.lgd", 3)] * 2
    # The document alone is not deduplicated either.
    both = _run(_FRONT + _SCOPE + "{{ref: gone}} and {{ref: gone}}.\n", {})
    assert len(_found(both, "ref-broken")) == 2


def test_a_fragment_included_in_several_places_makes_each_distinct_finding_once():
    # The auto-generated identifier is adjusted to part-2 in one place and part-3 in the next: a finding whose message
    # differs by place is reported for each place; the same finding made in every place, once.
    text = (
        _FRONT + _SCOPE + "## Part\n\nText.\n\n{{include: part.lgd}}\n\n## Again {#again}\n\n{{include: part.lgd}}\n\n"
        "## More {#more}\n\n{{include: part.lgd}}\n"
    )
    result = _run(text, {"part.lgd": "## Part\n\nSee {{ref: gone}}.\n"})
    assert _found(result, "anchor-autogen-collision") == [("anchor-autogen-collision", "part.lgd", 1)] * 3
    assert [d.message.split("adjusted to '")[1].split("'")[0] for d in result.diagnostics if d.rule == "anchor-autogen-collision"] == [
        "part-2", "part-3", "part-4"
    ]
    assert _found(result, "ref-broken") == [("ref-broken", "part.lgd", 3)]


def test_findings_of_different_targets_in_the_places_of_a_fragment_are_all_kept():
    # A reference in a fragment that is unsafe to one section in one place and to another in the next: both are findings.
    text = (
        _TEMPLATE_FRONT + _SCOPE + "## A {#a when=x}\n\nA.\n\n## B {#b when=z}\n\nB.\n\n## P1 {#p1 when=x}\n\n{{include: part.lgd}}\n\n"
        "## P2 {#p2 when=z}\n\n{{include: part.lgd}}\n"
    )
    result = _run(text, {"part.lgd": "See {{ref: a}} and {{ref: b}}.\n"})
    messages = [d.message for d in result.diagnostics if d.rule == "condition-reference-unsafe"]
    assert len(messages) == 2 and any("'a'" in m for m in messages) and any("'b'" in m for m in messages)
    assert {(d.file, d.line) for d in result.diagnostics if d.rule == "condition-reference-unsafe"} == {("part.lgd", 1)}


def test_an_absolute_path_or_a_uri_in_a_fragment_is_not_relative_wherever_the_fragment_is():
    text = _FRONT + _SCOPE + "{{include: sub/part.lgd}}\n"
    part = "## Part {#part}\n\n![x](/etc/passwd.png) ![y](C:\\x.png) ![z](https://x/y.png) ![w](../w.png)\n"
    result = _run(text, {"sub/part.lgd": part})
    assert _found(result, "path-not-relative", "path-outside-root") == [("path-not-relative", "sub/part.lgd", 3)] * 3
    assert all("relative to the fragment" in d.message for d in result.diagnostics if d.rule == "path-not-relative")
    # The document's own say the document.
    own = _run(_FRONT + _SCOPE + "![x](/abs.png)\n\n{{include: part.lgd}}\n", {"part.lgd": "Text.\n"})
    assert [d.message for d in own.diagnostics if d.rule == "path-not-relative"][0].endswith("relative to the document (§2.3).")


def test_a_definition_of_the_document_that_only_a_fragments_image_uses_is_judged_where_it_is_written():
    # A link reference definition is judged where it is written, once something uses it: here a fragment does.
    text = _FRONT + _SCOPE + "Text.\n\n[r]: /abs.png\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "See ![a][r].\n"})
    assert _found(result, "path-not-relative") == [("path-not-relative", "main.lgd", _at(text, "[r]:"))]
    # ... and the other way round: a definition of the fragment that an image of the document uses.
    text = _FRONT + _SCOPE + "See ![a][r].\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "Text.\n\n[r]: /abs.png\n"})
    assert _found(result, "path-not-relative") == [("path-not-relative", "part.lgd", 3)]


def test_a_definition_only_pandoc_or_markdown_it_reads_is_judged_where_it_is_written_across_a_fragment():
    # The document's image names the fragment's definition (a DEL in its bare destination), and the other way round.
    text = _FRONT + _SCOPE + "See ![a][r].\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "Text.\n\n[r]: /etc/p\x7f\n"})
    assert _found(result, "path-not-relative") == [("path-not-relative", "part.lgd", 3)]
    assert "relative to the fragment" in next(d.message for d in result.diagnostics if d.rule == "path-not-relative")
    text = _FRONT + _SCOPE + "Text.\n\n[r]: /etc/p\x7f\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "See ![a][r].\n"})
    assert _found(result, "path-not-relative") == [("path-not-relative", "main.lgd", _at(text, "[r]:"))]
    assert "relative to the document" in next(d.message for d in result.diagnostics if d.rule == "path-not-relative")


def test_a_definition_in_a_fragment_that_leaves_the_root_is_judged_as_a_strict_one_is():
    # (``path-outside-root`` is the document's own pass's: a fragment's path is no path from the document's directory.)
    text = _FRONT + _SCOPE + "See ![a][r].\n\n{{include: part.lgd}}\n"
    for definition in ("[r]: ../../x\x7f", "[r]: ../../x"):
        result = _run(text, {"part.lgd": f"Text.\n\n{definition}\n"}, document_root="/r")
        assert _found(result, "path-outside-root", "path-not-relative") == []


def test_the_first_definition_of_a_label_in_the_order_of_the_splice_is_the_one_judged():
    files = {"a.lgd": "[r]: ok.png\n", "b.lgd": "[r]: /etc/p\x7f\n"}
    text = _FRONT + _SCOPE + "![a][r]\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n"
    assert _found(_run(text, files), "path-not-relative") == []
    text = _FRONT + _SCOPE + "![a][r]\n\n{{include: b.lgd}}\n\n{{include: a.lgd}}\n"
    assert _found(_run(text, files), "path-not-relative") == [("path-not-relative", "b.lgd", 1)]
    # The document's own comes first when it is first, and a fragment's that it comes before is not judged.
    text = _FRONT + _SCOPE + "[r]: ok.png\n\n![a][r]\n\n{{include: b.lgd}}\n"
    assert _found(_run(text, files), "path-not-relative") == []


def test_the_definitions_of_each_file_are_read_again_once_however_often_it_is_included(monkeypatch):
    from legaldown.markdown import Reading
    from legaldown.validator import path_definitions

    readings: list[Reading] = []
    real = path_definitions.definitions_read_as

    def counted(reading):
        readings.append(reading)
        return real(reading)

    monkeypatch.setattr(path_definitions, "definitions_read_as", counted)
    text = _FRONT + _SCOPE + "[r]: /etc/p\x7f\n\n![a][r]\n\n{{include: part.lgd}}\n\n{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "Lead.\n\n[s]: /etc/q\x7f\n\n![a][s]\n"})
    # The document and the fragment, each once for the one reading a DEL differs in, for the two passes together.
    assert readings == [Reading.PANDOC, Reading.PANDOC]
    assert [d.file for d in result.diagnostics if d.rule == "path-not-relative"].count("part.lgd") == 1


def test_the_importers_are_called_once_for_each_file_however_often_the_document_is_read():
    front = _FRONT.replace("---\n\n", "amends:\n  title: Original\n  file: orig.lgd\nattachments:\n  - id: a\n    title: A\n    file: a.lgd\n---\n\n")
    text = front + _SCOPE + "{{attach: a}} {{include: part.lgd}}\n\n{{include: part.lgd}}\n"
    asked: list[str] = []

    def amends(path, filename):
        asked.append(path)
        return {"orig": "Original"}

    def attachment(path):
        asked.append(path)
        return {"x": "X"}

    with pytest.warns(DeprecationWarning):
        validate(
            parse(text, filename="main.lgd"), resolve={"part.lgd": "## Part {#part}\n\nText.\n"}.get,
            import_definitions=amends, import_attachment_definitions=attachment,
        )
    assert sorted(asked) == ["a.lgd", "orig.lgd"]


def test_a_line_of_a_fragment_keeps_its_file_through_arithmetic():
    from legaldown.validator.result import Spot

    spot = Spot(3, "part.lgd", 2)
    for computed in (spot + 2, 2 + spot, spot - 1, 5 - spot):
        assert isinstance(computed, Spot) and (computed.file, computed.instance) == ("part.lgd", 2)
    assert (spot + 2, 2 + spot, spot - 1, 5 - spot) == (5, 5, 2, 2)
    assert Spot(None, "part.lgd") == 0 and not Spot(None, "part.lgd")
