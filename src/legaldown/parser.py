"""LegalDown parser — converts .legal.md source text into a Document object.

The parser handles:
- YAML frontmatter extraction
- Heading-based section splitting with optional ``{#identifier}`` syntax
- Block-level parsing: paragraphs, definitions, lists, tables, quotes, rules
- Inline directive detection (ref/term blocks)
"""
from __future__ import annotations

import bisect
import copy
import os
import re
import warnings
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from .definitions import DefinitionAnchor, block_fragments, find_definition_anchors
from .directives import Directive, lex
from .markdown import (
    FENCE_OPEN_RE,
    HTML_BLOCK_START_RE,
    LINE_ENDING_RE,
    closes_fence,
    dedent,
    fence_end,
    html_block_end,
    html_block_opening,
    indent_width,
    indented_code_end,
    is_blank,
    item_content_column,
    nested_offset,
    paragraph_text,
    strip_text,
)
from .markers import Marker, split_heading
from .models import Block, Document, ListItem, document_from_dict
from .positions import SourceMap, document_shape, frontmatter_keys

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
HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+)$")  # the text is stripped by split_heading
# An ATX heading as CommonMark reads it, whose text may be empty (``#``): in
# a list item's or a block quote's content, where a heading is a block
# (``_parse_body``). A section's heading needs text (HEADING_RE).
ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
# A setext underline under a paragraph: ``===`` makes a level-1 heading,
# ``---`` a level-2 one. Anywhere else, ``---`` is a thematic break.
SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
# A thematic break: three or more ``-``, ``*``, or ``_``, the same one,
# with optional spaces or tabs between them.
RULE_RE = re.compile(r"^[ \t]*(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
# A list item marker (CommonMark): ``-``, ``*``, or ``+`` for an unordered
# list, a number of at most nine ASCII digits and ``.`` or ``)`` for an
# ordered one, with only spaces and tabs around it — other whitespace is
# text. A thematic break such as ``* * *`` matches too; callers test
# RULE_RE first.
LIST_ITEM_RE = re.compile(r"^[ \t]*(?:(?P<number>[0-9]{1,9})(?P<delimiter>[.)])|(?P<bullet>[-*+]))(?:[ \t]+|$)")
# A cell of a table's delimiter row (GFM): colons mark the alignment.
_DELIMITER_CELL_RE = re.compile(r":?-+:?")


# ── Internal helpers ──────────────────────────────────────────────

class FrontmatterError(yaml.YAMLError, ValueError):
    """The frontmatter cannot be read (§3.1, frontmatter-invalid-yaml): its
    YAML is malformed, nested too deep, or a mapping of another kind (a
    ``!!set``). A ``yaml.YAMLError`` and a ``ValueError``, as what
    ``parse`` raised for it before. ``line`` is the file line (from
    1) of the problem, when the YAML reader gives one (§16.9)."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(message)
        self.line = line


def _split_frontmatter(source: str) -> tuple[list[str], dict[str, Any], str, bool, dict[tuple[Any, ...], int]]:
    """Split *source* into ``(keys not line-editable, parsed frontmatter,
    body, absent, lines)``; the first is read from how the YAML is written,
    which the parsed data loses, and the last is where each frontmatter node
    is (``positions.frontmatter_keys``, the opening ``---`` at the empty
    path), empty without frontmatter. The frontmatter is *absent* when no closed
    ``---`` block opens the source, or when the block's YAML is a scalar or
    a list rather than a mapping of fields: then its ``---`` lines are
    thematic breaks, and the whole source is body. A block that is empty or
    holds only comments is present and empty."""
    match = FRONTMATTER_RE.match(source)
    if not match:
        return [], {}, source, True, {}
    loader = _StrDateSafeLoader(match.group(1) or "")
    try:
        node = loader.get_single_node()
        if node is None:
            return [], {}, source[match.end():], False, {(): 1}
        if not isinstance(node, yaml.MappingNode):
            return [], {}, source, True, {}
        if node.tag != "tag:yaml.org,2002:map":
            # A mapping tagged as something else (!!set, a flow !!omap) is
            # meant as frontmatter, but holds no fields. (A block !!omap is
            # a sequence: not frontmatter.)
            raise FrontmatterError("Frontmatter must be a YAML mapping of fields.", node.start_mark.line + 2)
        # Keys merged into the root count, but each entry is judged as
        # written, before merges inside it reorder its keys.
        loader.flatten_mapping(node)
        # The YAML starts on the line after the opening `---` (line 1).
        keys = {(): 1, **frontmatter_keys(node, 2)}
        not_line_editable = _not_line_editable(node)
        _read_as_written(loader, node)
        metadata = loader.construct_document(node)
    except FrontmatterError:
        raise
    except (yaml.YAMLError, ValueError, KeyError, RecursionError) as exc:
        # What the YAML says, not a fault of this parser: malformed YAML, a
        # value its tag cannot hold (an integer too long to convert; a
        # `!!bool maybe`, which PyYAML looks up as a key), or nesting deeper
        # than the YAML reader goes.
        raise FrontmatterError(str(exc) or type(exc).__name__, _error_line(exc)) from exc
    finally:
        loader.dispose()
    if "questions" in metadata and metadata["questions"] is None:
        metadata["questions"] = {}  # a `questions:` key left empty is still declared (§15.1)
    return not_line_editable, metadata, source[match.end():], False, keys


def _error_line(exc: BaseException) -> int | None:
    """The file line of a YAML error in the frontmatter, whose YAML starts on
    line 2: where the problem is — or, for a quoted scalar or a flow
    collection left open, where it opens, which the problem is only found
    far past."""
    context = getattr(exc, "context", None) or ""
    context_mark = getattr(exc, "context_mark", None)
    problem_mark = getattr(exc, "problem_mark", None)
    opened = context_mark is not None and ("quoted" in context or "flow" in context)
    mark = context_mark if opened else problem_mark or context_mark
    return mark.line + 2 if mark is not None else None


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
    the list item or block quote whose paragraph it continues — a table's
    row included, whose delimiter row must be in the container (GFM). Any
    list item marker is taken to start a list, as this parser has always
    read them; a lone HTML tag, which cannot interrupt a
    paragraph, starts an HTML block where the line leaves its container
    (cmark-gfm). A line indented four or more columns starts none: indented
    code cannot interrupt a paragraph."""
    if indent_width(line) >= 4:
        return bool(line.strip(" \t"))
    return (
        bool(line.strip(" \t"))
        and not FENCE_OPEN_RE.match(line)
        and not ATX_HEADING_RE.match(line)
        and not RULE_RE.match(line)
        and not LIST_ITEM_RE.match(line)
        and html_block_opening(line) is None
        and not line.lstrip(" \t").startswith(">")
    )


# Leading block quote markers and the whitespace around them.
_QUOTE_MARKERS_RE = re.compile(r"[ \t]*(?:>[ \t]*)*")


def _opens_paragraph(content: str) -> bool:
    """True if a quoted line's *content* leaves paragraph text open — its
    own, or that of a list item or nested quote it starts, behind markers of
    any nesting — which a lazy line may continue. A heading, a thematic
    break, a fence, an HTML block, or an empty list item or quote leaves
    none."""
    inner = content
    while True:
        inner = inner[_QUOTE_MARKERS_RE.match(inner).end():]
        marker = None if RULE_RE.match(inner) else LIST_ITEM_RE.match(inner)
        if not marker:
            break
        inner = inner[marker.end():]
    return (
        bool(inner.strip(" \t"))
        and not (FENCE_OPEN_RE.match(inner) and indent_width(content) < 4)
        and not ATX_HEADING_RE.match(inner)
        and not RULE_RE.match(inner)
        and not HTML_BLOCK_START_RE.match(inner)
    )


# How deep ``_Quote`` follows nested quotes; in deeper ones, a line's
# markers are skipped (``_opens_paragraph``).
_QUOTE_DEPTH = 8


class _Quote:
    """A block quote read line by line (CommonMark), for what a line without
    ``>`` needs to know: whether a paragraph is open that it lazily
    continues. Quoted code, fenced or indented, raw HTML and a table are not
    a paragraph; a nested quote's paragraph is, until a line that neither
    carries its ``>`` nor lazily continues it."""

    def __init__(self, depth: int = 0) -> None:
        self.paragraph = False
        self._fence: str | None = None
        self._html: tuple[re.Pattern[str] | None] | None = None  # how an open HTML block ends
        self._inner: _Quote | None = None  # a quote open in this one
        self._column = 0  # the content column of the item holding open code or HTML
        self._depth = depth  # the quotes it is in; past _QUOTE_DEPTH, nested ones are text
        self._table = False  # a table is open: its rows are no paragraph
        self._last: str | None = None  # the paragraph's last line, a table's header if one follows
        self._item = False  # the open paragraph is a list item's, whose content this does not follow
        self._item_column = 0  # that item's content column
        self._item_quote = False  # the paragraph is a quote's, opened in the item's first line

    def read(self, content: str) -> None:
        """Take one quoted line: its *content* after ``>`` and the one
        optional space."""
        if (self._fence is not None or self._html is not None) and (
            not content.strip(" \t") or indent_width(content) >= self._column
        ):
            own = content[self._column:]  # as the item holding it has it
            if self._fence is not None:
                if closes_fence(own, self._fence):
                    self._fence = None
            else:
                (marker,) = self._html
                if marker.search(own) if marker is not None else not own.strip(" \t"):
                    self._html = None
            self.paragraph = False
            return
        # A line short of the content of the item holding open code or HTML
        # ends that item, and them.
        self._fence = self._html = None
        last, self._last = self._last, None
        if indent_width(content) < 4 and content.lstrip(" \t").startswith(">") and self._depth < _QUOTE_DEPTH:
            self._inner = self._inner or _Quote(self._depth + 1)
            self._table = False
            self._inner.read(content.lstrip(" \t")[1:].removeprefix(" "))
            self.paragraph = self._inner.paragraph
            return
        if self._inner is not None:
            inner, self._inner = self._inner, None
            if inner.continues(content):
                self._inner = inner  # a lazy line of the nested quote's paragraph
                return
        if self._table and _continues_table(content):
            return  # a row of the open table
        self._table = False
        column = self._item_column if self._item else 0
        if (
            self.paragraph and not (self._item and self._item_quote)
            and column <= indent_width(content) <= column + 3
            and SETEXT_UNDERLINE_RE.match(content.lstrip(" \t"))
        ):
            # A setext underline in the paragraph's container: a heading,
            # which ends the paragraph.
            self.paragraph = False
            return
        if (
            self.paragraph and last is not None and not self._item
            and _parse_table([last, content], 0) is not None
        ):
            # A delimiter row under the paragraph's line: a table (GFM).
            self._table, self.paragraph = True, False
            return
        # Code or HTML may open the line, or the item or items it starts.
        body, self._column = content, 0
        while indent_width(body) < 4 and not RULE_RE.match(body) and (item := LIST_ITEM_RE.match(body)):
            if not body[item.end():].strip(" \t"):
                break
            column = item_content_column(body)
            body, self._column = body[column:], self._column + column
        if opening := FENCE_OPEN_RE.match(body):
            self._fence = opening.group("fence")
            self.paragraph = False
        elif (html := html_block_opening(body, in_paragraph=self.paragraph and not self._column)) is not None:
            (marker,) = html
            if marker is None or not marker.search(body):
                self._html = html
            self.paragraph = False
        elif self.paragraph or indent_width(content) < 4:  # else indented code
            was = self.paragraph
            self.paragraph = _opens_paragraph(content)
            self._last = content if self.paragraph else None
            # A paragraph opened by an item, or an item started under one, is
            # the item's: a delimiter-like line may be its lazy text.
            starts_item = not RULE_RE.match(content) and LIST_ITEM_RE.match(content) is not None
            self._item = self.paragraph and (starts_item or (was and self._item))
            if self.paragraph and starts_item:
                # A quote in the item holds the paragraph: a line without its
                # ``>`` only lazily continues it.
                self._item_column = self._column
                self._item_quote = body.lstrip(" \t").startswith(">")

    def continues(self, line: str) -> bool:
        """True if *line*, which has no ``>``, is a lazy continuation line of
        the quote."""
        return self.paragraph and _is_lazy_line(line)

    def lazy(self, line: str) -> None:
        """Take a lazy continuation line (``continues``): the paragraph's last
        line now, a table's header if a delimiter row follows in the quote —
        itself never a delimiter row (GFM)."""
        if self._inner is not None:
            self._inner.lazy(line)
        self._last = line


def _read_quote(lines: list[str], index: int) -> tuple[int, list[str], bool]:
    """The block quote starting at ``lines[index]``: ``(end, content,
    paragraph)`` — where it ends, its content one line per source line, and
    whether that ends in open paragraph text.

    After ``>``, one optional space is syntax; any further indentation is
    the content's, the tabs in a line's leading markers and indentation
    counted at the columns they reach (CommonMark: a tab after ``>`` is
    partly that space). A lazy continuation line is content without its
    indentation — but for one indented four or more columns, which can
    look like a block's start (a heading, a fence, an item) that it is not,
    and for one that looks like a setext underline or a table's delimiter
    row, which a lazy line never is: it is put four columns past the widest leading markers since the last
    blank line, past the content of any item it could be in, where it
    continues the paragraph again. Read as blocks (``quote_content``), the
    content is what the quote holds."""
    quote = _Quote()
    content: list[str] = []
    widest, measured = 0, 0  # the widest leading markers since a blank line, in content[:measured]
    end = index
    while end < len(lines):
        line = lines[end]
        if indent_width(line) < 4 and line.lstrip(" \t").startswith(">"):
            text = _expand_prefix(line).lstrip(" \t")[1:].removeprefix(" ")
            quote.read(text)
        elif quote.continues(line):
            quote.lazy(line)
            text = line.strip(" \t")
            if indent_width(line) >= 4 or _delimiter_row(line) is not None or SETEXT_UNDERLINE_RE.match(line):
                for earlier in content[measured:]:
                    widest = max(widest, len(_CONTAINER_PREFIX_RE.match(earlier).group(0))) if earlier.strip(" \t") else 0
                measured = len(content)
                text = " " * (widest + 4) + text
        else:
            break
        content.append(text)
        end += 1
    return end, content, quote.paragraph


def _list_type(marker: re.Match[str]) -> str:
    """What a list item's marker makes it (CommonMark): the bullet of an
    unordered item, the delimiter (``.`` or ``)``) of an ordered one. A
    marker of another type starts a new list."""
    return marker.group("delimiter") or marker.group("bullet")


def _starts_item(line: str, chain: list[int], margin: int) -> re.Match[str] | None:
    """The marker of the list item *line* starts, inside a list whose open
    items' content columns are *chain* (``_parse_list``) and whose own items
    are measured from *margin*: None when *line* is no item's start, or its
    marker lies four or more columns into the content it is in (CommonMark:
    indented code, or text)."""
    marker = None if RULE_RE.match(line) else LIST_ITEM_RE.match(line)
    if marker is None:
        return None
    depth = bisect.bisect_right(chain, indent_width(line))
    return marker if indent_width(line) - (chain[depth - 1] if depth else margin) < 4 else None


def _scan_list(
    lines: list[str], index: int, *, list_type: str, headings: bool
) -> tuple[list[tuple[int, int]], int, bool, dict[int, int]]:
    """Where the list starting at ``lines[index]`` ends, and where its own
    items start: ``(items, end, lazy, text)``, each item as its first line
    and its content column. *lazy* is True when the list ends in paragraph
    text, which a following unindented line would lazily continue. *text*:
    the lines that look like an item's start but, four or more columns into
    the content they are in, lazily continue a paragraph, each with the
    column four past that paragraph's content.

    The walk follows every item, nested ones too, to tell which lines are
    the list's: continuation lines (indented two or more columns, or lazy
    continuation lines of an open paragraph), and past a blank line the
    lines indented into one of its items (§5.7) or starting another item of
    its own (a loose list). An item of another type not nested in one of
    its items, or a thematic break, ends it; so does, where *headings* are
    sections, an ATX heading after a blank line. An item is nested in the
    items whose content its marker reaches, and a marker four or more
    columns into that content starts no item (CommonMark). A fenced code
    block in an item runs until it closes, until an unindented line, or
    until an item starts short of its content; a block quote in an item
    takes its lazy continuation lines. What each item holds is read from its
    lines afterwards (``_parse_list``).
    """
    items: list[str] = []  # each item's text as far as the walk needs it
    own: list[tuple[int, int]] = []  # the list's own items: first line, content column
    text: dict[int, int] = {}  # lazy lines that look like an item's start
    chain: list[int] = []  # the content columns of the current item and those it is nested in
    # Where the list's own items are measured from: the margin, or for a list
    # in the content of an item before it (``_parse_body``'s tail), where it
    # starts.
    margin = indent_width(lines[index]) if indent_width(lines[index]) >= 4 else 0
    fence_inside = False  # the open fence was opened in the current item's content
    fence: str | None = None  # the open fence inside the current item
    html: tuple[re.Pattern[str] | None] | None = None  # how an HTML block open in an item ends
    html_column = 0  # the content column of the item holding it
    first_code = False  # the current item's first line is indented code
    quote: _Quote | None = None  # the block quote the current item ends with
    content_indent = 0  # the current item's content column (CommonMark)
    lazy = False
    paragraph = False  # the current item's own last content is paragraph text
    open_paragraph = False  # so is it, or that of an item nested on its line
    table = False  # the current item's content is in a table
    previous: str | None = None  # the item's last content line, when paragraph text
    previous_code = False  # that line is indented code
    para_column = 0  # the content column of the item whose paragraph is open
    para_quote = False  # the paragraph is a quote's, in an item nested on the first line
    offset = 0  # the current item's nested_offset
    innermost = 0  # past the item's content column, where its innermost first-line item's content starts
    end = index
    while end < len(lines):
        line = lines[end]
        if fence is not None:
            # A line that starts an item, indented less than the current
            # item's content, closes the item and a fence opened in its
            # content (CommonMark): a sibling of a nested item, say.
            starts = (
                fence_inside and indent_width(line) < content_indent
                and _starts_item(line, chain, margin) is not None
            )
            if not starts and (is_blank(line) or indent_width(line) >= (chain[0] if chain else 2)):
                code = dedent(line, content_indent, expand=True)
                items[-1] += "\n" + code
                end += 1
                if closes_fence(code, fence):
                    fence = None
                lazy = False
                continue
            fence = None
        if html is not None and not is_blank(line):
            if indent_width(line) >= html_column:
                # The HTML block's, whatever it looks like — an item's
                # marker, a quote — and no paragraph a lazy line continues.
                (close,) = html
                if close is not None and close.search(line):
                    html = None
                items[-1] += "\n" + dedent(line, content_indent, expand=True)
                lazy = paragraph = open_paragraph = table = False
                quote = previous = None
                end += 1
                continue
            html = None  # short of the item holding it, which that ends
        if is_blank(line):
            if html is not None and html[0] is None:
                html = None  # a block-level or lone tag's HTML ends at a blank line
            following = next((k for k in range(end, len(lines)) if not is_blank(lines[k])), None)
            if following is None or (headings and HEADING_RE.match(lines[following])):
                break  # a heading stays a section heading
            if not items[-1]:
                # An item begins with at most one blank line: an empty item
                # ends here, its parent's content going on.
                chain.pop()
            width = indent_width(lines[following])
            if chain and width >= chain[0]:
                # Indented into an item still open: its content goes on, after
                # the items nested in it too.
                del chain[bisect.bisect_right(chain, width):]
                content_indent = chain[-1]
            elif (
                (sibling := _starts_item(lines[following], [], margin)) is None
                or _list_type(sibling) != list_type
            ):
                break
            else:
                chain.clear()  # another item of the list's own: it is loose
            items[-1] += "\n" * (following - end)  # the blank lines, one row each
            end = following
            lazy = paragraph = open_paragraph = table = False  # the blank line closed the item's paragraph
            quote = previous = None  # a later paragraph in the item is not the quote's
            continue
        was_open, was_table, was_paragraph = open_paragraph, table, paragraph  # before this line
        marker = None if RULE_RE.match(line) else LIST_ITEM_RE.match(line)
        quote_lazy = False  # the line lazily continues the item's quote
        indented = indent_width(line) >= 2
        joined = False  # the line is paragraph text though it looks like an item
        lazy_line = False  # the line lazily continues an open paragraph
        # The items the line is inside: those whose content it reaches.
        depth = bisect.bisect_right(chain, indent_width(line))
        if marker and _starts_item(line, chain, margin) is None:
            # Four or more columns into the content it is in, a marker
            # starts no item (CommonMark).
            if depth == len(chain):
                # The current item's: text of its open paragraph, or code.
                marker = None
                joined = paragraph and quote is None
            elif quote.paragraph if quote is not None else open_paragraph:
                # Short of the current item's content: it lazily continues
                # the paragraph open there, as indented code cannot
                # interrupt one.
                marker = None
                lazy_line = True
                joined = quote is None
                text[end] = content_indent + innermost + 4
            elif not depth:
                break  # indented code after the list
            # Otherwise indented code in an earlier item's content, which the
            # model holds no place for: read as an item, as it always was.
        if (
            marker and paragraph and quote is None and indented
            and indent_width(line) >= content_indent and not _may_interrupt(line, marker)
        ):
            # A nested list would interrupt the item's own open paragraph,
            # which only an item that may interrupt one does: the line is
            # text. (A quote's paragraph is not the item's: after it, such
            # a line starts a list.)
            marker = None
            joined = True
        if marker and not depth and _list_type(marker) != list_type:
            break  # an item of another type starts another list
        if marker:
            content_indent = item_content_column(line)
            content = line[marker.end():].strip(" \t")
            # Content four or more columns past the item's content column is
            # indented code (CommonMark): no paragraph, no fence.
            first_code = indent_width(_expand_prefix(line)[content_indent:]) >= 4
            items.append(content)
            if not depth:
                own.append((end, content_indent))
            chain[depth:] = [content_indent]
            quote, table, previous = None, False, None
            offset = nested_offset(content)  # an item nested on its line: its content is further in
            # So is that of a bare item ending the line (``1. -``), on the next.
            rest = content[offset:]
            bare = not RULE_RE.match(rest) and (end_marker := LIST_ITEM_RE.match(rest)) and is_blank(rest[end_marker.end():])
            innermost = offset + (item_content_column(rest) if bare else 0)
        elif items and (
            # Into the content of an item still open: the list's (the item a
            # line is in is read from its lines, ``_parse_list``). Short of
            # it, only a lazy continuation line of an open paragraph.
            (chain and indent_width(line) >= chain[0])
            or ((quote.paragraph if quote is not None else open_paragraph) and (lazy_line or _is_lazy_line(line)))
        ):
            content = dedent(line, content_indent, expand=True) if indented else line.strip(" \t")
            if indent_width(content) < 4 and content.lstrip(" \t").startswith(">"):
                content = content.lstrip(" \t")  # a block quote, indented up to three columns
            if lazy_line and quote is None:
                # Kept four columns in, as if in the item's content: there too
                # it continues the paragraph, and starts no item.
                content = " " * 4 + content.strip(" \t")
            elif (
                quote is None and not (chain and indent_width(line) >= chain[0])
                and (
                    indent_width(line) >= 4 or _delimiter_row(line) is not None
                    or SETEXT_UNDERLINE_RE.match(line)
                )
            ):
                # A lazy line four or more columns in, a lazy delimiter row,
                # which makes no table (GFM), or a lazy setext underline,
                # which makes no heading (CommonMark), is text of the
                # paragraph: the item's content has it four columns into it,
                # where it starts no block. (A quote's lazy lines are its own.)
                text[end] = content_indent + innermost + 4
                content = " " * 4 + content.strip(" \t")
            if quote is not None and not content.startswith(">"):
                # A lazy line (unindented) always continues the quote here:
                # the list is lazy only while the quote's paragraph is open.
                # Whether the line is lazy is read in the innermost item it
                # is in, where its indentation may keep it from starting a block.
                within = dedent(line, chain[depth - 1], expand=True) if depth else line
                if lazy_line or quote.continues(within):
                    if not lazy_line and not depth and (
                        indent_width(line) >= 4 or _delimiter_row(line) is not None
                        or SETEXT_UNDERLINE_RE.match(line)
                    ):
                        # As a paragraph's lazy line above: four columns into
                        # the item's content, where it still continues the
                        # quote (``_read_quote``) and starts no block.
                        text[end] = content_indent + innermost + 4
                    content = "> " + content
                    quote_lazy = True
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
                items[-1] += " " + content.strip(" \t")
        else:
            break
        if content.startswith(">") and not (marker and first_code):
            quote = quote or _Quote()
            if quote_lazy:
                quote.lazy(content[2:])
            else:
                quote.read(content[1:].removeprefix(" "))
            lazy = quote.paragraph
        else:
            # An item's first line may open items nested on it (``- - ``````):
            # the fence is theirs.
            body = content[nested_offset(content):] if marker else content
            opening = None if marker and first_code else FENCE_OPEN_RE.match(body)
            fence = opening.group("fence") if opening else None
            # Opened in the item's content, not by a line short of it that
            # only this parser reads into the item (``indented``).
            fence_inside = marker is not None or indent_width(line) >= content_indent
            # So may raw HTML, which runs to its end or its item's (a lone
            # tag cannot interrupt the item's paragraph, CommonMark).
            opened = None if fence or (marker and first_code) else html_block_opening(
                body, in_paragraph=paragraph and not marker
            )
            if opened is not None and (opened[0] is None or not opened[0].search(body)):
                html, html_column = opened, content_indent + (offset if marker else 0)
            # An empty item has no text, nor one opening with code.
            lazy = fence is None and opened is None and bool(content.strip(" \t")) and not (marker and first_code)
        # Content indented four more columns where no paragraph is open is
        # indented code, and a table's rows are no paragraph: a lazy line
        # continues neither (CommonMark).
        code = indent_width(content) >= 4 + offset and not (paragraph or open_paragraph)
        table = (table and _continues_table(content)) or (
            previous is not None and not code and not previous_code
            # Short of an item nested on the first line, the line is lazy: a
            # lazy delimiter row makes no table (GFM).
            and indent_width(content) >= offset
            and _parse_table([previous, content], 0) is not None
        )
        # A setext underline in the content of the item whose paragraph is
        # open makes it a heading, which ends it (CommonMark).
        underline = (
            not marker and was_open and quote is None and not was_table and not para_quote
            and para_column <= indent_width(line) <= para_column + 3
            and SETEXT_UNDERLINE_RE.match(line.lstrip(" \t")) is not None
        )
        prose = lazy and quote is None and not code and not table and not underline
        # Not the item's own when the line continues the paragraph of an item
        # nested on its first line (``- 1. a`` and ``     b``): a sibling of
        # that nested item then starts a list, as after no text at all.
        paragraph = prose and (joined or _is_paragraph_text(content)) and (
            joined or marker is not None or was_paragraph or not was_open
        )
        # Open in the item or in an item nested on its line: a lazy line
        # continues either.
        open_paragraph = prose and (joined or _is_paragraph_text(content, nested=True))
        if underline:
            lazy = False
        elif open_paragraph and (marker or not was_open):
            # Opened in the innermost item the line is in: one nested on the
            # current item's first line when it reaches that one's content.
            if marker:
                para_column = content_indent + offset
            elif indent_width(line) >= content_indent + innermost:
                para_column = content_indent + innermost
            else:
                para_column = chain[depth - 1] if depth else content_indent
            # A quote there (``- - > a``) holds it: a line without its ``>``
            # only lazily continues it.
            para_quote = bool(marker) and content[nested_offset(content):].lstrip(" \t").startswith(">")
        # Only paragraph text can be a table's header row (GFM).
        previous, previous_code = (content, code) if open_paragraph else (None, False)
        end += 1
    # A following line is lazy only while a paragraph is open.
    return own, end, quote.paragraph if quote is not None else open_paragraph, text


# The markers and indentation that open a line inside list items and block
# quotes, whose tabs stand for the columns they reach.
_CONTAINER_PREFIX_RE = re.compile(r"(?:[ \t]*(?:(?:[-*+]|[0-9]{1,9}[.)])(?=[ \t]|$)|>))*[ \t]*")


def _expand_prefix(line: str) -> str:
    """*line* with the tabs in its leading markers and indentation written
    as the spaces they stand for, from the line's start (CommonMark tab
    stops of four): so a line taken out of an item measures its columns as
    it did in place."""
    if "\t" not in line:
        return line
    prefix = _CONTAINER_PREFIX_RE.match(line).group(0)
    return prefix.expandtabs(4) + line[len(prefix):] if "\t" in prefix else line


def _item_lines(lines: list[str], column: int, text: dict[int, int]) -> list[str]:
    """An item's content, one line per source line: *lines* are the item's,
    the first with its marker; *column* is the item's content column. Past
    the marker and each later line's indentation to *column*, a line is the
    item's as it stands; a line indented less, such as a lazy continuation
    line, loses its indentation — except one in *text* (indices into
    *lines*), which looks like an item's start but continues a paragraph: it
    is put at the column *text* gives, four past that paragraph's content,
    where it starts no item either."""
    content = []
    for k, line in enumerate(lines):
        expanded = _expand_prefix(line)
        if k == 0 or indent_width(expanded) >= column:
            content.append(expanded[column:] if len(expanded) > column else "")
        else:
            content.append(" " * max(text.get(k, 0) - column, 0) + expanded.lstrip(" \t"))
    return content


# How deep lists are read into items: past it, an item's content is one
# paragraph of text, so that no document recurses without bound.
MAX_LIST_DEPTH = 64
# How deep a block quote's content is read as blocks (``quote_content``):
# a quote in this many list items and quotes is read as one text. Each
# level reads the text of the quotes in it again.
MAX_QUOTE_DEPTH = 16


def _parse_list(
    lines: list[str], index: int, *, list_type: str, headings: bool = True, offset: int = 0, depth: int = 0,
) -> tuple[Block, int, bool, list[_ItemSpan]]:
    """Parse the list starting at ``lines[index]``: ``(block, end, lazy,
    items)`` — *end* and *lazy* as ``_scan_list`` gives them, *items* where
    each item lies. Each item's content is parsed as blocks of its own
    (``_parse_body``), nested lists included: no heading, and no lifting of
    a directive into block fields. *offset*: the source line ``lines[0]``
    is (for the item spans); *depth*: how many items the list is in, past
    ``MAX_LIST_DEPTH`` of which an item's content is one paragraph."""
    own, end, lazy, text = _scan_list(lines, index, list_type=list_type, headings=headings)
    items: list[ListItem] = []
    spans: list[_ItemSpan] = []
    starts = [first for first, _column in own] + [end]
    for (first, column), stop in zip(own, starts[1:], strict=True):
        layout = _Layout()
        content = _item_lines(lines[first:stop], column, {k - first: at for k, at in text.items() if first <= k < stop})
        if depth >= MAX_LIST_DEPTH:
            joined = paragraph_text([part for part in content if not is_blank(part)])
            blocks = [Block(kind="paragraph", text=joined)] if joined else []
            if blocks:
                layout.preamble.append(_BlockSpan("paragraph", offset + first, offset + stop))
        else:
            blocks, _sections = _parse_body(content, layout, headings=False, offset=offset + first, depth=depth + 1)
        items.append(ListItem(blocks=blocks))
        spans.append(_ItemSpan(offset + first, offset + stop, layout.preamble))
    kind = "ordered_list" if list_type in ".)" else "unordered_list"
    return Block(kind=kind, items=items), end, lazy, spans


def _split_row(line: str) -> list[str]:
    """The cells of the table row *line* (GFM): it is split at each ``|``
    not directly after a backslash, and a leading and a trailing ``|``
    delimit the row rather than a cell. A pipe after a backslash is cell
    text, and that one backslash is removed, whatever precedes it
    (cmark-gfm). A pipe inside a code span splits the row like any other
    (GFM), so it too is written ``\\|``."""
    row = line.strip(" \t")
    if row == "|":
        return []  # no cells (GFM)
    cells: list[str] = []
    cell: list[str] = []
    for pos, char in enumerate(row):
        if char != "|":
            cell.append(char)
        elif row[pos - 1:pos] == "\\":
            cell[-1] = "|"  # replaces the escaping backslash
        else:
            cells.append("".join(cell))
            cell = []
    cells.append("".join(cell))
    if row.startswith("|"):
        cells.pop(0)
    if len(cells) > 1 and row.endswith("|") and not row.endswith("\\|"):
        cells.pop()
    return [cell.strip(" \t") for cell in cells]


_ALIGNMENTS = {(True, False): "left", (False, True): "right", (True, True): "center", (False, False): ""}


def _row_width_end(line: str, width: int) -> int:
    """Where the first *width* cells of table row *line* end (``_split_row``):
    at the pipe after them, or the line's end. GFM drops the cells past the
    header's width."""
    pos = len(line) - len(line.lstrip(" \t"))
    pos += line.startswith("|", pos)
    count = 0
    for at in range(pos, len(line)):
        if line[at] == "|" and line[at - 1:at] != "\\":
            count += 1
            if count == width:
                return at
    return len(line)


def _delimiter_row(line: str) -> list[str] | None:
    """The cells of *line* when it is a table's delimiter row (GFM): indented
    at most three columns, holding a pipe (``---`` alone is a setext
    underline or a rule), no list item (``- | -`` is one), and every cell
    ``:?-+:?``."""
    if indent_width(line) > 3 or "|" not in line or LIST_ITEM_RE.match(line):
        return None
    cells = _split_row(line)
    return cells if cells and all(_DELIMITER_CELL_RE.fullmatch(cell) for cell in cells) else None


def _continues_table(line: str) -> bool:
    """True if *line* is a body row of the table above it (GFM): not blank,
    indented at most three columns, starting no other block — a quote, a
    heading, a fence, raw HTML, a thematic break, a list item — and not a
    lone ``|``. Its pipes are optional: a line without one is a row of one
    cell."""
    return (
        not is_blank(line)
        and indent_width(line) <= 3
        and not FENCE_OPEN_RE.match(line)
        and not HEADING_RE.match(line)
        and not line.lstrip(" \t").startswith(">")
        and html_block_opening(line) is None
        and not RULE_RE.match(line)
        and not LIST_ITEM_RE.match(line)
        and bool(_split_row(line))
    )


def _parse_table(lines: list[str], index: int) -> tuple[Block, int] | None:
    """Parse the table starting at ``lines[index]``; return it with the index
    just past it, or None when the lines there are not a table.

    A table is a header row, then a delimiter row with as many cells
    (``_delimiter_row``), then its body rows, up to a blank line or another
    block's start (``_continues_table``); a row's outer pipes are optional
    (GFM). The caller makes sure the header is a paragraph line, no other
    block's start — as paragraph text, it may be indented any amount. A body row is padded with
    empty cells or cut to the header's width, as GFM renders it. Each
    column's alignment comes from its delimiter cell.
    """
    if index + 1 >= len(lines) or is_blank(lines[index]):
        return None
    delimiters = _delimiter_row(lines[index + 1])
    headers = _split_row(lines[index])
    if delimiters is None or not headers or len(delimiters) != len(headers):
        return None
    align = [_ALIGNMENTS[cell.startswith(":"), cell.endswith(":")] for cell in delimiters]
    rows: list[list[str]] = []
    end = index + 2
    while end < len(lines) and _continues_table(lines[end]):
        cells = _split_row(lines[end])[: len(headers)]
        rows.append(cells + [""] * (len(headers) - len(cells)))
        end += 1
    return Block(kind="table", headers=headers, rows=rows, align=align), end


def _parse_paragraph(paragraph: str) -> Block:
    stripped = strip_text(paragraph)
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
            text=strip_text(stripped[anchor.directive.end:]),
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
        and not directive.curly_quoted()
    )


