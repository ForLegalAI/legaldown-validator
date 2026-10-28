"""The parser builds the document as it stands (#110).

It used to turn every block into a dict and the dicts into the document
(``block_from_dict``), which normalised what the parser left as it read it, and
built everything twice. The normalisations are done where the blocks are
made. Four are cases a fuzzer found; the rest are in the fields the parser
lifts from a directive or a link reference definition, which a no-break space
reaches. The proof is a fixed point: running the old round trip over what the
parser gives changes nothing, over the corpus and over many random sources.
"""
from __future__ import annotations

import os
import random
from dataclasses import asdict
from pathlib import Path

import pytest

from legaldown import models, parse
from legaldown.models import Block, Document, block_from_dict, section_from_dict
from legaldown.parser import parse_item_content, quote_blocks

# ── The four cases ────────────────────────────────────────────────


def test_an_html_block_loses_its_trailing_spaces():
    (block,) = parse("<div>x  \t\n").preamble
    assert (block.kind, block.text) == ("html", "<div>x")


def test_an_html_block_in_an_item_loses_its_trailing_spaces():
    (outer,) = parse("- 1. <div>x \t\n").preamble
    inner = outer.items[0].blocks[0]
    assert inner.kind == "ordered_list"
    assert (inner.items[0].blocks[0].kind, inner.items[0].blocks[0].text) == ("html", "<div>x")


def test_a_quote_of_a_whitespace_only_line_is_empty():
    (block,) = parse(">   \n").preamble
    assert (block.kind, block.text) == ("quote", "")


def test_a_heading_of_only_a_marker_is_a_new_section_in_a_document_with_no_title():
    assert [section.title for section in parse("#  \n\nx\n").sections] == ["New Section"]


def test_the_filename_is_stripped():
    assert parse("x", filename="  a.lgd ").filename == "a.lgd"


# ── What a no-break space in a lifted field did ───────────────────

_NBSP = " "


@pytest.mark.parametrize(
    ("source", "kind", "field", "expected"),
    [
        (f'"T" {{{{def: {_NBSP}t}}}} means x.', "definition", "definition_id", "t"),
        (f"{{{{ref: {_NBSP}s}}}}", "ref", "target", "s"),
        (f"{{{{term: {_NBSP}t, label={_NBSP}L{_NBSP}}}}}", "term", "target", "t"),
        (f"{{{{term: t, label={_NBSP}L{_NBSP}}}}}", "term", "label", "L"),
        (f"[{_NBSP}z]: u", "link_definition", "label", "z"),
        (f"[z]: {_NBSP}u", "link_definition", "target", "u"),
    ],
)
def test_a_lifted_field_is_stripped_of_all_whitespace(source, kind, field, expected):
    (block,) = parse(f"# T {{#t}}\n\n{source}\n").sections[0].blocks
    assert block.kind == kind
    assert getattr(block, field) == expected


# ── A quote's content, and an item given as text, are read as the source has them ──


def test_a_quote_s_blocks_are_not_tidied():
    """Assembly and the validator read them with their text and lines as written."""
    (html,) = quote_blocks(Block(kind="quote", text="<div>x  "))
    assert html.text == "<div>x  "
    (link,) = quote_blocks(Block(kind="quote", text=f"[{_NBSP}z]: u"))
    assert link.label == f"{_NBSP}z"
    (inner,) = quote_blocks(Block(kind="quote", text="> a\n>  "))
    assert inner.kind == "quote" and inner.text == "a\n "


def test_an_item_given_as_text_is_not_tidied():
    (html,) = parse_item_content("<div>x  ")
    assert html.text == "<div>x  "


# ── Nothing is built twice ────────────────────────────────────────


def test_parse_does_not_round_trip_the_blocks(monkeypatch):
    def refused(*_args, **_kwargs):
        raise AssertionError("round trip")

    calls = []
    real_asdict = models.asdict
    monkeypatch.setattr(models, "block_from_dict", refused)
    monkeypatch.setattr(models, "section_from_dict", refused)
    monkeypatch.setattr(models, "document_from_dict", refused)
    monkeypatch.setattr(models, "asdict", lambda value: calls.append(value) or real_asdict(value))
    document = parse("---\ntitle: T\n---\n\n# S {#s}\n\n- a\n- b\n\n> q\n\n<div>x\n\ntext\n")
    assert [section.title for section in document.sections] == ["S"]
    assert calls == []  # nor are the blocks turned into dicts: the metadata is the only dict


