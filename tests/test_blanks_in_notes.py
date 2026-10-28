"""A placeholder inside a drafting note (#112). A note is removed before anything is
filled (§15.7.2 step 2), so an occurrence in one is no part of the blank: it takes no part in the
blank's type, currency or unit, and a question only notes use needs no answer (§15.7.2 step 1). It
is still recognized and validated by itself (§15.6), and under the final option it is still a
placeholder that remains (§15.9)."""
from __future__ import annotations

import pytest

from legaldown import Template, parse, parse_template, validate

_NOTE = "> [!DRAFTING]\n> {}\n\n"
_RULES = {
    "placeholder-type-inconsistent", "question-unused", "question-invalid", "placeholder-id-malformed",
    "placeholder-type-invalid", "placeholder-unknown-currency", "duration-invalid-unit", "choose-invalid",
    "placeholder-question-mismatch", "placeholder-unfilled", "template-construct-present", "condition-invalid",
    "directive-malformed",
}


def _document(body: str, front: str = "") -> str:
    return f"---\ntitle: Fixture\n{front}---\n\n# Scope {{#scope}}\n\n{body}"


def _result(body: str, front: str = "", **kwargs):
    return validate(parse(_document(body, front), filename="f.lgd"), **kwargs)


def _found(result) -> list[tuple[str, int | None]]:
    return [(d.rule, d.line) for d in result.diagnostics if d.rule in _RULES]


def _note(text: str) -> str:
    return _NOTE.format(text)


# ── The type, currency and unit of a blank are the body's ───────────────

@pytest.mark.parametrize("first", ["note", "body"])
def test_a_note_with_another_type_than_the_body_is_clean_in_either_order(first):
    note = _note("Fee {{placeholder: fee, type=money, currency=USD}}.")
    body = "Pay {{placeholder: fee}}.\n\n"
    result = _result(note + body if first == "note" else body + note)
    assert _found(result) == []
    blank = result.index.blanks["fee"]
    assert (blank.type, blank.fixed, blank.consistent) == ("text", "", True)


@pytest.mark.parametrize("first", ["note", "body"])
def test_a_currency_fixed_in_a_note_is_not_the_blanks(first):
    note = _note("Fee {{placeholder: fee, type=money, currency=USD}}.")
    body = "Pay {{placeholder: fee, type=money, currency=EUR}}.\n\n"
    result = _result(note + body if first == "note" else body + note)
    assert _found(result) == []
    blank = result.index.blanks["fee"]
    assert (blank.type, blank.fixed, blank.consistent) == ("money", "EUR", True)


def test_two_notes_may_fix_different_currencies():
    notes = _note("{{placeholder: fee, type=money, currency=USD}}") + _note("{{placeholder: fee, type=money, currency=GBP}}")
    assert _found(_result(notes + "Pay {{placeholder: fee, type=money}}.\n")) == []


def test_a_conflict_between_two_body_occurrences_is_still_an_error():
    body = "{{placeholder: fee, type=money, currency=EUR}} {{placeholder: fee, type=money, currency=USD}}\n"
    assert {rule for rule, _ in _found(_result(_note("{{placeholder: fee}}") + body))} == {"placeholder-type-inconsistent"}
    assert {rule for rule, _ in _found(_result("{{placeholder: fee}} {{placeholder: fee, type=date}}\n"))} == {
        "placeholder-type-inconsistent"
    }


def test_a_blank_only_in_a_note_is_none_and_draws_nothing():
    result = _result(_note("Fee {{placeholder: fee, type=money, currency=USD}}."))
    assert _found(result) == [] and "fee" not in result.index.blanks


def test_a_blank_in_a_note_and_the_body_takes_its_facts_from_the_body():
    result = _result(
        _note("{{placeholder: fee, type=money, currency=USD}}") + "Pay {{placeholder: fee, type=money}}.\n",
    )
    blank = result.index.blanks["fee"]
    assert (blank.type, blank.fixed, blank.consistent) == ("money", "", True)


def test_a_default_is_not_held_to_a_currency_only_a_note_fixes():
    front = "questions:\n  fee:\n    type: money\n    default:\n      amount: '100.00'\n      currency: EUR\n"
    note = _note("{{placeholder: fee, currency=USD}}")
    assert _found(_result(note + "Pay {{placeholder: fee}}.\n", front)) == []
    # ... and still held to the body's.
    assert [r for r, _ in _found(_result("Pay {{placeholder: fee, currency=USD}}.\n", front))] == ["question-invalid"]


# ── A question only a note uses is unused ───────────────────────────────

def test_a_question_only_a_note_placeholder_uses_is_unused():
    front = "questions:\n  fee:\n    type: money\n"
    result = _result(_note("{{placeholder: fee}}"), front)
    assert _found(result) == [("question-unused", 4)]
    assert "only drafting notes do" in next(d.message for d in result.diagnostics if d.rule == "question-unused")
    assert _found(_result(_note("{{placeholder: fee}}") + "Pay {{placeholder: fee}}.\n", front)) == []


def test_a_decision_question_only_a_note_choose_uses_is_unused():
    front = "questions:\n  vat:\n    type: boolean\n"
    choose = "{{choose: vat, true=\"with VAT\", false=\"without\"}}"
    assert _found(_result(_note(choose), front)) == [("question-unused", 4)]
    assert _found(_result(_note(choose) + choose + "\n", front)) == []


def test_a_question_a_condition_uses_is_used_whatever_the_notes_do():
    front = "questions:\n  vat:\n    type: boolean\n"
    choose = _note("{{choose: vat, true=\"with VAT\", false=\"without\"}}")
    assert _found(_result("Text. {when=vat}\n\n" + choose, front)) == []


# ── What a note's occurrence still draws, by itself (§15.6) ─────────────