def _first_liftable(directives: list[Directive], name: str) -> Directive | None:
    """The first *name* directive the ref/term block fields hold without loss:
    well-formed, with a target and only the parameters it defines. One whose
    arguments the validator warns about stays paragraph text, where it is
    checked."""
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
            and not directive.curly_quoted()
        ):
            return directive
    return None


def _starts_interrupting_item(line: str) -> bool:
    """True if *line* is a list item that may interrupt a paragraph
    (CommonMark): indented at most three columns, not empty, and, if
    ordered, numbered 1. A thematic break is not one."""
    marker = LIST_ITEM_RE.match(line)
    return (
        marker is not None
        and not RULE_RE.match(line)
        and indent_width(line) <= 3
        and _may_interrupt(line, marker)
    )


def _is_paragraph_text(content: str, *, nested: bool = False) -> bool:
    """True if *content*, a list item's text or one of its lines, is
    paragraph text rather than a block of its own: a heading, a thematic
    break, a nested list item, a fence, or an HTML block. *nested*: a nested
    item's text counts too (`1. item` opens that item's paragraph)."""
    while nested and not RULE_RE.match(content) and (marker := LIST_ITEM_RE.match(content)):
        content = content[marker.end():]
    return bool(content.strip(" \t")) and not (
        ATX_HEADING_RE.match(content) or RULE_RE.match(content) or LIST_ITEM_RE.match(content)
        or FENCE_OPEN_RE.match(content) or HTML_BLOCK_START_RE.match(content)
    )


