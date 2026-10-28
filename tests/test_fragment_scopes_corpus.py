"""``fragment_scopes`` (#105): the scopes of every block of every fixtures-corpus
case run in order and never span list items (runs when ``LEGALDOWN_FIXTURES_DIR``
is set, as the rest of the harness does); and the grouping on blocks built by
hand (always)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from legaldown.definitions import block_fragments, fragment_scopes, list_fragments, nested_blocks
from legaldown.models import LIST_KINDS
from legaldown.parser import MAX_QUOTE_DEPTH, parse

FIXTURES_DIR = os.environ.get("LEGALDOWN_FIXTURES_DIR", "")

needs_corpus = pytest.mark.skipif(
    not FIXTURES_DIR or not Path(FIXTURES_DIR).is_dir(),
    reason="LEGALDOWN_FIXTURES_DIR not set (LegalDown specification fixtures corpus)",
)


def _cases():
    root = Path(FIXTURES_DIR)
    return [pytest.param(lgd, id=str(lgd.relative_to(root))) for lgd in sorted(root.rglob("*.lgd"))] if FIXTURES_DIR else []


@needs_corpus
@pytest.mark.parametrize("lgd", _cases())
def test_the_scopes_of_every_block_run_in_order_and_never_span_list_items(lgd):
    document = parse(lgd.read_text(encoding="utf-8"), filename=lgd.name, on_frontmatter_error="keep")
    for _section, _index, block in document.iter_indexed_blocks():
        for inner in nested_blocks(block):
            scopes = fragment_scopes(inner)
            where = (lgd.name, inner.kind)
            assert scopes == sorted(scopes) and all(scope >= 0 for scope in scopes), where
            if inner.kind in LIST_KINDS:
                # A scope is one paragraph, so it is in one item.
                items: dict[int, tuple[int, ...]] = {}
                for scope, fragment in zip(scopes, list_fragments(inner), strict=True):
                    assert items.setdefault(scope, fragment.items) == fragment.items, where


# ── The grouping, on blocks built by hand ─────────────────────────


def _block(body: str):
    return parse(f"# T {{#t}}\n\n{body}\n").sections[0].blocks[0]


@pytest.mark.parametrize(
    "body",
    [
        'A "Bar" {{def: bar}} is {{term: bar}}.',
        '{{term: bar}} then "Bar" {{def: bar}} after.',
        "- one\n  - two\n\n  more\n- three",
        "> a\n>\n> b\n>\n> - c\n> - d",
        "| a | b |\n|---|---|\n| c |  |\n|  | d |",
        "```\ncode\n```\n\nafter",
        "<div>x</div>",
        "> " * (MAX_QUOTE_DEPTH + 2) + "deep",
    ],
)
def test_a_block_has_a_scope_for_each_fragment(body):
    block = _block(body)
    assert len(fragment_scopes(block)) == len(block_fragments(block))


def test_the_cells_of_a_row_share_a_scope_and_the_rows_do_not():
    scopes = fragment_scopes(_block("| a | b |\n|---|---|\n| c | d |\n|  | e |\n| f | g |"))
    assert scopes == [1, 1, 2, 2, 3, 4, 4]  # the header row; a row's empty cells hold no fragment


def test_the_paragraphs_of_list_items_are_separate_scopes():
    scopes = fragment_scopes(_block("- one\n  - two\n\n  more\n- three"))
    assert len(scopes) == len(set(scopes)) == 4


def test_the_blocks_of_a_quote_are_separate_scopes():
    scopes = fragment_scopes(_block("> a\n>\n> b\n>\n> - c\n> - d"))
    assert len(scopes) == len(set(scopes)) == 4


def test_a_quote_too_deep_to_read_is_one_scope():
    scopes = fragment_scopes(_block("> " * (MAX_QUOTE_DEPTH + 2) + "a\n" + ">" * (MAX_QUOTE_DEPTH + 2) + "\n" + "> " * (MAX_QUOTE_DEPTH + 2) + "b"))
    assert scopes == [0]


def test_the_text_prefix_and_suffix_of_a_block_are_one_scope():
    block = _block('Before "Bar" {{def: bar}} {{term: bar}} after')
    assert block.kind == "term"
    assert fragment_scopes(block) == [0, 0]
