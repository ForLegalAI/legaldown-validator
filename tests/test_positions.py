"""Diagnostics name their line (§16.9, #27): where in the source each one is."""
from __future__ import annotations

import json
import random

import pytest

from legaldown import document_from_dict, document_to_dict, parse_document
from legaldown.cli import main
from legaldown.models import Block
from legaldown.parser import FrontmatterError
from legaldown.validator import validate_document

_SIDES = """sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
        legal_name: Acme Corporation
  - name: clients
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Industries Inc.
"""
_HEAD = f"---\ntitle: T\n{_SIDES}---\n\n# A\n\n"
_BODY = _HEAD.count("\n") + 1  # the body's first line after `# A` and a blank


def _lines(source: str, rule: str) -> list[int | None]:
    result = validate_document(parse_document(source, filename="t.lgd"))
    return [d.line for d in result.diagnostics if d.rule == rule]


# ── Frontmatter ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("frontmatter", "rule", "line"),
    [
        ("title: T\ndocument_type: agreement\n", "document-type-invalid", 3),
        # A missing key: the first key, where it would go.
        ("document_type: contract\n", "title-missing", 2),
        ("title: T\ndocument_type: contract\n", "sides-absent", 3),
        # A missing field of an object: the object's key.
        ("title: T\namends:\n  file: a.lgd\n", "amends-title-empty", 3),
        ("title: T\neffective_date: 2026-13-45\n", "metadata-date-invalid", 3),
        ("title: T\nfield_types:\n  Bad_Key: x\n", "field-type-key-format", 4),
        # An entry: its own line, at any depth.
        ("title: T\nsides:\n  - name: a\n    parties:\n      - name: p\n        type: corporation\n",
         "party-type-invalid", 7),
        ("title: T\nsides:\n  - name: a\n    parties:\n      - name: p\n        type: legal_entity\n"
         "  - name: b\n    parties:\n      - name: p\n        type: legal_entity\n", "party-name-duplicate", 10),
        ("title: T\nattachments:\n  - id: a\n    title: A\n    file: a.pdf\n  - id: a\n    title: B\n"
         "    file: b.pdf\n", "attachment-id-duplicate", 7),
        ("title: T\nattachments:\n  - id: a\n    title: \"\"\n    file: a.pdf\n", "attachment-title-empty", 5),
        ("title: T\nquestions:\n  unused:\n    type: boolean\n", "question-unused", 4),
        # A list entry that is not a mapping is not in the model: the lines
        # of the ones after it still fit.
        ("title: T\nsides:\n  - just text\n  - name: a\n    parties:\n      - name: p\n        type: bad\n",
         "party-type-invalid", 8),
    ],
)
def test_a_frontmatter_diagnostic_names_its_key(frontmatter, rule, line):
    assert line in _lines(f"---\n{frontmatter}---\n\n# A\n\nText.\n", rule)


def test_frontmatter_that_cannot_be_read_names_its_line():
    with pytest.raises(FrontmatterError) as raised:
        parse_document("---\ntitle: Fixture\n  bad indent: [unclosed\n---\n\n# A\n")
    assert raised.value.line == 3
    with pytest.raises(FrontmatterError) as raised:
        parse_document('---\ntitle: T\nsubtitle: "open\nlanguage: en\n---\n')
    assert raised.value.line == 3  # where the quote opens


