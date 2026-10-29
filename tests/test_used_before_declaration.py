"""Definitions used before their declaration (``definition-used-before-declaration``, §16.3, Info; #116).

The specification gives the rule a name and a level, and no more (§7.2 lets a ``{{def:}}`` "appear
anywhere"). So: a ``{{term:}}`` that comes before the declaration of the definition it names, in the
document's own text in the order it is written, preamble first. One Info is given for each definition,
at its first such use, with the number of them and the line of the declaration."""
from __future__ import annotations

import pytest

from legaldown import parse, validate
from legaldown.definitions import DefinitionRef
from legaldown.validator.circular import TermUse, used_before_declaration
from legaldown.validator.conditions import ALWAYS

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
_RULE = "definition-used-before-declaration"
#: The line of the first line of a body (a preamble moves it): the frontmatter's 12, a blank, the heading, a blank.
_AT = 16


def _source(body: str, frontmatter: str = "", preamble: str = "") -> str:
    return f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n{preamble}# Terms {{#terms}}\n\n{body}\n"


def _infos(body: str, frontmatter: str = "", preamble: str = "", **kwargs):
    """The (line, message) of each Info; the body is at line ``_AT``."""
    result = validate(parse(_source(body, frontmatter, preamble), filename="t.lgd"), **kwargs)
    return [(d.line, d.message) for d in result.diagnostics if d.rule == _RULE]


def _lines(body: str, frontmatter: str = "", preamble: str = "") -> list[int | None]:
    return [line for line, _message in _infos(body, frontmatter, preamble)]


_DEF = '"Services" {{def: services}} means x.'


def test_the_fixture_case_is_reported_at_the_use():
    # fixtures/invalid/definition-used-before-declaration/basic.lgd: a use in a section, the
    # definition in the next one, as the `{{term:}}` paragraph the parser lifts.
    source = _source(_DEF).replace("# Terms {#terms}\n", "# Use {#use}\n\nThe {{term: services}} apply.\n\n# Definitions {#definitions}\n")
    result = validate(parse(source, filename="t.lgd"))
    assert [(d.line, d.level) for d in result.diagnostics if d.rule == _RULE] == [(_AT, "info")]


def test_a_use_after_the_definition_draws_nothing():
    assert _infos(f"{_DEF} See {{{{term: services}}}}.\n\nThe {{{{term: services}}}} apply.") == []


def test_a_use_in_the_paragraph_before_the_anchor_is_before_it():
    body = 'See {{term: services}}. "Services" {{def: services}} means x.'
    assert _lines(body) == [_AT]
    # ... as the same words the other way round are not.
    assert _lines('"Services" {{def: services}} means x. See {{term: services}}.') == []


def test_the_message_counts_the_uses_and_names_the_declaration():
    body = "The {{term: services}} apply, and so do the {{term: services}}.\n\nAnd {{term: services}}.\n\n" + _DEF
    ((line, message),) = _infos(body)
    assert line == _AT
    assert message == "Definition 'services' is used 3 times before its declaration at line 20 (§7.2)."
    ((_line, message),) = _infos("The {{term: services}} apply.\n\n" + _DEF)
    assert message == "Definition 'services' is used once before its declaration at line 18 (§7.2)."


def test_one_info_is_given_for_each_definition_at_its_first_use():
    body = (
        "Both {{term: a}} and {{term: b}} apply. Also {{term: a}}.\n\n"
        '"A" {{def: a}} means x.\n\n"B" {{def: b}} means y.'
    )
    assert [(line, message.split(" before")[0]) for line, message in _infos(body)] == [
        (_AT, "Definition 'a' is used 2 times"),
        (_AT, "Definition 'b' is used once"),
    ]


@pytest.mark.parametrize(
    "body",
    [
        "- the {{term: a}} item\n\n" + '"A" {{def: a}} means x.',  # a list item
        "> the {{term: a}} quote\n\n" + '"A" {{def: a}} means x.',  # a block quote
        "| H |\n|---|\n| the {{term: a}} cell |\n\n" + '"A" {{def: a}} means x.',  # a table cell (line _AT + 2)
        "Text {{term: a}} with {{money: 5 EUR}} on.\n\n" + '"A" {{def: a}} means x.',  # a paragraph the parser reads whole
        "{{term: a}}\n\n" + '"A" {{def: a}} means x.',  # a paragraph that is only the term
    ],
)
def test_a_use_in_any_kind_of_text_before_the_definition_is_reported(body):
    assert _lines(body) == [_AT + 2 if body.startswith("| H") else _AT]


