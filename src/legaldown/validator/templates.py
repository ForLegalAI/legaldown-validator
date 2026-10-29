"""Template questions (spec §15.2) and the answers they accept (§15.7.1).

A question's ``default`` must be an answer assembly would accept, so the
answer rules live here, next to the declarations they apply to.
"""
from __future__ import annotations

import posixpath
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from typing import Any

from ..directives import PLACEHOLDER_TYPE_PARAMS, Directive, Lexed, is_escaped, mask_directives
from ..markdown import DRAFTING_MARKER, DraftingNotes, find_all, is_drafting_marker, scan_destination
from ..models import Block, Document
from ..positions import Locator
from .helpers import is_positive_numeric, is_valid_iso_date, is_valid_money_amount
from .patterns import (
    DURATION_UNITS,
    IDENTIFIER_RE,
    LEGALDOWN_EXTENSIONS,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import Depends, Line, _Recorder

_CURRENCY_RE = re.compile(r"[A-Z]{3}")
_LINE_BREAK_RE = re.compile(r"[\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029]")

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
class _BlankState:
    """Every occurrence of one placeholder id — one logical blank (§10.7)."""
    #: The effective type of its first occurrence with a valid one.
    type: str | None = None
    #: What its occurrences of that type fix — the ``currency`` of a money
    #: blank, the ``unit`` of a duration blank — with ``""`` for any that
    #: fixes none. Two codes are an Error (placeholder-type-inconsistent).
    codes: set[str] = field(default_factory=set)
    in_frontmatter: bool = False
    #: Whether two occurrences have different types (placeholder-type-inconsistent).
    mixed: bool = False
    #: The line of the first occurrence fixing each code (§16.9).
    code_lines: dict[str, Any] = field(default_factory=dict)  # a ``result.Line`` each
    #: The line of the first occurrence (§16.9), a ``result.Line``. Occurrences in a
    #: drafting note, which assembly removes before anything is filled (§15.7.2), are
    #: not part of a blank (#112).
    line: Any = None


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


def answer_problem(qtype: str, answer: Any, *, choices: Any = None, blank: _BlankState | None = None) -> str | None:
    """Why *answer* is not a valid answer to a *qtype* question (§15.7.1,
    §15.7.2 step 1), or None when it is. *blank* describes the question's
    placeholders — the currency or unit they fix, and whether one is in
    frontmatter — and *choices* the declared choices of a ``choice``
    question."""
    if qtype == "text":
        if not isinstance(answer, str) or not answer:
            return "must be a non-empty string"
        if _LINE_BREAK_RE.search(answer):
            return "must not contain a line break"
        if answer != answer.strip(" \t"):
            return "must not begin or end with a space or tab"
        if blank is not None and blank.in_frontmatter and "{{" in answer:
            return "must not contain '{{' when it fills a placeholder in frontmatter"
        return None
    if qtype == "date":
        if (isinstance(answer, date) and not isinstance(answer, datetime)) or (
            isinstance(answer, str) and is_valid_iso_date(answer)
        ):
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
    blanks: dict[str, _BlankState],
    result: _Recorder,
    *,
    template: bool,
    not_line_editable: list[str],
    where: Locator | None = None,
) -> None:
    """Report malformed question declarations (question-invalid, §15.2).

    *blanks* are the document's placeholders by id: a default must agree with
    the currency or unit its question's placeholders fix, and in a *template*
    an undeclared placeholder id is a question id too. *where* gives each
    diagnostic's line: the question's, or the key's; for a blank whose id
    YAML reads as a boolean or null, the blank's own.
    """
    def line_of(*path: Any) -> int | None:
        return where.key(*path) if where is not None else None

    line = line_of("questions")  # of the declaration each diagnostic is about

    def invalid(message: str) -> None:
        result.error("question-invalid", message, line=line)

    if questions is not None and not isinstance(questions, dict):
        invalid("'questions' must be a map of question id to declaration (§15.2).")
    declared = questions if isinstance(questions, dict) else {}
    for qid, declaration in declared.items():
        line = line_of("questions", qid)
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
        line = line_of(key)
        if key == "questions" or template:
            invalid(
                f"'{key}' must be written in YAML block style, one key per line"
                + (", each entry beginning with '- id:'" if key == "attachments" else "")
                + ", so that assembly can edit it line by line (§15.2)."
            )
    if template:
        for pid, blank in blanks.items():
            if pid not in declared and pid in YAML_KEYWORDS:
                # It is the blank that cannot be answered, whatever `questions` holds.
                result.error(
                    "question-invalid",
                    f"Placeholder id '{pid}' is read by YAML as a boolean or null, so it "
                    f"cannot be answered portably in a template (§15.2).",
                    line=blank.line,
                )


