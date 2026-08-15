"""Core validation logic for LegalDown documents.

Every diagnostic is recorded under the **stable rule id** the specification
assigns to its check (§15.1) via ``ValidationResult.error/warning/info``,
so tooling can filter, suppress, or escalate specific rules consistently
across implementations (§15.9).
"""
from __future__ import annotations

import re
from collections.abc import Callable

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
    ATTACH_RE,
    DATE_RE,
    DIRECTIVE_NAME_RE,
    DURATION_RE,
    FIELD_RE,
    IDENTIFIER_RE,
    KNOWN_CURRENCIES,
    KNOWN_DIRECTIVES,
    MONEY_RE,
    NOTE_RE,
    PARTY_RE,
    PLACEHOLDER_RE,
    REF_RE,
    RESERVED_VALUE_TYPES,
    SIDE_RE,
    TERM_RE,
    VALID_DOC_TYPES,
    VALID_DURATION_UNITS,
    VALID_PLACEHOLDER_TYPES,
)
from .result import SectionIndexEntry, ValidationResult

# Type aliases for the optional definitions-import callbacks.
DefinitionsImporter = Callable[[str, str], dict[str, str] | None]
AttachmentDefinitionsImporter = Callable[[str], dict[str, str] | None]

# Frontmatter fields that are identifiers/structural — a {{placeholder:}} here is
# an error (§3.10). Value fields (title, legal_name, address, …) are allowed.
_PLACEHOLDER_LITERAL = "{{placeholder:"

# §15.6: a metadata value that is itself a placeholder satisfies presence and
# is exempt from the field's format checks; the placeholder's own checks apply.
def _is_placeholder_value(value: str) -> bool:
    return _PLACEHOLDER_LITERAL in (value or "")


