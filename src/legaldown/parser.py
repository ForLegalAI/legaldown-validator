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

from .definitions import DefinitionAnchor, find_definition_anchors, text_fragments
from .directives import Directive, iter_directives, scan_directives
from .models import Block, Document, document_from_dict
from .validator import slugify_identifier

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
    # Directives are lifted into block fields only when the serializer writes
    # back an equivalent directive — the same values, with spacing and quoting
    # normalized. Anything else stays paragraph text so the validator sees the
    # source: emphasis-wrapped or single-quoted terms
    # (def-emphasis / def-single-quote-ambiguous) and a {{def:}} with
    # parameters or malformed arguments. The definition is still collected
    # from the paragraph by collect_definitions.
    scanned = list(scan_directives(stripped))
    anchors = find_definition_anchors(stripped, scanned=scanned)
    if anchors and _is_liftable_definition(anchors[0], stripped):
        anchor = anchors[0]
        return Block(
            kind="definition",
            definition_id=anchor.directive.positional or "",
            term=anchor.term,
            text=stripped[anchor.directive.end:].strip(),
        )
    # A directive inside a defined term's quoted span cannot be split out
    # without separating the {{def:}} from its opening quotation mark.
    anchored = [(a.start, a.directive.start) for a in anchors if a.term is not None]
    directives = [
        d
        for d, _scan in scanned
        if not any(start <= d.start < end for start, end in anchored)
    ]
    ref_directive = _first_liftable(directives, "ref")
    if ref_directive is not None:
        return Block(
            kind="ref",
            prefix=stripped[:ref_directive.start],
            target=ref_directive.positional,
            suffix=stripped[ref_directive.end:],
        )
    term_directive = _first_liftable(directives, "term")
    if term_directive is not None:
        return Block(
            kind="term",
            prefix=stripped[:term_directive.start],
            target=term_directive.positional,
            label=term_directive.params.get("label", ""),
            suffix=stripped[term_directive.end:],
        )
    return Block(kind="paragraph", text=stripped)


def _is_liftable_definition(anchor: DefinitionAnchor, paragraph: str) -> bool:
    """True if the definition block fields hold *anchor* without loss: it
    leads the paragraph, with a plain quoted term and a bare or omitted id.

    The serializer writes a non-empty term in straight double quotes, so only
    a paragraph that begins exactly that way keeps its term and delimiters.
    """
    directive = anchor.directive
    return (
        bool(anchor.term)
        and anchor.start == 0
        and paragraph.startswith(f'"{anchor.term}"')
        and not anchor.emphasis
        and not directive.malformed
        and not directive.params
        and directive.positional != ""
    )


def _first_liftable(directives: list[Directive], name: str) -> Directive | None:
    """The first *name* directive the ref/term block fields hold without loss:
    well-formed, with a target and only the parameters it defines."""
    for directive in directives:
        if (
            directive.name == name
            and not directive.malformed
            and directive.positional
            and not directive.duplicates
            and not directive.unknown_params()
            # Block fields hold "" for an absent parameter, so an explicitly
            # empty one (label=) would be dropped on serialization.
            and all(directive.params.values())
        ):
            return directive
    return None


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


def _block_dicts(lines: list[str]) -> list[dict[str, Any]]:
    """Parse body lines into block dicts for ``document_from_dict``."""
    return [asdict(block) for block in _parse_blocks("\n".join(lines).strip())]


# ── Public API ────────────────────────────────────────────────────

def parse_document(source: str, *, filename: str = "") -> Document:
    """Parse a LegalDown source string into a Document object.

    The parser is deliberately faithful to the source: nothing is rewritten to
    make a document valid, so the validator reports what the document actually
    says (a bare ``unit=M`` surfaces as duration-invalid-unit rather than being
    silently corrected).
    """
    metadata, body = _split_frontmatter(source or "")
    payload: dict[str, Any] = {
        "metadata": metadata,
        "sections": [],
        "filename": filename,
    }
    lines = body.splitlines()
    current: dict[str, Any] | None = None
    # Lines before the first heading are the preamble (§4.4).
    preamble_lines: list[str] = []
    current_lines = preamble_lines

    for raw_line in lines:
        match = HEADING_RE.match(raw_line)
        if match:
            hashes, title, identifier = match.groups()
            level = len(hashes)
            if title.strip() == "Signature Block" and identifier == "signature-block":
                break
            if current is not None:
                current["blocks"] = _block_dicts(current_lines)
                payload["sections"].append(current)
            current = {
                "title": title.strip(),
                "level": level,
                "identifier": identifier or slugify_identifier(title.strip()),
            }
            current_lines = []
        else:
            current_lines.append(raw_line)

    if current is not None:
        current["blocks"] = _block_dicts(current_lines)
        payload["sections"].append(current)
    payload["preamble"] = _block_dicts(preamble_lines)
    return document_from_dict(payload)


def collect_source_directives(document: Document) -> tuple[set[str], set[str]]:
    """Collect all ref and term targets used in a document.

    Returns ``(ref_targets, term_targets)``.
    """
    refs: set[str] = set()
    terms: set[str] = set()
    for _section, _index, block in document.iter_blocks():
        if block.kind == "ref" and block.target:
            refs.add(block.target)
        if block.kind == "term" and block.target:
            terms.add(block.target)
        for fragment in text_fragments(block):
            for directive in iter_directives(fragment):
                if directive.malformed or not directive.positional:
                    continue
                if directive.name == "ref":
                    refs.add(directive.positional)
                elif directive.name == "term":
                    terms.add(directive.positional)
    return refs, terms
