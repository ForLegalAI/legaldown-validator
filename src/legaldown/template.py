"""Templates and their forms (LegalDown §15.7): a template read once, and what
is asked of the person filling it, given what they have answered so far.

``load_template`` (or ``parse_template``) reads a template and the files it
includes, once. ``Template.form(answers)`` is a snapshot of the interview:
the questions reached so far, which of them are unanswered, which stop
assembly, what is wrong with the answers given, and whether assembly can run.
It is a plain value: change the answers, ask for a new form.
"""
from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml

from .assembly import (
    _MISSING,
    AssemblyResult,
    Question,
    _answer_pairs,
    _answer_problem,
    _Answers,
    _decide,
    _effective_type,
    _emit_result,
    _FormAnswers,
    _missing_diagnostic,
    _questions,
    _read,
    _Template,
)
from .directives import PLACEHOLDER_TYPE_PARAMS
from .files import LoadFile, file_loader
from .validator import validate
from .validator.patterns import VALID_DURATION_UNITS
from .validator.result import Diagnostic, ValidationResult

__all__ = ["AnswersError", "Form", "Template", "load_answers", "load_template", "parse_template"]

#: The rules of the validator that a template must satisfy for assembly
#: (§15.7.2 gives assembly a template that validates without Errors): those of
#: questions, conditions, ``{{choose:}}``, placeholders and insertions. Any
#: other Error of ``Template.validation`` is advice — the validator reads a
#: template alone, without the files it includes, so a reference to a section
#: of a fragment is ``ref-broken`` to it while assembly reads the fragment.
TEMPLATE_RULES: frozenset[str] = frozenset({
    "question-invalid",
    "condition-invalid",
    "choose-invalid",
    "placeholder-id-malformed",
    "placeholder-in-structural-field",
    "placeholder-question-mismatch",
    "placeholder-type-inconsistent",
    "placeholder-type-invalid",
    "insertion-boundary",
    "def-term-variable",
    "drafting-note-def",
    "template-fragment-invalid",
})

#: A rule of the validator that a template must satisfy, unless it includes
#: fragments: it then cannot tell a reference to a section of a fragment from
#: one to a section its condition removes.
_ALONE_ONLY: frozenset[str] = frozenset({"condition-reference-unsafe"})


def _coerced(question: Question, value: Any) -> Any:
    """*value* as an answer to *question*, if it can be read as one without guessing."""
    if isinstance(value, str):
        if not value:
            return value
        try:
            answer = question.from_text(value)
        except ValueError:
            return value
        return value if answer is None else answer
    if question.type == "money":
        try:
            if type(value) is int:
                return str(value)
            if isinstance(value, dict) and set(value) == {"amount", "currency"} and type(value["amount"]) is int:
                return {**value, "amount": str(value["amount"])}
        except ValueError:  # an integer too long for Python to write as text
            pass
    return value


class AnswersError(ValueError):
    """An answers file that is not an answers set (§15.7.1): not YAML, or not a
    mapping of question ids to answers."""