def _may_interrupt(line: str, marker: re.Match[str]) -> bool:
    """True if the list item *marker* opens on *line* may interrupt a
    paragraph (CommonMark): it is not empty and, if ordered, its number is
    1 — ``01.`` included."""
    return bool(line[marker.end():].strip(" \t")) and (
        marker.group("number") is None or int(marker.group("number")) == 1
    )


def _paragraph_end(lines: list[str], index: int, lazy: bool, *, headings: bool = True) -> tuple[int, int]:
    """Return ``(end, setext_level)`` for the paragraph starting at
    ``lines[index]``. *setext_level* is 1 or 2 when the paragraph is the text
    of a setext heading, whose underline is ``lines[end - 1]``, else 0.

    A paragraph ends at a blank line or at a block that can interrupt it
    (CommonMark): a fence, an ATX heading, a block quote, an HTML block of
    kinds 1–6, a table (GFM), a thematic break
    other than a setext underline, or a list item (an ordered one only when
    numbered 1), none of them indented four or more columns. A *lazy* paragraph continues a
    list, block quote, or table (no blank line between), so it cannot be
    setext text: ``---`` under it is a rule. Without *headings* (a list
    item's or a quote's content), an ATX heading may be empty (``#``).
    """
    atx = HEADING_RE if headings else ATX_HEADING_RE
    end = index + 1
    while end < len(lines) and not is_blank(lines[end]):
        line = lines[end]
        if (
            FENCE_OPEN_RE.match(line)
            or atx.match(line)
            or (line.lstrip(" \t").startswith(">") and indent_width(line) <= 3)
            or HTML_BLOCK_START_RE.match(line)
            or _starts_interrupting_item(line)
            # The delimiter row decides: the header row is paragraph text —
            # but for a setext underline, which ends the paragraph as a
            # heading, and a line that is a delimiter row itself (cmark-gfm).
            or (
                not SETEXT_UNDERLINE_RE.match(line) and _delimiter_row(line) is None
                and _parse_table(lines, end) is not None
            )
            or (RULE_RE.match(line) and not SETEXT_UNDERLINE_RE.match(line) and indent_width(line) <= 3)
        ):
            break
        underline = SETEXT_UNDERLINE_RE.match(line)
        if underline and not lazy:
            return end + 1, 1 if underline.group(1)[0] == "=" else 2
        if underline and RULE_RE.match(line):
            break
        end += 1
    return end, 0


