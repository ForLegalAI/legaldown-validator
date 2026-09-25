"""LegalDown serializer — converts a Document object into .legal.md source text.

Produces a complete LegalDown file with YAML frontmatter and markdown body.
The output is deterministic for a given Document input.
"""
from __future__ import annotations

import re
from typing import Any

import yaml

from .directives import format_value
from .markdown import (
    FENCE_OPEN_RE,
    HTML_BLOCK_START_RE,
    close_fences,
    dedent,
    html_block_end,
    indent_width,
    is_indented_code,
)
from .markers import Marker, format_marker
from .models import Amends, Block, Document, Metadata, metadata_from_dict
from .parser import HEADING_RE, LIST_ITEM_RE, RULE_RE, _paragraph_end, _parse_body

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


def _opens_block(text: str) -> bool:
    """True if *text* at the margin would open a code block, a heading, or
    an HTML block rather than a paragraph."""
    return bool(FENCE_OPEN_RE.match(text) or HEADING_RE.match(text) or html_block_end([text], 0) is not None)


def _opens_tail_block(text: str) -> bool:
    """True if *text*'s first line, indented after a list, would still open
    a block: a fence or an HTML block, which CommonMark reads in the list's
    last item (``parser._tail_block``). Only the first line is judged, as
    the parser does: a fence's or an HTML block's own extent, over several
    lines, does not bear on whether it opens one."""
    first = text.split("\n", 1)[0]
    return bool(FENCE_OPEN_RE.match(first) or html_block_end([first], 0) is not None)


def _paragraph(text: str, *, indent: bool = False) -> str:
    """A paragraph's text. *indent* writes it indented four columns, which
    after a list the parser reads as paragraph text unless it opens a fence
    or an HTML block. Elsewhere, text that would open another block gets a
    backslash before it, which renders as nothing (CommonMark) — except a
    lone tag, joined from two lines, which is split back at a space: the
    parser reads it as a paragraph and joins it to the same text."""
    if indent:
        return "    " + text
    if not _opens_block(text):
        return text
    space = text.find(" ")
    lone_tag = not (FENCE_OPEN_RE.match(text) or HEADING_RE.match(text) or HTML_BLOCK_START_RE.match(text))
    if lone_tag and space > 0:
        first, second = text[:space], text[space + 1:]
        # Splitting a lone tag at a space is safe only if the two resulting
        # lines are still read back as one paragraph of two lines: not a
        # setext heading, and not interrupted (a heading, a list item, a
        # fence, …) by the second line.
        end, setext_level = _paragraph_end([first, second], 0, False)
        if end == 2 and not setext_level:
            return first + "\n" + second
    return "\\" + text


def _code(text: str, *, after_list: bool) -> str:
    """A code block, fenced or indented, as it is; other text as it is, to
    be read as what it is. Indented code directly after a list is written
    fenced: there the parser reads an indented line as paragraph text. A
    fence indented there (one the parser read in a list's last item, which
    would not read back so written as is) is written at the margin."""
    if FENCE_OPEN_RE.match(text):
        return close_fences(text)
    opening = text.split("\n", 1)[0]
    if after_list and indent_width(opening) >= 4 and FENCE_OPEN_RE.match(opening.lstrip(" \t")):
        depth = indent_width(opening)
        return close_fences("\n".join(dedent(line, depth) for line in text.split("\n")))
    if not (after_list and is_indented_code(text)):
        return text
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)  # longer than any backtick run in the code
    return "\n".join([fence, *(dedent(line, 4) for line in text.split("\n")), fence])


_DELIMITERS = {"left": ":---", "right": "---:", "center": ":---:"}


def _table_cell(text: str) -> str:
    """A table cell's text with a backslash before each pipe, so that it
    does not split the row (GFM): the parser removes exactly that one."""
    return " ".join(text.split("\n")).strip().replace("|", "\\|")


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(_table_cell(cell) for cell in cells) + " |"


def _render_block(block: Block, *, indent: bool = False) -> str:
    """*block* as source; *indent* writes a paragraph-like block indented
    four columns (``_paragraph``)."""
    if block.kind == "paragraph":
        return _paragraph(block.text.strip(), indent=indent)
    if block.kind == "definition":
        term = block.term.strip() or block.definition_id.replace("-", " ").title()
        did = block.definition_id.strip()
        anchor = (
            f'"{term}" {{{{def: {format_value(did, positional=True)}}}}}'
            if did
            else f'"{term}" {{{{def:}}}}'
        )
        body = block.text.strip()
        return _paragraph(f"{anchor} {body}" if body else anchor, indent=indent)
    if block.kind == "ref":
        target = format_value(block.target, positional=True)
        return _paragraph(f"{block.prefix}{{{{ref: {target}}}}}{block.suffix}".strip(), indent=indent)
    if block.kind == "term":
        target = format_value(block.target, positional=True)
        label_part = f", label={format_value(block.label)}" if block.label else ""
        return _paragraph(f"{block.prefix}{{{{term: {target}{label_part}}}}}{block.suffix}".strip(), indent=indent)
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
        lines = block.text.split("\n")  # the parser joins quoted lines with LF
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
        return _code(block.text, after_list=False)
    if block.kind == "html":
        return block.text
    return block.text.strip()


