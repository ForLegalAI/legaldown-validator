"""Core validation logic for LegalDown documents.

Every diagnostic is recorded under the **stable rule id** the specification
assigns to its check (§16.1) through the recorder's ``error/warning/info``,
so tooling can filter, suppress, or escalate specific rules consistently
across implementations (§16.9).
"""
from __future__ import annotations

import os
import re
import warnings
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache, partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NamedTuple

from .._deprecation import LegaldownDeprecationWarning
from ..directives import (
    DIRECTIVE_PARAMS,
    KNOWN_DIRECTIVES,
    PLACEHOLDER_TYPE_PARAMS,
    Directive,
    Lexed,
    is_escaped,
    iter_directives,
    lex,
    mask_directives,
)
from ..files import LoadFile, file_loader
from ..markdown import (
    INLINE_HTML_RE,
    DraftingNotes,
    back_line_space,
    inline_html_unclosed,
    is_comment_only,
    scan_inline_link,
    strip_comments,
)
from ..models import LIST_KINDS, Amends, Document, Metadata, frontmatter_message
from ..positions import Locator
from ..specification import SPEC_VERSION, parse_version
from .combined import Combined, CombinedLocator, CombinedUnits, merge_diagnostics
from .conditions import (
    ALWAYS,
    Exclusivity,
    Presence,
    always_covered,
    condition_issue,
    exclusive,
    parse_condition,
    satisfiable,
)
from .fragments import (
    Files,
    FragmentKind,
    IncludeOutcome,
    UnreadReason,
    check_includes,
    content_findings,
    shape_findings,
)
from .helpers import (
    authoritative_mismatch,
    format_section_number,
    generate_identifier,
    identifier_rule,
    is_positive_numeric,
    is_valid_iso_date,
    is_valid_money_amount,
    slugify_identifier,
)
from .paths import FileReferences, ReferenceImages, check_fragment_images, check_frontmatter_paths, check_heading_images
from .patterns import (
    _CONSTRUCT_PRESENT,
    _UNFILLED,
    DURATION_UNITS,
    FRONTMATTER_KEY_DUPLICATE_RULE,
    IDENTIFIER_RE,
    ISO_639_1_CODES,
    KNOWN_CURRENCIES,
    LEGALDOWN_EXTENSIONS,
    MAX_SECTION_LEVEL,
    PARTY_TYPES,
    RESERVED_VALUE_TYPES,
    SKIPPED_RULE,
    VALID_DOC_TYPES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import (
    Blank,
    Depends,
    Diagnostic,
    Line,
    PlacedMarker,
    SectionIndexEntry,
    Spot,
    ValidationResult,
    _Recorder,
)
from .skipped import NOT_IMPORTED, skipped_categories
from .templates import (
    BRACE_STRAY,
    DECISION_QUESTION_TYPES,
    Quote,
    _BlankState,
    _fixed_by_all,
    check_choose,
    check_questions,
    check_template_body,
    question_type,
)
from .units import FoundMarker, Units, find_markers, marker_matches, own_presence

if TYPE_CHECKING:
    from ..definitions import DefinitionRef

# Type aliases for the definitions-import callbacks: deprecated since 0.4.0,
# removed in 0.5.0, with the callbacks (``validate(resolve=)`` replaces them).
DefinitionsImporter = Callable[[str, str], dict[str, str] | None]
AttachmentDefinitionsImporter = Callable[[str], dict[str, str] | None]


def _strings(value: Any) -> Iterator[str]:
    """Every string in a YAML value, mapping keys included. A container an
    alias repeats, or one that holds itself, is visited once."""
    seen: set[int] = set()
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            yield current
        elif isinstance(current, (dict, list)) and id(current) not in seen:
            seen.add(id(current))
            items = current.items() if isinstance(current, dict) else ((item,) for item in current)
            for entry in items:
                stack.extend(entry)


def _placeholder_type(directive: Directive, declared: str | None) -> str:
    """A placeholder's effective type (§10.7, §15.2): *declared*, the type of
    the question declared with its id, when that is a value question; else
    its ``type`` parameter, else text."""
    if declared in VALID_PLACEHOLDER_TYPES:
        return declared
    return directive.params.get("type", "text")


def _is_date_placeholder(value: str, directives: list[Directive], questions: Any) -> bool:
    """True if a frontmatter date field holds a placeholder that is its whole
    value and has effective type ``date`` — the one case §3.10 exempts from
    the field's date check (§16.6). *directives* are those of *value*."""
    if len(directives) != 1:
        return False
    directive = directives[0]
    declared = question_type(questions, directive.positional or "")
    return (
        directive.name == "placeholder"
        and not directive.malformed
        and (directive.start, directive.end) == (0, len(value))
        and _placeholder_type(directive, declared) == "date"
    )


_SHOWN_WIDTH = 80  # the most of a key that a message writes out


def _shown(key: str) -> str:
    """A key as it is written in the source, for a message: its line breaks (and the indentation after them)
    folded to a space, cut short (``…``) after ``_SHOWN_WIDTH`` characters (a key may be as long as the frontmatter,
    and a message names it for each entry it replaces), between backticks (a longer fence if it holds backticks,
    padded, as CommonMark does)."""
    text = re.sub(r"\s*\n\s*", " ", key[: 10 * _SHOWN_WIDTH]).strip()
    if len(text) > _SHOWN_WIDTH or len(key) > 10 * _SHOWN_WIDTH:
        text = text[:_SHOWN_WIDTH] + "…"
    text = text or "''"  # a key written as nothing
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _key_clause(why: str, by_written: str, again: str = "") -> str:
    """What a frontmatter key that the model drops is (``ldv-frontmatter-key-duplicate``): written again
    (*again*, " at line N", when there is a line to name), or the same key as *by_written* once read as
    text, stripped, or as a YAML value."""
    return {
        "repeated": f"is written again{again}",
        "text": f"is the same key as {_shown(by_written)} once read as text",
        "stripped": f"is the same key as {_shown(by_written)} once stripped",
        "value": f"is the same key as {_shown(by_written)} once read as a YAML value (Python treats them as equal)",
    }[why]


def _key_ignored(subject: str, written: str, clause: str, stays: str = "") -> str:
    """The message of ``ldv-frontmatter-key-duplicate``: the key *written* of *subject* (a map or a mapping) is
    *clause* (``_key_clause``), and ignored, with *stays* saying which entry the model keeps."""
    return f"The {subject} key {_shown(written)} {clause}: this entry is ignored{stays}."


def _key_read(line: int) -> str:
    """What follows a key that the model drops for another: the entry at *line* is the one it reads."""
    return f", and the entry at line {line} is the one read"


def _frontmatter_fields(
    meta: Any, where: Locator | None = None
) -> tuple[list[tuple[str, str, tuple[Any, ...]]], list[tuple[str, tuple[Any, ...]]]]:
    """The frontmatter fields that may hold text: ``(label, value, path)`` of
    each identifier, structural, or format-checked field, where a
    placeholder is not allowed, and ``(value, path)`` of the value fields,
    where it is (§3.10). A path is the field's place in the frontmatter
    (``positions.SourceMap.key``), as *where* tells it when a custom field
    may be written in either form."""
    structural: list[tuple[str, str, tuple[Any, ...]]] = [
        ("document_type", meta.document_type, ("document_type",)),
        ("legaldown", meta.legaldown, ("legaldown",)),
        ("language", meta.language, ("language",)),
        ("authoritative", meta.authoritative, ("authoritative",)),
    ]
    for i, side in enumerate(meta.sides):
        structural.append((f"side name '{side.name}'", side.name, ("sides", i, "name")))
        for j, party in enumerate(side.parties):
            party_path = ("sides", i, "parties", j)
            structural.append((f"party name '{party.name}'", party.name, (*party_path, "name")))
            structural.append((f"party type for '{party.name}'", party.type, (*party_path, "type")))
    for code, path in meta.translations.items():
        structural.append(("a translations language code", code, ("translations", code)))
        structural.append((f"translations file for '{code}'", path, ("translations", code)))
    for key, description in meta.field_types.items():
        structural.append(("a field_types key", key, ("field_types", key)))
        structural.append((f"field_types entry '{key}'", description, ("field_types", key)))
    for i, att in enumerate(meta.attachments):
        structural.append(("attachment id", att.id, ("attachments", i, "id")))
        structural.append((f"file of attachment '{att.id}'", att.file, ("attachments", i, "file")))
        structural.append((f"when of attachment '{att.id}'", att.when, ("attachments", i, "when")))
    if meta.amends:
        structural.append(("amends.file", meta.amends.file, ("amends", "file")))
    if isinstance(meta.supersedes, Amends):
        structural.append(("supersedes.file", meta.supersedes.file, ("supersedes", "file")))
    structural.extend(("questions", text, ("questions",)) for text in _strings(meta.questions))

    values: list[tuple[str, tuple[Any, ...]]] = [
        (meta.title, ("title",)), (meta.subtitle, ("subtitle",)), (meta.version, ("version",)),
        (meta.effective_date, ("effective_date",)), (meta.governing_law, ("governing_law",)),
        (meta.adopted_by, ("adopted_by",)), (meta.adoption_date, ("adoption_date",)),
    ]
    if meta.amends:
        values.append((meta.amends.title, ("amends", "title")))
    if isinstance(meta.supersedes, Amends):
        values.append((meta.supersedes.title, ("supersedes", "title")))
    else:
        values.append((meta.supersedes, ("supersedes",)))
    values.extend((att.title, ("attachments", i, "title")) for i, att in enumerate(meta.attachments))
    for i, side in enumerate(meta.sides):
        values.append((side.label, ("sides", i, "label")))
        for j, party in enumerate(side.parties):
            party_path = ("sides", i, "parties", j)
            values.extend((getattr(party, name), (*party_path, name)) for name in (
                "label", "legal_name", "identification_number", "address", "date_of_birth",
            ))
            for k, rep in enumerate(party.representatives):
                values.append((rep.name, (*party_path, "representatives", k, "name")))
                values.append((rep.title, (*party_path, "representatives", k, "title")))
            # A custom field is a key of the party's own (§3.4), or an entry
            # of its `custom_fields` list. Its label, a key, holds no
            # placeholder; its value is a value field.
            # A key's label is read stripped (``models.party_from_dict``), the
            # key is written as it is: a label's nth field is the nth key
            # that strips to it, in the order of the source — when each has
            # one, and none may be merged in (``<<``), which the model reads
            # before the party's own: else it is looked for by its label.
            written: dict[str, list[str]] = {}
            for raw in where.children(*party_path) if where is not None else ():
                written.setdefault(raw.strip(), []).append(raw)
            listed_paths = [(*party_path, "custom_fields", k) for k in range(len(party.custom_fields))]
            keyed_fields = [
                cf.label for cf, listed in zip(party.custom_fields, listed_paths, strict=True)
                if where is None or not where.has(*listed)
            ]
            merges = where is not None and where.merges(*party_path)
            for label in set(keyed_fields):
                count = keyed_fields.count(label)
                if len(written.get(label, ())) != count or (merges and count > 1):
                    written.pop(label, None)
            for cf, listed in zip(party.custom_fields, listed_paths, strict=True):
                keyed = where is None or not where.has(*listed)
                key = written[cf.label].pop(0) if keyed and written.get(cf.label) else cf.label
                structural.append((
                    "a party's custom field name", cf.label, (*party_path, key) if keyed else (*listed, "label")
                ))
                values.append((cf.value, (*party_path, key) if keyed else (*listed, "value")))
    return structural, values


def _check_date_field(
    label: str, value: str, questions: Any, rule: str, result: _Recorder
) -> None:
    """Report a frontmatter date field that is neither an ISO 8601 date nor a
    whole-value ``date`` placeholder (§3.10, §16.6)."""
    if not value or is_valid_iso_date(value):
        return
    directives = list(iter_directives(value))
    if _is_date_placeholder(value, directives, questions):
        return
    hint = (
        " A placeholder in a date field must be the whole value and of type date (§3.10)."
        if any(d.name == "placeholder" for d in directives)
        else ""
    )
    result.error(rule, f"{label} '{value}' must be a valid ISO 8601 date (YYYY-MM-DD).{hint}")


def _check_implicit_identifiers(
    document: Document,
    refs: Sequence[DefinitionRef],
    where: Locator,
    definition_line: Callable[[DefinitionRef], Line],
    result: _Recorder,
) -> None:
    """The headings and ``{{def:}}`` without an explicit identifier of a document that links
    translations (§14.2, §16.7), as far as the document alone shows: the Core rows (§17.2).

    A translation (``identifier_rule``) draws an Error for each, since identifiers originate in the
    primary and a translation mirrors them explicitly. Without ``authoritative`` the primary cannot
    be told from its translations, and the document draws a Warning for each, as any linked file
    does. *refs* are ``collect_definitions``', which holds none in a drafting note (§15.6); one whose
    generated identifier ``anchor-format`` rejects is not counted (the validator passes none: a
    generated identifier is always valid, §5.3). The linked files' own identifiers are Full (§17.4,
    §17.5)."""
    meta = document.metadata
    rule, _why_not = identifier_rule(meta)
    if rule is None:
        return
    if rule == "translation-authoritative-absent":
        report = result.warning
        why = "and authoritative is not declared, so the primary cannot be told from its translations"
        fix = "declare authoritative, and write the primary's identifier"
    else:
        report = result.error
        why = f"and the document is a translation ({meta.language}, of the authoritative {meta.authoritative})"
        fix = "write the primary's identifier"
    for index, section in enumerate(document.sections):
        if not section.identifier.strip():
            report(
                rule, f"Heading '{section.title}' has no explicit identifier, {why}: {fix}, {{#id}} (§14.2).",
                line=where.heading(index),
            )
    for ref in refs:
        if not ref.auto_id or not IDENTIFIER_RE.fullmatch(ref.id):
            continue
        report(
            rule, f"Definition '{ref.term}' has no explicit identifier ('{ref.id}' is generated from the "
            f"term), {why}: {fix}, {{{{def: id}}}} (§14.2).", line=definition_line(ref),
        )


def _check_duration_unit(unit: str, result: _Recorder) -> None:
    """Report a duration ``unit`` that §10.5 does not define."""
    if not unit:
        result.error("duration-invalid-unit", "The duration unit is missing or empty.")
    elif unit == "M":
        # §10.5: bare "M" is deliberately undefined (ISO 8601 would read it
        # as months; earlier drafts as minutes).
        result.error(
            "duration-invalid-unit",
            "Duration unit 'M' is not defined. Use 'MIN' for minutes or 'MO' for months.",
        )
    elif unit not in VALID_DURATION_UNITS:
        result.error(
            "duration-invalid-unit",
            f"Invalid duration unit '{unit}'. Must be one of: {', '.join(DURATION_UNITS)}.",
        )


# §10.1: notes are plain text — Markdown formatting would leak markers into
# machine-facing output.
_MARKDOWN_RE = re.compile(r"\*\*|__|\+\+|`|(?<!\w)\*(?!\s)")


def _check_directive_arguments(
    directive: Directive, result: _Recorder, *, effective_type: str | None = None
) -> bool:
    """Report the argument problems any directive can have; False if malformed.

    A malformed directive (§11.2) has no reliable arguments, so its own checks
    are skipped; its Error keeps the document from passing. An unknown
    parameter is ignored (§11.2) and a repeated one keeps its first value, so
    the directive's own checks still run. *effective_type* is a placeholder's
    effective type, which decides its type-specific parameters.
    """
    name = directive.name
    if directive.malformed:
        # An unclosed directive runs to the end of its line — often the whole
        # paragraph — so only its start is quoted.
        source = directive.source
        if len(source) > 60:
            source = source[:57] + "..."
        result.error(
            "directive-malformed",
            f"Malformed {{{{{name}:}}}} directive ({directive.malformed}): '{source}'.",
        )
        return False
    for param in dict.fromkeys(directive.duplicates):
        result.error(
            "directive-duplicate-param",
            f"Parameter '{param}' is given more than once in '{directive.source}'.",
        )
    for param in directive.unknown_params(effective_type=effective_type):
        result.warning(
            "directive-unknown-param",
            f"Parameter '{param}' is not defined for {{{{{name}:}}}} and is ignored: "
            f"'{directive.source}'.",
        )
    for param, value in directive.curly_quoted():
        of = f" of '{param}'" if param else ""
        result.warning(
            "value-curly-quote",
            f"Unquoted value{of}, '{value}', begins with a typographic quotation mark, which does not "
            f"quote it (§11.3); write a straight double quote (\") if one was meant: "
            f"'{directive.source}'.",
        )
    note = directive.params.get("note")
    if note is not None and "note" in DIRECTIVE_PARAMS.get(name, ()) and _MARKDOWN_RE.search(note):
        result.error(
            "note-invalid",
            "Note parameter must be plain text without Markdown formatting.",
        )
    return True


def _open_blank(blanks: dict[str, _BlankState], pid: str, in_frontmatter: bool, line: Line) -> _BlankState:
    """The blank of *pid*, made if need be, for an occurrence of it at *line*."""
    blank = blanks.setdefault(pid, _BlankState())
    if blank.line is None:
        blank.line = line
    blank.in_frontmatter |= in_frontmatter
    return blank


def _check_placeholder(
    directive: Directive,
    result: _Recorder,
    blanks: dict[str, _BlankState],
    questions: Any,
    *,
    in_frontmatter: bool = False,
    in_note: bool = False,
    line: Line = None,
) -> None:
    """Validate one ``{{placeholder:}}`` directive, its arguments included,
    and record it in *blanks*; *line* is where it is, and *in_note* whether it
    is in a drafting note.

    Shared by the body-block scan and the frontmatter scan so a placeholder id
    used in both is treated as the *same* blank (consistent type, currency,
    and unit, §3.10, §10.7).

    A drafting note is removed before anything is filled (§15.7.2 step 2), so
    an occurrence in one is no part of the blank: it is still recognized and
    validated by itself (§15.6), and takes no part in the blank's type,
    currency or unit (#112).
    """
    pid = directive.positional or ""
    params = directive.params
    written_type = params.get("type")
    declared = question_type(questions, pid)
    ptype = _placeholder_type(directive, declared)
    if not _check_directive_arguments(directive, result, effective_type=ptype):
        return
    result.index.values.placeholders.append((pid, ptype))
    if not pid or not IDENTIFIER_RE.fullmatch(pid):
        result.error(
            "placeholder-id-malformed",
            f"Placeholder id '{pid}' is invalid — must match [a-z][a-z0-9-]*.",
        )
        return
    # A note's occurrence is validated, and takes no part in the blank: no bookkeeping.
    blank = None if in_note else _open_blank(blanks, pid, in_frontmatter, line)
    type_valid = written_type is None or written_type in VALID_PLACEHOLDER_TYPES
    if not type_valid:
        result.error(
            "placeholder-type-invalid",
            f"Placeholder type '{written_type}' is unsupported. "
            f"Must be one of: {', '.join(sorted(VALID_PLACEHOLDER_TYPES))}.",
        )
    if declared in DECISION_QUESTION_TYPES:
        result.error(
            "placeholder-question-mismatch",
            f"Placeholder '{pid}' uses the id of {declared} question '{pid}'; a decision "
            f"question is answered by conditions and {{{{choose:}}}}, never by a blank (§15.2).",
        )
        return
    if not type_valid:
        return
    if declared is not None and written_type is not None and written_type != declared:
        result.error(
            "placeholder-question-mismatch",
            f"Placeholder '{pid}' is written with type '{written_type}', but its "
            f"question is declared with type '{declared}' (§15.2).",
        )
    # The currency or unit this occurrence fixes, by its type (§10.7).
    code_param = PLACEHOLDER_TYPE_PARAMS.get(ptype)
    code = params.get(code_param, "") if code_param else ""
    if blank is not None:
        if blank.type is None:
            blank.type = ptype
        if blank.type != ptype:
            blank.mixed = True
            result.error(
                "placeholder-type-inconsistent",
                f"Placeholder '{pid}' used with inconsistent types: "
                f"'{blank.type}' and '{ptype}'.",
            )
        elif ptype != "duration" or code in VALID_DURATION_UNITS or not code:
            # An invalid unit is reported below and fixes nothing.
            blank.codes.add(code)
            blank.code_lines.setdefault(code, line)
    if ptype == "money" and code and code not in KNOWN_CURRENCIES:
        result.warning(
            "placeholder-unknown-currency",
            f"Placeholder '{pid}' has unrecognized currency code '{code}'.",
        )
    if ptype == "duration" and "unit" in params:
        _check_duration_unit(code, result)


def _check_blank_codes(blanks: dict[str, _BlankState], result: _Recorder) -> None:
    """Report each blank whose occurrences fix two currencies or units: one
    blank cannot hold two (§10.7)."""
    for pid, blank in blanks.items():
        fixed = sorted(blank.codes - {""})
        if len(fixed) > 1:
            kind = "currencies" if blank.type == "money" else "units"
            # At the first occurrence that fixes a second one.
            second = [line for code, line in blank.code_lines.items() if code][1]
            result.error(
                "placeholder-type-inconsistent",
                f"Placeholder '{pid}' fixes different {kind} in different occurrences "
                f"({', '.join(fixed)}); one blank cannot hold two (§10.7).",
                line=second,
            )


def _check_final(
    placeholders: list[tuple[Directive, Line]],
    chooses: list[tuple[Directive, Line]],
    notes: list[tuple[Quote, Line]],
    conditions: list[tuple[str, str, Line]],
    questions: Any,
    result: _Recorder,
    questions_line: int | None,
) -> None:
    """The final check (§15.9): no blank and no template construct remains
    in a document meant for signature. The arguments are every one in the
    document: blanks, choices, drafting notes, and conditions (where, as
    written, and their lines)."""
    for directive, line in placeholders:
        result.error(
            _UNFILLED,
            f"'{directive.source}' is an unfilled blank in a document meant to be final (§15.9).",
            line=line,
        )

    def construct(what: str, line: int | None) -> None:
        result.error(
            _CONSTRUCT_PRESENT,
            f"{what} remains in a document meant to be final (§15.9).",
            line=line,
        )

    if questions is not None:
        construct("The 'questions' key", questions_line)
    for place, text, line in conditions:
        construct(f"The condition '{text}' on {place}", line)
    for directive, line in chooses:
        construct(f"'{directive.source}'", line)
    for _note, line in notes:
        construct("A drafting note", line)


def _clashes(presence: Presence, others: Sequence[Presence], questions: Any) -> bool:
    """True if a declaration with *presence* can appear together with one of
    the earlier declarations of the same identifier (§15.4)."""
    return any(not exclusive(presence, other, questions) for other in others)


# The identifier ``stem-k`` is the suffix k of ``stem`` (§5.5), as written: no
# leading zero, and k at least 2.
_SUFFIXED_RE = re.compile(r"(.*)-([2-9]|[1-9][0-9]+)")


class _Declarations(Sequence[Presence]):
    """The presences an identifier is declared with, to read: only its
    ``_FreeIdentifiers`` adds to them (``_add``)."""

    def __init__(self) -> None:
        self._presences: list[Presence] = []

    def _add(self, presence: Presence) -> None:
        self._presences.append(presence)

    def __len__(self) -> int:
        return len(self._presences)

    def __getitem__(self, index: Any) -> Any:
        return self._presences[index]

    def __iter__(self) -> Iterator[Presence]:
        return iter(self._presences)

    def __contains__(self, presence: object) -> bool:
        return presence in self._presences

    def __eq__(self, other: object) -> bool:
        return list(self._presences) == list(other) if isinstance(other, Sequence) else NotImplemented

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return repr(self._presences)


#: How many negative declarations a bitmap of ``_AnswerTally`` holds.
_CHUNK = 1024


class _AnswerTally:
    """What the declarations at one place — an identifier, or the suffixes of
    a base — say about one question, counted by the answers they allow, so
    that how many a presence excludes is a lookup, not a test of each (#94).

    A declaration that can appear and conditions the question allows either
    the one answer it wants (*positive*) or every answer but those it refuses
    (*negative*)."""

    def __init__(self) -> None:
        self.positive: Counter[Any] = Counter()  # declarations wanting a value, by value
        self.positive_total = 0
        self.negative = 0
        self.refusing: Counter[Any] = Counter()  # negative declarations refusing a value, by value
        #: The negative declarations refusing a value, by value, as bitmaps of
        #: ``_CHUNK`` declarations each, a bit for each in the order they were
        #: added (#110). Chunked, so that adding one costs a few words, not a
        #: bitmap of every declaration.
        self.refusers: dict[Any, list[int]] = {}
        self.widest = 0  # the most answers one negative declaration refuses

    @property
    def mentions(self) -> int:
        return self.positive_total + self.negative

    def add(self, wanted: frozenset[Any], refusal: frozenset[Any]) -> None:
        """Count a declaration that can appear and wants *wanted* and refuses
        *refusal* of the question's answers (``Exclusivity.values``)."""
        if wanted:
            (value,) = wanted
            self.positive[value] += 1
            self.positive_total += 1
        else:
            chunk, bit = divmod(self.negative, _CHUNK)
            self.negative += 1
            self.refusing.update(refusal)
            for value in refusal:
                chunks = self.refusers.setdefault(value, [])
                if len(chunks) <= chunk:
                    chunks.extend([0] * (chunk + 1 - len(chunks)))
                chunks[chunk] |= 1 << bit
            self.widest = max(self.widest, len(refusal))

    def excluded(self, wanted: frozenset[Any], refusal: frozenset[Any], answers: Collection[Any]) -> int:
        """How many of the declarations that name the question a presence
        excludes, when it wants *wanted* and refuses *refusal* of its *answers*
        (it can appear): those with no answer in common with it."""
        if wanted:
            (value,) = wanted
            # A positive declaration wants another value; a negative one refuses this.
            return self.positive_total - self.positive[value] + self.refusing[value]
        count = sum(self.positive[value] for value in refusal)  # wanting a value it refuses
        # A negative declaration is excluded if between them they refuse every
        # answer, which they cannot when the widest refuses too few: if it
        # refuses every answer the presence does not, which are the declarations
        # in the bitmap of each.
        if self.negative and len(refusal) + self.widest >= len(answers):
            others = [value for value in answers if value not in refusal]
            if not others:
                count += self.negative  # it refuses every answer: it excludes every one
            elif all(value in self.refusers for value in others):
                # zip stops at the shortest: a chunk a value has no bit in holds none.
                for chunks in zip(*(self.refusers[value] for value in others), strict=False):
                    both = chunks[0]
                    for chunk in chunks[1:]:
                        both &= chunk
                        if not both:
                            break  # no declaration in the chunk refuses every one
                    count += both.bit_count()
        return count


class _FreeIdentifiers:
    """The identifiers headings get when theirs is missing or taken (§5.5).

    Called with a heading's base identifier and presence, it returns the
    lowest suffix of the base — none, -2, -3, … — not taken by an explicit
    identifier or by an earlier heading that can appear with this one. The
    caller declares each identifier it takes (``declare``), explicit or not,
    and reads the declarations of each identifier back (``anchors``).

    Probing every suffix for each heading would be quadratic in a document of
    many headings with one title (#94). Three things avoid it:

    - A suffix taken for a presence stays taken, as declarations only grow,
      so a call for the same base and presence resumes after the suffixes
      known to be taken.
    - A presence can appear together with a declaration unless some question
      they both condition has no answer that satisfies both (``satisfiable``
      decides per question, §15.4). When no declaration at a base's suffixes
      is excluded by the heading's own conditions on any question, every
      suffix with a declaration is taken, and the lowest free one is the
      first with none.
    - Whether the heading's conditions on one question exclude the
      declarations at a suffix, or all of them, is counted from what they
      want and refuse (``_AnswerTally``), not tested for each.

    Two shapes remain superlinear:

    - Headings whose presences are all different and all clash, like
      ``p_k ∧ q`` and ``p_k ∧ !q`` for every k (each needs a declared question):
      each is looked at against every earlier suffix, which the counts decide
      without testing a condition, so n headings cost O(n²) cheap lookups: about
      0.7 s for 1,000, 3 s for 2,000 and 14 s for 4,000. The identifiers are
      exact and the work is not capped, which would make them depend on the
      size of the document (#110). A heading with conditions on several
      questions that together exclude each declaration at a suffix, no one
      question excluding them all, is tested against each declaration there.
    - A choice question with many distinct refusals of several values each
      (``!c:a ∧ !c:b …``): a negative presence intersects, whenever the widest
      refusal at a suffix and its own might cover every answer, the bitmaps of
      the declarations that refuse each answer it does not: n such headings cost
      O(n²) machine words (n / 64 for each of the answers), 0.3 s for 4,000 (#110).
    """

    def __init__(
        self, explicit_ids: Collection[str], questions: Any, exclusivity: Exclusivity | None = None
    ) -> None:
        self._explicit_ids = frozenset(explicit_ids)  # the pointers below rely on its not changing
        self._declarations: dict[str, _Declarations] = {}
        #: The declarations of each identifier, by presence. Read-only: they
        #: are made by ``declare``, which the indexes below follow.
        self.anchors: Mapping[str, Sequence[Presence]] = MappingProxyType(self._declarations)
        #: The answers of each question and what each presence wants and refuses
        #: of them, worked out once for the pool (or for the validation, given its
        #: *exclusivity*); the pairs it excludes are tested as they are asked, with
        #: no cache of their own.
        self._exclusivity = Exclusivity(questions) if exclusivity is None else exclusivity
        #: How many leading suffixes of a base are taken for a presence.
        self._taken: dict[tuple[str, Presence], int] = {}
        #: How many declarations that can appear an identifier has.
        self._occupants: dict[str, int] = {}
        #: What those declarations say of a question: at an identifier, and
        #: across a base's suffixes.
        self._at: dict[tuple[str, str], _AnswerTally] = {}
        self._across: dict[tuple[str, str], _AnswerTally] = {}
        #: Per base, the lowest suffix that is not explicit, and the lowest
        #: that is not explicit either and has no declaration that can appear.
        #: Both only move up.
        self._lowest: dict[str, int] = {}
        self._lowest_empty: dict[str, int] = {}

    def declare(self, identifier: str, presence: Presence) -> None:
        """Record that *identifier* is declared with *presence*."""
        self._declarations.setdefault(identifier, _Declarations())._add(presence)
        if not self._can_appear(presence):
            return  # it clashes with nothing
        self._occupants[identifier] = self._occupants.get(identifier, 0) + 1
        suffixed = _SUFFIXED_RE.fullmatch(identifier)
        bases = (identifier, suffixed.group(1)) if suffixed else (identifier,)
        for question in self._exclusivity.questions(presence):
            wanted, refusal = self._exclusivity.values(presence, question)
            self._at.setdefault((identifier, question), _AnswerTally()).add(wanted, refusal)
            for base in bases:
                self._across.setdefault((base, question), _AnswerTally()).add(wanted, refusal)

    def __call__(self, base: str, presence: Presence) -> str:
        if not self._can_appear(presence):
            return self._suffixed(base, self._first(self._lowest, base, occupied=False))
        empty = self._first(self._lowest_empty, base, occupied=True)
        if not self._excludes_some(base, presence):
            return self._suffixed(base, empty)  # every suffix with a declaration is taken
        key = (base, presence)
        suffix = self._taken.get(key, 0) + 1
        while suffix < empty:
            candidate = self._suffixed(base, suffix)
            if candidate not in self._explicit_ids and not self._appears_with(candidate, presence):
                return candidate
            self._taken[key] = suffix
            suffix += 1
        return self._suffixed(base, empty)

    @staticmethod
    def _suffixed(base: str, suffix: int) -> str:
        return base if suffix == 1 else f"{base}-{suffix}"

    def _first(self, lowest: dict[str, int], base: str, *, occupied: bool) -> int:
        """The lowest suffix of *base* from ``lowest[base]`` on that is not
        explicit and, with *occupied*, has no declaration that can appear."""
        suffix = lowest.get(base, 1)
        while (candidate := self._suffixed(base, suffix)) in self._explicit_ids or (
            occupied and self._occupants.get(candidate)
        ):
            suffix += 1
        lowest[base] = suffix
        return suffix

    def _excludes_some(self, base: str, presence: Presence) -> bool:
        """True if *presence* (that can appear) excludes some declaration at
        some suffix of *base*, on some question."""
        for question in self._exclusivity.questions(presence):
            declared = self._across.get((base, question))
            if declared is None:
                continue
            if declared.excluded(*self._exclusivity.values(presence, question), self._exclusivity.answers(question)):
                return True
        return False

    def _appears_with(self, identifier: str, presence: Presence) -> bool:
        """True if *presence* (that can appear) can appear together with a
        declaration of *identifier* (``_clashes``)."""
        occupants = self._occupants.get(identifier, 0)
        if not occupants:
            return False  # nothing, or only what never appears
        questions = self._exclusivity.questions(presence)
        excluded = 0  # the declarations the questions exclude, counted for each question
        for question in questions:
            declared = self._at.get((identifier, question))
            if declared is None or declared.mentions < occupants:
                if len(questions) == 1:
                    return True  # a declaration that says nothing of the one question
                if declared is None:
                    continue
            count = declared.excluded(*self._exclusivity.values(presence, question), self._exclusivity.answers(question))
            if count == occupants:
                return False  # this question alone excludes every declaration
            excluded += count
        if excluded < occupants:
            return True  # some declaration is excluded by no question
        # The questions together might exclude each declaration, one for each.
        return any(not self._exclusivity.excluded(presence, other) for other in self._declarations[identifier])

    def _can_appear(self, presence: Presence) -> bool:
        return self._exclusivity.appears(presence)


def _link_destination_end(view: str, at: int) -> int | None:
    """Where the inline link whose ``<…>`` destination opens at ``view[at]`` ends (after its ``)``), or
    None when it is none: the ``<`` follows ``](``, with the gap of ``scan_inline_link`` between, and no raw
    HTML is in a link's destination (CommonMark)."""
    before = back_line_space(view, at)
    if view[before - 2:before] != "](":
        return None
    link = scan_inline_link(view, before - 1)
    return None if link is None else link[2]


def _inline_html(text: str, lexed: Lexed) -> str | None:
    """The first inline raw HTML in *text* other than a comment (§8.7), as
    written, or None: outside code spans, comments and directives (``lex``),
    not after a backslash, and not a link's ``<…>`` destination."""
    # What the lexer blanked, a comment or a code span, is filled with a
    # character no tag holds outside a quoted value: no tag runs across it, and
    # neither does a declaration, processing instruction or CDATA section. (In
    # CommonMark the HTML that starts first wins, so ``<!a `>` b`` is a
    # declaration, ending in the code span, and so is ``<!a <!-- c --> b``, ending
    # in the comment's ``>``; those are not found here: CONFORMANCE.md, #111.)
    view = "".join(
        "`" if seen == " " and written not in " \t\n" else seen
        for seen, written in zip(mask_directives(lexed.view, lexed.directives), text, strict=True)
    )
    # A processing instruction, declaration or CDATA section is read to its closer, which takes the rest of
    # the text to look for when there is none: once there is no closer after an offset, none is searched
    # for there again, and every ``<`` costs what it reads.
    unclosed = inline_html_unclosed(view)
    opened = -1  # the last unescaped ``[`` before *at* with no ``]`` after it but a link's
    scanned = 0
    pos = 0
    while (at := view.find("<", pos)) >= 0:
        pos = at + 1
        if is_escaped(view, at):
            continue
        for k in range(scanned, at):
            if view[k] == "[" and not is_escaped(view, k):
                opened = k
            elif view[k] == "]" and view[k + 1:k + 2] != "(":
                opened = -1
        scanned = at
        if opened >= 0 and (destination := _link_destination_end(view, at)) is not None:
            pos = scanned = destination
            opened = -1
            continue
        if not unclosed(at) and (tag := INLINE_HTML_RE.match(view, at)):
            return text[at:tag.end()]
    return None


def _check_raw_html(
    document: Document, lex_fragment: Callable[[str], Lexed], result: _Recorder, where: Locator
) -> None:
    """Warn about raw HTML other than comments (§8.7), which renderers leave
    out: once for each HTML block, in list items and quotes too, and once for
    each text — a paragraph, a table cell, a heading — holding inline HTML."""
    from ..definitions import block_fragments, nested_blocks  # see the import note in _validate

    def warn(what: str, source: str, line: int | None) -> None:
        shown = source.strip().split("\n", 1)[0]
        shown = shown if len(shown) <= 60 else shown[:57] + "..."
        result.warning(
            "raw-html",
            f"Raw HTML {what} ('{shown}') is not rendered: renderers leave it out of the "
            f"output (§8.7). Only a comment (<!-- -->) is portable (§8.6).",
            line=line,
        )

    for index, section in enumerate(document.sections):
        title = section.title
        if "<" in title and (html := _inline_html(title, lex_fragment(title))) is not None:
            warn("in text", html, where.heading(index))
    for section_index, block_index, top in document.iter_indexed_blocks():
        for block in nested_blocks(top):
            if block.kind == "html" and not is_comment_only(block.text):
                source = strip_comments(block.text)
                first = source.strip().split("\n", 1)[0]
                warn("block", source, where.find(section_index, block_index, first))
        for fragment_index, (text, _position) in enumerate(block_fragments(top)):
            if "<" in text and (html := _inline_html(text, lex_fragment(text))) is not None:
                line = where.find(section_index, block_index, html, fragment_index, text.find(html))
                warn("in text", html, line)


def _check_never_true(
    document: Document,
    markers: list[FoundMarker],
    units: Units,
    questions: Any,
    result: _Recorder,
    where: Locator,
) -> None:
    """Report each conditional unit that can never appear: its own condition
    contradicts those of the units enclosing it (condition-never-true,
    §15.4). A unit inside one already reported is not reported again."""
    for index, section in enumerate(document.sections):
        if (
            own_presence(section.condition, questions)
            and satisfiable(units.enclosing(index), questions)
            and not satisfiable(units.presence(index), questions)
        ):
            result.warning(
                "condition-never-true",
                f"The heading '{section.title}' can never appear: its condition "
                f"'{section.condition}' contradicts those of the sections enclosing it (§15.4).",
                line=where.heading(index),
            )
    for found in markers:
        if found.misplaced or found.section is None:
            continue  # literal text, or a preamble paragraph (no enclosing section)
        if not units.own(found.section, found.block, found.fragment):
            continue
        if satisfiable(
            units.enclosing_unit(found.section, found.block, found.fragment), questions
        ) and not satisfiable(units.presence(found.section, found.block, found.fragment), questions):
            result.warning(
                "condition-never-true",
                f"'{found.source}' marks a unit that can never appear: its condition "
                f"contradicts those of the sections and list items enclosing it (§15.4).",
                line=where.find(found.section, found.block, found.source, found.fragment, found.offset),
            )


def is_template(document: Document) -> bool:
    """True if *document* is a template (§15.1): it declares ``questions``,
    carries a condition — on a section, an attachment, or a placed marker —
    or contains a ``{{choose:}}``, wherever it is. ``validate``
    reports it too (``DocumentIndex.is_template``)."""
    return _is_template(document, None, lex)


def _is_template(
    document: Document,
    markers: list[FoundMarker] | None,
    lex_fragment: Callable[[str], Lexed],
) -> bool:
    """``is_template``; *markers* are ``find_markers(document,
    lex_fragment)`` when the caller has them."""
    from ..definitions import text_fragments  # see the import note in _validate

    meta = document.metadata
    if markers is None:
        markers = find_markers(document, lex_fragment)
    structural_fields, value_fields = _frontmatter_fields(meta)
    texts = [
        *(text for _label, text, _path in structural_fields),
        *(text for text, _path in value_fields),
        *(section.title for section in document.sections),
        *(text for _s, _i, block in document.iter_indexed_blocks() for text in text_fragments(block)),
    ]
    return (
        meta.questions is not None
        or any(att.when for att in meta.attachments)
        or any(section.condition for section in document.sections)
        or any(found.marker and found.marker.condition and not found.misplaced for found in markers)
        or any(d.name == "choose" for text in texts for d in lex_fragment(text or "").directives)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _Mode:
    """How ``_validate`` reads a document that is not a complete, readable one
    (#39, #43). Internal: only ``validate(fragment=)`` is public. The default is
    an ordinary document; the other presets below are plain data, so each issue
    that needs one picks it where it knows the case.

    ``empty_metadata``: validate with the empty metadata of a document without
    frontmatter in place of the document's own (the source map still fits: it
    describes the body, ``positions.document_shape``), so that nothing is
    checked of, or resolved against, frontmatter that is not to be read as
    declarations, and every rule that waits on ``frontmatter_absent`` (§3.1,
    §16.6) is silent. ``suppress``: the findings that need these (``Depends``)
    are not reported. ``report_frontmatter_absent``: whether the document draws
    frontmatter-absent (§3.2); it does not when its frontmatter is there but
    is not read. ``check_first_heading``: the first heading is required at
    level 1 (§4.1) although the metadata is the empty one, because the block
    that is not read still is the frontmatter of a main document. ``fragment``:
    the document is an include fragment or an attachment file validated on its
    own (§12.2, §12.4): its findings with a reason are ``host_dependent``.
    ``template``: whether the document is a template, when the caller knows
    (the fragment's host is one, or is not); None leaves it to the document
    (``is_template``). ``kind``: what the fragment is to its host, an include
    fragment or a LegalDown attachment file (#35): the ids of the findings on its
    shape, and what §15.3 asks of it in a template."""
    empty_metadata: bool = False
    suppress: frozenset[Depends] = frozenset()
    report_frontmatter_absent: bool = True
    check_first_heading: bool = False
    fragment: bool = False
    template: bool | None = None
    kind: FragmentKind = "include"


#: A complete document.
_DOCUMENT = _Mode()
#: An include fragment or attachment file on its own (#39): all the findings
#: are made, and those the including document may settle are flagged.
_FRAGMENT = _Mode(empty_metadata=True, report_frontmatter_absent=False, fragment=True)
#: A document whose frontmatter cannot be read (#43): its body is validated, but
#: not against what the frontmatter might have declared.
_UNREADABLE_FRONTMATTER = _Mode(
    empty_metadata=True,
    suppress=frozenset({Depends.FRONTMATTER}),
    report_frontmatter_absent=False,
    check_first_heading=True,
)


def validate(
    document: Document,
    *,
    final: bool = False,
    fragment: bool = False,
    fragment_kind: FragmentKind | None = None,
    template: bool | None = None,
    resolve: LoadFile | None = None,
    document_root: str | os.PathLike[str] | None = None,
    import_definitions: DefinitionsImporter | None = None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None = None,
) -> ValidationResult:
    """Validate a LegalDown document and build lookup indices.

    A document whose frontmatter could not be read (``parse`` or ``load`` with
    ``on_frontmatter_error="keep"``, #43) draws frontmatter-invalid-yaml, and
    its body is validated all the same, as one whose metadata is empty:
    nothing is reported for want of what the frontmatter might have declared
    (a title, sides, and the party, side, attachment or question the body uses).
    It is so for as long as ``Document.frontmatter_error`` is set, not only
    when the document is parsed: a caller that repairs the metadata in code
    sets ``document.frontmatter_error = None`` to have it read as it is.

    A document that holds a construct whose checks lie beyond Core (an include paragraph,
    an attachment file, ``amends.file``, ``translations``, ``supersedes.file``, an image
    path) draws the Warning ``ldv-check-skipped`` that §17.5 asks for, once for each category
    and at its first construct, naming what this validation did not perform (for a document with an
    include, the combined-document checks of §16.11 when an include was not followed, a fragment
    that was not read, its checks, and the limits reached when following includes; none when every
    include was followed and nothing else was skipped; for an attachment file, the checks across
    the combined document it is not part of). A document without such a construct
    draws none.

    Parameters
    ----------
    document:
        A ``Document``, from ``load`` or ``parse``.
    final:
        Apply the final check (§15.9): the document is meant for signature,
        so a remaining blank or template construct is an Error.
    fragment:
        The document is an include fragment (§12.2) or a LegalDown attachment
        file (§12.4) validated on its own: body-only, so it is not asked for
        frontmatter, a title, parties or sides, and a level-1 first heading is
        not required (§4.1). What it cannot settle alone, because the document
        that includes it declares it (a party, a side, an attachment or a
        question) or defines it (the target of a ``{{ref:}}`` or a
        ``{{term:}}``), is still reported, with the same rule id and level it
        has in a complete document, but marked ``Diagnostic.host_dependent``:
        a finding to set aside when the host is not known, and to take
        seriously once the fragment is validated in it. What is wrong with the
        fragment itself is not marked. Frontmatter in the fragment is
        include-has-frontmatter (§16.11) and a level-1 heading include-has-h1
        (attachment-has-frontmatter and attachment-has-h1 for an attachment
        file, *fragment_kind*); the metadata is then not read, so nothing is
        checked of it. The fragment's own includes are not read: *resolve*
        belongs to the document that includes it, which follows them. A
        definition the fragment declares and does not reference is still
        reported (def-unreferenced), as ``host_dependent``: the host may
        reference it (§12.2). Default False: a complete document, as before.
    fragment_kind:
        Only with *fragment*: what the file is to the document that uses it,
        ``"include"`` (an ``{{include:}}`` fragment, §12.2) or ``"attachment"`` (a
        LegalDown attachment file, ``attachments[].file``, §12.4). They share
        a file model, but their findings have other ids: the frontmatter or
        level-1 heading of an attachment file is attachment-has-frontmatter or
        attachment-has-h1 (§16.10), of a fragment include-has-frontmatter or
        include-has-h1 (§16.11). And with ``template=True`` an include fragment is
        held to what §15.3 asks of a template's fragments, as assembly asks it: no
        condition, no drafting note, no ``{{include:}}``, and an explicit identifier
        on every heading (template-fragment-invalid, §16.12), while an attachment
        file may hold conditions and notes of its own, and only an ``{{include:}}``
        in it is reported. Those checks are made for ``template=True`` only: a
        fragment's host is not known to be a template otherwise. By default
        (None) the file is an include fragment, as before. Raises ``ValueError``
        without *fragment*, or for any other value.
    template:
        Only with *fragment*: whether the document that includes the fragment
        is a template (§15.1), which a fragment cannot tell by itself (a
        blank or a drafting note is legal in any document). True checks it as
        part of a template: the checks only a template has are made (a
        blank whose id YAML reads as a boolean or null; what §15.3 asks of a
        template's fragment, ``template-fragment-invalid``, per *fragment_kind*)
        and a preamble paragraph's condition is placed (§5.7, §15.3). False says it is not one, so it declares no
        questions either: a condition or ``{{choose:}}`` naming a question is
        then ``condition-invalid`` / ``choose-invalid`` without being
        ``host_dependent``, and the fragment is no template. By default
        (None) a fragment is a template exactly when it holds a condition or a
        ``{{choose:}}``, as any document does, but is then not held to what §15.3
        asks of a template's fragments, for it is not known to be in one;
        ``DocumentIndex.is_template`` and ``placed_markers`` follow the
        decision. Raises ``ValueError`` without *fragment*.
    resolve:
        Reads a file the document refers to: ``(relative_path) -> text | None``,
        with ``None`` for a file that is not there or cannot be read. These are
        the document it amends (``amends.file``, §7.5) and its LegalDown
        attachment files (§12.4), for the definitions they declare, and its
        include fragments (§12.2), which are checked: ``include-file-missing`` at
        the include of one that is not there, and, at their own lines and with
        ``Diagnostic.file`` the fragment's path, ``include-has-frontmatter``,
        ``include-has-h1`` and ``include-heading-skip`` (§16.11), and, in a
        template, the parts of ``template-fragment-invalid`` that read a file
        (§15.3, §16.12). By default the files are read from the directory of
        ``document.path`` (``file_loader``), as far as that is within it (§2.3), or
        within the *document_root* when one is configured; a document without a
        ``path`` (from ``parse``) has none, so nothing is
        read and the checks that need a file are not made. ``resolve=lambda path:
        None`` says every file is missing, which ``include-file-missing`` reports:
        to validate a loaded document without reading its files, validate it
        without its path (``dataclasses.replace(document, path=None)``). A path
        a loader is not asked for (``relative_path``) is not read. An include of
        one that holds a backslash or a null byte is ``include-file-missing``, as
        assembly refuses it. A path that is refused only because it leads out of
        the document's directory (``../x.lgd``) is not that: it is a valid path
        (§2.3). With a *document_root* that holds it, it is read like any other
        (a custom *resolve* is asked for ``../x.lgd`` too); with none this
        implementation does not read it, never asks *resolve* for it, and says
        nothing but the Warning of §17.5 (given a root it leaves, it is
        path-outside-root, and nothing outside the root is ever read, nor anything
        a document outside it names). Only the referenced file's own
        definitions are read, not those it refers to in turn. The includes a fragment
        holds are followed (§12.2), outside a template: each is judged as the document's
        are, from the fragment's directory, at its line in the fragment, a chain that comes back
        is ``include-cycle`` and an identifier the combined document declares twice
        ``include-anchor-duplicate`` (§16.11); to a depth of 32 fragments and 10,000 includes and
        headings, past which nothing is followed and the Warning of §17.5 says so. When every
        include of the document was followed into a fragment, the document is validated once more
        with the fragments spliced in at their includes, as the one document it is (§16.11; the
        combined document): a reference or a term into a fragment resolves, an auto-generated
        identifier that the fragments and the document share is ``anchor-autogen-collision``, and
        what a fragment holds that a complete document may not is reported in it, at its line, with
        ``Diagnostic.file`` the fragment's path. A LegalDown attachment file is not part of that
        document. With any include not followed, the document is validated as it is, and the
        Warning says the combined-document checks were not made.
    document_root:
        The directory every file the document refers to must lie within
        (§2.3): a path that escapes it, by ``../`` or after ``%2E%2E/`` in an
        image, draws path-outside-root. Without one (the default) the
        containment check does not run, and ``../shared/definitions.lgd`` is
        valid, though not read (a document one does not control must not read what
        it names; configure a root to read such files, which a document in the
        root may then do, and never beyond it). A document from ``load`` is read from the directory of its
        ``path``, which must be within the root (else the document is
        reported once as outside it); one from ``parse`` is taken to be at the
        root. The check reads only the text of the paths, never a file, and a symbolic
        link is not followed; the default loader does follow one, and reads no file whose
        real path is outside the root. A path that is not relative (an absolute one, or
        one beginning with a URI scheme) draws path-not-relative, with or
        without a root. Both checks cover ``{{include:}}``, an image's path
        (``![alt](path)``, not a reference-style one) and ``attachments[].file``,
        ``amends.file``, ``supersedes.file`` and ``translations`` of the
        frontmatter, and are Core checks: whether the file exists is Full
        (§17.4). In a *fragment* the include and image paths are the
        fragment's own, so they are checked and never ``host_dependent``;
        its frontmatter is never read, so it has no paths to check.
    import_definitions, import_attachment_definitions:
        Deprecated since 0.4.0, removed in 0.5.0: use *resolve*. They give the
        definitions themselves, which *resolve* leaves to the validator.
    """
    if template is not None and not fragment:
        raise ValueError("validate(template=) is only meaningful with fragment=True")
    if fragment_kind is not None and not fragment:
        raise ValueError("validate(fragment_kind=) is only meaningful with fragment=True")
    if fragment_kind not in (None, "include", "attachment"):
        raise ValueError(f"fragment_kind must be 'include' or 'attachment', not {fragment_kind!r}")
    resolve = _resolver(
        document, resolve, import_definitions, import_attachment_definitions, stacklevel=3, document_root=document_root
    )
    mode = replace(_FRAGMENT, template=template, kind=fragment_kind or "include") if fragment else _DOCUMENT
    return _validate(
        document, final, resolve, import_definitions, import_attachment_definitions, mode, document_root=document_root
    )


def validate_document(
    document: Document,
    *,
    import_definitions: DefinitionsImporter | None = None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None = None,
    final: bool = False,
) -> ValidationResult:
    """Deprecated since 0.4.0, removed in 0.5.0. Same as ``validate``, with the
    same arguments."""
    warnings.warn(
        "legaldown.validate_document() is deprecated since 0.4.0 and will be removed in 0.5.0; "
        "use legaldown.validate()",
        LegaldownDeprecationWarning,
        stacklevel=2,
    )
    resolve = _resolver(document, None, import_definitions, import_attachment_definitions, stacklevel=3)
    return _validate(document, final, resolve, import_definitions, import_attachment_definitions, _DOCUMENT)


def validate_template(
    document: Document, resolve: LoadFile | None, document_root: str | os.PathLike[str] | None
) -> tuple[ValidationResult, bool]:
    """``validate`` for the template of a ``Template``: it reads through *resolve* and no other way. A
    document with a path (the template's, for the root and for the self-include test) is not read from beside
    it by default, as assembly does not: with no *resolve*, nothing is read, and the Warning of §17.5 says so.
    With it, whether the template was validated with its fragments in (§16.11)."""
    result = _check(document, False, resolve, None, None, _DOCUMENT, document_root=document_root)
    return result.result(), result.combined


def _resolver(
    document: Document,
    resolve: LoadFile | None,
    import_definitions: DefinitionsImporter | None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None,
    *,
    stacklevel: int,
    document_root: str | os.PathLike[str] | None = None,
) -> LoadFile | None:
    """How the validation reads the files *document* refers to: *resolve*, else
    the directory of its path (and the *document_root* around it, which a file may
    lead out to, #109), else not at all.
    Says the importers are deprecated when one is given, and then reads nothing
    by itself: each importer answers for its own files, as it always did."""
    if import_definitions is not None or import_attachment_definitions is not None:
        warnings.warn(
            "import_definitions and import_attachment_definitions are deprecated since 0.4.0 "
            "and will be removed in 0.5.0; use validate(resolve=), which reads the files itself",
            LegaldownDeprecationWarning,
            stacklevel=stacklevel,
        )
        return resolve
    if resolve is None and document.path is not None:
        resolve = file_loader(document.path.parent, root=document_root)

    return resolve


class _Imported(NamedTuple):
    """The definitions of a file a document names (``None`` when none were read), and, then, why
    not (``UnreadReason``)."""
    definitions: dict[str, str] | None
    unread: UnreadReason | None = None

    @property
    def not_imported(self) -> UnreadReason | None:
        """The reason the Warning of §17.5 names an import that did not happen for (``NOT_IMPORTED``):
        every one but the document's own, whose definitions are the document's already."""
        return self.unread if self.unread in NOT_IMPORTED else None


def _imported(definitions: dict[str, str] | None) -> _Imported:
    """What a deprecated importer gave: its word for the file; None is a file it had not."""
    return _Imported(definitions, None if definitions is not None else "unreadable")


def _definitions_in(
    document: Document, files: Files | None, path: str, references: FileReferences
) -> _Imported:
    """The definitions the file at *path* declares (``{id: term}``), or None
    when it cannot be read as a LegalDown document: not there, no *files* (the
    validation has no ``resolve``), or frontmatter that cannot be read (that
    file's problem, not this document's), with the reason. Any other exception is
    the parser's fault, as it is anywhere."""
    # Imported here for the reason given in ``_validate``.
    from ..definitions import collect_definitions, definition_lookup

    # Asked for as assembly asks (§15.7): normalized, and only a path within
    # the document's directory, or the document root around it (§2.3).
    if references.is_wrong(path):
        return _Imported(None, "rejected")
    if files is None:
        return _Imported(None, "no-resolver")
    relative, problem = files.relative_check(path)
    if relative is None:
        # Valid as a path, and not one a loader is asked for (it leads out of the document's
        # directory, and no document root holds it); or none (a backslash, a null byte).
        return _Imported(None, "leads-out" if problem == "leads-out" else "unreadable")
    # A document that names itself has nothing to add to itself.
    if files.is_own(relative):
        return _Imported(None, "own")
    other = files.read(relative)
    if other is None or other.document.frontmatter_error is not None:
        return _Imported(None, "unreadable")
    return _Imported(definition_lookup(collect_definitions(other.document)))


def _validate(*args: Any, **kwargs: Any) -> ValidationResult:
    """``_check``, as the ``ValidationResult`` ``validate`` returns."""
    return _check(*args, **kwargs).result()


def _check(
    document: Document,
    final: bool,
    resolve: LoadFile | None,
    import_definitions: DefinitionsImporter | None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None,
    mode: _Mode,
    document_root: str | os.PathLike[str] | None = None,
    combined: Combined | None = None,
) -> _Recorder:
    # *combined*: the document is the combined document of another (§16.11, #117), validated once more
    # as the document it is with its fragments in: ``document`` is its own, and the validation reads
    # the files, the lexing and the locations of the document that holds the includes.
    # A document whose frontmatter could not be read (parse(on_frontmatter_error=
    # "keep"), #43) is validated by its body. A fragment is not: it must have no
    # frontmatter, which is then not read either (§12.2), so what it asks is
    # the same, and its mode is already that of a body that is not declared.
    unreadable = document.frontmatter_error if not mode.fragment else None
    if unreadable is not None:
        mode = _UNREADABLE_FRONTMATTER
    result = _Recorder(
        suppress=mode.suppress, fragment=mode.fragment, places=[] if combined is not None else None
    )
    # A fragment's own frontmatter, if it wrongly has any, is reported below
    # and then not read: the host's frontmatter is the one that declares.
    # Everything after this reads the document as a body-only one, whose
    # source map is the same (it describes the body, positions.document_shape).
    original = document
    if mode.empty_metadata:
        document = replace(document, metadata=Metadata(frontmatter_absent=True))
    # Where each part is, for the line of each diagnostic (§16.9).
    where = Locator(document) if combined is None else CombinedLocator(combined)
    # The files the document refers to, each read and parsed once: the original it
    # amends, its attachment files, and its include fragments (#35). A fragment
    # validated on its own reads none: its own includes are the host's concern.
    files = combined.files if combined is not None else (
        Files(resolve, document, document_root) if resolve is not None else None
    )
    # The paths the document names (§2.3, #104), from the directory it is in.
    file_references = FileReferences(
        result, document_root, document.path.parent if document.path is not None else None, where.start()
    )
    # §3.2: a newer declared version draws a Warning, never a failure, and
    # softens unknown directives to Warnings (§11.5).
    declared_version = parse_version(document.metadata.legaldown)
    newer_version = declared_version is not None and declared_version > parse_version(
        SPEC_VERSION
    )
    if newer_version:
        result.warning(
            "legaldown-version-newer",
            f"The document declares LegalDown {document.metadata.legaldown}; this "
            f"implementation supports {SPEC_VERSION}, so constructs added since may not "
            f"be recognized (§3.2).",
            line=where.field("legaldown"),
        )
    # §16.6: an Error, but "prevents rendering" (§16.1), not "stops validating":
    # the block is not read (the metadata is empty, above), and the body still is.
    if unreadable is not None:
        result.error("frontmatter-invalid-yaml", frontmatter_message(unreadable), line=unreadable.line or 1)
    # Without frontmatter a document is valid, but has no metadata to
    # check: it draws this Warning alone (§3.2, §16.6).
    frontmatter_absent = document.metadata.frontmatter_absent
    if frontmatter_absent and mode.report_frontmatter_absent:
        result.warning(
            "frontmatter-absent",
            "The document has no frontmatter (§3.1), so it has no title, parties, or other "
            "metadata. Frontmatter is a mapping of YAML fields between a '---' line at the "
            "very start and a closing '---' line.",
            line=where.start(),
        )
    if mode.fragment:
        # The file's own text has no frontmatter and no level-1 heading, whatever it is
        # used for (§12.2, §12.4): an include fragment's ids, or an attachment file's.
        for finding in shape_findings(original, where, mode.kind):
            result.error(finding.rule, finding.message, line=finding.line)
    title = document.metadata.title.strip()
    if not title and not frontmatter_absent:
        result.error("title-missing", "The document title is required.", line=where.field("title"))

    # Imported here to avoid a module-level import cycle (definitions ->
    # validator.helpers -> validator/__init__ -> core).
    from ..definitions import (
        block_fragments,
        collect_definitions,
        find_definition_anchors,
        fragments_before_target,
        id_term,
        quote_ranges,
        text_fragments,
    )
    from .circular import TermUse, circular_message, find_circular_definitions, term_owners, used_before_declaration

    # Each text is lexed once per validation and shared by the passes below.
    lex_fragment = cache(lex) if combined is None else combined.lex_fragment
    meta = document.metadata
    questions = meta.questions
    #: What each presence wants and refuses of the questions, worked out once for the validation:
    #: the identifiers of headings, circular definitions and the headings of included fragments share it.
    exclusivity = Exclusivity(questions)
    structural_fields, value_fields = _frontmatter_fields(meta, where)
    frontmatter_texts = [(text, path) for _label, text, path in structural_fields] + value_fields
    headings = [section.title for section in document.sections]

    def named(text: str, name: str) -> list[Directive]:
        """The *name* directives in a frontmatter value or heading, each text
        lexed once per validation."""
        return [d for d in lex_fragment(text or "").directives if d.name == name]

    # The combined pass of a document that is no template: §15.1 decides it by the document alone, and a
    # document that is none has no condition and no {{choose:}} of its own, so those of its fragments are not
    # collected, nor judged (the fragment is as it was before it was validated with the document).
    untemplated = combined is not None and not combined.template
    # {{choose:}} belongs in body text: never in frontmatter or a heading
    # (§15.5). Wherever it is, it makes the document a template (§15.1).
    misplaced_chooses = [
        (directive, "in frontmatter; it belongs in body text", where.key(*path))
        for text, path in frontmatter_texts
        for directive in named(text, "choose")
    ] + [
        (directive, "in a heading, where §4.2 allows plain text only", where.heading(index))
        for index, text in enumerate(headings)
        for directive in named(text, "choose")
    ]
    if untemplated:
        misplaced_chooses = []

    # ── Templates and the presence of units (§15.1, §15.3) ──
    # Markers are found first: only a template gives a preamble paragraph's
    # condition its place (§5.7).
    markers = find_markers(document, lex_fragment) if combined is None else combined.markers()
    if combined is not None:
        template = combined.template  # the document's own: §15.1, as the assembler decides it
    else:
        template = _is_template(document, markers, lex_fragment) if mode.template is None else mode.template
    units = Units(document, markers, questions, template=template) if combined is None else CombinedUnits(
        combined, questions, template=template
    )
    # A host that is not a template declares no questions: a question that a
    # condition or a {{choose:}} names and the fragment does not declare is then
    # nobody's to declare (#39).
    host_declares = mode.template is not False
    body_directives = {
        directive.name
        for _s, _i, block in document.iter_indexed_blocks()
        for text in text_fragments(block)
        for directive in lex_fragment(text).directives
    }

    # ── Validate document_type (§16.6) ──
    doc_type = document.metadata.document_type or "contract"
    if doc_type not in VALID_DOC_TYPES:
        result.error(
            "document-type-invalid",
            f"Invalid document_type '{doc_type}'. Must be one of: contract, unilateral_act, collective_act.",
            line=where.field("document_type"),
        )

    # ── Validate field_types keys (§16.5) ──
    for ft_key in document.metadata.field_types:
        if not IDENTIFIER_RE.fullmatch(ft_key):
            result.error(
                "field-type-key-format",
                f"field_types key '{ft_key}' must match [a-z][a-z0-9-]*.", line=where.key("field_types", ft_key),
            )
        elif ft_key in RESERVED_VALUE_TYPES:
            result.error(
                "field-type-key-reserved",
                f"field_types key '{ft_key}' collides with a reserved value-type "
                f"name (date, money, duration, party, text).", line=where.key("field_types", ft_key),
            )

    # ── Entries of `translations` and `field_types` that another replaces (#126) ──
    # The model reads the keys of these maps as text, stripped, and keeps the last of the keys it reads as
    # one: the same key written twice (`fr` and `"fr"` too), keys that read alike as text (`1` and `'1'`),
    # keys that strip alike (`fr` and `' fr'`). The earlier entry is never checked, and nothing else says so:
    # each is reported at its own line, naming the entry that repeats it (the one written again) or the entry
    # the model keeps. A rule of this implementation
    # (§16.1). An Error when YAML itself rejects the key written twice (strict readers fail the document as
    # `frontmatter-invalid-yaml`); a Warning for different keys the model reads as one, as the specification
    # judges a padded language code a Warning (`language-code-invalid`) and a rule of ours does not fail a
    # document it accepts. Judged against the model, not for one edited since it was parsed.
    if not mode.empty_metadata:
        for map_name, each in where.ignored():
            if (each.by.key in getattr(document.metadata, map_name)) == bool(each.skipped):
                continue
            entry, by = each.entry, each.by
            again = f" at line {by.line}" if by.start != entry.start else ""  # an aliased key is one node
            what = _key_clause(each.why, by.written, "" if each.skipped else again)
            if each.skipped:
                stays = f", and the entry at line {by.line} is skipped ({each.skipped}), so no entry is kept"
            elif each.why != "repeated" and again:
                stays = _key_read(by.line)
            else:
                stays = ""
            report = result.error if each.why == "repeated" else result.warning
            report(FRONTMATTER_KEY_DUPLICATE_RULE, _key_ignored(map_name, entry.written, what, stays), line=entry.line)

    # ── Keys of any other frontmatter mapping that a later key replaces (#131) ──
    # The reader keeps the last of the keys a mapping holds as one and says nothing of the others: a `title:` or
    # a party's `name:` written twice, `1` and `'1'` (read as text outside a question's `default`), `1` and `1.0`
    # (one key in a `default`, whose YAML types stay). Reported as above, at the line of the key that is dropped,
    # under the same rule and levels. `translations` and `field_types` are left to #126; a path names the mapping.
    # A key written over one merged in (`<<`) replaces it, as YAML says, and is no repeat.
    if not mode.empty_metadata:
        paths: dict[str, str] = {}  # the path of each mapping, shown once
        for each in where.repeats():
            at = "" if each.by_line is None else f" at line {each.by_line}"
            what = _key_clause(each.why, each.by_written, at)
            stays = _key_read(each.by_line) if each.why != "repeated" and each.by_line is not None else ""
            report = result.error if each.why == "repeated" else result.warning
            if each.path not in paths:
                # a long path keeps its end, which names the mapping
                cut = each.path if len(each.path) <= _SHOWN_WIDTH else "…" + each.path[1 - _SHOWN_WIDTH:]
                paths[each.path] = _shown(cut) if cut else "frontmatter"
            subject = paths[each.path]
            report(FRONTMATTER_KEY_DUPLICATE_RULE, _key_ignored(subject, each.written, what, stays), line=each.line)

    # ── Metadata dates (§16.6) ──
    for field_name, field_value in (
        ("effective_date", document.metadata.effective_date),
        ("adoption_date", document.metadata.adoption_date),
    ):
        with result.at(where.key(field_name)):
            _check_date_field(field_name, field_value, questions, "metadata-date-invalid", result)

    # ── Language codes and the authoritative language (§3.2, §16.6) ──
    # `language`, `authoritative` and the `translations` keys, when present, are ISO 639-1 codes;
    # `authoritative`, when present, is the document's language or a key. §3.2 gives `language` no
    # default, and the model holds none (`""`): a document that does not say its language has none.
    # A placeholder is the placeholder rule's. A value that is no code is no language, so the
    # comparisons judge only the codes that pass (`authoritative_mismatch`, and the translation
    # rules below): one typo draws one Warning.
    language = meta.language
    def check_code(label: str, code: str, path: tuple[str, ...]) -> None:
        if code not in ISO_639_1_CODES and not named(code, "placeholder"):
            result.warning(
                "language-code-invalid",
                f"The {label} '{code}' must be an ISO 639-1 language code: two lowercase letters, "
                f"such as 'en' or 'cs'.", line=where.key(*path),
            )

    # The keys the reader kept, as it stripped them, at the line of the entry that stays (#126).
    for label, code in (("language", language), ("authoritative", meta.authoritative)):
        if code:
            check_code(label, code, (label,))
    for key in meta.translations:
        check_code("translations key", key, ("translations", key))
    if authoritative_mismatch(meta):
        own_language = f"'{language}'" if language else "not declared"
        keys = ", ".join(f"'{key}'" for key in meta.translations) or "none"
        result.warning(
            "authoritative-not-declared",
            f"authoritative '{meta.authoritative}' is neither the document's language ({own_language}) "
            f"nor a translations key ({keys}).", line=where.key("authoritative"),
        )

    # ── Build party lookup from metadata ──
    seen_side_names: set[str] = set()
    seen_party_names: set[str] = set()
    for side_index, side in enumerate(document.metadata.sides):
        side_path = ("sides", side_index)
        side_name = (side.name or "").strip()
        if side_name:
            if not IDENTIFIER_RE.fullmatch(side_name):
                result.error(
                    "side-party-name-format",
                    f"Side name '{side.name}' must be a lowercase identifier matching "
                    f"'{IDENTIFIER_RE.pattern}'.",
                    line=where.key(*side_path, "name"),
                )
            elif side_name in seen_side_names:
                result.error("side-name-duplicate", f"Duplicate side name '{side_name}'.", line=where.key(*side_path))
            else:
                seen_side_names.add(side_name)
                # §3.6 display derivation: label, else name with hyphens
                # replaced by spaces and each word capitalized.
                result.index.side_lookup[side_name] = (
                    side.label or side_name.replace("-", " ").title()
                )

        for party_index, party in enumerate(side.parties):
            party_path = (*side_path, "parties", party_index)
            party_name = (party.name or "").strip()
            if not party_name:
                continue
            if not IDENTIFIER_RE.fullmatch(party_name):
                result.error(
                    "side-party-name-format",
                    f"Party name '{party.name}' must be a lowercase identifier matching "
                    f"'{IDENTIFIER_RE.pattern}'.",
                    line=where.key(*party_path, "name"),
                )
                continue
            if party_name in seen_party_names:
                result.error(
                    "party-name-duplicate", f"Duplicate party name '{party_name}'.", line=where.key(*party_path)
                )
                continue
            seen_party_names.add(party_name)
            if party.type not in PARTY_TYPES:
                allowed = " or ".join(map(repr, PARTY_TYPES))
                result.error(
                    "party-type-invalid",
                    f"Party '{party_name}' has invalid type '{party.type}'. Must be {allowed}.",
                    line=where.key(*party_path, "type"),
                )
            with result.at(where.key(*party_path, "date_of_birth")):
                _check_date_field(
                    f"Party '{party_name}' date_of_birth",
                    party.date_of_birth,
                    questions,
                    "date-of-birth-invalid",
                    result,
                )
            for rep_index, rep in enumerate(party.representatives):
                if not (rep.name or "").strip():
                    result.error(
                        "representative-name-empty",
                        f"A representative of party '{party_name}' is missing the required name.",
                        line=where.key(*party_path, "representatives", rep_index, "name"),
                    )
            result.index.party_lookup[party_name] = (
                party.label or party.legal_name or party_name
            )

    # ── Document type party/side constraints (§16.6) ──
    # When sides is absent entirely the structural rows cannot be verified:
    # emit a single Warning instead of reporting them as violated (§16.6).
    total_sides = len(document.metadata.sides)
    total_parties = sum(len(s.parties) for s in document.metadata.sides)
    # A count too low is reported at the parties of the first side without
    # any, else at `sides`.
    bare_side = next((k for k, side in enumerate(document.metadata.sides) if not side.parties), None)
    few_parties = where.key("sides", bare_side, "parties") if bare_side is not None else where.key("sides")
    if total_sides == 0:
        # Without frontmatter, frontmatter-absent is reported instead (§16.6).
        if not frontmatter_absent:
            result.warning(
                "sides-absent",
                f"No sides are declared, so the document_type '{doc_type}' "
                f"side/party constraints cannot be verified.",
                line=where.field("document_type"),
            )
    elif doc_type == "contract":
        if total_sides < 2:
            result.error(
                "sides-minimum",
                f"Contracts require at least 2 distinct sides (found {total_sides}).",
                line=where.key("sides"),
            )
        if total_parties < 2:
            result.error(
                "parties-minimum",
                f"Contracts require at least 2 parties (found {total_parties}).",
                line=few_parties,
            )
    elif doc_type in ("unilateral_act", "collective_act"):
        if total_parties < 1:
            result.error(
                "parties-minimum",
                f"Document type '{doc_type}' requires at least 1 party.",
                line=few_parties,
            )
        if "issuer" not in seen_side_names:
            result.error(
                "issuer-side-required",
                f"Document type '{doc_type}' requires a side named 'issuer'.",
                line=where.key("sides", 0),
            )

    # ── Attachment id validation (§16.10) ──
    # An id is unique among attachments that can be present together; an
    # attachment's presence is its `when` condition (§3.9, §15.4).
    attachment_presence: dict[str, list[Presence]] = {}
    for att_index, att in enumerate(document.metadata.attachments):
        att_path = ("attachments", att_index)
        presence = own_presence(att.when, questions)
        if not att.id:
            result.error("anchor-format", "Attachment is missing required 'id'.", line=where.key(*att_path))
        elif not IDENTIFIER_RE.fullmatch(att.id):
            result.error(
                "anchor-format",
                f"Attachment id '{att.id}' must match [a-z][a-z0-9-]*.",
                line=where.key(*att_path, "id"),
            )
        elif _clashes(presence, attachment_presence.get(att.id, []), questions):
            result.error("attachment-id-duplicate", f"Duplicate attachment id '{att.id}'.", line=where.key(*att_path))
        else:
            attachment_presence.setdefault(att.id, []).append(presence)
            result.index.attachment_lookup.setdefault(att.id, att.title or att.id)
        if not att.title:
            result.error(
                "attachment-title-empty",
                f"Attachment '{att.id}' is missing required 'title'.",
                line=where.key(*att_path, "title"),
            )
        if not att.file:
            result.error(
                "attachment-file-missing",
                f"Attachment '{att.id}' is missing required 'file'.",
                line=where.key(*att_path, "file"),
            )
    attachment_ids = set(attachment_presence)
    # Each attachment id's first entry: a collision with it is reported there.
    attachment_entry: dict[str, int] = {}
    for att_index, att in enumerate(document.metadata.attachments):
        attachment_entry.setdefault(att.id, att_index)

    # ── Amendment validation (§16.8) ──
    if document.metadata.amends and not document.metadata.amends.title.strip():
        result.error(
            "amends-title-empty", "amends.title is required when amends is present.",
            line=where.key("amends", "title"),
        )
    supersedes = document.metadata.supersedes
    if isinstance(supersedes, Amends) and not supersedes.title.strip():
        result.error(
            "supersedes-title-empty",
            "supersedes.title is required when supersedes is written as an object (§3.2).",
            line=where.key("supersedes", "title"),
        )

    # ── File references of the frontmatter (§2.3, §16.2) ──
    check_frontmatter_paths(document.metadata, where, file_references)

    # ── Section numbering and identifiers ──
    # Explicit identifiers share the anchor namespace with attachment ids
    # (§5.6). Two declarations of one identifier are duplicates only when
    # they can appear together (§15.4); `anchors` holds each identifier's
    # presences, which are also where a {{ref:}} to it can resolve.
    placed_anchors = [
        found
        for found in markers
        if not found.misplaced and found.marker.identifier and not found.include_only
    ]
    explicit_ids = (
        {s.identifier.strip() for s in document.sections if s.identifier.strip()}
        | {found.marker.identifier for found in placed_anchors}
        | attachment_ids
    )
    free_identifier = _FreeIdentifiers(explicit_ids, questions, exclusivity)
    # Each anchor's declarations, by presence. While sections are numbered,
    # it holds only earlier headings' identifiers.
    anchors = free_identifier.anchors

    counters = [0] * 7
    # Numbers count from the shallowest heading level: a document whose
    # headings all start at ## (an attachment or include file, which has no
    # # heading) numbers them 1, 2, … A level that a heading skips counts
    # as 1 (see below), so no two sections get the same number (#38).
    shallowest = min((min(max(s.level, 1), MAX_SECTION_LEVEL) for s in document.sections), default=1)
    path_stack: list[str] = []
    # The level before the first heading: 0 in a main document, whose first
    # heading must be at level 1 (§4.1). A document without frontmatter may
    # be an include fragment or an attachment file validated on its own,
    # which has no level-1 heading and to which §4.1 does not apply alone
    # (§12): its first heading sets the level. Not so when the frontmatter is
    # there but is not read (#43): it still is a main document's.
    first_level: int | None = None if document.metadata.frontmatter_absent and not mode.check_first_heading else 0
    last_level = first_level
    # The last section at each open level: (identifier, presence). An
    # alternative to it shares its number (§15.8).
    previous_sibling: dict[int, tuple[str, Presence]] = {}
    # Each heading-skip, with the place among the diagnostics it is recorded at (and the
    # diagnostic before that place, which must still be there), until the headings that a
    # fragment between them makes valid in the combined document are known (check_includes,
    # #35): it is recorded then, if it stands, with the line it has here.
    heading_skips: dict[int, tuple[int, Diagnostic | None, str, Line]] = {}

    for section_index, section in enumerate(document.sections):
        # An out-of-range level is an Error, but the section still gets an
        # index entry: `result.index.sections` is positionally paired with
        # `document.sections` by callers (renderers, the editor), so skipping
        # one here would shift every later section's number and drop the last
        # one from rendered output. Numbering clamps into the valid range.
        level = section.level
        if level < 1 or level > MAX_SECTION_LEVEL:
            result.error(
                "heading-depth",
                f"Section '{section.title}' uses unsupported heading level "
                f"{section.level}. LegalDown supports levels 1-{MAX_SECTION_LEVEL} (§4.1).", line=where.heading(section_index),
            )
            level = min(max(level, 1), MAX_SECTION_LEVEL)
        if last_level == 0 and level > 1:
            heading_skips[section_index] = (
                len(result.diagnostics),
                result.diagnostics[-1] if result.diagnostics else None,
                f"Heading levels must not skip. '{section.title}' is at level {level}, but the "
                f"document has no level-1 heading before it.",
                where.heading(section_index),
            )
        elif last_level and level - last_level > 1:
            heading_skips[section_index] = (
                len(result.diagnostics),
                result.diagnostics[-1] if result.diagnostics else None,
                f"Heading levels must not skip. '{section.title}' jumps from level {last_level} to {level}.",
                where.heading(section_index),
            )
        if re.match(
            r"^(article\s+[ivxlcdm]+|\d+(?:\.\d+)*)",
            section.title.strip(),
            re.IGNORECASE,
        ):
            result.warning(
                "heading-hardcoded-number",
                f"Section '{section.title}' appears to include hardcoded numbering. LegalDown headings should be plain text.", line=where.heading(section_index),
            )

        presence = units.presence(section_index)
        identifier = section.identifier.strip()
        if identifier and not IDENTIFIER_RE.fullmatch(identifier):
            result.error(
                "anchor-format",
                f"Identifier '{identifier}' on section '{section.title}' is invalid. Use lowercase letters, numbers, and hyphens only.", line=where.heading(section_index),
            )
            identifier = free_identifier(slugify_identifier(identifier), presence)
        elif identifier and _clashes(presence, anchors.get(identifier, []), questions):
            # Duplicate explicit anchors are an Error (§16.2); the identifier
            # is still adjusted so downstream indices stay usable.
            identifier = free_identifier(identifier, presence)
            result.error(
                "anchor-duplicate",
                f"Duplicate section identifier on '{section.title}'. It was adjusted to "
                f"'{identifier}'.", line=where.heading(section_index),
            )
        elif not identifier:
            base, lossy = generate_identifier(section.title)
            identifier = free_identifier(base, presence)
            if lossy:
                result.warning(
                    "anchor-lossy-slug",
                    f"The identifier generated for '{section.title}' is '{base}': letters or "
                    f"digits without an ASCII form were dropped. Give the heading an explicit "
                    f"identifier (§5.3).", line=where.heading(section_index),
                )
            if identifier != base:
                result.warning(
                    "anchor-autogen-collision",
                    f"Auto-generated identifier on '{section.title}' collides with another "
                    f"identifier. It was adjusted to '{identifier}'; give the heading an "
                    f"explicit identifier (§5.5).", line=where.heading(section_index),
                )
        free_identifier.declare(identifier, presence)

        if _clashes(presence, attachment_presence.get(identifier, []), questions):
            result.error(
                "attachment-id-collision",
                f"Section identifier '{identifier}' collides with an attachment id.",
                line=where.key("attachments", attachment_entry[identifier]),
            )

        sibling_id, sibling_presence = previous_sibling.get(level, ("", ALWAYS))
        alternative = sibling_id == identifier and exclusive(presence, sibling_presence, questions)
        previous_sibling = {lvl: v for lvl, v in previous_sibling.items() if lvl < level}
        previous_sibling[level] = (identifier, presence)
        # A level between this heading and its nearest ancestor that has no
        # heading of its own (a skip, heading-skip) counts as its first:
        # # A, ### B, ## C are 1, 1.1.1, 1.2. Numbers stay unique, except
        # that alternatives, and what they contain, share them (§15.8).
        for idx in range(shallowest, level):
            counters[idx] = counters[idx] or 1
        for idx in range(level, 7):
            if idx == level:
                counters[idx] += 0 if alternative else 1
            elif idx > level:
                counters[idx] = 0
        path_stack = path_stack[: max(level - 1, 0)]
        path_stack.append(identifier)
        number = format_section_number(counters, level)
        entry = SectionIndexEntry(
            title=section.title,
            identifier=identifier,
            path=".".join(path_stack),
            level=level,
            number=number,
            alternative=alternative,
        )
        result.index.sections.append(entry)
        # Alternatives share an identifier: a reference resolves to
        # whichever is present after assembly; the index keeps the first.
        result.index.section_lookup.setdefault(identifier, entry)
        last_level = level

    # ── Markers in the body (§5.7, §12.2, §15.3) ──
    # A marker at the end of a top-level paragraph or of a list item's first
    # paragraph is in a marker position: its #id joins the anchor namespace
    # and resolves to its containing section (the §13.2 enumeration-path
    # refinement is a Rendering-level concern; §6.3 falls back to the
    # section's number). Anything else is literal text (anchor-misplaced).
    for heading_index, title in enumerate(headings):
        for look_alike in marker_matches(title, lex_fragment(title)):
            result.warning(
                "anchor-misplaced",
                f"'{look_alike.group(0)}' in the heading '{title}' is literal text: a heading's "
                f"marker ends it and holds '#id', 'when=condition', or both, each at most "
                f"once (§5.2, §15.3).",
                line=where.heading(heading_index),
            )

    def marker_line(found: FoundMarker) -> int | None:
        return where.find(found.section, found.block, found.source, found.fragment, found.offset)

    for found in markers:
        if not found.placed(template):
            result.warning("anchor-misplaced", f"'{found.source}' is {found.misplaced}.", line=marker_line(found))
        elif found.include_only and found.marker.identifier:
            result.warning(
                "anchor-misplaced",
                f"'#{found.marker.identifier}' in '{found.source}' is ignored: a paragraph "
                f"holding only an {{{{include:}}}} is replaced by its fragment, so it is not a "
                f"reference target. Anchor a heading inside the fragment (§12.2).",
                line=marker_line(found),
            )
    for found in placed_anchors:
        anchor_id = found.marker.identifier
        presence = units.presence(found.section, found.block, found.fragment)
        if not IDENTIFIER_RE.fullmatch(anchor_id):
            result.error(
                "anchor-format",
                f"Anchor '{{#{anchor_id}}}' is invalid. Use lowercase letters, numbers, and hyphens only.", line=marker_line(found),
            )
        elif _clashes(presence, anchors.get(anchor_id, []), questions):
            result.error(
                "anchor-duplicate",
                f"Anchor '{{#{anchor_id}}}' duplicates an existing anchor in the document.", line=marker_line(found),
            )
        elif _clashes(presence, attachment_presence.get(anchor_id, []), questions):
            result.error(
                "attachment-id-collision",
                f"Anchor '{{#{anchor_id}}}' collides with an attachment id.",
                line=where.key("attachments", attachment_entry[anchor_id]),
            )
        else:
            free_identifier.declare(anchor_id, presence)
            result.index.section_lookup.setdefault(anchor_id, result.index.sections[found.section])

    # ── Definitions (§7) ──
    # The mandatory, first-positioned Definitions section is gone (§7.2). A
    # definition is a quoted term followed by a ``{{def: id}}`` anchor, declared
    # either as a leading-anchor "definition" block or inline at first use, and
    # may appear anywhere.
    definition_refs = collect_definitions(
        document, language=meta.language, lex_fragment=lex_fragment
    )
    # The drafting notes of each top-level block (§15.6), worked out once for the validation.
    notes_of_block: dict[tuple[int | None, int], DraftingNotes] = {}

    def drafting_notes(section: int | None, index: int) -> DraftingNotes:
        if (section, index) not in notes_of_block:
            block = document.preamble[index] if section is None else document.sections[section].blocks[index]
            notes_of_block[section, index] = DraftingNotes(quote_ranges(block))
        return notes_of_block[section, index]

    def definition_line(ref: Any) -> int | None:
        if ref.fragment_index is None:  # a definition block's own anchor
            return where.lifted(ref.section_index, ref.block_index, "{{def")
        return where.find(ref.section_index, ref.block_index, "{{def", ref.fragment_index, ref.offset)

    # Each identifier's declarations: (term, auto-generated, presence).
    declared_terms: dict[str, list[tuple[str, bool, Presence]]] = {}
    # The definitions that are registered, and the {{term:}} uses outside
    # drafting notes, for definition-circular (§16.3, #105).
    registered: list[tuple[DefinitionRef, Presence]] = []
    #: The registered definitions and the duplicates among those with a valid id, for
    #: definition-used-before-declaration.
    written: list[tuple[DefinitionRef, Presence]] = []
    term_uses: list[TermUse] = []
    for ref in definition_refs:
        def_id = ref.id
        if not IDENTIFIER_RE.fullmatch(def_id):
            result.error(
                "anchor-format",
                f"Definition identifier '{def_id}' (term '{ref.term}') is invalid. "
                f"Use lowercase letters, numbers, and hyphens only.", line=definition_line(ref),
            )
            continue
        if ref.lossy_id:
            result.warning(
                "def-lossy-slug",
                f"The id generated for the defined term '{ref.term}' is '{def_id}': letters or "
                f"digits without an ASCII form were dropped. Give the definition an explicit "
                f"id (§5.3, §7.2).", line=definition_line(ref),
            )
        presence = units.presence(ref.section_index, ref.block_index, ref.fragment_index)
        written.append((ref, presence))
        # Uniqueness applies between definitions that can appear together
        # (§7.2, §15.4); alternatives may share an identifier.
        together = [
            (term, auto)
            for term, auto, other in declared_terms.get(def_id, [])
            if not exclusive(presence, other, questions)
        ]
        if together:
            if ref.auto_id and any(auto and term != ref.term for term, auto in together):
                result.error(
                    "def-autogen-collision",
                    f"Two definitions auto-generate the same id '{def_id}'. "
                    f"Add an explicit id to disambiguate.", line=definition_line(ref),
                )
            else:
                result.error(
                    "def-duplicate-id",
                    f"Definition identifier '{def_id}' is duplicated.", line=definition_line(ref),
                )
            continue
        declared_terms.setdefault(def_id, []).append((ref.term, ref.auto_id, presence))
        registered.append((ref, presence))
        result.index.definition_lookup.setdefault(def_id, ref.term or id_term(def_id))
    _check_implicit_identifiers(document, definition_refs, where, definition_line, result)

    # ── Definition source-form checks (§7.2 validation table) ──
    for block_section, block_index, block in document.iter_indexed_blocks():
        for fragment_index, fragment in enumerate(text_fragments(block)):
            for anchor in find_definition_anchors(
                fragment, language=meta.language, lexed=lex_fragment(fragment)
            ):
                anchor_line = where.find(
                    block_section, block_index, anchor.directive.source, fragment_index, anchor.directive.start
                )
                if anchor.term is None:
                    result.error(
                        "def-no-quoted-span",
                        "A {{def:}} anchor must immediately follow a quoted defined term.", line=anchor_line,
                    )
                    continue
                if anchor.emphasis:
                    result.warning(
                        "def-emphasis",
                        "Defined term wrapped in emphasis markers in source. Quotation marks "
                        "alone delimit a defined term; emphasis is a render-time style.", line=anchor_line,
                    )
                if anchor.single_quoted:
                    result.warning(
                        "def-single-quote-ambiguous",
                        f"Single-quoted defined term '{anchor.term}' may be ambiguous "
                        f"with an apostrophe (U+2019); prefer double-quote delimiters.", line=anchor_line,
                    )

    # ── Amendment definition import (§7.5) ──
    # Whether the original's definitions were imported: distinct from "imported something", for an
    # original that legitimately declares none still counts as consulted, so a missing term is an
    # Error (§16.8) rather than being downgraded to Info. Not so for a file that is not LegalDown.
    original_imported = False
    _imported_definitions: dict[str, str] = {}
    # What is imported from the files the document names, asked once: the combined pass takes it from the
    # document's own (``Combined.imports``), for an importer is called once however often the document is read.
    imports = combined.imports if combined is not None else {}
    if document.metadata.amends and document.metadata.amends.file:
        amends_file = document.metadata.amends.file
        if amends_file.endswith(LEGALDOWN_EXTENSIONS):
            if "amends" not in imports:
                imports["amends"] = (
                    _imported(import_definitions(amends_file, document.filename)) if import_definitions is not None
                    else _definitions_in(document, files, amends_file, file_references)
                )
            imported = imports["amends"]
            if imported.definitions is not None:
                original_imported = True
                _imported_definitions = imported.definitions
                for def_id in result.index.definition_lookup:
                    if def_id in _imported_definitions:
                        result.warning(
                            "amend-def-override",
                            f"Amendment redefines '{def_id}' which exists in the original document.",
                            line=next(
                                (definition_line(ref) for ref in definition_refs if ref.id == def_id), None
                            ),
                        )
                for def_id, term_text in _imported_definitions.items():
                    result.index.definition_lookup.setdefault(def_id, term_text)
                    # The original is always in force (§7.5), even where
                    # the amendment redefines the term under a condition.
                    declared_terms.setdefault(def_id, []).append((term_text, False, ALWAYS))

    # ── Attachment definition import (§7, §12.4) ──
    # A {{def:}} inside an attachment file registers a document-wide term; ids
    # must remain unique across the combined document (§16.10).
    attachments_unread: list[UnreadReason] = []  # why, of the LegalDown attachment files with none imported
    for att_index, att in enumerate(document.metadata.attachments):
        if not att.file.endswith(LEGALDOWN_EXTENSIONS):
            continue
        if ("attachment", att_index) not in imports:
            imports["attachment", att_index] = (
                _imported(import_attachment_definitions(att.file)) if import_attachment_definitions is not None
                else _definitions_in(document, files, att.file, file_references)
            )
        imported = imports["attachment", att_index]
        if imported.not_imported is not None:
            attachments_unread.append(imported.not_imported)
        att_defs = imported.definitions
        if not att_defs:
            continue
        # Its definitions are present when the attachment is (§15.3).
        presence = own_presence(att.when, questions)
        for def_id, term_text in att_defs.items():
            earlier = [other for _term, _auto, other in declared_terms.get(def_id, [])]
            if _clashes(presence, earlier, questions):
                result.error(
                    "def-duplicate-id",
                    f"Definition id '{def_id}' from attachment '{att.id}' collides with "
                    f"another definition that can appear with it (§16.10, §15.4).",
                    line=where.key("attachments", att_index),
                )
            else:
                declared_terms.setdefault(def_id, []).append((term_text, False, presence))
                result.index.definition_lookup.setdefault(def_id, term_text)

    # ── Inline directive validation ──
    blanks: dict[str, _BlankState] = {}
    referenced_attachments: set[str] = set()

    # ── Frontmatter placeholders (§3.10) ──
    # A {{placeholder:}} is allowed only as a quoted value in *value* fields, not
    # in identifier, structural, or format-checked fields, which a filled-in
    # value could break. Placeholders collected here share the same blank
    # (id, type, currency, unit) with any matching body placeholder.
    for field_label, field_value, field_path in structural_fields:
        if named(field_value, "placeholder"):
            result.error(
                "placeholder-in-structural-field",
                f"A {{{{placeholder:}}}} is not allowed in {field_label}: an identifier, "
                f"structural, or format-checked field; placeholders are only valid in "
                f"value fields (§3.10).",
                line=where.key(*field_path),
            )

    for field_value, field_path in value_fields:
        for directive in named(field_value, "placeholder"):
            field_line = where.key(*field_path)
            with result.at(field_line):
                _check_placeholder(directive, result, blanks, questions, in_frontmatter=True, line=field_line)
    # Every blank, wherever it is, with its line, for the final check (§15.9).
    placeholders: list[tuple[Directive, Line]] = [
        (directive, where.key(*path))
        for text, path in frontmatter_texts
        for directive in named(text, "placeholder")
    ] + [
        (directive, where.heading(index))
        for index, text in enumerate(headings)
        for directive in named(text, "placeholder")
    ]

    chooses: list[tuple[Directive, Line]] = []
    for directive, misplaced, line in misplaced_chooses:
        chooses.append((directive, line))
        result.error("choose-invalid", f"'{directive.source}' is {misplaced} (§15.5).", line=line)
    # The questions a blank or a {{choose:}} asks, outside drafting notes and in them: a note is
    # removed before anything is asked (§15.7.2 step 1), so a question only a note uses needs no
    # answer, and is unused (question-unused). The frontmatter's and the headings' are kept.
    asked = {directive.positional for directive, _line in placeholders + chooses if directive.positional}
    asked_in_notes: set[str] = set()

    # The images that name their file by a link reference definition (#92, #104):
    # the definition's path is judged once, where it is written.
    reference_images = ReferenceImages(document, where, combined)

    # Every {{ref:}}, {{term:}}, and {{attach:}}: (target, presence, in a
    # drafting note, line), for reference safety (§15.4).
    references: dict[str, list[tuple[str, Presence, bool, Line]]] = {"ref": [], "term": [], "attach": []}
    for section_index, block_index, block in document.iter_indexed_blocks():
        # The parser lifts a paragraph's first {{ref:}} or {{term:}} into
        # block fields when they hold it without loss (no other parameters).
        ref_targets: list[tuple[str, Line]] = []
        term_targets: list[tuple[str, Line]] = []
        block_presence = units.presence(section_index, block_index)
        if block.kind in ("ref", "term") and block.target.strip():
            lifted_line = partial(where.lifted, section_index, block_index, "{{" + block.kind)
            (ref_targets if block.kind == "ref" else term_targets).append((block.target.strip(), lifted_line))
            references[block.kind].append((block.target.strip(), block_presence, False, lifted_line))
            if block.kind == "term":
                term_uses.append(
                    TermUse(
                        section_index, block_index, None, block.target.strip(), block_presence,
                        offset=-1, line=lifted_line, before=fragments_before_target(block),
                    )
                )
        notes = drafting_notes(section_index, block_index)
        for fragment_index, (fragment, _position) in enumerate(block_fragments(block)):
            lexed = lex_fragment(fragment)
            owner_of = None  # for definition-circular, once a {{term:}} asks
            has_image = "![" in fragment
            # Only an image or a directive asks, and only a block with a drafting note has one.
            noted = bool(notes) and (has_image or bool(lexed.directives)) and fragment_index in notes
            if has_image:
                check_fragment_images(
                    fragment, lexed, file_references, where, section_index, block_index, fragment_index,
                    reference_images, noted=noted,
                )
            presence = units.presence(section_index, block_index, fragment_index)
            in_note = bool(lexed.directives) and noted
            for offset in lexed.stray_braces:
                result.warning(
                    "brace-stray", BRACE_STRAY,
                    line=where.find(section_index, block_index, "{{", fragment_index, offset),
                )
            for directive in lexed.directives:
                name = directive.name
                # Found only if a diagnostic is made there.
                line = partial(where.find, section_index, block_index, directive.source, fragment_index, directive.start)
                if name == "choose" and not untemplated:
                    chooses.append((directive, line))  # a template even when malformed
                if name in ("choose", "placeholder") and directive.positional:
                    (asked_in_notes if in_note else asked).add(directive.positional)
                if name == "placeholder":
                    placeholders.append((directive, line))
                    # Its effective type decides which parameters it defines.
                    with result.at(line):
                        _check_placeholder(directive, result, blanks, questions, in_note=in_note, line=line)
                    continue
                with result.at(line):
                    well_formed = _check_directive_arguments(directive, result)
                if not well_formed:
                    continue
                # ── Unknown directive names (§11.5): well-formed only ──
                if name not in KNOWN_DIRECTIVES:
                    # It may be a construct of the newer declared version.
                    report = result.warning if newer_version else result.error
                    report(
                        "directive-unknown",
                        f"Unknown directive '{{{{{name}:}}}}'. Renderers replace it "
                        f"with [UNKNOWN DIRECTIVE: {name}] (§11.5).",
                        line=line,
                    )
                    continue
                value = directive.positional or ""
                params = directive.params
                if name in references and value:
                    references[name].append((value, presence, in_note, line))
                    if name == "term" and not in_note:
                        owner_of = owner_of or term_owners(fragment, lexed, meta.language)
                        term_uses.append(
                            TermUse(
                                section_index, block_index, fragment_index, value, presence, owner_of(directive),
                                directive.start, line,
                            )
                        )
                if name in ("ref", "term") and not value:
                    # Whatever the host holds, a reference with no target is wrong.
                    result.error(
                        "ref-broken" if name == "ref" else "term-undefined",
                        f"'{directive.source}' has no target.", line=line,
                    )
                elif name == "ref":
                    ref_targets.append((value, line))
                elif name == "term":
                    term_targets.append((value, line))
                elif name == "date":
                    result.index.values.dates.append(value)
                    if not is_valid_iso_date(value):
                        result.error(
                            "date-invalid",
                            f"Invalid date value '{value}'. Must be a valid ISO 8601 date (YYYY-MM-DD).", line=line,
                        )
                elif name == "money":
                    currency = params.get("currency", "")
                    result.index.values.money.append((value, currency))
                    if not is_valid_money_amount(value):
                        result.error(
                            "money-invalid-amount",
                            f"Invalid money amount '{value}'. Must be a non-negative numeric value.", line=line,
                        )
                    if currency:
                        if currency not in KNOWN_CURRENCIES:
                            result.warning(
                                "money-unknown-currency",
                                f"Unrecognized currency code '{currency}'.", line=line,
                            )
                    else:
                        result.warning(
                            "money-missing-currency",
                            "Money directive without currency parameter.", line=line,
                        )
                elif name == "duration":
                    dur_unit = params.get("unit", "")
                    result.index.values.durations.append((value, dur_unit))
                    if not is_positive_numeric(value):
                        result.error(
                            "duration-invalid-value",
                            f"Invalid duration value '{value}'. Must be a positive integer "
                            "or decimal, with a period as the decimal separator.", line=line,
                        )
                    with result.at(line):
                        _check_duration_unit(dur_unit, result)
                elif name == "party":
                    if not value or not IDENTIFIER_RE.fullmatch(value):
                        result.error(
                            "party-name-malformed",
                            f"Party directive has invalid role value '{value}'. Must match [a-z][a-z0-9-]*.", line=line,
                        )
                    elif value not in result.index.party_lookup:
                        result.error(
                            "party-unknown",
                            f"Party directive references unknown party: '{value}'.", line=line,
                            depends=Depends.FRONTMATTER,
                        )
                elif name == "side":
                    if not value or not IDENTIFIER_RE.fullmatch(value):
                        result.error(
                            "side-name-malformed",
                            f"Side directive has invalid value '{value}'. Must match [a-z][a-z0-9-]*.", line=line,
                        )
                    elif value not in seen_side_names:
                        result.error(
                            "side-unknown",
                            f"Side directive references unknown side: '{value}'.", line=line,
                            depends=Depends.FRONTMATTER,
                        )
                elif name == "field":
                    ftype = params.get("type", "")
                    if not ftype:
                        result.error(
                            "field-type-missing",
                            "Field directive is missing required type parameter.", line=line,
                        )
                    elif not IDENTIFIER_RE.fullmatch(ftype):
                        result.error(
                            "field-type-missing",
                            f"Field type '{ftype}' is invalid — must match [a-z][a-z0-9-]*.", line=line,
                        )
                    elif (
                        document.metadata.field_types
                        and ftype not in document.metadata.field_types
                    ):
                        result.warning(
                            "field-type-undeclared",
                            f"Field type '{ftype}' is not declared in field_types.", line=line,
                        )
                    result.index.values.fields.append((value, ftype))
                elif name == "choose" and not untemplated:
                    with result.at(line):
                        check_choose(directive, questions, result, host_declares=host_declares)
                elif name == "include":
                    # §2.3: a path of the fragment's own, whoever includes it
                    file_references.check(
                        value, "{{include:}}", line,
                        at=(-1 if section_index is None else section_index, block_index, fragment_index, directive.start),
                    )
                elif name == "attach":
                    referenced_attachments.add(value)
                    if value not in attachment_ids:
                        result.error(
                            "attach-undeclared",
                            f"Attachment reference '{{{{attach: {value}}}}}' references undeclared attachment id.", line=line,
                            depends=Depends.FRONTMATTER,
                        )
        for target, line in ref_targets:
            if target in result.index.section_lookup:
                continue
            if target in attachment_ids:
                # §5.6: attachments live in the anchor namespace but are
                # referenced with {{attach:}}, never {{ref:}}.
                result.error(
                    "ref-targets-attachment",
                    f"Reference '{{{{ref: {target}}}}}' targets an attachment id. "
                    f"Use '{{{{attach: {target}}}}}' instead.", line=line,
                    depends=Depends.FRONTMATTER,
                )
            else:
                # A target that is no identifier names nothing in any document
                # (§6.2, §5.2); an identifier that is not here may be a section or an
                # anchor of the including document (#39).
                result.error(
                    "ref-broken", f"Broken section reference: '{target}'.", line=line,
                    depends=Depends.BODY if IDENTIFIER_RE.fullmatch(target) else None,
                )
        for target, line in term_targets:
            result.used_terms.add(target)
            if target not in result.index.definition_lookup:
                if document.metadata.amends:
                    if original_imported:
                        result.error(
                            "amend-term-undefined",
                            f"Undefined term reference: '{target}' (not found in "
                            f"amendment or imported original).", line=line,
                            depends=Depends.FRONTMATTER,  # `amends` is the frontmatter's
                        )
                    else:
                        # Original unavailable or not LegalDown source:
                        # the reference may resolve there (§16.8).
                        result.info(
                            "amend-term-unresolvable",
                            f"Term reference '{target}' is not defined in the "
                            f"amendment; the original document is not available "
                            f"to verify it.", line=line,
                            depends=Depends.FRONTMATTER,
                        )
                else:
                    # As for ref-broken: a term is defined by an id (§7.2).
                    result.error(
                        "term-undefined", f"Undefined term reference: '{target}'.", line=line,
                        depends=Depends.BODY if IDENTIFIER_RE.fullmatch(target) else None,
                    )

    _check_blank_codes(blanks, result)
    result.index.blanks = {}
    for pid, state in blanks.items():
        consistent = not state.mixed and len(state.codes - {""}) < 2
        result.index.blanks[pid] = Blank(
            id=pid,
            type=state.type,
            fixed=(_fixed_by_all(state.codes) or "") if consistent else "",
            in_frontmatter=state.in_frontmatter,
            consistent=consistent,
        )

    # ── Templates (§15) ──
    # Every condition in a condition position (§15.3): on what, as written,
    # and its line.
    conditions: list[tuple[str, str, int | None]] = [
        (f"the heading '{section.title}'", section.condition, where.heading(index))
        for index, section in enumerate(document.sections)
        if section.condition and not untemplated
    ]
    conditions.extend(
        (f"'{found.source}'", found.marker.condition, marker_line(found))
        for found in markers
        if found.marker
        and found.marker.condition
        and found.placed(template)
        and not untemplated
    )
    conditions.extend(
        (f"attachment '{att.id}'", att.when, where.key("attachments", index, "when"))
        for index, att in enumerate(meta.attachments)
        if att.when
    )
    for place, text, line in conditions:
        issue = condition_issue(text, questions)
        if issue:
            depends, problem = issue
            result.error(
                "condition-invalid", f"The condition '{text}' on {place}: {problem} (§15.3).",
                line=line, depends=depends if host_declares else None,
            )
    _check_never_true(document, markers, units, questions, result, where)

    # Circular definitions (§16.3, §7.2): one Warning for each set of
    # definitions explained in terms of each other, where the last of them, in
    # document order, is declared. Alternatives (§15.4) that cannot appear
    # together do not refer to each other; only a template has them.
    for circle in find_circular_definitions(
        document,
        registered,
        term_uses,
        exclusivity if template else None,
    ):
        result.warning(
            "definition-circular",
            circular_message(registered[number][0].id for number in circle),
            line=definition_line(registered[circle[-1]][0]),
        )

    # Definitions used before declaration (§16.3, Info): one for each definition, at its first
    # earlier use. The original an amendment amends is in force before the document (§7.5).
    for number, earlier in used_before_declaration(
        written, term_uses, _imported_definitions, exclusivity if template else None
    ):
        ref = written[number][0]
        declared = definition_line(ref)
        times = "once" if len(earlier) == 1 else f"{len(earlier)} times"
        at = f" at line {declared}" if declared else ""
        use = earlier[0].line
        if combined is not None and declared:
            # The declaration is in another file than the use when the lines name another: say which.
            use = use() if callable(use) else use
            file = declared.file if isinstance(declared, Spot) else document.filename
            if file != (use.file if isinstance(use, Spot) else document.filename):
                at += f" of {file or 'the document'}"
        result.info(
            "definition-used-before-declaration",
            f"Definition '{ref.id}' is used {times} before its declaration{at} (§7.2).",
            line=use,
        )

    # Reference safety (§15.4): a reference resolves in every assembled
    # document it is in. References in drafting notes are exempt: assembly
    # removes every note. An amendment's terms are not checked when its
    # original was not read: the original may define them (§16.8).
    original_unread = bool(meta.amends) and not original_imported
    term_presences = {
        def_id: [presence for _term, _auto, presence in declarations]
        for def_id, declarations in declared_terms.items()
    }
    targets_of = {"ref": anchors, "term": term_presences, "attach": attachment_presence}
    covered: dict[tuple[str, str, Presence], bool] = {}  # references of one target and presence are alike
    for kind, uses in references.items():
        for target, presence, in_note, line in uses:
            declared = targets_of[kind].get(target)
            if in_note or not declared or (kind == "term" and original_unread):
                continue
            if (kind, target, presence) not in covered:
                covered[kind, target, presence] = always_covered(presence, declared, questions)
            if covered[kind, target, presence]:
                continue
            result.error(
                "condition-reference-unsafe",
                f"'{{{{{kind}: {target}}}}}' can be present when '{target}' is not: under some "
                f"answers that keep the reference, no declaration of its target remains (§15.4).",
                line=line,
            )

    used_questions = asked | {
        condition.question
        for _place, text, _line in conditions
        if (condition := parse_condition(text)) is not None
    }
    # A question may be used in an include fragment or a LegalDown attachment
    # file. The files are read for their shape and what a template may hold in
    # them (#35), not for the questions they use, so none is unused for certain.
    # Not so in the combined document, which holds the fragments (but not the
    # attachment files, #117).
    reads_other_files = ("include" in body_directives and combined is None) or any(
        att.file.endswith(LEGALDOWN_EXTENSIONS) for att in meta.attachments
    )
    if isinstance(questions, dict) and not reads_other_files:
        for qid in questions:
            if qid not in used_questions:
                result.warning(
                    "question-unused",
                    f"Question '{qid}' is declared but no placeholder, condition, or "
                    f"{{{{choose:}}}} uses it (§15.2)"
                    + (
                        "; only drafting notes do, so it is no blank of the document and needs no answer "
                        "(§15.7.2), though assembly still lists it among the template's questions."
                        if qid in asked_in_notes else "."
                    ),
                    line=where.key("questions", qid),
                )

    check_questions(
        questions,
        blanks,
        result,
        template=template,
        not_line_editable=meta.not_line_editable,
        where=where,
    )
    body = check_template_body(
        document, lex_fragment, result, template=template, where=where, markers=markers, combined=combined,
        cache=imports,
    )
    _check_raw_html(document, lex_fragment, result, where)
    check_heading_images(document, lex_fragment, file_references, where, reference_images)
    reference_images.check(file_references)
    file_references.finish()
    # A fragment on its own is asked what a template's fragment must hold, when its host is
    # one (§15.3, §16.12); the shape of its own text was checked at the start.
    if mode.fragment and mode.template is True:
        for finding in content_findings(document, where, body, markers, mode.kind, noted=True):
            result.error(finding.rule, finding.message, line=finding.line)
    # ── Includes (§12.2, §16.11) ──
    # (Not in the combined document: the includes are the document's, and judged there.)
    included = IncludeOutcome() if combined is not None else check_includes(
        document, body.includes, result, where,
        judged=file_references.reported,
        files=None if mode.fragment else files,
        template=template,
        first_level=first_level,
        units=units,
        questions=questions,
        exclusivity=exclusivity,
        lex_fragment=lex_fragment,
    )
    # §4.1 judges the combined document (§12.2): a level a fragment fills is not skipped. The
    # rest are recorded where the loop met them, the last first so that the places hold. That
    # needs the diagnostics since then only to have been added to. If one was moved or removed
    # (nothing does today), the place is not known: the skip is still reported, at the end.
    for index, (position, before, message, line) in reversed(heading_skips.items()):
        if index in included.filled:
            continue
        if not ((position == 0 and before is None) or (0 < position <= len(result.diagnostics) and result.diagnostics[position - 1] is before)):
            position = len(result.diagnostics)
        result.error_at(position, "heading-skip", message, line=line)
    if final:
        _check_final(placeholders, chooses, body.notes, conditions, questions, result, where.key("questions"))

    # Warn about declared but unreferenced attachments (§16.10): once per id,
    # which alternatives share (§15.3).
    first_entries = {}
    for index, att in enumerate(document.metadata.attachments):
        first_entries.setdefault(att.id, index)
    for att_id, index in first_entries.items():
        if att_id and att_id not in referenced_attachments:
            result.warning(
                "attachment-unreferenced",
                f"Attachment '{att_id}' is declared but never referenced via {{{{attach:}}}}.",
                line=where.key("attachments", index),
            )

    # Warn about declared but never-referenced definitions (§7). May
    # false-positive when §7.4 automatic term recognition is enabled.
    _warned_defs: set[str] = set()
    for ref in definition_refs:
        if ref.id in _warned_defs:
            continue
        if ref.id in result.index.definition_lookup and ref.id not in result.used_terms:
            _warned_defs.add(ref.id)
            result.warning(
                "def-unreferenced",
                f"Definition '{ref.id}' is declared but never referenced via {{{{term:}}}}.",
                line=definition_line(ref),
                # A fragment's definitions are document-wide (§7.2, §12.2): the
                # including document may well reference them (#39).
                depends=Depends.BODY,
            )

    # What a renderer builds from: the template decision and the markers
    # that apply, as every check above read them.
    result.index.is_template = template
    if combined is not None:
        return result  # (the findings only: the index is the document's own)
    result.index.placed_markers = _placed_markers(document, markers, template, marker_line)

    # §16.11: the document with the fragments its includes take in, validated as the one document it is
    # (#117). What it says is the document's findings, but for those only the document's own pass can
    # make (``combined.HOST_ONLY``).
    if included.combined is not None:
        included.combined.template = template
        included.combined.imports = imports
        included.combined.definition_reads = reference_images.reads
        whole = _check(
            included.combined.document, final, resolve, import_definitions, import_attachment_definitions, mode,
            document_root, combined=included.combined,
        )
        assert whole.places is not None and len(whole.places) == len(whole.diagnostics)
        result.diagnostics = merge_diagnostics(result.diagnostics, whole.diagnostics, whole.places, included.combined)
        result.combined = True

    # §17.5: a Warning for each check category the constructs of the document call for and this
    # validation did not perform, last, once all that was read and checked is known.
    for category in skipped_categories(
        document, where,
        includes=body.includes,
        outcome=included,
        image=file_references.first_image,
        template=template,
        fragment=mode.fragment,
        attachments_unread=attachments_unread,
        amends_unread=not original_imported,
    ):
        result.warning(SKIPPED_RULE, category.message, line=category.line)

    # A finding in a fragment the document includes has its file already (#35).
    if document.filename:
        result.diagnostics = [d if d.file else replace(d, file=document.filename) for d in result.diagnostics]
    return result


def _placed_markers(
    document: Document,
    markers: list[FoundMarker],
    template: bool,
    line: Callable[[FoundMarker], int | None],
) -> list[PlacedMarker]:
    """The markers of *markers* that apply (``PlacedMarker``)."""
    from ..definitions import list_fragments  # see the import note in _validate

    placed = []
    lists: dict[tuple[int | None, int], list[tuple[str, bool, tuple[int, ...]]]] = {}  # each list walked once
    for found in markers:
        if found.marker is None or not found.placed(template):
            continue
        blocks = document.preamble if found.section is None else document.sections[found.section].blocks
        block = blocks[found.block]
        items: tuple[int, ...] = ()
        if block.kind in LIST_KINDS:
            key = (found.section, found.block)
            if key not in lists:
                lists[key] = list_fragments(block)
            items = lists[key][found.fragment][2]
        placed.append(PlacedMarker(
            section=found.section,
            block=found.block,
            fragment=found.fragment,
            offset=found.offset,
            source=found.source,
            identifier="" if found.include_only else found.marker.identifier,
            condition=found.marker.condition,
            field="suffix" if block.kind in ("ref", "term") else "text",
            item=items[-1] if items else None,
            include_only=found.include_only,
            line=line(found),
        ))
    return placed
