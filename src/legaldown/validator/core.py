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
    is_escaped,
    iter_directives,
    lex,
)
from ..models import Amends, Document
from ..specification import SPEC_VERSION, parse_version
from .helpers import (
    ensure_unique_identifier,
    format_section_number,
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
    block_quotes,
    check_choose,
    check_questions,
    check_template_body,
    question_type,
)

# Type aliases for the optional definitions-import callbacks.
DefinitionsImporter = Callable[[str, str], dict[str, str] | None]
AttachmentDefinitionsImporter = Callable[[str], dict[str, str] | None]


def _placeholders(value: str) -> list[Directive]:
    """The ``{{placeholder:}}`` directives in a frontmatter value (§3.10)."""
    return [d for d in iter_directives(value or "") if d.name == "placeholder"]


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
    document: Document,
    placeholders: list[Directive],
    chooses: list[Directive],
    result: ValidationResult,
) -> None:
    """The final check (§15.9): no blank and no template construct remains
    in a document meant for signature. *placeholders* and *chooses* are every
    one in the document: frontmatter, headings, and body."""
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

    if document.metadata.questions is not None:
        construct("The 'questions' key")
    for att in document.metadata.attachments:
        if att.when:
            construct(f"The condition 'when: {att.when}' of attachment '{att.id}'")
    for directive in chooses:
        construct(f"'{directive.source}'")
    for _section, _index, block in document.iter_blocks():
        for quote in block_quotes(block):
            if quote.is_drafting_note:
                construct("A drafting note")


def _is_include_only(text: str, directives: list[Directive]) -> bool:
    """True if *text* holds a single ``{{include:}}`` directive and nothing
    else but comments, which are not rendered (§8.6). *directives* are lexed
    from a string that begins with *text*."""
    inside = [d for d in directives if d.end <= len(text)]
    return (
        len(inside) == 1
        and inside[0].name == "include"
        and not inside[0].malformed
        and not _HTML_COMMENT_RE.sub("", text[: inside[0].start]).strip()
        and not _HTML_COMMENT_RE.sub("", text[inside[0].end:]).strip()
    )