def test_no_block_is_shared():
    document = parse("- a\n- b\n- c\n\n- d\n")
    blocks = [block for _section, _index, block in document.iter_blocks()]
    items = [item for block in blocks for item in block.items]
    assert len({id(item) for item in items}) == len(items) == 4
    assert len({id(inner) for item in items for inner in item.blocks}) == 4
    assert len({id(block.headers) for block in blocks} | {id(block.rows) for block in blocks}) == 2 * len(blocks)


# ── The fixed point ───────────────────────────────────────────────


def _blocks(block):
    yield block
    for item in block.items:
        for inner in item.blocks:
            yield from _blocks(inner)


def _fixed_point(document: Document) -> list[str]:
    """What the old round trip would still change in *document*: its blocks (items'
    included), its sections, its filename."""
    changed = []
    containers = [("preamble", document.preamble)]
    containers += [(f"section {index}", section.blocks) for index, section in enumerate(document.sections)]
    for where, blocks in containers:
        for top in blocks:
            for block in _blocks(top):
                if block_from_dict(asdict(block)) != block:
                    changed.append(f"{where}: {block.kind} {block.text!r}")
    for index, section in enumerate(document.sections):
        again = section_from_dict(asdict(section))
        if (again.title, again.level, again.identifier, again.condition) != (
            section.title, section.level, section.identifier, section.condition,
        ):
            changed.append(f"section {index}: {section.title!r}")
    if document.filename != document.filename.strip():
        changed.append("filename")
    return changed


_FIXTURES = os.environ.get("LEGALDOWN_FIXTURES_DIR", "")


def _corpus():
    root = Path(_FIXTURES)
    return sorted(root.rglob("*.lgd")) if _FIXTURES and root.is_dir() else []


@pytest.mark.skipif(not _corpus(), reason="LEGALDOWN_FIXTURES_DIR not set (LegalDown specification fixtures corpus)")
def test_the_round_trip_changes_nothing_in_the_corpus():
    for lgd in _corpus():
        document = parse(lgd.read_text(encoding="utf-8"), filename=lgd.name, on_frontmatter_error="keep")
        assert _fixed_point(document) == [], lgd.name


_TOKENS = [
    "# H", "## ", "#", "- ", "1. ", "  ", "    ", "> ", ">", "|", "| a | b |", "|---|:-:|", "---", "***", "```", "~~~",
    "<div>", "</div>", "<!--", "-->", "[x]: <u> 'ti\ntle'", '[y]: u "t"', "x", "y", "\t", " ", "\n", "\n\n", "\\",
    "{#id}", "{#a when=q}", '"T" {{def: t}}', "{{term: t}}", "Setext\n===", "Setext\n---", "\r\n", "\r", "  \n",
    "&amp;", "*", "_", "`", "{{placeholder: p}}", "{{choose: q}}", "| a |\n|---|\n| b | c | d |", "|:-|-:|:-:|",
    # Whitespace that is text to CommonMark, and in the fields a directive or a link reference definition lifts.
    " ", " ", "　", "\x0b", "\x0c", "\x1f", "​", "﻿", " ",
    "[ z]: u", "[z]:  u", "{# a}", '" T" {{def: t}}', '"T" {{def:  t}}',
    "{{term: t, label= L }}", "{{ref:  s}}", "{{ref: s}}",
]


@pytest.mark.parametrize("seed", range(8))
def test_the_round_trip_changes_nothing_in_random_sources(seed):
    rng = random.Random(110_700 + seed)
    for _ in range(500):
        source = "".join(rng.choice(_TOKENS) for _ in range(rng.randint(1, 40)))
        if rng.random() < 0.5:
            source = "---\ntitle: T\n---\n" + source
        document = parse(source, filename=rng.choice(["t.lgd", " t.lgd ", ""]), on_frontmatter_error="keep")
        assert _fixed_point(document) == [], source
        if document.frontmatter_error is None:
            again = Document.from_dict(document.to_dict())
            assert again.sections == document.sections
            assert again.preamble == document.preamble
            assert again.filename == document.filename
