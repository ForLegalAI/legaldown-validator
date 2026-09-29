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
    is_blank,
    is_indented_code,
    split_lone_tag,
    strip_text,
)
from .markers import Marker, format_marker
from .models import (
    PARTY_KEYS,
    Amends,
    Block,
    Document,
    ListItem,
    Metadata,
    Party,
    Side,
    heading_level,
    heading_text,
    list_items,
    metadata_from_dict,
)
from .parser import ATX_HEADING_RE, HEADING_RE, LIST_ITEM_RE, RULE_RE, _expand_prefix, _paragraph_end, _parse_table

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
        # Every side and party the model holds, as far as it is filled in:
        # one declared but not yet complete stays, where the validator
        # reports what it lacks (parties-minimum, representative-name-empty)
        # rather than what its absence would cause.
        payload["sides"] = [_side(side) for side in metadata.sides]
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
        # Each with its required fields, empty ones too (attachment-title-empty).
        payload["attachments"] = [
            {"id": att.id, "title": att.title, "file": att.file}
            | ({"when": att.when} if att.when else {})
            for att in metadata.attachments
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


def _side(side: Side) -> dict[str, Any]:
    """A side as frontmatter (§3.3): its name and parties always, empty ones
    too."""
    written: dict[str, Any] = {"name": side.name}
    if side.label:
        written["label"] = side.label
    written["parties"] = [_party(party) for party in side.parties]
    return written


def _party(party: Party) -> dict[str, Any]:
    """A party as frontmatter (§3.4): its name always, its other fields when
    set, and its custom fields as keys of their own, an empty one too (a row
    whose label is not yet written). When keys cannot hold them all — a
    label written twice, or naming one of the party's fields — they are
    written as its ``custom_fields`` list instead, in order, which the
    parser reads as well."""
    written: dict[str, Any] = {"name": party.name}
    for key in ("label", "type", "legal_name", "identification_number", "address", "date_of_birth"):
        if value := getattr(party, key):
            written[key] = value
    if party.representatives:
        written["representatives"] = [
            {"name": rep.name} | ({"title": rep.title} if rep.title else {}) for rep in party.representatives
        ]
    labels = [custom.label.strip() for custom in party.custom_fields]
    if len(set(labels)) == len(labels) and not PARTY_KEYS.intersection(labels):
        written.update(zip(labels, (custom.value for custom in party.custom_fields), strict=True))
    elif labels:
        written["custom_fields"] = [
            {"label": label, "value": custom.value} for label, custom in zip(labels, party.custom_fields, strict=True)
        ]
    return written


def _opens_block(text: str, *, in_item: bool = False) -> bool:
    """True if *text* at the margin would open a code block, a heading (in a
    list item, an empty one too), or an HTML block rather than a paragraph."""
    heading = ATX_HEADING_RE if in_item else HEADING_RE
    return bool(FENCE_OPEN_RE.match(text) or heading.match(text) or html_block_end([text], 0) is not None)


def _paragraph(text: str, *, in_item: bool = False) -> str:
    """A paragraph's text, written at the margin as its lines
    (``_paragraph_line``, ``_continues``). *in_item*: in a list item's
    content, where ``#`` alone is a heading too. A blank line, which only a
    model built in code holds, would end the paragraph: it is left out."""
    lines = [line for line in text.split("\n") if not is_blank(line)]
    if not lines:
        return ""
    written = [_paragraph_line(lines[0].lstrip(" \t"), in_item=in_item, alone=len(lines) == 1)]
    for line in lines[1:]:
        # A line's indentation is not its text (``paragraph_text``).
        line = line.lstrip(" \t")
        # Written as it is where the parser reads it as continuing the
        # paragraph: else four columns in, where a line always does, and is
        # read without its indentation. The parser never makes a paragraph
        # of lines that need it but for lazy ones (``===`` under a quote's
        # or an item's paragraph, where it is no underline).
        written.append(line if _continues([*written, line]) else "    " + line)
    return "\n".join(written)


def _continues(lines: list[str]) -> bool:
    """True if *lines* at the margin read as one paragraph: neither a table
    (GFM) nor a paragraph that ends, as a setext heading or otherwise,
    before its last line. A line of ``#`` alone, which only outside items
    and quotes the parser reads as text (a section's heading needs text),
    ends it too, as it does for CommonMark."""
    return _parse_table(lines, 0) is None and _paragraph_end(lines, 0, False, headings=False) == (len(lines), 0)


def _paragraph_line(text: str, *, in_item: bool, alone: bool) -> str:
    """A paragraph's first line, written at the margin. A paragraph of one
    line that only reads as a complete HTML tag (kind 7) got that way by
    being joined, at a space, from two lines that alone opened nothing (a
    model of an older version, or one built in code); writing it back over
    two lines the same way, at that space, is lossless
    (``split_lone_tag``) and keeps it a paragraph rather than an HTML block.
    Anything else that would open a block (a fence, a heading, an HTML block,
    a block quote, a thematic break, a list item) gets a backslash before it
    instead, which renders as nothing (CommonMark): only a model built in
    code, not one from ``parse_document``, holds that, since the parser
    never turns the start of a block into paragraph text."""
    if alone and (split := split_lone_tag(text)):
        return split
    if _opens_block(text, in_item=in_item) or text.startswith(">") or RULE_RE.match(text):
        return "\\" + text
    if marker := LIST_ITEM_RE.match(text):
        # A list item's marker, bare ones included: the backslash goes before
        # its bullet or its delimiter (``1\\.``), the characters it escapes.
        at = marker.start("delimiter") if marker.group("delimiter") else marker.start("bullet")
        return text[:at] + "\\" + text[at:]
    return text


def _heading(block: Block) -> str:
    """A heading block as an ATX heading line (§4.1). A ``#`` run ending its
    text, which would be read as a closing sequence, gets a backslash before
    it; an empty heading is written with one (``# #``), as ``#`` alone
    would not be read as a heading."""
    hashes = "#" * heading_level(block.level)
    text = heading_text(block.text)
    if not text:
        return f"{hashes} #"
    if closing := re.search(r"(?:^|[ \t])(#+)$", text):
        text = text[: closing.start(1)] + "\\" + text[closing.start(1):]
    return f"{hashes} {text}"


def _code(text: str, *, after_list: bool, keep_open: bool = False) -> str:
    """A code block, fenced or indented, as it is; other text as it is, to
    be read as what it is. Indented code directly after a list that the
    list could not be written to end before (``_render_list``) is written
    fenced: indented, it would continue the list's last item. A fence left
    open is closed, so that nothing after it is read into it, unless
    *keep_open*: nothing follows it."""
    if FENCE_OPEN_RE.match(text):
        return text if keep_open else close_fences(text)
    if not (after_list and is_indented_code(text)):
        return text
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)  # longer than any backtick run in the code
    return "\n".join([fence, *(dedent(line, 4) for line in text.split("\n")), fence])


