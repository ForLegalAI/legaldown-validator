"""The path rules and the link reference definitions that pandoc and markdown-it read (#121), pinned by hand.

The model reads a definition as the specification does, and a destination with a control character in it is paragraph
text. pandoc (``-f commonmark``) and markdown-it read it as a definition, and an image that names it is displayed, so
the path rules judge the definitions of each of the three readings (``markdown.Reading``): a definition that any
reading's image names is judged once, where it is written, at its first of the label for that reading. The documents
generated against the renderers are in ``test_path_definitions_differential.py``; these are the pitfalls, one by one."""
from __future__ import annotations

import re
import subprocess
import time

import pytest
from helpers_renderers import needs_markdown_it, needs_pandoc

from legaldown import Document, parse, validate
from legaldown.markdown import Reading, definitions_read_as, label_key, link_definitions_reading
from legaldown.models import Block, Section
from legaldown.parser import quote_content
from legaldown.positions import Locator
from legaldown.validator import path_definitions

_FRONT = "---\ntitle: T\n---\n\n# A {#a}\n\n"  # the first line of a body is line 7


def _document(body: str) -> Document:
    return parse(f"{_FRONT}{body}\n", filename="t.lgd")


def _found(body: str) -> list[tuple[str, int | None]]:
    return [(d.rule, d.line) for d in validate(_document(body)).diagnostics if d.rule.startswith("path-")]


def _paths(body: str) -> list[str]:
    """The paths the findings name, in order."""
    return [m.group(1) for d in validate(_document(body)).diagnostics if (m := re.match(r"File reference '(.*?)' \(", d.message, re.S))]


# ── First definition wins, for each reading ───────────────────────


def test_the_first_definition_a_reading_reads_is_the_one_its_image_uses():
    # pandoc and markdown-it read the first (a DEL in its bare destination), so it is what they display.
    assert _found("![a][r]\n\n[r]: /etc/p\x7f\n[r]: ok.png") == [("path-not-relative", 9)]
    # The specification reads the second, which is fine: but the first is displayed by the others.
    assert _found("![a][r]\n\n[r]: ok.png\n[r]: /etc/p\x7f") == []
    # A definition no image names is no file reference.
    assert _found("![a][r]\n\n[u]: /etc/x\x7f\n\n[r]: ok.png") == []
    assert _found("[r]: /etc/p\x7f\n\n![a][r]") == [("path-not-relative", 7)]


def test_the_definition_the_specification_reads_is_judged_though_a_reading_names_an_earlier_one():
    """pandoc and markdown-it use the first here, a renderer that reads as the specification does the second: both
    are judged, the second as it always was."""
    # (A definition cannot follow one in a paragraph that the specification does not read: the second is a line of it.)
    found = validate(_document("![a][r]\n\n[r]: /etc/p\x7f\n\n[r]: /etc/q")).diagnostics
    assert [d.line for d in found if d.rule == "path-not-relative"] == [9, 11]
    assert _paths("![a][r]\n\n[r]: /etc/p\x7f\n\n[r]: /etc/q") == ["/etc/p\x7f", "/etc/q"]
    assert _found("![a][r]\n\n[r]: /etc/p\x7f\n[r]: /etc/q") == [("path-not-relative", 9)]


def test_a_definition_named_by_every_reading_is_judged_once():
    assert _found("![a][r] ![r][] ![r]\n\n[r]: /etc/p") == [("path-not-relative", 9)]
    assert _found("![a][r] ![r][] ![r]\n\n[r]: /etc/p\x7f") == [("path-not-relative", 9)]


def test_a_definition_that_is_paragraph_text_to_the_model_is_found_at_its_own_line():
    body = "Intro.\n\n[a]: ok.png\n[b]: /etc/b\x01\n[c]: /etc/c\n\n![x][b] ![y][c]"
    assert _found(body) == [("path-not-relative", 10), ("path-not-relative", 11)]