# ── Drafting notes (§15.6) ────────────────────────────────────────

#: The first line of a quote that looks like a GitHub-flavoured alert.
_ALERT_RE = re.compile(r"\[![A-Za-z]+\]")


@dataclass(frozen=True, slots=True)
class Quote:
    """A block quote in a block: its first line without the ``>`` marker,
    and the indices of its fragments in ``block_fragments(block)`` — the
    blocks it holds, nested quotes' included."""
    first_line: str
    fragments: range

    @property
    def is_drafting_note(self) -> bool:
        """True if the quote is a drafting note: its first line is exactly
        ``[!DRAFTING]``, letters in any case (§15.6)."""
        return is_drafting_marker(self.first_line)

    @property
    def is_unrecognized_alert(self) -> bool:
        """True if the first line looks like an alert marker (``[!`` letters
        ``]``) but is not a drafting note's: an ordinary quote, whose
        guidance would reach the finished document (§15.6)."""
        return _ALERT_RE.match(self.first_line) is not None and not self.is_drafting_note


def is_drafting_note(quote: Block) -> bool:
    """True if block *quote* is a block quote that is a drafting note: its
    first line is exactly ``[!DRAFTING]``, letters in any case (§15.6)."""
    return quote.kind == "quote" and Quote(quote.text.split("\n", 1)[0].strip(), range(0)).is_drafting_note


def drafting_note_blocks(quote: Block, *, depth: int = 0) -> list[Block]:
    """The blocks of drafting note *quote* without its ``[!DRAFTING]`` marker
    line (§15.6), as ``quote_blocks`` reads the note's content: fresh blocks.
    The marker starts the first paragraph or heading, and is cut from it,
    the block going when nothing else is in it; where it does not (a marker
    line indented as code), the note is what is written after its first
    line. *depth*: as in ``quote_blocks``; past it the note is one paragraph
    of its text after the first line, as written. Raises ``ValueError`` for a
    block that is not a drafting note (``is_drafting_note``)."""
    from ..parser import MAX_QUOTE_DEPTH, quote_blocks  # see the import note in check_template_body

    if not is_drafting_note(quote):
        raise ValueError("not a drafting note")
    if depth >= MAX_QUOTE_DEPTH:
        text = quote.text.partition("\n")[2]
        return [Block(kind="paragraph", text=text)] if text.strip() else []
    blocks = quote_blocks(quote, depth=depth)
    first = blocks[0] if blocks else None
    if first is None or first.kind not in ("paragraph", "heading") or not first.text.upper().startswith(DRAFTING_MARKER):
        return quote_blocks(Block(kind="quote", text=quote.text.partition("\n")[2]), depth=depth)
    rest = first.text[len(DRAFTING_MARKER):].lstrip()
    if not rest:
        return blocks[1:]
    first.text = rest
    return blocks


def block_quotes(block: Block) -> list[Quote]:
    """The block quotes in *block*, nested ones included: a quote block and
    the quotes in it, or those in a list's items."""
    from ..definitions import quote_ranges  # see the import note in check_template_body

    return [Quote(first_line, fragments) for first_line, fragments in quote_ranges(block)]


