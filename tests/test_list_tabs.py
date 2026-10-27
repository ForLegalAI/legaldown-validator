"""A tab in the spacing of list markers is read at the columns it reaches, from
the start of the line (#94 review).

A line with a tab reads as the same line with the spaces the tab stands for, as
CommonMark has it (each pair below renders alike in markdown-it). The columns
of nested markers were counted from the start of each piece cut out of the
line, and used as indexes into the text, in which a tab is one character: the
text was cut too far into a fence, or an HTML block, or a quote it opens, and
the list took the line after it, or a fence never closed."""
from __future__ import annotations

from dataclasses import asdict

import pytest

from legaldown import document_to_dict, parse
from legaldown.markdown import item_marker_at, nested_content
from legaldown.parser import _item_body, _Quote, quote_blocks


def _blocks(source: str) -> list:
    return document_to_dict(parse(source))["preamble"]


def _read(source: str) -> list:
    """The blocks of *source* as dicts, a quote's as those it holds."""
    return [
        [asdict(each) for each in quote_blocks(block)] if block.kind == "quote" else asdict(block)
        for block in parse(source).preamble
    ]


CASES = [
    pytest.param("- -\t```\n    x\nb\n", "- - ```\n    x\nb\n", id="fence-after-two"),
    pytest.param("- - -\t```\n    x\nb\n", "- - - ```\n    x\nb\n", id="fence-after-three"),
    pytest.param("- - -\t> a\nb\n", "- - - > a\nb\n", id="quote-after-three"),
    pytest.param("- -\t<div>\n    x\nb\n", "- - <div>\n    x\nb\n", id="html-after-two"),
    pytest.param("-\t```\n\tx\n\t```\nb\n", "-   ```\n    x\n    ```\nb\n", id="fence-closed-by-a-tab"),
    pytest.param("1.\t-\t```\n\t\tx\nb\n", "1.  -   ```\n        x\nb\n", id="ordered-then-bullet"),
    pytest.param("-  -\t```\n      x\nb\n", "-  -   ```\n      x\nb\n", id="tab-after-two-spaces"),
    pytest.param("> -\t```\n> \tx\n> \t```\nb\n", "> -   ```\n>     x\n>     ```\nb\n", id="in-a-quote"),
    pytest.param("- >\t- \t```\n  \tx\nb\n", "- > -   ```\n    x\nb\n", id="quote-in-item"),
]


@pytest.mark.parametrize(("tabbed", "spaced"), CASES)
def test_a_tab_in_the_spacing_of_markers_reads_as_the_spaces_it_stands_for(tabbed: str, spaced: str) -> None:
    assert _read(tabbed) == _read(spaced)


def test_the_fence_a_nested_marker_and_a_tab_open_leaves_the_lazy_line_out_of_the_list() -> None:
    blocks = _blocks("- - -\t```\n    x\nb\n")
    assert [block["kind"] for block in blocks] == ["unordered_list", "paragraph"]
    assert blocks[1]["text"] == "b"


def test_a_fence_in_a_quote_closes_at_a_line_a_tab_indents_to_its_item() -> None:
    quote = _Quote()
    quote.read("-\t```")
    assert quote.paragraph is False
    quote.read("\t```")
    assert quote._fence is None


def test_the_items_a_quoted_line_opens_are_cut_at_their_text_not_their_column() -> None:
    assert _item_body("-\t```") == ("```", 4)
    assert _item_body("-\t-\t```") == ("```", 8)
    assert _item_body("- ```") == ("```", 2)
    assert _item_body("-\t   ```") == ("     ```", 2)  # six columns of spacing: one is the marker's, five the text's


def test_nested_content_in_columns_and_characters() -> None:
    assert nested_content("-\t```") == (4, 2)
    assert nested_content("- - ```") == (4, 4)
    assert nested_content("-\t-\t```") == (8, 4)
    assert nested_content("- ```", 2) == (2, 2)
    assert nested_content("```") == (0, 0)


def test_the_columns_of_a_marker_are_counted_from_where_its_text_starts() -> None:
    found = item_marker_at("-\ta", 0, 2)  # the dash at column 2: the tab runs to 4
    assert found is not None
    assert (found.indent, found.gap, found.column, found.end_column) == (0, 1, 4, 4)
    assert item_marker_at("-\ta", 0, 0).column == 4  # type: ignore[union-attr]
    assert item_marker_at("-a") is None
    assert item_marker_at("-").blank is True  # type: ignore[union-attr]
