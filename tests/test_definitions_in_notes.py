"""A ``{{def:}}`` in a drafting note is no definition of the document (#125, §15.6): assembly removes the note
with it, and ``drafting-note-def`` reports it. It is not registered, so it makes no duplicate of a real
definition, and a ``{{term:}}`` that only it declares is undefined."""
from __future__ import annotations

import pytest

from legaldown import collect_definitions, definition_lookup, parse, validate
from legaldown.files import file_loader
from legaldown.markdown import DraftingNotes

_SIDES = """sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
  - name: clients
    parties:
      - name: beta
        type: legal_entity
"""
_QUESTIONS = "questions:\n  x:\n    type: choice\n    choices:\n      a: A\n      b: B\n"
_NOTE = '> [!DRAFTING]\n> "Fee" {{def: fee}} is a note definition.\n'
_REAL = '"Fee" {{def: fee}} means the real fee.'


def _source(body: str, frontmatter: str = "") -> str:
    return f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n# Fees {{#fees}}\n\n{body}\n"


def _result(body: str, frontmatter: str = "", **kwargs):
    return validate(parse(_source(body, frontmatter), filename="t.lgd"), **kwargs)


def _rules(body: str, frontmatter: str = "", **kwargs) -> list[str]:
    return [d.rule for d in _result(body, frontmatter, **kwargs).diagnostics]


# ── The two repros of #125 ────────────────────────────────────────

@pytest.mark.parametrize("order", ["note-first", "note-last"])
def test_a_real_definition_of_a_notes_id_is_no_duplicate(order):
    body = f"{_NOTE}\n{_REAL}" if order == "note-first" else f"{_REAL}\n\n{_NOTE}"
    result = _result(body + "\n\nPay the {{term: fee}}.")
    assert [d.rule for d in result.diagnostics] == ["drafting-note-def"]
    assert result.index.definition_lookup == {"fee": "Fee"}


def test_a_term_declared_only_in_a_note_is_undefined():
    result = _result(f"{_NOTE}\nPay the {{{{term: fee}}}}.")
    assert sorted((d.rule, d.line) for d in result.diagnostics) == [("drafting-note-def", 17), ("term-undefined", 19)]
    assert result.index.definition_lookup == {}


# ── Terms in notes, references, other rules ───────────────────────

def test_a_term_in_a_note_resolves_against_definitions_outside_notes_only():
    assert _rules(f"{_REAL}\n\n> [!DRAFTING]\n> Remember {{{{term: fee}}}}.") == []
    # even the note's own definition: it is not one
    note = '> [!DRAFTING]\n> "Fee" {{def: fee}} means x. See {{term: fee}}.'
    assert sorted(_rules(note)) == ["drafting-note-def", "term-undefined"]


def test_a_ref_to_a_notes_definition_id_is_as_broken_as_ever():
    # a definition is no anchor (§5.6): {{ref:}} never reaches one, in a note or out of it
    assert "ref-broken" in _rules(f"{_NOTE}\nSee {{{{ref: fee}}}}.")
    assert "ref-broken" in _rules(f"{_REAL}\n\nSee {{{{ref: fee}}}}.")


def test_a_note_definition_is_not_unreferenced_nor_does_it_hide_the_real_ones_line():
    assert "def-unreferenced" not in _rules(_NOTE + "\nText.")
    # the Warning is the real definition's, at its own line (it was the note's, then)
    result = _result(f"{_NOTE}\n{_REAL}")
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule == "def-unreferenced"] == [("def-unreferenced", 19)]


def test_a_note_definition_does_not_collide_with_an_auto_generated_id():
    body = '> [!DRAFTING]\n> "Foo Bar" {{def:}} means x.\n\n"Foo-Bar" {{def:}} means y.\n\n{{term: foo-bar}}'
    assert _rules(body) == ["drafting-note-def"]


def test_a_note_definition_with_an_invalid_id_draws_only_drafting_note_def():
    assert _rules('> [!DRAFTING]\n> "Fee" {{def: Bad_ID}} means x.\n\nText.') == ["drafting-note-def"]


