"""Core validation logic for LegalDown documents.

Every diagnostic is recorded under the **stable rule id** the specification
assigns to its check (§16.1) through the recorder's ``error/warning/info``,
so tooling can filter, suppress, or escalate specific rules consistently
across implementations (§16.9).
"""
from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Iterator
from dataclasses import replace
from functools import cache, partial
from typing import Any

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
from ..files import LoadFile, file_loader, relative_path, within
from ..markdown import HTML_COMMENT_RE, INLINE_HTML_RE, is_comment_only
from ..models import LIST_KINDS, Amends, Document
from ..positions import Locator
from ..specification import SPEC_VERSION, parse_version
from .conditions import ALWAYS, Presence, always_covered, condition_problem, exclusive, parse_condition, satisfiable
from .helpers import (
    format_section_number,
    generate_identifier,
    is_positive_numeric,
    is_valid_iso_date,
    is_valid_money_amount,
    slugify_identifier,
)
from .patterns import (
    DURATION_UNITS,
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    LEGALDOWN_EXTENSIONS,
    RESERVED_VALUE_TYPES,
    VALID_DOC_TYPES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import Line, PlacedMarker, SectionIndexEntry, ValidationResult, _Recorder
from .templates import (
    BRACE_STRAY,
    DECISION_QUESTION_TYPES,
    Blank,
    Quote,
    block_quotes,
    check_choose,
    check_questions,
    check_template_body,
    question_type,
)
from .units import FoundMarker, Units, find_markers, marker_matches, own_presence

# Type aliases for the optional definitions-import callbacks.
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


def _check_placeholder(
    directive: Directive,
    result: _Recorder,
    blanks: dict[str, Blank],
    questions: Any,
    *,
    in_frontmatter: bool = False,
    line: Line = None,
) -> None:
    """Validate one ``{{placeholder:}}`` directive, its arguments included,
    and record it in *blanks*; *line* is where it is.

    Shared by the body-block scan and the frontmatter scan so a placeholder id
    used in both is treated as the *same* blank (consistent type, currency,
    and unit, §3.10, §10.7).
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
    blank = blanks.setdefault(pid, Blank())
    blank.in_frontmatter |= in_frontmatter
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
    if blank.type is None:
        blank.type = ptype
    if blank.type != ptype:
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


def _check_blank_codes(blanks: dict[str, Blank], result: _Recorder) -> None:
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
            "placeholder-unfilled",
            f"'{directive.source}' is an unfilled blank in a document meant to be final (§15.9).",
            line=line,
        )

    def construct(what: str, line: int | None) -> None:
        result.error(
            "template-construct-present",
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


def _clashes(presence: Presence, others: list[Presence], questions: Any) -> bool:
    """True if a declaration with *presence* can appear together with one of
    the earlier declarations of the same identifier (§15.4)."""
    return any(not exclusive(presence, other, questions) for other in others)


# A link's pointy-bracket destination, ``](<…>)``, then an optional title and
# the closing parenthesis: no raw HTML (CommonMark).
_LINK_DESTINATION_RE = re.compile(r"""<[^<>\n]*>[ \t]*(?:(?:"[^"]*"|'[^']*'|\([^()]*\))[ \t]*)?\)""")


def _inline_html(text: str, lexed: Lexed) -> str | None:
    """The first inline raw HTML in *text* other than a comment (§8.7), as
    written, or None: outside code spans, comments and directives (``lex``),
    not after a backslash, and not a link's ``<…>`` destination."""
    # What the lexer blanked, a comment or a code span, is filled with a
    # character no tag holds outside a quoted value: no tag runs across it.
    view = "".join(
        "`" if seen == " " and written not in " \t\n" else seen
        for seen, written in zip(mask_directives(lexed.view, lexed.directives), text, strict=True)
    )
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
        before = at
        while before > 0 and view[before - 1] in " \t":
            before -= 1
        if (
            opened >= 0 and view[before - 2:before] == "]("
            and (destination := _LINK_DESTINATION_RE.match(view, at))
        ):
            pos = scanned = destination.end()
            opened = -1
            continue
        if tag := INLINE_HTML_RE.match(view, at):
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
                source = HTML_COMMENT_RE.sub("", block.text)
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


def validate(
    document: Document,
    *,
    final: bool = False,
    resolve: LoadFile | None = None,
    import_definitions: DefinitionsImporter | None = None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None = None,
) -> ValidationResult:
    """Validate a LegalDown document and build lookup indices.

    Parameters
    ----------
    document:
        A ``Document``, from ``load`` or ``parse``.
    final:
        Apply the final check (§15.9): the document is meant for signature,
        so a remaining blank or template construct is an Error.
    resolve:
        Reads a file the document refers to — the document it amends
        (``amends.file``, §7.5) and its LegalDown attachment files (§12.4) —
        for the definitions they declare: ``(relative_path) -> text | None``,
        with ``None`` for a file that is not there or cannot be read. By
        default the files are read from the directory of ``document.path``
        (``file_loader``), as far as that is within it (§2.3); a document
        without a ``path`` (from ``parse``) has none, so the checks that need
        them are skipped, as they are with ``resolve=lambda path: None``, for
        a loaded document that is to be validated without reading anything.
        Only the referenced file's own definitions are read, not those it
        refers to in turn.
    import_definitions, import_attachment_definitions:
        Deprecated since 0.4.0, removed in 0.5.0: use *resolve*. They give the
        definitions themselves, which *resolve* leaves to the validator.
    """
    resolve = _resolver(document, resolve, import_definitions, import_attachment_definitions, stacklevel=3)
    return _validate(document, final, resolve, import_definitions, import_attachment_definitions)


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
        DeprecationWarning,
        stacklevel=2,
    )
    resolve = _resolver(document, None, import_definitions, import_attachment_definitions, stacklevel=3)
    return _validate(document, final, resolve, import_definitions, import_attachment_definitions)


