"""Validating a fragment on its own (#39): ``validate(fragment=True)`` and
``Diagnostic.host_dependent`` tell what the including document may settle (a
party, a side, an attachment or a question it declares, a section or a term it
defines) from what is wrong with the fragment itself (§12.2, §12.4), and the
internal modes (``_Mode``) that the unreadable-frontmatter case (#43) reuses."""
from __future__ import annotations

import dataclasses
import json

import pytest

from legaldown import Diagnostic, Document, load, parse, validate
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_OK, main
from legaldown.directives import lex
from legaldown.positions import Locator
from legaldown.validator import condition_problem
from legaldown.validator.conditions import condition_issue
from legaldown.validator.core import _DOCUMENT, _FRAGMENT, _UNREADABLE_FRONTMATTER, _Mode, _validate
from legaldown.validator.result import Depends
from legaldown.validator.templates import _choose_issues, choose_problem

_QUESTIONS = {
    "vat": {"type": "boolean"},
    "forum": {"type": "choice", "choices": {"courts": "State courts", "arbitration": "ICC"}},
    "note": {"type": "text"},
}


def _rows(result):
    return [(d.rule, d.level, d.line) for d in result.diagnostics]


def _flagged(result):
    return {(d.rule, d.line) for d in result.diagnostics if d.host_dependent}


def _fragment(body: str, **kwargs):
    return validate(parse(body), fragment=True, **kwargs)


def _without_absent(result):
    return [row for row in _rows(result) if row[0] != "frontmatter-absent"]


# ── A body-only fragment ──────────────────────────────────────────

def test_a_body_only_fragment_draws_neither_frontmatter_absent_nor_metadata_rules():
    body = "## Scope\n\nText.\n"
    assert "frontmatter-absent" in validate(parse(body)).rules()
    assert _rows(_fragment(body)) == []


def test_a_fragment_may_start_at_any_level_but_not_skip_after_it():
    result = _fragment("### Deep\n\n#### Deeper\n\n## Back\n\n#### Skips\n")
    assert [(d.rule, d.line) for d in result.diagnostics] == [("heading-skip", 7)]


# ── What the host may settle is flagged, and nothing else changes ──

HOST_DEPENDENT = [
    ("See {{ref: elsewhere}}.", "ref-broken"),
    ("See {{term: elsewhere}}.", "term-undefined"),
    ("{{party: acme}} pays.", "party-unknown"),
    ("The {{side: clients}} pay.", "side-unknown"),
    ("See {{attach: annex}}.", "attach-undeclared"),
    ("## Clause {when=vat}\n\nText.", "condition-invalid"),
    ("## Clause {when=forum:courts}\n\nText.", "condition-invalid"),
    ('{{choose: vat, true="a", false="b"}}', "choose-invalid"),
]


@pytest.mark.parametrize(("body", "rule"), HOST_DEPENDENT)
def test_what_the_host_may_declare_or_define_is_flagged(body, rule):
    source = f"## Scope\n\n{body}\n" if not body.startswith("##") else f"{body}\n"
    fragment = _fragment(source)
    assert any(d.rule == rule and d.host_dependent for d in fragment.diagnostics), fragment.diagnostics
    assert all(d.host_dependent for d in fragment.diagnostics if d.rule == rule)
    # Without fragment it is the same finding at the same line, not flagged.
    complete = validate(parse(source))
    assert not any(d.host_dependent for d in complete.diagnostics)
    assert _without_absent(complete) == _rows(fragment)


def test_the_flag_is_only_for_fragment_mode_even_where_the_reason_is_known():
    # A reference broken in a complete document is simply an Error: no host.
    complete = validate(parse("---\ntitle: T\n---\n\n# A\n\nSee {{ref: nope}}.\n"))
    assert "ref-broken" in complete.rules("error")
    assert not any(d.host_dependent for d in complete.diagnostics)


INTRINSIC = [
    ("A {{ref: Bad Id}}", "ref-broken"),
    ("A {{ref:}}", "ref-broken"),
    ("A {{term: Bad Id}}", "term-undefined"),
    ("A {{term:}}", "term-undefined"),
    ("A {{party: Bad Id}}", "party-name-malformed"),
    ("A {{side: Bad Id}}", "side-name-malformed"),
    ("## Clause {when=BAD}\n\nText.", "condition-invalid"),
    ("## Clause {{choose: q, a=\"x\"}}\n\nText.", "choose-invalid"),
    ("A {{date: yesterday}}", "date-invalid"),
    ("A {{money: abc, currency=EUR}}", "money-invalid-amount"),
    # No host can settle it: a question declared under the id is invalid in its turn.
    ("## Clause {when=vat}\n\n{{placeholder: yes}}", "question-invalid"),
]