# ── Insertion boundaries (§15.7.3) ────────────────────────────────

# What may directly precede and follow an insertion besides a letter, a
# digit, a space or tab, a line start or end, and another directive.
_BEFORE_INSERTION = frozenset("(\"'“‘„«/-")
_AFTER_INSERTION = frozenset(".,;:)!?\"'”’»/-")
# Characters that could combine with an insertion into a character
# reference, an autolink, or an escape.
_COMBINING = "&<\\"
# A line's container markers: block quote markers, then a list item marker.
_CONTAINER_RE = re.compile(r"(?:[ \t]*>[ \t]?)*(?:[ \t]*(?:[-*+]|[0-9]{1,9}[.)])[ \t]+)?")
_NON_BLANK_RE = re.compile(r"\S")
# A link reference definition's destination, after its ``]:`` (``_InsertionScan._reference_end``), and
# the blanks that set a title off from it.
_REFERENCE_DESTINATION_RE = re.compile(r"[ \t]*(\S*)")
_REFERENCE_BLANKS_RE = re.compile(r"[ \t]+")
# The blanks that follow an insertion in the title's place.
_BLANKS_RE = re.compile(r"[ \t]*")
# What closes a title by what opens it.
_TITLE_CLOSERS = {'"': '"', "'": "'", "(": ")"}


def template_text(text: str, directives: list[Directive]) -> str:
    """The template text of *text*: the source as written — comments and
    code spans included, which stay next to inserted text in the assembled
    source — with its *directives* made opaque."""
    return mask_directives(text, directives)


