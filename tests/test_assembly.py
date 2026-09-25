"""Template assembly (LegalDown §15.7), against the conformance fixtures and
the rules they do not reach.

Assembly is defined byte for byte — "no other byte of the template changes"
(§15.7.2) — so two conforming implementations produce identical output. The
fixtures are the specification's own conformance cases (``fixtures/assembly``
in the LegalDown repository), read from the checkout that
``LEGALDOWN_FIXTURES_DIR`` names, as the conformance suite reads them; without
one the fixture tests are skipped. Every output file must match exactly.

The focused tests below pin the parts of §15.7 a fixture exercises only once,
or not at all: which answers stop assembly, how inserted text is escaped, when
a line start needs a backslash, and what a draft keeps.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest
import yaml

from legaldown import (
    AssemblyResult,
    assemble,
    needed_questions,
    parse_document,
    template_questions,
    validate_document,
)

FIXTURES = Path(os.environ.get("LEGALDOWN_FIXTURES_DIR", "")) / "assembly"
CASES = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir()) if FIXTURES.is_dir() else []
needs_fixtures = pytest.mark.skipif(
    not CASES, reason="LEGALDOWN_FIXTURES_DIR not set (LegalDown specification fixtures corpus)"
)


def _loader(case: Path):
    """``load_file`` for a fixture case: files relative to its template."""

    def load(path: str) -> str | None:
        target = case / path
        return target.read_text(encoding="utf-8") if target.is_file() else None

    return load


def _assemble_case(name: str) -> AssemblyResult:
    case = FIXTURES / name
    answers = yaml.safe_load((case / "answers.yaml").read_text(encoding="utf-8")) or {}
    template = (case / "template.lgd").read_text(encoding="utf-8")
    return assemble(template, answers, load_file=_loader(case))


def _expected(name: str) -> dict[str, str]:
    """Every output file the case expects, by path; the template is
    ``template.lgd`` (fixtures/README.md)."""
    case = FIXTURES / name
    if (case / "expected.lgd").is_file():
        return {"template.lgd": (case / "expected.lgd").read_text(encoding="utf-8")}
    tree = case / "expected"
    return {
        path.relative_to(tree).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(tree.rglob("*"))
        if path.is_file()
    }


# ── The conformance fixtures ─────────────────────────────────────


@needs_fixtures
def test_the_corpus_has_six_cases():
    assert CASES == [
        "consulting",
        "draft-and-line-starts",
        "escaping",
        "frontmatter-and-defaults",
        "identifier-preservation",
        "multi-file",
    ]


@pytest.mark.parametrize("name", [c for c in CASES if (FIXTURES / c / "expected.lgd").is_file()])
def test_single_file_case_matches_byte_for_byte(name):
    result = _assemble_case(name)
    assert result.diagnostics == []
    assert result.output == _expected(name)["template.lgd"]
    assert result.files == {}


@needs_fixtures
def test_multi_file_case_writes_exactly_the_expected_tree():
    """Full level (case.json): a kept fragment is filled, a removed include
    writes nothing, an emptied attachment file is written as zero bytes, and a
    non-LegalDown attachment is not assembly output."""
    assert json.loads((FIXTURES / "multi-file" / "case.json").read_text())["requires_level"] == "full"
    result = _assemble_case("multi-file")
    assert result.diagnostics == []
    assert {"template.lgd": result.output, **result.files} == _expected("multi-file")
    assert result.files["schedules/data.lgd"] == ""


@pytest.mark.parametrize("name", CASES)
def test_fixture_template_and_its_output_have_no_errors(name):
    """The fixtures' premise (a template without Errors) and the assembly
    guarantee (§15.7.4): its output has none either."""
    template = (FIXTURES / name / "template.lgd").read_text(encoding="utf-8")
    assert validate_document(parse_document(template)).errors == []
    result = _assemble_case(name)
    assert validate_document(parse_document(result.output)).errors == []


# ── Helpers for the focused tests ────────────────────────────────

_SIDES = """sides:
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
"""


def _template(body: str, questions: str = "", front: str = "") -> str:
    """A contract with *questions* (YAML indented under ``questions:``) and
    any extra *front* matter, then *body*."""
    block = f"questions:\n{questions}" if questions else ""
    return f"---\ntitle: Fixture\ndocument_type: contract\n{block}{_SIDES}{front}---\n\n{body}"


def _body(result: AssemblyResult) -> str:
    assert result.ok, result.diagnostics
    return result.output.split("\n---\n", 1)[1]


def _rules(result: AssemblyResult) -> list[tuple[str, str]]:
    return [(d.rule, d.level) for d in result.diagnostics]


_BOOL = "  x:\n    type: boolean\n"
_TEXT = "  name:\n    type: text\n"


# ── Step 1: answers ──────────────────────────────────────────────


class TestAnswers:
    def test_a_needed_decision_without_answer_stops_assembly(self):
        result = assemble(_template("Text. {when=x}\n", _BOOL), {})
        assert _rules(result) == [("answer-missing", "error")]
        assert (result.output, result.files) == ("", {})

    def test_a_question_asked_only_inside_an_absent_section_is_not_needed(self):
        body = '# Extra {when=x}\n\nIt is {{choose: y, true="on", false="off"}}.\n\n# Main\n\nKept.\n'
        questions = _BOOL + "  y:\n    type: boolean\n"
        result = assemble(_template(body, questions), {"x": False})
        assert _body(result) == "\n# Main\n\nKept.\n"

    def test_a_question_asked_only_in_a_drafting_note_is_not_needed(self):
        body = '> [!DRAFTING]\n> Say {{choose: x, true="a", false="b"}}.\n\nText.\n'
        assert _body(assemble(_template(body, _BOOL), {})) == "\nText.\n"

    def test_a_default_answers_a_decision_and_a_blank(self):
        questions = (
            "  x:\n    type: boolean\n    default: true\n"
            "  name:\n    type: text\n    default: Acme\n"
        )
        result = assemble(_template("Hi {{placeholder: name}}. {when=x}\n", questions), {})
        assert _body(result) == "\nHi Acme.\n"

    def test_an_answer_overrides_the_default(self):
        questions = "  x:\n    type: boolean\n    default: true\n"
        assert _body(assemble(_template("Text. {when=x}\n\nEnd.\n", questions), {"x": False})) == "\nEnd.\n"

    @pytest.mark.parametrize("answer", ["yes", 1, "true"])
    def test_a_wrong_form_is_invalid(self, answer):
        result = assemble(_template("Text. {when=x}\n", _BOOL), {"x": answer})
        assert ("answer-invalid", "error") in _rules(result)
        assert result.output == ""

    def test_an_unknown_answer_is_a_warning_and_ignored(self):
        result = assemble(_template("Text.\n"), {"nobody": "asked"})
        assert _rules(result) == [("answer-unknown", "warning")]
        assert _body(result) == "\nText.\n"

    def test_a_placeholder_id_is_a_known_answer(self):
        result = assemble(_template("Hi {{placeholder: who}}.\n"), {"who": "Ann"})
        assert result.diagnostics == []
        assert _body(result) == "\nHi Ann.\n"

    def test_a_text_answer_holding_braces_is_invalid_for_frontmatter(self):
        """It would reopen a directive in the scalar (§15.7.1)."""
        template = _template("Hi.\n", _TEXT, front='governing_law: "{{placeholder: name}}"\n')
        result = assemble(template, {"name": "Acme {{x}}"})
        assert _rules(result) == [("answer-invalid", "error")]

    def test_the_same_answer_is_fine_in_the_body_where_it_is_escaped(self):
        result = assemble(_template("Hi {{placeholder: name}}.\n", _TEXT), {"name": "Acme {{x}}"})
        assert _body(result) == "\nHi Acme \\{\\{x}}.\n"

    @pytest.mark.parametrize("answer", ["2026-10-01", dt.date(2026, 10, 1)])
    def test_a_date_is_a_string_or_a_yaml_date(self, answer):
        template = _template("On {{placeholder: day, type=date}}.\n")
        assert _body(assemble(template, {"day": answer})) == "\nOn {{date: 2026-10-01}}.\n"

    def test_a_date_with_a_time_is_invalid(self):
        template = _template("On {{placeholder: day, type=date}}.\n")
        result = assemble(template, {"day": dt.datetime(2026, 10, 1, 9, 30)})
        assert _rules(result) == [("answer-invalid", "error")]


class TestMoneyAndDuration:
    """§15.7.1: a map, or the bare amount or value when every placeholder of
    the question fixes the same currency or unit."""

    MONEY = "  fee:\n    type: money\n"
    DURATION = "  term:\n    type: duration\n"

    def test_bare_amount_takes_the_fixed_currency(self):
        template = _template("Pay {{placeholder: fee, currency=EUR}}.\n", self.MONEY)
        assert _body(assemble(template, {"fee": "100.50"})) == "\nPay {{money: 100.50, currency=EUR}}.\n"

    def test_a_map_agreeing_with_the_fixed_currency(self):
        template = _template("Pay {{placeholder: fee, currency=EUR}}.\n", self.MONEY)
        result = assemble(template, {"fee": {"amount": "7", "currency": "EUR"}})
        assert _body(result) == "\nPay {{money: 7, currency=EUR}}.\n"

    def test_a_map_disagreeing_with_the_fixed_currency_is_invalid(self):
        template = _template("Pay {{placeholder: fee, currency=EUR}}.\n", self.MONEY)
        result = assemble(template, {"fee": {"amount": "7", "currency": "USD"}})
        assert _rules(result) == [("answer-invalid", "error")]

    def test_a_map_supplies_the_currency_the_placeholder_leaves_open(self):
        template = _template("Pay {{placeholder: fee, note=Net}}.\n", self.MONEY)
        result = assemble(template, {"fee": {"amount": "7", "currency": "CZK"}})
        assert _body(result) == "\nPay {{money: 7, currency=CZK, note=Net}}.\n"

    def test_a_bare_amount_without_a_fixed_currency_is_invalid(self):
        template = _template("Pay {{placeholder: fee}}.\n", self.MONEY)
        assert _rules(assemble(template, {"fee": "7"})) == [("answer-invalid", "error")]

    def test_a_note_is_kept_exactly_as_written(self):
        template = _template('Pay {{placeholder: fee, currency=EUR, note="net, of VAT"}}.\n', self.MONEY)
        result = assemble(template, {"fee": "7"})
        assert _body(result) == '\nPay {{money: 7, currency=EUR, note="net, of VAT"}}.\n'

    @pytest.mark.parametrize("answer", [30, "30"])
    def test_bare_value_takes_the_fixed_unit(self, answer):
        template = _template("For {{placeholder: term, unit=D}}.\n", self.DURATION)
        assert _body(assemble(template, {"term": answer})) == "\nFor {{duration: 30, unit=D}}.\n"

    def test_a_duration_map(self):
        template = _template("For {{placeholder: term}}.\n", self.DURATION)
        result = assemble(template, {"term": {"value": "1.5", "unit": "Y"}})
        assert _body(result) == "\nFor {{duration: 1.5, unit=Y}}.\n"

    def test_a_float_value_is_invalid(self):
        """A YAML float can alter the digits (§15.7.1)."""
        template = _template("For {{placeholder: term, unit=D}}.\n", self.DURATION)
        assert _rules(assemble(template, {"term": 1.5})) == [("answer-invalid", "error")]

    def test_in_frontmatter_as_plain_text(self):
        template = _template("Hi.\n", self.MONEY, front='governing_law: "{{placeholder: fee, currency=EUR}}"\n')
        result = assemble(template, {"fee": "9"})
        assert 'governing_law: "9 EUR"\n' in result.output

    @pytest.mark.parametrize("front,expected", [
        # A party written in flow style: its quoted scalar is a scalar like
        # any other, and was once left unfilled because only a block value
        # was recognized.
        ('x: [{a: b, name: "{{placeholder: who}}"}]\n', 'x: [{a: b, name: "Smith \\"S\\" Ltd"}]\n'),
        ("x: {name: '{{placeholder: who}}'}\n", "x: {name: 'Smith \"S\" Ltd'}\n"),
        ('x:\n  - "{{placeholder: who}}"\n', 'x:\n  - "Smith \\"S\\" Ltd"\n'),
    ])
    def test_in_any_quoted_scalar(self, front, expected):
        result = assemble(_template("Hi.\n", front=front), {"who": 'Smith "S" Ltd'})
        assert expected in result.output

    def test_not_in_a_plain_scalar_that_holds_a_quote(self):
        """`It's "..."` is one plain scalar; its quotes are characters, and
        filling the blank inside them unescaped could break the YAML."""
        front = 'governing_law: It\'s "{{placeholder:who}}"\n'
        result = assemble(_template("Hi.\n", front=front), {"who": "x"})
        assert '"{{placeholder:who' in result.output


# ── §15.7.3: escaping and line starts ────────────────────────────


class TestEscaping:
    @pytest.mark.parametrize("answer,inserted", [
        ("a *b* _c_ `d`", "a \\*b\\* \\_c\\_ \\`d\\`"),
        ("[link](x) <tag> |cell|", "\\[link\\](x) \\<tag> \\|cell\\|"),
        ("{#anchor} {when=x} \\", "\\{#anchor} \\{when=x} \\\\"),
        ("AT&T", "AT\\&T"),
        ("&#169; &amp;", "\\&#169; \\&amp;"),
        ("Smith & Co. s.r.o.", "Smith & Co. s.r.o."),
    ])
    def test_inserted_text_is_literal(self, answer, inserted):
        result = assemble(_template("To {{placeholder: who}} only.\n"), {"who": answer})
        assert _body(result) == f"\nTo {inserted} only.\n"

    def test_an_ampersand_is_escaped_against_the_template_text_after_it(self):
        result = assemble(_template("To {{placeholder: who}}T.\n"), {"who": "AT&"})
        assert _body(result) == "\nTo AT\\&T.\n"

    def test_a_choice_phrase_is_escaped_too(self):
        body = 'It is {{choose: x, true="*bold* [x]", false="no"}}.\n'
        assert _body(assemble(_template(body, _BOOL), {"x": True})) == "\nIt is \\*bold\\* \\[x\\].\n"


class TestLineStarts:
    @pytest.mark.parametrize("answer,line", [
        ("# Title", "\\# Title follows."),
        ("1. first", "1\\. first follows."),
        ("7) seventh", "7\\) seventh follows."),
        ("- item", "\\- item follows."),
        ("+ item", "\\+ item follows."),
        ("> quoted", "\\> quoted follows."),
        ("<div>", "\\<div> follows."),
    ])
    def test_an_insertion_that_would_begin_a_block(self, answer, line):
        result = assemble(_template("{{placeholder: x}} follows.\n"), {"x": answer})
        assert _body(result) == f"\n{line}\n"

    def test_a_thematic_break(self):
        result = assemble(_template("Intro.\n\n{{placeholder: x}}\n"), {"x": "---"})
        assert _body(result) == "\nIntro.\n\n\\---\n"

    def test_a_setext_underline_under_paragraph_text(self):
        result = assemble(_template("Intro\n{{placeholder: x}}\n"), {"x": "==="})
        assert _body(result) == "\nIntro\n\\===\n"

    def test_the_number_completed_by_template_text(self):
        result = assemble(_template("{{placeholder: year}}. The parties agree.\n"), {"year": "2026"})
        assert _body(result) == "\n2026\\. The parties agree.\n"

    def test_paragraph_continuation_is_read_in_context(self):
        """After paragraph text only an ordered list starting at 1 can begin
        (CommonMark), so "2." there is text and "1." is not."""
        template = _template("Intro\n{{placeholder: x}} more.\n")
        assert _body(assemble(template, {"x": "2. two"})) == "\nIntro\n2. two more.\n"
        assert _body(assemble(template, {"x": "1. one"})) == "\nIntro\n1\\. one more.\n"

    def test_inside_a_list_item_after_its_marker(self):
        result = assemble(_template("- {{placeholder: x}}\n- second\n"), {"x": "# Portal"})
        assert _body(result) == "\n- \\# Portal\n- second\n"

    def test_leading_space_after_an_empty_choice_goes(self):
        body = '{{choose: x, true="", false="No"}}   # not a heading.\n'
        assert _body(assemble(_template(body, _BOOL), {"x": True})) == "\n\\# not a heading.\n"

    def test_trailing_space_of_an_insertion_ending_the_line_goes(self):
        body = 'Net {{choose: x, true="value  ", false="sum"}}\nof VAT.\n'
        assert _body(assemble(_template(body, _BOOL), {"x": True})) == "\nNet value\nof VAT.\n"

    def test_a_line_emptied_by_a_choice_is_deleted_and_the_next_line_checked(self):
        body = 'Steps:\n\n{{choose: x, true="Then:", false=""}}\n2. Pay within thirty days.\n'
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == (
            "\nSteps:\n\n2\\. Pay within thirty days.\n"
        )

    def test_indented_code_is_prevented_by_removing_the_indentation(self):
        body = 'Intro.\n\n{{choose: x, true="Lead", false=""}}\n    indented continuation\n'
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == (
            "\nIntro.\n\nindented continuation\n"
        )


# ── Drafts: blanks left unanswered ───────────────────────────────


class TestDrafts:
    def test_an_unanswered_declared_blank_keeps_its_type_inline(self):
        questions = "  fee:\n    type: money\n  day:\n    type: date\n"
        template = _template(
            "Pay {{placeholder: fee, currency=EUR}} on {{placeholder: day}}.\n",
            questions,
            front='effective_date: "{{placeholder: day}}"\n',
        )
        result = assemble(template, {})
        assert 'effective_date: "{{placeholder: day, type=date}}"' in result.output
        assert _body(result) == (
            "\nPay {{placeholder: fee, type=money, currency=EUR}} on "
            "{{placeholder: day, type=date}}.\n"
        )
        assert "questions" not in result.output
        assert validate_document(parse_document(result.output)).errors == []

    def test_an_undeclared_or_already_typed_blank_is_left_as_written(self):
        template = _template("A {{placeholder: a}} and {{placeholder: b, type=date}}.\n")
        assert _body(assemble(template, {})) == "\nA {{placeholder: a}} and {{placeholder: b, type=date}}.\n"

    def test_a_document_without_template_constructs_goes_through_the_same_steps(self):
        draft = (
            "---\ntitle: Draft\n---\n\n\n> [!DRAFTING]\n> Remove me.\n\n"
            "Dear {{placeholder: who}},\n\n\n\nThanks.\n\n\n"
        )
        result = assemble(draft, {"who": "*Ann*"})
        assert result.output == "---\ntitle: Draft\n---\n\nDear \\*Ann\\*,\n\nThanks.\n"

    def test_an_output_with_nothing_left_is_empty(self):
        assert assemble("\n\n> [!DRAFTING]\n> Only guidance.\n\n", {}).output == ""


# ── Step 2–3, 6–8: structure ─────────────────────────────────────


class TestStructure:
    def test_drafting_notes_are_removed_wherever_they_are(self):
        body = (
            "> [!drafting]\n> Top-level note, lazy\ncontinuation.\n\n"
            "- first\n  > [!DRAFTING]\n  > note in an item\n- second\n\n"
            "> An ordinary quote stays.\n"
        )
        assert _body(assemble(_template(body), {})) == (
            "\n- first\n- second\n\n> An ordinary quote stays.\n"
        )

    def test_a_conditional_item_goes_with_its_nested_items(self):
        body = "# S\n\n- one {when=x}\n  - one-a\n- two\n  - two-a {when=!x}\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == "\n# S\n\n- two\n  - two-a\n"
        assert _body(assemble(_template(body, _BOOL), {"x": True})) == (
            "\n# S\n\n- one\n  - one-a\n- two\n"
        )

    def test_a_section_goes_with_its_subsections_up_to_a_same_level_heading(self):
        body = "# A {#a when=x}\n\nA text.\n\n## A.1\n\nMore.\n\n# B\n\nB text.\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == "\n# B\n\nB text.\n"

    @pytest.mark.parametrize("marker,kept", [
        ("{#a when=x}", "{#a}"),
        ("{when=x #a}", "{#a}"),
        ("{when=x}", ""),
    ])
    def test_markers_lose_their_condition(self, marker, kept):
        result = assemble(_template(f"# Heading {marker}\n\nText.\n", _BOOL), {"x": True})
        assert _body(result).startswith(f"\n# Heading{' ' + kept if kept else ''}\n")

    def test_a_condition_in_a_code_span_or_block_is_text(self):
        body = "Write `{when=x}` literally.\n\n```\n{when=x} {{placeholder: a}}\n```\n"
        assert _body(assemble(_template(body), {"a": "A"})) == "\n" + body

    def test_blank_lines_collapse_outside_code_only(self):
        body = (
            "One.\n\n\n\nTwo.\n  \n\t\n```\na\n\n\nb\n```\n\n\n"
            "Three.\n\n    code 1\n\n\n    code 2\n\n\n\nEnd.\n\n\n"
        )
        assert _body(assemble(_template(body), {})) == (
            "\nOne.\n\nTwo.\n\n```\na\n\n\nb\n```\n\n"
            "Three.\n\n    code 1\n\n\n    code 2\n\nEnd.\n"
        )

    def test_an_auto_identifier_that_would_change_is_written_in(self):
        """Step 7: removing the first "Services" would move "services" to the
        second, whose references point at "services-2"."""
        body = "# Services {when=x}\n\nExtended.\n\n# Services\n\nCore.\n"
        result = assemble(_template(body, _BOOL), {"x": False})
        assert _body(result) == "\n# Services {#services-2}\n\nCore.\n"

    def test_alternatives_share_their_identifier_and_need_nothing(self):
        questions = "  forum:\n    type: choice\n    choices:\n      courts: Courts\n      icc: ICC\n"
        body = "# Disputes {when=forum:courts}\n\nCourts.\n\n# Disputes {when=forum:icc}\n\nICC.\n"
        result = assemble(_template(body, questions), {"forum": "icc"})
        assert _body(result) == "\n# Disputes\n\nICC.\n"

    def test_the_questions_entry_is_deleted_with_its_comments(self):
        """An entry takes the comments inside it, but not the blank and
        comment lines at its end (§15.7.2)."""
        template = _template("Text.\n", "  # why x\n  x:\n    type: boolean\n\n# next\n")
        result = assemble(template, {"x": True})
        assert "questions" not in result.output and "# why x" not in result.output
        assert result.output.startswith(
            "---\ntitle: Fixture\ndocument_type: contract\n\n# next\nsides:\n"
        )

    ATTACHMENTS = """attachments:
  - id: one
    title: One
    file: one.pdf
    when: x
  - id: two
    title: Two
    file: two.pdf
    when: "!x"
  - id: three
    title: Three
    file: three.pdf