@pytest.mark.parametrize(("directive", "rule"), [
    ("{{placeholder: Fee}}", "placeholder-id-malformed"),
    ("{{placeholder: fee, type=number}}", "placeholder-type-invalid"),
    ("{{placeholder: fee, type=money, currency=ZZZ}}", "placeholder-unknown-currency"),
    ("{{placeholder: fee, type=duration, unit=M}}", "duration-invalid-unit"),
])
def test_a_note_placeholder_is_validated_by_itself(directive, rule):
    assert rule in {r for r, _ in _found(_result(_note(directive)))}


def test_a_note_placeholder_is_held_to_its_declared_question():
    front = "questions:\n  fee:\n    type: money\n  vat:\n    type: boolean\n"
    rules = _found(_result(_note("{{placeholder: fee, type=text}} {{placeholder: vat}}"), front))
    assert [r for r, _ in rules].count("placeholder-question-mismatch") == 2


# ── The final option (§15.9) ────────────────────────────────────────────

def test_a_placeholder_in_a_note_is_still_unfilled_under_the_final_option():
    note = _note("{{placeholder: fee}}")
    result = _result(note, final=True)
    assert {r for r, _ in _found(result)} == {"placeholder-unfilled", "template-construct-present"}
    assert [r for r, _ in _found(_result("Pay {{placeholder: fee}}.\n", final=True))] == ["placeholder-unfilled"]


# ── The forms of a note ─────────────────────────────────────────────────

@pytest.mark.parametrize("note", [
    "- Item\n\n  > [!DRAFTING]\n  > {{placeholder: fee, type=money, currency=USD}}\n\n",
    "> [!DRAFTING]\n> Text\n> {{placeholder: fee, type=money, currency=USD}}\n\n",
    "> [!DRAFTING]\n> Text\n{{placeholder: fee, type=money, currency=USD}}\n\n",
    "> > [!DRAFTING]\n> > {{placeholder: fee, type=money, currency=USD}}\n\n",
    "> [!drafting]\n> {{placeholder: fee, type=money, currency=USD}}\n\n",
])
def test_the_forms_of_a_note_do_not_reach_the_blank(note):
    result = _result(note + "Pay {{placeholder: fee}}.\n")
    assert _found(result) == []
    assert result.index.blanks["fee"].type == "text"


def test_a_fragment_whose_host_is_a_template_is_the_same():
    text = _note("{{placeholder: fee, type=money, currency=USD}}") + "Pay {{placeholder: fee}}.\n"
    result = validate(parse("## Part {#part}\n\n" + text), fragment=True, template=True)
    # (The note itself is not allowed in a template's fragment, §15.3: that is all it draws.)
    assert [d.rule for d in result.diagnostics] == ["template-fragment-invalid"]
    assert result.index.blanks["fee"].type == "text"


# ── Assembly reads a blank as the validator does ────────────────────────

def test_assembly_takes_the_type_of_a_blank_from_the_body_not_from_a_note_before_it():
    text = _document(_note("Fee {{placeholder: fee, type=money, currency=USD}}.") + "Pay {{placeholder: fee}}.\n")
    template = parse_template(text)
    (question,) = template.questions
    assert (question.id, question.type, question.currency) == ("fee", "text", None)
    # What the validator took for a text blank is answered with text: before, this was answer-invalid.
    form = template.form({"fee": "five hundred"})
    assert form.diagnostics == ()
    assert form.assemble().output.endswith("Pay five hundred.\n")


def test_assembly_takes_the_currency_of_a_blank_from_the_body_not_from_a_note():
    text = _document(
        _note("{{placeholder: fee, type=money, currency=USD}}") + "Pay {{placeholder: fee, type=money, currency=EUR}}.\n"
    )
    template = parse_template(text)
    (question,) = template.questions
    assert (question.type, question.currency) == ("money", "EUR")
    form = template.form({"fee": "500"})
    assert form.diagnostics == ()
    assert form.assemble().output.endswith("Pay {{money: 500, currency=EUR}}.\n")
    # ... and an amount with the note's currency is the wrong currency.
    assert [d.rule for d in template.form({"fee": "500 USD"}).diagnostics] == ["answer-invalid"]


def test_an_id_only_a_note_uses_is_still_a_text_question_in_assembly():
    template = parse_template(_document(_note("Fee {{placeholder: fee, type=money, currency=USD}}.") + "Text.\n"))
    (question,) = template.questions
    assert (question.id, question.type, question.currency, question.declared) == ("fee", "text", None, False)
    # It is known, so an answer to it is not answer-unknown; and it is asked of no one.
    assert template.form({"fee": "x"}).diagnostics == ()
    assert template.form({}).unanswered == ()
    assert isinstance(template, Template)


def test_questions_are_in_the_order_of_the_first_occurrence_outside_a_note():
    body = _note("{{placeholder: b}} {{placeholder: c}}") + "Pay {{placeholder: a}} to {{placeholder: b}}.\n"
    template = parse_template(_document(body))
    # a and b by the body; c, which only the note uses, after them. (`index.blanks` has the first two:
    # it has no note-only id, and no declared question that no placeholder uses.)
    assert [q.id for q in template.questions] == ["a", "b", "c"]
    assert list(validate(parse(_document(body))).index.blanks) == ["a", "b"]
    # A declared question comes first, in the order of the declarations, wherever it is used.
    declared = parse_template(_document(body, "questions:\n  z:\n    type: text\n"))
    assert [q.id for q in declared.questions] == ["z", "a", "b", "c"]
    noted = _note("{{placeholder: d}}") + _note("{{placeholder: c}}") + "{{placeholder: a}}\n"
    assert [q.id for q in parse_template(_document(noted)).questions] == ["a", "d", "c"]