_DELIMITERS = {"left": ":---", "right": "---:", "center": ":---:"}


def _table_cell(text: str) -> str:
    """A table cell's text with a backslash before each pipe, so that it
    does not split the row (GFM): the parser removes exactly that one."""
    return strip_text(" ".join(text.split("\n"))).replace("|", "\\|")


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(_table_cell(cell) for cell in cells) + " |"


def _render_block(block: Block, *, in_item: bool = False) -> str:
    """*block* as source; *in_item*: in a list item's content."""
    if block.kind == "paragraph":
        return _paragraph(strip_text(block.text), in_item=in_item)
    if block.kind == "heading":
        # A heading at the margin would be a section's: outside an item —
        # only a model built in code has one there — it is written as a
        # paragraph of its line, with a backslash before it.
        line = _heading(block)
        return line if in_item else "\\" + line
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
        # The parser joins quoted lines with LF. A tab in a line's leading
        # markers and indentation is written as the columns the quote's
        # content gives it, which a tab after "> " would not keep. A line's
        # trailing whitespace is kept (it may be a hard break); a blank line
        # is written as ">".
        lines = block.text.split("\n")
        return "\n".join(">" if is_blank(line) else f"> {_expand_prefix(line)}" for line in lines)
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
    if block.kind == "source":
        # As it stands, but for a fence it leaves open (``_render_blocks``
        # keeps one open that ends the document).
        return close_fences(block.text)
    if block.kind == "html":
        return block.text
    return block.text.strip()