def test_a_use_in_the_preamble_is_before_a_definition_in_a_section():
    assert _lines('"A" {{def: a}} means x.', preamble="The {{term: a}} apply.\n\n") == [_AT - 2]
    # ... and a use in the preamble after a definition in it is not.
    assert _infos("Text.", preamble='"A" {{def: a}} means x. See {{term: a}}.\n\n') == []


def test_a_use_in_the_paragraph_around_the_definition_is_judged_by_its_place_in_it():
    # The parser reads the first {{term:}} of such a paragraph into the block's own fields,
    # between the text before it and the text after it.
    assert _infos('Text "A" {{def: a}} means x, {{term: a}} y.') == []
    assert _lines('Text {{term: a}} then "A" {{def: a}} means x.') == [_AT]
    assert _lines('Text {{term: a}} then "A" {{def: a}} means x, {{term: a}} y.') == [_AT]


def test_a_definition_of_the_same_block_is_not_after_the_text_before_it():
    assert _lines('"A" {{def: a}} means x.\n\n{{term: a}}.') == []
    assert _lines('{{term: a}}.\n\n"A" {{def: a}} means x.') == [_AT]


def test_a_note_and_code_are_no_use():
    for text in (
        "> [!drafting]\n> See {{term: a}}.",
        "`{{term: a}}`",
        "```\n{{term: a}}\n```",
        "    {{term: a}}",
        "<!-- {{term: a}} -->",
        r"\{{term: a}}",
    ):
        assert _infos(text + "\n\n" + '"A" {{def: a}} means x.') == [], text


def test_a_definition_in_a_drafting_note_is_no_declaration():
    # Assembly removes the note (and drafting-note-def reports the definition): it declares nothing.
    note = '> [!DRAFTING]\n> "A" {{def: a}} means x.'
    assert _infos(f"See {{{{term: a}}}}.\n\n{note}") == []  # a use before it is not a use before a declaration
    assert _infos(f"{note}\n\nSee {{{{term: a}}}}.") == []
    # The definition written after the note is the declaration, though it is def-duplicate-id with the
    # note's (which registers first: pre-existing), so it is not in `registered`.
    body = f'See {{{{term: a}}}}.\n\n{note}\n\n"A" {{{{def: a}}}} means y.'
    ((line, message),) = _infos(body)
    assert line == _AT and message == "Definition 'a' is used once before its declaration at line 21 (§7.2)."


def test_an_id_no_definition_declares_is_term_undefined_alone():
    result = validate(parse(_source("The {{term: nothing}} apply."), filename="t.lgd"))
    rules = {d.rule for d in result.diagnostics}
    assert "term-undefined" in rules and _RULE not in rules


def test_a_use_between_two_declarations_is_after_the_first():
    # def-duplicate-id is the second's, which is not registered: the first counts.
    body = '"A" {{def: a}} means x.\n\nSee {{term: a}}.\n\n"A" {{def: a}} means y.'
    assert _infos(body) == []


def test_alternatives_count_when_every_one_comes_later():
    questions = "questions:\n  q:\n    type: boolean\n"
    first = 'See {{term: a}}.\n\n"A" {{def: a}} means x. {when=q}\n\n"A" {{def: a}} means y. {when=!q}'
    assert _lines(first, questions) == [_AT + 3]
    # ... not when one of them comes before the use: it resolves to whichever is present (§15.4).
    between = '"A" {{def: a}} means x. {when=q}\n\nSee {{term: a}}.\n\n"A" {{def: a}} means y. {when=!q}'
    assert _infos(between, questions) == []


def test_a_use_is_judged_against_the_declaration_it_can_appear_with():
    questions = "questions:\n  q:\n    type: boolean\n"
    # The first declaration is under `q`, the use under `not q`: it resolves to the second, below it
    # (the reviewer's case: the earliest declaration cannot appear with the use).
    body = '"A" {{def: a}} means x. {when=q}\n\nSee {{term: a}}. {when=!q}\n\n"A" {{def: a}} means y. {when=!q}'
    ((line, message),) = _infos(body, questions)
    assert line == _AT + 3 + 2 and message == "Definition 'a' is used once before its declaration at line 23 (§7.2)."
    # ... and the other way round, the use under `q` is after the declaration under `q`.
    assert _infos('"A" {{def: a}} means x. {when=q}\n\nSee {{term: a}}. {when=q}\n\n"A" {{def: a}} means y. {when=!q}', questions) == []
    # An unconditional use can appear with either: it is after the first.
    assert _infos('"A" {{def: a}} means x. {when=q}\n\nSee {{term: a}}.\n\n"A" {{def: a}} means y. {when=!q}', questions) == []


