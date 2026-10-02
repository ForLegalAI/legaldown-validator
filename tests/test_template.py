"""``Template`` and ``Form``: a template read once, and the interview over it —
which questions are reached, which are unanswered, which stop assembly, what
is wrong with the answers, whether assembly can run.

The assembled bytes are pinned by tests/test_assembly.py, which runs every one of
its tests through the functions and through ``Template`` and ``Form``; these tests
are about the form."""
from __future__ import annotations

import copy
import dataclasses
import pickle
import threading

import pytest

import legaldown
from legaldown import Form, FrontmatterError, Question, Template, load_template, parse_template
from legaldown.assembly import _Answers, _decide, _read

_FRONT = """---
title: T
questions:
  extras:
    type: boolean
    prompt: Include the extras?
  fee:
    type: money
    default:
      amount: "100"
      currency: EUR
  forum:
    type: choice
    choices:
      courts: State courts
      arbitration: ICC arbitration
---

"""

_BODY = """# Scope {#scope}

Hi {{placeholder: who}}. The fee is {{placeholder: fee}}.

# Extras {when=extras}

For {{placeholder: client}}.

{{choose: forum, courts="in the courts", arbitration="in arbitration"}}
"""

_TEMPLATE = _FRONT + _BODY


def _ids(questions) -> list[str]:
    return [question.id for question in questions]


# ── What is asked ─────────────────────────────────────────────────


def test_a_template_lists_its_questions_declared_first_then_by_placeholder():
    template = parse_template(_TEMPLATE)
    assert isinstance(template, Template) and isinstance(template.questions, tuple)
    assert _ids(template.questions) == ["extras", "fee", "forum", "who", "client"]
    assert [q.declared for q in template.questions] == [True, True, True, False, False]
    assert template.problems == ()


def test_the_form_grows_as_decisions_are_made():
    template = parse_template(_TEMPLATE)
    form = template.form({})
    assert isinstance(form, Form)
    assert _ids(form.questions) == ["who", "fee", "extras"]
    assert _ids(form.unanswered) == ["who", "extras"]  # fee has a default
    assert _ids(form.blocking) == ["extras"]  # only a decision stops assembly
    assert (form.ready, form.complete) == (False, False)
    more = template.form({"extras": True})
    assert _ids(more.questions) == ["who", "fee", "extras", "client", "forum"]
    assert _ids(more.blocking) == ["forum"]
    assert _ids(template.form({"extras": False}).questions) == ["who", "fee", "extras"]


def test_an_unanswered_value_does_not_stop_assembly_but_leaves_the_form_incomplete():
    form = parse_template(_TEMPLATE).form({"extras": False})
    assert _ids(form.unanswered) == ["who"] and form.blocking == ()
    assert (form.ready, form.complete) == (True, False)
    assert "{{placeholder: who}}" in form.assemble().output
    assert parse_template(_TEMPLATE).form({"extras": False, "who": "Ann"}).complete


def test_a_default_answers_a_question_but_is_still_asked():
    form = parse_template(_TEMPLATE).form({"extras": False, "who": "Ann"})
    assert "fee" in _ids(form.questions) and "fee" not in _ids(form.unanswered)
    (fee,) = (q for q in form.questions if q.id == "fee")
    assert dict(fee.default) == {"amount": "100", "currency": "EUR"}


def test_answers_to_questions_not_reached_are_unused_and_still_checked():
    template = parse_template(_TEMPLATE)
    form = template.form({"extras": False, "who": "Ann", "client": "Bo", "forum": "courts"})
    assert form.unused == ("forum", "client")
    assert form.ready  # valid answers to questions not reached do no harm
    bad = template.form({"extras": False, "who": "Ann", "forum": "nowhere"})
    assert bad.unused == ("forum",)
    assert [d.rule for d in bad.diagnostics] == ["answer-invalid"] and not bad.ready
    assert bad.problem("forum") is not None and bad.problem("who") is None


def test_an_unknown_answer_is_a_warning_and_does_not_stop_assembly():
    form = parse_template(_TEMPLATE).form({"extras": False, "who": "Ann", "nope": 1})
    assert [(d.rule, d.level) for d in form.diagnostics] == [("answer-unknown", "warning")]
    assert form.ready and form.assemble().ok