@pytest.mark.parametrize(("body", "rule"), INTRINSIC)
def test_what_is_wrong_whatever_the_host_declares_is_not_flagged(body, rule):
    source = f"## Scope\n\n{body}\n" if not body.startswith("##") else f"{body}\n"
    fragment = _fragment(source)
    assert any(d.rule == rule for d in fragment.diagnostics), fragment.diagnostics
    assert not any(d.host_dependent for d in fragment.diagnostics if d.rule == rule)


def test_a_reference_to_a_section_or_anchor_of_the_fragment_is_not_reported():
    result = _fragment(
        "## Scope {#scope}\n\nSee {{ref: scope}} and {{ref: item}}.\n\n- Item. {#item}\n"
    )
    assert _rows(result) == []


def test_a_reference_to_a_definition_of_the_fragment_is_not_reported():
    result = _fragment('## Scope\n\n"Fee" {{def: fee}} is due: {{term: fee}}.\n')
    assert _rows(result) == []


def test_a_fragment_declares_nothing_for_its_blanks_and_questions_to_be_checked_against():
    # The declared-question checks cannot be made without the host's questions,
    # so they are silent; only a choose on an undeclared question is reported.
    result = _fragment(
        '## Scope\n\nPay {{placeholder: fee, type=money}} {{choose: q, a="x", b="y"}}.\n'
    )
    assert [(d.rule, d.host_dependent) for d in result.diagnostics] == [("choose-invalid", True)]
    assert "question-unused" not in result.rules()
    assert "placeholder-question-mismatch" not in result.rules()


def test_a_definition_the_fragment_leaves_unreferenced_is_host_dependent():
    # The host may reference it (§7.2, §12.2).
    body = '## Scope\n\n"Fee" {{def: fee}} is due.\n'
    assert [(d.rule, d.host_dependent) for d in validate(parse(body)).diagnostics
            if d.rule == "def-unreferenced"] == [("def-unreferenced", False)]
    assert [(d.rule, d.level, d.host_dependent) for d in _fragment(body).diagnostics] == [
        ("def-unreferenced", "warning", True)
    ]


def test_a_fragment_with_a_blank_is_read_as_a_template():
    # Whether it is one is the host's to decide, so the template checks are made.
    body = "## Scope\n\nPay {{placeholder: no}}.\n"
    assert _rows(validate(parse(body))) == [("frontmatter-absent", "warning", 1)]
    fragment = _fragment(body)
    assert [(d.rule, d.host_dependent) for d in fragment.diagnostics] == [("question-invalid", False)]
    assert fragment.index.is_template
    # A blank in a heading, and a drafting note, are enough too.
    assert _fragment("## Pay {{placeholder: ok}}\n").index.is_template
    assert _fragment("## Scope\n\n> [!DRAFTING]\n> Note.\n\nPay {{placeholder: ok}}.\n").index.is_template
    assert _fragment("## Scope\n\n> [!DRAFTING]\n> Note.\n").index.is_template
    assert not _fragment("## Scope\n\nText.\n").index.is_template


# ── A fragment that has frontmatter, or a level-1 heading ──────────

_FRONTMATTER = "---\ntitle: Part\ndocument_type: contract\nquestions:\n  vat:\n    type: boolean\n---\n\n"


def test_frontmatter_in_a_fragment_is_reported_at_its_first_key_and_not_read():
    result = _fragment(_FRONTMATTER + "## Scope\n\nSee {{ref: nope}} {{choose: vat, true=\"a\", false=\"b\"}}.\n")
    assert [(d.rule, d.line, d.host_dependent) for d in result.diagnostics] == [
        ("include-has-frontmatter", 2, False),
        # The declared question is the fragment's own metadata, which is not read.
        ("choose-invalid", 11, True),
        ("ref-broken", 11, True),
    ]
    assert result.diagnostics[0].level == "error"


def test_the_metadata_of_a_fragment_is_not_checked_whatever_it_holds():
    result = _fragment("---\ndocument_type: nope\nsides: [{name: A}]\n---\n\n## Scope\n")
    assert [d.rule for d in result.diagnostics] == ["include-has-frontmatter"]
    assert result.diagnostics[0].line == 2


def test_an_empty_frontmatter_block_is_reported_at_its_opening_line():
    result = _fragment("---\n---\n\n## Scope\n")
    assert [(d.rule, d.line) for d in result.diagnostics] == [("include-has-frontmatter", 1)]


