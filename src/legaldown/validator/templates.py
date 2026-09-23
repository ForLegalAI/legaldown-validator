"""Template questions (spec §15.2) and the answers they accept (§15.7.1).

A question's ``default`` must be an answer assembly would accept, so the
answer rules live here, next to the declarations they apply to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .helpers import is_positive_numeric, is_valid_iso_date, is_valid_money_amount
from .patterns import (
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import ValidationResult

#: Questions filled through ``{{placeholder:}}`` (§15.2): the placeholder
#: types (§10.7).
VALUE_QUESTION_TYPES: frozenset[str] = VALID_PLACEHOLDER_TYPES
#: Questions used by conditions and ``{{choose:}}`` (§15.2).
DECISION_QUESTION_TYPES: frozenset[str] = frozenset({"boolean", "choice"})
QUESTION_TYPES: frozenset[str] = VALUE_QUESTION_TYPES | DECISION_QUESTION_TYPES

#: Words a YAML 1.1 reader takes for a boolean or null, which §15.2 forbids
#: as question ids, choice value ids, and a template's placeholder ids.
YAML_KEYWORDS: frozenset[str] = frozenset(
    {"y", "n", "yes", "no", "on", "off", "true", "false", "null"}
)


@dataclass(slots=True)
class Blank:
    """Every occurrence of one placeholder id — one logical blank (§10.7)."""
    type: str
    #: The ``currency`` and ``unit`` of each occurrence, ``""`` where it fixes
    #: none. Two different ones are an Error (placeholder-type-inconsistent).
    currencies: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    in_frontmatter: bool = False


def question_type(questions: Any, question_id: str) -> str | None:
    """The declared type of question *question_id*, or None when it is not
    declared with one of the six types (question-invalid reports that)."""
    if not isinstance(questions, dict):
        return None
    declaration = questions.get(question_id)
    if not isinstance(declaration, dict):
        return None
    declared = declaration.get("type")
    return declared if isinstance(declared, str) and declared in QUESTION_TYPES else None


def _fixed_by_all(codes: list[str]) -> str | None:
    """The currency or unit every occurrence of a blank fixes, if they all
    fix the same one."""
    distinct = set(codes)
    return codes[0] if len(distinct) == 1 and codes[0] else None


def answer_problem(qtype: str, answer: Any, *, choices: Any = None, blank: Blank | None = None) -> str | None:
    """Why *answer* is not a valid answer to a *qtype* question (§15.7.1,
    §15.7.2 step 1), or None when it is. *blank* describes the question's
    placeholders — the currency or unit they fix, and whether one is in
    frontmatter — and *choices* the declared choices of a ``choice``
    question."""
    if qtype == "text":
        if not isinstance(answer, str) or not answer:
            return "must be a non-empty string"
        if "\n" in answer or "\r" in answer:
            return "must not contain a line break"
        if answer != answer.strip(" \t"):
            return "must not begin or end with a space or tab"
        if blank is not None and blank.in_frontmatter and "{{" in answer:
            return "must not contain '{{' when it fills a placeholder in frontmatter"
        return None
    if qtype == "date":
        if isinstance(answer, date) or (isinstance(answer, str) and is_valid_iso_date(answer)):
            return None
        return "must be an ISO 8601 date (YYYY-MM-DD)"
    if qtype == "money":
        return _measure_problem(
            answer, "amount", "currency", blank.currencies if blank else [],
            valid_amount=lambda v: isinstance(v, str) and is_valid_money_amount(v),
            amount_rule="a string in §10.3 format",
            valid_code=lambda c: c in KNOWN_CURRENCIES,
            code_rule="an ISO 4217 code",
        )
    if qtype == "duration":
        return _measure_problem(
            answer, "value", "unit", blank.units if blank else [],
            valid_amount=lambda v: (
                isinstance(v, (int, str)) and not isinstance(v, bool) and is_positive_numeric(str(v))
            ),
            amount_rule=(
                "a positive integer, or a string holding a positive integer or decimal — "
                "not a YAML float, which can alter the digits"
            ),
            valid_code=lambda u: u in VALID_DURATION_UNITS,
            code_rule=f"one of {', '.join(VALID_DURATION_UNITS)}",
        )
    if qtype == "boolean":
        return None if isinstance(answer, bool) else "must be true or false"
    if qtype == "choice":
        if isinstance(answer, str) and isinstance(choices, dict) and answer in choices:
            return None
        return "must be one of the declared choices"
    return None


def _measure_problem(
    answer: Any,
    amount_key: str,
    code_key: str,
    codes: list[str],
    *,
    valid_amount,
    amount_rule: str,
    valid_code,
    code_rule: str,
) -> str | None:
    """A money or duration answer: ``{amount, currency}`` / ``{value, unit}``,
    or the amount alone when every placeholder for the question fixes the
    same code. *codes* are the codes its placeholders fix."""
    if isinstance(answer, dict):
        if set(answer) != {amount_key, code_key}:
            return f"must be a map of exactly '{amount_key}' and '{code_key}'"
        amount, code = answer.get(amount_key), answer.get(code_key)
        if not valid_amount(amount):
            return f"'{amount_key}' must be {amount_rule}"
        if not isinstance(code, str) or not valid_code(code):
            return f"'{code_key}' must be {code_rule}"
        fixed = next((c for c in codes if c and c != code), None)
        if fixed is not None:
            return f"'{code_key}' {code} disagrees with the {code_key} {fixed} its placeholders fix"
        return None
    if _fixed_by_all(codes) is None:
        return (
            f"must be a map of '{amount_key}' and '{code_key}' — the {amount_key} alone "
            f"is accepted only when every placeholder for the question fixes the same {code_key}"
        )
    if not valid_amount(answer):
        return f"must be {amount_rule}"
    return None


def _id_problem(value: str) -> str | None:
    """Why *value* cannot be a question id or choice value id (§15.2)."""
    if not IDENTIFIER_RE.fullmatch(value):
        return "must match [a-z][a-z0-9-]*"
    if value in YAML_KEYWORDS:
        return "is read by YAML as a boolean or null (y, n, yes, no, on, off, true, false, null)"
    return None


def _choices_problem(choices: Any) -> str | None:
    """Why *choices* is not a valid ``choices`` map (§15.2)."""
    if not isinstance(choices, dict) or len(choices) < 2:
        return "must declare at least two choices, as a map of value id to label"
    for value, label in choices.items():
        problem = _id_problem(str(value))
        if problem:
            return f"value id '{value}' {problem}"
        if not isinstance(label, str) or not label.strip():
            return f"the label of '{value}' must be non-empty text"
    return None


def check_questions(
    questions: Any,
    blanks: dict[str, Blank],
    result: ValidationResult,
    *,
    template: bool,
    not_line_editable: list[str],
) -> None:
    """Report malformed question declarations (question-invalid, §15.2).

    *blanks* are the document's placeholders by id: a default must agree with
    the currency or unit its question's placeholders fix, and in a *template*
    an undeclared placeholder id is a question id too.
    """
    def invalid(message: str) -> None:
        result.error("question-invalid", message)

    if questions is not None and not isinstance(questions, dict):
        invalid("'questions' must be a map of question id to declaration (§15.2).")
    declared = questions if isinstance(questions, dict) else {}
    for qid, declaration in declared.items():
        problem = _id_problem(str(qid))
        if problem:
            invalid(f"Question id '{qid}' {problem} (§15.2).")
        qtype = question_type(declared, qid)
        if qtype is None:
            invalid(
                f"Question '{qid}' must declare a type: text, date, money, duration, "
                f"boolean, or choice (§15.2)."
            )
            continue
        choices = declaration.get("choices")
        if qtype == "choice":
            problem = _choices_problem(choices)
            if problem:
                invalid(f"Choice question '{qid}': {problem} (§15.2).")
                continue
        elif "choices" in declaration:
            invalid(f"Question '{qid}' is not a choice question and cannot declare choices (§15.2).")
        if "default" in declaration:
            problem = answer_problem(
                qtype, declaration["default"], choices=choices, blank=blanks.get(qid)
            )
            if problem:
                invalid(f"The default of question '{qid}' is not a valid answer (§15.7.1): {problem}.")
    for key in not_line_editable:
        if key == "questions" or template:
            invalid(
                f"'{key}' must be written in YAML block style, one key per line"
                + (", each entry beginning with '- id:'" if key == "attachments" else "")
                + ", so that assembly can edit it line by line (§15.2)."
            )
    if template:
        for pid in blanks:
            if pid not in declared and pid in YAML_KEYWORDS:
                invalid(
                    f"Placeholder id '{pid}' is read by YAML as a boolean or null, so it "
                    f"cannot be answered portably in a template (§15.2)."
                )
