"""Link reference definitions are blocks (CommonMark §4.7, #92).

``[x]: https://example.com`` is a ``link_definition`` block, in an item or a
quote too; ``collect_link_definitions`` gives the document's by label. The
differential against pandoc and markdown-it over generated documents is in
``test_link_definitions_differential.py``.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest
from helpers_tree import OL, UL, tree

from legaldown import Block, Document, parse, parse_template, serialize, validate
from legaldown.markdown import (
    format_link_definition,
    is_bare_destination,
    no_blank_lines,
    normalize_label,
    split_link_definitions,
)
from legaldown.parser import quote_blocks
from legaldown.syntax import (
    LinkDefinition,
    PlacedLinkDefinition,
    block_fragments,
    collect_link_definitions,
    item_text,
    list_fragments,
    list_items,
    locate_link_definitions,
)

FRONTMATTER = "---\ntitle: T\n---\n\n"


def blocks(body: str) -> list[Block]:
    return parse(FRONTMATTER + body).preamble


def shape(found: list[Block]) -> list[tuple[str, ...]]:
    """Each block as its kind and what it says."""
    return [
        (b.kind, b.label, b.target, b.title) if b.kind == "link_definition" else (b.kind, b.text) for b in found
    ]


def definition(label: str, target: str, title: str = "") -> tuple[str, str, str, str]:
    return ("link_definition", label, target, title)


def assembled(template: str, answers: dict) -> str:
    """The assembled template, its frontmatter and all."""
    result = parse_template(template).form(answers).assemble()
    assert not result.diagnostics, result.diagnostics
    return result.output


def round_trips(body: str) -> Document:
    document = parse(FRONTMATTER + body)
    again = parse(serialize(document))
    assert (again.preamble, again.sections) == (document.preamble, document.sections)
    return document


# ── Parsing ───────────────────────────────────────────────────────


def test_the_example_of_the_issue():
    assert shape(blocks("Text.\n\n[x]: https://example.com\n")) == [
        ("paragraph", "Text."),
        definition("x", "https://example.com"),
    ]


def test_a_definition_does_not_interrupt_a_paragraph():
    assert shape(blocks("a\n[x]: u\n")) == [("paragraph", "a\n[x]: u")]


def test_definitions_of_one_paragraph_are_blocks_each():
    assert shape(blocks("[a]: /one\n[b]: /two 'T'\n[c]: </three>\n")) == [
        definition("a", "/one"),
        definition("b", "/two", "T"),
        definition("c", "/three"),
    ]


@pytest.mark.parametrize(
    ("source", "title"),
    [
        ('[x]: /u "double"', "double"),
        ("[x]: /u 'single'", "single"),
        ("[x]: /u (paren)", "paren"),
        ('[x]: /u "it\'s (fine)"', "it's (fine)"),
        ("[x]: /u 'say \"hi\"'", 'say "hi"'),
        ('[x]: /u "a\\"b"', 'a\\"b'),  # raw: the escape is kept
        ('[x]: /u ""', ""),
        ("[x]: /u\n\"on the next line\"", "on the next line"),
        ('[x]: /u "two\nlines"', "two\nlines"),
        ("[x]:\t/u\t\"tabs\"", "tabs"),
    ],
)
def test_the_forms_of_a_title(source, title):
    assert shape(blocks(source + "\n")) == [definition("x", "/u", title)]
    round_trips(source + "\n")


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("[x]: <url with space>", "url with space"),
        ("[x]: <>", ""),
        ("[x]:\n/below", "/below"),
        ("[x]:    /spaced", "/spaced"),
        ("[x]: a(b)c", "a(b)c"),
        ("[x]: \\(a\\)", "\\(a\\)"),  # raw: not decoded
        ("[x]: http://e.com/?a=1&amp;b=2", "http://e.com/?a=1&amp;b=2"),
        ("[x]: <a\\>b>", "a\\>b"),
    ],
)
def test_the_forms_of_a_destination(source, target):
    assert shape(blocks(source + "\n")) == [definition("x", target)]
    round_trips(source + "\n")


def test_a_label_is_kept_as_written():
    [first, second] = blocks("[ Foo  Bar ]: /a\n[ΑΓΩ\nnext]: /b\n")
    assert (first.label, second.label) == ("Foo  Bar", "ΑΓΩ\nnext")
    round_trips("[ Foo  Bar ]: /a\n[ΑΓΩ\nnext]: /b\n")


def test_a_definition_then_text_in_one_paragraph():
    assert shape(blocks("[x]: u\n[y]: v\nand the text\nafter it\n")) == [
        definition("x", "u"),
        definition("y", "v"),
        ("paragraph", "and the text\nafter it"),
    ]


def test_a_definition_in_an_item_a_nested_item_and_a_quote():
    [lst, quote] = blocks("- [x]: u\n  Text.\n  - [y]: v\n    More.\n\n> [z]: w\n> Quoted.\n")
    [item] = list_items(lst)
    assert shape(item.blocks[:2]) == [definition("x", "u"), ("paragraph", "Text.")]
    [nested] = list_items(item.blocks[2])
    assert shape(nested.blocks) == [definition("y", "v"), ("paragraph", "More.")]
    assert shape(quote_blocks(quote)) == [definition("z", "w"), ("paragraph", "Quoted.")]


def test_a_definition_alone_in_an_item():
    [lst] = blocks("- [x]: u\n- text\n")
    assert [shape(item.blocks) for item in list_items(lst)] == [[definition("x", "u")], [("paragraph", "text")]]


@pytest.mark.parametrize(
    "source",
    [
        '[x]: u "t" junk',
        "[x]: u junk",
        "[x]: <u>(title)",
        "[x]: a)",
        "[x]: a(",
        "[x]:",
        "[x] : u",
        "[x] u",
        "[]: u",
        "[ ]: u",
        "[a[b]: u",
        "[a]b]: u",
        "[" + "a" * 1000 + "]: u",
        "[x]: <a\nb>",
        "[x]: <a<b>",
        "\\[x]: u",
        "[x]: u\x7f",
    ],
)
def test_what_is_no_definition_is_a_paragraph(source):
    assert shape(blocks(source + "\n")) == [("paragraph", source)]


def test_a_label_of_999_characters_is_one():
    assert shape(blocks("[" + "a" * 999 + "]: u\n")) == [definition("a" * 999, "u")]


def test_a_title_with_a_blank_line_is_none_and_the_definition_with_it():
    """CommonMark 0.31: the blank line ends the paragraph, which holds a title that is not closed."""
    assert shape(blocks("[x]: u 'title\n\nwith blank line'\n")) == [
        ("paragraph", "[x]: u 'title"),
        ("paragraph", "with blank line'"),
    ]


def test_a_title_that_does_not_end_its_line_is_dropped_and_the_destination_ends_it():
    """cmark backs up to the destination (Revision 2, #92): the definition stands without
    the title if the destination's line ends there; the rest is a paragraph."""
    assert shape(blocks('[x]: u\n"t" junk\n')) == [definition("x", "u"), ("paragraph", '"t" junk')]
    assert shape(blocks('[x]: u "t" junk\n')) == [("paragraph", '[x]: u "t" junk')]
    assert shape(blocks("[x]: u\n'unterminated\n")) == [definition("x", "u"), ("paragraph", "'unterminated")]


def test_a_line_of_four_columns_is_code_and_a_continuation_is_text():
    assert shape(blocks("    [x]: u\n")) == [("code", "    [x]: u")]
    assert shape(blocks("[x]: u\n    [y]: v\n")) == [definition("x", "u"), definition("y", "v")]


def test_a_definition_before_an_html_tag_line_is_a_paragraph_after_it():
    """The tag cannot interrupt the paragraph the definition is in."""
    assert shape(blocks("[x]: u\n<span>\n")) == [definition("x", "u"), ("paragraph", "<span>")]
    round_trips("[x]: u\n<span>\n")


def test_a_table_with_an_indented_header_row_interrupts_a_paragraph_of_only_definitions():
    """The paragraph ended where the table starts, whether or not text is left of it (Revision 2 of #92)."""
    assert [b.kind for b in blocks("Text.\n    | a |\n|---|\n| 1 |\n")] == ["paragraph", "table"]
    assert [b.kind for b in blocks("[x]: u\n    | a |\n|---|\n| 1 |\n")] == ["link_definition", "table"]
    assert [b.kind for b in blocks("[x]: u\n| a |\n|---|\n| 1 |\n")] == ["link_definition", "table"]
    round_trips("[x]: u\n    | a |\n|---|\n| 1 |\n")


# ── Setext underlines ─────────────────────────────────────────────


def test_an_underline_under_only_a_definition_is_text():
    assert shape(blocks("[foo]: /url\n===\n")) == [definition("foo", "/url"), ("paragraph", "===")]


def test_an_underline_under_only_definitions_that_is_a_rule_is_a_rule():
    assert shape(blocks("[foo]: /url\n---\n")) == [definition("foo", "/url"), ("rule", "")]


def test_text_that_an_underline_followed_reads_on_as_a_paragraph():
    """``[foo]: /url`` then ``===`` then ``[foo]``: cmark has one paragraph ``===\\n[foo]``."""
    assert shape(blocks("[foo]: /url\n===\n[foo]\n")) == [definition("foo", "/url"), ("paragraph", "===\n[foo]")]


def test_a_second_underline_makes_the_text_of_the_first_a_heading():
    document = parse(FRONTMATTER + "[foo]: /url\n===\n===\n")
    assert shape(document.preamble) == [definition("foo", "/url")]
    assert [(section.title, section.level) for section in document.sections] == [("===", 1)]
    document = parse(FRONTMATTER + "[foo]: /url\n===\nbar\n---\n")
    assert [(section.title, section.level) for section in document.sections] == [("=== bar", 2)]


def test_a_heading_after_definitions_is_a_heading_with_its_own_lines():
    document = parse(FRONTMATTER + "[foo]: /url\nbar\n===\n\ntext\n")
    assert shape(document.preamble) == [definition("foo", "/url")]
    [section] = document.sections
    assert (section.title, section.level) == ("bar", 1)
    layout = document.layout()
    # The definition is the preamble's block, on the body's first line; the heading's
    # lines are the two below it, and its marker is on its last text line.
    assert [(b.kind, b.start, b.end) for b in layout.preamble] == [("link_definition", 5, 6)]
    [heading] = layout.sections
    assert (heading.heading.start, heading.heading.end, heading.heading.marker_line) == (6, 8, 6)
    assert document.line_of(0, None, None) == 6
    assert [(b.kind, b.start, b.end) for b in heading.blocks] == [("paragraph", 9, 10)]


def test_a_heading_in_an_item_after_a_definition():
    [lst] = blocks("- [x]: u\n  bar\n  ===\n")
    [item] = list_items(lst)
    assert shape(item.blocks) == [definition("x", "u"), ("heading", "bar")]


def test_a_conditional_section_after_a_definition_keeps_the_definition_out_of_its_unit():
    template = (
        "---\ntitle: T\nquestions:\n  q:\n    type: boolean\n    prompt: Q?\n---\n\n"
        "[foo]: /url\nHeading {when=q}\n=============\n\nBody.\n"
    )
    assert assembled(template, {"q": False}).count("[foo]: /url") == 1
    assert "Heading" not in assembled(template, {"q": False})
    assert "Heading" in assembled(template, {"q": True})


# ── Directives ────────────────────────────────────────────────────


def test_a_definition_with_a_directive_is_paragraph_text():
    assert shape(blocks("[x]: {{placeholder: p}}\n")) == [("paragraph", "[x]: {{placeholder: p}}")]
    assert shape(blocks('[x]: /u "{{placeholder: p}}"\n')) == [("paragraph", '[x]: /u "{{placeholder: p}}"')]
    assert shape(blocks("[x]: <a b>{{date: 2026-01-01}}\n")) == [("paragraph", "[x]: <a b>{{date: 2026-01-01}}")]


def test_a_definition_with_a_reference_is_still_a_ref_block():
    [lifted] = blocks("[x]: {{ref: nope}}\n")
    assert (lifted.kind, lifted.prefix, lifted.target, lifted.suffix) == ("ref", "[x]: ", "nope", "")
    [lifted] = blocks('[x]: /u "{{ref: a}}"\n')
    assert (lifted.kind, lifted.prefix, lifted.suffix) == ("ref", '[x]: /u "', '"')


def test_the_definitions_before_a_directive_are_blocks_and_the_rest_is_text():
    assert shape(blocks("[a]: u\n[b]: {{placeholder: p}}\n[c]: v\n")) == [
        definition("a", "u"),
        ("paragraph", "[b]: {{placeholder: p}}\n[c]: v"),
    ]


def test_a_definition_with_a_broken_reference_is_still_reported():
    result = validate(parse(FRONTMATTER + "# A\n\n[x]: {{ref: nope}}\n"))
    assert "ref-broken" in result.rules()


def test_a_definition_block_holds_no_text_for_the_validator():
    document = parse(FRONTMATTER + "# A\n\n[x]: https://example.com \"Title\"\n")
    [block] = document.sections[0].blocks
    assert block_fragments(block) == []
    from legaldown.definitions import fragment_scopes

    assert fragment_scopes(block) == []
    assert block_fragments(Block(kind="link_definition", label="a", target="{{ref: x}}", title="{{date: y}}")) == []


# ── The collector ─────────────────────────────────────────────────


def collected(body: str) -> dict[str, LinkDefinition]:
    return collect_link_definitions(parse(FRONTMATTER + body))


def test_the_first_definition_wins():
    assert collected("[foo]: /foo-url \"foo\"\n[foo]: /bar-url \"bar\"\n\n[Foo]: /baz-url\n") == {
        "foo": LinkDefinition("/foo-url", "foo")
    }


@pytest.mark.parametrize(
    ("one", "other"),
    [("FOO", "Foo"), ("ΑΓΩ", "αγω"), ("a   b", "A B"), ("a\nb", "A  B"), ("Straße", "STRASSE")],
)
def test_labels_match_with_case_and_whitespace_folded(one, other):
    found = collected(f"[{one}]: /url\n")
    assert normalize_label(other) in found and normalize_label(one) == normalize_label(other)


def test_the_keys_are_the_folded_labels():
    assert list(collected("[Foo  Bar]: /a\n[ΑΓΩ]: /φου\n")) == ["foo bar", "αγω"]


def test_definitions_in_items_and_quotes_count_for_the_document_in_document_order():
    found = collected("- [a]: /item\n  - [b]: /nested\n\n> [c]: /quote\n> > [d]: /deep\n\n[a]: /later\n[e]: /top\n")
    assert found == {
        "a": LinkDefinition("/item", ""),
        "b": LinkDefinition("/nested", ""),
        "c": LinkDefinition("/quote", ""),
        "d": LinkDefinition("/deep", ""),
        "e": LinkDefinition("/top", ""),
    }


def test_only_a_block_that_may_hold_a_definition_is_read_for_them(monkeypatch):
    import legaldown.parser as parser

    read: list[str] = []
    real = parser.quote_content

    def counting(text, depth):
        read.append(text)
        return real(text, depth)

    monkeypatch.setattr(parser, "quote_content", counting)
    body = "> plain\n> quote\n\n- item\n  > in an item\n\n> [a]: /quote\n\n- item\n  > [b]: /in-item\n"
    assert set(collected(body)) == {"a", "b"}
    # The two quotes with no ``]:`` are not read at all; the ones that may hold a definition are.
    assert set(read) == {"[a]: /quote", "[b]: /in-item"}


def test_a_model_built_definition_is_collected_and_an_empty_label_is_not():
    document = parse(FRONTMATTER + "# A\n")
    document.sections[0].blocks += [
        Block(kind="link_definition", label="Built", target="/b", title="T"),
        Block(kind="link_definition", label="  ", target="/blank"),
    ]
    assert collect_link_definitions(document) == {"built": LinkDefinition("/b", "T")}


def test_the_collector_is_derived_each_time():
    document = parse(FRONTMATTER + "[a]: /one\n")
    assert collect_link_definitions(document) == {"a": LinkDefinition("/one", "")}
    document.preamble[0].target = "/two"
    assert collect_link_definitions(document) == {"a": LinkDefinition("/two", "")}


# ── The helpers ───────────────────────────────────────────────────


def test_split_link_definitions_counts_the_lines_it_took():
    found, taken = split_link_definitions(["[a]: u", "[b]:", "v 't", "t'", "[c]: w", "text", "[d]: x"])
    assert [(d.label, d.target, d.title, d.lines) for d in found] == [
        ("a", "u", "", 1), ("b", "v", "t\nt", 3), ("c", "w", "", 1),
    ]
    assert taken == 5


def test_split_link_definitions_reads_lines_without_their_indentation():
    assert split_link_definitions(["   [a]: u", "\t[b]: v"])[1] == 2


@pytest.mark.parametrize(
    ("target", "bare"),
    [("/url", True), ("a(b)c", True), ("a\\)", True), ("a)", False), ("a(", False), ("", False), ("a b", False),
     ("<a>", False), ("a<b", True), ("a\x01b", False), ("a\x7fb", False), ("a\tb", False), ("(" * 33 + ")" * 33, False)],
)
def test_the_destinations_that_are_bare(target, bare):
    assert is_bare_destination(target) is bare


@pytest.mark.parametrize(
    ("label", "target", "title", "written"),
    [
        ("x", "/u", "", "[x]: /u"),
        ("x", "/u", "t", '[x]: /u "t"'),
        ("x", "a b", "", "[x]: <a b>"),
        ("x", "", "", "[x]: <>"),
        ("x", "a)", "", "[x]: <a)>"),
        ("x", "a>b", "", "[x]: a>b"),
        ("x", "a b>c", "", "[x]: <a b\\>c>"),
        ("x", "a b<c", "", "[x]: <a b\\<c>"),
        ("x", "a b\\", "", "[x]: <a b\\\\>"),
        ("x", "a\x01b", "", "[x]: <a\x01b>"),
        ("x", "/u", 'a"b', "[x]: /u 'a\"b'"),
        ("x", "/u", "a\"b'c", "[x]: /u (a\"b'c)"),
        ("x", "/u", "a\"b'c(d", '[x]: /u "a\\"b\'c(d"'),
        ("x", "/u", 'a\\"b', '[x]: /u "a\\"b"'),  # escaped quotes are not unescaped ones
        ("x", "/u", "ends\\", '[x]: /u "ends\\\\"'),
        ("a]b", "/u", "", "[a\\]b]: /u"),
        ("a[b", "/u", "", "[a\\[b]: /u"),
        ("a\\", "/u", "", "[a\\\\]: /u"),
        ("a\\]b", "/u", "", "[a\\]b]: /u"),
        (" x ", "/u", "", "[x]: /u"),
        ("x", "/u", "one\n\ntwo", '[x]: /u "one two"'),
        ("x", "/u", "one\ntwo", '[x]: /u "one\ntwo"'),
    ],
)
def test_format_link_definition(label, target, title, written):
    assert format_link_definition(label, target, title) == written


def test_no_blank_lines():
    assert no_blank_lines("a\n\nb") == "a b"
    assert no_blank_lines("a\n \t\n\nb\nc") == "a b\nc"
    assert no_blank_lines("a\nb") == "a\nb"


# ── Spans and positions ───────────────────────────────────────────


def test_each_definition_is_a_span_with_its_lines():
    document = parse(FRONTMATTER + "Text.\n\n[a]: u\n[b]:\nv\nafter\n")
    layout = document.layout()
    assert [(b.kind, b.start, b.end) for b in layout.preamble] == [
        ("paragraph", 5, 6), ("link_definition", 7, 8), ("link_definition", 8, 10), ("paragraph", 10, 11),
    ]
    assert [document.line_of(None, i, None) for i in range(4)] == [5, 7, 8, 10]


def test_a_definition_in_an_item_and_a_quote_lies_in_its_lines():
    document = parse(FRONTMATTER + "- [a]: u\n  Text.\n\n> [b]: v\n> More.\n")
    layout = document.layout()
    [lst, quote] = layout.preamble
    [item] = lst.items
    assert [(b.kind, b.start, b.end) for b in item.blocks] == [("link_definition", 5, 6), ("paragraph", 6, 7)]
    assert (quote.kind, quote.start, quote.end) == ("quote", 8, 10)
    assert document.line_of(None, 0, 0) == 5


def test_an_edited_document_has_no_layout():
    document = parse(FRONTMATTER + "[a]: u\n")
    assert document.layout() is not None
    document.preamble[0].title = "now with a title"
    assert document.layout() is None
    document = parse(FRONTMATTER + "[a]: u\n")
    document.preamble[0].label = "longer"
    assert document.layout() is None


def test_diagnostics_after_definitions_keep_their_lines():
    result = validate(parse(FRONTMATTER + "# A\n\n[x]: u\n[y]: v\n\nSee {{ref: nope}}.\n"))
    [finding] = [d for d in result.diagnostics if d.rule == "ref-broken"]
    assert finding.line == 10


# ── Assembly ──────────────────────────────────────────────────────


def test_a_definition_over_an_include_line_leaves_an_include_paragraph():
    """A definition is no text, so the line under it is the paragraph's alone (§12.2)."""
    from legaldown.validator.units import iter_include_paragraphs

    document = parse(FRONTMATTER + "# A\n\n[x]: u\n{{include: part.lgd}}\n")
    assert [b.kind for b in document.sections[0].blocks] == ["link_definition", "paragraph"]
    assert [(section, index, directive.positional) for section, index, directive in iter_include_paragraphs(document)] == [
        (0, 1, "part.lgd")
    ]
    template = (
        "---\ntitle: T\n---\n\n# A\n\n[x]: u\n{{include: part.lgd}}\n"
    )
    assembled_template = parse_template(
        template, resolve=lambda path: "Included text.\n" if path == "part.lgd" else None
    ).form({}).assemble()
    assert "[x]: u\n{{include: part.lgd}}\n" in assembled_template.output
    assert assembled_template.files == {"part.lgd": "Included text.\n"}  # read: it is an include paragraph


def test_a_template_with_definitions_assembles_with_its_lines_unchanged():
    template = (
        "---\ntitle: T\nquestions:\n  name:\n    type: text\n    prompt: Name?\n---\n\n# A\n\n"
        "[ref]: https://example.com \"Title\"\n[other]: <a b>\n\n"
        "Hello {{placeholder: name}}, see [ref].\n\n- [item]: /i\n  Text {{placeholder: name}}.\n\n"
        "> [quoted]: /q\n> More.\n"
    )
    text = assembled(template, {"name": "Ann"})
    assert text.split("---\n", 2)[2].strip() == (
        "# A\n\n[ref]: https://example.com \"Title\"\n[other]: <a b>\n\n"
        "Hello Ann, see [ref].\n\n- [item]: /i\n  Text Ann.\n\n> [quoted]: /q\n> More.\n"
    ).strip()


def test_a_conditional_item_after_a_definition_is_one_unit():
    template = (
        "---\ntitle: T\nquestions:\n  q:\n    type: boolean\n    prompt: Q?\n---\n\n# A\n\n"
        "- [x]: /u\n  Optional. {when=q}\n- Always.\n"
    )
    assert "Optional" in assembled(template, {"q": True})
    text = assembled(template, {"q": False})
    assert "Optional" not in text and "Always." in text


# ── Serializer ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "[x]: https://example.com\n",
        "Text.\n\n[x]: u\n[y]: v\nand after\n",
        "[x]: u\n\n[y]: v\n",
        "- [x]: u\n  Text.\n",
        "- [x]: u\n  [y]: v\n  Text.\n  - [z]: w\n    More.\n",
        "> [x]: u\n> [y]: v\n>\n> Text.\n",
        "> - [x]: u\n>   Text.\n",
        '[x]: u "two\nlines"\n',
        "[a\nb]: <c d> 'e'\n",
        "[foo]: /url\n===\n",
        "[foo]: /url\n===\n[foo]\n",
        "[foo]: /url\nbar\n===\n",
        "[foo]: /url\n---\n",
        "[a]: u\n[b]: {{placeholder: p}}\n",
        "[x]: u\n<span>\n",
        "[x]: u\n2. not a list\n",
        "[x]: u\n<span>\n{{ref: a}}\n",
        "[x]: u\n2. z\n{{term: a, label=\"b\"}} c\n",
        "[x]: u\n    indented text\n",
        "# H\n\n[x]: u\n\ntext\n",
    ],
)
def test_a_document_with_definitions_reads_back_the_same(body):
    round_trips(body)


