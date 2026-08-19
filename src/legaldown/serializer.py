"""LegalDown serializer — converts a Document object into .legal.md source text.

Produces a complete LegalDown file with YAML frontmatter and markdown body.
The output is deterministic for a given Document input.
"""
from __future__ import annotations

from typing import Any

import yaml

from .models import Block, Document, Metadata

# ── Internal helpers ──────────────────────────────────────────────

def _metadata_to_frontmatter(metadata: Metadata) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": metadata.title,
    }
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
    if metadata.supersedes:
        payload["supersedes"] = metadata.supersedes
    if metadata.attachments:
        payload["attachments"] = [
            {"id": att.id, "title": att.title, "file": att.file}
            for att in metadata.attachments
            if att.id and att.title and att.file
        ]
    if metadata.tags:
        payload["tags"] = metadata.tags
    return payload


def _render_block(block: Block) -> str:
    if block.kind == "paragraph":
        return block.text.strip()
    if block.kind == "definition":
        term = block.term.strip() or block.definition_id.replace("-", " ").title()
        did = block.definition_id.strip()
        anchor = f'"{term}" {{{{def: {did}}}}}' if did else f'"{term}" {{{{def:}}}}'
        body = block.text.strip()
        return f"{anchor} {body}" if body else anchor
    if block.kind == "ref":
        format_part = f", format={block.format}" if block.format else ""
        return f"{block.prefix}{{{{ref: {block.target}{format_part}}}}}{block.suffix}".strip()
    if block.kind == "term":
        label_part = f", label={block.label}" if block.label else ""
        return f"{block.prefix}{{{{term: {block.target}{label_part}}}}}{block.suffix}".strip()
    if block.kind == "unordered_list":
        return "\n".join(f"- {item}" for item in block.items if item.strip())
    if block.kind == "ordered_list":
        return "\n".join(
            f"{index}. {item}"
            for index, item in enumerate(
                (item for item in block.items if item.strip()), start=1
            )
        )
    if block.kind == "quote":
        lines = block.text.splitlines() or [""]
        return "\n".join(f"> {line}".rstrip() for line in lines)
    if block.kind == "table":
        headers = [
            header.strip() or f"Column {index + 1}"
            for index, header in enumerate(block.headers)
        ]
        separator = ["---"] * len(headers)
        rows = []
        for row in block.rows:
            padded = row + [""] * max(0, len(headers) - len(row))
            rows.append(
                "| "
                + " | ".join(cell.strip() for cell in padded[: len(headers)])
                + " |"
            )
        return "\n".join(
            [
                "| " + " | ".join(headers) + " |",
                "| " + " | ".join(separator) + " |",
                *rows,
            ]
        )
    if block.kind == "rule":
        return "---"
    return block.text.strip()


# ── Public API ────────────────────────────────────────────────────

def serialize_document(document: Document) -> str:
    """Serialize a Document object to LegalDown (.legal.md) source text."""
    frontmatter = yaml.safe_dump(
        _metadata_to_frontmatter(document.metadata), sort_keys=False, allow_unicode=True
    ).strip()
    parts = [
        "---",
        frontmatter,
        "---",
    ]
    for section in document.sections:
        heading = f"{'#' * section.level} {section.title.strip()}"
        if section.identifier.strip():
            heading += f" {{#{section.identifier.strip()}}}"
        parts.extend(["", heading])
        for block in section.blocks:
            rendered = _render_block(block)
            if rendered:
                parts.extend(["", rendered])
    return "\n".join(parts).strip() + "\n"




def render_block(block: Block) -> str:
    """Render a single block to LegalDown source.

    Public alias for the block renderer: applications that render one
    block at a time (editors, previews) need it, and a private name is
    not a contract this package can keep stable.
    """
    return _render_block(block)
