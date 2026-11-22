"""Where each marker is written in the source (#150): ``FoundMarker.span`` and ``FoundMarker.line``."""
from __future__ import annotations

import dataclasses
import random

import pytest
from test_linear_oracles import _CONTEXTS, _MARKER_TOKENS, _random_texts

from legaldown import Document, load, parse
from legaldown.syntax import FoundMarker, block_fragments, find_markers

_HEAD = "---\ntitle: T\n---\n\n"


def _written(source: str, found: FoundMarker) -> str:
    start, end = found.span
    return source[start:end]


def _fragment_text(document: Document, found: FoundMarker) -> str:
    """The marker as its fragment holds it: ``FoundMarker.source`` is read with comments and code spans
    blanked."""
    blocks = document.preamble if found.section is None else document.sections[found.section].blocks
    text = block_fragments(blocks[found.block])[found.fragment].text
    return text[found.offset:found.offset + len(found.source)]


def _file_line(source: str, offset: int) -> int:
    """The file line of *offset*, counted from 1, line endings being LF, CR or CRLF."""
    before = source[:offset]
    return before.count("\n") + before.count("\r") - before.count("\r\n") + 1


def _check(source: str, document: Document | None = None) -> list[FoundMarker]:
    """Every marker of *source* has a span of its own, which holds it as written, on its line."""
    document = parse(source, filename="t.lgd") if document is None else document
    found = find_markers(document)
    spans = [each.span for each in found]
    assert len(set(spans)) == len(spans), source
    for each in found:
        assert each.span is not None, (source, each)
        blocks = document.preamble if each.section is None else document.sections[each.section].blocks
        written = _written(source, each)
        if blocks[each.block].kind == "table":
            written = written.replace("\\|", "|")  # a cell's pipe is written escaped
        assert written == _fragment_text(document, each), (source, each)
        assert each.line == _file_line(source, each.span[0]), (source, each)
    return found


# ── The cases of the issue ────────────────────────────────────────


def test_a_marker_after_a_soft_break_in_a_paragraph():
    source = _HEAD + "# A\n\nFirst line\n   and the second {#p when=a}\n"
    [found] = _check(source)
    assert found.line == 8
    assert found.span == (source.index("{#p"), source.index("{#p") + len("{#p when=a}"))
    assert found.offset != found.span[0] - source.index("First")  # the fragment's leading spaces are gone


def test_markers_in_list_items_nested_with_tabs():
    source = _HEAD + "# A\n\n- one {#a}\n\t- two {#b}\n\t\t- three {when=x}\n- four\n\n  more {#c}\n"
    found = _check(source)
    assert [each.source for each in found] == ["{#a}", "{#b}", "{when=x}", "{#c}"]
    assert [each.line for each in found] == [7, 8, 9, 12]


def test_markers_in_block_quotes_and_a_list_in_a_quote():
    source = _HEAD + "# A\n\n> quoted {#q}\n> > deeper {#r}\n\n> - item {#s}\n>   lazy {#t}\n"
    found = _check(source)
    assert [(each.source, each.line) for each in found] == [("{#q}", 7), ("{#r}", 8), ("{#s}", 10), ("{#t}", 11)]
    assert all(each.misplaced for each in found)  # a quote is no marker position


def test_markers_in_a_definition_and_around_a_lifted_ref_or_term():
    source = _HEAD + (
        '# A\n\n"Fee" {{def: fee}} means money {#f}\n\nSee {{ref: a}} below {#g}\n\n'
        "{{term: fee}} is {#h} due\n"
    )
    found = _check(source)
    assert [(each.source, each.line) for each in found] == [("{#f}", 7), ("{#g}", 9), ("{#h}", 11)]


@pytest.mark.parametrize("ending", ["\r\n", "\r", "\n"])
def test_line_endings_count_as_written(ending):
    source = (_HEAD + "# A\n\nText\nmore {#a}\n\n- b {when=c}\n").replace("\n", ending)
    found = _check(source)
    assert [each.line for each in found] == [8, 10]
    assert [_written(source, each) for each in found] == ["{#a}", "{when=c}"]


def test_offsets_are_code_points_and_a_byte_order_mark_counts():
    source = "﻿" + _HEAD + "# Één\n\nÉtude 日本語 — 🙂 {#a}\n"
    [found] = _check(source)
    assert found.span[0] == source.index("{#a}")
    assert found.line == 7


def test_two_markers_spelled_alike_each_have_their_own_place():
    source = _HEAD + (
        "# A\n\nWrite `{#same}` as {#same} in the text \\{#same} {#same}\n\n> {#same}\n\nAgain {#same}\n"
    )
    found = _check(source)
    assert [each.source for each in found] == ["{#same}"] * 4
    assert [each.misplaced == "" for each in found] == [False, True, False, True]
    every = [at for at in range(len(source)) if source.startswith("{#same}", at)]
    # Not the one in a code span, nor the escaped one: they are text, no look-alike.
    assert [each.span[0] for each in found] == [every[1], every[3], every[4], every[5]]


