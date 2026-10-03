"""Model answers for tools: a quote's content, a drafting note without its
marker, a code block's content, and where a document's parts lie in its file."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from legaldown import document_from_dict, document_to_dict, parse
from legaldown.definitions import list_fragments
from legaldown.markdown import CodeContent, code_content
from legaldown.models import LIST_KINDS, Block, ListItem, item_text, list_items
from legaldown.parser import MAX_QUOTE_DEPTH, FrontmatterError, quote_blocks, quote_content
from legaldown.positions import BlockSpan, HeadingSpan, ItemSpan, SectionSpan, SourceLayout
from legaldown.validator import validate
from legaldown.validator.templates import drafting_note_blocks, is_drafting_note

_HEAD = "---\ntitle: T\n---\n\n# A\n\n"


def _blocks(body: str) -> list[Block]:
    return parse(_HEAD + body).sections[0].blocks


def _shown(blocks: list[Block]) -> list[tuple[str, str]]:
    return [(block.kind, block.text) for block in blocks]


# ── Quote content ──────────────────────────────────────────────────


def test_quote_blocks_are_the_blocks_the_validator_reads():
    [quote] = _blocks("> One\n>\n> - a\n> - b\n>\n> > Inner\n")
    blocks = quote_blocks(quote)
    assert [block.kind for block in blocks] == ["paragraph", "unordered_list", "quote"]
    assert blocks == list(quote_content(quote.text, 1)[0])
    assert _shown(quote_blocks(blocks[2], depth=1)) == [("paragraph", "Inner")]


def test_quote_blocks_are_fresh_copies():
    [quote] = _blocks("> One\n>\n> - a\n")
    blocks = quote_blocks(quote)
    blocks[0].text = "changed"
    blocks[1].items[0].blocks[0].text = "changed"
    blocks.append(Block())
    assert quote_blocks(quote) == list(quote_content(quote.text, 1)[0])
    assert quote_blocks(quote)[0].text == "One"


def test_a_heading_in_a_quote_is_a_heading_block():
    [quote] = _blocks("> # Title\n> text\n")
    assert [(block.kind, block.level) for block in quote_blocks(quote)] == [("heading", 1), ("paragraph", 0)]


def test_quote_blocks_of_a_quote_in_a_list_item():
    [lst] = _blocks("- item\n\n  > quoted\n")
    quote = lst.items[0].blocks[1]
    assert _shown(quote_blocks(quote, depth=1)) == [("paragraph", "quoted")]


def test_a_quote_past_the_quote_depth_is_one_text():
    [quote] = _blocks("> one\n>\n> two\n")
    assert len(quote_blocks(quote, depth=MAX_QUOTE_DEPTH - 1)) == 2
    assert _shown(quote_blocks(quote, depth=MAX_QUOTE_DEPTH)) == [("paragraph", "one\n\ntwo")]
    assert quote_blocks(Block(kind="quote", text=""), depth=MAX_QUOTE_DEPTH) == []


def test_a_quote_past_the_quote_depth_is_its_text_as_written_fenced_code_included():
    text = "> not read\n\n```\n{{ref: x}}\n```\n\nafter"
    quote = Block(kind="quote", text=text)
    assert _shown(quote_blocks(quote, depth=MAX_QUOTE_DEPTH)) == [("paragraph", text)]
    note = Block(kind="quote", text="[!DRAFTING]\n" + text)
    assert _shown(drafting_note_blocks(note, depth=MAX_QUOTE_DEPTH)) == [("paragraph", text)]
    # Short of the depth the same text is read into blocks, the fence a code block.
    assert [block.kind for block in quote_blocks(quote, depth=MAX_QUOTE_DEPTH - 1)][:3] == ["quote", "code", "paragraph"]


@pytest.mark.parametrize("kind", ["paragraph", "code", "unordered_list", "rule"])
def test_quote_blocks_of_a_block_that_is_no_quote(kind):
    with pytest.raises(ValueError, match="quote"):
        quote_blocks(Block(kind=kind, text="x"))


# ── Drafting notes (#88) ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # The marker starts a paragraph.
        ("> [!DRAFTING]\n> note\n", [("paragraph", "note")]),
        # It is a setext heading's text: the heading goes with it.
        ("> [!DRAFTING]\n> ---\n", []),
        # Indented as code: the note is what follows the marker line.
        (">     [!DRAFTING]\n> note\n", [("paragraph", "note")]),
        # Letters in any case; later blocks stay.
        ("> [!drafting]\n> first\n>\n> second\n", [("paragraph", "first"), ("paragraph", "second")]),
        # Nothing but the marker.
        ("> [!DRAFTING]\n", []),
        ("> [!DRAFTING]\n>\n> - a\n> - b\n", [("unordered_list", "")]),
        # Its first paragraph keeps its own lines.
        ("> [!DRAFTING]\n> one\n> two\n", [("paragraph", "one\ntwo")]),
        # A heading and a quote in the note.
        ("> [!DRAFTING]\n>\n> # Head\n>\n> > inner\n", [("heading", "Head"), ("quote", "inner")]),
    ],
)
def test_drafting_note_blocks(body, expected):
    [note] = _blocks(body)
    assert is_drafting_note(note)
    assert _shown(drafting_note_blocks(note)) == expected


def test_drafting_note_blocks_are_fresh_copies():
    [note] = _blocks("> [!DRAFTING]\n> note\n>\n> second\n")
    blocks = drafting_note_blocks(note)
    blocks[0].text = "changed"
    assert _shown(drafting_note_blocks(note)) == [("paragraph", "note"), ("paragraph", "second")]
    assert _shown(quote_blocks(note))[0] == ("paragraph", "[!DRAFTING]\nnote")


def test_drafting_note_blocks_past_the_quote_depth():
    note = Block(kind="quote", text="[!DRAFTING]\nnote\n\nmore")
    assert _shown(drafting_note_blocks(note, depth=MAX_QUOTE_DEPTH)) == [("paragraph", "note\n\nmore")]
    assert drafting_note_blocks(Block(kind="quote", text="[!DRAFTING]"), depth=MAX_QUOTE_DEPTH) == []


def test_drafting_note_blocks_of_what_is_no_note():
    for block in _blocks("> Plain\n\n> [!NOTE]\n> x\n\nText\n"):
        with pytest.raises(ValueError, match="drafting note"):
            drafting_note_blocks(block)


def test_a_drafting_note_in_a_quote():
    [outer] = _blocks("> text\n>\n> > [!DRAFTING]\n> > note\n")
    [_text, inner] = quote_blocks(outer)
    assert is_drafting_note(inner)
    assert _shown(drafting_note_blocks(inner, depth=1)) == [("paragraph", "note")]


# ── Code blocks (#93) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("```python\nx = 1\n\ny = 2\n```\n", CodeContent("python", "x = 1\n\ny = 2\n", True)),
        ("```  python  title=x \nx\n```\n", CodeContent("python  title=x", "x\n", True)),
        ("~~~\na\n~~~\n", CodeContent("", "a\n", True)),
        ("~~~ js\n```\n~~~\n", CodeContent("js", "```\n", True)),
        ("````\n```\nx\n```\n````\n", CodeContent("", "```\nx\n```\n", True)),
        # Unclosed: runs to the end.
        ("```\nx\ny\n", CodeContent("", "x\ny\n", True)),
        # No lines at all.
        ("```\n```\n", CodeContent("", "", True)),
        ("```\n", CodeContent("", "", True)),
        # An indented fence: each line loses as much as the fence had.
        ("  ```\n  a\n    b\n c\n  ```\n", CodeContent("", "a\n  b\nc\n", True)),
        # Indented code: four columns.
        ("    a\n      b\n\n    c\n", CodeContent("", "a\n  b\n\nc\n", False)),
        ("\tx\n", CodeContent("", "x\n", False)),
    ],
)
def test_code_content(source, expected):
    [block] = _blocks(source)
    assert code_content(block) == expected


def test_code_content_in_a_list_item_and_a_quote():
    [lst] = _blocks("- item\n\n  ```sh\n  ls\n  ```\n")
    assert code_content(lst.items[0].blocks[1]) == CodeContent("sh", "ls\n", True)
    [quote] = _blocks("> ```sh\n> ls\n> ```\n")
    assert code_content(quote_blocks(quote)[0]) == CodeContent("sh", "ls\n", True)


def test_code_content_of_a_model_built_in_code():
    assert code_content(Block(kind="code", text="")) == CodeContent("", "", False)
    assert code_content(Block(kind="code", text="```py\nx\n```")) == CodeContent("py", "x\n", True)


@pytest.mark.parametrize("kind", ["paragraph", "quote", "html"])
def test_code_content_of_a_block_that_is_no_code(kind):
    with pytest.raises(ValueError, match="code"):
        code_content(Block(kind=kind, text="x"))


# ── Layout ─────────────────────────────────────────────────────────

_SOURCE = (
    "---\ntitle: T\n---\n"  # 1-3
    "\n"  # 4
    "Pre\n"  # 5
    "\n"  # 6
    "# A\n"  # 7
    "\n"  # 8
    "- a\n"  # 9
    "  - b\n"  # 10
    "  - c\n"  # 11
    "- d\n"  # 12
    "\n"  # 13
    "1. x\n"  # 14
    "   1. y\n"  # 15
    "      - z\n"  # 16
    "   2. w\n"  # 17
    "2. v\n"  # 18
    "\n"  # 19
    "> quote\n"  # 20
    "\n"  # 21
    "# B\n"  # 22
    "Text\n"  # 23
    "====\n"  # 24
    "Last\n"  # 25
)


def test_the_layout_gives_the_file_lines_of_each_part():
    layout = parse(_SOURCE).layout()
    assert isinstance(layout, SourceLayout)
    assert layout.frontmatter == (1, 4)
    assert layout.preamble == (BlockSpan("paragraph", 5, 6),)
    assert [section.heading for section in layout.sections] == [
        HeadingSpan(7, 8, 7),
        HeadingSpan(22, 23, 22),
        HeadingSpan(23, 25, 23),
    ]
    first, _second, third = layout.sections
    assert isinstance(first, SectionSpan)
    assert [(block.kind, block.start, block.end) for block in first.blocks] == [
        ("unordered_list", 9, 13),
        ("ordered_list", 14, 19),
        ("quote", 20, 21),
    ]
    [a, d] = first.blocks[0].items
    assert isinstance(a, ItemSpan)
    assert (a.start, a.end, d.start, d.end) == (9, 12, 12, 13)
    assert [(block.kind, block.start) for block in a.blocks] == [("paragraph", 9), ("unordered_list", 10)]
    assert [(item.start, item.end) for item in a.blocks[1].items] == [(10, 11), (11, 12)]
    assert third.blocks == (BlockSpan("paragraph", 25, 26),)


def test_a_span_ends_after_its_last_line_and_the_blank_lines_between_parts_belong_to_none():
    layout = parse("# A\n\nOne\ntwo\n\n\nThree\n").layout()
    first, second = layout.sections[0].blocks
    assert (layout.sections[0].heading.start, layout.sections[0].heading.end) == (1, 2)
    assert (first.start, first.end) == (3, 5)  # lines 3 and 4
    assert (second.start, second.end) == (7, 8)  # lines 5 and 6 are blank, in no span
    assert first.end < second.start


def test_the_layout_is_frozen():
    layout = parse(_SOURCE).layout()
    assert layout is not None
    with pytest.raises(AttributeError):
        layout.frontmatter = None  # type: ignore[misc]


def test_the_layout_without_frontmatter_or_body():
    layout = parse("# A\n\nText\n").layout()
    assert layout is not None and layout.frontmatter is None and layout.preamble == ()
    assert layout.sections[0].blocks == (BlockSpan("paragraph", 3, 4),)
    assert parse("").layout() == SourceLayout(None, (), ())


@pytest.mark.parametrize(
    ("source", "end"),
    [
        ("---\ntitle: T\n---", 4),  # no line ending after the closing line
        ("---\ntitle: T\n---\n", 4),
        ("---\n---\n\n# A\n", 3),
        ("---\r\ntitle: T\r\n---  \r\n\r\n# A\r\n", 4),
        ("﻿---\ntitle: T\n---\n# A\n", 4),
    ],
)
def test_the_layout_of_the_frontmatter(source, end):
    layout = parse(source).layout()
    assert layout is not None and layout.frontmatter == (1, end)


def test_a_thematic_break_is_not_frontmatter():
    layout = parse("---\n\n# A\n").layout()
    assert layout is not None and layout.frontmatter is None
    assert layout.preamble == (BlockSpan("rule", 1, 2),)


def test_the_layout_counts_lines_as_written():
    assert parse(_SOURCE.replace("\n", "\r\n")).layout() == parse(_SOURCE).layout()


def test_a_document_without_a_source_has_no_layout():
    rebuilt = document_from_dict(document_to_dict(parse(_SOURCE)))
    assert rebuilt.layout() is None
    assert rebuilt.line_of(0) is None and rebuilt.line_of(0, 0) is None and rebuilt.line_of(0, 0, 1) is None


def test_a_document_changed_since_parsing_has_no_layout():
    document = parse(_SOURCE)
    document.sections[0].blocks.insert(0, Block(kind="paragraph", text="New."))
    assert document.layout() is None
    assert document.line_of(0, 1) is None
    with pytest.raises(IndexError):  # misuse is still named
        document.line_of(0, 99)


# ── Lines ──────────────────────────────────────────────────────────


def test_line_of_a_heading_a_block_and_an_item():
    document = parse(_SOURCE)
    assert [document.line_of(index) for index in range(3)] == [7, 22, 23]
    assert document.line_of(None, 0) == 5
    assert [document.line_of(0, index) for index in range(3)] == [9, 14, 20]
    assert document.line_of(2, 0) == 25
    # Items in pre-order: an item, then those nested in it.
    assert [document.line_of(0, 0, item) for item in range(4)] == [9, 10, 11, 12]
    assert [document.line_of(0, 1, item) for item in range(5)] == [14, 15, 16, 17, 18]


def test_line_of_the_items_of_a_list_in_the_preamble():
    document = parse("- a\n- b\n  - c\n\n# A\n")
    assert [document.line_of(None, 0, item) for item in range(3)] == [1, 2, 3]


def test_line_of_items_are_numbered_as_list_fragments_number_them():
    document = parse(_SOURCE)
    lines = _SOURCE.split("\n")
    for index in (0, 1):
        block = document.sections[0].blocks[index]
        flat = _preorder(block)
        for fragment in list_fragments(block):
            if fragment.anchor:  # the end of an item's first paragraph
                assert fragment.text == item_text(flat[fragment.items[-1]])
        for number, item in enumerate(flat):
            assert item_text(item) in lines[document.line_of(0, index, number) - 1]


def test_line_of_items_stop_at_a_quote():
    document = parse("- a\n\n  > - q\n  > - r\n- b\n")
    # The quote's list items are no items of the list (§15.3).
    assert [document.line_of(None, 0, item) for item in range(2)] == [1, 5]
    with pytest.raises(IndexError):
        document.line_of(None, 0, 2)


def test_line_of_in_a_document_without_frontmatter():
    document = parse("# A\n\nText\n")
    assert (document.line_of(0), document.line_of(0, 0)) == (1, 3)


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ((5,), IndexError),
        ((-1,), IndexError),
        ((0, 9), IndexError),
        ((0, -1), IndexError),
        ((None, 5), IndexError),
        ((0, 0, 4), IndexError),
        ((0, 0, -1), IndexError),
        ((0, 2, 0), ValueError),  # a quote is no list
        ((0, None, 0), ValueError),
        ((None,), ValueError),
        ((None, None, 0), ValueError),
    ],
)
def test_line_of_misuse(args, error):
    with pytest.raises(error):
        parse(_SOURCE).line_of(*args)


def test_line_of_agrees_with_the_lines_diagnostics_name():
    document = parse(_HEAD + "> [!DRAFT]\n> text\n\nAnd\n\n> [!DRAFT]\n> more\n\n### Skip\n\nText\n")
    lines: dict[str, list[int | None]] = {}
    for diagnostic in validate(document).diagnostics:
        lines.setdefault(diagnostic.rule, []).append(diagnostic.line)
    assert lines["drafting-note-unrecognized"] == [document.line_of(0, 0), document.line_of(0, 2)]
    assert lines["heading-skip"] == [document.line_of(1)]
    assert document.line_of(0, 0) == 7


# ── Over the specification's documents ─────────────────────────────

_ITEM_RE = re.compile(r"[ \t]*(?:[0-9]{1,9}[.)]|[-*+])(?:[ \t]|$)")


def _corpus():
    root = os.environ.get("LEGALDOWN_FIXTURES_DIR", "")
    if not root or not Path(root).is_dir():
        return []
    return [pytest.param(path, id=path.relative_to(root).as_posix()) for path in sorted(Path(root).rglob("*.lgd"))]


def _preorder(block: Block) -> list[ListItem]:
    found: list[ListItem] = []
    for item in list_items(block):
        found.append(item)
        for child in item.blocks:
            if child.kind in LIST_KINDS:
                found.extend(_preorder(child))
    return found


def _read(blocks: list[Block], depth: int) -> None:
    """Ask the model for every quote and code block, nested ones too."""
    for block in blocks:
        if block.kind in LIST_KINDS:
            for item in list_items(block):
                _read(item.blocks, depth + 1)
        elif block.kind == "code":
            assert code_content(block).text.endswith("\n") or not block.text.strip()
        elif block.kind == "quote":
            content = quote_blocks(block, depth=depth)
            if is_drafting_note(block):
                drafting_note_blocks(block, depth=depth)
            _read(content, depth + 1)


@pytest.mark.parametrize("path", _corpus())
def test_the_model_answers_hold_over_every_specification_document(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        document = parse(text)
    except FrontmatterError:
        pytest.skip("unreadable frontmatter")
    layout = document.layout()
    assert layout is not None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").removeprefix("﻿").split("\n")
    count = len(lines) - (1 if lines[-1] == "" else 0)
    if layout.frontmatter is not None:
        assert layout.frontmatter[0] == 1 <= layout.frontmatter[1] <= count + 1
    assert len(layout.sections) == len(document.sections)
    containers = [(None, document.preamble, layout.preamble)]
    containers += [
        (index, section.blocks, span.blocks)
        for index, (section, span) in enumerate(zip(document.sections, layout.sections, strict=True))
    ]
    for index, blocks, spans in containers:
        if index is not None:
            heading = layout.sections[index].heading
            assert document.line_of(index) == heading.start
            assert 1 <= heading.start <= heading.marker_line < heading.end <= count + 1
        assert len(blocks) == len(spans)
        for number, (block, span) in enumerate(zip(blocks, spans, strict=True)):
            assert document.line_of(index, number) == span.start
            assert 1 <= span.start < span.end <= count + 1
            assert span.kind == block.kind
            if block.kind in LIST_KINDS:
                flat = _preorder(block)
                for item_number, item in enumerate(flat):
                    at = document.line_of(index, number, item_number)
                    assert at is not None and span.start <= at < span.end
                    assert _ITEM_RE.match(lines[at - 1]), (at, lines[at - 1])
                    assert item_text(item).split("\n")[0].strip() in lines[at - 1]
                with pytest.raises(IndexError):
                    document.line_of(index, number, len(flat))
                assert len(span.items) == len(block.items)
                for item_span, item in zip(span.items, list_items(block), strict=True):
                    assert span.start <= item_span.start < item_span.end <= span.end
                    assert len(item_span.blocks) == len(item.blocks)
        _read(blocks, 0)