def test_body_lines_are_the_file_lines_with_frontmatter_swapped_out():
    body = "## Scope\n\nSee {{ref: nope}}.\n"
    bare = [d for d in _fragment(body).diagnostics]
    framed = [d for d in _fragment(_FRONTMATTER + body).diagnostics if d.rule == "ref-broken"]
    offset = len(_FRONTMATTER.splitlines())
    assert [d.line + offset for d in bare] == [d.line for d in framed]


def test_a_level_one_heading_is_include_has_h1_at_each_heading():
    result = _fragment("# One\n\ntext\n\n## Two\n\n# Three\n")
    assert [(d.rule, d.level, d.line, d.host_dependent) for d in result.diagnostics] == [
        ("include-has-h1", "error", 1, False),
        ("include-has-h1", "error", 7, False),
    ]


def test_a_document_built_in_code_has_frontmatter_only_if_it_holds_metadata():
    assert validate(Document(), fragment=True).diagnostics == []
    built = Document.from_dict({"metadata": {"title": "T"}})
    assert [d.rule for d in validate(built, fragment=True).diagnostics] == ["include-has-frontmatter"]
    assert validate(built, fragment=True).diagnostics[0].line is None


def test_first_key_of_the_locator():
    assert Locator(parse(_FRONTMATTER + "## A\n")).first_key() == 2
    assert Locator(parse("---\n---\n\n## A\n")).first_key() == 1
    assert Locator(parse("## A\n")).first_key() is None
    assert Locator(Document()).first_key() is None


def test_a_fragment_is_named_in_its_diagnostics():
    result = validate(parse("## A\n\n{{ref: nope}}\n", filename="part.lgd"), fragment=True)
    assert [(d.file, d.host_dependent) for d in result.diagnostics] == [("part.lgd", True)]


# ── condition_problem and choose_problem are unchanged ─────────────

CONDITIONS = [
    ("Q", _QUESTIONS, "must be '[!]question' or '[!]question:value', in the identifier format"),
    ("vat:x", _QUESTIONS, "'vat' is a boolean question: write 'vat' or '!vat'"),
    ("forum", _QUESTIONS, "'forum' is a choice question: write 'forum:value'"),
    ("forum:x", _QUESTIONS, "'x' is not a declared choice of 'forum'"),
    ("note", _QUESTIONS, "names 'note', which is not a declared boolean or choice question"),
    ("nope", _QUESTIONS, "names 'nope', which is not a declared boolean or choice question"),
    ("nope", None, "names 'nope', which is not a declared boolean or choice question"),
    ("vat", _QUESTIONS, None),
    ("!forum:courts", _QUESTIONS, None),
]


@pytest.mark.parametrize(("text", "questions", "message"), CONDITIONS)
def test_condition_problem_is_the_message_of_condition_issue(text, questions, message):
    assert condition_problem(text, questions) == message
    issue = condition_issue(text, questions)
    assert (issue[1] if issue else None) == message


@pytest.mark.parametrize(
    ("text", "questions", "depends"),
    [
        ("Q", _QUESTIONS, None),  # malformed
        ("vat:x", _QUESTIONS, None),  # wrong form of a declared question
        ("forum", _QUESTIONS, None),
        ("forum:x", _QUESTIONS, None),
        ("note", _QUESTIONS, None),  # declared, but not a decision question
        ("nope", _QUESTIONS, Depends.FRONTMATTER),  # not declared here
        ("nope", None, Depends.FRONTMATTER),
    ],
)
def test_a_condition_on_a_question_that_is_not_declared_is_frontmatter_dependent(text, questions, depends):
    assert condition_issue(text, questions)[0] is depends


def _choose(source: str):
    return next(d for d in lex(source).directives if d.name == "choose")


@pytest.mark.parametrize(
    ("source", "message", "depends"),
    [
        ('{{choose: nope, a="x"}}', "'{{choose: nope, a=\"x\"}}' must name a declared boolean or choice question (§15.5).",
         Depends.FRONTMATTER),
        ('{{choose: note, a="x"}}', "'{{choose: note, a=\"x\"}}' must name a declared boolean or choice question (§15.5).",
         None),
        ('{{choose: vat, true="x"}}', "'{{choose: vat, true=\"x\"}}' lists no phrase for false: every answer to 'vat' needs one (§15.5).",
         None),
        ('{{choose: vat, true="x", false="y", maybe="z"}}',
         "'{{choose: vat, true=\"x\", false=\"y\", maybe=\"z\"}}' lists maybe, which is not an answer to 'vat' (§15.5).", None),
    ],
)
def test_choose_problem_is_the_first_message_of_choose_issues(source, message, depends):
    directive = _choose(source)
    assert choose_problem(directive, _QUESTIONS) == message
    assert _choose_issues(directive, _QUESTIONS)[0] == (depends, message)


