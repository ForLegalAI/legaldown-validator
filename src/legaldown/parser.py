"""LegalDown parser — converts .legal.md source text into a Document object.

The parser handles:
- YAML frontmatter extraction
- Heading-based section splitting with optional ``{#identifier}`` syntax
- Block-level parsing: paragraphs, definitions, lists, tables, quotes, rules
- Inline directive detection (ref/term blocks)
"""
from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

import yaml

from .definitions import DEF_ANCHOR_RE, EMPHASIS_DEF_RE, extract_def, is_single_quoted
from .models import Block, Document, document_from_dict
from .validator import REF_RE, TERM_RE, slugify_identifier

# ── YAML loader ───────────────────────────────────────────────────
# PyYAML's implicit timestamp resolution constructs datetime objects — and
# crashes outright on out-of-range dates like 2026-13-45. LegalDown metadata
# dates are strings validated by the validator (metadata-date-invalid), so
# load them as plain scalars.


class _StrDateSafeLoader(yaml.SafeLoader):
    pass


_StrDateSafeLoader.add_constructor(
    "tag:yaml.org,2002:timestamp",
    _StrDateSafeLoader.construct_yaml_str,
)

# ── Parser regex patterns ─────────────────────────────────────────

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# The anchor group deliberately accepts any non-brace run: a malformed id
# (e.g. {#Bad_ID}) must reach the validator to be reported as anchor-format
# rather than silently remaining part of the title.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+\{#([^}\s]+)})?\s*$")
REF_BLOCK_RE = re.compile(
    r"^(.*?)\{\{ref:\s*([^,}]+)(?:,\s*format=([^}]+))?}}(.*)$", re.DOTALL
)
TERM_BLOCK_RE = re.compile(
    r"^(.*?)\{\{term:\s*([^,}]+)(?:,\s*label=([^}]+))?}}(.*)$", re.DOTALL
)


# ── Internal helpers ──────────────────────────────────────────────

