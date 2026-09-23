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
from .markdown import FENCE_OPEN_RE, closes_fence, dedent, fence_end, indent_width
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

# A YAML 1.1 reader changes what was written: ``no`` becomes False, ``0.10``
# becomes 0.1, a ``null`` key becomes None. Frontmatter holds text — ids,
# names, labels, versions, conditions — so plain scalars are read as
# written, except a question's ``default``, whose YAML type the answer rules
# rely on (§15.7.1). A value left empty or written ``null`` stays absent.
_STR_TAG = "tag:yaml.org,2002:str"
_NULL_TAG = "tag:yaml.org,2002:null"
_CONVERTED_TAGS = frozenset(
    f"tag:yaml.org,2002:{name}" for name in ("bool", "int", "float", "timestamp")
)


def _read_as_written(loader: yaml.SafeLoader, node: yaml.Node, path: tuple[str, ...] = ()) -> None:
    """Retag the plain scalars under *node*, the value at *path*, to read as
    the strings written."""
    if path[:1] == ("questions",) and path[2:3] == ("default",):
        return
    if isinstance(node, yaml.ScalarNode):
        if node.style is None and node.tag in _CONVERTED_TAGS:
            node.tag = _STR_TAG
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _read_as_written(loader, item, (*path, ""))
    elif isinstance(node, yaml.MappingNode):
        loader.flatten_mapping(node)  # bring merged (<<) keys in first
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode) and key.style is None and key.tag == _NULL_TAG:
                key.tag = _STR_TAG
            _read_as_written(loader, key)
            _read_as_written(loader, value, (*path, str(key.value)))

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
RULE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*$")
# A list item marker: ``-`` for an unordered list, ``1.`` for an ordered one.
LIST_ITEM_RE = re.compile(r"^\s*(?:(?P<number>\d+)\.|-)\s+")


# ── Internal helpers ──────────────────────────────────────────────

def _split_frontmatter(source: str) -> tuple[yaml.Node | None, dict[str, Any], str]:
    """Split *source* into ``(frontmatter YAML node, parsed frontmatter,
    body)``. The node keeps how the YAML is written, which the data loses."""
    match = FRONTMATTER_RE.match(source)
    if not match:
        return None, {}, source
    loader = _StrDateSafeLoader(match.group(1) or "")
    try:
        node = loader.get_single_node()
        if node is not None:
            _read_as_written(loader, node)
        metadata = (loader.construct_document(node) if node is not None else None) or {}
    finally:
        loader.dispose()
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must be a YAML mapping of fields.")
    body = source[match.end():]
    return node, metadata, body


def _has_flow_style(node: yaml.Node) -> bool:
    """True if *node* or anything inside it has entries written in YAML flow
    style. An empty ``{}`` or ``[]`` has none to edit."""
    if isinstance(node, yaml.MappingNode):
        return bool(node.value) and (
            node.flow_style
            or any(_has_flow_style(key) or _has_flow_style(value) for key, value in node.value)
        )
    if isinstance(node, yaml.SequenceNode):
        return bool(node.value) and (
            node.flow_style or any(_has_flow_style(item) for item in node.value)
        )
    return False


def _not_line_editable(root: yaml.Node | None) -> list[str]:
    """The keys among ``questions`` and ``attachments`` that are not written
    as §15.2 requires for assembly to edit them line by line: in YAML block
    style, and each attachment entry beginning with ``id``. *root* is the
    frontmatter's YAML node. A key with no entries is not reported."""
    if not isinstance(root, yaml.MappingNode):
        return []
    keys: list[str] = []
    for key, value in root.value:
        if key.value == "questions" and _has_flow_style(value):
            keys.append("questions")
        elif (
            key.value == "attachments"
            and isinstance(value, yaml.SequenceNode)
            and (
                _has_flow_style(value)
                or any(
                    not isinstance(entry, yaml.MappingNode)
                    or not entry.value
                    or entry.value[0][0].value != "id"
                    for entry in value.value
                )
            )
        ):
            keys.append("attachments")
    return keys


