"""Template conditions (spec §15.3) and the reasoning over them (§15.4).

A condition is one test of one decision question. A unit's *presence* is the
set of conditions that must all hold for it to appear in an assembled
document: its own condition and those of every unit enclosing it.
"""
from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from .result import Depends
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


def condition_issue(text: str, questions: Any) -> tuple[Depends | None, str] | None:
    """What is wrong with *text* as a condition for *questions* (the document's
    ``metadata.questions``; condition-invalid, §15.3): ``(depends, problem)``,
    or None when it is valid. *depends* is what only the including document's
    frontmatter can settle (``Depends.FRONTMATTER``): the condition names a
    question that is not declared here, which a fragment (§12.2) may well
    leave to its host (#39). It is None for a problem the condition has
    whatever is declared: a malformed condition, or the wrong form for a
    question that is declared."""
    condition = parse_condition(text)
    if condition is None:
        return None, "must be '[!]question' or '[!]question:value', in the identifier format"
    qtype = question_type(questions, condition.question)
    if qtype not in ("boolean", "choice"):
        # Undeclared, or declared with no usable type: the declaration is not here
        # to read. Declared as a value question it is wrong wherever it is read.
        depends = Depends.FRONTMATTER if qtype is None else None
        return depends, f"names '{condition.question}', which is not a declared boolean or choice question"
    if qtype == "boolean" and condition.value is not None:
        return None, f"'{condition.question}' is a boolean question: write '{condition.question}' or '!{condition.question}'"
    if qtype == "choice":
        if condition.value is None:
            return None, f"'{condition.question}' is a choice question: write '{condition.question}:value'"
        choices = questions[condition.question].get("choices")
        if not isinstance(choices, dict) or condition.value not in choices:
            return None, f"'{condition.value}' is not a declared choice of '{condition.question}'"
    return None


def condition_problem(text: str, questions: Any) -> str | None:
    """Why *text* is not a valid condition for *questions* (the document's
    ``metadata.questions``; condition-invalid, §15.3), or None."""
    issue = condition_issue(text, questions)
    return issue[1] if issue else None


_BOOLEAN_ANSWERS = (True, False)


def question_answers(question: str, questions: Any) -> Collection[Any]:
    """Every answer *question* can have: true and false, or its choices."""
    if question_type(questions, question) == "boolean":
        return _BOOLEAN_ANSWERS
    choices = questions[question].get("choices")
    return choices if isinstance(choices, dict) else ()  # a dict is read as its keys


def holds_under(presence: Iterable[Condition], answers: dict[str, Any]) -> bool:
    """True if every condition in *presence* holds when each question in *answers* has the answer it gives. A
    condition on a question that *answers* does not name holds (``answer_combinations``)."""
    return all(condition.holds(answers[condition.question]) for condition in presence if condition.question in answers)


def answer_combinations(
    presences: Iterable[Presence], questions: Any, *, max_questions: int, max_combinations: int
) -> tuple[list[str], list[dict[str, Any]]] | None:
    """Every combination of answers to the questions that *presences* name (§15.4), as the questions
    in the order they are first used (*presences* in order; those of one presence in the order
    *questions* declares them) and the combinations in the order of their answers: true before
    false, a choice's values as declared, the last question changing fastest. A question that has
    no answer at all (a choice without choices: ``question-invalid`` says so) is left out: its
    conditions hold. None when there are more than *max_questions* questions or more than
    *max_combinations* combinations, which is too many to try."""
    position = {question: index for index, question in enumerate(questions)} if isinstance(questions, dict) else {}

    def answers_of(question: str) -> Collection[Any]:
        try:
            return question_answers(question, questions)
        except (KeyError, AttributeError, TypeError):  # not declared, or no mapping: no answer to give
            return ()

    asked: list[str] = []
    for presence in presences:
        for question in sorted({condition.question for condition in presence}, key=lambda q: position.get(q, len(position))):
            if question not in asked and answers_of(question):
                asked.append(question)
    if len(asked) > max_questions:
        return None
    count = 1
    for question in asked:
        count *= len(answers_of(question))
        if count > max_combinations:
            return None
    combinations = [
        dict(zip(asked, answers, strict=True))
        for answers in itertools.product(*(answers_of(question) for question in asked))
    ]
    return asked, combinations


def by_question(presence: Iterable[Condition]) -> dict[str, list[Condition]]:
    """The conditions in *presence* on each question they name."""
    grouped: dict[str, list[Condition]] = {}
    for condition in presence:
        grouped.setdefault(condition.question, []).append(condition)
    return grouped


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
    return _covered(possible, [_profile(target) for target in targets])