def test_an_invalid_decision_answer_counts_as_no_answer_and_is_reported_once():
    form = parse_template(_TEMPLATE).form({"extras": "yes", "who": "Ann"})
    assert _ids(form.unanswered) == ["extras"] and _ids(form.blocking) == ["extras"]
    assert [d.rule for d in form.diagnostics] == ["answer-invalid"]  # not also answer-missing
    assert "client" not in _ids(form.questions)  # a bad answer does not steer a branch


def test_an_invalid_value_does_not_fall_back_to_the_default():
    form = parse_template(_TEMPLATE).form({"extras": False, "who": "Ann", "fee": 5000})
    assert "fee" in _ids(form.unanswered)
    assert form.problem("fee") is not None and not form.ready


# ── Ready and complete ───────────────────────────────────────────

_INCLUDING = _FRONT + "# Scope {#scope}\n\n{{include: frag.lgd}}\n\nHi {{placeholder: who}}.\n\n# Extras {when=extras}\n\nText.\n"
_FRAGMENT = "## Part {#part}\n\nFor {{placeholder: client}}.\n"


def test_a_blank_inside_an_include_leaves_the_form_incomplete():
    template = parse_template(_INCLUDING, resolve={"frag.lgd": _FRAGMENT}.get)
    form = template.form({"extras": False, "who": "Ann"})
    assert form.ready and not form.complete and _ids(form.unanswered) == ["client"]
    assert template.form({"extras": False, "who": "Ann", "client": "Bo"}).complete


def test_an_error_unrelated_to_the_answers_does_not_stop_the_form():
    untitled = _TEMPLATE.replace("title: T\n", "")
    template = parse_template(untitled)
    assert any(d.rule == "title-missing" for d in template.validation.diagnostics)
    form = template.form({"extras": False, "who": "Ann"})
    assert form.ready and form.complete  # title-missing is no template rule


def test_the_questions_of_an_include_are_asked_where_the_include_is():
    template = parse_template(_INCLUDING, resolve={"frag.lgd": _FRAGMENT}.get)
    assert _ids(template.form({}).questions) == ["client", "who", "extras"]
    # the function reads the fragment after the body
    with pytest.warns(DeprecationWarning):
        assert _ids(legaldown.needed_questions(_INCLUDING, {}, load_file={"frag.lgd": _FRAGMENT}.get)) == [
            "who", "extras", "client",
        ]


@pytest.mark.parametrize("answers", [{}, {"extras": True}, {"extras": False, "who": "Ann"}, {"extras": "yes"}])
def test_reading_includes_in_place_decides_the_same_lines_and_files(answers):
    for text, files in [
        (_INCLUDING, {"frag.lgd": _FRAGMENT}),
        (_INCLUDING.replace("{{include: frag.lgd}}", "{{include: frag.lgd}} {when=extras}"), {"frag.lgd": _FRAGMENT}),
    ]:
        t = _read(text, files.get)
        after = _decide(t, _Answers(answers, t.declared))
        inline = _decide(t, _Answers(answers, t.declared), inline=True)
        assert (inline.removed, inline.files, inline.attachments) == (after.removed, after.files, after.attachments)
        assert set(inline.needed) == set(after.needed) and set(inline.missing) == set(after.missing)


# ── What stops a template ────────────────────────────────────────

_BROKEN = _FRONT + 'Choose {{choose: forum, courts="only one phrase"}}.\n'


def test_a_template_rule_error_stops_assembly_of_the_form_but_not_of_the_function():
    template = parse_template(_BROKEN)
    assert [d.rule for d in template.problems] == ["choose-invalid"]
    form = template.form({"forum": "courts"})
    assert not form.ready
    refused = form.assemble()
    assert not refused.ok and refused.output == "" and refused.diagnostics == list(template.problems)
    with pytest.warns(DeprecationWarning):
        assert legaldown.assemble(_BROKEN, {"forum": "courts"}).ok  # as before: assembly does not validate


