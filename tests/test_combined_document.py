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


def test_a_condition_or_a_choice_only_a_fragment_holds_makes_the_combined_document_a_template():
    # §15.1: a document that holds a {{choose:}} is a template, wherever it is. The document alone is none, and the
    # fragment alone is not told by the document that includes it; the combined document is judged as the template it is.
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n"
    result = _run(text, {"part.lgd": "## Part {#part}\n\n{{choose: q}}\n"})
    assert _found(result, "choose-invalid") == [("choose-invalid", "part.lgd", 3)]
    conditional = _run(text, {"part.lgd": "## Part {#part} {when=x}\n\nText.\n"})
    assert [r for r, _, _ in _found(conditional, "condition-invalid")] == ["condition-invalid"]
    assert _found(conditional, "condition-invalid")[0][1:] == ("part.lgd", 1)


def test_a_blank_of_two_types_across_the_document_and_a_fragment_is_found_where_it_is_later():
    files = {"part.lgd": "## Part {#part}\n\nDue {{placeholder: due, type=money}}.\n"}
    first = _FRONT + _SCOPE + "Due {{placeholder: due, type=date}}.\n\n{{include: part.lgd}}\n"
    template = Template(first, resolve=files.get)
    assert _found(template.validation, "placeholder-type-inconsistent") == [("placeholder-type-inconsistent", "part.lgd", 3)]
    # A finding in a fragment does not stop assembly: the template is read as the files are.
    assert template.problems == ()
    # In the document, as the later of the two, it is the document's own finding and it does (§15.7.2).
    later = _FRONT + _SCOPE + "{{include: part.lgd}}\n\nDue {{placeholder: due, type=date}}.\n"
    template = Template(later, resolve=files.get)
    line = _at(later, "type=date")
    assert _found(template.validation, "placeholder-type-inconsistent") == [("placeholder-type-inconsistent", "", line)]
    assert [(d.rule, d.line) for d in template.problems] == [("placeholder-type-inconsistent", line)]


def test_a_fragment_finding_does_not_stop_assembly_of_a_template():
    text = _TEMPLATE_FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{placeholder: p}}\n"
    part = "## Part {#part}\n\n*{{placeholder: p}}*\n"
    template = Template(text, resolve={"part.lgd": part}.get)
    assert any(d.rule == "insertion-boundary" and d.file == "part.lgd" for d in template.validation.diagnostics)
    assert not any(d.rule == "insertion-boundary" for d in template.problems)


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
    monkeypatch.setattr(core, "merge_diagnostics", lambda host, whole: given.append((host, whole)) or real(host, whole))
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


@pytest.mark.parametrize(("text", "files"), _ORACLE_CASES)
def test_the_combined_pass_equals_validating_the_spliced_text(text, files, monkeypatch):
    """The differential test: the text of the fragments spliced in and validated as one document says the same
    (the rules, the messages and the identifiers of the sections), but for what is about the includes and files."""
    seen = []
    real = core._validate

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        if kwargs.get("combined") is not None:
            seen.append(result)
        return result

    monkeypatch.setattr(core, "_validate", spy)
    merged = _run(text, files)
    (combined,) = seen
    oracle = validate(parse(_splice(text, files)))

    def counted(diagnostics):
        # (A line of the spliced text is no line of any file: a message that names one is as it is in each.)
        return Counter(
            (d.rule, d.level, re.sub(r" at line \d+( of \S+)?", "", d.message)) for d in diagnostics if d.rule not in HOST_ONLY
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
    real = core._validate

    def spy(*args, **kwargs):
        result = real(*args, **kwargs)
        if kwargs.get("combined") is not None:
            seen.append(result)
        return result

    monkeypatch.setattr(core, "_validate", spy)
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
