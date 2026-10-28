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
from legaldown.markdown import ItemMarkers, item_marker_at, nested_content
from legaldown.parser import _Rule, _Walks, quote_blocks

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
            "- 1. ```\n      code\n      ```\n\n     para\nlazy\n",
            [("un", [[("or", [[C, P]])]])],
            id="fence-closed-in-the-nested-item",
        ),
        pytest.param("- 1. ```\n   code\n  ```\nb\n", [("un", [[("or", [[C]]), P, C]]), P], id="closer-short-of-the-nested-item"),
        pytest.param("- 10.  ```\n      code\na\n", [("un", [[("or", [[C]]), C]]), P], id="code-in-the-item-after-the-nested-fence"),
        pytest.param("- 1.      -\n     a\n  ===\nb\n", [("un", [[("or", [[C, P]])]])], id="a-dash-in-code-is-no-item"),
        pytest.param("- -\t> <v>\n**\n", [("un", [[("un", [[("q", [("html",)])]])]]), P], id="a-quote-nested-on-the-line"),
        pytest.param("1.\t2)\t>~~~\n->\n", [("or", [[("or", [[("q", [C])]])]]), P], id="a-quote-of-a-fence-nested-on-the-line"),
        pytest.param(">q\n>2) 1)     e\nc\n", [("q", [P])], id="lazy-text-in-a-quote-is-no-code"),
        pytest.param(">q\n>     +      q\na\n", [("q", [P])], id="indented-lazy-text-in-a-quote-is-no-code"),
        pytest.param(
            "> - >```\n>      >a\nd\n",
            [("q", [("un", [[("q", [C])]])]), P],
            id="a-fence-in-a-quote-in-an-item-in-a-quote-holds-its-quoted-lines",
        ),
        pytest.param(
            ">1.\n>    >a\n>    0.\n->\n",
            [("q", [("or", [[("q", [P]), ("or", [[]])]])]), P],
            id="an-item-starts-after-a-quote-in-an-item",
        ),
        pytest.param(
            ">-\t1.\n>    >q\n>    0.\n--\n",
            [("q", [("un", [[("or", [[]]), ("q", [P]), ("or", [[]])]])]), P],
            id="a-quote-in-an-item-short-of-the-empty-item-nested-on-its-line",
        ),
        pytest.param(
            "- -\n      2. >\nc\n", [("un", [[("un", [[("or", [[("q", [])]])]])]]), P],
            id="a-bare-item-ends-the-line-and-a-marker-reaches-its-content",
        ),
        pytest.param("* *\n      2. >\nc", [("un", [[("un", [[("or", [[("q", [])]])]])]]), P], id="the-same-with-stars"),
        pytest.param("- -\n      -     c\n=", [("un", [[("un", [[("un", [[C]])]])]]), P], id="code-in-the-item-after-a-bare-one"),
        pytest.param("- *\n      - <y>\n=", [("un", [[("un", [[("un", [[("html",)]])]])]]), P], id="html-in-the-item-after-a-bare-one"),
        pytest.param(
            "+ 2)\n      1. a\n      2.\n=", [("un", [[("or", [[("or", [[P], []])]])]]), P], id="an-item-after-a-bare-one-has-siblings"
        ),
        pytest.param("* -\n    |\n\t1.\n=\n", [("un", [[("un", [[P]])]])], id="a-paragraph-in-the-bare-item-is-continued-by-a-tab"),
        pytest.param(
            "  + -    |\n        -\n\t1.\n**", [("un", [[("un", [[P]]), ("or", [[]])]]), P], id="the-same-after-spacing"
        ),
        pytest.param(
            "+ 1.\n  --\n\n      a\n**", [("un", [[("or", [[]]), P, C]]), P], id="the-bare-item-ends-at-a-blank-line-and-code-follows"
        ),
        pytest.param(
            "0. 1. a\n\n   h\n\n\t   c\nc", [("or", [[("or", [[P]]), P, C]]), P], id="nested-items-end-at-a-blank-line-and-code-follows"
        ),
        pytest.param(
            "2) 2. 1.\n   =\n\n       a\n**",
            [("or", [[("or", [[("or", [[]])]]), P, C]]), P],
            id="three-levels-end-at-a-blank-line-and-code-follows",
        ),
        pytest.param(
            "-    -    a\n         2.\n     0.\n.\n",
            [("un", [[("un", [[P]]), ("or", [[]])]]), P],
            id="a-lazy-line-of-a-nested-item-is-not-the-outer-items-paragraph",
        ),
        pytest.param(
            "2. -\n       >```\n\t>**\n=\n",
            [("or", [[("un", [[("q", [C])]]), ("q", [P])]])],
            id="a-quote-in-the-item-after-a-bare-one-holds-its-fence",
        ),
        pytest.param(
            ">* a\n    ***\n>   2) 1)     e\nc\n",
            [("q", [("un", [[P]])])],
            id="a-lazy-rule-is-text-so-a-marker-after-it-cannot-interrupt",
        ),
        pytest.param("> - a\n>   <x-tag>\nb\n", [("q", [("un", [[P]])])], id="a-lone-tag-is-text-in-the-items-paragraph"),
        pytest.param(
            ">---1. 1)a1. \n>1. =\n>    <x-y>\nb\n",
            [("q", [P, ("or", [[P]])])],
            id="the-same-in-an-item-after-a-setext-like-line",
        ),
        pytest.param(
            "*   2. - b\n        1. \nc\n",
            [("un", [[("or", [[("un", [[P]]), ("or", [[]])]])]]), P],
            id="an-empty-item-nested-in-an-item-nested-on-the-line-is-no-paragraph",
        ),
        pytest.param(
            "* - > a\n    2. \n    b\n=\n",
            [("un", [[("un", [[("q", [P]), ("or", [[]]), P]])]])],
            id="a-line-in-the-item-nested-on-the-first-line-after-an-empty-item",
        ),
        pytest.param(
            "> * > <x-y>\nb\n",
            [("q", [("un", [[("q", [("html",)])]])]), P],
            id="html-in-the-quote-of-an-item-leaves-no-paragraph",
        ),
        pytest.param(
            "> *  2. > <x-y>\nb\n",
            [("q", [("un", [[("or", [[("q", [("html",)])]])]])]), P],
            id="the-same-one-item-further-in",
        ),
        pytest.param("1. * * *\n       x\ny\n", [("or", [[("rule",), C]]), P], id="a-thematic-break-nests-no-item"),
        pytest.param(
            ">  +    *\te\n>   \t    >a\n>     *\n",
            [("q", [("un", [[("un", [[P, ("q", [P])]])]])])],
            id="lazy-text-after-a-quote-in-a-nested-item",
        ),
        pytest.param(
            ">\t2) 1.\t-\t#\n>           - c\n>=\n",
            [("q", [("or", [[("or", [[("un", [[("heading",)], [P]])]])]])])],
            id="a-setext-underline-is-lazy-text-in-a-sibling-of-the-nested-item",
        ),
        pytest.param(
            ">\t2) 1.\t\t-\n>          - c\n>=\n",
            [("q", [("or", [[("or", [[C, ("un", [[P]])]])]])])],
            id="the-same-after-code-in-the-nested-item",
        ),
        pytest.param(
            "+     + q\n      ~\n  2) ```\n\tx\nc\n",
            [("un", [[C, ("or", [[C]]), P]])],
            id="a-line-short-of-the-item-with-the-fence-ends-it",
        ),
        pytest.param(
            "- > 0.   -  \t<v>\nz\tz\n  ===\n",
            [("un", [[("q", [("or", [[("un", [[C]])]])])]])],
            id="code-in-a-quote-is-no-paragraph-to-continue",
        ),
        pytest.param(
            "-\n  1)   ```\n      c\n       x\ne\n",
            [("un", [[("or", [[C]]), C]]), P],
            id="a-line-short-of-a-nested-item-ends-it-and-is-code-in-the-outer",
        ),
        pytest.param(
            "- 1)\t\t=\n     a\n  2) ***\nx\n",
            [("un", [[("or", [[C, P], [("rule",)]])]]), P],
            id="a-paragraph-in-a-nested-item-is-not-the-items-own",
        ),
        pytest.param(
            "> > - 0. \t-     a\n> >         x\ne\n",
            [("q", [("q", [("un", [[("or", [[("un", [[C]]), P]])]])])])],
            id="a-quoted-line-in-an-outer-item-opens-a-paragraph-after-code",
        ),
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
    assert nested_content("1.      - x")[:2] == (3, 3)  # the item is there; what follows is its code
    assert nested_content("- 1.      - x")[:2] == (5, 5)
    assert nested_content("- 1.    - x")[:2] == (10, 10)  # four columns of spacing: the next item nests
    assert nested_content("-\t- x")[:2] == (6, 4)  # the tab is three columns, one character