# ── Body ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "lines"),
    [
        # A paragraph's later line.
        ("Text\nsee {{ref: nope}} here\n", [1]),
        ("Text {{ref: nope}}\nand {{ref: nope}}\n", [0, 1]),
        # In list items, nested ones, and a quote.
        ("- a\n  - b {{ref: nope}}\n- c {{ref: nope}}\n", [1, 2]),
        ("> quoted\n> {{ref: nope}}\nlazy {{ref: nope}}\n", [1, 2]),
        ("- item\n  > {{ref: nope}}\n", [1]),
        # In a table's cells.
        ("| a | b |\n|---|---|\n| x | {{ref: nope}} |\n", [2]),
        # What the lexer does not read is passed over: code, raw HTML, an
        # escaped brace — the second occurrence is the directive.
        ("Code `{{ref: nope}}` then\n{{ref: nope}}\n", [1]),
        ("\\{{ref: nope}} escaped\nthen {{ref: nope}}\n", [1]),
        ("- a\n\n      {{ref: nope}}\n\n  {{ref: nope}}\n", [4]),
        ("> ```\n> {{ref: nope}}\n> ```\n> {{ref: nope}}\n", [3]),
        ("<!-- {{ref: nope}} -->\n\n{{ref: nope}}\n", [2]),
        # A paragraph whose reference the parser lifts into its fields.
        ("See {{ref: nope}}.\n", [0]),
        ("Code `{{ref: nope}}` see\n{{ref: nope}} then {{ref: nope}}\n", [1, 1]),
    ],
)
def test_a_directive_names_its_line(body, lines):
    assert sorted(_lines(_HEAD + body, "ref-broken")) == [_BODY + line for line in lines]


def test_headings_and_definitions_name_their_lines():
    source = _HEAD + '"Term" {{def: term}} means x.\n\n### Skip\n\nThe "Other" {{def: other}} too.\n'
    assert _lines(source, "heading-skip") == [_BODY + 2]
    assert sorted(_lines(source, "def-unreferenced")) == [_BODY, _BODY + 4]


def test_a_drafting_note_and_a_quote_name_their_first_line():
    source = _HEAD + "> [!DRAFT]\n> text\n\nAnd\n\n> [!DRAFT]\n> more\n"
    assert _lines(source, "drafting-note-unrecognized") == [_BODY, _BODY + 5]


def test_a_blank_fixing_two_currencies_is_reported_at_the_second():
    body = "Pay {{placeholder: fee, type=money, currency=EUR}}.\n\nOr {{placeholder: fee, type=money, currency=USD}}.\n"
    assert _lines(_HEAD + body, "placeholder-type-inconsistent") == [_BODY + 2]


def test_lines_are_counted_in_the_source_as_written():
    source = (_HEAD + "Text\nsee {{ref: nope}}\n").replace("\n", "\r\n")
    assert _lines(source, "ref-broken") == [_BODY + 1]
    assert _lines("﻿" + _HEAD + "{{ref: nope}}\n", "ref-broken") == [_BODY]


def test_a_document_without_frontmatter_counts_from_its_first_line():
    source = "# A\n\nSee {{ref: nope}}.\n"
    assert _lines(source, "ref-broken") == [3]
    assert _lines(source, "frontmatter-absent") == [1]


def test_every_diagnostic_names_its_file():
    result = validate_document(parse_document(_HEAD + "{{ref: nope}}\n", filename="contract.lgd"))
    assert {d.file for d in result.diagnostics} == {"contract.lgd"}


# ── Documents without a source ─────────────────────────────────────


def test_a_document_from_a_dict_has_no_lines():
    document = parse_document(_HEAD + "{{ref: nope}}\n")
    rebuilt = document_from_dict(document_to_dict(document))
    assert rebuilt == document and rebuilt.source_map is None
    assert "source_map" not in document_to_dict(document)
    assert [d.line for d in validate_document(rebuilt).diagnostics] == [None]


def test_a_document_changed_after_parsing_names_no_line():
    """Its source map describes the source as parsed: stale lines would point
    at the wrong text."""
    document = parse_document(_HEAD + "{{ref: nope}}\n\nText.\n")
    document.sections[0].blocks.insert(0, Block(kind="paragraph", text="New."))
    assert [d.line for d in validate_document(document).diagnostics] == [None]
    document = parse_document(_HEAD + "Text {{ref: nope}} and {{term: nope}}.\n")
    document.sections[0].blocks[0].suffix = " and {{term: other}}."
    assert {d.line for d in validate_document(document).diagnostics} == {None}


