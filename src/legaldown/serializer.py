"""LegalDown serializer — converts a Document object into .legal.md source text.

Produces a complete LegalDown file with YAML frontmatter and markdown body.
The output is deterministic for a given Document input.
"""
from __future__ import annotations

from typing import Any

import yaml

from .directives import format_value
from .markdown import FENCE_OPEN_RE, close_fences, html_block_end
from .markers import Marker, format_marker
from .models import Amends, Block, Document, Metadata, metadata_from_dict
from .parser import HEADING_RE, RULE_RE

# ── Internal helpers ──────────────────────────────────────────────

def _metadata_to_frontmatter(metadata: Metadata) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if metadata.legaldown:
        payload["legaldown"] = metadata.legaldown
    payload["title"] = metadata.title
    if metadata.subtitle:
        payload["subtitle"] = metadata.subtitle
    if metadata.version:
        payload["version"] = metadata.version
    if metadata.document_type and metadata.document_type != "contract":
        payload["document_type"] = metadata.document_type
    if metadata.effective_date:
        payload["effective_date"] = metadata.effective_date
    if metadata.field_types:
        payload["field_types"] = metadata.field_types
    if metadata.amends:
        amends_obj: dict[str, Any] = {"title": metadata.amends.title}
        if metadata.amends.file:
            amends_obj["file"] = metadata.amends.file
        payload["amends"] = amends_obj

    if metadata.sides:
        sides_list: list[dict[str, Any]] = []
        for side in metadata.sides:
            if not side.parties:
                continue
            side_obj: dict[str, Any] = {"name": side.name}
            if side.label:
                side_obj["label"] = side.label

            party_dicts: list[dict[str, Any]] = []
            for party in side.parties:
                if not party.name and not party.legal_name:
                    continue
                party_dict: dict[str, Any] = {"name": party.name}
                if party.label:
                    party_dict["label"] = party.label
                if party.type:
                    party_dict["type"] = party.type
                if party.legal_name:
                    party_dict["legal_name"] = party.legal_name
                if party.identification_number:
                    party_dict["identification_number"] = party.identification_number
                if party.address:
                    party_dict["address"] = party.address
                if party.type == "natural_person" and party.date_of_birth:
                    party_dict["date_of_birth"] = party.date_of_birth
                if party.representatives:
                    reps = [
                        {
                            k: v
                            for k, v in [
                                ("name", r.name),
                                ("title", r.title),
                            ]
                            if v
                        }
                        for r in party.representatives
                        if r.name or r.title
                    ]
                    if reps:
                        party_dict["representatives"] = reps
                if party.custom_fields:
                    for cf in party.custom_fields:
                        if cf.label and cf.value:
                            party_dict[cf.label] = cf.value
                party_dicts.append(party_dict)

            if party_dicts:
                side_obj["parties"] = party_dicts
                sides_list.append(side_obj)

        if sides_list:
            payload["sides"] = sides_list

    if metadata.governing_law:
        payload["governing_law"] = metadata.governing_law
    payload["language"] = metadata.language
    if metadata.translations:
        payload["translations"] = metadata.translations
    if metadata.authoritative:
        payload["authoritative"] = metadata.authoritative
    if metadata.adopted_by:
        payload["adopted_by"] = metadata.adopted_by
    if metadata.adoption_date:
        payload["adoption_date"] = metadata.adoption_date
    if isinstance(metadata.supersedes, Amends):
        supersedes_obj: dict[str, Any] = {"title": metadata.supersedes.title}
        if metadata.supersedes.file:
            supersedes_obj["file"] = metadata.supersedes.file
        payload["supersedes"] = supersedes_obj
    elif metadata.supersedes:
        payload["supersedes"] = metadata.supersedes
    if metadata.attachments:
        payload["attachments"] = [
            {"id": att.id, "title": att.title, "file": att.file}
            | ({"when": att.when} if att.when else {})
            for att in metadata.attachments
            if att.id and att.title and att.file
        ]
    if metadata.questions is not None:
        payload["questions"] = metadata.questions
    if metadata.tags:
        payload["tags"] = metadata.tags
    return payload


def _list_item(marker: str, item: str) -> str:
    """A list item: its later lines (a fenced code block in the item, closed
    if left open) are indented to the item's content; blank lines stay
    blank."""
    first, *rest = (marker + close_fences(item)).split("\n")
    return "\n".join([first, *(" " * len(marker) + line if line else line for line in rest)])


def _paragraph(text: str) -> str:
    """A paragraph's text, indented four columns if at the margin it would
    open a code block, a heading, or an HTML block: the parser only reads
    such text as a paragraph when it was indented like that in the source."""
    if FENCE_OPEN_RE.match(text) or HEADING_RE.match(text) or html_block_end([text], 0) is not None:
        return "    " + text
    return text