def _parse_list(lines: list[str], index: int, *, ordered: bool) -> tuple[Block, int, bool]:
    """Parse the list starting at ``lines[index]``.

    Returns ``(block, end, lazy)``: *end* is the index just past the list,
    and *lazy* is True when it ends in item text, which a following
    unindented line would lazily continue (it cannot continue a code block).

    The list runs through its items, their continuation lines (indented two
    or more columns), and nested items; an unindented item of the other kind
    ends it. A fenced code block in an item stays in that item, one line per
    line, indented relative to the item, blank lines included, until it
    closes or an unindented line ends the item (and the fence with it).
    """
    items: list[str] = []
    fence: str | None = None  # the open fence inside the current item
    content_indent = 0  # columns before the current item's text
    lazy = False
    end = index
    while end < len(lines):
        line = lines[end]
        if fence is not None:
            if not line.strip() or indent_width(line) >= 2:
                code = dedent(line, content_indent)
                items[-1] += "\n" + code
                end += 1
                if closes_fence(code, fence):
                    fence = None
                lazy = False
                continue
            fence = None
        if not line.strip():
            break
        marker = LIST_ITEM_RE.match(line)
        indented = indent_width(line) >= 2
        if marker and ((marker.group("number") is not None) == ordered or indented):
            content_indent = len(line[:marker.end()].expandtabs(4))  # in columns
            content = line[marker.end():].strip()
            items.append(content)
        elif items and indented:
            content = dedent(line, content_indent)
            # An item holding code keeps its lines; other continuation lines
            # join the item's text.
            if FENCE_OPEN_RE.match(content) or "\n" in items[-1]:
                items[-1] += "\n" + content
            else:
                items[-1] += " " + content.strip()
        else:
            break
        opening = FENCE_OPEN_RE.match(content)
        fence = opening.group("fence") if opening else None
        lazy = fence is None
        end += 1
    kind = "ordered_list" if ordered else "unordered_list"
    return Block(kind=kind, items=items), end, lazy


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


def _starts_interrupting_item(line: str) -> bool:
    """True if *line* is a list item that may interrupt a paragraph
    (CommonMark): indented at most three columns, not empty, and, if
    ordered, numbered 1."""
    marker = LIST_ITEM_RE.match(line)
    return (
        marker is not None
        and indent_width(line) <= 3
        and bool(line[marker.end():].strip())
        and marker.group("number") in (None, "1")
    )


def _paragraph_end(lines: list[str], index: int, lazy: bool) -> tuple[int, int]:
    """Return ``(end, setext_level)`` for the paragraph starting at
    ``lines[index]``. *setext_level* is 1 or 2 when the paragraph is the text
    of a setext heading, whose underline is ``lines[end - 1]``, else 0.

    A paragraph ends at a blank line or at a block that can interrupt it
    (CommonMark): a fence, an ATX heading, a block quote, or a list item
    (an ordered one only when numbered 1). A *lazy* paragraph continues a
    list, block quote, or table (no blank line between), so it cannot be
    setext text: ``---`` under it is a rule.
    """
    end = index + 1
    while end < len(lines) and lines[end].strip():
        line = lines[end]
        if (
            FENCE_OPEN_RE.match(line)
            or HEADING_RE.match(line)
            or line.lstrip().startswith(">")
            or _starts_interrupting_item(line)
        ):
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
        if not line.strip():
            index += 1
            lazy = False
            continue
        heading: _Heading | None = None
        opening = FENCE_OPEN_RE.match(line)
        atx = HEADING_RE.match(line)
        marker = LIST_ITEM_RE.match(line)
        if opening:
            end = fence_end(lines, index, opening.group("fence"))
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
            # After ">", one optional space is syntax; any further
            # indentation belongs to the quoted content (CommonMark).
            quoted = (quote.lstrip()[1:].removeprefix(" ") for quote in lines[index:end])
            blocks.append(Block(kind="quote", text="\n".join(quoted).strip()))
            index = end
            lazy = True
        elif line.lstrip().startswith("|") and lines[index + 1:index + 2] and (
            lines[index + 1].lstrip().startswith("|")
        ):
            end = index
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            blocks.append(_parse_table(lines[index:end]))
            index = end
            lazy = True
        elif marker:
            block, index, lazy = _parse_list(
                lines, index, ordered=marker.group("number") is not None
            )
            blocks.append(block)
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
    frontmatter_node, metadata, body = _split_frontmatter((source or "").removeprefix("\ufeff"))
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
    document = document_from_dict(payload)
    # Set from the source, never from a frontmatter key of that name.
    document.metadata.not_line_editable = _not_line_editable(frontmatter_node)
    return document


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