_LISTS = ("ordered_list", "unordered_list")


_BULLETS = ("-", "+", "*")  # in the order a list takes them


def _first_line(item: ListItem) -> str:
    """The first line of *item*'s content as written, as far as its bullet
    needs it: the line that follows the marker."""
    if not item.blocks:
        return ""
    first = item.blocks[0]
    if first.kind in _LISTS:
        rows = [_first_line(inner) for inner in list_items(first)]  # each once: a chain of them nests
        own = "." if first.kind == "ordered_list" else (_safe(rows) or ["+"])[0]
        head = f"1{own}" if first.kind == "ordered_list" else own
        return f"{head} {rows[0]}" if rows and rows[0] else head
    return _render_block(first, in_item=True).split("\n")[0]


def _safe(rows: list[str]) -> list[str]:
    """The bullets that make none of *rows*, items' first lines, a thematic
    break (``- --``); ``+`` never does."""
    return [bullet for bullet in _BULLETS if not any(RULE_RE.match(f"{bullet} {row}") for row in rows)]


def _bullets(block: Block) -> list[str]:
    """The bullets a list's own items can be written with (``_safe``)."""
    return _safe([_first_line(item) for item in list_items(block)])


def _list_markers(blocks: list[Block]) -> dict[int, str]:
    """The bullet or delimiter each list among *blocks* writes its own items
    with, by index. A list right after one of its kind takes another, so
    that CommonMark, which would merge the two into one list, keeps them
    apart, as this parser does (``parser._list_type``): bullets are chosen
    along each run of such lists so that every one has a bullet left that
    differs from the one before, where its items allow it."""
    markers: dict[int, str] = {}
    start = 0
    while start < len(blocks):
        kind = blocks[start].kind
        end = start + 1
        while kind in _LISTS and end < len(blocks) and blocks[end].kind == kind:
            end += 1
        if kind == "ordered_list":
            markers.update((k, ".)"[(k - start) % 2]) for k in range(start, end))
        elif kind == "unordered_list":
            # A bullet that would make an item's first line a thematic break
            # is left for last: its item starts on the next line
            # (``_render_list``).
            options = [
                [*_bullets(blocks[k]), *(b for b in _BULLETS if b not in _bullets(blocks[k]))]
                for k in range(start, end)
            ]
            # The bullets each list can take and still leave the next one a
            # different bullet, and so on to the end of the run.
            usable = [[] for _ in options]
            for j in reversed(range(len(options))):
                usable[j] = [
                    bullet for bullet in options[j]
                    if j == len(options) - 1 or any(other != bullet for other in usable[j + 1])
                ]
            previous = ""
            for j, k in enumerate(range(start, end)):
                choices = [bullet for bullet in usable[j] or options[j] if bullet != previous]
                markers[k] = previous = (choices or options[j])[0]
        start = end
    return markers


def _item_content(item: ListItem, *, open_end: bool) -> str:
    """*item*'s blocks as written in its content, from its content column;
    *open_end*: a fence left open in its last block may stay open, nothing
    after the item being read into it."""
    return "\n".join(_render_blocks(item.blocks, last=open_end, in_item=True)[1:])