# A {#id}-like marker (§5.7), in body text as opposed to a heading.
_ANCHOR_MARKER_RE = re.compile(r"\{#([^}\s]+)\}")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


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
    title = document.metadata.title.strip()
    if not title:
        result.error("title-missing", "The document title is required.")

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

    questions = document.metadata.questions

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
    attachment_ids: set[str] = set()
    for att in document.metadata.attachments:
        if not att.id:
            result.error("anchor-format", "Attachment is missing required 'id'.")
        elif not IDENTIFIER_RE.fullmatch(att.id):
            result.error(
                "anchor-format",
                f"Attachment id '{att.id}' must match [a-z][a-z0-9-]*.",
            )
        elif att.id in attachment_ids:
            result.error("attachment-id-duplicate", f"Duplicate attachment id '{att.id}'.")
        else:
            attachment_ids.add(att.id)
            result.attachment_lookup[att.id] = att.title or att.id
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
    used_identifiers: set[str] = set()
    counters = [0] * 7
    path_stack: list[str] = []
    last_level = 0

    for section in document.sections:
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

        explicit_identifier = bool(section.identifier.strip())
        identifier = section.identifier.strip() or slugify_identifier(section.title)
        if not IDENTIFIER_RE.fullmatch(identifier):
            result.error(
                "anchor-format",
                f"Identifier '{identifier}' on section '{section.title}' is invalid. Use lowercase letters, numbers, and hyphens only.",
            )
            identifier = slugify_identifier(identifier or section.title)
        identifier, deduped = ensure_unique_identifier(identifier, used_identifiers)
        if deduped:
            if explicit_identifier:
                # Duplicate explicit anchors are an Error (§16.2); the id is
                # still adjusted so downstream indices stay usable.
                result.error(
                    "anchor-duplicate",
                    f"Duplicate section identifier on '{section.title}'. It was adjusted to '{identifier}'.",
                )
            else:
                result.warning(
                    "anchor-autogen-collision",
                    f"Auto-generated identifier on '{section.title}' collides with an earlier section. It was adjusted to '{identifier}'.",
                )
        section.identifier = identifier

        if identifier in attachment_ids:
            result.error(
                "attachment-id-collision",
                f"Section identifier '{identifier}' collides with an attachment id.",
            )

        for idx in range(level, 7):
            if idx == level:
                counters[idx] += 1
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
        result.section_lookup[identifier] = entry
        result.section_lookup[entry.path] = entry
        last_level = level

    # ── Item and paragraph anchors (§5.7) ──
    # A {#id} at the very end of a top-level paragraph or of a list item joins
    # the shared anchor namespace and is a valid {{ref:}} target. It resolves
    # to its containing section (the §13.2 enumeration-path refinement is a
    # Rendering-level concern; §6.3 falls back to the containing section's
    # number). A marker anywhere else, including the preamble (§4.4), is
    # literal text (anchor-misplaced). The definitions module is imported
    # lazily to avoid a module-level import cycle (definitions ->
    # validator.helpers -> validator/__init__ -> core).
    from ..definitions import (
        block_fragments,
        collect_definitions,
        find_definition_anchors,
        text_fragments,
    )

    # Each fragment is lexed once per validation and shared by the passes below.
    lex_fragment = cache(lex)

    # An anchor resolves to its section's index entry. result.sections is
    # paired with document.sections by position (see the numbering above), so
    # the walk pairs them the same way; the preamble has no entry.
    bodies = [
        (None, document.preamble),
        *zip(result.sections, (s.blocks for s in document.sections), strict=True),
    ]
    for entry, blocks in bodies:
        for block in blocks:
            for fragment, anchor_position in block_fragments(block):
                if "{#" not in fragment:
                    continue
                lexed = lex_fragment(fragment)
                for m in _ANCHOR_MARKER_RE.finditer(lexed.view):
                    if is_escaped(fragment, m.start()) or any(
                        d.start <= m.start() < d.end
                        for d in lexed.directives
                        if not d.malformed
                    ):
                        continue  # literal: escaped, or part of a directive's value
                    # Only whitespace and comments may follow an anchor: a
                    # comment is not rendered (§8.6), but a code span is text.
                    at_end = not _HTML_COMMENT_RE.sub("", fragment[m.end():]).strip()
                    if entry is None or not anchor_position or not at_end:
                        result.warning(
                            "anchor-misplaced",
                            f"'{m.group(0)}' is not in an anchor position and is literal "
                            f"text: anchors go at the end of a list item or of a paragraph "
                            f"directly inside a section (§5.7).",
                        )
                        continue
                    if block.kind == "paragraph" and _is_include_only(
                        fragment[: m.start()], lexed.directives
                    ):
                        result.warning(
                            "anchor-misplaced",
                            f"'{m.group(0)}' is ignored: a paragraph holding only an "
                            f"{{{{include:}}}} is replaced by its fragment, so it is not a "
                            f"reference target. Anchor a heading inside the fragment (§12.2).",
                        )
                        continue
                    anchor_id = m.group(1)
                    if not IDENTIFIER_RE.fullmatch(anchor_id):
                        result.error(
                            "anchor-format",
                            f"Anchor '{{#{anchor_id}}}' is invalid. Use lowercase letters, numbers, and hyphens only.",
                        )
                    elif anchor_id in used_identifiers:
                        result.error(
                            "anchor-duplicate",
                            f"Anchor '{{#{anchor_id}}}' duplicates an existing anchor in the document.",
                        )
                    elif anchor_id in attachment_ids:
                        result.error(
                            "attachment-id-collision",
                            f"Anchor '{{#{anchor_id}}}' collides with an attachment id.",
                        )
                    else:
                        used_identifiers.add(anchor_id)
                        result.section_lookup[anchor_id] = entry

    # ── Definitions (§7) ──
    # The mandatory, first-positioned Definitions section is gone (§7.2). A
    # definition is a quoted term followed by a ``{{def: id}}`` anchor, declared
    # either as a leading-anchor "definition" block or inline at first use, and
    # may appear anywhere.
    definition_refs = collect_definitions(
        document, language=document.metadata.language, lex_fragment=lex_fragment
    )
    auto_ids_seen: dict[str, str] = {}
    for ref in definition_refs:
        def_id = ref.id
        if not IDENTIFIER_RE.fullmatch(def_id):
            result.error(
                "anchor-format",
                f"Definition identifier '{def_id}' (term '{ref.term}') is invalid. "
                f"Use lowercase letters, numbers, and hyphens only.",
            )
            continue
        if def_id in result.definition_lookup:
            if ref.auto_id and auto_ids_seen.get(def_id, ref.term) != ref.term:
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
        result.definition_lookup[def_id] = ref.term or def_id.replace("-", " ").title()
        if ref.auto_id:
            auto_ids_seen[def_id] = ref.term

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
                        if def_id not in result.definition_lookup:
                            result.definition_lookup[def_id] = term_text

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
            for def_id, term_text in att_defs.items():
                if def_id in result.definition_lookup:
                    result.error(
                        "def-duplicate-id",
                        f"Definition id '{def_id}' from attachment '{att.id}' collides with "
                        f"a definition in the main document (ids must be unique, §16.10).",
                    )
                else:
                    result.definition_lookup[def_id] = term_text

    # ── Inline directive validation ──
    blanks: dict[str, Blank] = {}
    referenced_attachments: set[str] = set()

    # ── Frontmatter placeholders (§3.10) ──
    # A {{placeholder:}} is allowed only as a quoted value in *value* fields, not
    # in identifier, structural, or format-checked fields, which a filled-in
    # value could break. Placeholders collected here share the same blank
    # (id, type, currency, unit) with any matching body placeholder.
    meta = document.metadata
    structural_fields: list[tuple[str, str]] = [
        ("document_type", meta.document_type),
        ("legaldown", meta.legaldown),
        ("language", meta.language),
        ("authoritative", meta.authoritative),
    ]
    for side in meta.sides:
        structural_fields.append((f"side name '{side.name}'", side.name))
        for party in side.parties:
            structural_fields.append((f"party name '{party.name}'", party.name))
            structural_fields.append((f"party type for '{party.name}'", party.type))
    for code, path in meta.translations.items():
        structural_fields.append(("a translations language code", code))
        structural_fields.append((f"translations file for '{code}'", path))
    for key, description in meta.field_types.items():
        structural_fields.append(("a field_types key", key))
        structural_fields.append((f"field_types entry '{key}'", description))
    for att in meta.attachments:
        structural_fields.append(("attachment id", att.id))
        structural_fields.append((f"file of attachment '{att.id}'", att.file))
        structural_fields.append((f"when of attachment '{att.id}'", att.when))
    if meta.amends:
        structural_fields.append(("amends.file", meta.amends.file))
    if isinstance(meta.supersedes, Amends):
        structural_fields.append(("supersedes.file", meta.supersedes.file))
    structural_fields.extend(("questions", text) for text in _strings(meta.questions))
    for field_label, field_value in structural_fields:
        if _placeholders(field_value):
            result.error(
                "placeholder-in-structural-field",
                f"A {{{{placeholder:}}}} is not allowed in {field_label}: an identifier, "
                f"structural, or format-checked field; placeholders are only valid in "
                f"value fields (§3.10).",
            )

    value_fields: list[str] = [
        meta.title, meta.subtitle, meta.version, meta.effective_date,
        meta.governing_law, meta.adopted_by, meta.adoption_date,
    ]
    if meta.amends:
        value_fields.append(meta.amends.title)
    if isinstance(meta.supersedes, Amends):
        value_fields.append(meta.supersedes.title)
    else:
        value_fields.append(meta.supersedes)
    value_fields.extend(att.title for att in meta.attachments)
    for side in meta.sides:
        value_fields.append(side.label)
        for party in side.parties:
            value_fields.extend([
                party.label, party.legal_name, party.identification_number,
                party.address, party.date_of_birth,
            ])
            value_fields.extend(rep.name for rep in party.representatives)
            value_fields.extend(rep.title for rep in party.representatives)
            value_fields.extend(cf.value for cf in party.custom_fields)
    for field_value in value_fields:
        for directive in _placeholders(field_value):
            _check_placeholder(directive, result, blanks, questions, in_frontmatter=True)
    frontmatter_texts = [text for _label, text in structural_fields] + value_fields
    # Every blank, wherever it is, for the final check (§15.9).
    placeholders = [
        directive
        for text in [*frontmatter_texts, *(section.title for section in document.sections)]
        for directive in _placeholders(text)
    ]

    # {{choose:}} belongs in body text: never in frontmatter or a heading
    # (§15.5). Wherever it is, it makes the document a template (§15.1).
    chooses: list[Directive] = []
    for text in frontmatter_texts:
        for directive in iter_directives(text or ""):
            if directive.name == "choose":
                chooses.append(directive)
                result.error(
                    "choose-invalid",
                    f"'{directive.source}' is in frontmatter; it belongs in body text (§15.5).",
                )
    for section in document.sections:
        for directive in iter_directives(section.title):
            if directive.name == "choose":
                chooses.append(directive)
                result.error(
                    "choose-invalid",
                    f"'{directive.source}' is in a heading, where §4.2 allows plain text only (§15.5).",
                )

    for _section, _index, block in document.iter_blocks():
        # The parser lifts a paragraph's first {{ref:}} or {{term:}} into
        # block fields when they hold it without loss (no other parameters).
        ref_targets: list[str] = []
        term_targets: list[str] = []
        if block.kind == "ref" and block.target.strip():
            ref_targets.append(block.target.strip())
        if block.kind == "term" and block.target.strip():
            term_targets.append(block.target.strip())
        for fragment in text_fragments(block):
            lexed = lex_fragment(fragment)
            for _offset in lexed.stray_braces:
                result.warning("brace-stray", BRACE_STRAY)
            for directive in lexed.directives:
                name = directive.name
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
                    chooses.append(directive)
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
    # A document declaring questions, carrying a condition, or containing a
    # {{choose:}} is a template (§15.1).
    template = questions is not None or any(att.when for att in meta.attachments) or bool(chooses)
    check_questions(
        questions,
        blanks,
        result,
        template=template,
        not_line_editable=meta.not_line_editable,
    )
    check_template_body(document, lex_fragment, result, template=template)
    if final:
        _check_final(document, placeholders, chooses, result)

    # Warn about declared but unreferenced attachments (§16.10).
    for att in document.metadata.attachments:
        if att.id and att.id not in referenced_attachments:
            result.warning(
                "attachment-unreferenced",
                f"Attachment '{att.id}' is declared but never referenced via {{{{attach:}}}}.",
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