# ── The structures definitions are in ─────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        "[r]: /etc/passwd\x7f\n===\n\n![a][r]",
        "[a]: ok.png\n[r]: /etc/e\x7f\ntext\n===\n\n![a][r]",
        "- [r]: /etc/passwd\x7f\n  ===\n\n![a][r]",
        "> [r]: /etc/passwd\x7f\n> ---\n\n![a][r]",
        "- item\n\n  [r]: /etc/passwd\x01\n  text\n  ---\n\n![a][r]",
        "> - [r]: /etc/passwd\x7f\n>   ===\n\n![a][r]",
    ],
)
def test_a_definition_is_read_under_a_setext_underline_as_the_renderers_read_it(body):
    """pandoc peels the definitions off a setext heading, where the specification reads the heading's text."""
    assert [rule for rule, _line in _found(body)] == ["path-not-relative"], body


def test_a_definition_is_read_in_a_heading_of_the_model():
    found = _found("[r]: /etc/p\x7f\n---\n\n![a][r]")
    assert found == [("path-not-relative", 7)]


@pytest.mark.parametrize("body", ["# [r]: /etc/e\x7f\n\n![a][r]", "## [r]: /etc/e\x01 \"t\"\n\n![a][r]", "text\n[r]: /etc/e\x7f\n\n![a][r]"])
def test_what_no_renderer_reads_as_a_definition_is_not_judged(body):
    # An ATX heading, and a line that is a paragraph's second (a definition cannot interrupt one).
    assert _found(body) == []


@pytest.mark.parametrize(
    "body",
    [
        "| [r]: /etc/x\x7f |\n|---|\n| c |",
        "| h |\n|---|\n| [r]: /etc/x\x7f |",
        "| h |\n|---|\n| c | [r]: /etc/x\x7f |",
        "- | [r]: /etc/x\x7f |\n  |---|\n  | c |",
        "> | h |\n> |---|\n> | [r]: /etc/x\x7f |",
        "- > | [r]: /etc/x\x7f |\n  > |---|",
    ],
)
def test_a_cell_of_a_table_is_no_definition(body):
    assert _found(f"![a][r]\n\n{body}") == []


@pytest.mark.parametrize(
    "body",
    [
        "| h |\n|---|\n[r]: /etc/x\x7f",
        "| h |\n|---|\n| c |\n[r]: /etc/x\x7f",
        "- | h |\n  |---|\n  [r]: /etc/x\x7f",
        "> | h |\n> |---|\n> [r]: /etc/x\x7f",
    ],
)
def test_a_line_without_a_pipe_after_a_table_is_a_row_and_no_definition(body):
    """GFM, the model, markdown-it and ``pandoc -f commonmark`` read a row (or a line of a paragraph). Only
    ``pandoc -f gfm`` reads a definition there: the residual of CONFORMANCE.md (``-f gfm`` is not a supported reading)."""
    assert _found(f"![a][r]\n\n{body}") == []


@needs_pandoc
def test_pandoc_gfm_reads_a_definition_after_a_table_that_the_path_rules_do_not_judge():
    """The one way an image of ``pandoc -f gfm`` goes unjudged: pinned, so that a pandoc that agrees is noticed."""
    source = "| h |\n|---|\n[r]: /etc/x\x7f\n\n![a][r]\n"
    out = subprocess.run(
        ["pandoc", "-f", "gfm", "-t", "json"], input=source, capture_output=True, text=True, check=True
    ).stdout
    assert '"Image"' in out and "/etc/x\x7f" in out
    commonmark = subprocess.run(
        ["pandoc", "-f", "commonmark", "-t", "json"], input=source, capture_output=True, text=True, check=True
    ).stdout
    assert '"Image"' not in commonmark


def test_a_table_is_the_models_though_a_renderer_without_tables_reads_a_definition_in_it():
    """The residual: ``[r]: x`` and a delimiter row under it is a table of one cell to the model (the specification's
    tables are GFM's), and a paragraph with a definition to a renderer without tables (``pandoc -f commonmark``)."""
    assert _found("![a][r]\n\n[r]: /etc/x\x7f\n|---|") == []
    assert _found("![a][r]\n\n[r]: /etc/x\n|---|") == []  # (the specification's too, as it ever was)