def _split_frontmatter(source: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(source)
    if not match:
        return {}, source
    metadata = yaml.load(match.group(1), Loader=_StrDateSafeLoader) or {}
    body = source[match.end():]
    return metadata, body


def _parse_list(lines: list[str], *, ordered: bool) -> Block:
    """Parse a sequence of list-item lines (including continuation lines)."""
    marker = re.compile(r"^\s*(?:\d+\.|-)\s+(.*)$")
    items: list[str] = []
    for line in lines:
        m = marker.match(line)
        if m:
            items.append(m.group(1).strip())
        elif items:
            items[-1] = items[-1] + " " + line.strip()
    return Block(kind="ordered_list" if ordered else "unordered_list", items=items)


def _parse_table(lines: list[str]) -> Block:
    rows = [line.strip().strip("|") for line in lines]
    headers = [cell.strip() for cell in rows[0].split("|")]
    data_rows: list[list[str]] = []
    for row in rows[2:]:
        data_rows.append([cell.strip() for cell in row.split("|")])
    return Block(kind="table", headers=headers, rows=data_rows)


def _parse_paragraph(paragraph: str) -> Block:
    stripped = paragraph.strip()
    # Definition: a paragraph whose leading token is a quoted term followed by a
    # ``{{def: id}}`` anchor. The id may be omitted (derived at validation time).
    # Non-canonical source forms — emphasis-wrapped or single-quoted terms —
    # stay paragraphs so the validator can see the raw text and report
    # def-emphasis / def-single-quote-ambiguous (the definition itself is still
    # collected from the paragraph by collect_definitions).
    def_match = DEF_ANCHOR_RE.match(stripped)
    if def_match and (
        EMPHASIS_DEF_RE.match(stripped) or is_single_quoted(def_match)
    ):
        def_match = None
    if def_match:
        term, raw_id = extract_def(def_match)
        return Block(
            kind="definition",
            definition_id=raw_id,
            term=term,
            text=stripped[def_match.end():].strip(),
        )
    ref_match = REF_BLOCK_RE.match(paragraph.strip())
    if ref_match and ref_match.group(2).strip():
        prefix, target, fmt, suffix = ref_match.groups()
        return Block(
            kind="ref",
            prefix=prefix,
            target=target.strip(),
            format=(fmt or "").strip(),
            suffix=suffix,
        )
    term_match = TERM_BLOCK_RE.match(paragraph.strip())
    if term_match and term_match.group(2).strip():
        prefix, target, label, suffix = term_match.groups()
        return Block(
            kind="term",
            prefix=prefix,
            target=target.strip(),
            label=(label or "").strip(),
            suffix=suffix,
        )
    return Block(kind="paragraph", text=paragraph.strip())


def _parse_blocks(chunk: str) -> list[Block]:
    lines = chunk.splitlines()
    blocks: list[Block] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line.strip() == "---":
            blocks.append(Block(kind="rule"))
            index += 1
            continue
        if line.lstrip().startswith(">"):
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote_lines.append(lines[index].lstrip()[1:].lstrip())
                index += 1
            blocks.append(Block(kind="quote", text="\n".join(quote_lines).strip()))
            continue
        if line.lstrip().startswith("|"):
            table_lines: list[str] = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                table_lines.append(lines[index])
                index += 1
            if len(table_lines) >= 2:
                blocks.append(_parse_table(table_lines))
            continue
        if re.match(r"^\s*-\s+", line):
            list_lines: list[str] = []
            while index < len(lines) and lines[index].strip():
                cur = lines[index]
                if (
                    not re.match(r"^\s*-\s+", cur)
                    and not cur.startswith("  ")
                    and not cur.startswith("\t")
                ):
                    break
                list_lines.append(cur)
                index += 1
            blocks.append(_parse_list(list_lines, ordered=False))
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            list_lines = []
            while index < len(lines) and lines[index].strip():
                cur = lines[index]
                if (
                    not re.match(r"^\s*\d+\.\s+", cur)
                    and not cur.startswith("  ")
                    and not cur.startswith("\t")
                ):
                    break
                list_lines.append(cur)
                index += 1
            blocks.append(_parse_list(list_lines, ordered=True))
            continue
        paragraph_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            paragraph_lines.append(lines[index])
            index += 1
        blocks.append(_parse_paragraph(" ".join(paragraph_lines)))
    return blocks


# ── Public API ────────────────────────────────────────────────────

def parse_document(source: str, *, filename: str = "") -> Document:
    """Parse a LegalDown source string into a Document object.

    The parser is deliberately faithful to the source: pre-0.1 spellings are
    **not** rewritten here, so the validator reports what the document actually
    says (a bare ``unit=M`` must surface as duration-invalid-unit). Legacy
    content is upgraded at the storage boundary instead — see
    ``app.services.documents.parse_document_row``.
    """
    metadata, body = _split_frontmatter(source or "")
    payload: dict[str, Any] = {
        "metadata": metadata,
        "sections": [],
        "filename": filename,
    }
    lines = body.splitlines()
    current: dict[str, Any] | None = None
    current_lines: list[str] = []

    for raw_line in lines:
        match = HEADING_RE.match(raw_line)
        if match:
            hashes, title, identifier = match.groups()
            level = len(hashes)
            if title.strip() == "Signature Block" and identifier == "signature-block":
                break
            if current is not None:
                current["blocks"] = [
                    asdict(block)
                    for block in _parse_blocks("\n".join(current_lines).strip())
                ]
                payload["sections"].append(current)
            current = {
                "title": title.strip(),
                "level": level,
                "identifier": identifier or slugify_identifier(title.strip()),
            }
            current_lines = []
        elif current is not None:
            current_lines.append(raw_line)

    if current is not None:
        current["blocks"] = [
            asdict(block) for block in _parse_blocks("\n".join(current_lines).strip())
        ]
        payload["sections"].append(current)

    if not payload.get("sections"):
        payload["sections"] = []
    document = document_from_dict(payload)
    if not document.sections:
        document.sections = []
    return document


def collect_source_directives(document: Document) -> tuple[set[str], set[str]]:
    """Collect all ref and term targets used in a document.

    Returns ``(ref_targets, term_targets)``.
    """
    refs: set[str] = set()
    terms: set[str] = set()
    for section in document.sections:
        for block in section.blocks:
            if block.kind == "ref" and block.target:
                refs.add(block.target)
            if block.kind == "term" and block.target:
                terms.add(block.target)
            text_fragments: list[str] = []
            if block.text:
                text_fragments.append(block.text)
            if block.prefix:
                text_fragments.append(block.prefix)
            if block.suffix:
                text_fragments.append(block.suffix)
            text_fragments.extend(item for item in block.items if item)
            text_fragments.extend(cell for row in block.rows for cell in row if cell)
            for fragment in text_fragments:
                refs.update(
                    match.group(1).strip() for match in REF_RE.finditer(fragment)
                )
                terms.update(
                    match.group(1).strip() for match in TERM_RE.finditer(fragment)
                )
    return refs, terms


