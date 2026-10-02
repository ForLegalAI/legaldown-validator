"""The public API (#26): what ``validate_document`` decides for a renderer —
the template decision and the markers that apply — and the helpers a
renderer builds with, importable from ``legaldown`` and ``legaldown.validator``."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

import legaldown
import legaldown.validator
from legaldown import (
    PlacedMarker,
    ValidationResult,
    block_fragments,
    document_from_dict,
    is_drafting_note,
    is_template,
    list_fragments,
    load,
    parse,
    validate_document,
)
from legaldown.directives import lex
from legaldown.validator.units import find_markers

_FRONTMATTER = "---\ntitle: T\n---\n\n"


def _markers(body: str) -> list[PlacedMarker]:
    return validate_document(parse(_FRONTMATTER + body)).placed_markers


@pytest.mark.parametrize("module", [legaldown, legaldown.validator])
def test_every_public_name_is_importable(module):
    assert [name for name in module.__all__ if not hasattr(module, name)] == []


def test_the_renderers_names_are_public():
    for name in ("PlacedMarker", "is_template", "is_drafting_note", "lex", "Lexed", "is_escaped",
                 "block_fragments", "list_fragments", "list_items"):
        assert name in legaldown.__all__
    for name in ("parse_condition", "Condition", "condition_problem", "exclusive", "Presence", "ALWAYS", "is_template",
                 "PlacedMarker", "IDENTIFIER_RE", "KNOWN_CURRENCIES", "is_valid_iso_date",
                 "is_valid_money_amount", "is_positive_numeric"):
        assert name in legaldown.validator.__all__


def test_a_result_built_directly_has_neither_decision():
    result = ValidationResult()
    assert (result.is_template, result.placed_markers) == (False, [])


def test_a_marker_ending_a_paragraph_over_lines():
    [marker] = _markers("# A\n\nText\nmore. {#t}\n")
    assert (marker.section, marker.block, marker.fragment, marker.item) == (0, 0, 0, None)
    assert (marker.source, marker.identifier, marker.condition, marker.line) == ("{#t}", "t", "", 8)
    assert "Text\nmore. {#t}"[marker.offset:] == "{#t}"


def test_a_list_items_marker_names_its_item():
    """An item's fragments are its blocks': the item a marker marks is not
    its fragment's index."""
    body = "# A\n\n- first {#a}\n\n  second para\n- second {#b}\n  - nested\n    more {#c when=x}\n"
    markers = _markers(body)
    assert [(m.identifier, m.fragment, m.item) for m in markers] == [("a", 0, 0), ("b", 2, 1), ("c", 3, 2)]
    assert markers[2].condition == "x"
    [block] = parse(_FRONTMATTER + body).sections[0].blocks
    assert [list_fragments(block)[m.fragment][2][-1] for m in markers] == [0, 1, 2]


def test_an_include_only_paragraphs_identifier_does_not_apply():
    [marker] = _markers("# A\n\n{{include: x.lgd}} {#i when=b}\n")
    assert (marker.identifier, marker.condition, marker.include_only) == ("", "b", True)


def test_a_preamble_condition_applies_only_in_a_template():
    assert _markers("Intro {when=a}\n\n# A\n\nText.\n") == []
    result = validate_document(parse(_FRONTMATTER + "Intro {when=a}\n\n# A {when=b}\n\nText.\n"))
    assert result.is_template
    assert [(m.section, m.condition) for m in result.placed_markers] == [(None, "a")]


def test_the_offset_is_the_markers_not_a_copy_in_a_comment():
    [marker] = _markers("# A\n\nText {#t} <!-- {#t} -->\n")
    assert marker.offset == 5


@pytest.mark.parametrize("body", [
    "# A\n\n| a |\n|---|\n| x {#t} |\n",
    "# A\n\n> quoted {#t}\n",
    "# A\n\n> > nested {#t}\n",
    "# A\n\nText {#t} more.\n",
    "# A\n\n- a\n\n  second {#t}\n",
])
def test_a_marker_out_of_place_is_not_placed(body):
    assert _markers(body) == []


def test_a_document_built_in_code_has_no_lines():
    document = document_from_dict({"sections": [{"title": "A", "blocks": [{"kind": "paragraph", "text": "Text {#t}"}]}]})
    [marker] = validate_document(document).placed_markers
    assert (marker.identifier, marker.line) == ("t", None)


def test_is_template_on_its_own():
    assert not is_template(parse(_FRONTMATTER + "# A\n\nText.\n"))
    assert is_template(parse(_FRONTMATTER + "# A\n\nPick {{choose: q}}.\n"))


def test_is_drafting_note():
    [note, quote] = parse(_FRONTMATTER + "# A\n\n> [!drafting]\n> Check.\n\n> Plain.\n").sections[0].blocks
    assert is_drafting_note(note) and not is_drafting_note(quote)


