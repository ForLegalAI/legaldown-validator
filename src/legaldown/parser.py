"""LegalDown parser — converts .legal.md source text into a Document object.

The parser handles:
- YAML frontmatter extraction
- Heading-based section splitting with optional ``{#identifier}`` syntax
- Block-level parsing: paragraphs, definitions, lists, tables, quotes, rules
- Inline directive detection (ref/term blocks)
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

import yaml

from .definitions import DefinitionAnchor, find_definition_anchors, text_fragments
from .directives import Directive, iter_directives, lex
from .markdown import (
    FENCE_OPEN_RE,
    HTML_BLOCK_START_RE,
    closes_fence,
    dedent,
    fence_end,
    indent_width,
)
from .markers import Marker, split_heading
from .models import Block, Document, document_from_dict

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
# written, except in a question's ``default``, whose YAML type the answer
# rules rely on (§15.7.1). A value left empty or written ``null`` stays absent.
_STR_TAG = "tag:yaml.org,2002:str"
_NULL_TAG = "tag:yaml.org,2002:null"
_CONVERTED_TAGS = frozenset(
    f"tag:yaml.org,2002:{name}" for name in ("bool", "int", "float", "timestamp")
)


def _walk(node: yaml.Node, seen: set[int], loader: yaml.SafeLoader | None = None) -> Iterator[yaml.Node]:
    """*node* and every node under it not in *seen*, each once — an alias
    shares its anchor's node, so a graph with aliases is neither walked
    exponentially nor forever. With a *loader*, each mapping has its merged
    (``<<``) entries brought in before it is walked."""
    stack = [node]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, yaml.SequenceNode):
            stack.extend(reversed(current.value))
        elif isinstance(current, yaml.MappingNode):
            if loader is not None:
                loader.flatten_mapping(current)
            for key, value in reversed(current.value):
                stack.extend((value, key))


def _entries(loader: yaml.SafeLoader, node: yaml.Node) -> list[tuple[yaml.Node, yaml.Node]]:
    """The entries of mapping *node*, merged ones included."""
    if not isinstance(node, yaml.MappingNode):
        return []
    loader.flatten_mapping(node)
    return node.value


def _read_as_written(loader: yaml.SafeLoader, root: yaml.Node) -> None:
    """Retag the plain scalars of the frontmatter *root* to read as the
    strings written, leaving the nodes of question defaults typed — even a
    default shared through an alias with another field."""
    typed: set[int] = set()
    for key, questions in _entries(loader, root):
        if key.value == "questions":
            for _qid, declaration in _entries(loader, questions):
                for field_key, value in _entries(loader, declaration):
                    if field_key.value == "default":
                        typed.update(id(node) for node in _walk(value, set()))
    for node in _walk(root, typed, loader):
        if isinstance(node, yaml.MappingNode):
            for key, _value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.style is None and key.tag == _NULL_TAG:
                    key.tag = _STR_TAG
        elif isinstance(node, yaml.ScalarNode) and node.style is None and node.tag in _CONVERTED_TAGS:
            node.tag = _STR_TAG


# ── Parser regex patterns ─────────────────────────────────────────

# The YAML between the delimiters may be empty. The optional group is lazy
# so that empty frontmatter is tried first: otherwise ``---``/``---`` would
# extend to the next ``---`` rule in the body.
FRONTMATTER_RE = re.compile(r"\A---[ \t\r]*\n(?:(.*?)\n)??---[ \t\r]*(?:\n|\Z)", re.DOTALL)
# An ATX heading: its level and its text, which may end in a marker (split
# off by markers.split_heading).
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")  # the text is stripped by split_heading
# A setext underline under a paragraph: ``===`` makes a level-1 heading,
# ``---`` a level-2 one. Anywhere else, ``---`` is a thematic break.
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
RULE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*$")
# A list item marker: ``-`` for an unordered list, ``1.`` for an ordered one.
LIST_ITEM_RE = re.compile(r"^\s*(?:(?P<number>\d+)\.|-)\s+")


# ── Internal helpers ──────────────────────────────────────────────

def _split_frontmatter(source: str) -> tuple[list[str], dict[str, Any], str]:
    """Split *source* into ``(keys not line-editable, parsed frontmatter,
    body)``; the first is read from how the YAML is written, which the
    parsed data loses."""
    match = FRONTMATTER_RE.match(source)
    if not match:
        return [], {}, source
    loader = _StrDateSafeLoader(match.group(1) or "")
    try:
        node = loader.get_single_node()
        if node is None:
            return [], {}, source[match.end():]
        # Keys merged into the root count, but each entry is judged as
        # written, before merges inside it reorder its keys.
        if isinstance(node, yaml.MappingNode):
            loader.flatten_mapping(node)
        not_line_editable = _not_line_editable(node)
        _read_as_written(loader, node)
        metadata = loader.construct_document(node)
    finally:
        loader.dispose()
    if not isinstance(metadata, dict):
        raise ValueError("Frontmatter must be a YAML mapping of fields.")
    if "questions" in metadata and metadata["questions"] is None:
        metadata["questions"] = {}  # a `questions:` key left empty is still declared (§15.1)
    return not_line_editable, metadata, source[match.end():]


def _has_flow_style(node: yaml.Node) -> bool:
    """True if *node* or anything under it has entries written in YAML flow
    style. An empty ``{}`` or ``[]`` has none to edit."""
    return any(
        isinstance(inner, (yaml.MappingNode, yaml.SequenceNode))
        and inner.flow_style
        and bool(inner.value)
        for inner in _walk(node, set())
    )


def _not_line_editable(root: yaml.Node) -> list[str]:
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


def _is_lazy_line(line: str) -> bool:
    """True if *line* would continue an open paragraph rather than start a
    block of its own: a *lazy continuation line* (CommonMark), which joins
    the list item or block quote whose paragraph it continues. Any list item
    marker is taken to start a list, and a ``|`` line a table, as this
    parser has always read them."""
    return (
        bool(line.strip())
        and not FENCE_OPEN_RE.match(line)
        and not HEADING_RE.match(line)
        and not RULE_RE.match(line)
        and not LIST_ITEM_RE.match(line)
        and not HTML_BLOCK_START_RE.match(line)
        and not line.lstrip().startswith((">", "|"))
    )


def _opens_paragraph(content: str) -> bool:
    """True if a quoted line's *content* leaves paragraph text open — its
    own, or that of a list item or nested quote it starts — which a lazy
    line may continue. A heading, a thematic break, an HTML block, or an
    empty list item or quote leaves none."""
    inner = content.lstrip()
    while inner.startswith(">"):
        inner = inner[1:].lstrip()
    marker = LIST_ITEM_RE.match(inner)
    if marker:
        inner = inner[marker.end():]
    return (
        bool(inner.strip())
        and not HEADING_RE.match(inner)
        and not RULE_RE.match(inner)
        and not HTML_BLOCK_START_RE.match(inner)
    )


class _Quote:
    """A block quote read line by line (CommonMark), for what a line without
    ``>`` needs to know: whether a paragraph is open that it lazily
    continues. Quoted code, fenced or indented, is not a paragraph."""

    def __init__(self) -> None:
        self.paragraph = False
        self._fence: str | None = None

    def read(self, content: str) -> None:
        """Take one quoted line: its *content* after ``>`` and the one
        optional space."""
        if self._fence is not None:
            if closes_fence(content, self._fence):
                self._fence = None
            self.paragraph = False
        elif opening := FENCE_OPEN_RE.match(content):
            self._fence = opening.group("fence")
            self.paragraph = False
        elif self.paragraph or indent_width(content) < 4:  # else indented code
            self.paragraph = _opens_paragraph(content)

    def continues(self, line: str) -> bool:
        """True if *line*, which has no ``>``, is a lazy continuation line of
        the quote."""
        return self.paragraph and _is_lazy_line(line)


def _parse_list(lines: list[str], index: int, *, ordered: bool) -> tuple[Block, int, bool]:
    """Parse the list starting at ``lines[index]``.

    Returns ``(block, end, lazy)``: *end* is the index just past the list,
    and *lazy* is True when it ends in paragraph text, which a following
    unindented line would lazily continue (it cannot continue code).

    The list runs through its items, their continuation lines (indented two
    or more columns, or lazy continuation lines after item text), and nested
    items; an unindented item of the other kind ends it. A fenced code block
    in an item stays in that item, one line per line, indented relative to
    the item, blank lines included, until it closes or an unindented line
    ends the item (and the fence with it). A block quote in an item (a
    drafting note, §15.6) keeps its lines too, each with its ``>``: a lazy
    continuation line of the quote is kept as the quoted line it means.
    """
    items: list[str] = []
    fence: str | None = None  # the open fence inside the current item
    quote: _Quote | None = None  # the block quote the current item ends with
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
            quote = None
        elif items and (indented or (lazy and _is_lazy_line(line))):
            content = dedent(line, content_indent) if indented else line.strip()
            if quote is not None and not content.startswith(">"):
                # A lazy line (unindented) always continues the quote here:
                # the list is lazy only while the quote's paragraph is open.
                if quote.continues(content):
                    content = "> " + content
                else:
                    quote = None
            # An item holding code or a block quote keeps its lines; other
            # continuation lines join the item's text.
            if (
                FENCE_OPEN_RE.match(content)
                or content.startswith(">")
                or "\n" in items[-1]
                or items[-1].startswith(">")
            ):
                items[-1] += "\n" + content
            else:
                items[-1] += " " + content.strip()
        else:
            break
        if content.startswith(">"):
            quote = quote or _Quote()
            quote.read(content[1:].removeprefix(" "))
            lazy = quote.paragraph
        else:
            opening = FENCE_OPEN_RE.match(content)
            fence = opening.group("fence") if opening else None
            lazy = fence is None and bool(content.strip())  # an empty item has no text
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
    if any(d.name in ("placeholder", "choose") for d in lexed.directives):
        # Text around a blank or choice is checked against it (§15.7.3), so
        # the paragraph is kept whole.
        return Block(kind="paragraph", text=stripped)
    anchors = find_definition_anchors(stripped, lexed=lexed)
    if anchors and _is_liftable_definition(anchors[0], stripped, lexed.directives):
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


def _is_liftable_definition(
    anchor: DefinitionAnchor, paragraph: str, directives: list[Directive]
) -> bool:
    """True if the definition block fields hold *anchor* without loss: it
    leads the paragraph, with a plain quoted term and a bare or omitted id.
    A term holding a directive stays paragraph text, where it is checked
    (def-term-variable, §15.5); *directives* are the paragraph's.

    The serializer writes a non-empty term in straight double quotes, so only
    a paragraph that begins exactly that way keeps its term and delimiters.
    """
    directive = anchor.directive
    return (
        bool(anchor.term)
        and anchor.start == 0
        and paragraph.startswith(f'"{anchor.term}"')
        and not anchor.emphasis
        and not any(anchor.start < d.start < directive.start for d in directives)
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


_Heading = tuple[str, Marker, int]  # title, marker, level


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
            hashes, text = atx.groups()
            heading = (*split_heading(text), len(hashes))
            index += 1
        elif RULE_RE.match(line):
            blocks.append(Block(kind="rule"))
            index += 1
            lazy = False
        elif line.lstrip().startswith(">"):
            end = index
            quote = _Quote()
            while end < len(lines):
                if lines[end].lstrip().startswith(">"):
                    quote.read(lines[end].lstrip()[1:].removeprefix(" "))
                elif not quote.continues(lines[end]):
                    break
                end += 1
            # After ">", one optional space is syntax; any further
            # indentation belongs to the quoted content (CommonMark). A lazy
            # continuation line is quoted content as it stands.
            quoted = (
                source.lstrip()[1:].removeprefix(" ") if source.lstrip().startswith(">") else source.strip()
                for source in lines[index:end]
            )
            blocks.append(Block(kind="quote", text="\n".join(quoted)))
            index = end
            # The quote took every line it could continue; a paragraph after
            # it is lazy only if the quote's is still open.
            lazy = quote.paragraph
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
                heading = (*split_heading(text), setext_level)
            else:
                blocks.append(_parse_paragraph(" ".join(lines[index:end])))
            index = end
            lazy = False
        if heading is not None:
            if heading[0] == "Signature Block" and heading[1].identifier == "signature-block":
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
    not_line_editable, metadata, body = _split_frontmatter((source or "").removeprefix("\ufeff"))
    preamble, sections = _parse_body(body.splitlines())
    payload: dict[str, Any] = {
        "metadata": metadata,
        "sections": [
            {
                "title": title,
                "level": level,
                # An omitted identifier stays empty: the validator generates
                # it (§5.3, §5.5), knowing which headings can appear together.
                "identifier": marker.identifier,
                "condition": marker.condition,
                "blocks": [asdict(block) for block in blocks],
            }
            for (title, marker, level), blocks in sections
        ],
        "filename": filename,
        "preamble": [asdict(block) for block in preamble],
    }
    document = document_from_dict(payload)
    document.metadata.not_line_editable = not_line_editable
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