# ── The labels ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("definition", "image"),
    [
        ("a\u00a0b", "a b"), ("a\u3000b", "A  B"), ("a\u2005b", "a b"), ("a\x0bb", "a b"), ("a\x0cb", "a b"),
        ("\u0390", "\u03b9\u0308\u0301"), ("\u00e9", "e\u0301"), ("\u212b", "\u00c5"),
        ("\u0131", "I"), ("a\u2028b", "a b"), ("a\x1fb", "a b"),
    ],
)
def test_a_label_is_matched_by_each_reading_as_the_renderer_does(definition, image):
    """Some of these are one label to pandoc (a no-break space, U+3000, a canonical equivalent), some to markdown-it
    (``ı`` and ``I``, U+2028, a control character), some to both: the image is displayed by one of them."""
    keys = {reading: label_key(definition, reading) == label_key(image, reading) for reading in Reading}
    assert keys[Reading.PANDOC] or keys[Reading.MDIT]
    assert _found(f"![a][{image}]\n\n[{definition}]: /etc/x") == [("path-not-relative", 9)], (definition, image)
    # ... but a label no reading matches is no image.
    assert _found(f"![a][{image}x]\n\n[{definition}]: /etc/x") == []


def test_a_definition_that_only_one_reading_matches_the_label_of_does_not_shadow_a_later_one():
    # U+0390 and its three code points are one label to pandoc (it reads NFC): its first is displayed by it. The
    # upper case's three code points are not. The specification and markdown-it match all of them.
    three, upper = "\u03b9\u0308\u0301", "\u0399\u0308\u0301"
    assert _found(f"![a][{upper}]\n\n[\u0390]: ok.png\n\n[{upper}]: /etc/x") == [("path-not-relative", 11)]
    assert _found(f"![a][{three}]\n\n[\u0390]: ok.png\n\n[{three}]: /etc/x") == []  # (the first, by every reading)
    # A no-break space ends a label for no reading but pandoc's and markdown-it's.
    assert _found("![a][a b]\n\n[a\u00a0b]: ok.png\n[a b]: /etc/x") == [("path-not-relative", 10)]
    assert _found("![a][a\u00a0b]\n\n[a b]: ok.png\n[a\u00a0b]: /etc/x") == [("path-not-relative", 10)]


def test_the_label_keys_of_the_readings():
    assert label_key("  Foo\t\n Bar ") == "foo bar"
    assert label_key("a\u00a0b") == "a\u00a0b"  # a no-break space is text to the specification
    assert label_key("a\u00a0 b", Reading.PANDOC) == "a b" and label_key("a\u00a0 b", Reading.MDIT) == "A B"
    assert label_key("\u3000a\u3000", Reading.PANDOC) == "a"
    assert label_key("a\x1fb", Reading.PANDOC) != "a b" and label_key("a\x1fb", Reading.MDIT) == "A B"
    assert label_key("\u0131", Reading.MDIT) == "I" and label_key("\u0131", Reading.PANDOC) == "\u0131"
    assert label_key("\u0390", Reading.PANDOC) == label_key("\u03b9\u0308\u0301", Reading.PANDOC)
    assert label_key("\u0390", Reading.PANDOC) != label_key("\u0399\u0308\u0301", Reading.PANDOC)
    assert label_key("\u0390") == label_key("\u0399\u0308\u0301")  # (casefold)
    assert label_key("\u00e9", Reading.PANDOC) == label_key("e\u0301", Reading.PANDOC)
    assert label_key("\u00e9", Reading.MDIT) != label_key("e\u0301", Reading.MDIT)


# ── A label of white space is blank to the reading whose white space it is ─────


