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
    close_fences,
    dedent,
    html_block_end,
    indent_width,
    is_indented_code,
    split_lone_tag,
    strip_text,
)
from .markers import Marker, format_marker
from .models import Amends, Block, Document, Metadata, listed_items, metadata_from_dict
from .parser import HEADING_RE, LIST_ITEM_RE, RULE_RE

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
    if not metadata.include_signatures:
        # Only the non-default value: a document that leaves signature
        # blocks out keeps doing so (§2.2 leaves them to the implementation).
        payload["include_signatures"] = False
    if metadata.tags:
        payload["tags"] = metadata.tags
    return payload


def _list_item(prefix: str, item: str, *, close: bool = False) -> str:
    """A list item written after *prefix*, its indentation and marker: its
    later lines are indented to the item's content; blank lines stay blank.
    A fence left open in it ends with the item, as CommonMark reads it;
    *close* closes it, where the parser would read the indented lines after
    it into it."""
    first, *rest = (prefix + (close_fences(item) if close else item)).split("\n")
    return "\n".join([first, *(" " * len(prefix) + line if line else line for line in rest)])


def _opens_block(text: str) -> bool:
    """True if *text* at the margin would open a code block, a heading, or
    an HTML block rather than a paragraph."""
    return bool(FENCE_OPEN_RE.match(text) or HEADING_RE.match(text) or html_block_end([text], 0) is not None)


def _paragraph(text: str) -> str:
    """A paragraph's text, written at the margin. A paragraph whose text
    only reads as a complete HTML tag (kind 7) got that way by being
    joined, at a space, from two source lines that alone opened nothing
    (``_parse_paragraph``); writing it back over two lines the same way,
    at that space, is lossless (``split_lone_tag``) and keeps it a
    paragraph rather than an HTML block. Anything else that would open a
    block (a fence, a heading, an HTML block kind 1–6, a list item) gets a
    backslash before it instead, which renders as nothing (CommonMark): only a model
    built in code, not one from ``parse_document``, holds that, since the
    parser never turns the start of a block into paragraph text."""
    if split := split_lone_tag(text):
        return split
    if _opens_block(text):
        return "\\" + text
    if marker := LIST_ITEM_RE.match(text):
        # A list item's marker, bare ones included: the backslash goes before
        # its bullet or its delimiter (``1\\.``), the characters it escapes.
        at = marker.start("delimiter") if marker.group("delimiter") else marker.start("bullet")
        return text[:at] + "\\" + text[at:]
    return text


def _code(text: str, *, after_list: bool) -> str:
    """A code block, fenced or indented, as it is; other text as it is, to
    be read as what it is. Indented code directly after a list that the
    list could not be written to end before (``_render_list``) is written
    fenced: indented, it would continue the list's last item."""
    if FENCE_OPEN_RE.match(text):
        return close_fences(text)
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


def _render_block(block: Block) -> str:
    """*block* as source."""
    if block.kind == "paragraph":
        return _paragraph(strip_text(block.text))
    if block.kind == "definition":
        term = block.term.strip() or block.definition_id.replace("-", " ").title()
        did = block.definition_id.strip()
        anchor = (
            f'"{term}" {{{{def: {format_value(did, positional=True)}}}}}'
            if did
            else f'"{term}" {{{{def:}}}}'
        )
        body = strip_text(block.text)
        return _paragraph(f"{anchor} {body}" if body else anchor)
    if block.kind == "ref":
        target = format_value(block.target, positional=True)
        return _paragraph(strip_text(f"{block.prefix}{{{{ref: {target}}}}}{block.suffix}"))
    if block.kind == "term":
        target = format_value(block.target, positional=True)
        label_part = f", label={format_value(block.label)}" if block.label else ""
        return _paragraph(strip_text(f"{block.prefix}{{{{term: {target}{label_part}}}}}{block.suffix}"))
    if block.kind in _LISTS:
        return _render_list(block) or ""
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


def list_runs(nesting: list[tuple[int, str]]) -> list[int]:
    """Which list each item is in, as an index into the lists in order of
    their first item: an item continues the list of the last item before it
    at its depth, under the same parent and of the same kind, and otherwise
    starts one. *nesting* holds each item's depth and kind, depths starting
    at 0 and rising by at most one (``list_nesting``)."""
    runs: list[int] = []
    open_runs: list[tuple[int, str]] = []  # (list, kind) of the last item at each depth
    for level, kind in nesting:
        del open_runs[level + 1:]
        if level < len(open_runs) and open_runs[level][1] == kind:
            run = open_runs[level][0]
        else:
            run = max(runs, default=-1) + 1
        open_runs[level:] = [(run, kind)]
        runs.append(run)
    return runs


