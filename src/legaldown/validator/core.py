"""Core validation logic for LegalDown documents.

Every diagnostic is recorded under the **stable rule id** the specification
assigns to its check (§16.1) via ``ValidationResult.error/warning/info``,
so tooling can filter, suppress, or escalate specific rules consistently
across implementations (§16.9).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from functools import cache
from typing import Any

from ..directives import (
    DIRECTIVE_PARAMS,
    KNOWN_DIRECTIVES,
    PLACEHOLDER_TYPE_PARAMS,
    Directive,
    iter_directives,
    lex,
)
from ..models import Amends, Document
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
from .result import SectionIndexEntry, ValidationResult
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


def _frontmatter_fields(meta: Any) -> tuple[list[tuple[str, str]], list[str]]:
    """The frontmatter fields that may hold text: ``(label, value)`` of each
    identifier, structural, or format-checked field, where a placeholder is
    not allowed, and the value fields, where it is (§3.10)."""
    structural: list[tuple[str, str]] = [
        ("document_type", meta.document_type),
        ("legaldown", meta.legaldown),
        ("language", meta.language),
        ("authoritative", meta.authoritative),
    ]
    for side in meta.sides:
        structural.append((f"side name '{side.name}'", side.name))
        for party in side.parties:
            structural.append((f"party name '{party.name}'", party.name))
            structural.append((f"party type for '{party.name}'", party.type))
    for code, path in meta.translations.items():
        structural.append(("a translations language code", code))
        structural.append((f"translations file for '{code}'", path))
    for key, description in meta.field_types.items():
        structural.append(("a field_types key", key))
        structural.append((f"field_types entry '{key}'", description))
    for att in meta.attachments:
        structural.append(("attachment id", att.id))
        structural.append((f"file of attachment '{att.id}'", att.file))
        structural.append((f"when of attachment '{att.id}'", att.when))
    if meta.amends:
        structural.append(("amends.file", meta.amends.file))
    if isinstance(meta.supersedes, Amends):
        structural.append(("supersedes.file", meta.supersedes.file))
    structural.extend(("questions", text) for text in _strings(meta.questions))

    values: list[str] = [
        meta.title, meta.subtitle, meta.version, meta.effective_date,
        meta.governing_law, meta.adopted_by, meta.adoption_date,
    ]
    if meta.amends:
        values.append(meta.amends.title)
    if isinstance(meta.supersedes, Amends):
        values.append(meta.supersedes.title)
    else:
        values.append(meta.supersedes)
    values.extend(att.title for att in meta.attachments)
    for side in meta.sides:
        values.append(side.label)
        for party in side.parties:
            values.extend([
                party.label, party.legal_name, party.identification_number,
                party.address, party.date_of_birth,
            ])
            values.extend(rep.name for rep in party.representatives)
            values.extend(rep.title for rep in party.representatives)
            values.extend(cf.value for cf in party.custom_fields)
    return structural, values


def _check_date_field(
    label: str, value: str, questions: Any, rule: str, result: ValidationResult
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


def _check_duration_unit(unit: str, result: ValidationResult) -> None:
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
    directive: Directive, result: ValidationResult, *, effective_type: str | None = None
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
    result: ValidationResult,
    blanks: dict[str, Blank],
    questions: Any,
    *,
    in_frontmatter: bool = False,
) -> None:
    """Validate one ``{{placeholder:}}`` directive, its arguments included,
    and record it in *blanks*.

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
    result.inline_placeholders.append((pid, ptype))
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
    if ptype == "money" and code and code not in KNOWN_CURRENCIES:
        result.warning(
            "placeholder-unknown-currency",
            f"Placeholder '{pid}' has unrecognized currency code '{code}'.",
        )
    if ptype == "duration" and "unit" in params:
        _check_duration_unit(code, result)


def _check_blank_codes(blanks: dict[str, Blank], result: ValidationResult) -> None:
    """Report each blank whose occurrences fix two currencies or units: one
    blank cannot hold two (§10.7)."""
    for pid, blank in blanks.items():
        fixed = sorted(blank.codes - {""})
        if len(fixed) > 1:
            kind = "currencies" if blank.type == "money" else "units"
            result.error(
                "placeholder-type-inconsistent",
                f"Placeholder '{pid}' fixes different {kind} in different occurrences "
                f"({', '.join(fixed)}); one blank cannot hold two (§10.7).",
            )