def _render_list(
    block: Block, last_column: int = 0, *, own: str = "", close_last: bool = False, in_item: bool = False,
) -> str | None:
    """A list: each item's marker, then its content (``_item_content``),
    later lines indented to the item's content column; an empty item is its
    bare marker. The last item has its content start at *last_column* or
    past it — more spacing after its marker (at most four, CommonMark) — so
    that a block written after the list, indented less, is not read as that
    item's (§5.7); None when no spacing reaches it. *own*: the bullet or
    delimiter of its items (``_list_markers``), by default its first choice.
    A fence left open at the end of the last item is closed when
    *close_last* (what follows would run into it: a list with the same
    marker, or code the list could not be written to end before) or
    *last_column* (so would the block after the list)."""
    ordered = block.kind == "ordered_list"
    if not own:
        own = "." if ordered else (_bullets(block) or ["+"])[0]
    last = len(block.items) - 1
    written = []
    for k, item in enumerate(list_items(block)):
        marker = f"{k + 1}{own} " if ordered else f"{own} "
        # A fence left open in the last item ends at what follows the list,
        # short of the item's content (``parser._scan_list``).
        stays_open = k < last or not close_last
        content = _item_content(item, open_end=stays_open)
        rows = content.split("\n") if content else []
        if rows and (RULE_RE.match(marker + rows[0]) or rows[0][:1] == " "):
            # The marker line would be a thematic break (``- --``), or the
            # content's first line is indented further, which spacing after
            # the marker cannot tell: the content starts on the next line,
            # after the bare marker.
            rows = ["", *rows]
        needed = 0
        if not in_item:
            # A later line that reads as an ATX heading at the margin is
            # written at least four columns in, where the parser keeps it
            # the item's (``parser._scan_list``).
            headings = [row for row in rows[1:] if HEADING_RE.match(row)]
            needed = 4 - min((indent_width(row) for row in headings), default=4)
        if k == last and rows:
            needed = max(needed, last_column)
        if len(marker) < needed:
            spacing = needed - len(marker.rstrip())
            if spacing > 4:
                return None  # past last_column only: a heading line needs less
            marker = marker.rstrip() + " " * spacing
        if not rows or not rows[0]:
            if rows and k == last and last_column > len(marker.rstrip()) + 1:
                return None  # a bare marker's content column is one past it: no spacing moves it
            head = marker.rstrip()  # an empty item, or one whose content starts on the next line
            marker = marker.rstrip() + " "
        else:
            head = marker + rows[0]
        written.append("\n".join([head, *(" " * len(marker) + row if row else row for row in rows[1:])]))
    return "\n".join(written)


def _written(blocks: list[Block]) -> list[Block]:
    """The blocks that write anything: one that writes nothing does not
    stand between a list and what follows it."""
    return [block for block in blocks if (bool(block.items) if block.kind in _LISTS else _render_block(block))]


def _bare(rendered: str) -> bool:
    """True if a rendered list opens with a bare marker, an empty item's."""
    first = rendered.split("\n", 1)[0]
    return bool(re.fullmatch(r"(?:[-+*]|[0-9]{1,9}[.)])", first))