_DELIMITERS = {"left": ":---", "right": "---:", "center": ":---:"}


def _table_cell(text: str) -> str:
    """A table cell's text with a backslash before each pipe, so that it
    does not split the row (GFM): the parser removes exactly that one."""
    return " ".join(text.split("\n")).strip().replace("|", "\\|")


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(_table_cell(cell) for cell in cells) + " |"


def _render_block(block: Block) -> str:
    if block.kind == "paragraph":
        return _paragraph(block.text.strip())
    if block.kind == "definition":
        term = block.term.strip() or block.definition_id.replace("-", " ").title()
        did = block.definition_id.strip()
        anchor = (
            f'"{term}" {{{{def: {format_value(did, positional=True)}}}}}'
            if did
            else f'"{term}" {{{{def:}}}}'
        )
        body = block.text.strip()
        return f"{anchor} {body}" if body else anchor
    if block.kind == "ref":
        target = format_value(block.target, positional=True)
        return _paragraph(f"{block.prefix}{{{{ref: {target}}}}}{block.suffix}".strip())
    if block.kind == "term":
        target = format_value(block.target, positional=True)
        label_part = f", label={format_value(block.label)}" if block.label else ""
        return _paragraph(f"{block.prefix}{{{{term: {target}{label_part}}}}}{block.suffix}".strip())
    if block.kind == "unordered_list":
        items = [item for item in block.items if item.strip()]
        # An item whose text begins with dashes, such as "--", would make a
        # "- " line a thematic break; "+" never forms one.
        bullet = "+ " if any(RULE_RE.match("- " + item.split("\n")[0]) for item in items) else "- "
        return "\n".join(_list_item(bullet, item) for item in items)
    if block.kind == "ordered_list":
        return "\n".join(
            _list_item(f"{index}. ", item)
            for index, item in enumerate(
                (item for item in block.items if item.strip()), start=1
            )
        )
    if block.kind == "quote":
        lines = block.text.splitlines() or [""]
        return "\n".join(f"> {line}".rstrip() for line in lines)
    if block.kind == "table":
        # GFM needs a header row; a table built without one gets empty
        # header cells, as wide as its widest row.
        width = len(block.headers) or max((len(row) for row in block.rows), default=0) or 1
        headers = block.headers + [""] * (width - len(block.headers))
        align = block.align[:width] + [""] * (width - len(block.align))
        rows = [row[:width] + [""] * (width - len(row)) for row in block.rows]
        return "\n".join(
            [
                _table_row(headers),
                _table_row([_DELIMITERS.get(column, "---") for column in align]),
                *(_table_row(row) for row in rows),
            ]
        )
    if block.kind == "rule":
        return "---"
    if block.kind == "code":
        return close_fences(block.text)
    if block.kind == "html":
        return block.text
    return block.text.strip()


def _render_blocks(blocks: list[Block]) -> list[str]:
    """Rendered blocks, each preceded by a blank separator line."""
    parts: list[str] = []
    for block in blocks:
        rendered = _render_block(block)
        if rendered:
            parts.extend(["", rendered])
    return parts


class _BlockDumper(yaml.SafeDumper):
    """Writes every value in full, never as an ``&anchor``/``*alias``: a
    question declaration shared in the source gets lines of its own, so
    assembly can edit each one (§15.2)."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


# ── Public API ────────────────────────────────────────────────────

def serialize_document(document: Document) -> str:
    """Serialize a Document object to LegalDown (.legal.md) source text.

    A document parsed without frontmatter (§3.1) is written without it, as
    long as its metadata is still empty; a thematic break opening it is then
    written ``***``, which cannot open frontmatter."""
    payload = _metadata_to_frontmatter(document.metadata)
    bare = document.metadata.frontmatter_absent and payload == _metadata_to_frontmatter(metadata_from_dict({}))
    parts: list[str] = []
    if not bare:
        frontmatter = yaml.dump(payload, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True).strip()
        parts = ["---", frontmatter, "---"]
    preamble = _render_blocks(document.preamble)
    if bare and preamble[1:2] == ["---"]:
        preamble[1] = "***"
    parts.extend(preamble)
    for section in document.sections:
        heading = f"{'#' * section.level} {section.title.strip()}"
        marker = format_marker(Marker(section.identifier.strip(), section.condition.strip()))
        if marker:
            heading += " " + marker
        parts.extend(["", heading])
        parts.extend(_render_blocks(section.blocks))
    return "\n".join(parts).strip() + "\n"




def render_block(block: Block) -> str:
    """Render a single block to LegalDown source.

    Public alias for the block renderer: applications that render one
    block at a time (editors, previews) need it, and a private name is
    not a contract this package can keep stable.
    """
    return _render_block(block)
