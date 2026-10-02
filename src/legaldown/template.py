"""Templates and their forms (LegalDown §15.7): a template read once, and what
is asked of the person filling it, given what they have answered so far.

``load_template`` (or ``parse_template``) reads a template and the files it
includes, once. ``Template.form(answers)`` is a snapshot of the interview:
the questions reached so far, which of them are unanswered, which stop
assembly, what is wrong with the answers given, and whether assembly can run.
It is a plain value: change the answers, ask for a new form.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .assembly import (
    _MISSING,
    AssemblyResult,
    Question,
    _answer_pairs,
    _answer_problem,
    _Answers,
    _decide,
    _emit_result,
    _FormAnswers,
    _missing_diagnostic,
    _questions,
    _read,
)
from .files import LoadFile, file_loader
from .validator import validate
from .validator.result import Diagnostic, ValidationResult

__all__ = ["Form", "Template", "load_template", "parse_template"]

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
    # What a placeholder fills in is checked as the directive it becomes.
    "duration-invalid-unit",
    "directive-duplicate-param",
})

#: A rule of the validator that a template must satisfy, unless it reads other
#: files: it then cannot tell a reference to a section of a fragment from one
#: to a section its condition removes.
_ALONE_ONLY: frozenset[str] = frozenset({"condition-reference-unsafe"})


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

    *check* is whether ``problems`` include the validator's template rules
    (``TEMPLATE_RULES``); ``load_template`` and ``parse_template`` always do.

    Raises ``FrontmatterError`` when the template's frontmatter cannot be read.
    """

    def __init__(
        self, text: str, *, resolve: LoadFile | None = None, path: Path | None = None, check: bool = True
    ) -> None:
        self.path = path
        self._resolve = _snapshot(resolve)
        self._t = _read(text, self._resolve)
        self._check = check

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
        problems = list(self._t.problems)
        if self._check:
            rules = TEMPLATE_RULES if self._t.subs else TEMPLATE_RULES | _ALONE_ONLY
            problems += [d for d in self.validation.diagnostics if d.level == "error" and d.rule in rules]
        return tuple(problems)

    def form(self, answers: Mapping[str, Any] | None = None) -> Form:
        """The form for *answers*, the answers so far (copied: the form does not
        change when they do)."""
        return _build_form(self, {} if answers is None else answers)


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

    def problem(self, qid: str) -> str | None:
        """Why the answer to question *qid* — the answer given, else its
        default — is not valid, or None."""
        answer = _Answers(self._answers, self._template._t.declared).get(qid)
        if answer is _MISSING:
            return None
        return _answer_problem(self._template._t, qid, answer)

    def assemble(self) -> AssemblyResult:
        """Assemble the template with these answers (§15.7.2). Nothing is
        assembled while the template has ``problems`` or an answer is in error;
        the result then holds the diagnostics alone."""
        template = self._template
        if template.problems:
            return AssemblyResult(diagnostics=list(template.problems))
        return _emit_result(template._t, self._resolved, self._decision, list(self.diagnostics))


def _copied(value: Any) -> Any:
    """*value* with its containers copied, so that it does not change when the
    caller's does; anything else is kept as it is (an answer of no valid kind is
    reported, not copied)."""
    if isinstance(value, dict):
        return {key: _copied(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copied(item) for item in value]
    return value


def _build_form(template: Template, answers: Mapping[str, Any]) -> Form:
    t = template._t
    given = _copied(dict(answers))
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
    return Template(file.read_bytes().decode("utf-8"), resolve=file_loader(file.parent), path=file)