def test_past_the_cap_of_the_tests_a_use_is_not_judged(monkeypatch):
    from legaldown.validator import circular as circular_module

    questions = "questions:\n  q:\n    type: boolean\n"
    body = '"A" {{def: a}} means x. {when=q}\n\nSee {{term: a}}. {when=!q}\n\n"A" {{def: a}} means y. {when=!q}'
    assert len(_infos(body, questions)) == 1
    # The first declaration cannot be told from the use (they name `q`) and the cap is reached: an Info
    # missed, not one given for a declaration the use can never appear with.
    monkeypatch.setattr(circular_module, "MAX_EXCLUSION_TESTS", 0)
    assert _infos(body, questions) == []
    # Pairs that name no question in common need no test: they are told apart under any cap.
    other = "questions:\n  q:\n    type: boolean\n  r:\n    type: boolean\n"
    assert len(_infos('See {{term: a}}. {when=r}\n\n"A" {{def: a}} means x. {when=q}', other)) == 1
    # ... one test is not enough to tell the first declaration, which is excluded, and the second; two are.
    monkeypatch.setattr(circular_module, "MAX_EXCLUSION_TESTS", 1)
    assert _infos(body, questions) == []
    monkeypatch.setattr(circular_module, "MAX_EXCLUSION_TESTS", 2)
    assert len(_infos(body, questions)) == 1


def test_past_the_cap_a_use_before_every_declaration_is_judged_by_one_known_to_fit(monkeypatch):
    from legaldown.validator import circular as circular_module

    questions = "questions:\n  q:\n    type: boolean\n  r:\n    type: boolean\n"
    first = '"A" {{def: a}} means x. {when=!q}'
    last = '"A" {{def: a}} means y. {when=r}'  # names no question the use does: told apart with no test
    before = f"See {{{{term: a}}}}. {{when=q}}\n\n{first}\n\n{last}"
    between = f"{first}\n\nSee {{{{term: a}}}}. {{when=q}}\n\n{last}"
    assert len(_infos(before, questions)) == 1 and len(_infos(between, questions)) == 1
    monkeypatch.setattr(circular_module, "MAX_EXCLUSION_TESTS", 0)
    # The first declaration could not be told (it names `q`, as the use does): the use before every
    # declaration is before the one known to fit; one between them might be after the one it resolves to.
    assert len(_infos(before, questions)) == 1
    assert _infos(between, questions) == []


def test_a_pair_of_presences_is_tested_once_whatever_the_ids(monkeypatch):
    from legaldown.validator.circular import used_before_declaration
    from legaldown.validator.conditions import Exclusivity
    from legaldown.validator.units import own_presence

    questions = {"q": {"type": "boolean"}}
    declared, used = own_presence("q", questions), own_presence("!q", questions)
    ids = 50
    registered = [(_ref(f"a{k}", 0, 2 * k + 1, 0, offset=5), declared) for k in range(ids)]
    uses = [TermUse(0, 2 * k, 0, f"a{k}", used) for k in range(ids)]
    calls = []
    wrapped = Exclusivity.excluded
    monkeypatch.setattr(Exclusivity, "excluded", lambda self, a, b: calls.append(1) or wrapped(self, a, b))
    # `q` and `not q` exclude each other: no declaration fits, and one test told it for the fifty ids.
    assert used_before_declaration(registered, uses, exclusivity=Exclusivity(questions)) == []
    assert len(calls) == 1


def test_a_declaration_that_can_never_appear_is_no_declaration():
    questions = "questions:\n  q:\n    type: boolean\n"
    body = (
        "# Never {#never when=q}\n\n"
        '- "A" {{def: a}} means x. {when=!q}\n\n'
        "# Use {#use}\n\nSee {{term: a}}.\n\n"
        "# Real {#real}\n\n"
        '"A" {{def: a}} means y.'
    )
    ((line, message),) = _infos(body, questions)
    assert line == _AT + 3 + 6 and message.endswith("at line 29 (§7.2).")
    # ... nor is a use that can never appear a use.
    never_use = body.replace("# Use {#use}\n\nSee {{term: a}}.", "# Use {#use when=q}\n\nSee {{term: a}}. {when=!q}")
    assert _infos(never_use, questions) == []


def test_a_lifted_target_is_judged_by_its_offset_in_its_paragraph():
    # Without text before it, the target is before everything the text after it holds.
    assert _lines('{{term: b}} and then "B" {{def: b}} means y.') == [_AT]
    # With some, after the definition that text holds.
    assert _infos('Text "B" {{def: b}} means x, {{term: b}} and then.') == []
    assert _infos('Text "B" {{def: b}} means x, {{term: b}} and "C" {{def: c}} means {{term: c}}.') == []