# An ATX heading's closing sequence (CommonMark): ``#`` characters ending
# the line, after a space or tab or as all of its text.
_CLOSING_SEQUENCE_RE = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")


def _atx_text(text: str) -> str:
    """An ATX heading's text as CommonMark reads it, in a list item or a
    quote: without the spaces around it and without a closing sequence."""
    return _CLOSING_SEQUENCE_RE.sub("", text.strip(" \t")).strip(" \t")


_Heading = tuple[str, Marker, int]  # title, marker, level


@dataclass(slots=True)
class _HeadingSpan:
    """Where a heading lies in the body: lines ``[start, end)``, and the
    line its marker is on (an ATX line, or a setext heading's last text
    line)."""

    start: int
    end: int
    level: int
    marker_line: int


@dataclass(slots=True)
class _BlockSpan:
    """Where a block lies in the body: lines ``[start, end)``. *kind* is the
    parsed block's. *items*: a list's items (``_ItemSpan``)."""

    kind: str
    start: int
    end: int
    items: list[_ItemSpan] = field(default_factory=list)


@dataclass(slots=True)
class _ItemSpan:
    """Where a list item lies: lines ``[start, end)``, its marker's line
    first, and where each block of its content lies."""

    start: int
    end: int
    blocks: list[_BlockSpan] = field(default_factory=list)