@pytest.mark.parametrize("rest", ["", "more\n", "- d\n", "===\n"])
def test_a_bare_hash_after_a_definition_in_an_item_is_written_under_it(rest):
    # In an item ``#`` alone is a heading, so it gets no backslash under the definition: it is text there.
    body = "- [x]: /u\n      #\n" + "".join(f"      {line}\n" for line in rest.splitlines())
    document = parse(body)
    assert [b.text for b in document.preamble[0].items[0].blocks] == ["", "#" + "".join(f"\n{line}" for line in rest.splitlines())]
    written = serialize(document)
    assert "\\#" not in written
    round_trips(body)
    assert serialize(parse(written)) == written


_FIRST_LINES = [
    "- c", "* c", "+ c", "1. c", "1) c", "# c", "## c", "#", "###", "> c", ">", "```", "~~~", "---", "***", "===",
    "<div>", '<a href="b">', "</span>", "<!-- c", "| a | b |", "[y]: /v", "- ", "1.", "    code", "c",
]
_SECOND_LINES = [None, "more", "- d", "#", "===", "---", "> q", "1. n", "[z]: /w"]


@pytest.mark.parametrize("second", _SECOND_LINES)
@pytest.mark.parametrize("shape", _FIRST_LINES)
@pytest.mark.parametrize("where", ["top level", "list item", "quote"])
def test_what_follows_a_definition_reads_back_the_same_whatever_its_first_line(where, shape, second):
    first, rest = {"top level": ("", ""), "list item": ("- ", "  "), "quote": ("> ", "> ")}[where]
    lines = [first + "[x]: /u", rest + "    " + shape] + ([] if second is None else [rest + "    " + second])
    body = "\n".join(lines) + "\n"
    document = parse(FRONTMATTER + body)
    written = serialize(document)
    again = parse(written)
    assert (again.preamble, again.sections) == (document.preamble, document.sections), (body, written)
    assert serialize(again) == written, (body, written)


