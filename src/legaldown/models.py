"""LegalDown document model.

Defines the canonical dataclasses for representing a LegalDown document
in memory, plus factory functions for safe construction from dicts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Representative:
    """A person acting on behalf of a party (e.g. CEO, attorney)."""
    name: str = ""
    title: str = ""


@dataclass(slots=True)
class CustomField:
    """An arbitrary key-value pair attached to a party."""
    label: str = ""
    value: str = ""


@dataclass(slots=True)
class Amends:
    """Reference to the document this one amends."""
    title: str = ""
    file: str = ""


@dataclass(slots=True)
class Attachment:
    """A file attachment declared in document metadata."""
    id: str = ""
    title: str = ""
    file: str = ""


@dataclass(slots=True)
class Party:
    """A contractual party (legal entity or natural person)."""
    name: str = ""
    label: str = ""
    type: str = "legal_entity"
    legal_name: str = ""
    identification_number: str = ""
    address: str = ""
    date_of_birth: str = ""
    representatives: list[Representative] = field(default_factory=list)
    custom_fields: list[CustomField] = field(default_factory=list)


@dataclass(slots=True)
class Side:
    """A contractual side grouping one or more parties."""
    name: str = ""
    label: str = ""
    parties: list[Party] = field(default_factory=list)


@dataclass(slots=True)
class Metadata:
    """Document-level metadata from YAML frontmatter."""
    title: str = "Untitled Agreement"
    subtitle: str = ""
    version: str = ""
    document_type: str = "contract"
    effective_date: str = ""
    field_types: dict[str, str] = field(default_factory=dict)
    governing_law: str = ""
    language: str = "en"
    include_signatures: bool = True
    tags: list[str] = field(default_factory=list)
    sides: list[Side] = field(default_factory=list)
    translations: dict[str, str] = field(default_factory=dict)
    authoritative: str = ""
    adopted_by: str = ""
    adoption_date: str = ""
    supersedes: str = ""
    amends: Amends | None = None
    attachments: list[Attachment] = field(default_factory=list)


@dataclass(slots=True)
class Block:
    """A content block within a section (paragraph, definition, list, etc.)."""
    kind: str = "paragraph"
    text: str = ""
    definition_id: str = ""
    term: str = ""
    prefix: str = ""
    suffix: str = ""
    target: str = ""
    format: str = ""
    label: str = ""
    items: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass(slots=True)
class Section:
    """A headed section of the document."""
    title: str = "New Section"
    level: int = 1
    identifier: str = ""
    blocks: list[Block] = field(default_factory=list)


@dataclass(slots=True)
class Document:
    """Top-level container representing a complete LegalDown document."""
    metadata: Metadata = field(default_factory=Metadata)
    sections: list[Section] = field(default_factory=list)
    filename: str = ""


# ---------------------------------------------------------------------------
# Block defaults — canonical initial values per block kind
# ---------------------------------------------------------------------------

BLOCK_DEFAULTS: dict[str, dict[str, Any]] = {
    "paragraph": {"kind": "paragraph", "text": ""},
    "definition": {"kind": "definition", "definition_id": "", "term": "", "text": ""},
    "ref": {"kind": "ref", "prefix": "", "target": "", "format": "", "suffix": ""},
    "term": {"kind": "term", "prefix": "", "target": "", "suffix": "", "label": ""},
    "unordered_list": {"kind": "unordered_list", "items": [""]},
    "ordered_list": {"kind": "ordered_list", "items": [""]},
    "quote": {"kind": "quote", "text": ""},
    "table": {"kind": "table", "headers": ["Column 1", "Column 2"], "rows": [["", ""]]},
    "rule": {"kind": "rule"},
}


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _str(value: Any, default: str = "") -> str:
    """Coerce a value to a stripped string, falling back to *default*."""
    result = str(value or "").strip()
    return result or default


def _clean_list(values: list) -> list[str]:
    """Strip and filter empty strings from a list."""
    return [str(v).strip() for v in values if str(v).strip()]


def _clean_rows(rows: list[list]) -> list[list[str]]:
    """Strip cells and drop fully-empty rows."""
    return [
        [str(cell).strip() for cell in row]
        for row in rows
        if any(str(cell).strip() for cell in row)
    ]


def _to_bool(value: Any, *, default: bool = False) -> bool:
    """Parse a truthy value from various representations."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_str_dict(raw: Any) -> dict[str, str]:
    """Parse a dict with string keys and values, skipping empty entries."""
    if not isinstance(raw, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in raw.items() if k and v}


# ---------------------------------------------------------------------------
# Factory functions — safe construction from untrusted dicts
# ---------------------------------------------------------------------------


def block_from_dict(data: dict[str, Any] | None) -> Block:
    """Construct a Block from a dict, applying defaults for missing fields."""
    payload = data or {}
    kind = str(payload.get("kind") or "paragraph")
    defaults = BLOCK_DEFAULTS.get(kind, BLOCK_DEFAULTS["paragraph"])
    merged = {**defaults, **payload}
    return Block(
        kind=kind,
        text=_str(merged.get("text")),
        definition_id=_str(merged.get("definition_id")),
        term=_str(merged.get("term")),
        prefix=str(merged.get("prefix") or ""),
        suffix=str(merged.get("suffix") or ""),
        target=_str(merged.get("target")),
        format=_str(merged.get("format")),
        label=_str(merged.get("label")),
        items=_clean_list(list(merged.get("items") or []))
        or ([""] if kind.endswith("list") else []),
        headers=_clean_list(list(merged.get("headers") or [])),
        rows=_clean_rows(list(merged.get("rows") or [])),
    )


