"""Template conditions (spec §15.3) and the reasoning over them (§15.4).

A condition is one test of one decision question. A unit's *presence* is the
set of conditions that must all hold for it to appear in an assembled
document: its own condition and those of every unit enclosing it.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .templates import question_type

_CONDITION_RE = re.compile(r"(!?)([a-z][a-z0-9-]*)(?::([a-z][a-z0-9-]*))?")


@dataclass(frozen=True, slots=True)
class Condition:
    """``q``, ``!q``, ``q:v``, or ``!q:v`` (§15.3)."""

    question: str
    value: str | None = None  # the choice value, or None for a boolean test
    negated: bool = False

    def holds(self, answer: Any) -> bool:
        """True if the condition holds when its question's answer is *answer*."""
        expected = True if self.value is None else self.value
        return (answer == expected) != self.negated

    def __str__(self) -> str:
        value = f":{self.value}" if self.value is not None else ""
        return f"{'!' if self.negated else ''}{self.question}{value}"


#: A unit's presence: the conditions that must all hold for it to appear.
Presence = frozenset[Condition]
ALWAYS: Presence = frozenset()


def parse_condition(text: str) -> Condition | None:
    """The condition *text* writes, or None when it does not follow
    ``[ "!" ] identifier [ ":" identifier ]``."""
    match = _CONDITION_RE.fullmatch(text)
    if match is None:
        return None
    negated, question, value = match.groups()
    return Condition(question, value, bool(negated))


def condition_problem(text: str, questions: Any) -> str | None:
    """Why *text* is not a valid condition for *questions* (the document's
    ``metadata.questions``; condition-invalid, §15.3), or None."""
    condition = parse_condition(text)
    if condition is None:
        return "must be '[!]question' or '[!]question:value', in the identifier format"
    qtype = question_type(questions, condition.question)
    if qtype not in ("boolean", "choice"):
        return f"names '{condition.question}', which is not a declared boolean or choice question"
    if qtype == "boolean" and condition.value is not None:
        return f"'{condition.question}' is a boolean question: write '{condition.question}' or '!{condition.question}'"
    if qtype == "choice":
        if condition.value is None:
            return f"'{condition.question}' is a choice question: write '{condition.question}:value'"
        choices = questions[condition.question].get("choices")
        if not isinstance(choices, dict) or condition.value not in choices:
            return f"'{condition.value}' is not a declared choice of '{condition.question}'"
    return None


_BOOLEAN_ANSWERS = (True, False)


def question_answers(question: str, questions: Any) -> Collection[Any]:
    """Every answer *question* can have: true and false, or its choices."""
    if question_type(questions, question) == "boolean":
        return _BOOLEAN_ANSWERS
    choices = questions[question].get("choices")
    return choices if isinstance(choices, dict) else ()  # a dict is read as its keys


def by_question(presence: Iterable[Condition]) -> dict[str, Presence]:
    """The conditions in *presence* on each question they name."""
    grouped: dict[str, set[Condition]] = {}
    for condition in presence:
        grouped.setdefault(condition.question, set()).add(condition)
    return {question: frozenset(conditions) for question, conditions in grouped.items()}


def condition_values(conditions: Iterable[Condition]) -> tuple[set[Any], set[Any]]:
    """The values that *conditions*, all on one question, want, and refuse. A
    condition holds for the answer equal to its value, or for every answer but
    that one when it is negated (``Condition.holds``); a boolean test's value
    is true."""
    wanted: set[Any] = set()
    refused: set[Any] = set()
    for condition in conditions:
        (refused if condition.negated else wanted).add(True if condition.value is None else condition.value)
    return wanted, refused


def _some_answer_holds(conditions: Iterable[Condition], answers: Collection[Any]) -> bool:
    """True if some answer in *answers* makes every one of *conditions*
    (all on one question) hold, without trying each: the answers are those
    equal to the one value wanted, if there is one, less the values refused."""
    wanted, refused = condition_values(conditions)
    if len(wanted) > 1:
        return False
    if wanted:
        (value,) = wanted
        return value in answers and value not in refused
    return len(answers) > sum(1 for value in refused if value in answers)


def satisfiable_in(presence: Iterable[Condition], answers_of: Callable[[str], Collection[Any]]) -> bool:
    """``satisfiable``, with the answers of a question given by *answers_of*:
    for a caller that asks many times and keeps them."""
    return all(
        _some_answer_holds(conditions, answers_of(question))
        for question, conditions in by_question(presence).items()
    )


def satisfiable(presence: Iterable[Condition], questions: Any) -> bool:
    """True if some answers make every condition in *presence* hold — decided
    per question, as §15.4 describes. Defaults play no part."""
    return satisfiable_in(presence, lambda question: question_answers(question, questions))


def exclusive_in(first: Presence, second: Presence, answers_of: Callable[[str], Collection[Any]]) -> bool:
    """``exclusive``, with the answers of a question given by *answers_of*."""
    return not satisfiable_in(first | second, answers_of)


def exclusive(first: Presence, second: Presence, questions: Any) -> bool:
    """True if two units, appearing under the conditions *first* and
    *second* (their ``Presence``), can never appear together for any answers
    to the document's *questions* (``document.metadata.questions``, §15.4)."""
    return exclusive_in(first, second, lambda question: question_answers(question, questions))


def always_covered(presence: Presence, targets: Sequence[Presence], questions: Any) -> bool:
    """True if, whenever a reference with *presence* appears, a declaration
    with one of the *targets* presences appears too (§15.4).

    Equivalent to trying every combination of answers to the questions
    involved, but decided one question at a time: the answers still possible
    for each question are narrowed only while some target is neither certain
    nor impossible, so alternatives nested under many questions stay cheap.
    """
    if any(target <= presence for target in targets):
        return True  # a target present whenever the reference is: no search
    involved = {c.question for c in presence.union(*targets)}
    possible = {
        q: [a for a in question_answers(q, questions) if all(c.holds(a) for c in presence if c.question == q)]
        for q in involved
    }
    if not all(possible.values()):
        return True  # no answers make the reference present
    return _covered(possible, targets)


def _covered(possible: dict[str, list[Any]], targets: Sequence[Presence]) -> bool:
    """True if every combination of the *possible* answers makes one of the
    *targets* hold."""
    live: list[Presence] = []
    undecided = ""  # a question some live target leaves open
    for target in targets:
        open_question = ""
        for question in {c.question for c in target}:
            holding = [
                a for a in possible[question] if all(c.holds(a) for c in target if c.question == question)
            ]
            if not holding:
                break  # impossible under these answers
            if len(holding) < len(possible[question]):
                open_question = question
        else:
            if not open_question:
                return True  # certain under these answers
            live.append(target)
            undecided = undecided or open_question
    if not live:
        return False
    return all(
        _covered({**possible, undecided: [answer]}, live) for answer in possible[undecided]
    )