@pytest.mark.parametrize("line", ["- c", "# c", "1. c"])
def test_a_paragraph_indented_four_columns_after_a_definition_is_written_under_it(line):
    # What is left of the paragraph reads as the start of a block alone, and is text under the
    # definition only when it is indented: a backslash would give another text back.
    document = parse(f"[x]: /u\n    {line}\n")
    assert [(b.kind, b.text) for b in document.preamble] == [("link_definition", ""), ("paragraph", line)]
    written = serialize(document)
    assert written == f"[x]: /u\n    {line}\n"
    again = parse(written)
    assert [b.text for b in again.preamble] == ["", line]
    assert serialize(again) == written
    round_trips(f"[x]: /u\n    {line}\nmore\n")
    round_trips(f"- [x]: /u\n      {line}\n")


@pytest.mark.parametrize(
    "line",
    ["- b", "1. b", "# h", "## h", "> q", "---", "***", "```", "~~~", "===", "<div>", "    code", "| a |", "+ b"],
)
@pytest.mark.parametrize("where", ["title", "label"])
def test_a_line_inside_a_title_or_label_is_written_so_that_it_continues_it(line, where):
    """Revision 2 of #92: the lines of a title or a label are written as the lines of a
    paragraph are (``_continues``), four columns in where they would end it."""
    source = f'[x]: u "t\n{line}\nz"\n' if where == "title" else f"[a\n{line}\nz]: u\n"
    document = Document.from_dict({"preamble": [{"kind": "link_definition", "label": "x", "target": "u"}]})
    if where == "title":
        document.preamble[0].title = f"t\n{line.strip()}\nz"
    else:
        document.preamble[0].label = f"a\n{line.strip()}\nz"
    written = serialize(document)
    [again] = parse(written).preamble
    assert (again.kind, again.label, again.target, again.title) == (
        "link_definition", document.preamble[0].label, "u", document.preamble[0].title
    ), (source, written)