# §11.4: directives and anchor markers are not recognized inside inline code
# spans, fenced/indented code blocks, or HTML comments — directive-like text
# there is literal. Blanking those regions keeps their content out of every
# scan below without shifting any offsets.
_CODE_SPAN_RE = re.compile(r"``.*?``|`[^`\n]*`", re.DOTALL)
_FENCED_CODE_RE = re.compile(r"^(?P<fence>```|~~~).*?^(?P=fence)", re.DOTALL | re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_uninterpreted(fragment: str) -> str:
    """Blank out code spans, code blocks, and comments, preserving length."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    text = _FENCED_CODE_RE.sub(_blank, fragment or "")
    text = _HTML_COMMENT_RE.sub(_blank, text)
    return _CODE_SPAN_RE.sub(_blank, text)


def _check_placeholder(
    match: re.Match,
    result: ValidationResult,
    placeholder_types: dict[str, str],
) -> None:
    """Validate one ``{{placeholder:}}`` match and record it.

    Shared by the body-block scan and the frontmatter scan so a placeholder id
    used in both is treated as the *same* blank (consistent type, single entry
    semantics per §3.10).
    """
    pid = match.group(1).strip()
    ptype = (match.group(2) or "text").strip()
    pcurrency = (match.group(3) or "").strip()
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
        if section.level < 1 or section.level > 5:
            result.error(
                "heading-depth",
                f"Section '{section.title}' uses unsupported heading level "
                f"{section.level}. LegalDown supports levels 1-5 (§4.1).",
            )
            continue
        if last_level > 0 and section.level - last_level > 1:
            result.error(
                "heading-skip",
                f"Heading levels must not skip. '{section.title}' jumps from level {last_level} to {section.level}.",
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

        for idx in range(section.level, 7):
            if idx == section.level:
                counters[idx] += 1
            elif idx > section.level:
                counters[idx] = 0
        path_stack = path_stack[: max(section.level - 1, 0)]
        path_stack.append(identifier)
        number = format_section_number(counters, section.level)
        entry = SectionIndexEntry(
            title=section.title,
            identifier=identifier,
            path=".".join(path_stack),
            level=section.level,
            number=number,
        )
        result.sections.append(entry)
        result.section_lookup[identifier] = entry
        result.section_lookup[entry.path] = entry
        last_level = section.level

    # ── Item and paragraph anchors (§5.7) ──
    # A trailing {#id} on a list item or paragraph joins the shared anchor
    # namespace and is a valid {{ref:}} target. It resolves to its containing
    # section (the §13.2 enumeration-path refinement is a Rendering-level
    # concern; §6.3 falls back to the containing section's number).
    from ..definitions import text_fragments as _all_text_fragments

    _trailing_anchor_re = re.compile(r"\{#([^}\s]+)\}\s*$")
    for entry in list(result.sections):
        section = next(
            (s for s in document.sections if s.identifier == entry.identifier), None
        )
        if section is None:
            continue
        for block in section.blocks:
            for fragment in (
                _strip_uninterpreted(f) for f in _all_text_fragments(block)
            ):
                m = _trailing_anchor_re.search(fragment)
                if not m:
                    continue
                anchor_id = m.group(1)
                if not IDENTIFIER_RE.match(anchor_id):
                    result.error(
                        "anchor-format",
                        f"Anchor '{{#{anchor_id}}}' is invalid. Use lowercase letters, numbers, and hyphens only.",
                    )
                    continue
                if anchor_id in used_identifiers:
                    result.error(
                        "anchor-duplicate",
                        f"Anchor '{{#{anchor_id}}}' duplicates an existing anchor in the document.",
                    )
                    continue
                used_identifiers.add(anchor_id)
                result.section_lookup[anchor_id] = entry

    # ── Definitions (§7) ──
    # The mandatory, first-positioned Definitions section is gone (§7.2). A
    # definition is a quoted term followed by a ``{{def: id}}`` anchor, declared
    # either as a leading-anchor "definition" block or inline at first use, and
    # may appear anywhere. Imported lazily to avoid a module-level import cycle
    # (definitions -> validator.helpers -> validator/__init__ -> core).
    from ..definitions import (
        DEF_ANCHOR_RE,
        DEF_TAG_RE,
        EMPHASIS_DEF_RE,
        collect_definitions,
        extract_def,
        is_single_quoted,
        text_fragments,
    )

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
    for section in document.sections:
        for block in section.blocks:
            for fragment in text_fragments(block):
                if EMPHASIS_DEF_RE.search(fragment):
                    result.warning(
                        "def-emphasis",
                        "Defined term wrapped in emphasis markers in source. Quotation marks "
                        "alone delimit a defined term; emphasis is a render-time style.",
                    )
                anchored_ends = {m.end() for m in DEF_ANCHOR_RE.finditer(fragment)}
                for tag in DEF_TAG_RE.finditer(fragment):
                    if tag.end() not in anchored_ends:
                        result.error(
                            "def-no-quoted-span",
                            "A {{def:}} anchor must immediately follow a quoted defined term.",
                        )
                for m in DEF_ANCHOR_RE.finditer(fragment):
                    if is_single_quoted(m):
                        result.warning(
                            "def-single-quote-ambiguous",
                            f"Single-quoted defined term '{extract_def(m)[0]}' may be ambiguous "
                            f"with an apostrophe (U+2019); prefer double-quote delimiters.",
                        )

    # ── Amendment definition import (§7.5) ──
    _amends_is_legaldown = False
    _imported_definitions: dict[str, str] = {}
    if document.metadata.amends and document.metadata.amends.file:
        amends_file = document.metadata.amends.file
        _legaldown_exts = (".lgd", ".legaldown", ".legal.md")
        if any(amends_file.endswith(ext) for ext in _legaldown_exts):
            _amends_is_legaldown = True
            if import_definitions is not None:
                imported = import_definitions(amends_file, document.filename)
                if imported is not None:
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
    for field_label, field_value in structural_fields:
        if field_value and _PLACEHOLDER_LITERAL in field_value:
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
        if field_value and _PLACEHOLDER_LITERAL in field_value:
            for m in PLACEHOLDER_RE.finditer(field_value):
                _check_placeholder(m, result, placeholder_types)

    for section in document.sections:
        for block in section.blocks:
            ref_targets: list[str] = []
            term_targets: list[str] = []
            if block.kind == "ref" and block.target.strip():
                ref_targets.append(block.target.strip())
            if block.kind == "term" and block.target.strip():
                term_targets.append(block.target.strip())
            text_fragments: list[str] = []
            if block.text:
                text_fragments.append(block.text)
            if block.prefix:
                text_fragments.append(block.prefix)
            if block.suffix:
                text_fragments.append(block.suffix)
            text_fragments.extend(item for item in block.items if item)
            text_fragments.extend(cell for row in block.rows for cell in row if cell)
            # §11.4: directive-like text inside code spans/blocks and comments
            # is literal — never a directive, never a diagnostic.
            for fragment in (_strip_uninterpreted(f) for f in text_fragments):
                # ── Unknown directive names (§11.5) ──
                for m in DIRECTIVE_NAME_RE.finditer(fragment):
                    name = m.group(1)
                    if name not in KNOWN_DIRECTIVES:
                        result.error(
                            "directive-unknown",
                            f"Unknown directive '{{{{{name}:}}}}'. Renderers replace it "
                            f"with [UNKNOWN DIRECTIVE: {name}] (§11.5).",
                        )
                ref_targets.extend(
                    match.group(1).strip() for match in REF_RE.finditer(fragment)
                )
                term_targets.extend(
                    match.group(1).strip() for match in TERM_RE.finditer(fragment)
                )
                for m in DATE_RE.finditer(fragment):
                    date_val = m.group(1).strip()
                    result.inline_dates.append(date_val)
                    if not is_valid_iso_date(date_val):
                        result.error(
                            "date-invalid",
                            f"Invalid date value '{date_val}'. Must be a valid ISO 8601 date (YYYY-MM-DD).",
                        )
                for m in MONEY_RE.finditer(fragment):
                    amount = m.group(1).strip()
                    currency = (m.group(2) or "").strip()
                    result.inline_money.append((amount, currency))
                    if not is_valid_money_amount(amount):
                        result.error(
                            "money-invalid-amount",
                            f"Invalid money amount '{amount}'. Must be a non-negative numeric value.",
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
                for m in DURATION_RE.finditer(fragment):
                    dur_val = m.group(1).strip()
                    dur_unit = (m.group(2) or "").strip()
                    result.inline_durations.append((dur_val, dur_unit))
                    if not is_positive_numeric(dur_val):
                        result.error(
                            "duration-invalid-value",
                            f"Invalid duration value '{dur_val}'. Must be a positive numeric value.",
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
                for m in PARTY_RE.finditer(fragment):
                    party_id = m.group(1).strip()
                    if not party_id or not IDENTIFIER_RE.fullmatch(party_id):
                        result.error(
                            "party-name-malformed",
                            f"Party directive has invalid role value '{party_id}'. Must match [a-z][a-z0-9-]*.",
                        )
                    elif party_id not in result.party_lookup:
                        result.error(
                            "party-unknown",
                            f"Party directive references unknown party: '{party_id}'.",
                        )
                for m in SIDE_RE.finditer(fragment):
                    side_id = m.group(1).strip()
                    if not side_id or not IDENTIFIER_RE.fullmatch(side_id):
                        result.error(
                            "side-name-malformed",
                            f"Side directive has invalid value '{side_id}'. Must match [a-z][a-z0-9-]*.",
                        )
                    elif side_id not in seen_side_names:
                        result.error(
                            "side-unknown",
                            f"Side directive references unknown side: '{side_id}'.",
                        )
                for m in FIELD_RE.finditer(fragment):
                    fval = m.group(1).strip()
                    ftype = (m.group(2) or "").strip()
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
                    result.inline_fields.append((fval, ftype))
                for m in PLACEHOLDER_RE.finditer(fragment):
                    _check_placeholder(m, result, placeholder_types)
                for m in ATTACH_RE.finditer(fragment):
                    att_id = m.group(1).strip()
                    referenced_attachments.add(att_id)
                    if att_id not in attachment_ids:
                        result.error(
                            "attach-undeclared",
                            f"Attachment reference '{{{{attach: {att_id}}}}}' references undeclared attachment id.",
                        )
                for m in NOTE_RE.finditer(fragment):
                    note_val = m.group(1)
                    if "," in note_val or "}}" in note_val:
                        result.error(
                            "note-invalid",
                            "Note parameter must not contain commas or closing braces.",
                        )
                    elif re.search(r"\*\*|__|\+\+|`|(?<!\w)\*(?!\s)", note_val):
                        # §10.1: notes are plain text — Markdown formatting
                        # would leak markers into machine-facing output.
                        result.error(
                            "note-invalid",
                            "Note parameter must be plain text without Markdown formatting.",
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
                        if _amends_is_legaldown and _imported_definitions:
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