@dataclass(slots=True)
class _Layout:
    """Where the parser found each heading and block of a body
    (``_parse_body``), for tools that edit the source as written, such as
    assembly (§15.7): the same walk that builds the model records it."""

    preamble: list[_BlockSpan] = field(default_factory=list)
    sections: list[tuple[_HeadingSpan, list[_BlockSpan]]] = field(default_factory=list)

    def containers(self) -> list[list[_BlockSpan]]:
        """The preamble's blocks, then each section's."""
        return [self.preamble, *(blocks for _heading, blocks in self.sections)]

    @property
    def headings(self) -> list[_HeadingSpan]:
        return [heading for heading, _blocks in self.sections]


def _layout(lines: list[str]) -> _Layout:
    """Where each heading and block of body *lines* lies."""
    layout = _Layout()
    _parse_body(lines, layout)
    return layout


def _parse_body(
    lines: list[str], layout: _Layout | None = None, *, headings: bool = True, offset: int = 0, depth: int = 0
) -> tuple[list[Block], list[tuple[_Heading, list[Block]]]]:
    """Parse body lines into the preamble's blocks (§4.4) and the sections'.

    Headings (ATX and setext, §4.1) and blocks are recognized in one pass, so
    a fenced code block (§11.4) or an HTML block (§8.6) is literal
    everywhere: no line inside one is a heading or starts another block.
    *layout*, when given, receives where each heading and block lies, as
    source lines counted from *offset*. *depth*: how many list items the
    lines are in (``_parse_list``).

    Without *headings*, the lines are a list item's or a block quote's
    content (``_parse_list``, ``quote_content``): a heading there, ATX or
    setext, is a ``heading`` block, not a section, and a paragraph's
    directives stay in its text.
    """
    spans = layout.preamble if layout is not None else None
    preamble: list[Block] = []
    sections: list[tuple[_Heading, list[Block]]] = []
    blocks = preamble
    lazy = False  # the last block was a list, quote, or table, with no blank line since
    interrupted = -1  # the line at which a paragraph was interrupted
    index = 0
    while index < len(lines):
        line = lines[index]
        if is_blank(line):
            index += 1
            lazy = False
            continue
        heading: _Heading | None = None
        start, count, item_spans = index, len(blocks), []
        # A table that interrupted a paragraph may have an indented header
        # row: the paragraph ended there (_paragraph_end).
        interrupting_table = index == interrupted and _parse_table(lines, index) is not None
        if indent_width(line) >= 4 and not interrupting_table:
            # Indented code (§11.4). A paragraph's own lines, and a list's
            # or quote's lazy lines, never get here.
            end = indented_code_end(lines, index)
            blocks.append(Block(kind="code", text="\n".join(lines[index:end])))
            index = end
            lazy = False
            if spans is not None:
                spans.append(_BlockSpan("code", offset + start, offset + index))
            continue
        opening = FENCE_OPEN_RE.match(line)
        atx = HEADING_RE.match(line) if headings else None
        marker = LIST_ITEM_RE.match(line)
        if interrupting_table and indent_width(line) >= 4:
            # The header row, four columns in or more, where no other block
            # starts (a ``>`` there is no quote marker).
            block, index = _parse_table(lines, index)
            blocks.append(block)
            lazy = True
        elif opening:
            end = fence_end(lines, index, opening.group("fence"))
            blocks.append(Block(kind="code", text="\n".join(lines[index:end])))
            index = end
            lazy = False
        elif atx:
            hashes, text = atx.groups()
            heading = (*split_heading(text), len(hashes))
            index += 1
        elif not headings and (inner := ATX_HEADING_RE.match(line)):
            # In an item or a quote, a heading is no section (§4.1): a block
            # of its own, which nothing continues (CommonMark).
            hashes, text = inner.groups()
            blocks.append(Block(kind="heading", text=_atx_text(text or ""), level=len(hashes)))
            index += 1
            lazy = False
        elif RULE_RE.match(line):
            blocks.append(Block(kind="rule"))
            index += 1
            lazy = False
        elif line.lstrip(" \t").startswith(">"):
            end, quoted, open_paragraph = _read_quote(lines, index)
            blocks.append(Block(kind="quote", text="\n".join(quoted)))
            index = end
            # The quote took every line it could continue; a paragraph after
            # it is lazy only if the quote's is still open.
            lazy = open_paragraph
        elif marker:
            block, index, lazy, item_spans = _parse_list(
                lines, index, list_type=_list_type(marker), headings=headings, offset=offset, depth=depth
            )
            blocks.append(block)
        elif (end := html_block_end(lines, index)) is not None:
            # Raw HTML, a comment included, is not rendered (§8.6, §8.7):
            # no heading or other block starts inside it.
            blocks.append(Block(kind="html", text="\n".join(lines[index:end])))
            index = end
            lazy = False
        elif table := _parse_table(lines, index):
            # A paragraph line with a delimiter row under it (GFM).
            block, index = table
            blocks.append(block)
            lazy = True
        else:
            end, setext_level = _paragraph_end(lines, index, lazy, headings=headings)
            if setext_level:
                text = " ".join(part.strip(" \t") for part in lines[index:end - 1])
                if headings:
                    heading = (*split_heading(text), setext_level)
                else:
                    blocks.append(Block(kind="heading", text=text, level=setext_level))
            else:
                # Its lines, line breaks kept (hard ones among them).
                text = paragraph_text(lines[index:end])
                blocks.append(_parse_paragraph(text) if headings else Block(kind="paragraph", text=text))
                interrupted = end
            index = end
            lazy = False
        if layout is not None and spans is not None:
            if heading is not None:
                marker_line = start if atx else index - 2  # a setext heading's last text line
                spans = []
                layout.sections.append((_HeadingSpan(start, index, heading[2], marker_line), spans))
            elif len(blocks) > count:
                spans.append(_BlockSpan(blocks[-1].kind, offset + start, offset + index, item_spans))
        if heading is not None:
            blocks = []
            sections.append((heading, blocks))
            lazy = False
    return preamble, sections