def test_a_label_of_unicode_space_makes_the_line_text_to_pandoc_and_markdown_it():
    # `[<NBSP>]: x` is a definition to the specification, and a line of text to the others: the next line is a line
    # of the same paragraph, no definition, so what they display is the later `[r]`.
    assert _found("![a][r]\n\n[\u00a0]: x\n[r]: ok.png\n\n[r]: /etc/p") == [("path-not-relative", 12)]
    assert _found("![a][r]\n\n[\u3000]: x\n[r]: ok.png\n\n[r]: /etc/p") == [("path-not-relative", 12)]
    # markdown-it's white space is wider (U+001C, U+0085): pandoc reads those as definitions.
    assert _found("![a][r]\n\n[\x1c]: x\n[r]: ok.png\n\n[r]: /etc/p") == [("path-not-relative", 12)]
    assert _found("![a][r]\n\n[\x85]: x\n[r]: ok.png\n\n[r]: /etc/p") == [("path-not-relative", 12)]
    # ... and what is no definition to them names nothing: `[i]` is no definition, `ı` is no label.
    assert _found("![a][\u0131]\n\n[\u00a0]: x\n[i]: /etc/p") == []


def test_an_image_in_the_title_of_a_blank_labelled_line_is_text_to_the_readings_that_read_it_so():
    assert _paths('![i]\n\n[\u00a0]: x "![i](/etc/t.png)"') == ["/etc/t.png"]
    assert _paths('[\u3000]: x "![i](/etc/t.png)"') == ["/etc/t.png"]


def test_the_triggers_cover_every_white_space_a_reading_has_for_a_label():
    import unicodedata

    triggers = path_definitions._TRIGGERS
    for number in range(0x30001):
        c = chr(number)
        if c in " \t\n\r" or 0xD800 <= number < 0xE000:
            continue
        label = f"[{c}]: x"
        if unicodedata.category(c) == "Zs" or c in "\x0b\x0c":
            assert triggers[Reading.PANDOC].search(label), hex(number)
        if c.isspace():
            assert triggers[Reading.MDIT].search(label), hex(number)


# ── Text a reading has where the model has a definition ───────────


@pytest.mark.parametrize(
    "body",
    [
        '[a]: x\\ "![i](/etc/p.png)"',  # markdown-it ends the destination before the backslash: text
        '[r]: p.png\\\n"![i](/etc/p.png)"',  # markdown-it takes the backslash with the line break: no title
        "- [r]: p.png\\\n  '![i](/etc/p.png)'",
        "> [r]: p.png\\\n> (![i](/etc/p.png))",
    ],
)
def test_the_title_of_a_definition_that_a_reading_reads_as_text_is_scanned_for_images(body):
    assert [rule for rule, _line in _found(body)] == ["path-not-relative"]
    assert _paths(body) == ["/etc/p.png"]


def test_that_text_is_found_at_its_line_and_once():
    assert _found('[r]: p.png\\\n"![i](/etc/z.png)"') == [("path-not-relative", 8)]
    assert _found('[a]: x\\ "![i](/etc/p.png)" ![j](/etc/q.png)') == [("path-not-relative", 7)] * 2


def test_a_reference_image_in_such_text_names_the_definitions_of_that_reading():
    assert _paths('[a]: x\\ "![i][b]"\n\n[b]: /etc/q') == ["/etc/q"]


@pytest.mark.parametrize(
    "body",
    [
        '[\u00a0]: x "![i"\n](/etc/t.png)',  # the image begins in the title and ends below it
        '[\u00a0]: x "![i"\n][b]\n\n[b]: /etc/t.png',
        '> [\u00a0]: x "![i](/etc/t.png)"\n> foo\n> ===',  # a setext heading of a quote that has other lines
        '> [\u00a0]: x "![i][b]"\n> foo\n> ===\n\n[b]: /etc/t.png',
        '- [\u00a0]: x "![i](/etc/t.png)"\n  foo\n  ===',
        '- [\u00a0]: x "![i][b]"\n  foo\n  ---\n\n[b]: /etc/t.png',
        '[\u00a0]: x "![i](/etc/t.png)"\nfoo\n===',
        '[\u00a0]: x "![i"\nfoo](/etc/t.png)\n---',
    ],
)
def test_text_a_reading_has_is_read_whole_and_the_images_that_begin_in_a_title_are_judged(body):
    assert _paths(body) == ["/etc/t.png"], body