def test_a_paragraph_that_reads_as_a_definition_is_written_with_a_backslash():
    document = Document.from_dict({"preamble": [{"kind": "paragraph", "text": "[x]: u"}]})
    written = serialize(document)
    assert written.endswith("\n\\[x]: u\n")
    [again] = parse(written).preamble
    assert (again.kind, again.text) == ("paragraph", "\\[x]: u")


def test_a_paragraph_of_definitions_with_text_after_them_is_escaped_whole():
    document = Document.from_dict({"preamble": [{"kind": "paragraph", "text": "[x]: u\n[y]: v\ntext"}]})
    [again] = parse(serialize(document)).preamble
    assert again.kind == "paragraph" and again.text.startswith("\\[x]: u")


def test_a_paragraph_the_parser_leaves_whole_is_not_escaped():
    """A definition with a directive is text, so there is nothing to escape (#92)."""
    document = Document.from_dict({"preamble": [{"kind": "paragraph", "text": "[x]: {{placeholder: p}}"}]})
    assert serialize(document).endswith("\n[x]: {{placeholder: p}}\n")


def test_a_ref_block_that_starts_like_a_definition_keeps_its_directive():
    document = round_trips("[x]: {{ref: nope}}\n")
    assert [b.kind for b in document.preamble] == ["ref"]


def test_a_definition_in_an_item_past_the_depth_limit_is_text_and_is_escaped():
    """At ``MAX_LIST_DEPTH`` an item is one paragraph; like ``# h`` there, ``[x]: u`` is escaped (#92)."""
    from legaldown.parser import MAX_LIST_DEPTH

    document = parse(FRONTMATTER + "- " * (MAX_LIST_DEPTH + 1) + "[x]: u\n")
    inner = document.preamble[0]
    for _ in range(MAX_LIST_DEPTH):
        inner = list_items(inner)[0].blocks[0]
    [deepest] = list_items(inner)[0].blocks
    assert (deepest.kind, deepest.text) == ("paragraph", "[x]: u")
    written = serialize(document)
    assert written.rstrip().endswith("\\[x]: u")
    again = parse(written)
    assert collect_link_definitions(again) == {}


def test_a_definition_with_an_empty_label_writes_nothing():
    document = Document.from_dict({"preamble": [
        {"kind": "paragraph", "text": "a"},
        {"kind": "link_definition", "label": " ", "target": "u"},
        {"kind": "paragraph", "text": "b"},
    ]})
    assert serialize(document).endswith("\n\na\n\nb\n")


@pytest.mark.parametrize(
    ("target", "again"),
    [("a)", "a)"), ("a(", "a("), ("a b", "a b"), ("", ""), ("a\x01b", "a\x01b"), ("a\x7fb", "a\x7fb"),
     ("a<b", "a<b"), ("a>b", "a>b"), ("a b>c", "a b\\>c"), ("a b<c", "a b\\<c"), ("a b\\", "a b\\\\")],
)
def test_a_destination_that_is_no_bare_one_is_written_in_brackets(target, again):
    """Revision 2 of #92: a destination is bare only where the parser reads it so."""
    document = Document.from_dict({"preamble": [{"kind": "link_definition", "label": "x", "target": target}]})
    [read] = parse(serialize(document)).preamble
    assert (read.kind, read.target) == ("link_definition", again)