"""

    def test_an_absent_attachment_entry_and_the_remaining_when_are_deleted(self):
        template = _template("See {{attach: two}} and {{attach: three}}.\n", _BOOL, front=self.ATTACHMENTS)
        result = assemble(template, {"x": False})
        assert (
            "attachments:\n  - id: two\n    title: Two\n    file: two.pdf\n"
            "  - id: three\n    title: Three\n    file: three.pdf\n---\n"
        ) in result.output

    def test_attachments_is_deleted_when_no_attachment_remains(self):
        front = "attachments:\n  - id: one\n    title: One\n    file: one.pdf\n    when: x\nlanguage: en\n"
        result = assemble(_template("Text.\n", _BOOL, front=front), {"x": False})
        assert "attachments" not in result.output
        assert "sides:" in result.output and "language: en\n---\n" in result.output

    def test_crlf_and_a_byte_order_mark_are_kept(self):
        template = "\ufeff" + _template("Hi {{placeholder: who}}.\n\n\nBye.\n").replace("\n", "\r\n")
        result = assemble(template, {"who": "Ann"})
        assert result.output.startswith("\ufeff---\r\n")
        assert result.output.endswith("\r\n\r\nHi Ann.\r\n\r\nBye.\r\n")
        assert "\n" not in result.output.replace("\r\n", "")


class TestFiles:
    TEMPLATE = _template(
        "{{include: parts/a.lgd}}\n\n{{include: parts/b.lgd}} {when=x}\n",
        _BOOL + _TEXT,
        front="attachments:\n  - id: sch\n    title: Schedule\n    file: sch.lgd\n    when: x\n",
    )
    FILES = {
        "parts/a.lgd": "## A {#a}\n\nFor {{placeholder: name}}.\n",
        "parts/b.lgd": "## B {#b}\n\nB.\n",
        "sch.lgd": "## Scope\n\nScope text.\n",
    }

    def test_without_a_loader_a_multi_file_template_is_refused(self):
        """Reading other files is Full (§17.6): refused, never half done."""
        result = assemble(self.TEMPLATE, {"x": True})
        assert sorted(_rules(result)) == [
            ("attachment-file-missing", "error"),
            ("include-file-missing", "error"),
            ("include-file-missing", "error"),
        ]
        assert result.output == ""

    def test_only_present_files_are_output(self):
        result = assemble(self.TEMPLATE, {"x": False, "name": "Ann"}, load_file=self.FILES.get)
        assert result.files == {"parts/a.lgd": "## A {#a}\n\nFor Ann.\n"}
        assert "parts/b.lgd" not in result.output and "attachments" not in result.output

    def test_every_present_file_is_output(self):
        result = assemble(self.TEMPLATE, {"x": True, "name": "Ann"}, load_file=self.FILES.get)
        assert set(result.files) == {"parts/a.lgd", "parts/b.lgd", "sch.lgd"}
        assert "{{include: parts/b.lgd}}\n" in result.output

    def test_a_blank_in_an_absent_fragment_is_still_a_known_answer(self):
        result = assemble(self.TEMPLATE, {"x": False, "name": "Ann"}, load_file=self.FILES.get)
        assert result.diagnostics == []


# ── The questions a form asks ────────────────────────────────────


class TestQuestions:
    TEMPLATE = _template(
        "Pay {{placeholder: fee, currency=EUR}} for {{placeholder: term, type=duration, unit=MO}}"
        " to {{placeholder: payee}}.\n\n"
        "# Extras {when=extras}\n\n"
        'Delivered {{choose: speed, fast="fast", slow="slowly"}}.\n',
        "  extras:\n    type: boolean\n    prompt: Include extras?\n    default: false\n"
        "  speed:\n    type: choice\n    prompt: How fast?\n    choices:\n"
        "      fast: Fast\n      slow: Slow\n"
        "  fee:\n    type: money\n    prompt: Fee\n",
    )

    def test_every_question_with_its_effective_type(self):
        questions = {q.id: q for q in template_questions(self.TEMPLATE)}
        assert list(questions) == ["extras", "speed", "fee", "term", "payee"]
        assert questions["extras"].prompt == "Include extras?" and questions["extras"].default is False
        assert questions["speed"].choices == {"fast": "Fast", "slow": "Slow"}
        assert questions["speed"].is_decision and not questions["fee"].is_decision
        assert (questions["fee"].type, questions["fee"].currency) == ("money", "EUR")
        assert (questions["term"].type, questions["term"].unit, questions["term"].declared) == (
            "duration", "MO", False,
        )
        assert (questions["payee"].type, questions["payee"].declared) == ("text", False)

    def test_needed_questions_follow_the_decisions_made(self):
        """A decision is needed only where a condition or choice using it is
        reached; `extras` defaults to false, so `speed` is not asked until
        extras are chosen."""
        needed = [q.id for q in needed_questions(self.TEMPLATE, {})]
        assert needed == ["fee", "term", "payee", "extras"]
        needed = [q.id for q in needed_questions(self.TEMPLATE, {"extras": True})]
        assert needed == ["fee", "term", "payee", "extras", "speed"]

    def test_a_question_under_an_unanswered_condition_is_not_asked_yet(self):
        template = _template(
            '# Extra {when=x}\n\nIt is {{choose: y, true="on", false="off"}}.\n',
            _BOOL + "  y:\n    type: boolean\n",
        )
        assert [q.id for q in needed_questions(template, {})] == ["x"]
        assert [q.id for q in needed_questions(template, {"x": True})] == ["x", "y"]


# ── The parser's current block structure (legaldown-validator) ────


class TestCurrentBlockStructure:
    """Assembly reads block positions from the parser's own walk, so every
    block kind it knows is handled, and handled as the validator reads it."""

    @pytest.mark.parametrize("marker", ["-", "*", "+", "1.", "1)"])
    def test_a_condition_on_an_item_of_any_list_marker(self, marker):
        second = "2." if marker == "1." else "2)" if marker == "1)" else marker
        body = f"# A\n\n{marker} Kept.\n{second} Dropped. {{when=x}}\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == f"\n# A\n\n{marker} Kept.\n"

    def test_a_blank_in_a_table_cell_is_filled_and_escaped(self):
        body = "| Party | Note |\n|---|---|\n| {{placeholder: name}} | a \\| b |\n"
        result = assemble(_template(body, _TEXT), {"name": "A|B"})
        assert _body(result) == "\n| Party | Note |\n|---|---|\n| A\\|B | a \\| b |\n"

    def test_nothing_in_raw_html_is_filled(self):
        body = "<div>\n{{placeholder: name}}\n</div>\n\nHi {{placeholder: name}}.\n"
        result = assemble(_template(body, _TEXT), {"name": "Ann"})
        assert _body(result) == "\n<div>\n{{placeholder: name}}\n</div>\n\nHi Ann.\n"

    def test_blank_lines_in_indented_code_are_kept_and_in_raw_html_collapsed(self):
        """Step 8 keeps blank lines in fenced and indented code only."""
        body = "Text.\n\n    code\n\n\n    more\n\nEnd.\n\n<pre>\nA\n\n\nB\n</pre>\n"
        result = assemble(_template(body), {})
        assert _body(result) == "\nText.\n\n    code\n\n\n    more\n\nEnd.\n\n<pre>\nA\n\nB\n</pre>\n"

    def test_a_condition_after_a_signature_block_heading_is_decided(self):
        """The heading is an ordinary section (§2.2): nothing after it is
        left unassembled."""
        body = "# Terms\n\nA.\n\n# Signature Block {#signature-block}\n\nB. {when=x}\n\n# After\n\nC.\n"
        result = assemble(_template(body, _BOOL), {"x": False})
        assert _body(result) == "\n# Terms\n\nA.\n\n# Signature Block {#signature-block}\n\n# After\n\nC.\n"

    @pytest.mark.parametrize("character", ["\x0c", "\x0b", "\x1c", "\x85", "\u2028"])
    def test_only_lf_cr_and_crlf_end_a_line(self, character):
        """Other characters str.splitlines() breaks at are text (CommonMark):
        splitting at them moved every later unit onto the wrong lines."""
        body = f"# A\n\nText{character}# Not a heading {{when=x}}\n\n- one {character}- two\n\nAfter.\n"
        result = assemble(_template(body, _BOOL), {"x": False})
        assert _body(result) == f"\n# A\n\n- one {character}- two\n\nAfter.\n"

    def test_a_stray_cr_does_not_set_the_line_ending(self):
        template = "<!-- note\rmore -->\n\n# One {#one}\n\nHello {{placeholder: who}}.\n"
        result = assemble(template, {"who": "Bob"})
        assert result.output == "<!-- note\nmore -->\n\n# One {#one}\n\nHello Bob.\n"

    def test_a_cr_only_file_is_assembled_and_written_back_with_cr(self):
        template = _template("# A\n\nKept.\n\nDropped. {when=x}\n", _BOOL).replace("\n", "\r")
        result = assemble(template, {"x": False})
        assert result.ok and "\n" not in result.output
        assert result.output.endswith("\r# A\r\rKept.\r")

    def test_a_dashed_block_that_is_not_a_mapping_is_body(self):
        """The parser reads it as a rule and a setext heading, not
        frontmatter; assembly splits the file where the parser does."""
        template = "---\njust a string\n---\n\n# Heading\n\nFor {{placeholder: name}}.\n"
        result = assemble(template, {"name": "Ann"})
        assert result.output == "---\njust a string\n---\n\n# Heading\n\nFor Ann.\n"

    def test_a_fragment_opening_with_such_a_block_is_body_too(self):
        files = {"f.lgd": "---\njust a string\n---\n\nFor {{placeholder: name}}.\n"}
        result = assemble(_template("# A\n\n{{include: f.lgd}}\n"), {"name": "Ann"}, load_file=files.get)
        assert result.files == {"f.lgd": "---\njust a string\n---\n\nFor Ann.\n"}

    def test_an_item_numbered_after_1_keeps_its_marker_and_its_insertion_is_escaped(self):
        """An item numbered 2 continues its list, so its marker is a container
        marker even though it could not interrupt a paragraph."""
        result = assemble(_template("# A\n\n1. first item\n2. {{placeholder: name}}\n", _TEXT), {"name": "# T"})
        assert _body(result) == "\n# A\n\n1. first item\n2. \\# T\n"

    def test_an_item_numbered_2_left_first_stays_a_list_item(self):
        body = "# A\n\n1. first {when=x}\n2. {{placeholder: name}} second\n"
        result = assemble(_template(body, _BOOL + _TEXT), {"x": False, "name": "Tee"})
        assert _body(result) == "\n# A\n\n2. Tee second\n"

    def test_spacing_after_an_empty_choice_goes_in_an_item_numbered_2(self):
        body = '# A\n\n1. first\n2. {{choose: x, true="", false=""}}  rest\n'
        result = assemble(_template(body, _BOOL), {"x": True})
        assert _body(result) == "\n# A\n\n1. first\n2. rest\n"

    def test_a_nested_item_numbered_2(self):
        body = "# A\n\n- item one\n  1. nested first\n  2. {{placeholder: name}} nested\n"
        result = assemble(_template(body, _TEXT), {"name": "# T"})
        assert _body(result) == "\n# A\n\n- item one\n  1. nested first\n  2. \\# T nested\n"

    def test_a_conditional_last_item_goes_with_its_later_paragraphs(self):
        """An indented paragraph after a list is the last item's (CommonMark),
        though the parser keeps it a paragraph."""
        body = "# A\n\n- kept\n- dropped {when=x}\n\n    Its second paragraph.\n\n    Its third.\n\nAfter.\n"
        result = assemble(_template(body, _BOOL), {"x": False})
        assert _body(result) == "\n# A\n\n- kept\n\nAfter.\n"

    def test_a_later_paragraph_goes_with_the_item_it_is_indented_to(self):
        # Indented less than the deepest item's content, the paragraph is
        # its parent's (cmark-gfm): it stays when the deepest item goes.
        body = "# A\n\n- one\n  - nested a\n    - deep a\n    - deep b {when=x}\n\n    Nested a's.\n\nAfter.\n"
        result = assemble(_template(body, _BOOL), {"x": False})
        assert _body(result) == "\n# A\n\n- one\n  - nested a\n    - deep a\n\n    Nested a's.\n\nAfter.\n"

    @pytest.mark.parametrize("item", ["1234567890. a", " - a", "\x0c- a"])
    def test_any_item_the_parser_reads_is_assembled(self, item):
        result = assemble(_template(f"Text\n\n{item}\n"), {})
        assert _body(result) == f"\nText\n\n{item}\n"

    @pytest.mark.parametrize("after", [
        "  Its second paragraph.\n",
        "  Its second paragraph.\n\n    Its third.\n",
        "    - A nested item.\n",
        "    > A quote.\n",
        "   ~~~\n   code\n   ~~~\n",
    ])
    def test_a_conditional_last_item_goes_with_whatever_is_indented_to_it(self, after):
        body = f"# A\n\n- Kept.\n- Dropped. {{when=x}}\n\n{after}\nAfter.\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == "\n# A\n\n- Kept.\n\nAfter.\n"

    def test_a_nested_item_under_an_empty_item_takes_its_later_paragraph(self):
        body = "# A\n\n- \n  - b {when=x}\n\n    Its second paragraph.\n\nAfter.\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == "\n# A\n\n- \n\nAfter.\n"

    @pytest.mark.parametrize(("after", "kept"), [
        # What CommonMark reads as outside the item stays (cmark-gfm).
        ("  - b\n- c\n", "- c\n"),
        ("  1. b\n2. c\n", "2. c\n"),
        ("  <!--\nfoo\n  -->\n", "foo\n  -->\n"),
        ("  | a | b |\n  |---|---|\n| c | d |\n", "| c | d |\n"),
    ])
    def test_a_conditional_last_item_goes_only_as_far_as_commonmark_reads_it(self, after, kept):
        body = f"# A\n\n- a {{when=x}}\n\n{after}\nAfter.\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == f"\n# A\n\n{kept}\nAfter.\n"

    def test_an_item_indented_less_than_the_content_before_it_is_a_sibling(self):
        """` - b` is not inside `- a`, whose content starts at column 2, and
        neither is the paragraph after it (cmark-gfm)."""
        body = "# A\n\n- a {when=x}\n - b\n\n  para\n\nAfter.\n"
        assert _body(assemble(_template(body, _BOOL), {"x": False})) == "\n# A\n\n - b\n\n  para\n\nAfter.\n"

    def test_an_escaped_pipe_in_a_choice_in_a_table_cell_is_a_pipe(self):
        """The row's ``\\|`` is a pipe before the cell is read (GFM): the
        phrase is p|q, inserted escaped once."""
        choice = "  c:\n    type: choice\n    choices:\n      p: P\n      o: O\n"
        body = '# A\n\n| X |\n|---|\n| {{choose: c, p="p\\|q", o="other"}} |\n'
        result = assemble(_template(body, choice), {"c": "p"})
        assert _body(result) == "\n# A\n\n| X |\n|---|\n| p\\|q |\n"


class TestFencedCodeInContainers:
    """Fenced code inside a list item or a quote is literal (§11.4), as the
    validator reads the parser's item and quote text."""

    @pytest.mark.parametrize("body", [
        "- a\n  - b\n    ~~~\n    {{placeholder: name}}\n    ~~~\n",
        "> ~~~\n> {{placeholder: name}}\n> ~~~\n",
        "- ~~~\n  {{placeholder: name}}\n  ~~~\n",
    ])
    def test_nothing_in_it_is_filled(self, body):
        result = assemble(_template(f"# A\n\n{body}\nHi {{{{placeholder: name}}}}.\n", _TEXT), {"name": "Ann"})
        assert _body(result) == f"\n# A\n\n{body}\nHi Ann.\n"

    def test_nothing_in_it_is_malformed(self):
        body = "# A\n\n> [!DRAFTING]\n> Example:\n>\n> ~~~\n> {{placeholder: name\n> ~~~\n\nText.\n"
        assert _body(assemble(_template(body), {})) == "\n# A\n\nText.\n"

    @pytest.mark.parametrize("end", ["", "\n"])
    def test_a_one_line_item_opening_a_fence_is_code(self, end):
        """Its info string is code (§11.4), as the validator reads it (#52)."""
        body = f"# A\n\n- ~~~ {{{{placeholder: name}}}}\n{end}\nHi {{{{placeholder: name}}}}.\n"
        result = assemble(_template(body, _TEXT), {"name": "Ann"})
        # A blank line inside the open fence is code: step 8 keeps it.
        assert _body(result) == f"\n# A\n\n- ~~~ {{{{placeholder: name}}}}\n{end}\nHi Ann.\n"

    @pytest.mark.parametrize("line", ["- > ~~~", "> - ~~~", "> > ~~~"])
    def test_a_fence_behind_nested_markers_is_code(self, line):
        body = f"# A\n\n{line} {{{{placeholder: name}}}}\n\nHi {{{{placeholder: name}}}}.\n"
        result = assemble(_template(body, _TEXT), {"name": "Ann"})
        assert _body(result) == f"\n# A\n\n{line} {{{{placeholder: name}}}}\n\nHi Ann.\n"

    def test_a_continuation_line_is_not_a_fence(self):
        body = "# A\n\n- b\n      ~~~ text\n  more {{placeholder: name}}\n"
        result = assemble(_template(body, _TEXT), {"name": "Ann"})
        assert _body(result) == "\n# A\n\n- b\n      ~~~ text\n  more Ann\n"

    def test_an_include_in_it_is_not_one(self):
        fragment = "## B {#b}\n\n- a\n  - b\n    ~~~\n    {{include: other.lgd}}\n    ~~~\n"
        template = _template("# A\n\n{{include: f.lgd}}\n\nB. {when=x}\n", _BOOL)
        assert assemble(template, {"x": True}, load_file={"f.lgd": fragment}.get).ok