def load_answers(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The answers set (§15.7.1) in the YAML file at *path*: a mapping of question
    ids to answers, empty for an empty file. Plain YAML: ``Template.coerce``
    puts right what it gets wrong for the template's questions.

    Raises ``OSError`` when the file cannot be read, and ``AnswersError`` when it
    is not UTF-8, not YAML (a date such as ``2026-13-45`` is not one), or not a
    mapping."""
    file = Path(path)
    try:
        answers = yaml.safe_load(file.read_bytes().decode("utf-8-sig"))
    except (yaml.YAMLError, ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
        raise AnswersError(f"cannot read the answers in {file}: {exc}") from exc
    if answers is None:
        return {}
    if not isinstance(answers, dict):
        raise AnswersError(f"the answers in {file} must be a YAML mapping of question ids to answers")
    return answers


def _text(value: Any) -> str:
    """``str(value)``, where an integer too long for Python to write is a note."""
    try:
        return str(value)
    except ValueError:
        return "<an integer too long to write>"


_JSON_NODES = 10_000
_JSON_DEPTH = 40


def _jsonable(value: Any, budget: list[int] | None = None, depth: int = 0) -> Any:
    """*value* as strict JSON holds it: a date as its ISO text, a map with text
    keys, a number that is not finite (or too long to write) as text. An answer
    is data a person or a file gave: what is nested too deep, or runs past a
    budget of nodes (an alias bomb in a YAML file), is cut short, so that
    describing it stays cheap."""
    budget = [_JSON_NODES] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > _JSON_DEPTH:
        return "..."
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {_text(key): _jsonable(item, budget, depth + 1) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_jsonable(item, budget, depth + 1) for item in sorted(value, key=_text)]
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, budget, depth + 1) for item in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        text = _text(value)
        return text if text.startswith("<") else value
    if value is None or isinstance(value, (str, bool)):
        return value
    return _text(value)


def _diagnostic_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    return {"rule": diagnostic.rule, "level": diagnostic.level, "message": diagnostic.message, "line": diagnostic.line}


def _placeholder_problems(t: _Template) -> list[Diagnostic]:
    """A placeholder that would fill in a directive the validator rejects: a
    repeated parameter, or a duration unit that §10.5 does not define. Those
    in drafting notes are removed with them, and are no problem."""
    found = [(source.notes, occ) for source in (t.main, *t.subs.values()) for occ in source.occurrences]
    found += [(set(), occ) for occ in (t.front.occurrences if t.front else [])]
    problems = []
    for notes, occ in found:
        directive = occ.directive
        if directive.name != "placeholder" or occ.line in notes:
            continue
        for param in dict.fromkeys(directive.duplicates):
            problems.append(Diagnostic(
                "directive-duplicate-param", "error",
                f"'{directive.source}' repeats the parameter '{param}'; the template is not assembled.",
            ))
        unit = directive.params.get(PLACEHOLDER_TYPE_PARAMS["duration"])
        if _effective_type(directive, t.declared) == "duration" and unit is not None and unit not in VALID_DURATION_UNITS:
            problems.append(Diagnostic(
                "duration-invalid-unit", "error",
                f"'{directive.source}' fixes the duration unit '{unit}', which §10.5 does not define; "
                f"the template is not assembled.",
            ))
    return problems


def _snapshot(resolve: LoadFile | None) -> LoadFile | None:
    """*resolve* asking each file once, so that reading the template and
    validating it see the same text."""
    if resolve is None:
        return None
    seen: dict[str, str | None] = {}

    def read(path: str) -> str | None:
        if path not in seen:
            seen[path] = resolve(path)
        return seen[path]

    return read


class Template:
    """A template: its source, the include fragments and LegalDown attachment
    files it reads, and its questions. Read once; ``form`` asks and assembles
    as often as needed. A template is a snapshot: files that change on disk
    afterwards are not seen — load it again.

    ``Template(text, resolve=None)`` is ``parse_template``: *resolve* reads the
    include fragments and LegalDown attachment files the template names.
    ``path`` is the file ``load_template`` read it from, absolute, and ``None``
    for a template made from its text.

    Raises ``FrontmatterError`` when the template's frontmatter cannot be read.
    """

    def __init__(self, text: str, *, resolve: LoadFile | None = None, _check: bool = True) -> None:
        # Private: ``_check=False`` leaves the validator's template rules
        # (``TEMPLATE_RULES``) out of ``problems``, as the deprecated ``assemble``
        # does, for the tests and the conformance harness that check assembly
        # on its own.
        self.path: Path | None = None
        self._resolve = _snapshot(resolve)
        self._t = _read(text, self._resolve)
        self._check = _check

    @cached_property
    def questions(self) -> tuple[Question, ...]:
        """Every question (§15.2): the declared ones in declaration order, then
        each undeclared placeholder, in the order first written."""
        return tuple(_questions(self._t))

    @cached_property
    def validation(self) -> ValidationResult:
        """What ``validate`` says of the template read alone. Advice: only the
        rules in ``problems`` stop assembly."""
        return validate(self._t.document, resolve=self._resolve)

    @cached_property
    def problems(self) -> tuple[Diagnostic, ...]:
        """Why the template cannot be assembled, whatever the answers: a file
        it includes that cannot be read, a malformed placeholder, a translation
        group, and the template rules (``TEMPLATE_RULES``) the validator reports
        as Errors. Empty when it can."""
        t = self._t
        problems = list(t.problems)
        if self._check:
            includes = any(include.path in t.subs for include in t.main.includes)
            rules = TEMPLATE_RULES if includes else TEMPLATE_RULES | _ALONE_ONLY
            problems += [d for d in self.validation.diagnostics if d.level == "error" and d.rule in rules]
            problems += _placeholder_problems(t)
        return tuple(problems)

    def form(self, answers: Mapping[str, Any] | None = None) -> Form:
        """The form for *answers*, the answers so far (copied: the form does not
        change when they do)."""
        return _build_form(self, {} if answers is None else answers)

    def coerce(self, answers: Mapping[str, Any]) -> dict[str, Any]:
        """*answers* with the shapes that plain YAML or JSON get wrong put right,
        as a new mapping; *answers* is not changed, and nothing is raised.

        For a question of the template: text is read as ``Question.from_text``
        reads it (``yes`` for a boolean, ``5000 EUR`` for money, a choice by its
        label, surrounding spaces dropped), except the empty text, which stays;
        a money amount written as a number (``fee: 5000``, bare or in the
        ``amount`` of a map) becomes the string the specification wants.
        Anything it cannot put right — a float, a ``yes`` that YAML made ``True``
        for a text question, an unknown id — is left as it is, for ``form`` to
        report. A form does not do this by itself: its diagnostics are the
        specification's. A front end that takes answers from a person or a file
        calls it first. Anything but a mapping is returned unchanged."""
        if not isinstance(answers, Mapping):
            return answers  # type: ignore[return-value]
        by_id = {question.id: question for question in self.questions}
        return {
            key: _coerced(by_id[key], value) if isinstance(key, str) and key in by_id else value
            for key, value in answers.items()
        }


@dataclass(slots=True, frozen=True)
class Form:
    """What is asked of the person filling a template, given the answers so
    far (``Template.form``); a snapshot.

    ``questions`` are those reached now, in the order they are reached,
    answered or not: a decision question when a condition or ``{{choose:}}``
    using it lies in a present unit, a value question when one of its
    placeholders does. A unit under a condition whose question is unanswered
    is not reached yet, so the form grows as decisions are made.

    ``unanswered`` are the reached questions with no valid answer and no
    default; ``blocking`` the decision questions among them, which stop
    assembly (§15.7.2 step 1), while an unanswered value question leaves its
    blank. An answer that is not valid counts as no answer. ``unused`` are the
    questions with an answer that are not reached: advisory, since one answer
    that changes can make many unused.

    ``diagnostics`` say what is wrong with the answers; ``ready`` is whether
    assembly can run (the template has no ``problems``, no answer is in
    error) and ``complete`` whether nothing is left blank: ready, and no
    question unanswered.
    """

    questions: tuple[Question, ...]
    unanswered: tuple[Question, ...]
    blocking: tuple[Question, ...]
    unused: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    ready: bool
    complete: bool
    _template: Template = field(repr=False, compare=False)
    _answers: dict = field(repr=False, compare=False)
    _resolved: _FormAnswers = field(repr=False, compare=False)
    _decision: Any = field(repr=False, compare=False)
    _pairs: tuple = field(repr=False, compare=False)

    def problem(self, qid: str) -> str | None:
        """Why the answer to question *qid* — the answer given, else its
        default — is not valid, or None."""
        answer = _Answers(self._answers, self._template._t.declared).get(qid)
        if answer is _MISSING:
            return None
        return _answer_problem(self._template._t, qid, answer)

    def as_dict(self) -> dict[str, Any]:
        """The form as plain data — JSON-ready — for a front end, a service or an
        agent: ``ready``, ``complete``, the template's ``problems``, the
        ``diagnostics`` about the answers (each with the ``question`` it is about,
        ``""`` for none), and ``questions``: those reached first, in order, then
        the others, each with

        - ``id``, ``type``, ``label`` (its prompt, else its id), ``prompt``,
          ``declared``, ``choices``, ``currency``, ``unit``;
        - ``reached`` and ``blocking`` (a decision that stops assembly);
        - ``state``: ``answered`` (a valid answer), ``default`` (the default
          applies), ``invalid`` (an answer that is not valid; it counts as none)
          or ``unanswered``; ``answer`` (the answer given, else null), ``default``,
          and ``answer_text``, ``default_text`` as a person would type them;
        - ``problem``: why the answer is not valid, else null;
        - ``hint``, in words, and ``accepts``, as data (``Question.accepts``).
        """
        t = self._template._t
        reached = {question.id for question in self.questions}
        blocking = {question.id for question in self.blocking}
        order = [*self.questions, *(q for q in self._template.questions if q.id not in reached)]
        questions = []
        for question in order:
            given = self._answers.get(question.id)
            problem = self.problem(question.id)
            if given is not None:
                state = "invalid" if _answer_problem(t, question.id, given) else "answered"
            else:
                state = "default" if question.default is not None else "unanswered"
            questions.append({
                "id": question.id,
                "type": question.type,
                "label": question.prompt or question.id,
                "prompt": question.prompt,
                "declared": question.declared,
                "reached": question.id in reached,
                "blocking": question.id in blocking,
                "state": state,
                "answer": _jsonable(given),
                "answer_text": question.to_text(given),
                "default": _jsonable(question.default),
                "default_text": question.to_text(question.default),
                "choices": dict(question.choices),
                "currency": question.accepts.get("currency"),
                "unit": question.accepts.get("unit"),
                "problem": problem,
                "hint": question.hint,
                "accepts": question.accepts,
            })
        return {
            "ready": self.ready,
            "complete": self.complete,
            "problems": [_diagnostic_dict(d) for d in self._template.problems],
            "diagnostics": [{**_diagnostic_dict(d), "question": qid} for qid, d in self._pairs],
            "questions": questions,
        }

    def assemble(self) -> AssemblyResult:
        """Assemble the template with these answers (§15.7.2). Nothing is
        assembled while the template has ``problems`` or an answer is in error;
        the result then holds the diagnostics alone."""
        template = self._template
        if template.problems:
            return AssemblyResult(diagnostics=list(template.problems))
        return _emit_result(template._t, self._resolved, self._decision, list(self.diagnostics))


def _copied(value: Any, seen: dict[int, tuple[Any, Any]] | None = None) -> Any:
    """*value* with its containers copied, so that it does not change when the
    caller's does; anything else is kept as it is (an answer of no valid kind is
    reported, not copied). A container met again is the copy already made; the
    memo keeps each original alive, so that its id cannot be taken by another."""
    seen = {} if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return seen[id(value)][1]
        if isinstance(value, dict):
            made: Any = {}
            seen[id(value)] = (value, made)
            for key, item in value.items():
                made[key] = _copied(item, seen)
        else:
            made = []
            seen[id(value)] = (value, made)
            made.extend(_copied(item, seen) for item in value)
        return made
    return value


def _build_form(template: Template, answers: Mapping[str, Any]) -> Form:
    t = template._t
    try:
        given = _copied(dict(answers))
    except RecursionError:  # an answer nested too deep to copy is reported as invalid, as it is kept
        given = dict(answers)
    resolved = _FormAnswers(given, t)
    decision = _decide(t, resolved, inline=True)
    by_id = {question.id: question for question in template.questions}
    reached = tuple(by_id[qid] for qid in decision.needed if qid in by_id)
    unanswered = tuple(question for question in reached if resolved.get(question.id) is _MISSING)
    blocking = tuple(by_id[qid] for qid in decision.needed if qid in decision.missing and qid in by_id)
    pairs = list(_answer_pairs(t, given))
    invalid = {qid for qid, d in pairs if d.rule == "answer-invalid"}
    pairs += [(qid, _missing_diagnostic(qid)) for qid in decision.missing if qid not in invalid]
    reached_ids = {question.id for question in reached}
    diagnostics = tuple(d for _qid, d in pairs)
    ready = not template.problems and not any(d.level == "error" for d in diagnostics)
    return Form(
        questions=reached,
        unanswered=unanswered,
        blocking=blocking,
        unused=tuple(qid for qid in t.types if given.get(qid) is not None and qid not in reached_ids),
        diagnostics=diagnostics,
        ready=ready,
        complete=ready and not unanswered,
        _template=template,
        _answers=given,
        _resolved=resolved,
        _decision=decision,
        _pairs=tuple(pairs),
    )


def parse_template(text: str, *, resolve: Callable[[str], str | None] | None = None) -> Template:
    """A ``Template`` from its source *text*. *resolve* reads the include
    fragments and LegalDown attachment files it names — a relative path to the
    file's text, or ``None`` when there is none (``file_loader`` for files on
    disk). Without it, a template with either is refused (``Template.problems``):
    reading them is a Full capability (§17.6)."""
    return Template(text, resolve=resolve)


def load_template(path: str | os.PathLike[str]) -> Template:
    """The ``Template`` in the file at *path*, with the include fragments and
    attachment files it names, read from beside it (``file_loader``). The file is
    read as written — byte-order mark and line endings included — since assembly
    keeps every byte it does not edit (§15.7.2)."""
    file = Path(path).absolute()
    template = Template(file.read_bytes().decode("utf-8"), resolve=file_loader(file.parent))
    template.path = file
    return template