class _InsertionScan:
    """The text of one fragment and what its insertions' boundaries (§15.7.3)
    are judged by. A fragment can hold thousands of insertions on one line, so
    the places that a check looks back or ahead for are indexed once, and
    found by bisect: a scan of the line for each insertion would be
    quadratic (#94).

    *directives* are all the directives lexed from *text*, and *template* is
    ``template_text(text, directives)``. *definitions* is whether *text* can hold a link
    reference definition: an ATX heading's or a table cell's cannot (a setext heading's can,
    for CommonMark takes definitions out of a paragraph before it reads an underline), and a
    definition there is read as it was before #119 (a bare destination, a closed title, no
    title slot). The rule is per line (§15.7.3): a line that continues a paragraph, which is no
    definition, is read as any other."""

    def __init__(self, text: str, template: str, directives: list[Directive], definitions: bool = True) -> None:
        self.text = text
        self.template = template
        self._definitions = definitions
        self._newlines = find_all(text, "\n")
        self._directive_ends = {directive.end for directive in directives}
        # Where an insertion begins and ends: only an insertion, filled in, could be a title.
        self._insertions = {
            directive.start: directive.end
            for directive in directives
            if directive.name in _INSERTIONS and not directive.malformed
        }
        self._blanks = _merged(template, " ", "\t")
        self._combining = _merged(template, *_COMBINING)
        self._destination_starts = find_all(template, "](")  # it cannot overlap itself
        self._closing_parentheses = find_all(template, ")")
        # Where a link reference definition's label and title end, found when a line asks for the first time.
        self._found: dict[str, list[int]] = {")": self._closing_parentheses}
        # Per line, which the insertions of a line share: where its content
        # begins, what its link reference definition covers, and whether
        # what follows that on the line is blank.
        self._content_starts: dict[int, int] = {}
        self._references: dict[int, int] = {}
        self._blank_after: dict[tuple[int, int], bool] = {}

    def _line_content(self, line_start: int) -> int:
        """Where the content of the line at *line_start* begins: after its
        container markers."""
        if line_start not in self._content_starts:
            self._content_starts[line_start] = _CONTAINER_RE.match(self.text, line_start).end()
        return self._content_starts[line_start]

    def _closers(self, closer: str) -> list[int]:
        """The offsets of *closer* in the template, found once."""
        if closer not in self._found:
            self._found[closer] = find_all(self.template, closer)
        return self._found[closer]

    def _reference_end(self, content_start: int) -> int:
        """Where the link reference definition at *content_start* ends, or -1. It is the text
        `` {0,3}\\[[^\\]]+\\]:[ \\t]*``, a destination and an optional title. The destination is the
        longer of two readings: the bare ``\\S*`` and, if it begins with ``<``, the ``<…>`` that
        ``markdown.scan_destination`` reads (it may hold blanks, §6.3). After it comes a closed
        title, which the blanks before it set off and the next ``"``, ``'`` or ``)`` closes, on a
        later line too; or, where an answer could be a title, the blanks, the insertions
        (``{{placeholder:}}``, ``{{choose:}}``; no other directive) and the blanks between
        and after them. The last two only for text that can hold a definition (*definitions*);
        else the definition is as it was before #119. (A pinned over-approximation, not
        CommonMark's §4.7; it was a regular expression, which an unclosed label or title ran to
        the end of the text for at each line, #113. Each closer is found by bisect now.)"""
        if content_start not in self._references:
            self._references[content_start] = self._read_reference(content_start)
        return self._references[content_start]

    def _read_reference(self, at: int) -> int:
        template = self.template
        bracket = at
        while bracket < at + 4 and template.startswith(" ", bracket):
            bracket += 1
        if bracket - at > 3 or not template.startswith("[", bracket):
            return -1
        # The label ends at the first ``]`` after the ``[``, and is not empty: ``[]x]:`` is none.
        brackets = self._closers("]")
        found = bisect_left(brackets, bracket + 1)
        if found == len(brackets) or brackets[found] < bracket + 2 or not template.startswith(":", brackets[found] + 1):
            return -1
        destination = _REFERENCE_DESTINATION_RE.match(template, brackets[found] + 2)
        end = self._after_title(destination.end())
        # A ``<…>`` destination may hold blanks (§6.3). (The longer reading wins: no verdict that the definition
        # drew before is lost.)
        if (
            self._definitions
            and template.startswith("<", destination.start(1))
            and (angle := scan_destination(template, destination.start(1)))
        ):
            end = max(end, self._after_title(angle[2]))
        return end

    def _after_title(self, end: int) -> int:
        """Where the definition whose destination ends at *end* ends: past its title, which the blanks before it
        set off and the next ``"``, ``'`` or ``)`` closes, on a later line too; past the insertions and blanks
        where an answer could be a title; else *end*."""
        template = self.template
        blanks = _REFERENCE_BLANKS_RE.match(template, end)
        if blanks and (delimiter := _TITLE_CLOSERS.get(template[blanks.end():blanks.end() + 1])):
            closers = self._closers(delimiter)
            found = bisect_left(closers, blanks.end() + 1)
            if found < len(closers):
                return closers[found] + 1
        elif self._definitions and blanks and blanks.end() in self._insertions:
            at = blanks.end()
            while at in self._insertions:
                at = _BLANKS_RE.match(template, self._insertions[at]).end()
            return at
        return end

    def _blank_from(self, reference_end: int, line_end: int) -> bool:
        """Whether the template is blank from *reference_end* to *line_end*, found once for the
        insertions of a line (a blank run after the definition is not read again for each)."""
        key = (reference_end, line_end)
        if key not in self._blank_after:
            self._blank_after[key] = _NON_BLANK_RE.search(self.template, reference_end, line_end) is None
        return self._blank_after[key]

    def problem(self, insertion: Directive) -> str | None:
        """Why *insertion* — a ``{{placeholder:}}`` or ``{{choose:}}`` in the
        text — is not kept apart from template text as §15.7.3 requires, or
        None."""
        text, template = self.text, self.template
        start, end = insertion.start, insertion.end
        before_start = bisect_left(self._newlines, start)  # the newlines before it
        line_start = self._newlines[before_start - 1] + 1 if before_start else 0
        after_end = bisect_left(self._newlines, end)  # the first newline from its end on
        line_end = self._newlines[after_end] if after_end < len(self._newlines) else len(text)
        # The line's content begins after its container markers.
        content_start = min(self._line_content(line_start), start)

        if start > content_start:
            before = text[start - 1]
            if not (
                before.isalnum()
                or before in " \t"
                or start in self._directive_ends
                or (before in _BEFORE_INSERTION and not (before == "(" and text[start - 2:start - 1] == "]"))
            ):
                return f"'{before}' directly before it"
            # The run of text just before it begins after the last space or tab.
            last_blank = bisect_left(self._blanks, start) - 1
            run_start = content_start
            if last_blank >= 0 and self._blanks[last_blank] >= content_start:
                run_start = self._blanks[last_blank] + 1
            first_combining = bisect_left(self._combining, run_start)
            if first_combining < len(self._combining) and self._combining[first_combining] < start:
                return f"'{template[self._combining[first_combining]]}' in the text just before it"
        if end < line_end:
            after = text[end]
            if not (
                after.isalnum()
                or after in " \t"
                or text.startswith("{{", end)
                or after in _AFTER_INSERTION
            ):
                return f"'{after}' directly after it"
        reference_end = self._reference_end(content_start)
        # A definition is the whole line; text after it on the line makes the
        # line a paragraph's. (One a later line follows is still a definition:
        # a paragraph keeps its lines.)
        if (
            reference_end >= 0
            and start < reference_end
            and self._blank_from(reference_end, line_end)
        ):
            return "it is in a link reference definition"
        # The last ``](`` before it, with no ``)`` between.
        last_destination = bisect_right(self._destination_starts, start - 2) - 1
        if last_destination >= 0 and self._destination_starts[last_destination] >= line_start:
            destination = self._destination_starts[last_destination]
            closing = bisect_left(self._closing_parentheses, destination + 2)
            if closing == len(self._closing_parentheses) or self._closing_parentheses[closing] >= start:
                return "it is inside a link or image destination"
        return None