def test_a_model_with_a_directive_in_a_definition_is_written_as_it_is_and_read_as_text():
    """Accepted (#92): a model built in code is not the parser's, which keeps such a definition text."""
    document = Document.from_dict({"preamble": [{"kind": "link_definition", "label": "x", "target": "{{ref: a}}"}]})
    written = serialize(document)
    assert written.endswith("\n[x]: <{{ref: a}}>\n")
    assert [b.kind for b in parse(written).preamble] == ["ref"]


def test_to_dict_and_from_dict_round_trip():
    document = parse(FRONTMATTER + "[x]: <a b> 'T'\n\n- [y]: v\n  Text.\n")
    data = document.to_dict()
    [first, *_] = data["preamble"]
    assert first == {**first, "kind": "link_definition", "label": "x", "target": "a b", "title": "T"}
    assert Document.from_dict(data).preamble == document.preamble
    assert all("title" in block for block in data["preamble"])


def test_a_dict_without_a_title_loads_with_none():
    data = parse(FRONTMATTER + "[x]: u\n\nText.\n").to_dict()
    for block in data["preamble"]:
        del block["title"]
    document = Document.from_dict(data)
    assert [b.title for b in document.preamble] == ["", ""]


def test_from_dict_mends_what_could_not_be_written():
    block = Document.from_dict({"preamble": [
        {"kind": "link_definition", "label": " a\n\nb  c ", "target": " x\r\ny ", "title": "t\n  \nu"},
    ]}).preamble[0]
    # Blank lines end a paragraph and a destination in brackets holds no line break; spaces in a
    # label are the label's.
    assert (block.label, block.target, block.title) == ("a b  c", "xy", "t u")


def test_other_blocks_have_no_title():
    assert Document.from_dict({"preamble": [{"kind": "paragraph", "text": "a", "title": "t"}]}).preamble[0].title == ""


# ── Items: the first paragraph ────────────────────────────────────


def test_an_item_that_opens_with_definitions_has_its_first_paragraph_after_them():
    """``- [x]: u`` then ``Text {#id}``: the text is the item's first paragraph, where an
    anchor may end (§5.7), and its text (Revision 2 of #92)."""
    document = parse(FRONTMATTER + "# A\n\n- [x]: u\n  Text {#id}\n")
    [lst] = document.sections[0].blocks
    assert [(text, position) for text, position, _items in list_fragments(lst)] == [("Text {#id}", True)]
    assert item_text(list_items(lst)[0]) == "Text {#id}"
    result = validate(document)
    assert [d.rule for d in result.diagnostics if d.rule.startswith("anchor")] == []


def test_a_paragraph_after_a_paragraph_is_not_the_first_though_definitions_come_between():
    document = parse(FRONTMATTER + "# A\n\n- First.\n\n  [x]: u\n\n  Second. {#id}\n")
    [lst] = document.sections[0].blocks
    assert [position for _text, position, _items in list_fragments(lst)] == [True, False]
    assert "anchor-misplaced" in validate(document).rules()


def test_a_condition_on_the_text_after_a_definition_applies_to_the_item():
    document = parse(
        "---\ntitle: T\nquestions:\n  q:\n    type: boolean\n    prompt: Q?\n---\n\n# A\n\n"
        "- [x]: u\n  Text. {when=q}\n"
    )
    assert "anchor-misplaced" not in validate(document).rules()


def test_the_scopes_of_an_items_texts_ignore_the_definitions():
    from legaldown.definitions import fragment_scopes

    document = parse(FRONTMATTER + '# A\n\n- [x]: u\n  "Foo" {{def: foo}} means a.\n\n  [y]: v\n\n  "Bar" {{def: bar}} means b.\n')
    [lst] = document.sections[0].blocks
    assert fragment_scopes(lst) == [0, 1]


def test_definitions_do_not_make_a_circle_or_hide_one():
    text = (
        FRONTMATTER + '# A\n\n- [x]: u\n  "A" {{def: a}} is {{term: b}}.\n\n  [y]: v\n\n  "B" {{def: b}} is {{term: a}}.\n'
    )
    assert "definition-circular" in validate(parse(text)).rules()
    plain = FRONTMATTER + '# A\n\n- [x]: u\n  "A" {{def: a}} is a thing.\n\n  [y]: v\n\n  "B" {{def: b}} is {{term: a}}.\n'
    assert "definition-circular" not in validate(parse(plain)).rules()


# ── Reference-style images (#104) ─────────────────────────────────


def paths(body: str, **options) -> list[tuple[str, int | None]]:
    result = validate(parse(FRONTMATTER + "# A\n\n" + body), **options)
    return [(d.rule, d.line) for d in result.diagnostics if d.rule.startswith("path-")]


@pytest.mark.parametrize(
    "body",
    [
        "![a][r]\n\n[r]: /etc/passwd\n",
        "![a][r]\n\n[R]: /etc/passwd\n",
        "![a][]\n\n[a]: /etc/passwd\n",
        "![a]\n\n[a]: /etc/passwd\n",
        "![a][r]\n\n[r]: <C:/x.png>\n",
        "![a][r]\n\n[r]: https://example.com/x.png\n",
        "![a][r]\n\n[r]: %2Fetc/x\n",
        "![a][r]\n\n[r]: file:///x\n",
        "![a][r]\n\n[r]: //host/x.png\n",
        "![a][r]\n\n[r]: \\\\server\\share\n",
        "- ![a][r]\n\n  [r]: /etc/passwd\n",
        "![a][r]\n\n> [r]: /etc/passwd\n",
        "> ![a][r]\n\n[r]: /etc/passwd\n",
        "![a][r]\n\n- [r]: /etc/passwd\n",
        "![a](/e) and ![b][r]\n\n[r]: /etc/passwd\n",
    ],
)
def test_a_reference_style_image_with_an_absolute_path_is_path_not_relative(body):
    found = paths(body)
    assert [rule for rule, _line in found if rule == "path-not-relative"], found


def test_the_path_is_reported_once_where_it_is_written():
    found = paths("![a][r] and ![b][r] and ![c][R]\n\n[r]: /etc/passwd\n")
    assert found == [("path-not-relative", 9)]
    assert paths("![a][r]\n\n> [r]: /etc/passwd\n") == [("path-not-relative", 9)]
    assert paths("![a][r]\n\n- text\n\n  [r]: /etc/passwd\n") == [("path-not-relative", 11)]


def test_a_reference_style_image_leaving_the_root_is_path_outside_root():
    assert paths("![a][r]\n\n[r]: ../x.png\n") == []
    assert paths("![a][r]\n\n[r]: ../x.png\n", document_root="/tmp/root") == [("path-outside-root", 9)]
    assert paths("![a][]\n\n[a]: <../a b.png> 'T'\n", document_root="/tmp/root") == [("path-outside-root", 9)]
    assert paths("![a][r]\n\n[r]: %2E%2E/x.png\n", document_root="/tmp/root") == [("path-outside-root", 9)]
    assert paths("![a][r]\n\n[r]: x.png?p=/../..\n", document_root="/tmp/root") == []
    assert paths("![a][r]\n\n[r]: img/x.png\n", document_root="/tmp/root") == []


