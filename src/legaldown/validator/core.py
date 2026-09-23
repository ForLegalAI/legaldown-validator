"""Core validation logic for LegalDown documents.

Every diagnostic is recorded under the **stable rule id** the specification
assigns to its check (§15.1) via ``ValidationResult.error/warning/info``,
so tooling can filter, suppress, or escalate specific rules consistently
across implementations (§15.9).
"""
from __future__ import annotations

import re
from collections.abc import Callable

from ..directives import (
    DIRECTIVE_PARAMS,
    KNOWN_DIRECTIVES,
    Directive,
    is_escaped,
    iter_directives,
    lex,
)
from ..models import Document
from .helpers import (
    ensure_unique_identifier,
    format_section_number,
    is_positive_numeric,
    is_valid_iso_date,
    is_valid_money_amount,
    slugify_identifier,
)
from .patterns import (
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    RESERVED_VALUE_TYPES,
    VALID_DOC_TYPES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import SectionIndexEntry, ValidationResult

# Type aliases for the optional definitions-import callbacks.
DefinitionsImporter = Callable[[str, str], dict[str, str] | None]
AttachmentDefinitionsImporter = Callable[[str], dict[str, str] | None]


def _placeholders(value: str) -> list[Directive]:
    """The ``{{placeholder:}}`` directives in a frontmatter value (§3.10)."""
    return [d for d in iter_directives(value or "") if d.name == "placeholder"]


# §15.6: a metadata value that is itself a placeholder satisfies presence and
# is exempt from the field's format checks; the placeholder's own checks apply.
def _is_placeholder_value(value: str) -> bool:
    return bool(_placeholders(value))


# §10.1: notes are plain text — Markdown formatting would leak markers into
# machine-facing output.
_MARKDOWN_RE = re.compile(r"\*\*|__|\+\+|`|(?<!\w)\*(?!\s)")


def _check_directive_arguments(directive: Directive, result: ValidationResult) -> bool:
    """Report the argument problems any directive can have; False if malformed.

    A malformed directive (§11.2) has no reliable arguments, so its own checks
    are skipped; its Error keeps the document from passing. An unknown
    parameter is ignored (§11.2) and a repeated one keeps its first value, so
    the directive's own checks still run.
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
    for param in directive.unknown_params():
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
    placeholder_types: dict[str, str],
) -> None:
    """Validate one ``{{placeholder:}}`` directive and record it.

    Shared by the body-block scan and the frontmatter scan so a placeholder id
    used in both is treated as the *same* blank (consistent type, single entry
    semantics per §3.10).
    """
    pid = directive.positional or ""
    ptype = directive.params.get("type", "text")
    pcurrency = directive.params.get("currency", "")
    if not pid or not IDENTIFIER_RE.match(pid):
        result.error(
            "placeholder-id-malformed",
            f"Placeholder id '{pid}' is invalid — must match [a-z][a-z0-9-]*.",
        )
    elif ptype not in VALID_PLACEHOLDER_TYPES:
        result.error(
            "placeholder-type-invalid",
            f"Placeholder type '{ptype}' is unsupported. Must be one of: text, date, money.",
        )
    else:
        if pid in placeholder_types:
            if placeholder_types[pid] != ptype:
                result.error(
                    "placeholder-type-inconsistent",
                    f"Placeholder '{pid}' used with inconsistent types: "
                    f"'{placeholder_types[pid]}' and '{ptype}'.",
                )
        else:
            placeholder_types[pid] = ptype
        if ptype == "money" and pcurrency and pcurrency not in KNOWN_CURRENCIES:
            result.warning(
                "placeholder-unknown-currency",
                f"Placeholder '{pid}' has unrecognized currency code '{pcurrency}'.",
            )
    result.inline_placeholders.append((pid, ptype))


# A {#id}-like marker (§5.7), in body text as opposed to a heading.
_ANCHOR_MARKER_RE = re.compile(r"\{#([^}\s]+)\}")


def validate_document(
    document: Document,
    *,
    import_definitions: DefinitionsImporter | None = None,
    import_attachment_definitions: AttachmentDefinitionsImporter | None = None,
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
    """
    result = ValidationResult()
    title = document.metadata.title.strip()
    if not title:
        result.error("title-missing", "The document title is required.")

    # ── Validate document_type (§15.6) ──
    doc_type = document.metadata.document_type or "contract"
    if doc_type not in VALID_DOC_TYPES:
        result.error(
            "document-type-invalid",
            f"Invalid document_type '{doc_type}'. Must be one of: contract, unilateral_act, collective_act.",
        )

    # ── Validate field_types keys (§15.5) ──
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

    # ── Metadata dates (§15.6) ──
    for field_name, field_value in (
        ("effective_date", document.metadata.effective_date),
        ("adoption_date", document.metadata.adoption_date),
    ):
        if (
            field_value
            and not _is_placeholder_value(field_value)
            and not is_valid_iso_date(field_value.strip())
        ):
            result.error(
                "metadata-date-invalid",
                f"{field_name} '{field_value}' must be a valid ISO 8601 date (YYYY-MM-DD).",
            )

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
            if (
                party.date_of_birth
                and not _is_placeholder_value(party.date_of_birth)
                and not is_valid_iso_date(party.date_of_birth.strip())
            ):
                result.error(
                    "date-of-birth-invalid",
                    f"Party '{party_name}' date_of_birth '{party.date_of_birth}' "
                    f"must be a valid ISO 8601 date (YYYY-MM-DD).",
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

    # ── Document type party/side constraints (§15.6) ──
    # When sides is absent entirely the structural rows cannot be verified:
    # emit a single Warning instead of reporting them as violated (§15.6).
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

    # ── Attachment id validation (§15.10) ──
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

    # ── Amendment validation (§15.8) ──
    if document.metadata.amends and not document.metadata.amends.title.strip():
        result.error(
            "amends-title-empty", "amends.title is required when amends is present."
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
        if not IDENTIFIER_RE.match(identifier):
            result.error(
                "anchor-format",
                f"Identifier '{identifier}' on section '{section.title}' is invalid. Use lowercase letters, numbers, and hyphens only.",
            )
            identifier = slugify_identifier(identifier or section.title)
        identifier, deduped = ensure_unique_identifier(identifier, used_identifiers)
        if deduped:
            if explicit_identifier:
                # Duplicate explicit anchors are an Error (§15.2); the id is
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

    bodies = [
        (None, document.preamble),
        *zip(result.sections, (s.blocks for s in document.sections), strict=True),
    ]
    for entry, blocks in bodies:
        for block in blocks:
            for fragment, anchor_position in block_fragments(block):
                if "{#" not in fragment:
                    continue
                lexed = lex(fragment)
                end_of_text = len(lexed.view.rstrip())
                for m in _ANCHOR_MARKER_RE.finditer(lexed.view):
                    if is_escaped(fragment, m.start()) or any(
                        d.start <= m.start() < d.end for d in lexed.directives
                    ):
                        continue  # literal: escaped, or part of a directive's value
                    if entry is None or not anchor_position or m.end() != end_of_text:
                        result.warning(
                            "anchor-misplaced",
                            f"'{m.group(0)}' is not in an anchor position and is literal "
                            f"text: anchors go at the end of a list item or of a paragraph "
                            f"directly inside a section (§5.7).",
                        )
                        continue
                    anchor_id = m.group(1)
                    if not IDENTIFIER_RE.match(anchor_id):
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
    definition_refs = collect_definitions(document, language=document.metadata.language)
    auto_ids_seen: dict[str, str] = {}
    for ref in definition_refs:
        def_id = ref.id
        if not IDENTIFIER_RE.match(def_id):
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
                fragment, language=document.metadata.language
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
    # an Error (§15.8) rather than being downgraded to Info.
    _amends_import_succeeded = False
    _imported_definitions: dict[str, str] = {}
    if document.metadata.amends and document.metadata.amends.file:
        amends_file = document.metadata.amends.file
        _legaldown_exts = (".lgd", ".legaldown", ".legal.md")
        if any(amends_file.endswith(ext) for ext in _legaldown_exts):
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
    # must remain unique across the combined document (§15.10).
    if import_attachment_definitions is not None:
        _legaldown_exts = (".lgd", ".legaldown", ".legal.md")
        for att in document.metadata.attachments:
            if not att.file or not any(att.file.endswith(ext) for ext in _legaldown_exts):
                continue
            att_defs = import_attachment_definitions(att.file)
            if not att_defs:
                continue
            for def_id, term_text in att_defs.items():
                if def_id in result.definition_lookup:
                    result.error(
                        "def-duplicate-id",
                        f"Definition id '{def_id}' from attachment '{att.id}' collides with "
                        f"a definition in the main document (ids must be unique, §15.10).",
                    )
                else:
                    result.definition_lookup[def_id] = term_text

    # ── Inline directive validation ──
    placeholder_types: dict[str, str] = {}
    referenced_attachments: set[str] = set()

    # ── Frontmatter placeholders (§3.10) ──
    # A {{placeholder:}} is allowed only as a quoted value in *value* fields, not
    # in identifier/structural fields. Placeholders collected here share the same
    # blank (id + type) with any matching body placeholder.
    meta = document.metadata
    structural_fields: list[tuple[str, str]] = [("document_type", meta.document_type)]
    for side in meta.sides:
        structural_fields.append((f"side name '{side.name}'", side.name))
        for party in side.parties:
            structural_fields.append((f"party name '{party.name}'", party.name))
            structural_fields.append((f"party type for '{party.name}'", party.type))
    # A {{placeholder:}} is an error in identifier/structural fields (§3.10);
    # value fields (title, legal_name, address, …) allow it.
    for field_label, field_value in structural_fields:
        if _placeholders(field_value):
            result.error(
                "placeholder-in-structural-field",
                f"A {{{{placeholder:}}}} is not allowed in the identifier/structural "
                f"field ({field_label}); placeholders are only valid in value fields (§3.10).",
            )

    value_fields: list[str] = [
        meta.title, meta.subtitle, meta.version, meta.effective_date,
        meta.governing_law, meta.authoritative, meta.adopted_by,
        meta.adoption_date, meta.supersedes,
    ]
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
            if _check_directive_arguments(directive, result):
                _check_placeholder(directive, result, placeholder_types)

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
            lexed = lex(fragment)
            for _offset in lexed.stray_braces:
                result.warning(
                    "brace-stray",
                    "'{{' does not begin a directive and is literal text; "
                    "write '\\{{' if that is intended (§11.4).",
                )
            for directive in lexed.directives:
                name = directive.name
                if not _check_directive_arguments(directive, result):
                    continue
                # ── Unknown directive names (§11.5): well-formed only ──
                if name not in KNOWN_DIRECTIVES:
                    result.error(
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
                            f"Invalid duration value '{value}'. Must be a positive numeric value.",
                        )
                    if not dur_unit:
                        result.error(
                            "duration-invalid-unit",
                            "Duration directive missing required unit parameter.",
                        )
                    elif dur_unit == "M":
                        # §10.5: bare "M" is deliberately undefined (ISO 8601
                        # would read it as months; earlier drafts as minutes).
                        result.error(
                            "duration-invalid-unit",
                            "Duration unit 'M' is not defined. Use 'MIN' for minutes or 'MO' for months.",
                        )
                    elif dur_unit not in VALID_DURATION_UNITS:
                        result.error(
                            "duration-invalid-unit",
                            f"Invalid duration unit '{dur_unit}'. Must be one of: S, MIN, H, D, W, MO, Y.",
                        )
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
                    elif not IDENTIFIER_RE.match(ftype):
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
                elif name == "placeholder":
                    _check_placeholder(directive, result, placeholder_types)
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
                        # the reference may resolve there (§15.8).
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

    # Warn about declared but unreferenced attachments (§15.10).
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