def _merged(text: str, *needles: str) -> list[int]:
    """The offsets in *text* of each of *needles*, in order."""
    return sorted(at for needle in needles for at in find_all(text, needle))


# ── Inline choices (§15.5) ────────────────────────────────────────

#: The brace-stray message, shared with the lexer's own strays.
BRACE_STRAY = "'{{' does not begin a directive and is literal text; write '\\{{' if that is intended (§11.4)."


def _choose_issues(directive: Directive, questions: Any) -> list[tuple[Depends | None, str]]:
    """Every way a ``{{choose:}}`` fails to list exactly the answers of a
    declared decision question (choose-invalid, §15.5), as ``(depends,
    message)``. *depends* is ``Depends.FRONTMATTER`` for a question that is not
    declared here, which a fragment's host may declare (#39); the phrases are
    only compared with a declared question's answers, so a problem with them is
    the directive's own."""
    problems: list[tuple[Depends | None, str]] = []
    qid = directive.positional or ""
    qtype = question_type(questions, qid)
    if qtype not in DECISION_QUESTION_TYPES:
        # Declared as a value question it is wrong wherever it is read.
        depends = Depends.FRONTMATTER if qtype is None else None
        problems.append((depends, f"'{directive.source}' must name a declared boolean or choice question (§15.5)."))
    elif qtype == "boolean" or _choices_problem(questions[qid].get("choices")) is None:
        # Malformed choices are question-invalid; there is nothing to match.
        answers = (
            ["true", "false"] if qtype == "boolean" else [str(value) for value in questions[qid]["choices"]]
        )
        missing = [answer for answer in answers if answer not in directive.params]
        extra = [param for param in directive.params if param not in answers]
        repeated = [param for param in dict.fromkeys(directive.duplicates) if param in answers]
        if missing:
            problems.append((
                None,
                f"'{directive.source}' lists no phrase for {', '.join(missing)}: every answer "
                f"to '{qid}' needs one (§15.5).",
            ))
        if extra:
            problems.append((
                None,
                f"'{directive.source}' lists {', '.join(extra)}, which "
                f"{'is not an answer' if len(extra) == 1 else 'are not answers'} to '{qid}' (§15.5).",
            ))
        if repeated:
            problems.append((
                None,
                f"'{directive.source}' lists {', '.join(repeated)} more than once; each answer "
                f"has one phrase (§15.5).",
            ))
    return problems