_LISTS = ("ordered_list", "unordered_list")
_PARAGRAPHS = ("paragraph", "definition", "ref", "term")


def _render_blocks(blocks: list[Block]) -> list[str]:
    """Rendered blocks, each preceded by a blank separator line.

    After a list the parser reads indented lines as paragraphs, whatever
    they begin with but a fence or an HTML block, for as long as each is
    indented (``parser._parse_body``). So the paragraphs directly after a
    list, up to the last one that would otherwise open another block or
    that comes before a fence or an HTML block of the list's last item, are
    written indented; the run stops at one the parser reads as another block
    even when indented (a quote, a list item, a rule, a fence, an HTML
    block). A one-line paragraph starting with ``|`` stays a paragraph: a
    table needs a second row."""
    parts: list[str] = []
    indented: set[int] = set()
    tails: set[int] = set()  # code or HTML verified to round-trip written as is, in the tail
    chain_ends: set[int] = set()  # a verified tail block after which nothing else is reached
    for index, block in enumerate(blocks):
        # Indented code here would read as one more paragraph after the list.
        after_list = index > 0 and (index - 1) not in chain_ends and (
            blocks[index - 1].kind in _LISTS or index - 1 in indented or index - 1 in tails
        )
        if block.kind in _LISTS:
            # The blocks the tail could still reach, tried as written: the
            # list itself, and — as the run goes on — each paragraph and
            # verified tail block after it. Reparsing rather than predicting
            # the parser's column arithmetic (``parser._parse_body``) makes
            # the two self-consistent by construction.
            written = [_render_block(block)]
            run = index + 1
            while run < len(blocks):
                candidate = blocks[run]
                if candidate.kind in ("code", "html") and indent_width(candidate.text) >= 4:
                    if _reparses_as_written(written, candidate.text, blocks[index:run + 1]):
                        tails.add(run)
                        indented.update(i for i in range(index + 1, run) if blocks[i].kind in _PARAGRAPHS)
                        written.append(candidate.text)
                        run += 1
                        if candidate.kind == "code" and _fence_left_open(candidate.text):
                            # An unclosed tail fence: its own last line does
                            # not close it, so it runs to its bound and
                            # swallows anything written into the same run
                            # after it. Nothing after it is reached, in this
                            # run or the next block's ``after_list``.
                            chain_ends.add(run - 1)
                            break
                        continue
                    break
                if candidate.kind not in _PARAGRAPHS:
                    break
                text = _render_block(candidate, indent=True)[4:]
                if (
                    text.startswith(">") or RULE_RE.match(text) or LIST_ITEM_RE.match(text)
                    or _opens_tail_block(text)
                ):
                    break
                if _opens_block(text):
                    indented.update(range(index + 1, run + 1))
                written.append("    " + text)
                run += 1
        if index in tails:
            rendered = block.text  # verified above: written as is, it round-trips
        elif after_list and block.kind == "code":
            rendered = _code(block.text, after_list=True)
        else:
            rendered = _render_block(block, indent=index in indented)
        if rendered:
            parts.extend(["", rendered])
    return parts


def _reparses_as_written(written: list[str], candidate: str, expected: list[Block]) -> bool:
    """True if the list and any run after it already *written*, followed by
    *candidate* written as is, are read back as *expected* (kind, text, and
    items, in order): so *candidate* round-trips written as is, in the tail
    of the list ``expected[0]``."""
    lines = "\n\n".join([*written, candidate]).split("\n")
    got, _sections = _parse_body(lines)
    tail = got[-len(expected):]
    return tail == expected


def _fence_left_open(candidate: str) -> bool:
    """True if the fenced code block opening *candidate* (a tail block's
    text, indented to a list item's content) is not closed by its own last
    line: written as is, it would run on — through a blank line, and
    whatever came after it in the same run — to its bound or to the end of
    the text (``parser._tail_block``)."""
    indent = len(candidate) - len(candidate.lstrip(" \t"))
    dedented = "\n".join(dedent(line, indent) for line in candidate.split("\n"))
    return close_fences(dedented) != dedented


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
    # Only line breaks are trimmed: a document may open with indented code.
    return "\n".join(parts).strip("\n") + "\n"




def render_block(block: Block) -> str:
    """Render a single block to LegalDown source.

    Public alias for the block renderer: applications that render one
    block at a time (editors, previews) need it, and a private name is
    not a contract this package can keep stable.
    """
    return _render_block(block)
