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
from .directives import Directive, iter_directives, lex
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

# The YAML between the delimiters may be empty. The optional group is lazy
# so that empty frontmatter is tried first: otherwise ``---``/``---`` would
# extend to the next ``---`` rule in the body.
FRONTMATTER_RE = re.compile(r"\A---[ \t\r]*\n(?:(.*?)\n)??---[ \t\r]*(?:\n|\Z)", re.DOTALL)
# The anchor group deliberately accepts any non-brace run: a malformed id
# (e.g. {#Bad_ID}) must reach the validator to be reported as anchor-format
# rather than silently remaining part of the title.
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+\{#([^}\s]+)})?\s*$")
# Setext heading text (§4.1), with the same optional trailing anchor.
SETEXT_TEXT_RE = re.compile(r"^(.+?)(?:\s+\{#([^}\s]+)})?\s*$")
# A setext underline: ``===`` makes a level-1 heading, ``---`` a level-2 one.
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
# A fenced code block opens with three or more backticks or tildes; a
# backtick fence's info string cannot contain a backtick (CommonMark).
FENCE_OPEN_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}(?=[^`]*$)|~{3,})")
# Lines that begin a block of their own, so they are not paragraph text.
_BLOCK_START_RE = re.compile(r"^\s*(?:>|\||-\s|\d+\.\s|#{1,6}\s)")


# ── Internal helpers ──────────────────────────────────────────────

def _split_frontmatter(source: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(source)
    if not match:
        return {}, source
    metadata = yaml.load(match.group(1) or "", Loader=_StrDateSafeLoader) or {}
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must be a YAML mapping of fields.")
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
    lexed = lex(stripped)
    anchors = find_definition_anchors(stripped, lexed=lexed)
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
        for d in lexed.directives
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


def _closes_fence(line: str, fence: str) -> bool:
    """True if *line* closes a code block opened with *fence*: the same
    character, at least as many of it, and nothing else (CommonMark)."""
    stripped = line.strip()
    return (
        len(line) - len(line.lstrip(" ")) <= 3
        and len(stripped) >= len(fence)
        and set(stripped) == {fence[0]}
    )


def _parse_blocks(chunk: str) -> list[Block]:
    lines = chunk.splitlines()
    blocks: list[Block] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        opening = FENCE_OPEN_RE.match(line)
        if opening:
            # A fenced code block is literal (§11.4) and kept whole, blank
            # lines included; unclosed, it runs to the end of the chunk.
            code_lines = [line]
            index += 1
            while index < len(lines):
                code_lines.append(lines[index])
                index += 1
                if _closes_fence(code_lines[-1], opening.group("fence")):
                    break
            blocks.append(Block(kind="code", text="\n".join(code_lines)))
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
        # A fence interrupts a paragraph (CommonMark), as in _split_sections.
        while (
            index < len(lines)
            and lines[index].strip()
            and not FENCE_OPEN_RE.match(lines[index])
        ):
            paragraph_lines.append(lines[index])
            index += 1
        blocks.append(_parse_paragraph(" ".join(paragraph_lines)))
    return blocks


def _setext_content(body: list[str]) -> int:
    """How many lines at the end of *body* form the text of a setext heading
    whose underline comes next: the trailing paragraph lines, unless they are
    the lazy continuation of a list item, block quote, or table."""
    run = 0
    while run < len(body) and body[-1 - run].strip() and not _BLOCK_START_RE.match(
        body[-1 - run]
    ):
        run += 1
    if run < len(body) and _BLOCK_START_RE.match(body[-1 - run]):
        return 0
    return run


def _split_sections(
    lines: list[str],
) -> tuple[list[str], list[tuple[str, int, str | None, list[str]]]]:
    """Split body lines at headings into the preamble (§4.4) and sections.

    Returns ``(preamble_lines, [(title, level, identifier, lines), ...])``.
    ATX (``#``) and setext (underlined) headings are both headings (§4.1);
    nothing inside a fenced code block is.
    """
    preamble: list[str] = []
    sections: list[tuple[str, int, str | None, list[str]]] = []
    body = preamble
    fence: str | None = None
    for line in lines:
        if fence is not None:
            body.append(line)
            if _closes_fence(line, fence):
                fence = None
            continue
        opening = FENCE_OPEN_RE.match(line)
        if opening:
            fence = opening.group("fence")
            body.append(line)
            continue
        atx = HEADING_RE.match(line)
        underline = SETEXT_UNDERLINE_RE.match(line)
        run = _setext_content(body) if underline else 0
        if atx:
            hashes, title, identifier = atx.groups()
            heading = (title.strip(), len(hashes), identifier)
        elif run:
            text = " ".join(content.strip() for content in body[-run:])
            del body[-run:]
            title, identifier = SETEXT_TEXT_RE.match(text).groups()
            heading = (title.strip(), 1 if underline.group(1)[0] == "=" else 2, identifier)
        else:
            body.append(line)
            continue
        if heading[0] == "Signature Block" and heading[2] == "signature-block":
            break
        body = []
        sections.append((*heading, body))
    return preamble, sections


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
    # A byte-order mark is an encoding artifact, not content.
    metadata, body = _split_frontmatter((source or "").removeprefix("\ufeff"))
    preamble_lines, sections = _split_sections(body.splitlines())
    payload: dict[str, Any] = {
        "metadata": metadata,
        "sections": [
            {
                "title": title,
                "level": level,
                "identifier": identifier or slugify_identifier(title),
                "blocks": _block_dicts(section_lines),
            }
            for title, level, identifier, section_lines in sections
        ],
        "filename": filename,
        "preamble": _block_dicts(preamble_lines),
    }
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
