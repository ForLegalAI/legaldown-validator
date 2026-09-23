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
# A setext underline under a paragraph: ``===`` makes a level-1 heading,
# ``---`` a level-2 one. Anywhere else, ``---`` is a thematic break.
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
RULE_RE = re.compile(r"^ {0,3}-{3,}[ \t]*$")
# A fenced code block opens with three or more backticks or tildes; a
# backtick fence's info string cannot contain a backtick (CommonMark).
FENCE_OPEN_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}(?=[^`]*$)|~{3,})")
UNORDERED_ITEM_RE = re.compile(r"^\s*-\s+")
ORDERED_ITEM_RE = re.compile(r"^\s*\d+\.\s+")


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


def _indent(line: str) -> int:
    """Columns of leading whitespace, a tab counting as four (CommonMark)."""
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _closes_fence(line: str, fence: str) -> bool:
    """True if *line* closes a code block opened with *fence*: at most three
    columns of indentation, then the same character at least as many times,
    and nothing else (CommonMark)."""
    stripped = line.strip()
    return (
        _indent(line) <= 3
        and len(stripped) >= len(fence)
        and set(stripped) == {fence[0]}
    )


def _fence_end(lines: list[str], index: int, fence: str) -> int:
    """Index just past the fenced code block opening at ``lines[index]``;
    an unclosed fence runs to the end of the document."""
    for end in range(index + 1, len(lines)):
        if _closes_fence(lines[end], fence):
            return end + 1
    return len(lines)


def _list_end(lines: list[str], index: int, item_re: re.Pattern[str]) -> int:
    """Index just past the list starting at ``lines[index]``: its items and
    their indented continuation lines, including a fenced code block inside
    an item, which lasts while its lines stay indented or blank."""
    end = index + 1
    while end < len(lines) and lines[end].strip():
        line = lines[end]
        if item_re.match(line):
            end += 1
            continue
        if _indent(line) < 2:
            break
        opening = FENCE_OPEN_RE.match(line.lstrip())
        end += 1
        if opening:
            while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) >= 2):
                end += 1
                if _closes_fence(lines[end - 1].lstrip(), opening.group("fence")):
                    break
    return end


def _paragraph_end(lines: list[str], index: int, lazy: bool) -> tuple[int, int]:
    """Return ``(end, setext_level)`` for the paragraph starting at
    ``lines[index]``. *setext_level* is 1 or 2 when the paragraph is the text
    of a setext heading, whose underline is ``lines[end - 1]``, else 0.

    A paragraph ends at a blank line, a fence, or an ATX heading. A *lazy*
    paragraph continues a list, block quote, or table (no blank line between),
    so it cannot be setext text (CommonMark): ``---`` under it is a rule.
    """
    end = index + 1
    while end < len(lines) and lines[end].strip():
        line = lines[end]
        if FENCE_OPEN_RE.match(line) or HEADING_RE.match(line):
            break
        underline = SETEXT_UNDERLINE_RE.match(line)
        if underline and not lazy:
            return end + 1, 1 if underline.group(1)[0] == "=" else 2
        if underline and RULE_RE.match(line):
            break
        end += 1
    return end, 0


_Heading = tuple[str, int, str | None]  # title, level, explicit identifier


def _parse_body(lines: list[str]) -> tuple[list[Block], list[tuple[_Heading, list[Block]]]]:
    """Parse body lines into the preamble's blocks (§4.4) and the sections'.

    Headings (ATX and setext, §4.1) and blocks are recognized in one pass, so
    a fenced code block is literal everywhere (§11.4): no line inside one is
    a heading or starts another block.
    """
    preamble: list[Block] = []
    sections: list[tuple[_Heading, list[Block]]] = []
    blocks = preamble
    lazy = False  # the last block was a list, quote, or table, with no blank line since
    index = 0
    while index < len(lines):
        line = lines[index]
        heading: _Heading | None = None
        opening = FENCE_OPEN_RE.match(line)
        atx = HEADING_RE.match(line)
        if not line.strip():
            index += 1
            lazy = False
            continue
        if opening:
            end = _fence_end(lines, index, opening.group("fence"))
            blocks.append(Block(kind="code", text="\n".join(lines[index:end])))
            index = end
            lazy = False
        elif atx:
            hashes, title, identifier = atx.groups()
            heading = (title.strip(), len(hashes), identifier)
            index += 1
        elif RULE_RE.match(line):
            blocks.append(Block(kind="rule"))
            index += 1
            lazy = False
        elif line.lstrip().startswith(">"):
            end = index
            while end < len(lines) and lines[end].lstrip().startswith(">"):
                end += 1
            quoted = (quote.lstrip()[1:].lstrip() for quote in lines[index:end])
            blocks.append(Block(kind="quote", text="\n".join(quoted).strip()))
            index = end
            lazy = True
        elif line.lstrip().startswith("|"):
            end = index
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            if end - index >= 2:
                blocks.append(_parse_table(lines[index:end]))
            index = end
            lazy = True
        elif UNORDERED_ITEM_RE.match(line) or ORDERED_ITEM_RE.match(line):
            ordered = not UNORDERED_ITEM_RE.match(line)
            end = _list_end(lines, index, ORDERED_ITEM_RE if ordered else UNORDERED_ITEM_RE)
            blocks.append(_parse_list(lines[index:end], ordered=ordered))
            index = end
            lazy = True
        else:
            end, setext_level = _paragraph_end(lines, index, lazy)
            if setext_level:
                text = " ".join(part.strip() for part in lines[index:end - 1])
                title, identifier = SETEXT_TEXT_RE.match(text).groups()
                heading = (title.strip(), setext_level, identifier)
            else:
                blocks.append(_parse_paragraph(" ".join(lines[index:end])))
            index = end
            lazy = False
        if heading is not None:
            if heading[0] == "Signature Block" and heading[2] == "signature-block":
                break
            blocks = []
            sections.append((heading, blocks))
            lazy = False
    return preamble, sections


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
    preamble, sections = _parse_body(body.splitlines())
    payload: dict[str, Any] = {
        "metadata": metadata,
        "sections": [
            {
                "title": title,
                "level": level,
                "identifier": identifier or slugify_identifier(title),
                "blocks": [asdict(block) for block in blocks],
            }
            for (title, level, identifier), blocks in sections
        ],
        "filename": filename,
        "preamble": [asdict(block) for block in preamble],
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