def test_an_image_that_begins_below_the_title_is_judged_once_by_the_model_alone():
    assert _found('[\u00a0]: x "t"\n![i](/etc/t.png)') == [("path-not-relative", 8)]
    assert _found('> [\u00a0]: x "t"\n> foo ![i](/etc/t.png)\n> ===') == [("path-not-relative", 8)]


def test_the_lines_of_a_headings_joined_text_are_not_guessed():
    # A setext heading in an item has its lines joined: an image on its second line is not on the first, where the
    # title of a definition that pandoc reads is.
    assert "/etc/c\x7f" in _paths('* [FOO]: /etc/p\x7f "![i](/etc/t.png)"\n  word ![a](/etc/c\\\x7f) text\n  ---')


# ── What a reading has as a title is no image ─────────────────────


def test_a_reference_image_in_the_title_of_a_definition_a_reading_reads_is_not_that_readings():
    # pandoc reads the first line as a definition with a title (a DEL ends the destination for the others, which show
    # the image: with `ok.png`, the definition they read).
    assert _found('[a]: x\x01 "![i][b]"\n\n[b]: /etc/q\x01\n\n[b]: ok.png') == []
    assert _found('[a]: x\x01 "![i][b]"\n---\n\n[b]: /etc/q\x01\n\n[b]: ok.png') == []
    # ... but not an image of the same text elsewhere.
    assert _found('[a]: x\x01 "![i][b]"\n\n![j][b]\n\n[b]: /etc/q\x01\n\n[b]: ok.png') == [("path-not-relative", 11)]


def test_an_inline_image_in_the_title_of_a_definition_that_the_only_reading_to_read_it_has_is_none():
    # Only pandoc reads `![i](/etc/q<0x01>)` as an image, and it reads the line as a definition with that title.
    assert _found('[a]: x\x01 "![i](/etc/q\x01)"') == []
    assert _found('[a]: x\x01 "![i](/etc/q\x01)"\nfoo\n===') == []
    assert _found('> [a]: x\x01 "![i](/etc/q\x01)"\n> foo\n> ===') == []
    # ... the same image elsewhere is one.
    assert _found('[a]: x\x01 "![i](/etc/q\x01)"\n\n![j](/etc/q\x01)') == [("path-not-relative", 9)]


# ── One finding for a destination ─────────────────────────────────


def test_a_destination_the_readings_read_alike_is_one_finding_though_pandoc_reads_it_as_nfc():
    # The file name as macOS writes it (NFD), and the same with a root.
    assert _found("![a](/etc/e\u0301)") == [("path-not-relative", 7)]
    assert _found("![a][r]\n\n[r]: </etc/e\u0301>") == [("path-not-relative", 9)]
    document = _document("![a](../xe\u0301.png)")
    assert [d.rule for d in validate(document, document_root="/r").diagnostics if d.rule.startswith("path-")] == ["path-outside-root"]
    # A definition's destination that white space ends (VT, which the model strips).
    assert _found("![a][r]\n\n[r]: </etc/p\x0b>") == [("path-not-relative", 9)]
    # ... but the Kelvin sign makes a drive only for pandoc, and that is said.
    assert _paths("![a](\u212a:/x)") == ["K:/x"]


# ── Inline images ─────────────────────────────────────────────────