def test_a_reference_to_an_included_section_is_no_problem_of_the_template():
    text = (
        "---\ntitle: T\ndocument_type: contract\nquestions:\n  name:\n    type: text\n---\n\n"
        "# Intro {#intro}\n\n{{include: frag.lgd}}\n\nSee {{ref: frag}}. The {{term: services}} are for {{placeholder: name}}.\n"
    )
    template = parse_template(text, resolve={"frag.lgd": '## Frag {#frag}\n\n"Services" {{def: services}} means consulting.\n'}.get)
    assert {d.rule for d in template.validation.diagnostics if d.level == "error"} >= {"ref-broken", "term-undefined"}
    assert template.problems == ()  # the validator reads the template alone, so these are only advice
    assert template.form({"name": "Ann"}).assemble().ok
    # the same text, in the template itself, is no error to the validator
    inline = text.replace("{{include: frag.lgd}}", '## Frag {#frag}\n\n"Services" {{def: services}} means consulting.')
    assert not [d for d in parse_template(inline).validation.diagnostics if d.level == "error"]


def test_a_file_that_cannot_be_read_is_a_problem():
    template = parse_template(_INCLUDING, resolve={}.get)
    assert [d.rule for d in template.problems] == ["include-file-missing"]
    assert [d.rule for d in parse_template(_INCLUDING).problems] == ["include-file-missing"]
    assert not template.form({"extras": False}).ready


def test_unreadable_frontmatter_is_raised_when_the_template_is_read():
    with pytest.raises(FrontmatterError):
        parse_template("---\ntitle: [unclosed\n---\n")


# ── A snapshot, repeatable ───────────────────────────────────────


def test_one_template_assembles_again_and_again_as_a_fresh_one_would():
    template = parse_template(_INCLUDING, resolve={"frag.lgd": _FRAGMENT}.get)
    sets = [
        {"extras": False, "who": "Ann", "client": "Bo"},
        {"extras": True, "who": "Cy", "client": "Di"},
        {"extras": False, "who": "Ann", "client": "Bo"},
    ]
    for answers in sets:
        fresh = parse_template(_INCLUDING, resolve={"frag.lgd": _FRAGMENT}.get).form(answers).assemble()
        again = template.form(answers).assemble()
        assert (again.output, again.files, again.diagnostics) == (fresh.output, fresh.files, fresh.diagnostics)
    assert template.form(sets[0]).assemble().output == template.form(sets[2]).assemble().output


def test_the_form_does_not_change_when_the_answers_do():
    answers = {"extras": False, "who": "Ann"}
    form = parse_template(_TEMPLATE).form(answers)
    before = (form.questions, form.unanswered, form.diagnostics, form.assemble().output)
    answers["extras"] = True
    answers["who"] = "Bo"
    assert (form.questions, form.unanswered, form.diagnostics, form.assemble().output) == before


def test_what_a_template_hands_out_cannot_change_it():
    template = parse_template(_BROKEN)
    refused = template.form({}).assemble()
    refused.diagnostics.append(None)
    assert None not in template.problems and len(template.problems) == 1
    (fee,) = (q for q in parse_template(_TEMPLATE).questions if q.id == "fee")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fee.prompt = "x"


def test_a_questions_default_and_choices_are_the_kinds_an_answer_and_a_declaration_are():
    template = parse_template(_TEMPLATE)
    by_id = {q.id: q for q in template.questions}
    fee, forum = by_id["fee"], by_id["forum"]
    assert fee.default == {"amount": "100", "currency": "EUR"} and isinstance(fee.default, dict)
    assert isinstance(forum.choices, dict) and list(forum.choices) == ["courts", "arbitration"]
    # the interview pattern: show the default, accept it
    assert fee.problem(fee.default) is None
    assert template.form({"extras": False, "who": "Ann", "fee": fee.default}).ready
    for question in template.questions:  # a question can be copied, pickled and sent
        assert copy.deepcopy(question) == question and pickle.loads(pickle.dumps(question)) == question
        assert dataclasses.asdict(question)["id"] == question.id
    assert Question("x", "text", choices=None).choices == {}


def test_a_question_does_not_share_its_default_with_the_template():
    template = parse_template(_TEMPLATE)
    (fee,) = (q for q in template.questions if q.id == "fee")
    fee.default["amount"] = "999"  # a caller that ignores "read-only" harms only the question it holds
    form = template.form({"extras": False, "who": "Ann"})
    assert "100" in form.assemble().output


