"""The public API (#26): what ``validate`` decides for a renderer —
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
    file_loader,
    is_drafting_note,
    is_template,
    list_fragments,
    load,
    parse,
    validate,
)
from legaldown.directives import lex
from legaldown.validator.units import find_markers

_FRONTMATTER = "---\ntitle: T\n---\n\n"


def _markers(body: str) -> list[PlacedMarker]:
    return validate(parse(_FRONTMATTER + body)).index.placed_markers


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
    assert (result.index.is_template, result.index.placed_markers) == (False, [])


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
    result = validate(parse(_FRONTMATTER + "Intro {when=a}\n\n# A {when=b}\n\nText.\n"))
    assert result.index.is_template
    assert [(m.section, m.condition) for m in result.index.placed_markers] == [(None, "a")]


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
    [marker] = validate(document).index.placed_markers
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
    result = validate(document)
    assert result.index.is_template == is_template(document)
    found = [f for f in find_markers(document, lex) if f.marker is not None and f.placed(result.index.is_template)]
    assert [(m.section, m.block, m.fragment, m.offset, m.source) for m in result.index.placed_markers] == [
        (f.section, f.block, f.fragment, f.offset, f.source) for f in found
    ]
    places = [(m.section, m.block, m.fragment) for m in result.index.placed_markers]
    assert len(places) == len(set(places))
    lines = path.read_text(encoding="utf-8").split("\n")
    for marker in result.index.placed_markers:
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
    assert [m.line for m in validate(document).index.placed_markers] == [None, None, None]


def test_a_list_of_string_items_built_in_code():
    document = document_from_dict({"sections": [{"title": "A", "blocks": [
        {"kind": "unordered_list", "items": ["a {#x}", "b\n\n  - c {#y}"]},
    ]}]})
    assert [(m.identifier, m.item) for m in validate(document).index.placed_markers] == [("x", 0), ("y", 2)]


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
    [diagnostic] = [d for d in validate(load(file)).diagnostics if d.rule == "ref-broken"]
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


# validate: what it reads, what it returns -------------------------------------

_AMENDMENT = "---\ntitle: A\namends:\n  title: Original\n  file: {file}\n---\n\n# S\n\nUses {{{{term: services}}}}.\n"
_ORIGINAL = '---\ntitle: O\n---\n\n# S\n\n"Services" {{def: services}} means x.\n'
_EMPTY_ORIGINAL = "---\ntitle: O\n---\n\n# S\n\nText.\n"


def _amendment(tmp_path, original: str | None, *, file: str = "original.lgd", name: str = "amendment.lgd"):
    if original is not None:
        (tmp_path / "original.lgd").write_text(original, encoding="utf-8")
    path = tmp_path / name
    path.write_text(_AMENDMENT.format(file=file), encoding="utf-8")
    return load(path)


def test_validate_reads_the_amended_original_from_beside_the_document(tmp_path):
    assert "amend-term-undefined" not in validate(_amendment(tmp_path, _ORIGINAL)).rules()
    assert "amend-term-undefined" in validate(_amendment(tmp_path, _EMPTY_ORIGINAL)).rules("error")


def test_a_document_without_a_path_has_no_original_to_read():
    result = validate(parse(_AMENDMENT.format(file="original.lgd")))
    assert "amend-term-unresolvable" in result.rules()


@pytest.mark.parametrize("original", [None, "---\ntitle: [unclosed\n---\n", b"\xff\xfe"])
def test_an_original_that_cannot_be_read_is_not_a_finding_of_its_own(tmp_path, original):
    unreadable = validate(parse(_AMENDMENT.format(file="original.lgd")))
    if isinstance(original, bytes):
        (tmp_path / "original.lgd").write_bytes(original)
        document = _amendment(tmp_path, None)
    else:
        document = _amendment(tmp_path, original)
    assert validate(document).rules() == unreadable.rules()


def test_an_original_outside_the_documents_directory_is_not_read(tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "original.lgd").write_text(_ORIGINAL, encoding="utf-8")
    document = _amendment(inner, None, file="../original.lgd")
    assert "amend-term-unresolvable" in validate(document).rules()


def test_only_the_originals_own_definitions_are_read(tmp_path):
    (tmp_path / "base.lgd").write_text(_ORIGINAL, encoding="utf-8")
    original = "---\ntitle: O\namends:\n  title: Base\n  file: base.lgd\n---\n\n# S\n\nText.\n"
    assert "amend-term-undefined" in validate(_amendment(tmp_path, original)).rules("error")


def test_resolve_stands_in_for_the_filesystem(tmp_path):
    document = _amendment(tmp_path, _EMPTY_ORIGINAL)
    asked: list[str] = []

    def resolve(path: str) -> str:
        asked.append(path)
        return _ORIGINAL

    assert "amend-term-undefined" not in validate(document, resolve=resolve).rules()
    assert asked == ["original.lgd"]
    assert "amend-term-undefined" not in validate(parse(_AMENDMENT.format(file="o.lgd")), resolve=resolve).rules()


def test_the_importer_callbacks_still_work_and_say_they_are_deprecated():
    document = parse(_AMENDMENT.format(file="original.lgd"))
    with pytest.warns(DeprecationWarning, match=r"removed in 0\.5\.0"):
        result = validate(document, import_definitions=lambda *_: {"services": "Services"})
    assert "amend-term-undefined" not in result.rules() and "amend-term-unresolvable" not in result.rules()
    with pytest.warns(DeprecationWarning, match=r"removed in 0\.5\.0"):
        validate(document, import_attachment_definitions=lambda _file: {})


def test_validate_document_is_a_deprecated_alias_of_validate():
    document = parse(_SOURCE)
    with pytest.warns(DeprecationWarning, match=r"removed in 0\.5\.0") as record:
        result = legaldown.validate_document(document, final=True)
    assert result == validate(document, final=True)
    assert record[0].filename == __file__


def test_the_messages_by_severity_are_the_diagnostics_own():
    result = validate(parse(_FRONTMATTER + "# A\n\nSee {{ref: nowhere}}.\n"))
    assert result.errors == [d.message for d in result.diagnostics if d.level == "error"] != []
    assert result.warnings == [d.message for d in result.diagnostics if d.level == "warning"]
    assert result.infos == [d.message for d in result.diagnostics if d.level == "info"]
    assert not result.is_valid
    result.diagnostics.clear()
    assert (result.errors, result.is_valid) == ([], True)  # nothing kept apart from them


def test_a_result_is_what_was_found_not_the_validators_recorder():
    result = validate(parse(_SOURCE))
    assert type(result) is ValidationResult
    assert not any(hasattr(result, name) for name in ("error", "warning", "info", "at", "used_terms"))


# file_loader -----------------------------------------------------------------


def test_file_loader_reads_beside_and_below_the_base_as_written(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.lgd").write_bytes(b"one\r\ntwo\r\n")
    (tmp_path / "sub" / "b.lgd").write_text("b", encoding="utf-8")
    load_file = file_loader(tmp_path)
    assert load_file("a.lgd") == "one\r\ntwo\r\n"  # no line-break translation
    assert load_file("sub/b.lgd") == "b"
    assert load_file("sub/../a.lgd") == "one\r\ntwo\r\n"


def test_file_loader_reads_nothing_it_may_not(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "outside.lgd").write_text("x", encoding="utf-8")
    (base / "latin.lgd").write_bytes(b"caf\xe9")
    load_file = file_loader(base)
    assert load_file("../outside.lgd") is None
    assert load_file(str(tmp_path / "outside.lgd")) is None
    assert load_file("missing.lgd") is None
    assert load_file("") is None
    assert load_file(".") is None  # a directory
    assert load_file("a\0b") is None
    assert load_file("latin.lgd") is None  # not UTF-8


def test_a_loaded_document_can_be_validated_without_reading_anything(tmp_path):
    document = _amendment(tmp_path, _ORIGINAL)
    assert "amend-term-undefined" not in validate(document).rules()
    assert "amend-term-unresolvable" in validate(document, resolve=lambda path: None).rules()


def test_a_document_that_names_itself_as_its_original_reads_nothing(tmp_path):
    path = tmp_path / "amendment.lgd"
    path.write_text(
        '---\ntitle: A\namends:\n  title: A\n  file: ./amendment.lgd\n---\n\n# S\n\n"Services" {{def: services}} means x.\n',
        encoding="utf-8",
    )
    for options in ({}, {"resolve": file_loader(tmp_path)}):  # by default, and through any resolver
        rules = validate(load(path), **options).rules()
        assert "amend-def-override" not in rules


def test_one_importer_does_not_start_reading_the_other_files_from_disk(tmp_path):
    document = _amendment(tmp_path, _ORIGINAL)
    with pytest.warns(DeprecationWarning):
        # An importer for the attachments only: the amended original is still not read, as it never was.
        result = validate(document, import_attachment_definitions=lambda _file: {})
    assert "amend-term-unresolvable" in result.rules()


@pytest.mark.parametrize(
    ("written", "asked"),
    [("original.lgd", ["original.lgd"]), ("./sub/../original.lgd", ["original.lgd"]),
     ("../original.lgd", []), ("/original.lgd", []), ("sub/../../original.lgd", [])],
)
def test_a_resolver_is_asked_for_a_normalized_path_within_the_directory(written, asked):
    document = parse(_AMENDMENT.format(file=written))
    seen: list[str] = []
    validate(document, resolve=lambda path: seen.append(path))
    assert seen == asked


# DocumentIndex ---------------------------------------------------------------


def test_the_analysis_is_in_the_index_and_the_result_holds_findings_and_index():
    result = validate(parse(_FRONTMATTER + "# A {#a}\n\nSee {{ref: a}}. {{date: 2026-06-01}} {{money: 5, currency=EUR}}\n"))
    assert [f.name for f in dataclasses.fields(result)] == ["diagnostics", "index"]
    assert isinstance(result.index, legaldown.DocumentIndex) and isinstance(result.index.values, legaldown.InlineValues)
    assert [e.identifier for e in result.index.sections] == ["a"] and "a" in result.index.section_lookup
    assert (result.index.values.dates, result.index.values.money) == (["2026-06-01"], [("5", "EUR")])
    assert result.index.is_template is False and result.index.placed_markers == []


@pytest.mark.parametrize(
    "old",
    ["sections", "section_lookup", "definition_lookup", "party_lookup", "side_lookup", "attachment_lookup",
     "inline_dates", "inline_money", "inline_durations", "inline_fields", "inline_placeholders",
     "is_template", "placed_markers"],
)
def test_the_analysis_is_not_on_the_result_any_more(old):
    assert not hasattr(validate(parse(_SOURCE)), old)


def test_each_validation_has_an_index_of_its_own():
    first, second = validate(parse(_SOURCE)), validate(parse(_SOURCE))
    assert first.index == second.index and first.index is not second.index
    assert first.index.values is not second.index.values


def test_a_result_is_built_by_keyword_so_an_old_positional_call_fails_loudly():
    with pytest.raises(TypeError):
        ValidationResult([], [])
    assert ValidationResult(diagnostics=[], index=legaldown.DocumentIndex()).is_valid