@pytest.mark.parametrize(
    "body",
    [
        "[a][r]\n\n[r]: /etc/passwd\n",  # a link
        "[a]\n\n[a]: /etc/passwd\n",
        "![a][r]\n",  # no definition
        "![a][r]\n\n[s]: /etc/passwd\n",  # another label
        "![a][r]\n\n[a]: /etc/passwd\n",  # the text's label is not used when another follows
        "![a][ ]\n\n[a]: /etc/passwd\n",
        "\\![a][r]\n\n[r]: /etc/passwd\n",
        "`![a][r]`\n\n[r]: /etc/passwd\n",
        "![a] [r]\n\n[r]: /etc/passwd\n",  # a space between: a shortcut image of the text, then text
        "[r]: /etc/passwd\n",  # a definition no image names
        "![a][r]\n\n[r]: {{placeholder: p}}\n",  # text: the path is not final
        "![a [b] c]\n\n[a [b] c]: /etc/passwd\n",
        "![a][r]\n\n    [r]: /etc/passwd\n",  # code
    ],
)
def test_what_names_no_definition_checks_nothing(body):
    assert paths(body, document_root="/tmp/root") == []


def test_a_shortcut_image_when_the_text_is_followed_by_no_label():
    assert paths("![a] [b]\n\n[a]: /etc/passwd\n") == [("path-not-relative", 9)]
    assert paths("![a](bad\n\n[a]: /etc/passwd\n") == [("path-not-relative", 9)]


def test_an_inline_image_is_not_a_reference_image():
    assert paths("![a](ok.png)\n\n[a]: /etc/passwd\n") == []


def test_the_first_definition_of_a_label_is_the_image_s():
    assert paths("![a][r]\n\n[r]: ok.png\n[r]: /etc/passwd\n") == []
    assert paths("![a][r]\n\n[r]: /etc/passwd\n[r]: ok.png\n") == [("path-not-relative", 9)]


def test_a_reference_style_image_in_a_heading_is_checked():
    document = parse(FRONTMATTER + "# A ![a][r]\n\n[r]: /etc/passwd\n")
    assert [(d.rule, d.line) for d in validate(document).diagnostics if d.rule.startswith("path-")] == [
        ("path-not-relative", 7)
    ]


def test_a_fragment_on_its_own_is_checked_as_a_document_is():
    """#39: its definition and its images are its own; nothing is the host's to settle."""
    fragment = parse("## Scope\n\n![a][r]\n\n[r]: /etc/x\n")
    result = validate(fragment, fragment=True)
    assert [(d.rule, d.line, d.host_dependent) for d in result.diagnostics] == [("path-not-relative", 5, False)]
    assert result.rules() == validate(fragment, fragment=True, fragment_kind="include").rules()


def test_a_document_with_unreadable_frontmatter_is_checked_to_the_line():
    """#43: the body is read as it is, and its lines are the file's."""
    document = parse("---\ntitle: [\n---\n\n# A\n\n![a][r]\n\n- [r]: /etc/x\n", on_frontmatter_error="keep")
    assert document.frontmatter_error is not None
    result = validate(document)
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule.startswith("path-")] == [("path-not-relative", 9)]
    assert [b.kind for b in document.sections[0].blocks[1:]] == ["unordered_list"]


# ── The verdicts of the insertion boundary (§15.7.3) ─────────────


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("[label]: {{placeholder: p}}\n", True),
        ("[Note]: {{placeholder: p}} shall pay.\n", False),
        ("[a]: u\n[label]: {{placeholder: p}}\n", True),
        ("- [label]: {{placeholder: p}}\n", True),
        # Unchanged by #119: nothing of the definition's destination, and no title slot.
        ("[a]: u {{placeholder: p}} v\n", False),
        ("[a]: <x {{placeholder: p}} y\n", False),
        ("[a]: <x\n{{placeholder: p}} y>\n", False),
        ("Text [a]: <x {{placeholder: p}} y>\n", False),
        ("[a]: <x {{placeholder: p}} y> shall pay.\n", False),
        ("[a]: u\n[b]: v\n", False),
        ("[a]: u {{ref: q}}\n", False),
        ("[a]: u {{ref: q}} {{placeholder: p}}\n", False),  # only an insertion could be the title
        ("[a]: u {{placeholder: p}} {{ref: q}}\n", False),
        # An ATX heading or a table cell holds no definition: read as before #119 (a bare destination, a closed title).
        ("# [a]: u {{placeholder: p}}\n", False),
        ("# [a]: <x {{placeholder: p}} y>\n", False),
        ("- # [a]: u {{placeholder: p}}\n", False),
        ("> # [a]: u {{placeholder: p}}\n", False),
        ("| x |\n|---|\n| [a]: u {{placeholder: p}} |\n", False),
        ("| [a]: <x {{placeholder: p}} y> |\n|---|\n| z |\n", False),
        ("# [a]: {{placeholder: p}}\n", True),
        ("| x |\n|---|\n| [a]: {{placeholder: p}} |\n", True),
    ],
)
def test_the_insertion_boundary_verdicts_of_a_definition_line_are_unchanged(body, error):
    template = "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n---\n\n# A\n\n" + body
    rules = validate(parse(template)).rules()
    assert ("insertion-boundary" in rules) is error


@pytest.mark.xfail(strict=True, reason="a title is not read as one an answer could make, nor a destination on the next line (#129)")
@pytest.mark.parametrize(
    "body",
    [
        '[a]: u "t" {{choose: q}}\n',  # an empty {{choose:}} phrase makes it a definition
        '[a]: u {{choose: q}} "t"\n',  # the same: it becomes the title's place
        '[a]: u {{placeholder: p}}"\n',  # an answer that opens a quote makes it a title
        "[a]:\n{{placeholder: p}}\n",  # an answer makes it a definition
        "[b]: {{placeholder: p}}\n[a]: u\n===\n",  # the parser joins the lines of a setext heading: one line
    ],
)
def test_an_insertion_after_a_title_or_in_a_destination_on_the_next_line_is_off_limits(body):
    template = "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n  q:\n    type: boolean\n    prompt: Q?\n---\n\n# A\n\n" + body
    assert "insertion-boundary" in validate(parse(template)).rules()


@pytest.mark.parametrize(
    ("body", "lines"),
    [
        ("[a]: <x {{placeholder: p}} y>\n", [14]),  # #119: blanks in a <…> destination
        ("[a]: u {{placeholder: p}}\n", [14]),  # #119: where an answer could be the title
        ("[a]: <x y> {{placeholder: p}}\n", [14]),
        ("[a]: <{{placeholder: p}} y>\n", [14]),
        ('[a]: <x {{placeholder: p}} y> "t"\n', [14]),
        ("[a]: <x \\> {{placeholder: p}} y>\n", [14]),
        ("[a]: u {{placeholder: p}} {{placeholder: p}}\n", [14, 14]),
        ("[a]: u \t{{choose: q}}  \n", [14]),
        ("- [a]: u {{placeholder: p}}\n", [14]),
        ("> [a]: <x {{placeholder: p}} y>\n", [14]),
        ("[a]: <x y>\n[b]: <{{placeholder: p}} z>\n", [15]),
        # A setext heading's text holds a definition: CommonMark takes it out of the paragraph before the underline.
        ("[a]: u {{placeholder: p}}\n===\n", [14]),
        ("[a]: u {{placeholder: p}}\n---\n", [14]),
        ("- [a]: u {{placeholder: p}}\n  ===\n", [14]),
        ("> [a]: u {{placeholder: p}}\n> ===\n", [14]),
        # An over-approximation: a line that continues a paragraph is read like any other (§15.7.3 is per line).
        ("Text\n[a]: u {{placeholder: p}}\n", [15]),
        ("[b]: v\nText\n[a]: <x {{placeholder: p}} y>\n", [16]),
        ("{{choose: q}}\n[a]: u {{placeholder: p}}\n", [15]),
        ("[b]: v {{placeholder: p}}\n[a]: u {{placeholder: p}}\n", [14, 15]),
    ],
)
def test_an_insertion_in_the_destination_of_a_definition_is_off_limits(body, lines):
    template = "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n  q:\n    type: boolean\n    prompt: Q?\n---\n\n# A\n\n" + body
    result = validate(parse(template))
    assert [d.line for d in result.diagnostics if d.rule == "insertion-boundary"] == lines