def test_nested_content_stops_at_a_thematic_break_it_is_told_of() -> None:
    assert nested_content("* * *")[:2] == (4, 4)  # without: two items, as the rest of a line of markers
    assert nested_content("* * *", 0, _Rule("* * *"))[:2] == (0, 0)
    assert nested_content("* * a", 0, _Rule("* * a"))[:2] == (4, 4)
    assert nested_content("1. * * *", 0, _Rule("1. * * *"))[:2] == (3, 3)  # the break begins after the first marker


def test_an_item_marker_before_a_line_ending_is_empty() -> None:
    for text in ("-", "- ", "-\n", "- \n", "1.\n"):
        found = item_marker_at(text)
        assert found is not None and found.blank, text
    assert item_marker_at("-\nx") is None  # a marker is followed by a blank, or ends the text


def test_nested_content_tells_the_columns_of_each_level() -> None:
    assert nested_content("- 1. a").levels == (2, 5)
    assert nested_content("-\t- x").levels == (4, 6)  # the second marker is at the tab stop
    assert nested_content("1.      - x").levels == (3,)  # the code ends the walk: the marker's content is one column in
    assert nested_content("a").levels == ()
    found = nested_content("- 1. * a")
    assert found.levels[-1] == found.columns


def test_the_marker_walker_gives_the_markers_of_a_line_in_order() -> None:
    found = list(ItemMarkers("- 12) *  x"))
    assert [(marker.start, marker.end) for marker in found] == [(0, 2), (2, 6), (6, 9)]
    assert [marker.start_column for marker in found] == [0, 2, 6]
    assert list(ItemMarkers("x - y")) == []  # text before a marker ends the walk before it
    assert [m.start_column for m in ItemMarkers("-\t-")] == [0, 4]  # a tab is read to its stop


def test_the_walks_of_one_list_are_dropped_with_it() -> None:
    walks = _Walks()
    mark = walks.mark()
    walks.nested_content("- - - - a")
    walks.skip_markers("- - - - a")
    assert walks.nested and walks.skipped
    walks.release(mark)
    assert not walks.nested and not walks.skipped and walks.mark() == mark


def test_a_walk_takes_its_entry_out_and_is_the_walk_of_the_text() -> None:
    walks = _Walks()
    walks.nested_content("- - - a")
    assert "- - a" in walks.nested
    assert walks.nested_content("- - a") == nested_content("- - a", 0, _Rule("- - a"))
    assert "- - a" not in walks.nested  # used once
    assert "- a" in walks.nested  # and the next level's made