def _corpus():
    root = os.environ.get("LEGALDOWN_FIXTURES_DIR", "")
    if not root or not Path(root).is_dir():
        return []
    spec = Path(root).parent
    return [pytest.param(path, id=path.relative_to(spec).as_posix()) for path in sorted(spec.rglob("*.lgd"))]


@pytest.mark.parametrize("path", _corpus())
def test_the_decisions_are_the_validators_own(path: Path):
    """Over the specification's documents: the template decision is
    ``is_template``, the placed markers are the validator's placed findings,
    each where it says, at most one in a fragment."""
    try:
        document = parse(path.read_text(encoding="utf-8"))
    except legaldown.FrontmatterError:
        pytest.skip("unreadable frontmatter")
    result = validate_document(document)
    assert result.is_template == is_template(document)
    found = [f for f in find_markers(document, lex) if f.marker is not None and f.placed(result.is_template)]
    assert [(m.section, m.block, m.fragment, m.offset, m.source) for m in result.placed_markers] == [
        (f.section, f.block, f.fragment, f.offset, f.source) for f in found
    ]
    places = [(m.section, m.block, m.fragment) for m in result.placed_markers]
    assert len(places) == len(set(places))
    lines = path.read_text(encoding="utf-8").split("\n")
    for marker in result.placed_markers:
        blocks = document.preamble if marker.section is None else document.sections[marker.section].blocks
        text = block_fragments(blocks[marker.block])[marker.fragment][0]
        assert text[marker.offset:marker.offset + len(marker.source)] == marker.source
        assert marker.source in lines[marker.line - 1]


def test_a_marker_after_a_lifted_reference_is_in_the_suffix():
    [marker] = _markers("# A\n\nSee {{ref: a}} below. {#t}\n\n# B {#a}\n\nText.\n")
    block = parse(_FRONTMATTER + "# A\n\nSee {{ref: a}} below. {#t}\n\n# B {#a}\n\nText.\n").sections[0].blocks[0]
    assert (block.kind, marker.field) == ("ref", "suffix")
    assert block.suffix[marker.offset:] == "{#t}"


def test_a_list_items_marker_has_its_line():
    body = "# A\n\n- first {#a}\n\n  second para\n- \n- third\n  more {#c}\n"
    markers = _markers(body)
    lines = (_FRONTMATTER + body).split("\n")
    assert [(m.identifier, m.item) for m in markers] == [("a", 0), ("c", 2)]
    assert [lines[m.line - 1] for m in markers] == ["- first {#a}", "  more {#c}"]


def test_a_list_is_walked_once(monkeypatch):
    """Its fragments are read once for the placed markers, however many it
    holds (not once a marker, which is quadratic in a long list)."""
    import legaldown.definitions

    calls = []
    real = legaldown.definitions.list_fragments
    monkeypatch.setattr(legaldown.definitions, "list_fragments", lambda block: calls.append(1) or real(block))
    markers = _markers("# A\n\n" + "".join(f"- item {{#i{n}}}\n" for n in range(50)))
    assert [m.item for m in markers] == list(range(50))
    assert len(calls) <= 2  # the units' reading, and the placed markers'


def test_a_document_changed_since_parsing_has_no_lines():
    document = parse(_FRONTMATTER + "# A\n\nText {#t}\n\n- a {#b}\n")
    document.sections[0].blocks.insert(0, document.sections[0].blocks[0])
    assert [m.line for m in validate_document(document).placed_markers] == [None, None, None]


def test_a_list_of_string_items_built_in_code():
    document = document_from_dict({"sections": [{"title": "A", "blocks": [
        {"kind": "unordered_list", "items": ["a {#x}", "b\n\n  - c {#y}"]},
    ]}]})
    assert [(m.identifier, m.item) for m in validate_document(document).placed_markers] == [("x", 0), ("y", 2)]


def test_two_lists_in_a_section_number_their_own_items():
    """Each list's fragments are its own: the first list's fragment 1 is in
    its item 0, the second's in its item 1."""
    markers = _markers("# A\n\n- a {#a}\n\n  more\n- b {#b}\n\nText.\n\n1. c\n2. d {#d}\n")
    assert [(m.identifier, m.block, m.fragment, m.item) for m in markers] == [
        ("a", 0, 0, 0), ("b", 0, 2, 1), ("d", 2, 1, 1),
    ]


def test_a_marker_after_a_lifted_term_is_in_the_suffix():
    body = '"Thing" {{def: thing}} means a thing.\n\n# A\n\nUse the {{term: thing}} well. {#t}\n'
    [marker] = _markers(body)
    block = parse(_FRONTMATTER + body).sections[0].blocks[0]
    assert (block.kind, marker.field) == ("term", "suffix")
    assert block.suffix[marker.offset:] == "{#t}"