def choose_problem(directive: Directive, questions: Any) -> str | None:
    """Why the ``{{choose:}}`` *directive* is not valid for *questions* (the
    document's ``metadata.questions``): the first choose-invalid message
    (§15.5) the validator reports for it, or None when it names a declared
    boolean or choice question and lists exactly its answers. Raises
    ``ValueError`` for a directive that is not a ``{{choose:}}`` or is
    malformed: the validator reports a malformed one as directive-malformed,
    never as choose-invalid."""
    if directive.name != "choose" or directive.malformed:
        raise ValueError(f"not a well-formed {{{{choose:}}}}: {directive.source!r}")
    issues = _choose_issues(directive, questions)
    return issues[0][1] if issues else None


def check_choose(
    directive: Directive, questions: Any, result: _Recorder, *, host_declares: bool = True
) -> None:
    """Report a ``{{choose:}}`` that does not list exactly the answers of a
    declared decision question (choose-invalid, §15.5), and any ``{{`` in
    its phrases, which is literal text (brace-stray). *host_declares*: False
    when no other document declares questions for it, so naming one that is not
    declared is its own fault."""
    for depends, problem in _choose_issues(directive, questions):
        result.error("choose-invalid", problem, depends=depends if host_declares else None)
    for offset in range(2, len(directive.source) - 1):
        if directive.source.startswith("{{", offset) and not is_escaped(directive.source, offset):
            result.warning("brace-stray", BRACE_STRAY)


# ── Template constructs in the body (§15.3, §15.5–§15.7) ──────────

_INSERTIONS = ("placeholder", "choose")


@dataclass(frozen=True, slots=True)
class IncludeDirective:
    """An ``{{include:}}`` in a document's body (§12.2), wherever it is."""
    #: The path as the document writes it, and normalised (``posixpath.normpath``):
    #: the key assembly and the validator read a fragment by. Both "" for an include that
    #: names no file.
    written: str
    path: str
    #: The directive as written (``{{include: part.lgd}}``).
    source: str
    line: Line
    #: Where it is: the section (None in the preamble), the block, the fragment of
    #: the block's text and the offset in it, as ``FileReferences.check`` places it.
    section: int | None
    block: int
    fragment: int
    offset: int
    #: Whether it is in a drafting note, which assembly removes unread (§15.6).
    in_note: bool
    #: Whether its paragraph holds only it and at most its marker (§12.2): the one
    #: kind of include a fragment replaces, and so the only one that is read. Any
    #: other is text that the include rules do not look at.
    paragraph: bool

    @property
    def at(self) -> tuple[int, int, int, int]:
        """Its place as ``FileReferences.check`` was given it."""
        return (-1 if self.section is None else self.section, self.block, self.fragment, self.offset)


@dataclass(frozen=True, slots=True)
class BodyScan:
    """What ``check_template_body`` met in a document's body besides what it
    reported: its drafting notes, each with its line, and every
    ``{{include:}}`` (``IncludeDirective``), in document order."""
    notes: list[tuple[Quote, Line]]
    includes: list[IncludeDirective]