# ── Output ─────────────────────────────────────────────────────────


def test_the_cli_writes_each_diagnostics_line(tmp_path, capsys):
    path = tmp_path / "doc.lgd"
    path.write_text(_HEAD + "{{ref: later}}\n\n{{ref: first}}\n".replace("first", "nope"), encoding="utf-8")
    main(["validate", str(path)])
    out = capsys.readouterr().out.splitlines()
    assert [line.split(": ")[0] for line in out] == [f"{path}:{_BODY}", f"{path}:{_BODY + 2}"]
    main(["validate", "--format", "json", str(path)])
    data = json.loads(capsys.readouterr().out)
    assert [d["line"] for d in data["diagnostics"]] == [_BODY, _BODY + 2]


def test_the_cli_names_the_line_of_frontmatter_it_cannot_read(tmp_path, capsys):
    path = tmp_path / "doc.lgd"
    path.write_text("---\ntitle: T\n  bad: [x\n---\n", encoding="utf-8")
    main(["validate", "--format", "json", str(path)])
    [diagnostic] = json.loads(capsys.readouterr().out)["diagnostics"]
    assert (diagnostic["rule"], diagnostic["line"]) == ("frontmatter-invalid-yaml", 3)


# ── Occurrences among decoys, at random ────────────────────────────


def _ref(n: int) -> str:
    return "{{ref: bad-" + str(n) + "}}"


def _piece(r: random.Random, n: int) -> tuple[list[str], list[tuple[int, int]]]:
    """Lines, and each real reference's (target, line index)."""
    a, b = n, n + r.randrange(2)
    pieces = [
        ([f"Text {_ref(a)} here", "more", f"and {_ref(b)} end"], [(a, 0), (b, 2)]),
        ([f"- item {_ref(a)}", f"  continued {_ref(b)}", f"  - nested {_ref(a)}"], [(a, 0), (b, 1), (a, 2)]),
        ([f"> quoted {_ref(a)}", f"lazy {_ref(b)}"], [(a, 0), (b, 1)]),
        (["| x | y |", "|---|---|", f"| {_ref(a)} | z |", f"| w | {_ref(b)} |"], [(a, 2), (b, 3)]),
        (["```", _ref(a), "```", "", f"Real {_ref(a)}."], [(a, 4)]),
        ([f"Escaped \\{_ref(a)} first", f"then {_ref(a)}"], [(a, 1)]),
        (["<div>", _ref(a), "</div>", "", f"After {_ref(a)}"], [(a, 4)]),
        (["- first", "", f"      {_ref(a)}", "", f"  real {_ref(a)}"], [(a, 4)]),
        ([f"Code `{_ref(a)}` then {_ref(a)}", f"next {_ref(a)}"], [(a, 0), (a, 1)]),
        ([f"See {_ref(a)}.", "", f"Also {_ref(a)} and {_ref(b)}."], [(a, 0), (a, 2), (b, 2)]),
        ([f"- item {_ref(a)}", "  > ```", f"  > {_ref(a)}", "  > ```", f"  > after {_ref(b)}"], [(a, 0), (b, 4)]),
    ]
    return pieces[r.randrange(len(pieces))]


@pytest.mark.parametrize("seed", range(20))
def test_each_reference_is_reported_at_its_own_line(seed):
    r = random.Random(seed)
    lines = _HEAD.rstrip("\n").split("\n") + [""]
    real = []
    for n in range(0, 2 * r.randint(2, 6), 2):
        body, refs = _piece(r, n)
        real += [(target, len(lines) + index + 1) for target, index in refs]
        lines += [*body, ""]
    result = validate_document(parse_document("\n".join(lines)))
    got = sorted(
        (int(d.message.split("'")[1].split("-")[1]), d.line) for d in result.diagnostics if d.rule == "ref-broken"
    )
    assert got == sorted(real)