def test_an_answer_that_cannot_be_copied_is_reported_and_does_not_crash_the_form():
    form = parse_template(_TEMPLATE).form({"extras": False, "who": threading.Lock()})
    assert [d.rule for d in form.diagnostics] == ["answer-invalid"] and not form.ready


def test_a_question_is_a_copy_of_the_declaration():
    declared = {"fee": {"type": "money", "default": {"amount": "100", "currency": "EUR"}}}
    question = Question("fee", "money", default=declared["fee"]["default"])
    declared["fee"]["default"]["amount"] = "999"
    assert question.default["amount"] == "100"


def test_a_template_reads_each_file_once():
    text = (
        "---\ntitle: T\nattachments:\n  - id: s\n    title: S\n    file: s.lgd\n---\n\n"
        "# Scope {#scope}\n\nSee {{attach: s}}.\n\n{{include: frag.lgd}}\n"
    )
    asked: list[str] = []
    files = {"s.lgd": '"Fee" {{def: fee}} means money.\n', "frag.lgd": _FRAGMENT}

    def resolve(path: str) -> str | None:
        asked.append(path)
        return files.get(path)

    template = parse_template(text, resolve=resolve)
    template.validation  # noqa: B018
    template.problems  # noqa: B018
    template.form({}).assemble()
    assert sorted(asked) == ["frag.lgd", "s.lgd"]


# ── Questions check answers ──────────────────────────────────────


def test_a_question_says_why_an_answer_is_not_valid():
    template = parse_template(_TEMPLATE)
    by_id = {q.id: q for q in template.questions}
    assert by_id["extras"].problem(True) is None and by_id["extras"].problem("yes") is not None
    assert by_id["forum"].problem("courts") is None and by_id["forum"].problem("nowhere") is not None
    assert by_id["who"].problem("Ann") is None and by_id["who"].problem(" Ann") is not None
    assert by_id["fee"].problem({"amount": "5", "currency": "EUR"}) is None
    assert by_id["fee"].problem("5") is not None  # no placeholder fixes a currency


def test_a_bare_amount_is_valid_where_every_placeholder_fixes_the_currency():
    text = _FRONT + "Fee {{placeholder: price, type=money, currency=EUR}} and {{placeholder: price, type=money, currency=EUR}}.\n"
    price = next(q for q in parse_template(text).questions if q.id == "price")
    assert price.currency == "EUR" and price.problem("5") is None
    assert price.problem({"amount": "5", "currency": "USD"}) is not None  # disagrees with the placeholders


def test_a_text_answer_may_not_hold_a_directive_where_it_fills_frontmatter():
    text = "---\ntitle: '{{placeholder: name}}'\n---\n\nText.\n"
    (name,) = parse_template(text).questions
    assert name.problem("{{x}}") is not None and name.problem("Acme") is None


def test_a_question_built_without_a_template_checks_what_it_can():
    assert Question("x", "boolean").problem(True) is None
    assert Question("x", "money").problem("5") is not None  # no placeholder fixes a currency
    assert Question("x", "money").problem({"amount": "5", "currency": "EUR"}) is None


# ── Reading from a file ──────────────────────────────────────────


def test_a_template_loaded_from_a_file_reads_what_it_includes_from_beside_it(tmp_path):
    (tmp_path / "frag.lgd").write_text(_FRAGMENT, encoding="utf-8")
    path = tmp_path / "t.lgd"
    path.write_text(_INCLUDING, encoding="utf-8")
    template = load_template(path)
    assert template.path == path and template.problems == ()
    result = template.form({"extras": False, "who": "Ann", "client": "Bo"}).assemble()
    assert result.ok and result.files["frag.lgd"].endswith("For Bo.\n")


def test_a_file_outside_the_templates_directory_is_not_read(tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "frag.lgd").write_text(_FRAGMENT, encoding="utf-8")
    (inner / "t.lgd").write_text(_INCLUDING.replace("frag.lgd", "../frag.lgd"), encoding="utf-8")
    assert [d.rule for d in load_template(inner / "t.lgd").problems] == ["include-file-missing"]