@needs_markdown_it
@pytest.mark.parametrize(
    "image",
    ["![a](/etc/passwd\\\tx)", "![a](/etc/passwd\\\nx)", "![a](</etc/passwd\\\nx>)", "![a](/etc/passwd\\\x01x)", "![a](/etc/passwd\\\x7fx)"],
)
def test_an_image_markdown_it_reads_with_a_backslash_before_a_character_the_specification_ends_the_destination_at(image):
    """markdown-it reads a backslash as a pair before a tab, a line break, a control character and DEL: the destination
    goes on, which the specification (and pandoc, but for ``<…>``) end at the character."""
    import markdown_it

    assert any(t.type == "image" for tok in markdown_it.MarkdownIt("commonmark").parse(image) for t in tok.children or [])
    assert [rule for rule, _line in _found(image)].count("path-not-relative") >= 1


def test_the_image_of_the_issue_is_no_image_to_any_renderer():
    # markdown-it ends the destination at a backslash before a space, and then finds no ``)``.
    assert _found('![a](/etc/passwd\\ "t)') == []
    # ... but with the title closed it is an image to the specification and pandoc: its destination is ``/etc/passwd\``.
    assert _found('![a](/etc/passwd\\ "t")') == [("path-not-relative", 7)]


def test_an_image_the_readings_read_with_different_destinations_is_judged_for_each():
    """``![a](/etc/y\\<0x01>z)``: pandoc drops the backslash (``/etc/y<0x01>z``), markdown-it keeps it. Both are
    wrong, and both are said, once for each distinct destination."""
    found = _paths("![a](/etc/y\\\x01z)")
    assert found == ["/etc/y\x01z", "/etc/y\\\x01z"]
    # Wrong in one only: ``\\<0x01>etc`` is absolute to markdown-it (a backslash first) and relative to pandoc.
    assert _paths("![a][r]\n\n[r]: \\\x01etc") == ["\\\x01etc"]


def test_pandoc_reads_its_input_as_nfc():
    """The Kelvin sign is ``K`` to pandoc: ``K:/x`` is a drive."""
    assert _paths("![a](\u212a:/x)") == ["K:/x"]


def test_an_image_in_the_title_of_a_definition_is_judged_as_text_when_no_reading_reads_the_title_apart():
    # pandoc reads ``x<DEL>`` as a destination, markdown-it does not: it reads the whole line as text, the image in it
    # is displayed, and the finding is right.
    assert _paths('![a][r]\n\n[r]: x\x7f "![i](/etc/p)"') == ["/etc/p"]
    # Both read the definition (a backslash before a control character), and neither displays the title's image: the
    # model keeps the line as text, and the path rules judge what is in it (CONFORMANCE.md, #121).
    assert _paths('![a][r]\n\n[r]: a\\\x01b "![i](/etc/p)"') == ["/etc/p"]


def test_a_definition_with_a_directive_is_never_judged_here():
    assert _found("![a][r]\n\n[r]: /etc/p\x7f{{placeholder: x}}") == []


# ── Where the findings are ────────────────────────────────────────


def test_a_finding_for_a_definition_is_at_its_line_and_names_the_label():
    result = validate(_document("![a][R  r]\n\n[r r]: /etc/p\x7f\n\n![b][r r]"))
    [found] = [d for d in result.diagnostics if d.rule == "path-not-relative"]
    assert found.line == 9 and "(image, by the definition of [r r])" in found.message and "relative to the document" in found.message


def test_the_findings_of_one_block_are_in_the_order_of_the_lines():
    # A definition the specification reads, then two that only pandoc and markdown-it do, in a list item.
    body = "![a][x] ![a][y] ![a][z]\n\n- [x]: /etc/x\n  [y]: /etc/y\x01\n  [z]: /etc/z\\\x7f"
    result = validate(_document(body))
    assert [d.line for d in result.diagnostics if d.rule == "path-not-relative"] == [9, 10, 11]


def test_an_image_in_a_heading_names_a_definition_the_renderers_read():
    assert _found("[r]: /etc/p\x7f\n\n# ![a][r]\n") == [("path-not-relative", 7)]
    assert _found("# ![a][r]\n\n[r]: /etc/p\x7f\n") == [("path-not-relative", 9)]


