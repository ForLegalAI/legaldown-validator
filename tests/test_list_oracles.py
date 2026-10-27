"""The list scan's linear-time helpers read as the code they replaced did (#94).

Each oracle below is the earlier implementation, copied; the helper is checked
against it on random strings made of the characters that matter (markers,
blanks, line endings), which meet in every order.
"""
from __future__ import annotations

import random
import re

from legaldown.markdown import (
    _ITEM_START_RE,
    FENCE_OPEN_RE,
    HTML_BLOCK_START_RE,
    indent_width,
    is_blank,
    item_content_column,
    item_marker_at,
    nested_content,
    nested_offset,
)
from legaldown.parser import (
    _QUOTE_MARKERS_RE,
    ATX_HEADING_RE,
    LIST_ITEM_RE,
    RULE_RE,
    _atx_text,
    _expand_prefix,
    _item_body,
    _opens_paragraph,
    _rule_from,
    _skip_markers,
)

_SEED = 94
_NESTED_ITEM_RE = re.compile(r"(?:[0-9]{1,9}[.)]|[-*+])[ \t]+(?=\S)")

# ``RULE_RE`` and ``LIST_ITEM_RE`` before their runs of blanks were possessive.
_OLD_RULE_RE = re.compile(r"^[ \t]*(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_OLD_ITEM_RE = re.compile(r"^[ \t]*(?:(?P<number>[0-9]{1,9})(?P<delimiter>[.)])|(?P<bullet>[-*+]))(?:[ \t]+|$)")
# An ATX heading's closing sequence, as ``_atx_text`` found it.
_OLD_CLOSING_SEQUENCE_RE = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")


def _old_item_content_column(line: str) -> int:
    marker = _ITEM_START_RE.match(line)
    if marker is None:
        return indent_width(line) + 2
    after = len(line[:marker.end()].expandtabs(4))
    rest = line[marker.end():]
    if is_blank(rest):
        return after + 1
    spacing = len(line[:len(line) - len(rest.lstrip(" \t"))].expandtabs(4)) - after
    return after + 1 if spacing > 4 else after + spacing


def _old_nested_offset(first: str) -> int:
    offset = 0
    while marker := _NESTED_ITEM_RE.match(first):
        offset += _old_item_content_column(first)
        first = first[marker.end():]
    return offset


def _old_after_item_markers(content: str) -> str:
    while not _OLD_RULE_RE.match(content) and (marker := _OLD_ITEM_RE.match(content)):
        content = content[marker.end():]
    return content


def _old_opens_paragraph(content: str) -> bool:
    inner = content
    while True:
        inner = inner[_QUOTE_MARKERS_RE.match(inner).end():]
        marker = None if _OLD_RULE_RE.match(inner) else _OLD_ITEM_RE.match(inner)
        if not marker:
            break
        inner = inner[marker.end():]
    return (
        bool(inner.strip(" \t"))
        and not (FENCE_OPEN_RE.match(inner) and indent_width(content) < 4)
        and not ATX_HEADING_RE.match(inner)
        and not _OLD_RULE_RE.match(inner)
        and not HTML_BLOCK_START_RE.match(inner)
    )


def _old_item_body(content: str) -> tuple[str, int]:
    body, column = content, 0
    while indent_width(body) < 4 and not _OLD_RULE_RE.match(body) and (item := _OLD_ITEM_RE.match(body)):
        if not body[item.end():].strip(" \t"):
            break
        columns = _old_item_content_column(body)
        body, column = body[columns:], column + columns
    return body, column


def _markers(rng: random.Random) -> str:
    """A string of marker characters and blanks: of all kinds, or, two times
    in three, of the few that make thematic breaks and the items they look
    like. A line ending may come anywhere."""
    alphabet = rng.choice(["-*_+1.) \t\ta0#>\n", "-*_ \t\n", "-- \t\n", "> -\t1.", ">- *\t\t`<#a"])
    return "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 16)))