def _resolver(
    document: Document,
    resolve: LoadFile | None,
    import_definitions: DefinitionsImporter | None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None,
    *,
    stacklevel: int,
) -> LoadFile | None:
    """How the validation reads the files *document* refers to: *resolve*, else
    the directory of its path, else not at all.
    Says the importers are deprecated when one is given, and then reads nothing
    by itself: each importer answers for its own files, as it always did."""
    if import_definitions is not None or import_attachment_definitions is not None:
        warnings.warn(
            "import_definitions and import_attachment_definitions are deprecated since 0.4.0 "
            "and will be removed in 0.5.0; use validate(resolve=), which reads the files itself",
            DeprecationWarning,
            stacklevel=stacklevel,
        )
        return resolve
    if resolve is None and document.path is not None:
        resolve = file_loader(document.path.parent)

    return resolve


def _definitions_in(document: Document, resolve: LoadFile | None, path: str) -> dict[str, str] | None:
    """The definitions the file at *path* declares (``{id: term}``), or None
    when it cannot be read as a LegalDown document: not there, no *resolve*,
    or frontmatter that cannot be read (that file's problem, not this
    document's). Any other exception is the parser's fault, as it is anywhere."""
    # Imported here for the reason given in ``_validate``.
    from ..definitions import collect_definitions, definition_lookup
    from ..parser import FrontmatterError, parse

    # Asked for as assembly asks (§15.7): normalized, and only a path within
    # the document's directory (§2.3).
    relative = relative_path(path)
    if resolve is None or relative is None:
        return None
    path = relative
    # A document that names itself has nothing to add to itself.
    if document.path is not None and within(document.path.parent, path) == document.path.resolve():
        return None
    text = resolve(path)
    if text is None:
        return None
    try:
        other = parse(text)
    except FrontmatterError:
        return None
    return definition_lookup(collect_definitions(other))