# ── What has no text to read again ────────────────────────────────


def test_a_document_built_in_code_has_the_specifications_reading_alone():
    """No source: nothing to read again. The model's own definitions are judged, and a paragraph that holds one
    pandoc would read is paragraph text (CONFORMANCE.md, #121)."""
    built = Document(
        sections=[Section(title="A", level=1, identifier="a", blocks=[
            Block(kind="paragraph", text="![a][r]"), Block(kind="paragraph", text="[r]: /etc/p\x7f"),
        ])]
    )
    assert [d.rule for d in validate(built).diagnostics if d.rule.startswith("path-")] == []
    with_block = Document(
        sections=[Section(title="A", level=1, identifier="a", blocks=[
            Block(kind="paragraph", text="![a][r]"), Block(kind="link_definition", label="r", target="/etc/p\x7f"),
        ])]
    )
    assert [d.rule for d in validate(with_block).diagnostics if d.rule.startswith("path-")] == ["path-not-relative"]


def test_a_document_changed_after_it_was_parsed_has_the_specifications_reading_alone():
    document = _document("![a][r]\n\n[r]: /etc/p\x7f")
    assert [d.rule for d in validate(document).diagnostics if d.rule.startswith("path-")] == ["path-not-relative"]
    document.sections[0].blocks.append(Block(kind="paragraph", text="More."))
    assert [d.rule for d in validate(document).diagnostics if d.rule.startswith("path-")] == []
    assert path_definitions.read_definitions(document, Locator(document), Reading.PANDOC) is None


# ── The reparse is contained ──────────────────────────────────────


def test_the_reading_is_the_specifications_outside_a_reparse_and_after_one_that_fails(monkeypatch):
    assert link_definitions_reading() is Reading.SPEC
    with definitions_read_as(Reading.PANDOC):
        assert link_definitions_reading() is Reading.PANDOC
    assert link_definitions_reading() is Reading.SPEC

    def refuse(*args, **kwargs):
        raise RuntimeError("the reparse failed")

    monkeypatch.setattr("legaldown.parser._parse_body", refuse)
    with pytest.raises(RuntimeError, match="reparse failed"):
        validate(_document("![a][r]\n\n[r]: /etc/p\x7f"))
    assert link_definitions_reading() is Reading.SPEC


def test_readings_do_not_nest():
    with (
        definitions_read_as(Reading.PANDOC),
        pytest.raises(RuntimeError, match="one reading at a time"),
        definitions_read_as(Reading.MDIT),
    ):
        pass
    assert link_definitions_reading() is Reading.SPEC


def test_a_quote_read_under_another_reading_is_neither_cached_nor_served_from_the_cache():
    text = "[r]: /e\x7f\n\ntext"
    assert [b.kind for b in quote_content(text, 1)[0]] == ["paragraph", "paragraph"]  # (the specification: text)
    with definitions_read_as(Reading.PANDOC):
        read = quote_content(text, 1)[0]
        assert [b.kind for b in read] == ["link_definition", "paragraph"]
        assert quote_content(text, 1)[0] is not read  # (not kept)
    assert [b.kind for b in quote_content(text, 1)[0]] == ["paragraph", "paragraph"]
    text = "[s]: /f\x01\n\nother"
    with definitions_read_as(Reading.PANDOC):
        quote_content(text, 1)
    assert [b.kind for b in quote_content(text, 1)[0]] == ["paragraph", "paragraph"]  # (not what was read as pandoc)


def test_the_model_is_the_specifications_after_validating():
    document = _document("![a][r]\n\n> [r]: /etc/p\x7f\n\n- [s]: /etc/q\x01")
    validate(document)
    from legaldown.syntax import collect_link_definitions

    assert collect_link_definitions(document) == {}
    assert [b.kind for b in document.sections[0].blocks] == ["paragraph", "quote", "unordered_list"]


# ── Only where it can differ ──────────────────────────────────────


