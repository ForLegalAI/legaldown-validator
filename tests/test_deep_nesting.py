"""Deep nesting up to the parser's limits, and past them: lists nested
``MAX_LIST_DEPTH`` deep, quotes nested ``MAX_QUOTE_DEPTH`` deep, and the two
in each other. Each public path reads such a document without a
``RecursionError``: past the limits the parser reads the rest as text, so no
document nests deeper than that, but a path that recurses more per level than
the parser does can still run out of stack below them."""
from __future__ import annotations

import pytest

from legaldown import Document, parse, parse_template, serialize, validate
from legaldown.grammar import MAX_LIST_DEPTH, MAX_QUOTE_DEPTH
from legaldown.parser import quote_content
from legaldown.syntax import (
    block_fragments,
    body_layout,
    code_content,
    drafting_note_blocks,
    find_markers,
    is_drafting_note,
    iter_document_directives,
    quote_blocks,
)

_FRONT = "---\ntitle: T\nquestions:\n  extras:\n    type: boolean\n---\n\n# A {#a}\n\n"

#: What the innermost level holds: placeholders, a choice, fenced code.
_INNER = ['x {{placeholder: who}} {{choose: extras, true="a", false="b"}}', "", "```", "{{placeholder: c}}", "```"]


def _nest(levels: str) -> str:
    """The body nesting ``_INNER`` in *levels*, outermost first: ``L`` a list
    item, ``Q`` a block quote, ``D`` a drafting note."""
    lines = list(_INNER)
    for kind in reversed(levels):
        if kind == "L":
            lines = ["- " + lines[0]] + [f"  {line}" if line else "" for line in lines[1:]]
        else:
            lines = [f"> {line}" if line else ">" for line in lines]
            if kind == "D":
                lines.insert(0, "> [!DRAFTING]")
    return "\n".join(lines)


_L, _Q = MAX_LIST_DEPTH, MAX_QUOTE_DEPTH
SHAPES = {
    "lists at the limit": "L" * _L,
    "lists past the limit": "L" * (_L + 6),
    "quotes at the limit": "Q" * _Q,
    "quotes past the limit": "Q" * (_Q + 4),
    "lists below the limit in a quote": "Q" + "L" * (_L - 2),
    "lists past the limit in a quote": "Q" + "L" * (_L + 6),
    "lists past the limit in quotes": "Q" * (_Q - 1) + "L" * (_L + 6),
    "quotes past the limit in lists": "L" * (_L - 1) + "Q" * (_Q + 4),
    "quotes in lists in quotes": "L" * (_L - 4) + "Q" * _Q + "L" * 10,
    "quotes and lists alternating": "QL" * (_Q + 4),
    "lists and quotes alternating": "LQ" * (_Q + 4),
    "lists in a drafting note": "D" + "L" * (_L + 6),
    "lists in a drafting note in lists": "L" * 10 + "D" + "L" * (_L - 4),
    "drafting notes in lists": "DL" * (_Q + 4),
}


@pytest.fixture(params=list(SHAPES), scope="module")
def source(request) -> str:
    return _FRONT + _nest(SHAPES[request.param]) + "\n"


def _quotes(blocks, depth: int, found: list) -> list:
    for block in blocks:
        if block.kind == "quote":
            found.append((block, depth))
        for item in block.items or []:
            _quotes(item.blocks, depth + 1, found)
    return found


def test_the_reported_quote_holds_its_nested_lists_as_fresh_blocks():
    body = "\n".join("> " + "  " * i + "- x" for i in range(62))
    [quote] = parse(_FRONT + body + "\n").sections[0].blocks
    blocks = quote_blocks(quote)
    cached = list(quote_content(quote.text, 1)[0])
    assert blocks == cached
    assert all(fresh is not shared for fresh, shared in zip(blocks, cached, strict=True))
    depth, block = 0, blocks[0]
    while block.kind == "unordered_list":
        depth += 1
        block = block.items[0].blocks[-1]
    assert depth == 62
    blocks[0].items.clear()  # the caller may change them; the cache keeps its reading
    assert quote_content(quote.text, 1)[0][0].items != []
    assert quote_blocks(quote) == cached


def test_a_drafting_note_holding_lists_past_the_limit_has_fresh_blocks():
    [note] = parse(_FRONT + _nest("D" + "L" * (_L + 6)) + "\n").sections[0].blocks
    assert is_drafting_note(note)
    blocks = drafting_note_blocks(note)
    assert [block.kind for block in blocks] == ["unordered_list"]
    blocks[0].items.clear()
    assert drafting_note_blocks(note)[0].items != []


def test_parse_validate_and_serialize(source):
    document = parse(source)
    validate(document)
    validate(parse(source), final=True)
    text = serialize(document)
    assert serialize(parse(text)) == text


def test_the_dict_round_trip(source):
    document = parse(source)
    assert Document.from_dict(document.to_dict()) == document


def test_where_the_parts_are(source):
    document = parse(source)
    assert document.layout() is not None
    assert document.line_of(0, 0) == 10
    assert body_layout(source.split("---\n\n", 1)[1]) is not None


def test_what_the_text_holds(source):
    document = parse(source)
    assert list(iter_document_directives(document)) != []
    find_markers(document)
    for block in document.sections[0].blocks:
        block_fragments(block)


def test_each_quote_read_into_blocks(source):
    pending = _quotes(parse(source).sections[0].blocks, 0, [])
    while pending:
        quote, depth = pending.pop()
        blocks = drafting_note_blocks(quote, depth=depth) if is_drafting_note(quote) else quote_blocks(quote, depth=depth)
        _quotes(blocks, depth + 1, pending)
        for block in blocks:
            if block.kind == "code":
                code_content(block)


@pytest.mark.parametrize("answers", [{"extras": True, "who": "Ann", "c": "C"}, {"extras": False}])
def test_assembly(source, answers):
    result = parse_template(source).form(answers).assemble()
    if result.ok:
        parse(result.output)