def test_item_content_column_is_what_it_was() -> None:
    rng = random.Random(_SEED)
    for _ in range(30000):
        line = _markers(rng)
        assert item_content_column(line) == _old_item_content_column(line), repr(line)


def test_nested_offset_is_what_it_was() -> None:
    """Without tabs it is the earlier function's, in columns and in characters.
    With them, what the earlier function made of the line with its markers' tabs
    written as the spaces they stand for, from the line's start."""
    rng = random.Random(_SEED)
    for _ in range(30000):
        line = _markers(rng) + rng.choice(["", "a", " a"])
        columns, characters = nested_content(line)
        assert columns == nested_offset(line) == _old_nested_offset(_expand_prefix(line)), repr(line)
        if "\t" not in line:
            assert characters == columns, repr(line)


def test_item_markers_are_stripped_until_a_thematic_break_as_before() -> None:
    rng = random.Random(_SEED)
    for _ in range(60000):
        content = _markers(rng)
        assert content[_skip_markers(content):] == _old_after_item_markers(content), repr(content)


def test_a_quoted_line_opens_paragraph_text_as_before() -> None:
    rng = random.Random(_SEED)
    for _ in range(60000):
        content = _markers(rng)
        assert _opens_paragraph(content) == _old_opens_paragraph(content), repr(content)


def test_item_markers_in_a_quoted_line_are_read_as_before() -> None:
    """Without tabs, as the earlier loop read them. With them, as it read the
    line with its markers' tabs written as the spaces they stand for: the same
    columns, and the same text after them where it has no tab."""
    rng = random.Random(_SEED)
    for _ in range(60000):
        content = _markers(rng)
        body, columns = _item_body(content)
        old_body, old_columns = _old_item_body(_expand_prefix(content))
        assert columns == old_columns, repr(content)
        if "\t" not in body + old_body:
            assert body == old_body, repr(content)
        if "\t" not in content:
            assert (body, columns) == _old_item_body(content), repr(content)


def test_item_marker_at_agrees_with_list_item_re() -> None:
    rng = random.Random(_SEED)
    for _ in range(60000):
        text = _markers(rng)
        position = rng.randrange(0, len(text) + 1)
        found, expected = item_marker_at(text, position), LIST_ITEM_RE.match(text[position:])
        assert (found is None) == (expected is None), (text, position)
        if found is not None and expected is not None:
            assert found.end - position == expected.end(), (text, position)


def test_the_rule_test_from_the_end_agrees_with_rule_re_at_every_position() -> None:
    """``_rule_from`` stands in for ``RULE_RE.match(content[position:])``."""
    rng = random.Random(_SEED)
    for _ in range(30000):
        content = _markers(rng)
        is_rule = _rule_from(content)
        for position in range(len(content) + 2):
            assert is_rule(position) == bool(RULE_RE.match(content[position:])), (content, position)


def test_possessive_runs_of_blanks_match_as_the_backtracking_ones_did() -> None:
    rng = random.Random(_SEED)
    for _ in range(60000):
        line = _markers(rng)
        assert bool(RULE_RE.match(line)) == bool(_OLD_RULE_RE.match(line)), repr(line)
        new, old = LIST_ITEM_RE.match(line), _OLD_ITEM_RE.match(line)
        assert (new is None) == (old is None), repr(line)
        if new is not None and old is not None:
            assert new.span() == old.span()
            assert new.groupdict() == old.groupdict()


def test_atx_text_reads_the_closing_sequence_as_its_expression_did() -> None:
    rng = random.Random(_SEED)
    for _ in range(30000):
        text = "".join(rng.choice("#  \ta\\") for _ in range(rng.randrange(0, 12)))
        assert _atx_text(text) == _OLD_CLOSING_SEQUENCE_RE.sub("", text.strip(" \t")).strip(" \t"), repr(text)