def _check_final(
    placeholders: list[Directive],
    chooses: list[Directive],
    notes: list[Quote],
    conditions: list[tuple[str, str]],
    questions: Any,
    result: ValidationResult,
) -> None:
    """The final check (§15.9): no blank and no template construct remains
    in a document meant for signature. The arguments are every one in the
    document: blanks, choices, drafting notes, and conditions (where, as
    written)."""
    for directive in placeholders:
        result.error(
            "placeholder-unfilled",
            f"'{directive.source}' is an unfilled blank in a document meant to be final (§15.9).",
        )

    def construct(what: str) -> None:
        result.error(
            "template-construct-present",
            f"{what} remains in a document meant to be final (§15.9).",
        )

    if questions is not None:
        construct("The 'questions' key")
    for where, text in conditions:
        construct(f"The condition '{text}' on {where}")
    for directive in chooses:
        construct(f"'{directive.source}'")
    for _note in notes:
        construct("A drafting note")


def _clashes(presence: Presence, others: list[Presence], questions: Any) -> bool:
    """True if a declaration with *presence* can appear together with one of
    the earlier declarations of the same identifier (§15.4)."""
    return any(not exclusive(presence, other, questions) for other in others)


def _check_never_true(
    document: Document,
    markers: list[FoundMarker],
    units: Units,
    questions: Any,
    result: ValidationResult,
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
            )
    for found in markers:
        if found.misplaced or found.section is None:
            continue  # literal text, or a preamble paragraph (no enclosing section)
        if not units.own(found.section, found.block, found.fragment):
            continue
        if satisfiable(units.presence(found.section), questions) and not satisfiable(
            units.presence(found.section, found.block, found.fragment), questions
        ):
            result.warning(
                "condition-never-true",
                f"'{found.source}' marks a unit that can never appear: its condition "
                f"contradicts those of the sections enclosing it (§15.4).",
            )