def section_from_dict(data: dict[str, Any] | None) -> Section:
    """Construct a Section from a dict."""
    payload = data or {}
    return Section(
        title=_str(payload.get("title"), "New Section"),
        level=max(1, min(6, int(payload.get("level") or 1))),
        identifier=_str(payload.get("identifier")),
        blocks=[block_from_dict(b) for b in list(payload.get("blocks") or [])],
    )


def party_from_dict(data: dict[str, Any] | None) -> Party:
    """Construct a Party from a frontmatter dict (§3.6).

    Values are taken verbatim: an unknown ``type`` or a non-identifier ``name``
    is preserved so the validator can report party-type-invalid or
    side-party-name-format (§15.6) instead of the model silently repairing the
    document.
    """
    payload = data or {}

    return Party(
        name=_str(payload.get("name")),
        label=_str(payload.get("label")),
        type=_str(payload.get("type"), "legal_entity"),
        legal_name=_str(payload.get("legal_name")),
        identification_number=_str(payload.get("identification_number")),
        address=_str(payload.get("address")),
        date_of_birth=_str(payload.get("date_of_birth")),
        representatives=[
            Representative(name=_str(r.get("name")), title=_str(r.get("title")))
            for r in list(payload.get("representatives") or [])
            if isinstance(r, dict)
        ],
        custom_fields=[
            CustomField(label=_str(cf.get("label")), value=_str(cf.get("value")))
            for cf in list(payload.get("custom_fields") or [])
            if isinstance(cf, dict)
        ],
    )


def side_from_dict(data: dict[str, Any] | None) -> Side:
    """Construct a Side and its parties from a frontmatter dict (§3.5).

    A non-identifier side name is preserved verbatim so the validator can
    report side-name-malformed (§15.6) instead of a silent repair.
    """
    payload = data or {}

    return Side(
        name=_str(payload.get("name")),
        label=_str(payload.get("label")),
        parties=[
            party_from_dict(p)
            for p in list(payload.get("parties") or [])
            if isinstance(p, dict)
        ],
    )


def metadata_from_dict(data: dict[str, Any] | None) -> Metadata:
    """Construct Metadata from a parsed frontmatter dict (§3)."""
    payload = data or {}

    # Tags (accept comma-separated string or list)
    tags = payload.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",")]

    # Sides (§3.5). Absent or malformed, the validator reports sides-absent.
    raw_sides = payload.get("sides")
    sides: list[Side] = (
        [side_from_dict(s) for s in raw_sides if isinstance(s, dict)]
        if isinstance(raw_sides, list)
        else []
    )

    # Amends
    raw_amends = payload.get("amends")
    amends = (
        Amends(title=_str(raw_amends.get("title")), file=_str(raw_amends.get("file")))
        if isinstance(raw_amends, dict)
        else None
    )

    # Attachments
    attachments = [
        Attachment(id=_str(a.get("id")), title=_str(a.get("title")), file=_str(a.get("file")))
        for a in (payload.get("attachments") or [])
        if isinstance(a, dict)
    ]

    return Metadata(
        # No default title: a missing title is a validation error (§15.6,
        # title-missing); app flows that create fresh documents supply one.
        title=_str(payload.get("title")),
        subtitle=_str(payload.get("subtitle")),
        version=_str(payload.get("version")),
        document_type=_str(payload.get("document_type"), "contract"),
        effective_date=_str(payload.get("effective_date")),
        field_types=_parse_str_dict(payload.get("field_types")),
        governing_law=_str(payload.get("governing_law")),
        language=_str(payload.get("language"), "en"),
        include_signatures=_to_bool(payload.get("include_signatures"), default=True),
        tags=_clean_list(list(tags)),
        sides=sides,
        translations=_parse_str_dict(payload.get("translations")),
        authoritative=_str(payload.get("authoritative")),
        adopted_by=_str(payload.get("adopted_by")),
        adoption_date=_str(payload.get("adoption_date")),
        supersedes=_str(payload.get("supersedes")),
        amends=amends,
        attachments=attachments,
    )


def document_from_dict(data: dict[str, Any] | None) -> Document:
    """Construct a full Document from a nested dict."""
    payload = data or {}
    return Document(
        metadata=metadata_from_dict(payload.get("metadata") or {}),
        sections=[section_from_dict(s) for s in list(payload.get("sections") or [])],
        filename=_str(payload.get("filename")),
    )


def document_to_dict(document: Document) -> dict[str, Any]:
    """Convert a Document to a plain dict (for JSON serialization etc.)."""
    return asdict(document)


def empty_document() -> Document:
    """Create a new document with sensible starter content."""
    return Document(
        metadata=Metadata(
            title="Untitled Agreement",
            language="en",
            include_signatures=True,
            sides=[
                Side(
                    name="providers", label="Providers",
                    parties=[Party(
                        name="provider-llc", label="Provider",
                        type="legal_entity", legal_name="Provider LLC",
                        representatives=[Representative()],
                    )],
                ),
                Side(
                    name="clients", label="Clients",
                    parties=[Party(
                        name="client-llc", label="Client",
                        type="legal_entity", legal_name="Client LLC",
                        representatives=[Representative()],
                    )],
                ),
            ],
        ),
        sections=[
            Section(
                title="Definitions", level=1, identifier="definitions",
                blocks=[Block(
                    kind="definition", definition_id="services",
                    term="Services",
                    text="means the services described in this agreement.",
                )],
            ),
            Section(
                title="Scope of Work", level=1, identifier="scope-of-work",
                blocks=[Block(kind="paragraph", text="Describe the scope of work.")],
            ),
        ],
    )