def _validate(
    document: Document,
    final: bool,
    resolve: LoadFile | None,
    import_definitions: DefinitionsImporter | None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None,
) -> ValidationResult:
    result = _Recorder()
    # Where each part is, for the line of each diagnostic (§16.9).
    where = Locator(document)
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
    # Without frontmatter a document is valid, but has no metadata to
    # check: it draws this Warning alone (§3.2, §16.6).
    frontmatter_absent = document.metadata.frontmatter_absent
    if frontmatter_absent:
        result.warning(
            "frontmatter-absent",
            "The document has no frontmatter (§3.1), so it has no title, parties, or other "
            "metadata. Frontmatter is a mapping of YAML fields between a '---' line at the "
            "very start and a closing '---' line.",
            line=where.start(),
        )
    title = document.metadata.title.strip()
    if not title and not frontmatter_absent:
        result.error("title-missing", "The document title is required.", line=where.field("title"))

    # Imported here to avoid a module-level import cycle (definitions ->
    # validator.helpers -> validator/__init__ -> core).
    from ..definitions import (
        block_fragments,
        collect_definitions,
        find_definition_anchors,
        id_term,
        text_fragments,
    )

    # Each text is lexed once per validation and shared by the passes below.
    lex_fragment = cache(lex)
    meta = document.metadata
    questions = meta.questions
    structural_fields, value_fields = _frontmatter_fields(meta, where)
    frontmatter_texts = [(text, path) for _label, text, path in structural_fields] + value_fields
    headings = [section.title for section in document.sections]

    def named(text: str, name: str) -> list[Directive]:
        """The *name* directives in a frontmatter value or heading, each text
        lexed once per validation."""
        return [d for d in lex_fragment(text or "").directives if d.name == name]

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

    # ── Templates and the presence of units (§15.1, §15.3) ──
    # Markers are found first: only a template gives a preamble paragraph's
    # condition its place (§5.7).
    markers = find_markers(document, lex_fragment)
    template = _is_template(document, markers, lex_fragment)
    units = Units(document, markers, questions, template=template)
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

    # ── Metadata dates (§16.6) ──
    for field_name, field_value in (
        ("effective_date", document.metadata.effective_date),
        ("adoption_date", document.metadata.adoption_date),
    ):
        with result.at(where.key(field_name)):
            _check_date_field(field_name, field_value, questions, "metadata-date-invalid", result)

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
            if party.type not in ("legal_entity", "natural_person"):
                result.error(
                    "party-type-invalid",
                    f"Party '{party_name}' has invalid type '{party.type}'. Must be 'legal_entity' or 'natural_person'.",
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
    # Each anchor's declarations, by presence. While sections are numbered,
    # it holds only earlier headings' identifiers.
    anchors: dict[str, list[Presence]] = {}

    def free_identifier(base: str, presence: Presence) -> str:
        """The lowest suffix of *base* — none, -2, -3, … — not taken by an
        explicit identifier or by an earlier heading that can appear with
        this one (§5.5)."""
        candidate, suffix = base, 1
        while candidate in explicit_ids or _clashes(presence, anchors.get(candidate, []), questions):
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    counters = [0] * 7
    # Numbers count from the shallowest heading level: a document whose
    # headings all start at ## (an attachment or include file, which has no
    # # heading) numbers them 1, 2, … A level that a heading skips counts
    # as 1 (see below), so no two sections get the same number (#38).
    shallowest = min((min(max(s.level, 1), 5) for s in document.sections), default=1)
    path_stack: list[str] = []
    # The level before the first heading: 0 in a main document, whose first
    # heading must be at level 1 (§4.1). A document without frontmatter may
    # be an include fragment or an attachment file validated on its own,
    # which has no level-1 heading and to which §4.1 does not apply alone
    # (§12): its first heading sets the level.
    last_level: int | None = None if document.metadata.frontmatter_absent else 0
    # The last section at each open level: (identifier, presence). An
    # alternative to it shares its number (§15.8).
    previous_sibling: dict[int, tuple[str, Presence]] = {}

    for section_index, section in enumerate(document.sections):
        # An out-of-range level is an Error, but the section still gets an
        # index entry: `result.index.sections` is positionally paired with
        # `document.sections` by callers (renderers, the editor), so skipping
        # one here would shift every later section's number and drop the last
        # one from rendered output. Numbering clamps into the valid range.
        level = section.level
        if level < 1 or level > 5:
            result.error(
                "heading-depth",
                f"Section '{section.title}' uses unsupported heading level "
                f"{section.level}. LegalDown supports levels 1-5 (§4.1).", line=where.heading(section_index),
            )
            level = min(max(level, 1), 5)
        if last_level == 0 and level > 1:
            result.error(
                "heading-skip",
                f"Heading levels must not skip. '{section.title}' is at level {level}, but the "
                f"document has no level-1 heading before it.", line=where.heading(section_index),
            )
        elif last_level and level - last_level > 1:
            result.error(
                "heading-skip",
                f"Heading levels must not skip. '{section.title}' jumps from level {last_level} to {level}.", line=where.heading(section_index),
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
        anchors.setdefault(identifier, []).append(presence)

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
            anchors.setdefault(anchor_id, []).append(presence)
            result.index.section_lookup.setdefault(anchor_id, result.index.sections[found.section])

    # ── Definitions (§7) ──
    # The mandatory, first-positioned Definitions section is gone (§7.2). A
    # definition is a quoted term followed by a ``{{def: id}}`` anchor, declared
    # either as a leading-anchor "definition" block or inline at first use, and
    # may appear anywhere.
    definition_refs = collect_definitions(
        document, language=document.metadata.language, lex_fragment=lex_fragment
    )
    def definition_line(ref: Any) -> int | None:
        if ref.fragment_index is None:  # a definition block's own anchor
            return where.lifted(ref.section_index, ref.block_index, "{{def")
        return where.find(ref.section_index, ref.block_index, "{{def", ref.fragment_index, ref.offset)

    # Each identifier's declarations: (term, auto-generated, presence).
    declared_terms: dict[str, list[tuple[str, bool, Presence]]] = {}
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
        result.index.definition_lookup.setdefault(def_id, ref.term or id_term(def_id))

    # ── Definition source-form checks (§7.2 validation table) ──
    for block_section, block_index, block in document.iter_indexed_blocks():
        for fragment_index, fragment in enumerate(text_fragments(block)):
            for anchor in find_definition_anchors(
                fragment, language=document.metadata.language, lexed=lex_fragment(fragment)
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
    _amends_is_legaldown = False
    # Distinct from "imported something": an original that legitimately
    # declares no definitions still counts as consulted, so a missing term is
    # an Error (§16.8) rather than being downgraded to Info.
    _amends_import_succeeded = False
    _imported_definitions: dict[str, str] = {}
    if document.metadata.amends and document.metadata.amends.file:
        amends_file = document.metadata.amends.file
        if amends_file.endswith(LEGALDOWN_EXTENSIONS):
            _amends_is_legaldown = True
            if import_definitions is not None:
                imported = import_definitions(amends_file, document.filename)
            else:
                imported = _definitions_in(document, resolve, amends_file)
            if imported is not None:
                _amends_import_succeeded = True
                _imported_definitions = imported
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
    for att_index, att in enumerate(document.metadata.attachments):
        if not att.file.endswith(LEGALDOWN_EXTENSIONS):
            continue
        if import_attachment_definitions is not None:
            att_defs = import_attachment_definitions(att.file)
        else:
            att_defs = _definitions_in(document, resolve, att.file)
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
    blanks: dict[str, Blank] = {}
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
        notes = [quote for quote in block_quotes(block) if quote.is_drafting_note]
        for fragment_index, (fragment, _position) in enumerate(block_fragments(block)):
            lexed = lex_fragment(fragment)
            presence = units.presence(section_index, block_index, fragment_index)
            for offset in lexed.stray_braces:
                result.warning(
                    "brace-stray", BRACE_STRAY,
                    line=where.find(section_index, block_index, "{{", fragment_index, offset),
                )
            for directive in lexed.directives:
                name = directive.name
                # Found only if a diagnostic is made there.
                line = partial(where.find, section_index, block_index, directive.source, fragment_index, directive.start)
                if name == "choose":
                    chooses.append((directive, line))  # a template even when malformed
                if name == "placeholder":
                    placeholders.append((directive, line))
                    # Its effective type decides which parameters it defines.
                    with result.at(line):
                        _check_placeholder(directive, result, blanks, questions, line=line)
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
                    in_note = any(fragment_index in note.fragments for note in notes)
                    references[name].append((value, presence, in_note, line))
                if name in ("ref", "term") and not value:
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
                elif name == "choose":
                    with result.at(line):
                        check_choose(directive, questions, result)
                elif name == "attach":
                    referenced_attachments.add(value)
                    if value not in attachment_ids:
                        result.error(
                            "attach-undeclared",
                            f"Attachment reference '{{{{attach: {value}}}}}' references undeclared attachment id.", line=line,
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
                )
            else:
                result.error("ref-broken", f"Broken section reference: '{target}'.", line=line)
        for target, line in term_targets:
            result.used_terms.add(target)
            if target not in result.index.definition_lookup:
                if document.metadata.amends:
                    if _amends_is_legaldown and _amends_import_succeeded:
                        result.error(
                            "amend-term-undefined",
                            f"Undefined term reference: '{target}' (not found in "
                            f"amendment or imported original).", line=line,
                        )
                    else:
                        # Original unavailable or not LegalDown source:
                        # the reference may resolve there (§16.8).
                        result.info(
                            "amend-term-unresolvable",
                            f"Term reference '{target}' is not defined in the "
                            f"amendment; the original document is not available "
                            f"to verify it.", line=line,
                        )
                else:
                    result.error(
                        "term-undefined", f"Undefined term reference: '{target}'.", line=line
                    )

    _check_blank_codes(blanks, result)

    # ── Templates (§15) ──
    # Every condition in a condition position (§15.3): on what, as written,
    # and its line.
    conditions: list[tuple[str, str, int | None]] = [
        (f"the heading '{section.title}'", section.condition, where.heading(index))
        for index, section in enumerate(document.sections)
        if section.condition
    ]
    conditions.extend(
        (f"'{found.source}'", found.marker.condition, marker_line(found))
        for found in markers
        if found.marker
        and found.marker.condition
        and found.placed(template)
    )
    conditions.extend(
        (f"attachment '{att.id}'", att.when, where.key("attachments", index, "when"))
        for index, att in enumerate(meta.attachments)
        if att.when
    )
    for place, text, line in conditions:
        problem = condition_problem(text, questions)
        if problem:
            result.error("condition-invalid", f"The condition '{text}' on {place}: {problem} (§15.3).", line=line)
    _check_never_true(document, markers, units, questions, result, where)

    # Reference safety (§15.4): a reference resolves in every assembled
    # document it is in. References in drafting notes are exempt: assembly
    # removes every note. An amendment's terms are not checked when its
    # original was not read: the original may define them (§16.8).
    original_unread = bool(meta.amends) and not (_amends_is_legaldown and _amends_import_succeeded)
    term_presences = {
        def_id: [presence for _term, _auto, presence in declarations]
        for def_id, declarations in declared_terms.items()
    }
    targets_of = {"ref": anchors, "term": term_presences, "attach": attachment_presence}
    for kind, uses in references.items():
        for target, presence, in_note, line in uses:
            declared = targets_of[kind].get(target)
            if in_note or not declared or (kind == "term" and original_unread):
                continue
            if always_covered(presence, declared, questions):
                continue
            result.error(
                "condition-reference-unsafe",
                f"'{{{{{kind}: {target}}}}}' can be present when '{target}' is not: under some "
                f"answers that keep the reference, no declaration of its target remains (§15.4).",
                line=line,
            )

    used_questions = (
        {directive.positional for directive, _line in placeholders if directive.positional}
        | {
            condition.question
            for _place, text, _line in conditions
            if (condition := parse_condition(text)) is not None
        }
        | {directive.positional for directive, _line in chooses if directive.positional}
    )
    # A question may be used in an include fragment or a LegalDown attachment
    # file, which a single-document validator does not read (Full, §17.4).
    reads_other_files = "include" in body_directives or any(
        att.file.endswith(LEGALDOWN_EXTENSIONS) for att in meta.attachments
    )
    if isinstance(questions, dict) and not reads_other_files:
        for qid in questions:
            if qid not in used_questions:
                result.warning(
                    "question-unused",
                    f"Question '{qid}' is declared but no placeholder, condition, or "
                    f"{{{{choose:}}}} uses it (§15.2).",
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
    notes = check_template_body(document, lex_fragment, result, template=template, where=where)
    _check_raw_html(document, lex_fragment, result, where)
    if final:
        _check_final(placeholders, chooses, notes, conditions, questions, result, where.key("questions"))

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
            )

    # What a renderer builds from: the template decision and the markers
    # that apply, as every check above read them.
    result.index.is_template = template
    result.index.placed_markers = _placed_markers(document, markers, template, marker_line)

    if document.filename:
        result.diagnostics = [replace(d, file=document.filename) for d in result.diagnostics]
    return result.result()


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