# ── Public API ────────────────────────────────────────────────────

@lru_cache(maxsize=512)
def quote_content(text: str, depth: int = 0) -> tuple[tuple[Block, ...], tuple[_BlockSpan, ...]]:
    """The blocks a block quote holds whose content is *text* — a quote
    block's text, one line per source line (``_read_quote``) — each with
    where it lies: lines counted from the quote's first. As in a list item,
    a heading is a ``heading`` block, not a section, and no directive is
    lifted into block fields. *depth*:
    how many list items and quotes the content is in (``_parse_list``).
    Cached, as the validator reads a quote's blocks more than once: the
    blocks are shared, not to be changed."""
    layout = _Layout()
    blocks, _sections = _parse_body(text.split("\n"), layout, headings=False, depth=depth)
    return tuple(blocks), tuple(layout.preamble)


def quote_blocks(block: Block, *, depth: int = 0) -> list[Block]:
    """The blocks the block quote *block* holds, as the validator reads them
    (``quote_content``): each a fresh copy, free to change, since the
    validator keeps its own reading of a quote's content. A heading in one
    is a ``heading`` block, not a section, and no directive is lifted into
    block fields (§4.1). *depth*: how many list items and quotes *block* is
    in, the count before entering it; a quote in ``MAX_QUOTE_DEPTH`` of them
    is not read into blocks: it is one paragraph of its text as written
    (the validator reads no directives in its fenced code there), or no
    block when it holds none. Raises ``ValueError`` for a block that is not a quote."""
    if block.kind != "quote":
        raise ValueError(f"not a block quote: {block.kind}")
    if depth >= MAX_QUOTE_DEPTH:
        return [Block(kind="paragraph", text=block.text)] if block.text.strip() else []
    return copy.deepcopy(list(quote_content(block.text, depth + 1)[0]))