def _render_blocks(blocks: list[Block], *, last: bool = False, in_item: bool = False) -> list[str]:
    """Rendered blocks, each preceded by a blank separator line.

    A line indented to a list's last item's content continues the item,
    past a blank line too (§5.7, ``parser._parse_list``): a paragraph after
    a list is written at the margin like any other (``_paragraph``), and a
    list followed by indented code or HTML is written with its last item's
    content past that indentation (``_render_list``), so the block is
    written as it is; where no spacing reaches it, code is fenced
    (``_code``). *last*: nothing written after the blocks is read into them
    — they end the document, or a list item whose end ends a fence (the
    parser's too) — so a fence left open in the last of them stays open, as
    the parser read it. *in_item*: the blocks are a list item's content,
    where a list right after a paragraph follows it directly (unless its
    bare marker could not interrupt the paragraph, CommonMark)."""
    parts: list[str] = []
    as_written: set[int] = set()  # code or HTML after a list the list is written to end before
    blocks = _written(blocks)
    final = len(blocks) - 1
    markers = _list_markers(blocks)
    for index, block in enumerate(blocks):
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        keep_open = last and index == final
        if block.kind in _LISTS and following is not None and following.kind in ("code", "html", "source"):
            # A block indented to the last item's content would continue it:
            # the item is written with its content further in.
            rendered = _render_list(
                block, indent_width(following.text) + 1, own=markers[index], in_item=in_item
            )
            if rendered is not None:
                as_written.add(index + 1)
            else:
                rendered = _render_list(block, own=markers[index], close_last=True, in_item=in_item) or ""
        elif block.kind in _LISTS:
            # Only where the items leave no other bullet does a list follow
            # one with its marker.
            same = following is not None and following.kind == block.kind and markers[index + 1] == markers[index]
            # What follows the list starts short of its items' content, where
            # a fence left open in them ends.
            rendered = _render_list(block, own=markers[index], close_last=same, in_item=in_item) or ""
        elif index in as_written:
            rendered = (block.text if keep_open else close_fences(block.text)) if block.kind != "html" else block.text
        elif index > 0 and blocks[index - 1].kind in _LISTS and block.kind == "code":
            rendered = _code(block.text, after_list=True, keep_open=keep_open)
        elif block.kind == "code":
            rendered = _code(block.text, after_list=False, keep_open=keep_open)
        elif block.kind == "source":
            # As it stands, but for a fence it leaves open, which would run
            # on into what follows it: closed unless nothing does.
            rendered = block.text if keep_open else close_fences(block.text)
        else:
            rendered = _render_block(block, in_item=in_item)
        if rendered:
            # In an item, what may interrupt a paragraph follows it directly,
            # and so does a list another list (CommonMark), keeping the list
            # tight as it is written.
            before = blocks[index - 1] if index > 0 else None
            closed = before is not None and before.kind == "code" and FENCE_OPEN_RE.match(before.text.split("\n")[0])
            heading = block.kind == "heading"
            tight = in_item and before is not None and (
                # A heading's line, which a blank line before it would make a
                # section's (``parser._scan_list``), follows any block that
                # ends at it.
                (heading and before.kind != "html")
                or
                (block.kind in _LISTS and before.kind in ("paragraph", "heading", *_LISTS) and not _bare(rendered))
                or (before.kind == "paragraph" and (block.kind == "quote" or FENCE_OPEN_RE.match(rendered.split("\n")[0])))
                # After a closed fence a block starts on the next line.
                or (closed and close_fences(before.text) == before.text and not _bare(rendered))
            )
            # A fence left open at the end of the block before would take a
            # blank line into its code: what follows, starting short of its
            # content, ends it directly (and blank lines it ends in are its).
            separated = index > 0 and blocks[index - 1].kind in _LISTS and _ends_open(blocks[index - 1:index])
            parts.extend([rendered] if (tight or separated) and parts else ["", rendered])
    return parts


def _ends_open(blocks: list[Block]) -> bool:
    """True if the last block written of *blocks* ends in a fence left open,
    in a list item's content too, which holds the blank lines after it."""
    written = _written(blocks)
    if not written:
        return False
    last = written[-1]
    if last.kind in _LISTS:
        return bool(last.items) and _ends_open(list_items(last)[-1].blocks)
    return last.kind in ("code", "source") and close_fences(last.text) != last.text


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
    preamble = _render_blocks(document.preamble, last=not document.sections)
    if bare and preamble[1:2] and preamble[1].split("\n", 1)[0] == "---":
        preamble[1] = "***" + preamble[1][3:]
    parts.extend(preamble)
    blocks = document.preamble
    for number, section in enumerate(document.sections, 1):
        heading = f"{'#' * section.level} {section.title.strip()}"
        marker = format_marker(Marker(section.identifier.strip(), section.condition.strip()))
        if marker:
            heading += " " + marker
        # A fence left open in a list item before it ends at the heading.
        written = _written(blocks)
        after_open = bool(written) and written[-1].kind in _LISTS and _ends_open(written)
        parts.extend([heading] if after_open else ["", heading])
        parts.extend(_render_blocks(section.blocks, last=number == len(document.sections)))
        blocks = section.blocks
    # Only line breaks are trimmed: a document may open with indented code,
    # and a fence left open at its end holds its blank lines as code.
    text = "\n".join(parts).lstrip("\n")
    if _ends_open(document.sections[-1].blocks if document.sections else document.preamble):
        return text + "\n"
    return text.rstrip("\n") + "\n"




def render_item(item: ListItem) -> str:
    """A list item's content as written after its marker, later lines
    indented from its content column: the text ``block_from_dict`` reads
    back as the item (a string item)."""
    return _item_content(item, open_end=True)


def render_block(block: Block) -> str:
    """Render a single block to LegalDown source.

    Public alias for the block renderer: applications that render one
    block at a time (editors, previews) need it, and a private name is
    not a contract this package can keep stable.
    """
    return _render_block(block)