def test_loading_keeps_the_byte_order_mark_and_line_endings(tmp_path):
    path = tmp_path / "t.lgd"
    path.write_bytes(("﻿" + _TEMPLATE).replace("\n", "\r\n").encode("utf-8"))
    output = load_template(path).form({"extras": False, "who": "Ann"}).assemble().output
    assert output.startswith("﻿---\r\n") and "\r\n" in output and "\n" not in output.replace("\r\n", "")


def test_a_template_that_is_not_there_is_an_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_template(tmp_path / "nope.lgd")


# ── The functions are deprecated, and as they were ───────────────


def test_the_functions_say_they_are_deprecated_and_name_the_caller():
    for call in (
        lambda: legaldown.assemble(_TEMPLATE, {"extras": False}),
        lambda: legaldown.needed_questions(_TEMPLATE, {}),
        lambda: legaldown.template_questions(_TEMPLATE),
    ):
        with pytest.warns(DeprecationWarning, match=r"removed in 0\.5\.0") as record:
            call()
        assert record[0].filename == __file__


def test_the_functions_keep_their_behaviour_for_unreadable_frontmatter():
    bad = "---\ntitle: [unclosed\n---\n"
    with pytest.warns(DeprecationWarning):
        result = legaldown.assemble(bad, {})
        assert [d.rule for d in result.diagnostics] == ["frontmatter-invalid-yaml"]
        with pytest.raises(FrontmatterError):
            legaldown.needed_questions(bad, {})
        with pytest.raises(FrontmatterError):
            legaldown.template_questions(bad)


def test_the_new_names_are_public():
    for name in ("Template", "Form", "load_template", "parse_template"):
        assert name in legaldown.__all__ and hasattr(legaldown, name)
    assert copy.copy(parse_template(_TEMPLATE).questions)


# ── Which rules stop a template ──────────────────────────────────

_UNSAFE = (
    "---\ntitle: T\nquestions:\n  a:\n    type: boolean\n---\n\n"
    "# One {#one}\n\nSee {{ref: local}}.\n\n{{include: frag.lgd}} {when=!a}\n\n# Local {#local when=a}\n\nText.\n"
)


def test_a_reference_to_a_section_a_condition_removes_stops_a_template_read_alone_only():
    # alone, the validator sees every section: the reference is unsafe
    assert [d.rule for d in parse_template(_UNSAFE.replace("{{include: frag.lgd}} {when=!a}\n\n", "")).problems] == [
        "condition-reference-unsafe"
    ]
    # with a fragment it cannot tell the fragment's `local` from the removable one: advice, not a problem
    template = parse_template(_UNSAFE, resolve={"frag.lgd": "## Other {#local}\n\nText.\n"}.get)
    assert template.problems == ()
    assert any(d.rule == "condition-reference-unsafe" for d in template.validation.diagnostics)
    assert all(template.form({"a": value}).assemble().ok for value in (True, False))


@pytest.mark.parametrize(
    ("directive", "rule"),
    [
        ("{{placeholder: term, type=duration, unit=XYZ}}", "duration-invalid-unit"),
        ("{{placeholder: term, type=text, type=text}}", "directive-duplicate-param"),
    ],
)
def test_a_placeholder_that_would_fill_in_an_invalid_directive_stops_a_template(directive, rule):
    template = parse_template(_FRONT + f"Term {directive}.\n")
    assert rule in [d.rule for d in template.problems]
    assert not template.form({"term": "5"}).ready


def test_a_file_included_twice_is_read_as_its_last_include_says():
    text = _FRONT + "# A\n\n{{include: frag.lgd}}\n\nMore. {when=extras}\n\n{{include: frag.lgd}} {when=extras}\n"
    files = {"frag.lgd": "## Part {#part}\n\nFor {{placeholder: client}}.\n"}.get
    for answers in ({"extras": True}, {"extras": False}, {}):
        t = _read(text, files)
        after = _decide(t, _Answers(answers, t.declared))
        inline = _decide(t, _Answers(answers, t.declared), inline=True)
        assert (inline.removed, inline.files) == (after.removed, after.files)
        assert set(inline.needed) == set(after.needed)
