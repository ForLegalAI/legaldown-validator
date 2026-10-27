"""Items nested on one line (``- 1. a``): what each holds, and where it ends
(#94 review).

Each document below reads as CommonMark has it: the shapes were checked with
markdown-it and with cmark (``pandoc -f commonmark``).

- An item whose marker has more than four columns of spacing holds indented
  code: no item nests in it, and no paragraph is open in it, so a line after it
  is not its lazy continuation.
- A fence in an item nested on the line ends at a line short of that item's
  content, as HTML does, and not at one short of the first item's.
"""
from __future__ import annotations

import pytest

from legaldown import parse
from legaldown.markdown import item_marker_at, nested_content
from legaldown.parser import quote_blocks

P, C = ("p",), ("c",)


def _shape(blocks: list) -> list:
    out: list = []
    for block in blocks:
        if block.kind in ("unordered_list", "ordered_list"):
            out.append((block.kind[:2], [_shape(item.blocks) for item in block.items]))
        elif block.kind == "quote":
            out.append(("q", _shape(quote_blocks(block))))
        else:
            out.append({"paragraph": P, "code": C}.get(block.kind, (block.kind,)))
    return out


def _read(source: str) -> list:
    return _shape(parse(source).preamble)


@pytest.mark.parametrize(
    ("source", "shape"),
    [
        pytest.param("- -     a\nx\n", [("un", [[("un", [[C]])]]), P], id="code-in-a-nested-item"),
        pytest.param("+  *     a\n  x\n", [("un", [[("un", [[C]])]]), P], id="the-same-with-spacing"),
        pytest.param("-  -     > q\nx\n", [("un", [[("un", [[C]])]]), P], id="a-quote-mark-is-code-there"),
        pytest.param("1. * 2)     a\nx\n", [("or", [[("un", [[("or", [[C]])]])]]), P], id="three-deep"),
        pytest.param("- 1.      - x\n       y\nb\n", [("un", [[("or", [[C, P]])]])], id="its-paragraph-after-the-code"),
        pytest.param("- 1. ```\n    > a\nb\n", [("un", [[("or", [[C]]), ("q", [P])]])], id="fence-ends-short-of-nested"),
        pytest.param("- 1. ```\n   > a\nb\n", [("un", [[("or", [[C]]), ("q", [P])]])], id="fence-ends-three-columns-in"),
        pytest.param("- 1. ```\n     > a\nb\n", [("un", [[("or", [[C]])]]), P], id="fence-goes-on-at-its-content"),
        pytest.param("- 1.\t```\n\t> a\nb\n", [("un", [[("or", [[C]]), ("q", [P])]])], id="fence-ends-short-of-a-tab"),
        pytest.param(
            "-1.\t1. \n- 1.\t```- \n\t> a \na1.\t- \n",
            [P, ("un", [[("or", [[C]]), ("q", [P])]])],
            id="the-fuzz-document",
        ),
    ],
)
def test_nested_items_hold_what_commonmark_has_them_hold(source: str, shape: list) -> None:
    assert _read(source) == shape


def test_nested_content_stops_at_a_marker_with_more_than_four_columns_of_spacing() -> None:
    assert nested_content("1.      - x") == (3, 3)  # the item is there; what follows is its code
    assert nested_content("- 1.      - x") == (5, 5)
    assert nested_content("- 1.    - x") == (10, 10)  # four columns of spacing: the next item nests
    assert nested_content("-\t- x") == (6, 4)  # the tab is three columns, one character


def test_an_item_marker_before_a_line_ending_is_empty() -> None:
    for text in ("-", "- ", "-\n", "- \n", "1.\n"):
        found = item_marker_at(text)
        assert found is not None and found.blank, text
    assert item_marker_at("-\nx") is None  # a marker is followed by a blank, or ends the text