class TestItemsAreLexedApart:
    """Each list item is lexed on its own, as the validator lexes its text
    (#53): a code span or comment left open does not run into the next."""

    @pytest.mark.parametrize(("first", "second"), [
        ("- Use `x", "- For {{placeholder: name}}, see `y`."),
        ("- See <!-- n", "- For {{placeholder: name}} -->."),
        ("- > <!-- x", "- > For {{placeholder: name}}\n   -->"),
        ("1. Use `x", "   1. For {{placeholder: name}}, see `y`."),
    ])
    def test_an_open_code_span_or_comment_stays_in_its_item(self, first, second):
        body = f"# A\n\n{first}\n{second}\n"
        filled = second.replace("{{placeholder: name}}", "Ann")
        assert _body(assemble(_template(body, _TEXT), {"name": "Ann"})) == f"\n# A\n\n{first}\n{filled}\n"


class TestMalformed:
    def test_a_blank_written_across_lines_is_refused(self):
        """The parser joins the lines, so the validator reads it; it could not
        be filled, so assembly refuses rather than leave it."""
        result = assemble(_template("# A\n\nFor {{placeholder:\nname}} here.\n", _TEXT), {"name": "Ann"})
        assert _rules(result) == [("directive-malformed", "error")]
        assert result.output == ""

    def test_a_choice_written_across_lines_in_an_item_is_refused(self):
        body = '# A\n\n- For {{choose: x,\n  true="a", false="b"}}.\n'
        assert _rules(assemble(_template(body, _BOOL), {"x": True})) == [("directive-malformed", "error")]

    @pytest.mark.parametrize("front", [
        'note: "T" # use {{placeholder: name\n',
        '# was {{placeholder: name, a, b}}\n',
        'note: "{{placeholder: name, note=\\"a, b\\"}}"\n',
    ])
    def test_what_only_looks_malformed_in_frontmatter_is_not_refused(self, front):
        """YAML drops a comment, and decodes a scalar's escapes."""
        result = assemble(_template("# A\n\nFor {{placeholder: name}}.\n", _TEXT, front=front), {"name": "Ann"})
        assert result.ok and "For Ann." in result.output

    @pytest.mark.parametrize("quote", ['"', "'"])
    def test_a_blank_on_a_later_line_of_a_quoted_scalar_is_filled(self, quote):
        front = f"note: {quote}Long text\n  for {{{{placeholder: name}}}} here{quote}\n"
        result = assemble(_template("# A\n\nText.\n", _TEXT, front=front), {"name": "O'Brien \"Jr\""})
        written = "O''Brien \"Jr\"" if quote == "'" else "O'Brien \\\"Jr\\\""
        assert f"  for {written} here{quote}\n" in result.output

    @pytest.mark.parametrize("quote", ['"', "'"])
    def test_a_blank_across_later_lines_of_a_quoted_scalar_is_refused(self, quote):
        front = f"note: {quote}Long text\n  for {{{{placeholder:\n  name}}}}{quote}\n"
        result = assemble(_template("# A\n\nText.\n", _TEXT, front=front), {"name": "Ann"})
        assert _rules(result) == [("directive-malformed", "error")]

    def test_a_blank_across_frontmatter_lines_is_refused(self):
        front = 'note: "For {{placeholder:\n  name}}"\n'
        result = assemble(_template("# A\n\nText.\n", _TEXT, front=front), {"name": "Ann"})
        assert _rules(result) == [("directive-malformed", "error")]