def parse_item_content(text: str) -> list[Block]:
    """The blocks a list item holds whose content is *text*, as written in
    the item without its marker and indentation (``_parse_list``)."""
    lines = LINE_ENDING_RE.sub("\n", text).split("\n")
    blocks, _sections = _parse_body(lines, headings=False)
    return blocks


def load(path: str | os.PathLike[str]) -> Document:
    """Open the LegalDown file at *path* as a Document.

    The one call for a document that lives in a file: it reads the file as
    UTF-8 and parses it as ``parse`` does, naming it in the result:
    ``Document.filename`` is the file's name (without its directory) and
    ``Document.path`` its absolute path, so a caller resolving files the document refers to
    (``amends``, includes, attachments) knows where to look. Symbolic links
    are not followed: the document is where it was named.

    Raises ``FileNotFoundError`` (or another ``OSError``) when the file cannot
    be read, ``UnicodeDecodeError`` when it is not UTF-8, and
    ``FrontmatterError`` when its frontmatter cannot be read.
    """
    # The path as given is what is read: ``..`` after a symbolic link leads
    # where the operating system takes it, which tidying the text of the path
    # first could not tell. ``absolute`` makes it absolute and nothing more.
    file = Path(path)
    # Bytes, decoded once: a byte-order mark and the line endings (LF, CR and
    # CRLF alike) are the parser's to read, so the text layer's own
    # translation would only be a second, redundant pass over the file.
    document = parse(file.read_bytes().decode("utf-8"), filename=file.name)
    document.path = file.absolute()
    return document


