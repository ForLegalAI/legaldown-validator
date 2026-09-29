"""The public API (#26): what ``validate_document`` decides for a renderer —
the template decision and the markers that apply — and the helpers a
renderer builds with, importable from ``legaldown`` and ``legaldown.validator``."""
from __future__ import annotations

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
    parse_document,
    validate_document,
)
from legaldown.directives import lex
from legaldown.validator.units import find_markers

_FRONTMATTER = "---\ntitle: T\n---\n\n"


def _markers(body: str) -> list[PlacedMarker]:
    return validate_document(parse_document(_FRONTMATTER + body)).placed_markers


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
    [block] = parse_document(_FRONTMATTER + body).sections[0].blocks
    assert [list_fragments(block)[m.fragment][2][-1] for m in markers] == [0, 1, 2]


def test_an_include_only_paragraphs_identifier_does_not_apply():
    [marker] = _markers("# A\n\n{{include: x.lgd}} {#i when=b}\n")
    assert (marker.identifier, marker.condition, marker.include_only) == ("", "b", True)


def test_a_preamble_condition_applies_only_in_a_template():
    assert _markers("Intro {when=a}\n\n# A\n\nText.\n") == []
    result = validate_document(parse_document(_FRONTMATTER + "Intro {when=a}\n\n# A {when=b}\n\nText.\n"))
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
    assert not is_template(parse_document(_FRONTMATTER + "# A\n\nText.\n"))
    assert is_template(parse_document(_FRONTMATTER + "# A\n\nPick {{choose: q}}.\n"))


def test_is_drafting_note():
    [note, quote] = parse_document(_FRONTMATTER + "# A\n\n> [!drafting]\n> Check.\n\n> Plain.\n").sections[0].blocks
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
        document = parse_document(path.read_text(encoding="utf-8"))
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
