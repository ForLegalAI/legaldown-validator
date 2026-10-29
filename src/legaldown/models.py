"""LegalDown document model.

Defines the canonical dataclasses for representing a LegalDown document
in memory, plus factory functions for safe construction from dicts (public
as ``Document.from_dict`` and ``Document.to_dict``).
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .markdown import LINE_ENDING_RE, is_blank, no_blank_lines, strip_text

if TYPE_CHECKING:
    from .parser import FrontmatterError
    from .positions import SourceLayout

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
    """Reference to the document this one amends (§3.8); also the object form
    of ``supersedes``, which has the same fields."""
    title: str = ""
    file: str = ""


@dataclass(slots=True)
class Attachment:
    """A file attachment declared in document metadata."""
    id: str = ""
    title: str = ""
    file: str = ""
    #: In a template, the condition under which the attachment is present
    #: (§15.3), as written.
    when: str = ""


@dataclass(slots=True)
class Party:
    """A contractual party (legal entity or natural person)."""
    name: str = ""
    label: str = ""
    #: legal_entity or natural_person. REQUIRED (§3.4) and deliberately not
    #: defaulted: an unset type must reach the validator as party-type-invalid
    #: rather than be silently chosen here.
    type: str = ""
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
    #: The document's language (ISO 639-1), or ``""`` when it says none: §3.2 gives it no default,
    #: so none is invented here (as for ``Party.type``). ``empty_document`` says ``"en"``.
    language: str = ""
    include_signatures: bool = True
    tags: list[str] = field(default_factory=list)
    sides: list[Side] = field(default_factory=list)
    translations: dict[str, str] = field(default_factory=dict)
    authoritative: str = ""
    adopted_by: str = ""
    adoption_date: str = ""
    #: A description of the superseded document, or the object form (§3.2).
    supersedes: str | Amends = ""
    amends: Amends | None = None
    attachments: list[Attachment] = field(default_factory=list)
    #: The specification version the document targets (§3.2).
    legaldown: str = ""
    #: The template questions (§15.2) as written — kept raw so the validator
    #: can report a malformed declaration. ``None`` when the document
    #: declares none.
    questions: Any = None
    #: Frontmatter keys (``questions``, ``attachments``) the parsed source
    #: does not write as §15.2 requires for assembly to edit them line by
    #: line: in YAML block style, each attachment entry beginning with
    #: ``id``. Only ``parse`` and ``load`` set it: a document built or rebuilt
    #: from a dict has no source, and the serializer writes block style.
    not_line_editable: list[str] = field(default_factory=list)
    #: True when the parsed source has no frontmatter (§3.1): no ``---``
    #: block opens it, or the block's YAML is not a mapping of fields, in
    #: which case the source is all body. Only ``parse`` and ``load`` set it,
    #: like ``not_line_editable``.
    frontmatter_absent: bool = False


@dataclass(slots=True)
class ListItem:
    """A list item: the blocks it holds (§5.7), in order — its first
    paragraph, then later paragraphs, headings, nested lists, code, quotes,
    tables. An empty item holds none. Blocks in an item are never sections:
    a heading in one is a ``heading`` block (§4.1), and its paragraphs are
    always of kind ``paragraph``: a directive in one is inline text (§8)."""
    blocks: list[Block] = field(default_factory=list)


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
    label: str = ""
    #: A ``link_definition`` block's title, raw: without its delimiters, and
    #: with backslash escapes and character references as written (§4.7, #92).
    #: Its ``label`` and ``target`` are raw too. "" for other blocks, and for
    #: a definition without one.
    title: str = ""
    #: A list's items (``ordered_list``, ``unordered_list``).
    items: list[ListItem] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    #: A table's column alignments, one per column: ``"left"``, ``"right"``,
    #: ``"center"``, or ``""`` for none (§9.1).
    align: list[str] = field(default_factory=list)
    #: A ``heading`` block's level, 1 to 6: a heading in a list item or a
    #: block quote, which is not a section (§4.1). 0 for other blocks.
    level: int = 0


LIST_KINDS = ("ordered_list", "unordered_list")


def list_items(block: Block) -> list[ListItem]:
    """A list's items, each a ``ListItem`` — one written as a string, as a
    model built in code may hold it, read as its content, as ``Document.from_dict`` reads one."""
    return [item if isinstance(item, ListItem) else _list_item(item) for item in block.items]


def item_text(item: ListItem) -> str:
    """An item's text: its first paragraph's (its lines joined with LF), or
    ``""`` when it opens with something else or is empty. Link reference
    definitions before it are no text (#92): ``- [x]: u`` then a paragraph
    in the item is its paragraph's."""
    first = next((block for block in item.blocks if block.kind != "link_definition"), None)
    return first.text if first is not None and first.kind == "paragraph" else ""


#: The title of a section whose heading has none.
DEFAULT_SECTION_TITLE = "New Section"


@dataclass(slots=True)
class Section:
    """A headed section of the document."""
    title: str = DEFAULT_SECTION_TITLE
    level: int = 1
    #: The explicit identifier (§5.2), or ``""`` when the heading has none
    #: and one is generated (§5.3).
    identifier: str = ""
    blocks: list[Block] = field(default_factory=list)
    #: In a template, the condition under which the section is present
    #: (§15.3), as written.
    condition: str = ""


@dataclass(slots=True)
class Document:
    """Top-level container representing a complete LegalDown document.

    ``preamble`` holds the body content before the first heading (§4.4): it
    is unnumbered and cannot be referenced, so it is not a section.
    """
    metadata: Metadata = field(default_factory=Metadata)
    sections: list[Section] = field(default_factory=list)
    filename: str = ""
    preamble: list[Block] = field(default_factory=list)
    #: Where the parsed source holds its parts (``positions.SourceMap``), for
    #: diagnostics that name their line (§16.9). Only ``parse`` and ``load``
    #: set it; it describes the source as parsed, so a document changed afterwards
    #: may no longer fit it (then its diagnostics name no line). Not part of
    #: equality or of ``to_dict``.
    source_map: Any = field(default=None, compare=False, repr=False)
    #: The file the document was loaded from, absolute (``load`` sets it), for
    #: resolving the files it refers to. ``None`` for a document
    #: parsed from a string or built in code. Not part of equality or of
    #: ``to_dict``: where a document lives is not what it says.
    path: Path | None = field(default=None, compare=False)
    #: Why the frontmatter could not be read, for a document parsed with
    #: ``on_frontmatter_error="keep"`` (#43): ``validate`` reports it as
    #: frontmatter-invalid-yaml, and the document, whose metadata is then empty,
    #: cannot be written out (``to_dict``, ``serialize`` and ``save`` raise it).
    #: ``None`` for any other document. Not part of equality or of ``to_dict``,
    #: like ``path``: it describes the source, not what the document says.
    frontmatter_error: FrontmatterError | None = field(default=None, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Document:
        """A document built from the nested dict *data*, as ``to_dict`` gives
        it or as JSON holds it: missing fields take their defaults, values are
        read as strings, and a list item may be written as a string, its
        content as written after its marker (the item form of models before
        0.3). ``None`` reads as ``{}``.

        A document built from a dict has no ``source_map`` and no ``path``,
        and the fields that describe a parsed source,
        ``Metadata.not_line_editable`` and ``Metadata.frontmatter_absent``,
        are left at their defaults: only ``parse`` and ``load`` set them (and
        ``Document.frontmatter_error``, which is ``None``)."""
        return document_from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        """The document as a plain, JSON-friendly dict — its metadata, sections,
        filename and preamble — that ``Document.from_dict`` reads back. Not
        part of it: ``source_map`` and ``path``, which describe where the
        document was read from rather than what it says (and
        ``Metadata.not_line_editable`` and ``Metadata.frontmatter_absent``,
        which it holds but ``from_dict`` does not read).

        Raises a ``FrontmatterError`` for a document whose frontmatter could
        not be read (``parse(on_frontmatter_error="keep")``), as
        ``document_to_dict`` does: its empty metadata would not say what the
        source does."""
        return document_to_dict(self)

    def iter_indexed_blocks(self) -> Iterator[tuple[int | None, int, Block]]:
        """Every body block in document order, as ``(section_index, index,
        block)``: the preamble first, with ``section_index`` ``None``."""
        for index, block in enumerate(self.preamble):
            yield None, index, block
        for section_index, section in enumerate(self.sections):
            for index, block in enumerate(section.blocks):
                yield section_index, index, block

    def layout(self) -> SourceLayout | None:
        """Where the document's frontmatter, headings, blocks and list items
        lie in the file it was parsed from (§16.9): file lines counted from
        1, each span the lines ``[start, end)`` (end exclusive; the blank
        lines between parts belong to none, but for those between a list's
        items). None for a document
        built in code, and for one changed since it was parsed so that it
        no longer fits its source: where its parts lie is not known then."""
        from .positions import source_layout  # positions describes documents: it builds on this module

        return source_layout(self)

    def line_of(self, section: int | None, block: int | None = None, item: int | None = None) -> int | None:
        """The first line, counted from 1, of a part of the document in its
        file: section *section*'s heading (*block* None); a top-level block,
        of a section or, with *section* None, of the preamble; or a list
        item's marker, *item* counted in pre-order among all the list's
        items, nested ones included (``list_fragments``). These are the
        lines its diagnostics name (§16.9). None when the document has no
        source to name a line of (``layout``). Raises ``IndexError`` for an
        index out of range, and ``ValueError`` for a preamble without a
        block, an item without a block, or an item of a block that is not a
        list."""
        from .positions import line_of

        return line_of(self, section, block, item)

    def iter_blocks(self) -> Iterator[tuple[Section | None, int, Block]]:
        """Every body block in document order, as ``(section, index, block)``:
        the preamble first, with ``section`` ``None``, then each section's."""
        for section_index, index, block in self.iter_indexed_blocks():
            yield (None if section_index is None else self.sections[section_index]), index, block


# ---------------------------------------------------------------------------
# Block defaults — canonical initial values per block kind
# ---------------------------------------------------------------------------

BLOCK_DEFAULTS: dict[str, dict[str, Any]] = {
    "paragraph": {"kind": "paragraph", "text": ""},
    "definition": {"kind": "definition", "definition_id": "", "term": "", "text": ""},
    "ref": {"kind": "ref", "prefix": "", "target": "", "suffix": ""},
    "term": {"kind": "term", "prefix": "", "target": "", "suffix": "", "label": ""},
    "unordered_list": {"kind": "unordered_list", "items": [""]},
    "ordered_list": {"kind": "ordered_list", "items": [""]},
    "quote": {"kind": "quote", "text": ""},
    # A fenced code block, fences included; its content is literal (§11.4).
    "code": {"kind": "code", "text": ""},
    # An HTML block (CommonMark), a comment included: raw source, not
    # rendered (§8.6, §8.7), and no directive is recognized in it (§11.4).
    "html": {"kind": "html", "text": ""},
    "table": {"kind": "table", "headers": ["Column 1", "Column 2"], "rows": [["", ""]]},
    "rule": {"kind": "rule"},
    # A heading in a list item or a block quote: not a section (§4.1), so
    # not numbered and without an identifier. Its text is one line.
    "heading": {"kind": "heading", "text": "", "level": 1},
    # A link reference definition (CommonMark §4.7): ``[label]: target "title"``,
    # each raw as written. It is no text: the validator reads no directive in
    # one (a definition holding ``{{`` stays a paragraph, #92).
    "link_definition": {"kind": "link_definition", "label": "", "target": "", "title": ""},
    # Markdown source a caller built, such as an editor's row: written as it
    # stands (``serialize``), never produced by the parser and not
    # read by the validator. Read back, it is the blocks it holds.
    "source": {"kind": "source", "text": ""},
}


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def normalise_field(value: Any, default: str = "") -> str:
    """Coerce a value to a stripped string, falling back to *default*. The rule
    for the identifiers, terms, targets and labels of a block, and a section's
    title, identifier and condition: ``block_from_dict`` and
    ``section_from_dict`` apply it, and so does the parser where it builds them
    (#110)."""
    result = str(value or "").strip()
    return result or default


def normalise_title(value: Any) -> str:
    """A section's title: stripped, ``DEFAULT_SECTION_TITLE`` when it is empty."""
    return normalise_field(value, DEFAULT_SECTION_TITLE)


def _clean_list(values: list) -> list[str]:
    """Strip and filter empty strings from a list."""
    return [str(v).strip() for v in values if str(v).strip()]


def _cell(value: Any) -> str:
    return strip_text(str(value if value is not None else ""))


def _table_rows(rows: list, width: int) -> list[list[str]]:
    """Strip cells, keeping empty ones and empty rows: a table's cells are
    positional. With a header (*width* > 0), each row is padded with empty
    cells or cut to its width, as GFM renders it."""
    cleaned = [[_cell(cell) for cell in (row if isinstance(row, (list, tuple)) else [row])] for row in rows]
    if not width:
        return cleaned
    return [row[:width] + [""] * (width - len(row)) for row in cleaned]


_ALIGNMENTS = frozenset({"left", "right", "center"})


def _table_align(values: list, width: int) -> list[str]:
    """A table's column alignments: an unknown value is none (``""``), and
    with a header there is one per column."""
    align = [value if value in _ALIGNMENTS else "" for value in (_cell(v).lower() for v in values)]
    if not width:
        return align
    return align[:width] + [""] * (width - len(align))


def _to_bool(value: Any, *, default: bool = False) -> bool:
    """Parse a truthy value from various representations."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_str_dict(raw: Any, *, skip_blank: bool = False) -> dict[str, str]:
    """Parse a dict with string keys and values, stripped, skipping an entry whose key or value is
    null or empty (a whitespace-only one is kept, stripped to ``""``). With *skip_blank* (``translations``:
    a blank key names no language, a blank value no file; ``field_types`` keeps a blank description,
    which is a declaration) it skips one that is blank once stripped too, before it can overwrite a
    sibling written differently (``fr`` and ``' fr'``); the last of the rest stays. An exact duplicate
    key is resolved by YAML before this reads it (#126)."""
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, str] = {}
    for k, v in raw.items():
        if not (k and v):
            continue
        key, value = str(k).strip(), str(v).strip()
        if not skip_blank or (key and value):
            parsed[key] = value
    return parsed


# ---------------------------------------------------------------------------
# Factory functions — safe construction from untrusted dicts
# ---------------------------------------------------------------------------


def normalise_text(kind: str, value: Any) -> str:
    """A block's text. A code block's is literal source, indentation
    included. A block quote keeps a leading blank line, which decides whether
    it is a drafting note (§15.6). The rule ``block_from_dict`` applies, and the
    parser where it builds a quote or an HTML block (#110)."""
    text = str(value or "")
    if kind == "code":
        return text
    if kind in ("html", "source"):
        # Kept as written, but for blank lines at its ends, which the
        # serializer's block separation would not keep.
        lines = LINE_ENDING_RE.sub("\n", text).split("\n")
        while lines and is_blank(lines[0]):
            lines.pop(0)
        return "\n".join(lines).rstrip(" \t\r\n")
    if kind == "heading":
        return heading_text(text)
    if kind == "quote":
        # A line's indentation is content: four columns make it code; so is
        # its trailing whitespace, which may be a hard break. A blank line is
        # written as ">" (``serializer``), and those at the end not at all.
        lines = ["" if is_blank(line) else line for line in text.split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)
    # Only spaces, tabs and line endings are stripped (CommonMark): other
    # whitespace, such as a no-break space, is text. A paragraph's lines are
    # joined with LF (``paragraph_text``).
    return strip_text(LINE_ENDING_RE.sub("\n", text))


def normalise_link_definition(label: Any, target: Any, title: Any) -> tuple[str, str, str]:
    """The *label*, *target* and *title* of a link reference definition block, as
    ``block_from_dict`` writes them: a blank line would end the paragraph that
    holds a label or a title (``no_blank_lines``), and a line break cannot be in a
    destination written in ``<>``; the title is raw, its spaces its own."""
    return (
        no_blank_lines(normalise_field(label)),
        LINE_ENDING_RE.sub("", normalise_field(target)),
        no_blank_lines(str(title or "")),
    )


def _list_item(value: Any) -> ListItem:
    """A list item from a dict (``{"blocks": [...]}``), or from its text as
    written in the item (a string, the item form of models before 0.3), read
    as the parser reads an item's content."""
    if isinstance(value, ListItem):
        return ListItem(blocks=[block_from_dict(asdict(block)) for block in value.blocks])
    if isinstance(value, dict):
        return ListItem(blocks=[block_from_dict(block) for block in list(value.get("blocks") or [])])
    from .parser import parse_item_content  # the parser builds on this module

    return ListItem(blocks=parse_item_content(str(value if value is not None else "")))


def block_from_dict(data: dict[str, Any] | None) -> Block:
    """Construct a Block from a dict, applying defaults for missing fields."""
    payload = data or {}
    kind = str(payload.get("kind") or "paragraph")
    defaults = BLOCK_DEFAULTS.get(kind, BLOCK_DEFAULTS["paragraph"])
    merged = {**defaults, **payload}
    target, label, title = normalise_field(merged.get("target")), normalise_field(merged.get("label")), ""
    if kind == "link_definition":
        label, target, title = normalise_link_definition(label, target, merged.get("title"))
    headers = [_cell(header) for header in list(merged.get("headers") or [])]
    if kind == "table" and "headers" not in payload:
        # The default headers widen to the widest row, so no cell is cut.
        width = max((len(row) for row in merged.get("rows") or [] if isinstance(row, (list, tuple))), default=0)
        headers += [f"Column {index + 1}" for index in range(len(headers), width)]
    return Block(
        kind=kind,
        text=normalise_text(kind, merged.get("text")),
        definition_id=normalise_field(merged.get("definition_id")),
        term=normalise_field(merged.get("term")),
        prefix=str(merged.get("prefix") or ""),
        suffix=str(merged.get("suffix") or ""),
        target=target,
        label=label,
        title=title,
        items=(
            [_list_item(item) for item in list(merged.get("items") or [])] or [ListItem()]
            if kind in LIST_KINDS else []
        ),
        headers=headers,
        rows=_table_rows(list(merged.get("rows") or []), len(headers)),
        align=_table_align(list(merged.get("align") or []), len(headers)),
        level=heading_level(merged.get("level")) if kind == "heading" else 0,
    )


def heading_text(text: str) -> str:
    """A heading block's *text* as one line, its line endings spaces."""
    return strip_text(LINE_ENDING_RE.sub(" ", text))


def heading_level(value: Any) -> int:
    """A heading block's level, 1 to 6; 1 when *value* is none."""
    try:
        level = int(value)
    except (TypeError, ValueError, OverflowError):
        return 1
    return min(max(level, 1), 6)


def section_from_dict(data: dict[str, Any] | None) -> Section:
    """Construct a Section from a dict."""
    payload = data or {}
    return Section(
        title=normalise_title(payload.get("title")),
        level=max(1, min(6, int(payload.get("level") or 1))),
        identifier=normalise_field(payload.get("identifier")),
        blocks=[block_from_dict(b) for b in list(payload.get("blocks") or [])],
        condition=normalise_field(payload.get("condition")),
    )


#: The keys of a party object that are its fields (§3.4, §3.5). Any other
#: key is a custom field (§3.4), and ``custom_fields`` is the list a model's
#: dict holds them in (``document_to_dict``).
PARTY_KEYS = frozenset({
    "name", "label", "type", "legal_name", "identification_number", "address",
    "date_of_birth", "representatives", "custom_fields",
})


def party_from_dict(data: dict[str, Any] | None) -> Party:
    """Construct a Party from a frontmatter dict (§3.4).

    Values are taken verbatim: an unknown ``type`` or a non-identifier ``name``
    is preserved, and a missing ``type`` stays empty even though §3.4 requires
    it, so the validator reports party-type-invalid or side-party-name-format
    (§16.6) instead of the model silently repairing the document. A key that
    is none of its fields (``PARTY_KEYS``) is a custom field (§3.4), when its
    value is one (not a list or a mapping) — an empty key too, a row whose
    label is not yet written; so is each entry of a ``custom_fields`` list,
    the form a model's dict holds them in.
    """
    payload = data or {}
    custom = [
        CustomField(label=normalise_field(cf.get("label")), value=normalise_field(cf.get("value")))
        for cf in list(payload.get("custom_fields") or [])
        if isinstance(cf, dict)
    ]
    for key, value in payload.items():
        label = "" if key is None else str(key).strip()
        if label not in PARTY_KEYS and not isinstance(value, (dict, list)):
            custom.append(CustomField(label=label, value="" if value is None else str(value).strip()))

    return Party(
        name=normalise_field(payload.get("name")),
        label=normalise_field(payload.get("label")),
        type=normalise_field(payload.get("type")),
        legal_name=normalise_field(payload.get("legal_name")),
        identification_number=normalise_field(payload.get("identification_number")),
        address=normalise_field(payload.get("address")),
        date_of_birth=normalise_field(payload.get("date_of_birth")),
        representatives=[
            Representative(name=normalise_field(r.get("name")), title=normalise_field(r.get("title")))
            for r in list(payload.get("representatives") or [])
            if isinstance(r, dict)
        ],
        custom_fields=custom,
    )


def side_from_dict(data: dict[str, Any] | None) -> Side:
    """Construct a Side and its parties from a frontmatter dict (§3.3).

    A non-identifier side name is preserved verbatim so the validator can
    report side-party-name-format (§16.6) instead of a silent repair.
    """
    payload = data or {}

    return Side(
        name=normalise_field(payload.get("name")),
        label=normalise_field(payload.get("label")),
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

    # Sides (§3.3). Absent or malformed, the validator reports sides-absent.
    raw_sides = payload.get("sides")
    sides: list[Side] = (
        [side_from_dict(s) for s in raw_sides if isinstance(s, dict)]
        if isinstance(raw_sides, list)
        else []
    )

    # Amends
    raw_amends = payload.get("amends")
    amends = (
        Amends(title=normalise_field(raw_amends.get("title")), file=normalise_field(raw_amends.get("file")))
        if isinstance(raw_amends, dict)
        else None
    )
    raw_supersedes = payload.get("supersedes")
    supersedes: str | Amends = (
        Amends(title=normalise_field(raw_supersedes.get("title")), file=normalise_field(raw_supersedes.get("file")))
        if isinstance(raw_supersedes, dict)
        else normalise_field(raw_supersedes)
    )

    # Attachments
    attachments = [
        Attachment(
            id=normalise_field(a.get("id")),
            title=normalise_field(a.get("title")),
            file=normalise_field(a.get("file")),
            when=normalise_field(a.get("when")),
        )
        for a in (payload.get("attachments") or [])
        if isinstance(a, dict)
    ]

    return Metadata(
        # No default title: a missing title is a validation error (§16.6,
        # title-missing); app flows that create fresh documents supply one.
        title=normalise_field(payload.get("title")),
        legaldown=normalise_field(payload.get("legaldown")),
        subtitle=normalise_field(payload.get("subtitle")),
        version=normalise_field(payload.get("version")),
        document_type=normalise_field(payload.get("document_type"), "contract"),
        effective_date=normalise_field(payload.get("effective_date")),
        field_types=_parse_str_dict(payload.get("field_types")),
        governing_law=normalise_field(payload.get("governing_law")),
        language=normalise_field(payload.get("language")),
        include_signatures=_to_bool(payload.get("include_signatures"), default=True),
        tags=_clean_list(list(tags)),
        sides=sides,
        translations=_parse_str_dict(payload.get("translations"), skip_blank=True),
        authoritative=normalise_field(payload.get("authoritative")),
        adopted_by=normalise_field(payload.get("adopted_by")),
        adoption_date=normalise_field(payload.get("adoption_date")),
        supersedes=supersedes,
        amends=amends,
        attachments=attachments,
        questions=payload.get("questions"),
    )


def document_from_dict(data: dict[str, Any] | None) -> Document:
    """Construct a full Document from a nested dict."""
    payload = data or {}
    return Document(
        metadata=metadata_from_dict(payload.get("metadata") or {}),
        sections=[section_from_dict(s) for s in list(payload.get("sections") or [])],
        filename=normalise_field(payload.get("filename")),
        preamble=[block_from_dict(b) for b in list(payload.get("preamble") or [])],
    )


def frontmatter_message(error: BaseException) -> str:
    """The text of the frontmatter-invalid-yaml diagnostic for *error*, one for
    validation and assembly alike (#43): what is wrong, on one line. Where it
    is wrong is the diagnostic's own ``line``, so the message holds no position
    of its own and none of the snippet the YAML reader draws."""
    return f"The frontmatter cannot be read: {error}"


def _check_frontmatter_read(document: Document) -> None:
    """Raise a ``FrontmatterError`` (a new one, with the message and line of
    the kept ``Document.frontmatter_error``, which is its cause) for a document
    whose frontmatter could not be read (``parse(on_frontmatter_error="keep")``,
    #43), to stop whatever would write it out from its empty metadata: the
    text of the block would be lost. The guard of ``document_to_dict``,
    ``serialize`` and ``save``. Raising the stored exception itself would grow
    its traceback at each raise and tie it to the frames that raised it."""
    error = document.frontmatter_error
    if error is not None:
        from .parser import FrontmatterError  # the parser builds on this module

        raise FrontmatterError(str(error), getattr(error, "line", None)) from error


def document_to_dict(document: Document) -> dict[str, Any]:
    """Convert a Document to a plain dict (for JSON serialization etc.),
    without its source map. Raises ``FrontmatterError`` for a document whose
    frontmatter could not be read (``Document.to_dict``): see
    ``_check_frontmatter_read``."""
    _check_frontmatter_read(document)
    return {
        "metadata": asdict(document.metadata),
        "sections": [asdict(section) for section in document.sections],
        "filename": document.filename,
        "preamble": [asdict(block) for block in document.preamble],
    }


def empty_document() -> Document:
    """A new document with starter content to edit: a provider and a client
    side, a definitions section and a scope section. Not a blank document:
    ``Document()`` is one, with nothing in it but default metadata."""
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
                    )],
                ),
                Side(
                    name="clients", label="Clients",
                    parties=[Party(
                        name="client-llc", label="Client",
                        type="legal_entity", legal_name="Client LLC",
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