class TestLoadedFiles:
    """The Full checks on a file assembly reads (§16.10–§16.12)."""

    @staticmethod
    def _assemble(fragment: str, *, questions: str = _BOOL, attachment: bool = False) -> AssemblyResult:
        if attachment:
            front = "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n"
            template = _template("# A\n\nSee {{attach: s}}.\n", questions, front=front)
            return assemble(template, {"x": True}, load_file={"s.lgd": fragment}.get)
        template = _template("# A\n\n{{include: f.lgd}}\n\nB. {when=x}\n" if questions else
                             "# A\n\n{{include: f.lgd}}\n", questions)
        return assemble(template, {"x": True}, load_file={"f.lgd": fragment}.get)

    @pytest.mark.parametrize("attachment", [False, True])
    def test_frontmatter_is_refused(self, attachment):
        result = self._assemble("---\ntitle: T\n---\n\n## B {#b}\n\nText.\n", attachment=attachment)
        rule = "attachment-has-frontmatter" if attachment else "include-has-frontmatter"
        assert _rules(result) == [(rule, "error")]

    @pytest.mark.parametrize("attachment", [False, True])
    def test_a_level_1_heading_is_refused(self, attachment):
        result = self._assemble("# B {#b}\n\nText.\n", attachment=attachment)
        rule = "attachment-has-h1" if attachment else "include-has-h1"
        assert _rules(result) == [(rule, "error")]

    @pytest.mark.parametrize("fragment", [
        "## B {#b when=x}\n\nText.\n",
        "## B {#b}\n\nText. {when=x}\n",
        "## B {#b}\n\n- Item. {when=x}\n",
        "## B {#b}\n\n> [!DRAFTING]\n> A note.\n",
        "## B\n\nText.\n",
        "## B {#b}\n\n{{include: g.lgd}}\n",
    ])
    def test_a_template_fragment_holds_none_of_these(self, fragment):
        """§15.3: no conditions, drafting notes or includes; an explicit
        identifier on every heading."""
        assert _rules(self._assemble(fragment)) == [("template-fragment-invalid", "error")]

    def test_a_template_attachment_file_may_hold_conditions_but_no_include(self):
        """Its conditions are its own, beneath its attachment's (§15.3)."""
        assert self._assemble("## B\n\nText. {when=x}\n", attachment=True).ok
        for text in ("{{include: g.lgd}}", "> [!DRAFTING]\n> {{include: g.lgd}}"):
            result = self._assemble(f"## B\n\n{text}\n", attachment=True)
            assert _rules(result) == [("template-fragment-invalid", "error")]

    def test_what_only_looks_like_a_condition_or_an_include_is_not_refused(self):
        """Marker text mid-paragraph is not a condition; an include in a
        drafting note is removed with it, unread."""
        assert self._assemble("## B {#b}\n\nThe text {when=x} goes on.\n").ok
        note = "## B\n\n> [!DRAFTING]\n> See {{include: g.lgd}}\n\nText.\n"
        assert self._assemble(note, questions="").ok

    def test_an_include_in_a_fragment_of_a_document_is_not_assembled(self):
        result = self._assemble("## B\n\n{{include: g.lgd}}\n", questions="")
        assert _rules(result) == [("include-file-missing", "error")]
        assert (result.output, result.files) == ("", {})


class TestFilesItCannotRead:
    def test_an_include_without_a_loader_is_refused(self):
        result = assemble(_template("{{include: parts/a.lgd}}\n"), {})
        assert _rules(result) == [("include-file-missing", "error")]
        assert (result.output, result.files) == ("", {})

    def test_a_translation_group_is_refused(self):
        """§17.6: a template whose linked templates are assembled with it is
        refused rather than assembled partially."""
        template = _template("Text.\n", front="language: en\ntranslations:\n  cs: smlouva.lgd\n")
        result = assemble(template, {})
        assert _rules(result) == [("translation-file-missing", "error")]
        assert result.output == ""