@pytest.mark.parametrize(
    "body",
    [
        '- Item\n\n  > [!DRAFTING]\n  > "Fee" {{def: fee}} in an item.\n\n' + _REAL,
        '> [!DRAFTING]\n> text\n>\n> > "Fee" {{def: fee}} nested.\n\n' + _REAL,
        '> [!drafting]\n> "Fee" {{def: fee}} lower case.\n\n' + _REAL,
    ],
    ids=["in a list item", "in a quote in a note", "marker in lower case"],
)
def test_a_definition_in_a_note_wherever_it_is_is_not_registered(body):
    assert _rules(body) == ["drafting-note-def", "def-unreferenced"]


@pytest.mark.parametrize(
    ("anchor", "rule"),
    [
        ("Fee {{def: fee}} means x.", "def-no-quoted-span"),
        ('**"Fee"** {{def: fee}} means x.', "def-emphasis"),
    ],
    ids=["no quoted span", "emphasis around the term"],
)
def test_the_form_checks_of_a_directive_still_run_on_a_note_definition(anchor, rule):
    # the directive is recognized and validated (§15.6); only its identifier goes unregistered
    rules = _rules(f"> [!DRAFTING]\n> {anchor}\n\nText.")
    assert rule in rules
    assert "drafting-note-def" in rules


def test_only_the_definitions_in_the_notes_of_a_list_are_left_out():
    # notes and definitions alternate in one list (and a note holds a quote, which holds a note): each is placed by its own fragment
    items = "\n".join(
        f'- Item {n}\n  > [!DRAFTING]\n  > "N{n}" {{{{def: n{n}}}}} in a note.\n\n  "R{n}" {{{{def: r{n}}}}} means x.' for n in range(3)
    )
    body = f"{items}\n- Last\n  > [!DRAFTING]\n  > > [!DRAFTING]\n  > > \"In\" {{{{def: inner}}}} x.\n\n  \"Out\" {{{{def: out}}}} y."
    document = parse(_source(body))
    assert [ref.id for ref in collect_definitions(document)] == ["r0", "r1", "r2", "out"]


def test_a_definition_after_a_note_in_a_note_is_still_in_the_outer_note():
    body = '> [!DRAFTING]\n> text\n>\n> > [!DRAFTING]\n> > inner\n>\n> "Late" {{def: late}} x.\n\n' + _REAL
    document = parse(_source(body))
    assert [ref.id for ref in collect_definitions(document)] == ["fee"]
    assert "term-undefined" in _rules(body + "\n\nPay the {{term: late}}.")


@pytest.mark.parametrize(
    ("quotes", "inside", "outside"),
    [
        # a note in a note, then more of the outer one
        ([("[!DRAFTING]", range(0, 5)), ("[!DRAFTING]", range(1, 2))], [0, 1, 2, 4], [5]),
        # a note without a fragment, inside a note and after one
        ([("[!DRAFTING]", range(0, 3)), ("[!DRAFTING]", range(1, 1)), ("[!DRAFTING]", range(3, 3))], [0, 1, 2], [3, 4]),
        # quotes that are no notes count for nothing, whatever they hold
        ([("[!NOTE]", range(0, 3)), ("", range(1, 1))], [], [0, 1, 2, 3]),
        # two notes apart
        ([("[!drafting]", range(1, 2)), ("[!DRAFTING]", range(4, 6))], [1, 4, 5], [0, 2, 3, 6]),
    ],
    ids=["nested", "empty", "no notes", "apart"],
)
def test_drafting_notes_place_a_fragment_by_the_outermost_notes(quotes, inside, outside):
    notes = DraftingNotes(quotes)
    assert [i for i in [*inside, *outside] if i in notes] == inside
    assert bool(notes) == bool(inside)


def test_an_ordinary_quote_is_no_note():
    assert "def-duplicate-id" in _rules('> "Fee" {{def: fee}} means x.\n\n' + _REAL)