class _Reads:
    def __init__(self, monkeypatch):
        self.readings: list[Reading] = []
        real = path_definitions.definitions_read_as

        def counted(reading):
            self.readings.append(reading)
            return real(reading)

        monkeypatch.setattr(path_definitions, "definitions_read_as", counted)


@pytest.mark.parametrize(
    ("body", "reads"),
    [
        ("![a][r]\n\n[r]: /etc/p", []),  # (a definition, nothing the readings differ on)
        ("![a][r]\n\n[r]: <a b>\n\n[s]: <a\\\\b>", []),  # (``<…>``, and a backslash before a punctuation character)
        ("![a][r]\n\n`x\x7f`\n\n[r]: /etc/p", [Reading.PANDOC]),  # (a DEL, anywhere: it may be in a destination)
        ("![a][r]\n\ntext\x7f\n\n[r]: /etc/p", [Reading.PANDOC]),
        ("![a][r]\n\ntext\x7f\n\n[r: /etc/p", []),  # (no ``]:``)
        ("![a][r]\n\n[r]: x\\\ty", [Reading.MDIT]),
        ("![a][r]\n\n[r]: x\\ y\n[s]: <a\\\nb>", [Reading.PANDOC, Reading.MDIT]),
        ("text\x7f\n\n![a][r]\n\n[s]: /etc/p", [Reading.PANDOC]),
        ("text\x7f\n\n![a](/etc/p)", []),  # (no ``]:``: no definition)
    ],
)
def test_the_document_is_read_again_only_for_a_reading_that_can_differ(monkeypatch, body, reads):
    counted = _Reads(monkeypatch)
    validate(_document(body))
    assert counted.readings == reads, body


def test_a_document_without_an_image_is_never_read_again(monkeypatch):
    counted = _Reads(monkeypatch)
    validate(_document("[r]: x\x7f\\\ty\n\n[s]: <a\\\nb>\n\ntext [r]"))
    assert counted.readings == []


def test_however_many_images_there_are_the_document_is_read_again_once_for_a_reading(monkeypatch):
    counted = _Reads(monkeypatch)
    validate(_document("[r]: /etc/p\x7f\n\n" + "![a][r] ![b](x) " * 500))
    assert counted.readings == [Reading.PANDOC]


def test_a_document_of_many_quotes_is_read_again_once(monkeypatch):
    counted = _Reads(monkeypatch)
    body = "![a][r]\n\n" + "> quote\n\n" * 200 + "> [r]: /etc/p\x7f\n"
    assert [rule for rule, _line in _found(body)] == ["path-not-relative"]
    assert counted.readings == [Reading.PANDOC]


# ── Linear time ───────────────────────────────────────────────────


def _seconds(call, *args) -> float:
    start = time.perf_counter()
    call(*args)
    return time.perf_counter() - start


@pytest.mark.parametrize(
    "unit",
    ["[r{n}]: x\x7f\n\n![a][r{n}]\n\n", "- [r{n}]: x\x7f\n  ![a][r{n}]\n", "> [r{n}]: x\x7f\n> ![a][r{n}]\n\n", "[r{n}]: x\\\ty\n\n![a][r{n}]\n\n"],
)
def test_a_document_of_many_definitions_and_images_is_validated_in_linear_time(unit):
    def document(count):
        return _document("".join(unit.format(n=n) for n in range(count)))

    small, large = document(2_000), document(4_000)
    first, second = _seconds(validate, small), _seconds(validate, large)
    assert second < max(3.5 * first, 1.0) and second < 15.0, (first, second)


def test_many_images_of_one_definition_and_one_definition_of_many_labels_are_linear():
    one = _document("[r]: /etc/p\x7f\n\n" + "![a][r] " * 20_000)
    assert _seconds(validate, one) < 15.0
    many = _document("".join(f"[r{n}]: /etc/p\x7f\n" for n in range(10_000)) + "\n" + "![a][r9999]\n")
    assert _seconds(validate, many) < 15.0
