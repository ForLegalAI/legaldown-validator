"""Link reference definitions are blocks (CommonMark §4.7, #92).

``[x]: https://example.com`` is a ``link_definition`` block, in an item or a
quote too; ``collect_link_definitions`` gives the document's by label. The
differential against pandoc and markdown-it over generated documents is in
``test_link_definitions_differential.py``.
"""
from __future__ import annotations

import pytest

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
    block_fragments,
    collect_link_definitions,
    item_text,
    list_fragments,
    list_items,
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
        "[x]: u\n    indented text\n",
        "# H\n\n[x]: u\n\ntext\n",
    ],
)
def test_a_document_with_definitions_reads_back_the_same(body):
    round_trips(body)


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


# ── The verdicts of the insertion boundary (§15.7.3) ─────────────


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ("[label]: {{placeholder: p}}\n", True),
        ("[Note]: {{placeholder: p}} shall pay.\n", False),
        ("[a]: u\n[label]: {{placeholder: p}}\n", True),
        ("- [label]: {{placeholder: p}}\n", True),
    ],
)
def test_the_insertion_boundary_verdicts_of_a_definition_line_are_unchanged(body, error):
    template = "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n---\n\n# A\n\n" + body
    rules = validate(parse(template)).rules()
    assert ("insertion-boundary" in rules) is error


# ── Known differences from CommonMark (CONFORMANCE.md) ────────────


@pytest.mark.xfail(strict=True, reason="a quote's open paragraph does not know that definitions leave an underline as text (#92)")
def test_a_line_after_an_underline_under_only_definitions_in_a_quote_continues_the_paragraph():
    """CommonMark: ``===`` there starts a paragraph in the quote, which the unquoted line continues."""
    [quote] = blocks("> [foo]: /url\n> ===\nlazy\n")
    assert shape(quote_blocks(quote)) == [definition("foo", "/url"), ("paragraph", "===\nlazy")]