def parse(source: str, *, filename: str = "") -> Document:
    """Parse a LegalDown source string into a Document object.

    For a file, ``load`` reads and parses it in one call. ``filename`` only
    names the document in its diagnostics.

    The parser is deliberately faithful to the source: nothing is rewritten to
    make a document valid, so the validator reports what the document actually
    says (a bare ``unit=M`` surfaces as duration-invalid-unit rather than being
    silently corrected).
    """
    # A byte-order mark is an encoding artifact, not content. Lines end at
    # LF, CR, or CRLF (CommonMark), and nowhere else.
    source = LINE_ENDING_RE.sub("\n", (source or "").removeprefix("\ufeff"))
    not_line_editable, metadata, body, absent, keys = _split_frontmatter(source)
    lines = body.split("\n")
    if lines[-1] == "":
        lines.pop()  # the last line's ending, not a line
    layout = _Layout()
    preamble, sections = _parse_body(lines, layout)
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
    document.metadata.frontmatter_absent = absent
    document.source_map = SourceMap(
        lines=source.split("\n"),
        body_start=source.count("\n", 0, len(source) - len(body)),
        keys=keys,
        layout=layout,
        shape=document_shape(document),
    )
    return document


def parse_document(source: str, *, filename: str = "") -> Document:
    """Deprecated since 0.4.0, removed in 0.5.0. Same as ``parse``, with the same
    arguments; ``load`` is the call for a file."""
    warnings.warn(
        "legaldown.parse_document() is deprecated since 0.4.0 and will be removed in 0.5.0; "
        "use legaldown.parse() for a string, or legaldown.load() for a file",
        DeprecationWarning,
        stacklevel=2,
    )
    return parse(source, filename=filename)


class DirectiveLocation(NamedTuple):
    """A directive and where it is (``iter_document_directives``): in block
    *block* of the preamble (*section* None) or of section *section*, in
    fragment *fragment* of ``block_fragments(block)``, which *directive*'s
    offsets are into. *fragment* is None for the ``{{ref:}}``, ``{{term:}}``
    or ``{{def:}}`` the parser lifted into the block's own fields: its
    offsets are into the directive as the serializer writes it alone, and a
    directive of a block built in code whose value holds a line break, which
    no source can write, has none (offsets 0, no spans, empty source)."""

    section: int | None
    block: int
    fragment: int | None
    directive: Directive


def iter_document_directives(document: Document) -> Iterator[DirectiveLocation]:
    """Every directive in the body of *document*, well-formed or malformed, in
    document order, each with where it is (``DirectiveLocation``).

    It reads the body as ``validate`` does — the same fragments
    (``block_fragments``), lexed the same way — so code spans, comments, code
    blocks and raw HTML hold none (§11.4), and a block quote's or a list's are
    those of the blocks it holds. A ``{{ref:}}`` or ``{{term:}}`` the parser
    lifted into a block's fields is among them, between the directives of the
    block's text before it and after it, and the ``{{def:}}`` it lifted into
    a definition block's fields, before those of the definition's text. The
    frontmatter and the headings are not read.
    """
    for section, index, block in document.iter_indexed_blocks():
        if block.kind == "definition":
            yield from _lifted_directive(section, index, block)
        lifted = block.kind in ("ref", "term") and bool(block.target)
        # The fragments before the lifted directive: its text, and its prefix.
        before = bool(block.text) + bool(block.prefix)
        for fragment, (text, _anchor) in enumerate(block_fragments(block)):
            if lifted and fragment == before:
                yield from _lifted_directive(section, index, block)
                lifted = False
            for directive in lex(text).directives:
                yield DirectiveLocation(section, index, fragment, directive)
        if lifted:
            yield from _lifted_directive(section, index, block)


def _lifted_directive(section: int | None, index: int, block: Block) -> Iterator[DirectiveLocation]:
    """The directive a ``ref``, ``term`` or ``definition`` block holds in its
    fields, as the serializer writes it alone; built directly, with no
    offsets, when the serializer refuses its value (a line break)."""
    from .serializer import render_block  # the serializer builds on this module

    name = "def" if block.kind == "definition" else block.kind
    try:
        source = render_block(
            Block(
                kind=block.kind,
                target=block.target,
                label=block.label,
                definition_id=block.definition_id,
                term=block.term,
            )
        )
    except ValueError:
        source = ""
    # The last: a term written before a ``{{def:}}`` may hold directives itself.
    found = [directive for directive in lex(source).directives if directive.name == name]
    if found:
        directive = found[-1]
    else:
        positional = block.definition_id if block.kind == "definition" else block.target
        params = {"label": block.label} if block.kind == "term" and block.label else {}
        directive = Directive(
            name=name, positional=positional or None, params=params, duplicates=(), malformed="",
            start=0, end=0, source="",
        )
    yield DirectiveLocation(section, index, None, directive)


def collect_source_directives(document: Document) -> tuple[set[str], set[str]]:
    """Collect all ref and term targets used in a document.

    Returns ``(ref_targets, term_targets)``.
    """
    refs: set[str] = set()
    terms: set[str] = set()
    for _section, _index, _fragment, directive in iter_document_directives(document):
        if directive.malformed or not directive.positional:
            continue
        if directive.name == "ref":
            refs.add(directive.positional)
        elif directive.name == "term":
            terms.add(directive.positional)
    return refs, terms