def test_a_marker_whose_fragment_blanks_a_comment_spans_what_is_written():
    source = _HEAD + "# A\n\nText {when=<!-- c -->a}\n"
    [found] = _check(source)
    assert _written(source, found) == "{when=<!-- c -->a}"
    assert found.source == "{when=          a}"  # as the validator reads it, the comment blanked


def test_a_marker_in_a_table_cell_and_one_after_a_fenced_code_block():
    source = _HEAD + "# A\n\n| h | i |\n|---|---|\n| c {#x} | d {#x} |\n\n```\n{#x}\n```\n\nText {#x}\n"
    found = _check(source)
    assert [each.line for each in found] == [9, 9, 15]


def test_a_document_changed_since_it_was_parsed_or_built_in_code_has_no_spans():
    source = _HEAD + "# A\n\nText {#a}\n"
    document = parse(source)
    assert _written(source, find_markers(document)[0]) == "{#a}"
    document.sections[0].blocks[0].text = "Changed text {#a}"
    [found] = find_markers(document)
    assert found.span is None and found.line is None
    built = Document.from_dict(parse(_HEAD + "# A\n\nText {#a}\n").to_dict())
    assert [(each.span, each.line) for each in find_markers(built)] == [(None, None)]


def test_load_gives_offsets_in_the_files_text(tmp_path):
    path = tmp_path / "t.lgd"
    source = "﻿" + (_HEAD + "# A\n\nTèxt\nmore {#a}\n").replace("\n", "\r\n")
    path.write_bytes(source.encode("utf-8"))
    [found] = find_markers(load(path))
    assert _written(source, found) == "{#a}"
    assert found.line == 8


def test_the_span_fields_come_last_default_to_none_and_are_not_compared():
    names = [f.name for f in dataclasses.fields(FoundMarker)]
    assert names[-2:] == ["span", "line"]
    bare = FoundMarker("{#a}", None, 0, 0, 0, "", False, False, 5)
    assert bare.span is None and bare.line is None
    assert bare == dataclasses.replace(bare, span=(1, 5), line=3)


# ── An oracle over random text ────────────────────────────────────

_CONTEXTS_TOO = [
    "# T\n\n- a\n\t- {text}",
    "# T\n\n> - {text}\n>   more",
    '# T\n\n"Term" {{{{def: term}}}} means {text}',
    "# T\n\nSee {{{{ref: x}}}} then {text}",
    "# T\n\n```\n{{#x}}\n```\n{text}",
    "# T\n\n- a\n\n  > {text}",
    "# T\n\n>> {text}\n> b",
    "# T\n\n1. a\n\n   b\n   {text}\n2. c",
]
_EXTRA_TOKENS = ["\\|", "\t", "é", "日", "🙂", "\r\n", "\r", "> ", "  - ", "\t- ", "{#x}"]


def test_every_marker_spans_itself_as_written_in_random_documents():
    rng = random.Random(150)
    checked = 0
    for text in _random_texts(_MARKER_TOKENS + _EXTRA_TOKENS, 4000, 14, seed=1501):
        source = rng.choice(_CONTEXTS + _CONTEXTS_TOO).format(text=text)
        if rng.random() < 0.3:
            source = source.replace("\n", "\r\n")
        if rng.random() < 0.1:
            source = "﻿" + source
        checked += len(_check(source))
    assert checked > 3000  # the generator reaches markers


def test_a_pipe_in_a_table_cell_is_spanned_as_the_cell_writes_it():
    source = _HEAD + "# A\n\n| h |\n|---|\n| c {when=a\\|b} |\n"
    [found] = find_markers(parse(source))
    assert found.source == "{when=a|b}"
    assert _written(source, found) == "{when=a\\|b}"
    assert found.line == 9


def test_a_marker_whose_comment_holds_a_brace_is_found_by_its_brace():
    source = _HEAD + "# A\n\nText {#a <!-- } -->} and {#a <!-- } -->}\n\n| h |\n|---|\n| {when=\"a\\|b\" <!-- } -->} |\n"
    found = _check(source)
    assert [_written(source, each) for each in found] == [
        "{#a <!-- } -->}", "{#a <!-- } -->}", '{when="a\\|b" <!-- } -->}'
    ]


def test_many_markers_holding_braces_in_comments_are_found_in_linear_time():
    import time

    def took(count: int) -> float:
        text = "\n".join(f"x {{#a{number} <!-- }} -->}}" for number in range(count))
        document = parse(_HEAD + "# A\n\n" + text + "\n")
        start = time.perf_counter()
        found = find_markers(document)
        assert len(found) == count and all(each.span for each in found)
        return time.perf_counter() - start

    small, large = took(1000), took(8000)
    assert large < max(small, 0.01) * 30  # 8 times the markers: quadratic would be 64 times the time