def check_template_body(
    document: Document,
    lex_fragment: Callable[[str], Lexed],
    result: _Recorder,
    *,
    template: bool,
    where: Locator | None = None,
) -> BodyScan:
    """Report what §16.12 checks in the body of a document: drafting notes
    (drafting-note-unrecognized, drafting-note-def), a blank or choice in a
    defined term (def-term-variable) or against template text
    (insertion-boundary), and, in a *template*, the Core parts of
    template-fragment-invalid. Return what it met (``BodyScan``): the
    document's drafting notes, each with its line, and its includes. *where*
    gives the lines."""
    # Imported here, as in core.py: definitions -> validator.helpers ->
    # validator/__init__ -> core -> templates would otherwise be a cycle.
    from ..definitions import find_definition_anchors, fragment_kinds, quote_ranges
    from .units import include_key, paragraph_include  # conditions -> templates -> units -> conditions

    def find(section: int | None, block: int, needle: str, fragment: int | None = None, offset: int = 0,
             nth: int = 0) -> Line:
        """The line of *needle* (``Locator.find``), found only when a
        diagnostic is made there."""
        if where is None:
            return None
        return partial(where.find, section, block, needle, fragment, offset, nth=nth)

    for heading, section in enumerate(document.sections):  # headings are body text too
        lexed = lex_fragment(section.title)
        scan: _InsertionScan | None = None  # the title's, for its first insertion
        for directive in lexed.directives:
            if directive.name in _INSERTIONS and not directive.malformed:
                scan = scan or _InsertionScan(
                    section.title,
                    template_text(section.title, lexed.directives),
                    lexed.directives,
                    definitions=where is not None and where.setext_section(heading),
                )
                line = where.heading(heading) if where is not None else None
                _check_insertion(scan, directive, result, line)
    includes: list[IncludeDirective] = []
    all_notes: list[tuple[Quote, Line]] = []
    for block_section, block_index, block in document.iter_indexed_blocks():
        ranges = quote_ranges(block)
        firsts: list[str] = []  # the first lines of the quotes before
        for first_line, fragments in ranges:
            quote = Quote(first_line, fragments)
            first = quote.first_line.strip()
            line = find(block_section, block_index, first, nth=firsts.count(first))
            firsts.append(first)
            if quote.is_unrecognized_alert:
                result.warning(
                    "drafting-note-unrecognized",
                    f"The quote begins '{quote.first_line}', which looks like an alert marker "
                    f"but is not [!DRAFTING]: it is an ordinary quote and would reach the "
                    f"finished document (§15.6).",
                    line=line,
                )
            elif quote.is_drafting_note:
                all_notes.append((quote, line))
        notes = DraftingNotes(ranges)
        for index, (fragment, kind) in enumerate(fragment_kinds(block)):
            lexed = lex_fragment(fragment)
            scan = None  # the fragment's, for its first insertion
            in_note = index in notes
            # The include of a paragraph that holds only it (§12.2): only a top-level
            # paragraph's first fragment is one, an include in a quote, a list or a table is text.
            only = paragraph_include(block, fragment, lexed) if index == 0 else None
            for directive in lexed.directives:
                if directive.malformed:
                    continue
                line = find(block_section, block_index, directive.source, index, directive.start)
                if in_note and directive.name == "def":
                    result.error(
                        "drafting-note-def",
                        f"'{directive.source}' is inside a drafting note, which assembly removes "
                        f"with the definition (§15.6).",
                        line=line,
                    )
                # One that names no file (``{{include:}}``) is recorded too, with the path "": an
                # include paragraph of it is refused as missing, and in a template it is a nested
                # include as any other, wherever it is (§15.3).
                if directive.name == "include":
                    includes.append(IncludeDirective(
                        directive.positional or "", include_key(directive), directive.source, line, block_section, block_index, index, directive.start, in_note,
                        only is directive,
                    ))
                    if in_note and template:
                        result.error(
                            "template-fragment-invalid",
                            f"'{directive.source}' is inside a drafting note; in a template an "
                            f"include belongs in the template's own body (§15.3).",
                            line=line,
                        )
                # A drafting note is removed before anything is inserted
                # (§15.7.2 step 2), so a blank in one is never filled.
                if directive.name in _INSERTIONS and not in_note:
                    scan = scan or _InsertionScan(
                        fragment,
                        template_text(fragment, lexed.directives),
                        lexed.directives,
                        definitions=_holds_definitions(kind, where, block_section, block_index, index),
                    )
                    _check_insertion(scan, directive, result, line)
            # The insertions inside a term are those between its anchor and its directive. The
            # lexer emits directives in source order, so they are a slice of all, found by
            # bisection: scanning every directive for each definition is quadratic (#110).
            insertions = [d for d in lexed.directives if d.name in _INSERTIONS]
            starts = [d.start for d in insertions]
            for anchor in (
                find_definition_anchors(fragment, language=document.metadata.language, lexed=lexed)
                if insertions
                else ()
            ):
                if anchor.term is None:
                    continue
                for directive in insertions[
                    bisect_right(starts, anchor.start) : bisect_left(starts, anchor.directive.start)
                ]:
                    result.error(
                        "def-term-variable",
                        f"'{directive.source}' is inside the term that "
                        f"'{anchor.directive.source}' defines: the term, and any id derived "
                        f"from it, must be the same in every assembled document (§15.5).",
                        line=find(block_section, block_index, directive.source, index, directive.start),
                    )
    if template:
        _check_fragments(document, [(i.path, i.line) for i in includes if i.path and not i.in_note], result, where)
    return BodyScan(all_notes, includes)