# ── The reasons: suppressing one (what #43 does) ───────────────────

_FULL = """---
title: Fixture
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
  - id: annex
    title: Annex
    file: annex.pdf
questions:
  vat:
    type: boolean
---

# Scope {#scope}

{{party: nobody}} {{side: nobody}} {{attach: nothing}} {{ref: annex}} {{ref: nope}} {{term: nope}}
{{choose: ghost, a="x"}} {{choose: vat, true="a", false="b"}}

## Clause {when=ghost}

Text.
"""


def test_every_reason_is_computed_in_a_complete_document_and_none_is_flagged():
    result = validate(parse(_FULL))
    assert {d.rule for d in result.diagnostics} >= {
        "party-unknown", "side-unknown", "attach-undeclared", "ref-targets-attachment",
        "ref-broken", "term-undefined", "choose-invalid", "condition-invalid",
    }
    assert not any(d.host_dependent for d in result.diagnostics)


def test_suppressing_frontmatter_drops_what_the_frontmatter_may_declare_and_keeps_the_body_rules():
    document = parse(_FULL)
    mode = _Mode(suppress=frozenset({Depends.FRONTMATTER}))
    rules = {d.rule for d in _validate(document, False, None, None, None, mode).diagnostics}
    assert rules.isdisjoint({
        "party-unknown", "side-unknown", "attach-undeclared", "ref-targets-attachment",
        "choose-invalid", "condition-invalid",
    })
    assert {"ref-broken", "term-undefined"} <= rules


def test_suppressing_body_drops_references_alone():
    document = parse(_FULL)
    mode = _Mode(suppress=frozenset({Depends.BODY}))
    rules = {d.rule for d in _validate(document, False, None, None, None, mode).diagnostics}
    assert rules.isdisjoint({"ref-broken", "term-undefined"})
    assert {"party-unknown", "ref-targets-attachment", "condition-invalid"} <= rules


def test_the_presets():
    assert _Mode() == _DOCUMENT
    assert _Mode(
        empty_metadata=True, suppress=frozenset(), report_frontmatter_absent=False,
        check_first_heading=False, fragment=True,
    ) == _FRAGMENT
    assert _Mode(
        empty_metadata=True, suppress=frozenset({Depends.FRONTMATTER}), report_frontmatter_absent=False,
        check_first_heading=True, fragment=False,
    ) == _UNREADABLE_FRONTMATTER
    with pytest.raises(dataclasses.FrozenInstanceError):
        _DOCUMENT.fragment = True  # type: ignore[misc]


def test_the_unreadable_frontmatter_preset_validates_the_body_as_the_body_of_a_main_document():
    body = "## Scope\n\n{{party: x}} {{side: x}} {{attach: x}} {{choose: q, a=\"x\"}} {{ref: nope}} {{term: nope}}\n"
    for source in (body, _FULL.split("# Scope")[0] + body):
        result = _validate(parse(source), False, None, None, None, _UNREADABLE_FRONTMATTER)
        rules = [d.rule for d in result.diagnostics]
        assert "frontmatter-absent" not in rules
        assert "title-missing" not in rules and "sides-absent" not in rules
        # The first heading is required at level 1 although the block is not read.
        assert rules.count("heading-skip") == 1
        assert {"ref-broken", "term-undefined"} <= set(rules)
        assert set(rules).isdisjoint({"party-unknown", "side-unknown", "attach-undeclared", "choose-invalid"})
        assert not any(d.host_dependent for d in result.diagnostics)


def test_the_unreadable_frontmatter_preset_accepts_a_first_heading_at_level_one():
    result = _validate(parse("# Scope\n\n## Sub\n"), False, None, None, None, _UNREADABLE_FRONTMATTER)
    assert result.diagnostics == []


# ── The public record ──────────────────────────────────────────────

def test_a_diagnostic_is_not_flagged_by_default_and_the_flag_takes_part_in_equality():
    plain = Diagnostic("ref-broken", "error", "m", 3, "f")  # the positional form before the flag
    assert plain.host_dependent is False
    flagged = dataclasses.replace(plain, host_dependent=True)
    assert flagged != plain and hash(flagged) != hash(plain)
    assert dataclasses.replace(flagged, file="g").host_dependent is True