def test_only_a_quote_block_is_a_drafting_note():
    from legaldown import Block

    assert not is_drafting_note(Block(kind="paragraph", text="[!DRAFTING]\nx"))
    assert is_drafting_note(Block(kind="quote", text="[!DRAFTING]\nx"))


def test_fragments_have_names():
    [block] = parse(_FRONTMATTER + "# A\n\n- a {#a}\n  - b\n").sections[0].blocks
    first = list_fragments(block)[0]
    assert (first.text, first.anchor, first.items) == ("a {#a}", True, (0,))
    text, anchor = block_fragments(block)[1]
    assert (text, anchor) == ("b", True)


# load / parse ----------------------------------------------------------------

_SOURCE = _FRONTMATTER + "# A {#a}\n\nText.\n"


def test_load_reads_a_file_and_names_it(tmp_path):
    file = tmp_path / "contract.lgd"
    file.write_text(_SOURCE, encoding="utf-8")
    document = load(file)
    assert (document.filename, document.path) == ("contract.lgd", file.resolve())
    assert document.sections[0].title == "A"


def test_load_takes_a_string_path_and_a_relative_one(tmp_path, monkeypatch):
    (tmp_path / "contract.lgd").write_text(_SOURCE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load("contract.lgd").path == (tmp_path / "contract.lgd").resolve()
    assert load("contract.lgd").path.is_absolute()


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_load_reads_the_same_whatever_the_line_endings(tmp_path, ending):
    file = tmp_path / "x.lgd"
    file.write_bytes(_SOURCE.replace("\n", ending).encode("utf-8"))
    assert load(file) == parse(_SOURCE, filename="x.lgd")


def test_load_drops_a_byte_order_mark_and_keeps_utf8(tmp_path):
    source = _FRONTMATTER + "# Čl. 1\n\nPlatba v €.\n"
    file = tmp_path / "x.lgd"
    file.write_bytes(b"\xef\xbb\xbf" + source.encode("utf-8"))
    assert load(file) == parse(source, filename="x.lgd")


def test_load_has_the_source_map_of_a_parsed_document(tmp_path):
    file = tmp_path / "x.lgd"
    file.write_text(_FRONTMATTER + "# A\n\nSee {{ref: nowhere}}.\n", encoding="utf-8")
    [diagnostic] = [d for d in validate_document(load(file)).diagnostics if d.rule == "ref-broken"]
    assert (diagnostic.line, diagnostic.file) == (7, "x.lgd")


def test_load_fails_in_the_ways_the_documentation_says(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.lgd")
    bad = tmp_path / "bad.lgd"
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeDecodeError):
        load(bad)
    bad.write_text("---\ntitle: [unclosed\n---\n", encoding="utf-8")
    with pytest.raises(legaldown.FrontmatterError):
        load(bad)


def test_where_a_document_lives_is_not_part_of_what_it_says(tmp_path):
    file = tmp_path / "x.lgd"
    file.write_text(_SOURCE, encoding="utf-8")
    assert load(file).path is not None
    assert parse(_SOURCE).path is None
    assert load(file).path != parse(_SOURCE).path
    assert load(file) == parse(_SOURCE, filename="x.lgd")
    assert "path" not in legaldown.document_to_dict(load(file))


def test_parse_document_is_a_deprecated_alias_of_parse():
    with pytest.warns(DeprecationWarning, match=r"removed in 0\.5\.0") as record:
        document = legaldown.parse_document(_SOURCE, filename="x.lgd")
    assert document == parse(_SOURCE, filename="x.lgd")
    assert record[0].filename == __file__  # the caller is named, not the shim


def test_a_symbolic_link_is_named_as_it_was_given(tmp_path):
    target = tmp_path / "v3-final.lgd"
    target.write_text(_SOURCE, encoding="utf-8")
    link = tmp_path / "current.lgd"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are not available")
    document = load(link)
    assert (document.filename, document.path) == ("current.lgd", link)


def test_dot_dot_after_a_symbolic_link_goes_where_the_system_goes(tmp_path, monkeypatch):
    sub = tmp_path / "data" / "sub"
    sub.mkdir(parents=True)
    (tmp_path / "data" / "contract.lgd").write_text(_FRONTMATTER + "# Real\n", encoding="utf-8")
    (tmp_path / "contract.lgd").write_text(_FRONTMATTER + "# Wrong\n", encoding="utf-8")
    try:
        (tmp_path / "link").symlink_to(sub)
    except OSError:
        pytest.skip("symbolic links are not available")
    monkeypatch.chdir(tmp_path)
    assert load("link/../contract.lgd").sections[0].title == "Real"


def test_document_keeps_source_map_in_its_place():
    fields = [f.name for f in dataclasses.fields(legaldown.Document)]
    assert fields.index("source_map") == 4  # positional callers: unchanged by path