def test_used_before_declaration_and_circles_ignore_the_note_as_before():
    early = f"Pay the {{{{term: fee}}}}.\n\n{_NOTE}\n{_REAL}"
    assert _rules(early) == ["definition-used-before-declaration", "drafting-note-def"]
    circle = '> [!DRAFTING]\n> "A" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} means {{term: a}}.'
    assert "definition-circular" not in _rules(circle)


# ── Conditional alternatives (§15.4) ──────────────────────────────

_ALTERNATIVES = """# Fee A {{#fee-a when=x:a}}

{a}"Fee" {{{{def: fee}}}} means A.

# Fee B {{#fee-b when=x:b}}

{b}"Fee" {{{{def: fee}}}} means B.

# Use {{#use}}

Pay the {{{{term: fee}}}}."""


def _alternatives(a: str = "", b: str = "") -> list[str]:
    source = f"---\ntitle: Fixture\n{_SIDES}{_QUESTIONS}---\n\n" + _ALTERNATIVES.format(a=a, b=b) + "\n"
    return [d.rule for d in validate(parse(source)).diagnostics]


def test_alternatives_with_a_note_definition_in_one_are_not_duplicates():
    assert _alternatives() == []
    assert _alternatives(a=_NOTE + "\n") == ["drafting-note-def"]
    assert _alternatives(a=_NOTE + "\n", b=_NOTE + "\n") == ["drafting-note-def", "drafting-note-def"]


def test_a_note_definition_does_not_make_a_conditional_term_safe_or_unsafe():
    # the note's definition is under x:a, the real one unconditional: a duplicate and an unsafe use, both false
    body = f"""# Opt {{#opt when=x:a}}

{_NOTE}
Text.

# Use {{#use}}

{_REAL}

Pay the {{{{term: fee}}}}."""
    rules = [d.rule for d in validate(parse(f"---\ntitle: Fixture\n{_SIDES}{_QUESTIONS}---\n\n{body}\n")).diagnostics]
    assert rules == ["drafting-note-def"]


# ── Files read for their definitions (§7.5, §12.4) ────────────────

def _files(tmp_path, files: dict[str, str], main: str):
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    document = parse((tmp_path / main).read_text(encoding="utf-8"), filename=main)
    return validate(document, resolve=file_loader(tmp_path))


def test_an_amended_originals_note_definition_is_not_imported(tmp_path):
    amends = "amends:\n  file: original.lgd\n  title: Original\n"
    result = _files(
        tmp_path,
        {"original.lgd": _source(_NOTE + "\nText."), "amendment.lgd": _source("Pay the {{term: fee}}.", amends)},
        "amendment.lgd",
    )
    assert "amend-term-undefined" in [d.rule for d in result.diagnostics]


def test_an_amendments_note_definition_does_not_override_the_originals(tmp_path):
    amends = "amends:\n  file: original.lgd\n  title: Original\n"
    result = _files(
        tmp_path,
        {"original.lgd": _source(_REAL), "amendment.lgd": _source(_NOTE + "\nPay the {{term: fee}}.", amends)},
        "amendment.lgd",
    )
    assert "amend-def-override" not in [d.rule for d in result.diagnostics]


def test_an_attachment_files_note_definition_is_not_imported(tmp_path):
    attachments = "attachments:\n  - id: schedule\n    title: Schedule\n    file: schedule.lgd\n"
    main = _source(f"{_REAL}\n\nPay the {{{{term: fee}}}}. See {{{{attach: schedule}}}}.", attachments)
    result = _files(tmp_path, {"schedule.lgd": _source(_NOTE + "\nText."), "main.lgd": main}, "main.lgd")
    assert "def-duplicate-id" not in [d.rule for d in result.diagnostics]


# ── The public helpers agree (#37) ────────────────────────────────

def test_collect_definitions_leaves_out_note_definitions():
    document = parse(_source(f"{_NOTE}\n{_REAL}\n\n> [!DRAFTING]\n> \"Other\" {{{{def: other}}}} x."))
    assert [(ref.id, ref.fragment_index) for ref in collect_definitions(document)] == [("fee", None)]
    assert definition_lookup(collect_definitions(document)) == validate(document).index.definition_lookup == {"fee": "Fee"}
