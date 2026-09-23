"""Template questions (spec §15.2) and the answers they accept (§15.7.1).

A question's ``default`` must be an answer assembly would accept, so the
answer rules live here, next to the declarations they apply to.
"""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..directives import PLACEHOLDER_TYPE_PARAMS, Directive, Lexed, is_escaped
from ..models import Block, Document
from .helpers import is_positive_numeric, is_valid_iso_date, is_valid_money_amount
from .patterns import (
    DURATION_UNITS,
    IDENTIFIER_RE,
    LEGALDOWN_EXTENSIONS,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import ValidationResult

_CURRENCY_RE = re.compile(r"[A-Z]{3}")

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
    #: The effective type of its first occurrence with a valid one.
    type: str | None = None
    #: What its occurrences of that type fix — the ``currency`` of a money
    #: blank, the ``unit`` of a duration blank — with ``""`` for any that
    #: fixes none. Two codes are an Error (placeholder-type-inconsistent).
    codes: set[str] = field(default_factory=set)
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


def _fixed_by_all(codes: set[str]) -> str | None:
    """The currency or unit every occurrence of a blank fixes, if they all
    fix the same one."""
    return next(iter(codes)) if len(codes) == 1 and "" not in codes else None


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
            answer, "amount", PLACEHOLDER_TYPE_PARAMS["money"], blank.codes if blank else set(),
            valid_amount=lambda v: isinstance(v, str) and is_valid_money_amount(v),
            amount_rule="a string in §10.3 format",
            # A code of the ISO 4217 form: one this implementation does not
            # know is only a Warning where it is inserted (§16.5).
            valid_code=lambda c: bool(_CURRENCY_RE.fullmatch(c)),
            code_rule="a three-letter ISO 4217 code",
        )
    if qtype == "duration":
        return _measure_problem(
            answer, "value", PLACEHOLDER_TYPE_PARAMS["duration"], blank.codes if blank else set(),
            valid_amount=lambda v: (
                isinstance(v, (int, str)) and not isinstance(v, bool) and is_positive_numeric(str(v))
            ),
            amount_rule=(
                "a positive integer, or a string holding a positive integer or decimal — "
                "not a YAML float, which can alter the digits"
            ),
            valid_code=lambda u: u in VALID_DURATION_UNITS,
            code_rule=f"one of {', '.join(DURATION_UNITS)}",
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
    codes: set[str],
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
        fixed = next((c for c in sorted(codes) if c and c != code), None)
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
        # A default left empty or written null is absent, like any value.
        if declaration.get("default") is not None:
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


# ── Drafting notes (§15.6) ────────────────────────────────────────

#: The first line of a quote that looks like a GitHub-flavoured alert.
_ALERT_RE = re.compile(r"\[![A-Za-z]+\]")
_DRAFTING_MARKER = "[!DRAFTING]"


def _first_line(block: Block) -> str:
    return block.text.split("\n", 1)[0].strip()


def is_drafting_note(block: Block) -> bool:
    """True if *block* is a drafting note: a block quote whose first line is
    exactly ``[!DRAFTING]``, letters in any case (§15.6)."""
    return block.kind == "quote" and _first_line(block).upper() == _DRAFTING_MARKER


def is_unrecognized_alert(block: Block) -> bool:
    """True if *block* is a block quote whose first line looks like an alert
    marker (``[!`` letters ``]``) but is not a drafting note's: an ordinary
    quote, whose guidance would reach the finished document (§15.6)."""
    return (
        block.kind == "quote"
        and _ALERT_RE.match(_first_line(block)) is not None
        and not is_drafting_note(block)
    )


# ── Insertion boundaries (§15.7.3) ────────────────────────────────

# What may directly precede and follow an insertion besides a letter, a
# digit, a space or tab, a line start or end, and another directive.
_BEFORE_INSERTION = frozenset("(\"'“‘„«/-")
_AFTER_INSERTION = frozenset(".,;:)!?\"'”’»/-")
# Characters that could combine with an insertion into a character
# reference, an autolink, or an escape.
_COMBINING = "&<\\"
# A line's container markers: block quote markers, then a list item marker.
_CONTAINER_RE = re.compile(r"(?:[ \t]*>[ \t]?)*(?:[ \t]*(?:[-*+]|[0-9]+[.)])[ \t]+)?")
_LINK_REFERENCE_RE = re.compile(r" {0,3}\[[^\]]+\]:")


def insertion_boundary_problem(
    text: str, view: str, insertion: Directive, directives: list[Directive]
) -> str | None:
    """Why *insertion* — a ``{{placeholder:}}`` or ``{{choose:}}`` in body
    *text* — is not kept apart from template text as §15.7.3 requires, or
    None. *view* is *text* as the lexer saw it, code and comments blanked;
    *directives* are all the directives lexed from *text*."""
    start, end = insertion.start, insertion.end
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    # The line's content begins after its container markers.
    content_start = min(_CONTAINER_RE.match(text, line_start).end(), start)
    # Template text: directives are not part of it.
    template = list(view)
    for directive in directives:
        template[directive.start:directive.end] = " " * (directive.end - directive.start)
    template_text = "".join(template)

    if start > content_start:
        before = text[start - 1]
        if not (
            before.isalnum()
            or before in " \t"
            or any(directive.end == start for directive in directives)
            or (before in _BEFORE_INSERTION and not (before == "(" and text[start - 2:start - 1] == "]"))
        ):
            return f"'{before}' directly before it"
        run_start = max(
            template_text.rfind(" ", content_start, start),
            template_text.rfind("\t", content_start, start),
            content_start - 1,
        ) + 1
        combining = next((ch for ch in template_text[run_start:start] if ch in _COMBINING), None)
        if combining:
            return f"'{combining}' in the text just before it"
    if end < line_end:
        after = text[end]
        if not (
            after.isalnum()
            or after in " \t"
            or text.startswith("{{", end)
            or after in _AFTER_INSERTION
        ):
            return f"'{after}' directly after it"
    if _LINK_REFERENCE_RE.match(template_text, content_start):
        return "it is on a link reference definition line"
    destination = template_text.rfind("](", line_start, start)
    if destination >= 0 and template_text.find(")", destination + 2, start) < 0:
        return "it is inside a link or image destination"
    return None


# ── Inline choices (§15.5) ────────────────────────────────────────

#: The brace-stray message, shared with the lexer's own strays.
BRACE_STRAY = "'{{' does not begin a directive and is literal text; write '\\{{' if that is intended (§11.4)."


def check_choose(directive: Directive, questions: Any, result: ValidationResult) -> None:
    """Report a ``{{choose:}}`` that does not list exactly the answers of a
    declared decision question (choose-invalid, §15.5), and any ``{{`` in
    its phrases, which is literal text (brace-stray)."""
    qid = directive.positional or ""
    qtype = question_type(questions, qid)
    if qtype not in DECISION_QUESTION_TYPES:
        result.error(
            "choose-invalid",
            f"'{directive.source}' must name a declared boolean or choice question (§15.5).",
        )
    elif qtype == "boolean" or isinstance(questions[qid].get("choices"), dict):
        # Malformed choices are question-invalid; there is nothing to match.
        answers = (
            ["true", "false"] if qtype == "boolean" else [str(value) for value in questions[qid]["choices"]]
        )
        missing = [answer for answer in answers if answer not in directive.params]
        extra = [param for param in directive.params if param not in answers]
        if missing:
            result.error(
                "choose-invalid",
                f"'{directive.source}' lists no phrase for {', '.join(missing)}: every answer "
                f"to '{qid}' needs one (§15.5).",
            )
        if extra:
            result.error(
                "choose-invalid",
                f"'{directive.source}' lists {', '.join(extra)}, which "
                f"{'is not an answer' if len(extra) == 1 else 'are not answers'} to '{qid}' (§15.5).",
            )
    for offset in range(2, len(directive.source) - 1):
        if directive.source.startswith("{{", offset) and not is_escaped(directive.source, offset):
            result.warning("brace-stray", BRACE_STRAY)


# ── Template constructs in the body (§15.3, §15.5–§15.7) ──────────

_INSERTIONS = ("placeholder", "choose")


def check_template_body(
    document: Document,
    lex_fragment: Callable[[str], Lexed],
    result: ValidationResult,
    *,
    template: bool,
) -> None:
    """Report what §16.12 checks in the body of a document: drafting notes
    (drafting-note-unrecognized, drafting-note-def), a blank or choice in a
    defined term (def-term-variable) or against template text
    (insertion-boundary), and, in a *template*, the Core parts of
    template-fragment-invalid."""
    # Imported here, as in core.py: definitions -> validator.helpers ->
    # validator/__init__ -> core -> templates would otherwise be a cycle.
    from ..definitions import find_definition_anchors, text_fragments

    includes: list[str] = []
    for _section, _index, block in document.iter_blocks():
        if is_unrecognized_alert(block):
            result.warning(
                "drafting-note-unrecognized",
                f"The quote begins '{_first_line(block)}', which looks like an alert marker but "
                f"is not [!DRAFTING]: it is an ordinary quote and would reach the finished "
                f"document (§15.6).",
            )
        note = is_drafting_note(block)
        for fragment in text_fragments(block):
            lexed = lex_fragment(fragment)
            for directive in lexed.directives:
                if directive.malformed:
                    continue
                if note and directive.name == "def":
                    result.error(
                        "drafting-note-def",
                        f"'{directive.source}' is inside a drafting note, which assembly removes "
                        f"with the definition (§15.6).",
                    )
                if directive.name == "include" and directive.positional:
                    if not note:
                        includes.append(posixpath.normpath(directive.positional))
                    elif template:
                        result.error(
                            "template-fragment-invalid",
                            f"'{directive.source}' is inside a drafting note; in a template an "
                            f"include belongs in the template's own body (§15.3).",
                        )
                if directive.name in _INSERTIONS:
                    problem = insertion_boundary_problem(
                        fragment, lexed.view, directive, lexed.directives
                    )
                    if problem:
                        result.error(
                            "insertion-boundary",
                            f"'{directive.source}' is not kept apart from the text around it: "
                            f"{problem} (§15.7.3).",
                        )
            for anchor in find_definition_anchors(
                fragment, language=document.metadata.language, lexed=lexed
            ):
                if anchor.term is None:
                    continue
                for directive in lexed.directives:
                    if directive.name in _INSERTIONS and anchor.start < directive.start < anchor.directive.start:
                        result.error(
                            "def-term-variable",
                            f"'{directive.source}' is inside the term that "
                            f"'{anchor.directive.source}' defines: the term, and any id derived "
                            f"from it, must be the same in every assembled document (§15.5).",
                        )
    if template:
        _check_fragments(document, includes, result)


def _check_fragments(document: Document, includes: list[str], result: ValidationResult) -> None:
    """The Core parts of template-fragment-invalid (§15.3): each fragment is
    included once, and each LegalDown attachment file is declared by one
    entry and is not also a fragment."""
    for path in sorted({path for path in includes if includes.count(path) > 1}):
        result.error(
            "template-fragment-invalid",
            f"Fragment '{path}' is included more than once; in a template each fragment has "
            f"one place (§15.3).",
        )
    files = [
        posixpath.normpath(att.file)
        for att in document.metadata.attachments
        if att.file.endswith(LEGALDOWN_EXTENSIONS)
    ]
    for path in sorted(set(files)):
        if files.count(path) > 1:
            result.error(
                "template-fragment-invalid",
                f"Attachment file '{path}' is declared by more than one attachment; in a "
                f"template each LegalDown attachment file has one entry (§15.3).",
            )
        if path in includes:
            result.error(
                "template-fragment-invalid",
                f"Attachment file '{path}' is also included as a fragment (§15.3).",
            )