def test_a_heading_of_a_model_without_a_source_is_read_as_an_atx_one():
    """Where it is not known how a heading is written (the document no longer fits its source), it is the ATX
    form that a model is saved in: it holds no definition."""
    template = "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n---\n\n# A\n\n- [a]: u {{placeholder: p}}\n  ===\n"
    document = parse(template)
    assert "insertion-boundary" in validate(document).rules()
    document.source_map = None
    assert "insertion-boundary" not in validate(document).rules()


# ── Known differences from CommonMark (CONFORMANCE.md) ────────────


@pytest.mark.parametrize("body", ['1. [a]: /u\n  "t"\n', '1. [a]: /u\n   "t"\n', '1. [a]: /u\n"t"\n'])
def test_a_title_on_a_lazy_line_belongs_to_the_definition(body):
    """The lazy line is paragraph text (CommonMark 5.2), and a definition is read from the closed paragraph (4.7),
    as cmark does: markdown-it agrees. pandoc (commonmark-hs, which reads per line) leaves the title out (#111)."""
    [listed] = blocks(body)
    assert [shape(item.blocks) for item in list_items(listed)] == [[definition("a", "/u", "t")]]


_DEFINITION = ("link_definition",)
_UNDERLINE = [_DEFINITION, "===\nlazy"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="a container's open paragraph does not know that definitions leave an underline as text (#92, #111)",
)
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("> [x]: u\n> ===\nlazy\n", [("quote", _UNDERLINE)], id="quote"),
        pytest.param("- [x]: u\n  ===\nlazy\n", [(UL, [_UNDERLINE])], id="bullet-item"),
        pytest.param("1. [x]: u\n   ===\nlazy\n", [(OL, [_UNDERLINE])], id="ordered-item"),
        pytest.param("> - [x]: u\n>   ===\n> lazy\n", [("quote", [(UL, [_UNDERLINE])])], id="item-in-quote-quoted-lazy"),
        pytest.param("> - [x]: u\n>   ===\nlazy\n", [("quote", [(UL, [_UNDERLINE])])], id="item-in-quote-unquoted-lazy"),
        pytest.param("> > [x]: u\n> > ===\nlazy\n", [("quote", [("quote", _UNDERLINE)])], id="quote-in-quote"),
        pytest.param("- > [x]: u\n  > ===\nlazy\n", [(UL, [[("quote", _UNDERLINE)]])], id="quote-in-item"),
    ],
)
def test_a_line_after_an_underline_under_only_definitions_in_a_container_continues_the_paragraph(body, expected):
    """CommonMark: ``===`` there starts a paragraph in the container, which the line without its marker continues.
    Here the container has closed its paragraph at the underline (CONFORMANCE.md)."""
    assert [tree(block) for block in blocks(body)] == expected


# ── Linear time (#94) ─────────────────────────────────────────────