#: A presence by question: the values its conditions on it want, and refuse
#: (``condition_values``).
_Profile = dict[str, tuple[frozenset[Any], frozenset[Any]]]


def _profile(target: Presence) -> _Profile:
    return {
        question: (frozenset(wanted), frozenset(refused))
        for question, conditions in by_question(target).items()
        for wanted, refused in [condition_values(conditions)]
    }


def _covered(possible: dict[str, list[Any]], targets: Sequence[_Profile]) -> bool:
    """True if every combination of the *possible* answers makes one of the
    *targets* hold. *possible* is narrowed as the search goes and restored
    when it ends.

    Each step takes the question some live target leaves open and tries each
    answer to it, with the targets that can hold under that answer (``_step``).
    The search keeps its own stack of the questions narrowed, so that its depth
    is not the interpreter's (a target for each of many questions would be
    deep), and so that ``possible`` is restored whichever way it ends.
    """
    stack: list[tuple[str, list[Any], Iterator[tuple[Any, list[_Profile]]]]] = []
    covered: bool | None = None  # None: *targets* are still to be looked at
    try:
        while True:
            if covered is None:
                step = _step(possible, targets)
                if isinstance(step, bool):
                    covered = step
                else:
                    stack.append(step)
            if covered is False or (covered and not stack):
                return covered
            question, saved, branches = stack[-1]
            branch = next(branches, None)
            if branch is None:
                possible[question] = saved  # every answer to it is covered
                stack.pop()
                covered = True
            else:
                possible[question] = [branch[0]]
                targets = branch[1]
                covered = None
    finally:
        while stack:
            question, saved, _branches = stack.pop()
            possible[question] = saved


def _step(
    possible: dict[str, list[Any]], targets: Sequence[_Profile]
) -> bool | tuple[str, list[Any], Iterator[tuple[Any, list[_Profile]]]]:
    """Whether the *targets* are decided under the *possible* answers: True
    if one is certain, False if none can hold. Otherwise the question to
    narrow, its answers as they were, and the branches to try, each an answer
    and the targets that can hold under it."""
    live: list[tuple[_Profile, int, str]] = []  # a target, the questions it leaves open, the first
    undecided = ""  # a question some live target leaves open
    for profile in targets:
        open_count = 0
        open_question = ""
        for question, (wanted, refused) in profile.items():
            answers = possible[question]
            if len(wanted) > 1:
                holding = 0
            elif wanted:
                (value,) = wanted
                holding = int(value in answers and value not in refused)
            else:
                holding = len(answers) - sum(1 for value in refused if value in answers)
            if not holding:
                break  # impossible under these answers
            if holding < len(answers):
                open_count += 1
                open_question = open_question or question
        else:
            if not open_question:
                return True  # certain under these answers
            live.append((profile, open_count, open_question))
            undecided = undecided or open_question
    if not live:
        return False
    answers = possible[undecided]
    # The live targets that want an answer to the question, by answer, and those that
    # refuse some (or none): they hold under every other answer.
    wanting: dict[Any, list[_Profile]] = {}
    refusing: list[tuple[_Profile, frozenset[Any]]] = []
    mentioned: set[Any] = set()
    # The answers refused by every target that refuses and leaves open only this
    # question. Such a target is certain once the question is narrowed to an
    # answer it does not refuse, so the answers outside this set are covered,
    # without building the targets that hold under each. (With many refusing
    # alternatives, as ``!c:v1`` … ``!c:v2000``, that is what keeps a reference
    # to them from costing a pass over all of them for each answer.)
    sure: set[Any] | None = None
    for profile, open_count, open_question in live:
        wanted, refused = profile.get(undecided, (frozenset(), frozenset()))
        if wanted:
            (value,) = wanted
            wanting.setdefault(value, []).append(profile)
            mentioned.add(value)
        else:
            refusing.append((profile, refused))
            mentioned.update(refused)
            if open_count == 1 and open_question == undecided:
                refused_here = {value for value in refused if value in answers}
                sure = refused_here if sure is None else sure & refused_here

    def branches() -> Iterator[tuple[Any, list[_Profile]]]:
        unmentioned = False
        for answer in answers:
            if sure is not None and answer not in sure:
                continue  # a target that refuses holds under it
            if answer not in mentioned:
                # Answers that no live target names are alike: try one.
                if unmentioned:
                    continue
                unmentioned = True
            yield answer, [
                *wanting.get(answer, ()),
                *(profile for profile, refused in refusing if answer not in refused),
            ]

    return undecided, answers, branches()