def _render_list(block: Block, last_column: int = 0, *, close_last: bool = False) -> str | None:
    """A list, its items nested as the model holds them (``listed_items``),
    each at its parent's content column. The last item of the list itself
    has its content start at *last_column* or past it — more spacing after
    its marker (at most four, CommonMark) — so that a block written after
    the list, indented less, is not read as that item's or as that of an
    item nested in it (§5.7). None when no spacing reaches *last_column*.
    *close_last*: a fence left open in the last item is closed, since
    another list follows that it would otherwise run into."""
    listed = listed_items(block)
    nesting = [(level, kind) for _item, level, kind in listed]
    runs = list_runs(nesting)
    # An item whose text begins with dashes, such as "--", would make a
    # "- " line a thematic break; "+" never forms one.
    plus = {
        run for run, (item, _level, kind) in zip(runs, listed, strict=True)
        if kind == "unordered_list" and RULE_RE.match("- " + item.split("\n")[0])
    }
    numbers: dict[int, int] = {}
    markers: list[str] = []
    for run, (_item, _level, kind) in zip(runs, listed, strict=True):
        if kind == "unordered_list":
            markers.append("+ " if run in plus else "- ")
        else:
            numbers[run] = numbers.get(run, 0) + 1
            markers.append(f"{numbers[run]}. ")
    last = max((k for k, (_item, level, _kind) in enumerate(listed) if not level), default=None)
    columns: list[int] = []  # the content column of the last item at each depth
    prefixes: list[str] = []
    for k, ((item, level, _kind), marker) in enumerate(zip(listed, markers, strict=True)):
        indent = columns[level - 1] if level else 0
        if item.strip():
            # A later line of an item that reads as an ATX heading at the
            # margin is written at least four columns in, where the parser
            # keeps it the item's text (``parser._parse_list``): its item's
            # content starts further in.
            rows = [row for row in item.split("\n")[1:] if HEADING_RE.match(row)]
            needed = 4 - min((indent_width(row) for row in rows), default=4) - indent
            if k == last:
                needed = max(needed, last_column)  # an item of the list itself: at the margin
            if len(marker) < needed:
                spacing = needed - len(marker.rstrip())
                if spacing > 4:
                    return None  # past last_column only: a heading row needs less
                marker = marker.rstrip() + " " * spacing
            width = len(marker)
        else:
            # An empty item is its bare marker: its content column is one
            # past it whatever follows (``item_content_column``), so spacing
            # cannot move it. One the list ends in is not open
            # (``open_items``); one with items nested after it is, and cannot
            # keep a block indented to *last_column* out.
            if k == last and last_column and k != len(listed) - 1:
                return None
            marker = marker.rstrip()
            width = len(marker) + 1
        prefixes.append(" " * indent + marker)
        columns[level:] = [indent + width]
    rendered = []
    for k, ((item, level, _kind), prefix) in enumerate(zip(listed, prefixes, strict=True)):
        # A fence left open in an item runs on into what is indented to its
        # content after it: an item nested in it, or a block after the list.
        following = k + 1 < len(listed) and listed[k + 1][1] > level
        close = following or ((close_last or bool(last_column)) and k == len(listed) - 1)
        if (
            k > 0 and not item.strip()
            and level == listed[k - 1][1] + 1 and listed[k - 1][0].strip()
        ):
            # An empty item cannot interrupt its parent's text (CommonMark):
            # a blank line comes between. Not after an empty parent, which a
            # blank line would end.
            rendered.append("")
        rendered.append(_list_item(prefix, item, close=close))
    return "\n".join(rendered)


def _render_blocks(blocks: list[Block]) -> list[str]:
    """Rendered blocks, each preceded by a blank separator line.

    A line indented to a list's last item's content continues the item,
    past a blank line too (§5.7, ``parser._parse_list``): a paragraph after
    a list is written at the margin like any other (``_paragraph``), and a
    list followed by indented code or HTML is written with its last item's
    content past that indentation (``_render_list``), so the block is
    written as it is; where no spacing reaches it, code is fenced
    (``_code``)."""
    parts: list[str] = []
    as_written: set[int] = set()  # code or HTML after a list the list is written to end before
    # A block that writes nothing does not stand between a list and what
    # follows it.
    blocks = [
        block for block in blocks
        if (bool(block.items) if block.kind in _LISTS else _render_block(block))
    ]
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if block.kind in _LISTS and following is not None and following.kind in ("code", "html"):
            # A block indented to the last item's content would continue it:
            # the item is written with its content further in.
            rendered = _render_list(block, indent_width(following.text) + 1)
            if rendered is not None:
                as_written.add(index + 1)
            else:
                rendered = _render_block(block)
        elif block.kind in _LISTS and following is not None and following.kind == block.kind:
            # A fence left open in the last item would run on through the
            # blank line into the next list, which then continues this one.
            rendered = _render_list(block, close_last=True) or ""
        elif index in as_written:
            rendered = close_fences(block.text) if block.kind == "code" else block.text
        elif index > 0 and blocks[index - 1].kind in _LISTS and block.kind == "code":
            rendered = _code(block.text, after_list=True)
        else:
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
    # Only line breaks are trimmed: a document may open with indented code.
    return "\n".join(parts).strip("\n") + "\n"




def render_block(block: Block) -> str:
    """Render a single block to LegalDown source.

    Public alias for the block renderer: applications that render one
    block at a time (editors, previews) need it, and a private name is
    not a contract this package can keep stable.
    """
    return _render_block(block)