def test_many_definitions_images_and_labels_are_linear():
    """One generous budget, at a size where reading each from the start of the paragraph, or
    scanning each label to the end, took many times as long."""
    import time

    count = 20000
    cases = {
        "definitions in a paragraph": "\n".join(f"[a{i}]: /u{i} 'T'" for i in range(count)),
        "definitions in items": "\n".join(f"- [a{i}]: /u{i}\n  text" for i in range(count // 4)),
        "unclosed labels": "\n\n".join("[" + "a" * 900 for _ in range(count // 4)),
        "labels that are brackets": "[" * count + "\n" + "]" * count,
        "images naming definitions": "\n".join(f"![a{i}][r{i}]" for i in range(count // 2))
        + "\n\n"
        + "\n".join(f"[r{i}]: img{i}.png" for i in range(count // 2)),
        "images and a long label": "![a][" + "x " * count + "\n" + "![a]" * 100,
    }
    for name, body in cases.items():
        start = time.perf_counter()
        validate(parse(FRONTMATTER + "# A\n\n" + body + "\n"), document_root="/tmp/root")
        assert time.perf_counter() - start < 15, name


# ── Review: reference-style images, where they are reported ───────


def test_definitions_in_the_preamble_and_in_a_section_are_both_checked():
    """The preamble's index is None, which does not sort with a section's."""
    found = paths("![a][r]\n\n[r]: /e1\n")
    assert found == [("path-not-relative", 9)]
    document = parse(FRONTMATTER + "![a][r]\n\n[r]: /e1\n\n# A\n\n![b][s]\n\n[s]: /e2\n")
    result = validate(document)
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule.startswith("path-")] == [
        ("path-not-relative", 7), ("path-not-relative", 13)
    ]


def test_a_finding_is_at_the_line_of_the_definition_block_not_of_its_text():
    # A label with spaces around it: the text of the source is not the label.
    assert paths("![a][ foo ]\n\n[ foo ]: /etc/x\n") == [("path-not-relative", 9)]
    assert paths("![a][foo]\n\n[   foo   ]: /etc/x\n") == [("path-not-relative", 9)]
    # An image that names the label earlier in the same top-level block.
    assert paths("- ![a][foo]\n\n  [foo]: /etc/x\n") == [("path-not-relative", 9)]
    assert paths("- text\n\n  ![a](ok.png) ![b][r]\n\n  [r]: /etc/x\n") == [("path-not-relative", 11)]
    assert paths("> ![c][q]\n>\n> [q]: /etc/y\n") == [("path-not-relative", 9)]
    assert paths("- a\n\n  - ![c][q] [q]\n\n    [q]: /etc/y\n") == [("path-not-relative", 11)]
    # A label over two lines, found at its first line.
    assert paths("![a][r foo]\n\n[r\nfoo]: /etc/x\n") == [("path-not-relative", 9)]


def test_definitions_of_one_block_are_reported_in_document_order():
    document = parse(FRONTMATTER + "# A\n\n- ![a][b] ![c][a]\n\n  [a]: /etc/a\n\n  [b]: /etc/b\n")
    result = validate(document)
    assert [(d.line, d.message.split("'")[1]) for d in result.diagnostics if d.rule == "path-not-relative"] == [
        (9, "/etc/a"), (11, "/etc/b")
    ]


def test_the_finding_names_the_label_with_its_spaces_collapsed():
    result = validate(parse(FRONTMATTER + "# A\n\n![a][r foo]\n\n[r\n  foo]: /etc/x\n"))
    [finding] = [d for d in result.diagnostics if d.rule == "path-not-relative"]
    assert "[r foo]" in finding.message and "\n" not in finding.message


def test_definitions_are_only_looked_for_when_an_image_is(monkeypatch):
    from legaldown import definitions

    calls = []
    real = definitions.locate_link_definitions
    monkeypatch.setattr(definitions, "locate_link_definitions", lambda document: calls.append(1) or real(document))
    validate(parse(FRONTMATTER + "# A\n\n[x]: u\n\nText [x] and a [link](here).\n"))
    assert calls == []
    validate(parse(FRONTMATTER + "# A\n\n[x]: u\n\n![a][x] and ![b][x]\n\n- ![c][x]\n"))
    assert calls == [1]


def test_a_text_is_scanned_once_for_its_inline_and_its_reference_images(monkeypatch):
    from legaldown.validator import paths as paths_module

    seen = []
    real = paths_module._closing_brackets
    monkeypatch.setattr(paths_module, "_closing_brackets", lambda view: seen.append(view) or real(view))
    validate(parse(FRONTMATTER + "# A\n\n![a](x.png) ![b][r] ![c]\n\n[r]: y.png\n"))
    assert len(seen) == 1


def test_locate_link_definitions_gives_the_blocks_and_where_they_are():
    document = parse(FRONTMATTER + "[a]: /p\n\n# A\n\n[b]: /one\n\n- text\n\n  [c]: /two\n  [d]: /three\n\n> [B]: /later\n")
    found = locate_link_definitions(document)
    assert list(found) == ["a", "b", "c", "d"]
    assert [(each.section, each.block, each.ordinal, each.definition.target) for each in found.values()] == [
        (None, 0, 0, "/p"), (0, 0, 0, "/one"), (0, 1, 0, "/two"), (0, 1, 1, "/three")
    ]
    assert isinstance(found["a"], PlacedLinkDefinition)
    assert {key: LinkDefinition(each.definition.target, each.definition.title) for key, each in found.items()} == (
        collect_link_definitions(document)
    )


def test_the_locator_gives_the_line_of_a_definitions_own_block():
    from legaldown.positions import Locator

    document = parse(FRONTMATTER + "# A\n\n- text\n\n  [c]: /two\n  [d]: /three\n\n> text\n>\n> [e]: /four\n")
    where = Locator(document)
    assert [where.definition(0, 0, n) for n in range(3)] == [9, 10, None]  # in a list item
    assert [where.definition(0, 1, n) for n in range(2)] == [14, None]  # in a quote
    document.sections[0].blocks[0].items[0].blocks[1].label = "changed"
    assert Locator(document).definition(0, 0, 0) is None  # no longer the source's shape


# ── Review: tight items, and what is not read back as a definition ─


@pytest.mark.parametrize(
    "body",
    [
        "- [x]: u\n  b\n- c\n",
        "- [x]: u\n  [y]: v\n  b\n- c\n",
        "- [x]: u\n  [y]: v\n- c\n",
        "- [x]: u\n  - n\n- c\n",
        "- [x]: u\n  > q\n- c\n",
        "- [x]: u\n  ```\n  c\n  ```\n- d\n",
        "- [x]: u\n  b\n  - n\n- c\n",
        "1. [x]: u 't'\n   b\n2. c\n",
        "- [x]: u\n  <span>\n- c\n",
        "- [a]: u\n  [b]: {{placeholder: p}}\n  c\n- d\n",
    ],
)
def test_a_definition_and_the_block_after_it_are_tight_in_an_item(body):
    written = serialize(parse(FRONTMATTER + body)).split("---\n\n", 1)[1]
    assert "\n\n" not in written.rstrip("\n"), written  # no blank line: the list stays tight
    round_trips(body)


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
@pytest.mark.parametrize(
    "body",
    [
        "- [x]: u\n  b\n- c\n",
        "- [x]: u\n  [y]: v\n  b\n- c\n\n[x] [y]\n",
        "- [x]: u\n  - n\n- c\n",
        "- [x]: u\n  > q\n- c\n",
        "- [x]: u\n  ```\n  c\n  ```\n- d\n",
    ],
)
def test_a_tight_list_is_tight_in_html_after_it_is_written(body):
    """pandoc renders a loose list's items in paragraphs: the HTML says what a reader sees."""
    def html(text: str) -> str:
        return subprocess.run(
            ["pandoc", "-f", "commonmark", "-t", "html"], input=text, capture_output=True, text=True, check=True
        ).stdout

    written = serialize(parse(FRONTMATTER + body)).split("---\n\n", 1)[1]
    assert html(written) == html(body)


def test_a_title_like_line_after_a_definition_is_not_made_its_title_by_writing_them_together():
    document = Document.from_dict({"preamble": [{"kind": "unordered_list", "items": [{"blocks": [
        {"kind": "link_definition", "label": "x", "target": "u"},
        {"kind": "paragraph", "text": '"not a title"'},
    ]}]}]})
    [item] = list_items(parse(serialize(document)).preamble[0])
    assert [(b.kind, b.title or b.text) for b in item.blocks] == [("link_definition", ""), ("paragraph", '"not a title"')]


def test_a_definition_the_parser_would_not_read_stands_apart():
    """Review of #92: a model-built definition with a directive in it is read back as text, so it is
    written as a paragraph of its own, never glued to the block before or after it."""
    texts = [{"kind": "link_definition", "label": "x", "target": "{{ref: a}}"}]
    for before, after in [
        ({"kind": "link_definition", "label": "a", "target": "u"}, {"kind": "paragraph", "text": "<span>"}),
        ({"kind": "paragraph", "text": "one"}, {"kind": "paragraph", "text": "two"}),
    ]:
        document = Document.from_dict({"preamble": [before, *texts, after]})
        written = serialize(document).split("---\n\n", 1)[1]
        assert "\n\n[x]: <{{ref: a}}>\n\n" in "\n\n" + written
        assert len(parse(serialize(document)).preamble) == 3
    # The same in an item: no tight list is made of text and a definition.
    document = Document.from_dict({"preamble": [{"kind": "unordered_list", "items": [{"blocks": [
        {"kind": "link_definition", "label": "a", "target": "u"}, *texts, {"kind": "paragraph", "text": "after"},
    ]}]}]})
    [item] = list_items(parse(serialize(document)).preamble[0])
    # (A directive in an item stays in its text: the paragraph is not a ``ref`` block.)
    assert [(b.kind, b.text) for b in item.blocks] == [
        ("link_definition", ""), ("paragraph", "[x]: <{{ref: a}}>"), ("paragraph", "after")
    ]


def test_a_definition_with_an_overlong_label_stands_apart_too():
    document = Document.from_dict({"preamble": [
        {"kind": "link_definition", "label": "a" * 1000, "target": "u"},
        {"kind": "paragraph", "text": "after"},
    ]})
    assert len(parse(serialize(document)).preamble) == 2


# ── Last round: order among images, no-break spaces ───────────────


def test_a_definition_is_reported_where_it_lies_among_the_images_of_its_block():
    """The body of the review: an inline image before the definition's image is reported first."""
    document = parse(FRONTMATTER + "# A\n\n- ![a](/abs1.png) ![b][r]\n- [r]: /abs2.png\n")
    found = [(d.line, d.message.split("'")[1]) for d in validate(document).diagnostics if d.rule == "path-not-relative"]
    assert found == [(7, "/abs1.png"), (8, "/abs2.png")]
    # And after the definition, an image that comes later in the block is reported later.
    document = parse(FRONTMATTER + "# A\n\n- ![b][r]\n- [r]: /abs2.png\n- ![a](/abs3.png)\n")
    found = [(d.line, d.message.split("'")[1]) for d in validate(document).diagnostics if d.rule == "path-not-relative"]
    assert found == [(8, "/abs2.png"), (9, "/abs3.png")]
    # Several definitions of one block, with images between: in the order of the source.
    document = parse(
        FRONTMATTER + "# A\n\n- ![a][x]\n\n  [x]: /a\n- ![b](/b.png)\n- ![c][y]\n\n  [y]: /c\n"
    )
    found = [d.message.split("'")[1] for d in validate(document).diagnostics if d.rule == "path-not-relative"]
    assert found == ["/a", "/b.png", "/c"]


def test_a_definition_in_the_preamble_is_placed_with_the_fragments_before_it():
    found = locate_link_definitions(parse(FRONTMATTER + "Text.\n\n- one\n\n  [a]: /a\n  two\n\n  [b]: /b\n"))
    assert [(each.block, each.ordinal, each.fragment) for each in found.values()] == [(1, 0, 1), (1, 1, 2)]


def test_a_label_in_a_finding_collapses_only_commonmark_whitespace():
    """A no-break space is text: ``str.split`` would take it for a space."""
    from legaldown.markdown import collapse_label_whitespace

    assert collapse_label_whitespace("  a \t\n b\u00a0\u00a0c ") == "a b\u00a0\u00a0c"
    document = parse(FRONTMATTER + "# A\n\n![a][r\u00a0\u00a0foo]\n\n[r\u00a0\u00a0foo]: /etc/x\n")
    [finding] = [d for d in validate(document).diagnostics if d.rule == "path-not-relative"]
    assert "[r\u00a0\u00a0foo]" in finding.message


def test_a_label_with_a_no_break_space_is_a_different_label():
    assert normalize_label("a\u00a0b") != normalize_label("a b")