def _lifted(text: str, prefix: str, suffix: str):
    """The Infos of a document built with one term block, which has *text* too (the parser gives a
    lifted block none; a model built in code may): fragments text, prefix, suffix."""
    from legaldown import Document

    document = Document.from_dict({
        "metadata": {"title": "T"},
        "sections": [{
            "title": "S", "identifier": "s",
            "blocks": [{"kind": "term", "text": text, "prefix": prefix, "target": "a", "suffix": suffix}],
        }],
    })
    return [d.message for d in validate(document).diagnostics if d.rule == _RULE]


def test_a_lifted_target_is_between_its_text_prefix_and_suffix_whatever_they_are():
    defined = '"A" {{def: a}} means x.'
    # Text, then prefix, then the target, then the suffix: a definition in the text or the prefix is before it.
    assert _lifted(defined, "Then ", " end.") == []
    assert _lifted("Text.", defined + " Then ", " end.") == []
    assert _lifted("", defined + " Then ", " end.") == []
    # ... one in the suffix is after it, with or without text and prefix.
    assert len(_lifted("Text.", "Then ", " " + defined)) == 1
    assert len(_lifted("Text.", "", " " + defined)) == 1
    assert len(_lifted("", "", " " + defined)) == 1
    assert len(_lifted("", "Then ", " " + defined)) == 1


def test_own_term_of_is_the_one_self_reference_the_circle_has():
    from legaldown.validator.circular import own_term_of

    registered = [(_ref("s", 0, 0, 0, offset=30), ALWAYS), (_ref("t", 0, 0, 0, offset=60), ALWAYS)]
    owner = own_term_of(registered)
    assert owner(TermUse(0, 0, 0, "s", ALWAYS, 30)) == 0  # in the quoted term of the definition it names
    assert owner(TermUse(0, 0, 0, "s", ALWAYS, 60)) is None  # in another's
    assert owner(TermUse(0, 0, 0, "s", ALWAYS)) is None  # in none


def test_a_term_in_the_definitions_own_quoted_term_is_the_circles_alone():
    body = '"Self {{term: s}}" {{def: s}} means x.'
    result = validate(parse(_source(body), filename="t.lgd"))
    rules = {d.rule for d in result.diagnostics}
    assert "definition-circular" in rules and _RULE not in rules
    # ... but the quoted term of another definition is not exempt for an id it does not define.
    assert _lines('"Self {{term: t}}" {{def: s}} means x.\n\n"T" {{def: t}} means y.') == [_AT]


def test_an_id_the_original_declares_is_in_force_before_the_amendments_redefinition():
    originals = {"original.lgd": f'---\ntitle: Original\n{_SIDES}---\n\n# Terms {{#terms}}\n\n{_DEF}\n'}
    front = "amends:\n  title: Original\n  file: original.lgd\n"
    body = "See {{term: services}}.\n\n" + _DEF
    source = _source(body, front)
    assert [d.rule for d in validate(parse(source, filename="t.lgd"), resolve=originals.get).diagnostics
            if d.rule == _RULE] == []
    assert [d.rule for d in validate(parse(source, filename="t.lgd")).diagnostics if d.rule == _RULE] == [_RULE]


def test_a_fragment_is_judged_by_its_own_text():
    fragment = parse("# Scope\n\nSee {{term: a}}.\n\n" + '"A" {{def: a}} means x.\n')
    assert [d.line for d in validate(fragment, fragment=True).diagnostics if d.rule == _RULE] == [3]


def test_the_position_of_a_declaration_in_a_list_or_a_table_is_its_fragments():
    assert _lines('- "A" {{def: a}} means x.\n- See {{term: a}}.') == []
    assert _lines('- See {{term: a}}.\n- "A" {{def: a}} means x.') == [_AT]
    assert _lines('| "A" {{def: a}} | See {{term: a}} |\n|---|---|\n| x | y |') == []


# ── The function ──────────────────────────────────────────────────


def _ref(def_id, section, block, fragment=None, offset=0):
    return DefinitionRef(def_id, def_id, section, block, fragment, fragment is not None, False, offset=offset)


def test_section_zero_is_after_the_preamble():
    """A use in the first section is not before a definition in it, nor the preamble's after it."""
    registered = [(_ref("a", 0, 1), ALWAYS)]
    uses = [TermUse(0, 0, 0, "a", ALWAYS), TermUse(None, 5, 0, "a", ALWAYS)]
    assert [(index, [use.section for use in earlier]) for index, earlier in used_before_declaration(registered, uses)] == [
        (0, [None, 0])
    ]
    registered = [(_ref("a", None, 0), ALWAYS)]
    assert used_before_declaration(registered, [TermUse(0, 0, 0, "a", ALWAYS)]) == []