def _holds_definitions(kind: str, where: Locator | None, section: int | None, block: int, fragment: int) -> bool:
    """Whether the text of a fragment, in a block of *kind*, can hold a link reference definition: not a
    table cell, nor an ATX heading's text (a setext heading's can: the definition is taken out of the
    paragraph before the underline is read, §4.7). A heading written in code is an ATX one."""
    if kind == "table":
        return False
    if kind == "heading":
        return where is not None and where.setext_fragment(section, block, fragment)
    return True


def _check_insertion(scan: _InsertionScan, insertion: Directive, result: _Recorder, line: Line = None) -> None:
    """Report *insertion*, at *line*, if it is not kept apart from template text."""
    problem = scan.problem(insertion)
    if problem:
        result.error(
            "insertion-boundary",
            f"'{insertion.source}' is not kept apart from the text around it: {problem} (§15.7.3).",
            line=line,
        )


def _check_fragments(
    document: Document, includes: list[tuple[str, Line]], result: _Recorder, where: Locator | None
) -> None:
    """The Core parts of template-fragment-invalid (§15.3): each fragment is
    included once, and each LegalDown attachment file is declared by one
    entry and is not also a fragment. *includes*: each fragment's path and
    the line of its {{include:}}; a second one is reported at its own line,
    an attachment file at its later entry."""
    included = Counter(path for path, _line in includes)
    seen: set[str] = set()
    for path, line in includes:
        if path in seen and included[path] > 1:
            included[path] = 0  # reported once
            result.error(
                "template-fragment-invalid",
                f"Fragment '{path}' is included more than once; in a template each fragment has "
                f"one place (§15.3).",
                line=line,
            )
        seen.add(path)
    files = [
        (posixpath.normpath(att.file), index)
        for index, att in enumerate(document.metadata.attachments)
        if att.file.endswith(LEGALDOWN_EXTENSIONS)
    ]
    entries: dict[str, list[int]] = {}
    for path, index in files:
        entries.setdefault(path, []).append(index)
    fragments = {path for path, _line in includes}
    for path in sorted(entries):
        indices = entries[path]
        if len(indices) > 1:
            result.error(
                "template-fragment-invalid",
                f"Attachment file '{path}' is declared by more than one attachment; in a "
                f"template each LegalDown attachment file has one entry (§15.3).",
                line=where.key("attachments", indices[1], "file") if where is not None else None,
            )
        if path in fragments:
            result.error(
                "template-fragment-invalid",
                f"Attachment file '{path}' is also included as a fragment (§15.3).",
                line=where.key("attachments", indices[0], "file") if where is not None else None,
            )