def validate_document(
    document: Document,
    *,
    import_definitions: DefinitionsImporter | None = None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None = None,
    final: bool = False,
) -> ValidationResult:
    """Validate a LegalDown document and build lookup indices.

    Parameters
    ----------
    document:
        A ``Document`` instance (from ``legaldown.models``).
    import_definitions:
        Optional callback ``(amends_file, current_filename) -> dict | None``
        used to resolve definitions from an amended document.
    import_attachment_definitions:
        Optional callback ``(attachment_file) -> dict | None`` used to resolve
        document-wide definitions declared inside an attachment file (§12.4).
    final:
        Apply the final check (§15.9): the document is meant for signature,
        so a remaining blank or template construct is an Error.
    """
    result = ValidationResult()
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
        )
    title = document.metadata.title.strip()
    if not title and not frontmatter_absent:
        result.error("title-missing", "The document title is required.")

    # Imported here to avoid a module-level import cycle (definitions ->
    # validator.helpers -> validator/__init__ -> core).
    from ..definitions import (
        block_fragments,
        collect_definitions,
        find_definition_anchors,
        text_fragments,
    )

    # Each text is lexed once per validation and shared by the passes below.
    lex_fragment = cache(lex)
    meta = document.metadata
    questions = meta.questions
    structural_fields, value_fields = _frontmatter_fields(meta)
    frontmatter_texts = [text for _label, text in structural_fields] + value_fields
    headings = [section.title for section in document.sections]

    def named(text: str, name: str) -> list[Directive]:
        """The *name* directives in a frontmatter value or heading, each text
        lexed once per validation."""
        return [d for d in lex_fragment(text or "").directives if d.name == name]

    # {{choose:}} belongs in body text: never in frontmatter or a heading
    # (§15.5). Wherever it is, it makes the document a template (§15.1).
    misplaced_chooses = [
        (directive, where)
        for texts, where in (
            (frontmatter_texts, "in frontmatter; it belongs in body text"),
            (headings, "in a heading, where §4.2 allows plain text only"),
        )
        for text in texts
        for directive in named(text, "choose")
    ]

    # ── Templates and the presence of units (§15.1, §15.3) ──
    # A document declaring questions, carrying a condition, or containing a
    # {{choose:}} is a template. Its markers are found first: only a template
    # gives a preamble paragraph's condition its place (§5.7).
    markers = find_markers(document, lex_fragment)
    body_directives = {
        directive.name
        for _s, _i, block in document.iter_indexed_blocks()
        for text in text_fragments(block)
        for directive in lex_fragment(text).directives
    }
    template = (
        questions is not None
        or any(att.when for att in meta.attachments)
        or any(section.condition for section in document.sections)
        or any(found.marker and found.marker.condition and not found.misplaced for found in markers)
        or "choose" in body_directives
        or bool(misplaced_chooses)
    )
    units = Units(document, markers, questions, template=template)

    # ── Validate document_type (§16.6) ──
    doc_type = document.metadata.document_type or "contract"
    if doc_type not in VALID_DOC_TYPES:
        result.error(
            "document-type-invalid",
            f"Invalid document_type '{doc_type}'. Must be one of: contract, unilateral_act, collective_act.",
        )

    # ── Validate field_types keys (§16.5) ──
    for ft_key in document.metadata.field_types:
        if not IDENTIFIER_RE.fullmatch(ft_key):
            result.error(
                "field-type-key-format",
                f"field_types key '{ft_key}' must match [a-z][a-z0-9-]*.",
            )
        elif ft_key in RESERVED_VALUE_TYPES:
            result.error(
                "field-type-key-reserved",
                f"field_types key '{ft_key}' collides with a reserved value-type "
                f"name (date, money, duration, party, text).",
            )

    # ── Metadata dates (§16.6) ──
    for field_name, field_value in (
        ("effective_date", document.metadata.effective_date),
        ("adoption_date", document.metadata.adoption_date),
    ):
        _check_date_field(field_name, field_value, questions, "metadata-date-invalid", result)

    # ── Build party lookup from metadata ──
    seen_side_names: set[str] = set()
    seen_party_names: set[str] = set()
    for side in document.metadata.sides:
        side_name = (side.name or "").strip()
        if side_name:
            if not IDENTIFIER_RE.fullmatch(side_name):
                result.error(
                    "side-party-name-format",
                    f"Side name '{side.name}' must be a lowercase identifier matching "
                    f"'{IDENTIFIER_RE.pattern}'.",
                )
            elif side_name in seen_side_names:
                result.error("side-name-duplicate", f"Duplicate side name '{side_name}'.")
            else:
                seen_side_names.add(side_name)
                # §3.6 display derivation: label, else name with hyphens
                # replaced by spaces and each word capitalized.
                result.side_lookup[side_name] = (
                    side.label or side_name.replace("-", " ").title()
                )

        for party in side.parties:
            party_name = (party.name or "").strip()
            if not party_name:
                continue
            if not IDENTIFIER_RE.fullmatch(party_name):
                result.error(
                    "side-party-name-format",
                    f"Party name '{party.name}' must be a lowercase identifier matching "
                    f"'{IDENTIFIER_RE.pattern}'.",
                )
                continue
            if party_name in seen_party_names:
                result.error("party-name-duplicate", f"Duplicate party name '{party_name}'.")
                continue
            seen_party_names.add(party_name)
            if party.type not in ("legal_entity", "natural_person"):
                result.error(
                    "party-type-invalid",
                    f"Party '{party_name}' has invalid type '{party.type}'. Must be 'legal_entity' or 'natural_person'.",
                )
            _check_date_field(
                f"Party '{party_name}' date_of_birth",
                party.date_of_birth,
                questions,
                "date-of-birth-invalid",
                result,
            )
            for rep in party.representatives:
                if not (rep.name or "").strip():
                    result.error(
                        "representative-name-empty",
                        f"A representative of party '{party_name}' is missing the required name.",
                    )
            result.party_lookup[party_name] = (
                party.label or party.legal_name or party_name
            )

    # ── Document type party/side constraints (§16.6) ──
    # When sides is absent entirely the structural rows cannot be verified:
    # emit a single Warning instead of reporting them as violated (§16.6).
    total_sides = len(document.metadata.sides)
    total_parties = sum(len(s.parties) for s in document.metadata.sides)
    if total_sides == 0:
        # Without frontmatter, frontmatter-absent is reported instead (§16.6).
        if not frontmatter_absent:
            result.warning(
                "sides-absent",
                f"No sides are declared, so the document_type '{doc_type}' "
                f"side/party constraints cannot be verified.",
            )
    elif doc_type == "contract":
        if total_sides < 2:
            result.error(
                "sides-minimum",
                f"Contracts require at least 2 distinct sides (found {total_sides}).",
            )
        if total_parties < 2:
            result.error(
                "parties-minimum",
                f"Contracts require at least 2 parties (found {total_parties}).",
            )
    elif doc_type in ("unilateral_act", "collective_act"):
        if total_parties < 1:
            result.error(
                "parties-minimum",
                f"Document type '{doc_type}' requires at least 1 party.",
            )
        if "issuer" not in seen_side_names:
            result.error(
                "issuer-side-required",
                f"Document type '{doc_type}' requires a side named 'issuer'.",
            )

    # ── Attachment id validation (§16.10) ──
    # An id is unique among attachments that can be present together; an
    # attachment's presence is its `when` condition (§3.9, §15.4).
    attachment_presence: dict[str, list[Presence]] = {}
    for att in document.metadata.attachments:
        presence = own_presence(att.when, questions)
        if not att.id:
            result.error("anchor-format", "Attachment is missing required 'id'.")
        elif not IDENTIFIER_RE.fullmatch(att.id):
            result.error(
                "anchor-format",
                f"Attachment id '{att.id}' must match [a-z][a-z0-9-]*.",
            )
        elif _clashes(presence, attachment_presence.get(att.id, []), questions):
            result.error("attachment-id-duplicate", f"Duplicate attachment id '{att.id}'.")
        else:
            attachment_presence.setdefault(att.id, []).append(presence)
            result.attachment_lookup.setdefault(att.id, att.title or att.id)
        if not att.title:
            result.error(
                "attachment-title-empty",
                f"Attachment '{att.id}' is missing required 'title'.",
            )
        if not att.file:
            result.error(
                "attachment-file-missing",
                f"Attachment '{att.id}' is missing required 'file'.",
            )
    attachment_ids = set(attachment_presence)

    # ── Amendment validation (§16.8) ──
    if document.metadata.amends and not document.metadata.amends.title.strip():
        result.error(
            "amends-title-empty", "amends.title is required when amends is present."
        )
    supersedes = document.metadata.supersedes
    if isinstance(supersedes, Amends) and not supersedes.title.strip():
        result.error(
            "supersedes-title-empty",
            "supersedes.title is required when supersedes is written as an object (§3.2).",
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
    last_level = 0
    # The last section at each open level: (identifier, presence). An
    # alternative to it shares its number (§15.8).
    previous_sibling: dict[int, tuple[str, Presence]] = {}

    for section_index, section in enumerate(document.sections):
        # An out-of-range level is an Error, but the section still gets an
        # index entry: `result.sections` is positionally paired with
        # `document.sections` by callers (renderers, the editor), so skipping
        # one here would shift every later section's number and drop the last
        # one from rendered output. Numbering clamps into the valid range.
        level = section.level
        if level < 1 or level > 5:
            result.error(
                "heading-depth",
                f"Section '{section.title}' uses unsupported heading level "
                f"{section.level}. LegalDown supports levels 1-5 (§4.1).",
            )
            level = min(max(level, 1), 5)
        if last_level > 0 and level - last_level > 1:
            result.error(
                "heading-skip",
                f"Heading levels must not skip. '{section.title}' jumps from level {last_level} to {level}.",
            )
        if re.match(
            r"^(article\s+[ivxlcdm]+|\d+(?:\.\d+)*)",
            section.title.strip(),
            re.IGNORECASE,
        ):
            result.warning(
                "heading-hardcoded-number",
                f"Section '{section.title}' appears to include hardcoded numbering. LegalDown headings should be plain text.",
            )

        presence = units.presence(section_index)
        identifier = section.identifier.strip()
        if identifier and not IDENTIFIER_RE.fullmatch(identifier):
            result.error(
                "anchor-format",
                f"Identifier '{identifier}' on section '{section.title}' is invalid. Use lowercase letters, numbers, and hyphens only.",
            )
            identifier = free_identifier(slugify_identifier(identifier), presence)
        elif identifier and _clashes(presence, anchors.get(identifier, []), questions):
            # Duplicate explicit anchors are an Error (§16.2); the identifier
            # is still adjusted so downstream indices stay usable.
            identifier = free_identifier(identifier, presence)
            result.error(
                "anchor-duplicate",
                f"Duplicate section identifier on '{section.title}'. It was adjusted to "
                f"'{identifier}'.",
            )
        elif not identifier:
            base, lossy = generate_identifier(section.title)
            identifier = free_identifier(base, presence)
            if lossy:
                result.warning(
                    "anchor-lossy-slug",
                    f"The identifier generated for '{section.title}' is '{base}': letters or "
                    f"digits without an ASCII form were dropped. Give the heading an explicit "
                    f"identifier (§5.3).",
                )
            if identifier != base:
                result.warning(
                    "anchor-autogen-collision",
                    f"Auto-generated identifier on '{section.title}' collides with another "
                    f"identifier. It was adjusted to '{identifier}'; give the heading an "
                    f"explicit identifier (§5.5).",
                )
        anchors.setdefault(identifier, []).append(presence)

        if _clashes(presence, attachment_presence.get(identifier, []), questions):
            result.error(
                "attachment-id-collision",
                f"Section identifier '{identifier}' collides with an attachment id.",
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
        result.sections.append(entry)
        # Alternatives share an identifier: a reference resolves to
        # whichever is present after assembly; the index keeps the first.
        result.section_lookup.setdefault(identifier, entry)
        last_level = level

    # ── Markers in the body (§5.7, §12.2, §15.3) ──
    # A marker at the end of a top-level paragraph or of a list item's first
    # paragraph is in a marker position: its #id joins the anchor namespace
    # and resolves to its containing section (the §13.2 enumeration-path
    # refinement is a Rendering-level concern; §6.3 falls back to the
    # section's number). Anything else is literal text (anchor-misplaced).
    for title in headings:
        for look_alike in marker_matches(title, lex_fragment(title)):
            result.warning(
                "anchor-misplaced",
                f"'{look_alike.group(0)}' in the heading '{title}' is literal text: a heading's "
                f"marker ends it and holds '#id', 'when=condition', or both, each at most "
                f"once (§5.2, §15.3).",
            )
    for found in markers:
        if not found.placed(template):
            result.warning("anchor-misplaced", f"'{found.source}' is {found.misplaced}.")
        elif found.include_only and found.marker.identifier:
            result.warning(
                "anchor-misplaced",
                f"'#{found.marker.identifier}' in '{found.source}' is ignored: a paragraph "
                f"holding only an {{{{include:}}}} is replaced by its fragment, so it is not a "
                f"reference target. Anchor a heading inside the fragment (§12.2).",
            )
    for found in placed_anchors:
        anchor_id = found.marker.identifier
        presence = units.presence(found.section, found.block, found.fragment)
        if not IDENTIFIER_RE.fullmatch(anchor_id):
            result.error(
                "anchor-format",
                f"Anchor '{{#{anchor_id}}}' is invalid. Use lowercase letters, numbers, and hyphens only.",
            )
        elif _clashes(presence, anchors.get(anchor_id, []), questions):
            result.error(
                "anchor-duplicate",
                f"Anchor '{{#{anchor_id}}}' duplicates an existing anchor in the document.",
            )
        elif _clashes(presence, attachment_presence.get(anchor_id, []), questions):
            result.error(
                "attachment-id-collision",
                f"Anchor '{{#{anchor_id}}}' collides with an attachment id.",
            )
        else:
            anchors.setdefault(anchor_id, []).append(presence)
            result.section_lookup.setdefault(anchor_id, result.sections[found.section])

    # ── Definitions (§7) ──
    # The mandatory, first-positioned Definitions section is gone (§7.2). A
    # definition is a quoted term followed by a ``{{def: id}}`` anchor, declared
    # either as a leading-anchor "definition" block or inline at first use, and
    # may appear anywhere.
    definition_refs = collect_definitions(
        document, language=document.metadata.language, lex_fragment=lex_fragment
    )
    # Each identifier's declarations: (term, auto-generated, presence).
    declared_terms: dict[str, list[tuple[str, bool, Presence]]] = {}
    for ref in definition_refs:
        def_id = ref.id
        if not IDENTIFIER_RE.fullmatch(def_id):
            result.error(
                "anchor-format",
                f"Definition identifier '{def_id}' (term '{ref.term}') is invalid. "
                f"Use lowercase letters, numbers, and hyphens only.",
            )
            continue
        if ref.lossy_id:
            result.warning(
                "def-lossy-slug",
                f"The id generated for the defined term '{ref.term}' is '{def_id}': letters or "
                f"digits without an ASCII form were dropped. Give the definition an explicit "
                f"id (§5.3, §7.2).",
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
                    f"Add an explicit id to disambiguate.",
                )
            else:
                result.error(
                    "def-duplicate-id",
                    f"Definition identifier '{def_id}' is duplicated.",
                )
            continue
        declared_terms.setdefault(def_id, []).append((ref.term, ref.auto_id, presence))
        result.definition_lookup.setdefault(def_id, ref.term or def_id.replace("-", " ").title())

    # ── Definition source-form checks (§7.2 validation table) ──
    for _section, _index, block in document.iter_blocks():
        for fragment in text_fragments(block):
            for anchor in find_definition_anchors(
                fragment, language=document.metadata.language, lexed=lex_fragment(fragment)
            ):
                if anchor.term is None:
                    result.error(
                        "def-no-quoted-span",
                        "A {{def:}} anchor must immediately follow a quoted defined term.",
                    )
                    continue
                if anchor.emphasis:
                    result.warning(
                        "def-emphasis",
                        "Defined term wrapped in emphasis markers in source. Quotation marks "
                        "alone delimit a defined term; emphasis is a render-time style.",
                    )
                if anchor.single_quoted:
                    result.warning(
                        "def-single-quote-ambiguous",
                        f"Single-quoted defined term '{anchor.term}' may be ambiguous "
                        f"with an apostrophe (U+2019); prefer double-quote delimiters.",
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
                if imported is not None:
                    _amends_import_succeeded = True
                    _imported_definitions = imported
                    for def_id in result.definition_lookup:
                        if def_id in _imported_definitions:
                            result.warning(
                                "amend-def-override",
                                f"Amendment redefines '{def_id}' which exists in the original document.",
                            )
                    for def_id, term_text in _imported_definitions.items():
                        result.definition_lookup.setdefault(def_id, term_text)
                        # The original is always in force (§7.5), even where
                        # the amendment redefines the term under a condition.
                        declared_terms.setdefault(def_id, []).append((term_text, False, ALWAYS))

    # ── Attachment definition import (§7, §12.4) ──
    # A {{def:}} inside an attachment file registers a document-wide term; ids
    # must remain unique across the combined document (§16.10).
    if import_attachment_definitions is not None:
        for att in document.metadata.attachments:
            if not att.file.endswith(LEGALDOWN_EXTENSIONS):
                continue
            att_defs = import_attachment_definitions(att.file)
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
                    )
                else:
                    declared_terms.setdefault(def_id, []).append((term_text, False, presence))
                    result.definition_lookup.setdefault(def_id, term_text)

    # ── Inline directive validation ──
    blanks: dict[str, Blank] = {}
    referenced_attachments: set[str] = set()

    # ── Frontmatter placeholders (§3.10) ──
    # A {{placeholder:}} is allowed only as a quoted value in *value* fields, not
    # in identifier, structural, or format-checked fields, which a filled-in
    # value could break. Placeholders collected here share the same blank
    # (id, type, currency, unit) with any matching body placeholder.
    for field_label, field_value in structural_fields:
        if named(field_value, "placeholder"):
            result.error(
                "placeholder-in-structural-field",
                f"A {{{{placeholder:}}}} is not allowed in {field_label}: an identifier, "
                f"structural, or format-checked field; placeholders are only valid in "
                f"value fields (§3.10).",
            )

    for field_value in value_fields:
        for directive in named(field_value, "placeholder"):
            _check_placeholder(directive, result, blanks, questions, in_frontmatter=True)
    # Every blank, wherever it is, for the final check (§15.9).
    placeholders = [
        directive
        for text in [*frontmatter_texts, *headings]
        for directive in named(text, "placeholder")
    ]

    chooses: list[Directive] = []
    for directive, where in misplaced_chooses:
        chooses.append(directive)
        result.error("choose-invalid", f"'{directive.source}' is {where} (§15.5).")

    # Every {{ref:}}, {{term:}}, and {{attach:}}: (target, presence, in a
    # drafting note), for reference safety (§15.4).
    references: dict[str, list[tuple[str, Presence, bool]]] = {"ref": [], "term": [], "attach": []}
    for section_index, block_index, block in document.iter_indexed_blocks():
        # The parser lifts a paragraph's first {{ref:}} or {{term:}} into
        # block fields when they hold it without loss (no other parameters).
        ref_targets: list[str] = []
        term_targets: list[str] = []
        block_presence = units.presence(section_index, block_index)
        if block.kind in ("ref", "term") and block.target.strip():
            (ref_targets if block.kind == "ref" else term_targets).append(block.target.strip())
            references[block.kind].append((block.target.strip(), block_presence, False))
        notes = [quote for quote in block_quotes(block) if quote.is_drafting_note]
        for fragment_index, (fragment, _position) in enumerate(block_fragments(block)):
            lexed = lex_fragment(fragment)
            presence = units.presence(section_index, block_index, fragment_index)
            for _offset in lexed.stray_braces:
                result.warning("brace-stray", BRACE_STRAY)
            for directive in lexed.directives:
                name = directive.name
                if name == "choose":
                    chooses.append(directive)  # a template even when malformed
                if name == "placeholder":
                    placeholders.append(directive)
                    # Its effective type decides which parameters it defines.
                    _check_placeholder(directive, result, blanks, questions)
                    continue
                if not _check_directive_arguments(directive, result):
                    continue
                # ── Unknown directive names (§11.5): well-formed only ──
                if name not in KNOWN_DIRECTIVES:
                    # It may be a construct of the newer declared version.
                    report = result.warning if newer_version else result.error
                    report(
                        "directive-unknown",
                        f"Unknown directive '{{{{{name}:}}}}'. Renderers replace it "
                        f"with [UNKNOWN DIRECTIVE: {name}] (§11.5).",
                    )
                    continue
                value = directive.positional or ""
                params = directive.params
                if name in references and value:
                    in_note = any(
                        note.fragment == fragment and note.start <= directive.start < note.end
                        for note in notes
                    )
                    references[name].append((value, presence, in_note))
                if name in ("ref", "term") and not value:
                    result.error(
                        "ref-broken" if name == "ref" else "term-undefined",
                        f"'{directive.source}' has no target.",
                    )
                elif name == "ref":
                    ref_targets.append(value)
                elif name == "term":
                    term_targets.append(value)
                elif name == "date":
                    result.inline_dates.append(value)
                    if not is_valid_iso_date(value):
                        result.error(
                            "date-invalid",
                            f"Invalid date value '{value}'. Must be a valid ISO 8601 date (YYYY-MM-DD).",
                        )
                elif name == "money":
                    currency = params.get("currency", "")
                    result.inline_money.append((value, currency))
                    if not is_valid_money_amount(value):
                        result.error(
                            "money-invalid-amount",
                            f"Invalid money amount '{value}'. Must be a non-negative numeric value.",
                        )
                    if currency:
                        if currency not in KNOWN_CURRENCIES:
                            result.warning(
                                "money-unknown-currency",
                                f"Unrecognized currency code '{currency}'.",
                            )
                    else:
                        result.warning(
                            "money-missing-currency",
                            "Money directive without currency parameter.",
                        )
                elif name == "duration":
                    dur_unit = params.get("unit", "")
                    result.inline_durations.append((value, dur_unit))
                    if not is_positive_numeric(value):
                        result.error(
                            "duration-invalid-value",
                            f"Invalid duration value '{value}'. Must be a positive integer "
                            "or decimal, with a period as the decimal separator.",
                        )
                    _check_duration_unit(dur_unit, result)
                elif name == "party":
                    if not value or not IDENTIFIER_RE.fullmatch(value):
                        result.error(
                            "party-name-malformed",
                            f"Party directive has invalid role value '{value}'. Must match [a-z][a-z0-9-]*.",
                        )
                    elif value not in result.party_lookup:
                        result.error(
                            "party-unknown",
                            f"Party directive references unknown party: '{value}'.",
                        )
                elif name == "side":
                    if not value or not IDENTIFIER_RE.fullmatch(value):
                        result.error(
                            "side-name-malformed",
                            f"Side directive has invalid value '{value}'. Must match [a-z][a-z0-9-]*.",
                        )
                    elif value not in seen_side_names:
                        result.error(
                            "side-unknown",
                            f"Side directive references unknown side: '{value}'.",
                        )
                elif name == "field":
                    ftype = params.get("type", "")
                    if not ftype:
                        result.error(
                            "field-type-missing",
                            "Field directive is missing required type parameter.",
                        )
                    elif not IDENTIFIER_RE.fullmatch(ftype):
                        result.error(
                            "field-type-missing",
                            f"Field type '{ftype}' is invalid — must match [a-z][a-z0-9-]*.",
                        )
                    elif (
                        document.metadata.field_types
                        and ftype not in document.metadata.field_types
                    ):
                        result.warning(
                            "field-type-undeclared",
                            f"Field type '{ftype}' is not declared in field_types.",
                        )
                    result.inline_fields.append((value, ftype))
                elif name == "choose":
                    check_choose(directive, questions, result)
                elif name == "attach":
                    referenced_attachments.add(value)
                    if value not in attachment_ids:
                        result.error(
                            "attach-undeclared",
                            f"Attachment reference '{{{{attach: {value}}}}}' references undeclared attachment id.",
                        )
        for target in ref_targets:
            if target in result.section_lookup:
                continue
            if target in attachment_ids:
                # §5.6: attachments live in the anchor namespace but are
                # referenced with {{attach:}}, never {{ref:}}.
                result.error(
                    "ref-targets-attachment",
                    f"Reference '{{{{ref: {target}}}}}' targets an attachment id. "
                    f"Use '{{{{attach: {target}}}}}' instead.",
                )
            else:
                result.error("ref-broken", f"Broken section reference: '{target}'.")
        for target in term_targets:
            result.used_terms.add(target)
            if target not in result.definition_lookup:
                if document.metadata.amends:
                    if _amends_is_legaldown and _amends_import_succeeded:
                        result.error(
                            "amend-term-undefined",
                            f"Undefined term reference: '{target}' (not found in "
                            f"amendment or imported original).",
                        )
                    else:
                        # Original unavailable or not LegalDown source:
                        # the reference may resolve there (§16.8).
                        result.info(
                            "amend-term-unresolvable",
                            f"Term reference '{target}' is not defined in the "
                            f"amendment; the original document is not available "
                            f"to verify it.",
                        )
                else:
                    result.error(
                        "term-undefined", f"Undefined term reference: '{target}'."
                    )

    _check_blank_codes(blanks, result)

    # ── Templates (§15) ──
    # Every condition in a condition position (§15.3): where, and as written.
    conditions: list[tuple[str, str]] = [
        (f"the heading '{section.title}'", section.condition)
        for section in document.sections
        if section.condition
    ]
    conditions.extend(
        (f"'{found.source}'", found.marker.condition)
        for found in markers
        if found.marker
        and found.marker.condition
        and found.placed(template)
    )
    conditions.extend(
        (f"attachment '{att.id}'", att.when) for att in meta.attachments if att.when
    )
    for where, text in conditions:
        problem = condition_problem(text, questions)
        if problem:
            result.error("condition-invalid", f"The condition '{text}' on {where}: {problem} (§15.3).")
    _check_never_true(document, markers, units, questions, result)

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
        for target, presence, in_note in uses:
            declared = targets_of[kind].get(target)
            if in_note or not declared or (kind == "term" and original_unread):
                continue
            if always_covered(presence, declared, questions):
                continue
            result.error(
                "condition-reference-unsafe",
                f"'{{{{{kind}: {target}}}}}' can be present when '{target}' is not: under some "
                f"answers that keep the reference, no declaration of its target remains (§15.4).",
            )

    used_questions = (
        {directive.positional for directive in placeholders if directive.positional}
        | {
            condition.question
            for _where, text in conditions
            if (condition := parse_condition(text)) is not None
        }
        | {directive.positional for directive in chooses if directive.positional}
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
                )

    check_questions(
        questions,
        blanks,
        result,
        template=template,
        not_line_editable=meta.not_line_editable,
    )
    notes = check_template_body(document, lex_fragment, result, template=template)
    if final:
        _check_final(placeholders, chooses, notes, conditions, questions, result)

    # Warn about declared but unreferenced attachments (§16.10): once per id,
    # which alternatives share (§15.3).
    for att_id in dict.fromkeys(att.id for att in document.metadata.attachments):
        if att_id and att_id not in referenced_attachments:
            result.warning(
                "attachment-unreferenced",
                f"Attachment '{att_id}' is declared but never referenced via {{{{attach:}}}}.",
            )

    # Warn about declared but never-referenced definitions (§7). May
    # false-positive when §7.4 automatic term recognition is enabled.
    _warned_defs: set[str] = set()
    for ref in definition_refs:
        if ref.id in _warned_defs:
            continue
        if ref.id in result.definition_lookup and ref.id not in result.used_terms:
            _warned_defs.add(ref.id)
            result.warning(
                "def-unreferenced",
                f"Definition '{ref.id}' is declared but never referenced via {{{{term:}}}}.",
            )

    return result