# ── The command line ───────────────────────────────────────────────

@pytest.fixture
def write(tmp_path):
    def _write(name: str, source: str):
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path
    return _write


def test_the_cli_validates_a_fragment_with_the_flag(write, capsys):
    path = write("part.lgd", "## Scope\n\nSee {{ref: elsewhere}} and {{party: acme}}.\n")
    assert main(["validate", str(path)]) == EXIT_DIAGNOSTICS
    assert "frontmatter-absent" in capsys.readouterr().out
    # Only the host can settle what is left: the exit status is clean.
    assert main(["validate", "--fragment", "--format", "json", str(path)]) == EXIT_OK
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["rule"], d["line"], d["host_dependent"]) for d in records] == [
        ("party-unknown", 3, True),
        ("ref-broken", 3, True),
    ]


def test_the_json_record_always_has_the_key(write, capsys):
    path = write("doc.lgd", "---\ntitle: T\n---\n\n# A\n\nSee {{ref: nope}}.\n")
    main(["validate", "--format", "json", str(path)])
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert records and all(d["host_dependent"] is False for d in records)
    bad = write("bad.lgd", "---\n*x*\n---\n\n# A\n")
    main(["validate", "--format", "json", str(bad)])
    (record,) = json.loads(capsys.readouterr().out)["diagnostics"]
    assert (record["rule"], record["host_dependent"]) == ("frontmatter-invalid-yaml", False)


def test_the_cli_output_is_sorted_as_before_with_the_flag(write, capsys):
    path = write("part.lgd", "## A\n\n{{ref: b}}\n\n{{party: c}}\n\n{{ref: Bad Id}}\n")
    main(["validate", "--fragment", "--format", "json", str(path)])
    lines = [d["line"] for d in json.loads(capsys.readouterr().out)["diagnostics"]]
    assert lines == sorted(lines)


def test_the_cli_text_output_marks_a_host_dependent_finding_and_only_that(write, capsys):
    path = write("part.lgd", "## A\n\n{{ref: b}} {{ref: Bad Id}}\n")
    main(["validate", "--fragment", str(path)])
    assert capsys.readouterr().out == (
        f"{path}:3: error: [ref-broken] Broken section reference: 'b'. (host-dependent)\n"
        f"{path}:3: error: [ref-broken] Broken section reference: 'Bad Id'.\n"
    )
    main(["validate", str(path)])  # not a fragment: nothing is marked
    assert "host-dependent" not in capsys.readouterr().out


def test_the_cli_exits_zero_for_a_fragment_with_only_host_dependent_findings(write, capsys):
    path = write("part.lgd", "## A\n\n{{ref: b}} {{party: acme}} {{term: fee}}\n")
    assert main(["validate", "--fragment", str(path)]) == EXIT_OK
    assert capsys.readouterr().out.count("(host-dependent)") == 3
    assert main(["validate", "--fragment", "--strict", str(path)]) == EXIT_OK
    assert main(["validate", str(path)]) == EXIT_DIAGNOSTICS  # as a complete document


def test_the_cli_exits_one_for_a_fragment_with_an_intrinsic_finding(write, capsys):
    path = write("part.lgd", "## A\n\n{{ref: b}} {{ref: Bad Id}}\n")
    assert main(["validate", "--fragment", str(path)]) == EXIT_DIAGNOSTICS
    skipped = write("warn.lgd", "## A\n\nPay {{money: 100}} {{ref: b}}\n")
    assert main(["validate", "--fragment", str(skipped)]) == EXIT_OK
    assert main(["validate", "--fragment", "--strict", str(skipped)]) == EXIT_DIAGNOSTICS
    capsys.readouterr()


def test_the_cli_reports_frontmatter_of_a_fragment_at_the_first_key_and_keeps_body_lines(write, capsys):
    source = _FRONTMATTER + "## Scope\n\nSee {{ref: nope}}.\n"
    path = write("part.lgd", source)
    assert main(["validate", "--fragment", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["rule"], d["line"], d["host_dependent"]) for d in records] == [
        ("include-has-frontmatter", 2, False),
        ("ref-broken", 11, True),
    ]
    assert source.splitlines()[10] == "See {{ref: nope}}."


def test_the_cli_fragment_of_a_clean_file_exits_zero(write, capsys):
    path = write("part.lgd", "## Scope\n\nText.\n")
    assert main(["validate", "--fragment", str(path)]) == EXIT_OK
    assert load(path).metadata.frontmatter_absent
