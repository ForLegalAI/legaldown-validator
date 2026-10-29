"""Circular definitions (``definition-circular``, §16.3, §7.2; #105).

A definition's scope is the paragraph holding its ``{{def:}}`` (§7.2): a
definition is explained in terms of another when a ``{{term:}}`` of the other
is in that paragraph, and definitions that are each explained in terms of
the next, round to the first, are circular. One Warning is given for each
set of them, at the definition that closes the circle: the last of them in
document order.
"""
from __future__ import annotations

import inspect
import sys

import pytest

from legaldown import parse, validate
from legaldown.definitions import DefinitionRef, block_fragments
from legaldown.models import empty_document
from legaldown.parser import MAX_QUOTE_DEPTH
from legaldown.validator import circular as circular_module
from legaldown.validator.circular import (
    TermUse,
    circular_message,
    find_circular_definitions,
    strongly_connected_components,
)
from legaldown.validator.conditions import ALWAYS, Condition, Exclusivity, exclusive

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
_Q = "questions:\n  q:\n    type: boolean\n"


def _source(body: str, frontmatter: str = "") -> str:
    return f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n# Terms {{#terms}}\n\n{body}\n"


def _circles(body: str, frontmatter: str = "", **kwargs) -> list[tuple[int | None, str]]:
    """The (line, message) of each definition-circular, with the body at line 14
    (frontmatter 12 lines, then the heading)."""
    result = validate(parse(_source(body, frontmatter), filename="t.lgd"), **kwargs)
    return [(d.line, d.message) for d in result.diagnostics if d.rule == "definition-circular"]


def _lines(body: str, frontmatter: str = "") -> list[int | None]:
    return [line for line, _message in _circles(body, frontmatter)]


def _first_line(body: str, frontmatter: str = "") -> int:
    """The line of the body's first paragraph."""
    return _source(body, frontmatter).split("\n").index(body.split("\n")[0]) + 1


# ── The rule ──────────────────────────────────────────────────────


def test_the_fixture_case_is_reported_at_the_second_definition():
    # fixtures/invalid/definition-circular/basic.lgd, which the conformance
    # harness runs when the corpus is there: the line is the second definition's.
    body = (
        '"Services" {{def: services}} means work under the {{term: agreement}}.\n\n'
        '"Agreement" {{def: agreement}} means this document governing the {{term: services}}.'
    )
    ((line, message),) = _circles(body)
    assert line == _first_line(body) + 2
    assert message == (
        "Definitions 'services' and 'agreement' are circular: each is explained, directly or through "
        "the others, in terms of itself (§7.2)."
    )


def test_it_is_a_warning_with_no_host_dependence():
    result = validate(parse(_source('"A {{term: a}}" {{def: a}}'), filename="t.lgd"))
    (diagnostic,) = (d for d in result.diagnostics if d.rule == "definition-circular")
    assert diagnostic.level == "warning"
    assert not diagnostic.host_dependent


def test_a_definition_whose_term_references_itself_is_circular():
    body = '"Self {{term: s}}" {{def: s}}'
    ((line, message),) = _circles(body)
    assert line == _first_line(body)
    assert message == "Definition 's' is circular: its own term refers to it (§7.2)."


def test_a_pair_is_one_warning_on_the_second_definition():
    body = '"Alpha {{term: b}}" {{def: a}}\n\n"Beta {{term: a}}" {{def: b}}'
    assert _lines(body) == [_first_line(body) + 2]


@pytest.mark.parametrize(
    "body",
    [
        '"Services" {{def: services}} means work. See {{term: services}}.',
        '"Services" {{def: services}} means work under {{term: services}}.',
        '{{term: services}} and "Services" {{def: services}} mean work.',
        '"A" {{def: a}} means x. "B" {{def: b}} means {{term: a}}.',
        '"A" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} means x. See {{term: b}}.',
    ],
)
def test_a_reference_in_the_paragraph_of_its_definition_is_no_circle(body):
    # The idiom the specification's own fixtures write as ordinary wording (17
    # of its cases): a definition and its paragraph are not a circle.
    assert _circles(body) == []


@pytest.mark.parametrize(
    ("shape", "body"),
    [
        ("definition block", '"Self {{term: s}}" {{def: s}} means x.'),
        ("inline definition", 'Here, a "Self {{term: s}}" {{def: s}} is x.'),
        ("term block", 'Then {{term: s}} and "Self {{term: s}}" {{def: s}}.'),
        ("list item", '- "Self {{term: s}}" {{def: s}} is x.'),
        ("block quote", '> "Self {{term: s}}" {{def: s}} is x.'),
        ("heading in a block quote", '> ## "Self {{term: s}}" {{def: s}}'),
        ("table row", '| a | b |\n|---|---|\n| "Self {{term: s}}" {{def: s}} | x |'),
    ],
)
def test_a_term_that_references_itself_is_circular_wherever_it_is(shape, body):
    ((line, message),) = _circles(body)
    assert line is not None and "Definition 's' is circular" in message, shape


_BOTH = '"A" {{def: a}} and "B" {{def: b}} use {{term: a}} and {{term: b}}.'


@pytest.mark.parametrize(
    ("shape", "body"),
    [
        ("definition block", '"A" {{def: a}} and "B" {{def: b}} use {{term: a}} and {{term: b}}.'),
        ("term block, reference before", '{{term: a}} and "A" {{def: a}} and "B" {{def: b}} use {{term: b}}.'),
        ("list item", "- " + _BOTH),
        ("nested list item", "- Item.\n\n  - " + _BOTH),
        ("block quote", "> " + _BOTH),
        ("heading in a block quote", "> ## " + _BOTH),
        ("table row", '| a | b |\n|---|---|\n| "A" {{def: a}} | "B" {{def: b}} use {{term: a}} {{term: b}} |'),
        ("table header row", '| "A" {{def: a}} | "B" {{def: b}} {{term: a}} {{term: b}} |\n|---|---|\n| x | y |'),
    ],
)
def test_definitions_that_a_paragraph_references_both_are_a_circle_in_each_scope(shape, body):
    # The approximation of paragraph scope: it cannot tell which of the two a
    # reference explains.
    assert len(_circles(body)) == 1, shape


@pytest.mark.parametrize(
    ("shape", "body"),
    [
        ("next paragraph", '"A" {{def: a}} and "B" {{def: b}} are x.\n\nSee {{term: a}} and {{term: b}}.'),
        ("two paragraphs of a list item", '- "A" {{def: a}} and "B" {{def: b}}.\n\n  See {{term: a}} {{term: b}}.'),
        ("two items", '- "A" {{def: a}} and "B" {{def: b}}.\n- See {{term: a}} {{term: b}}.'),
        ("two paragraphs of a quote", '> "A" {{def: a}} and "B" {{def: b}}.\n>\n> See {{term: a}} {{term: b}}.'),
        (
            "two rows of a table",
            '| a | b |\n|---|---|\n| "A" {{def: a}} "B" {{def: b}} | x |\n| y | {{term: a}} {{term: b}} |',
        ),
        ("a quote and its host", '"A" {{def: a}} and "B" {{def: b}}.\n\n> See {{term: a}} {{term: b}}.'),
    ],
)
def test_references_in_another_scope_are_no_circle(shape, body):
    assert _circles(body) == [], shape


def test_a_definition_in_a_section_heading_is_not_collected():
    # A {{def:}} in a heading is no definition (§7.2: the paragraph is the
    # scope), so the only heading that can be a scope is one in a block quote.
    body = '## "Bar {{term: bar}}" {{def: bar}} {#bar}\n\ntext'
    assert _circles(body) == []


def test_a_quote_too_deep_to_read_is_one_scope():
    def quote(depth: int) -> str:
        prefix = "> " * depth
        return (
            f'{prefix}"A" {{{{def: a}}}} and "B" {{{{def: b}}}}.\n{prefix.rstrip()}\n'
            f"{prefix}See {{{{term: a}}}} {{{{term: b}}}}."
        )

    assert _circles(quote(2)) == []
    assert len(_circles(quote(MAX_QUOTE_DEPTH + 2))) == 1


# ── What is no cycle ──────────────────────────────────────────────


def test_a_chain_is_no_cycle():
    body = (
        '"A" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} means {{term: c}}.\n\n'
        '"C" {{def: c}} means x.\n\nSee {{term: a}}.'
    )
    assert _circles(body) == []


def test_two_definitions_in_one_paragraph_that_reference_a_third_are_no_cycle():
    body = '"A" {{def: a}} and "B" {{def: b}} use {{term: c}}.\n\n"C" {{def: c}} is x.\n\n{{term: a}} {{term: b}}'
    assert _circles(body) == []


def test_a_reference_to_an_undeclared_term_is_no_cycle():
    assert _circles('"A" {{def: a}} means {{term: nowhere}}.\n\nSee {{term: a}}.') == []


@pytest.mark.parametrize(
    "reference",
    ["`{{term: a}}`", "``{{term: a}}``", "<!-- {{term: a}} -->", r"\{{term: a}}"],
)
def test_a_reference_that_is_literal_is_not_counted(reference):
    assert _circles(f'"A" {{{{def: a}}}} means {{{{term: b}}}}.\n\n"B" {{{{def: b}}}} is x. {reference}') == []


def test_the_sub_list_of_a_definition_is_not_part_of_its_scope():
    # §7.2: the scope is the paragraph holding the {{def:}}. The "means: (a)
    # ... (b) ..." layout puts the explanation in the item's sub-list, which a
    # later change may decide to include; until then this is deliberate.
    sub_list = '- "A" {{def: a}} means:\n  - the {{term: b}}\n\n- "B" {{def: b}} means:\n  - the {{term: a}}'
    assert _circles(sub_list) == []
    in_the_paragraph = '- "A" {{def: a}} means the {{term: b}}.\n- "B" {{def: b}} means the {{term: a}}.'
    assert len(_circles(in_the_paragraph)) == 1


def test_a_definition_in_a_drafting_note_whose_term_references_itself_is_no_circle():
    # drafting-note-def reports the definition; assembly removes the note (§15.6).
    body = '> [!DRAFTING]\n> "Self {{term: s}}" {{def: s}}\n\nSee {{term: s}}.'
    result = validate(parse(_source(body, _Q), filename="t.lgd"))
    assert "drafting-note-def" in result.rules()
    assert "definition-circular" not in result.rules()


def test_a_definition_that_is_a_duplicate_or_invalid_is_not_in_the_graph():
    duplicate = '"A" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} is x.\n\n"B" {{def: b}} means {{term: a}}.'
    # The first B is a plain definition, the second is rejected (def-duplicate-id).
    assert _circles(duplicate) == []
    invalid = '"A" {{def: A_1}} means {{term: b}}.\n\n"B" {{def: b}} means {{term: A_1}}.'
    assert _circles(invalid) == []


# ── Several circles ───────────────────────────────────────────────


def test_a_circle_of_three_names_them_in_document_order():
    body = (
        '"A" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} means {{term: c}}.\n\n'
        '"C" {{def: c}} means {{term: a}}.'
    )
    ((line, message),) = _circles(body)
    assert line == _first_line(body) + 4
    assert message == (
        "Definitions 'a', 'b' and 'c' are circular: each is explained, directly or through "
        "the others, in terms of itself (§7.2)."
    )


def test_two_disjoint_circles_are_two_warnings_in_the_order_they_close():
    # The circles are {a, d} and {b, c}: the second closes first (at c).
    body = (
        '"A" {{def: a}} means {{term: d}}.\n\n"B" {{def: b}} means {{term: c}}.\n\n'
        '"C" {{def: c}} means {{term: b}}.\n\n"D" {{def: d}} means {{term: a}}.'
    )
    first = _first_line(body)
    circles = _circles(body)
    assert [line for line, _ in circles] == [first + 4, first + 6]
    assert "'b' and 'c'" in circles[0][1]
    assert "'a' and 'd'" in circles[1][1]


def test_a_circle_with_a_tail_is_one_warning_at_the_last_member_of_the_circle():
    body = (
        '"Tail" {{def: tail}} means {{term: a}}.\n\n"A" {{def: a}} means {{term: b}}.\n\n'
        '"B" {{def: b}} means {{term: a}}.\n\n"Head" {{def: head}} is x. {{term: b}}'
    )
    assert _lines(body) == [_first_line(body) + 4]


def test_a_large_circle_names_five_definitions_and_counts_the_rest():
    count = 9
    body = "\n\n".join(
        f'"D{k}" {{{{def: d{k}}}}} means {{{{term: d{(k + 1) % count}}}}}.' for k in range(count)
    )
    ((line, message),) = _circles(body)
    assert line == _first_line(body) + 2 * (count - 1)
    assert message == (
        "Definitions 'd0', 'd1', 'd2', 'd3', 'd4' and 4 more are circular: each is explained, "
        "directly or through the others, in terms of itself (§7.2)."
    )


def test_the_report_is_the_same_every_time():
    body = "\n\n".join(
        f'"A{k}" {{{{def: a{k}}}}} means {{{{term: b{k}}}}}.\n\n"B{k}" {{{{def: b{k}}}}} means {{{{term: a{k}}}}}.'
        for k in range(30)
    )
    first = _circles(body)
    assert len(first) == 30
    assert [line for line, _ in first] == sorted(line for line, _ in first)
    assert all(_circles(body) == first for _ in range(3))


def test_a_term_that_references_itself_in_a_circle_is_one_warning():
    body = '"A {{term: a}}" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} means {{term: a}}.'
    ((line, message),) = _circles(body)
    assert line == _first_line(body) + 2
    assert "'a' and 'b'" in message


def test_the_alternatives_of_one_id_that_can_appear_together_are_one_id_not_a_circle():
    # Without the pruning (no exclusivity), as the validator calls it for a
    # document that is no template: an id declared twice is still one id.
    body = [
        '"T" {{def: t}} means x. See {{term: t}}.',
        '"T" {{def: t}} means y. See {{term: t}}.',
    ]
    document = _paragraph_document(body)
    registered = [
        (DefinitionRef("t", "T", 0, k, 0, True, False), ALWAYS) for k in range(2)
    ]
    uses = [TermUse(0, k, 0, "t", ALWAYS) for k in range(2)]
    assert find_circular_definitions(document, registered, uses) == []


def test_the_message_names_each_id_once():
    assert circular_message(["a", "b", "a"]) == circular_message(["a", "b"])
    assert circular_message(["a", "a"]) == circular_message(["a"])


# ── Alternatives (§15.4) ──────────────────────────────────────────


def test_alternatives_that_cannot_appear_together_do_not_refer_to_each_other():
    # b[!q] names `a`, which is a[q] or a[!q]: only a[!q] can be there with it,
    # and that one names nothing. Without the pruning, b -> a[q] -> b is a circle.
    body = (
        '"A" {{def: a}} means {{term: b}}. {when=q}\n\n"A" {{def: a}} means x. {when=!q}\n\n'
        '"B" {{def: b}} means {{term: a}}. {when=!q}'
    )
    assert _circles(body, _Q) == []


def test_alternatives_that_can_appear_together_are_a_circle():
    body = (
        '"A" {{def: a}} means {{term: b}}. {when=q}\n\n"A" {{def: a}} means x. {when=!q}\n\n'
        '"B" {{def: b}} means {{term: a}}. {when=q}'
    )
    ((line, _message),) = _circles(body, _Q)
    assert line == _first_line(body, _Q) + 4


def test_a_circle_whose_members_exclude_each_other_further_round_is_still_reported():
    # The documented approximation: only a reference and the declaration it
    # names are tested. d1 needs q and d3 needs not q, so no assembled document
    # has the circle, but no two neighbours exclude each other.
    body = (
        '"D1" {{def: d1}} means {{term: d2}}. {when=q}\n\n"D2" {{def: d2}} means {{term: d3}}.\n\n'
        '"D3" {{def: d3}} means {{term: d4}}. {when=!q}\n\n"D4" {{def: d4}} means {{term: d1}}.'
    )
    assert len(_circles(body, _Q)) == 1


def test_a_document_that_is_no_template_is_not_asked_whether_alternatives_exclude(monkeypatch):
    from legaldown.validator import core

    def refused(*_args):
        raise AssertionError("exclusive was called")

    body = '"A" {{def: a}} means {{term: b}}.\n\n"B" {{def: b}} means {{term: a}}.'
    document = parse(_source(body), filename="t.lgd")
    monkeypatch.setattr(core, "exclusive", refused)
    assert [d.rule for d in validate(document).diagnostics if d.rule == "definition-circular"]


# ── Imported definitions, fragments ───────────────────────────────

_AMENDMENT = """---
title: First Amendment
document_type: contract
amends:
  title: Services Agreement
  file: services-agreement.lgd
{sides}---

# Terms {{#terms}}

{body}
"""
_ORIGINAL = '---\ntitle: Original\n---\n\n# A\n\n"Services" {{def: services}} means {{term: fees}}.\n\n"Fees" {{def: fees}} means x.\n'


def _amendment(body: str):
    document = parse(_AMENDMENT.format(sides=_SIDES, body=body), filename="a.lgd")
    result = validate(document, resolve=lambda path: _ORIGINAL)
    return [(d.rule, d.line) for d in result.diagnostics if d.rule in ("definition-circular", "amend-def-override")]


def test_an_imported_definition_is_a_target_and_never_a_source():
    # `services` and `fees` are the original's; the amendment's definition
    # names them, and the original's own `services` -> `fees` is no circle.
    rows = _amendment('"Late fee" {{def: late-fee}} is {{term: fees}} on {{term: services}}.')
    assert rows == []


def test_a_definition_that_shadows_an_imported_one_is_still_a_source():
    rows = _amendment(
        '"Services" {{def: services}} means {{term: late-fee}}.\n\n"Late fee" {{def: late-fee}} is {{term: services}}.'
    )
    assert [rule for rule, _line in rows] == ["amend-def-override", "definition-circular"]


def test_a_fragment_reports_its_own_circle_and_it_is_not_host_dependent():
    result = validate(parse('## Scope\n\n"A" {{def: a}} is {{term: b}}.\n\n"B" {{def: b}} is {{term: a}}.\n'), fragment=True)
    (diagnostic,) = (d for d in result.diagnostics if d.rule == "definition-circular")
    assert not diagnostic.host_dependent
    assert diagnostic.line == 5


# ── Linear time on hostile input (#94) ────────────────────────────


class _Search:
    """Counts what the component search does: the nodes it was given, the
    calls for a node's successors, and the edges they gave."""

    def __init__(self, monkeypatch):
        self.nodes = self.calls = self.edges = 0
        wrapped = circular_module.strongly_connected_components

        def counted(count, successors):
            self.nodes = count

            def counting(node):
                self.calls += 1
                edges = list(successors(node))
                self.edges += len(edges)
                return edges

            return wrapped(count, counting)

        monkeypatch.setattr(circular_module, "strongly_connected_components", counted)


def _paragraph_document(paragraphs: list[str]):
    """A document of these top-level paragraphs, parsed as one section."""
    return parse(_source("\n\n".join(paragraphs)), filename="t.lgd")


def _graph(document, template=False):
    """What ``validate`` gives the check: every definition and every
    ``{{term:}}``, the paragraphs in a section being one block each."""
    from legaldown.definitions import collect_definitions
    from legaldown.directives import lex

    registered = [(ref, ALWAYS) for ref in collect_definitions(document)]
    uses = []
    for section, block, node in document.iter_indexed_blocks():
        for fragment, (text, _position) in enumerate(block_fragments(node)):
            uses += [
                TermUse(section, block, fragment, d.positional, ALWAYS)
                for d in lex(text).directives
                if d.name == "term"
            ]
        if node.kind == "term":
            uses.append(TermUse(section, block, None, node.target, ALWAYS))
    return registered, uses


def test_a_chain_of_any_length_takes_no_recursion():
    length = 100_000
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(len(inspect.stack(0)) + 80)  # a recursive search would overflow it
    try:
        chain = strongly_connected_components(length, lambda node: [node + 1] if node + 1 < length else [])
        ring = strongly_connected_components(length, lambda node: [(node + 1) % length])
    finally:
        sys.setrecursionlimit(old)
    assert len(chain) == length
    assert [len(component) for component in ring] == [length]


def test_components_are_found_in_each_shape():
    # 0 -> 1 -> 2 -> 0, 2 -> 3, 3 -> 3 (a self-loop is one node), 4 alone.
    edges = {0: [1], 1: [2], 2: [0, 3], 3: [3], 4: []}
    components = strongly_connected_components(5, lambda node: edges[node])
    assert sorted(sorted(component) for component in components) == [[0, 1, 2], [3], [4]]


@pytest.mark.parametrize("shape", ["ring", "one paragraph", "two-cycles", "chain"])
def test_circles_among_many_definitions_are_found_in_linear_work(shape, monkeypatch):
    n = 80_000 if shape == "ring" else 20_000
    if shape == "ring":
        paragraphs = [f'"D{k}" {{{{def: d{k}}}}} means {{{{term: d{(k + 1) % n}}}}}.' for k in range(n)]
        circles = 1
    elif shape == "one paragraph":
        paragraphs = [" ".join(f'"D{k}" {{{{def: d{k}}}}} {{{{term: d{(k + 1) % n}}}}}' for k in range(n))]
        circles = 1
    elif shape == "two-cycles":
        paragraphs = [
            f'"A{k}" {{{{def: a{k}}}}} is {{{{term: b{k}}}}}.\n\n"B{k}" {{{{def: b{k}}}}} is {{{{term: a{k}}}}}.'
            for k in range(n // 2)
        ]
        circles = n // 2
    else:
        paragraphs = [f'"D{k}" {{{{def: d{k}}}}} means {{{{term: d{k + 1}}}}}.' for k in range(n)]
        paragraphs.append(f'"D{n}" {{{{def: d{n}}}}} is x.')
        circles = 0
    document = _paragraph_document(paragraphs)
    registered, uses = _graph(document)
    search = _Search(monkeypatch)
    found = find_circular_definitions(document, registered, uses)
    assert len(found) == circles
    # Each node's successors are asked for once, and an edge is a definition's
    # paragraph or a reference: a search from each definition would be quadratic.
    assert search.nodes <= 2 * len(registered)
    assert search.calls == search.nodes
    assert search.edges <= len(registered) + len(uses)


def test_a_ring_of_definitions_in_the_validator_is_one_warning_at_the_last_definition():
    n = 3000
    body = "\n\n".join(f'"D{k}" {{{{def: d{k}}}}} means {{{{term: d{(k + 1) % n}}}}}.' for k in range(n))
    ((line, message),) = _circles(body)
    assert line == _first_line(body) + 2 * (n - 1)
    assert message.startswith("Definitions 'd0', 'd1', 'd2', 'd3', 'd4' and 2995 more are circular")


def _alternatives(count: int, referenced_when: str | None):
    """An id declared by *count* alternatives, one for each value of a choice
    question, and *count* paragraphs that each define another id, name that
    one, and are named by the alternatives. ``referenced_when`` gives each of
    these paragraphs a presence of its own."""
    paragraphs = []
    registered = []
    uses = []
    questions = {"c": {"type": "choice", "choices": {f"v{k}": str(k) for k in range(count)}}}
    for k in range(count):
        paragraphs.append(f'"T" {{{{def: t}}}} means {{{{term: u{k}}}}}.')
    for k in range(count):
        paragraphs.append(f'"U" {{{{def: u{k}}}}} means {{{{term: t}}}}.')
    document = _paragraph_document(paragraphs)
    for k in range(count):
        presence = frozenset({Condition("c", f"v{k}")})
        registered.append((DefinitionRef("t", "T", 0, k, 0, True, False, offset=0), presence))
        uses.append(TermUse(0, k, 0, f"u{k}", presence))
    for k in range(count):
        presence = ALWAYS if referenced_when is None else frozenset({Condition("c", f"v{k}")})
        registered.append((DefinitionRef(f"u{k}", "U", 0, count + k, 0, True, False, offset=0), presence))
        uses.append(TermUse(0, count + k, 0, "t", presence))
    return document, registered, uses, questions


class _Exclusive(Exclusivity):
    """An ``Exclusivity`` for a choice question, counting its pair tests."""

    def __init__(self, questions):
        super().__init__(questions)
        self.calls = 0

    def excluded(self, first, second):
        self.calls += 1
        return super().excluded(first, second)


class _EveryPair(Exclusivity):
    """What was asked before #110: every pair of a reference and an alternative is tested by ``exclusive``,
    whether or not either can appear or they share a question."""

    def appears(self, presence):
        return True

    def questions(self, presence):
        return frozenset(self._questions)

    def excluded(self, first, second):
        return exclusive(first, second, self._questions)


def test_two_thousand_alternatives_named_by_two_thousand_paragraphs_test_each_pair_of_presences_once(monkeypatch):
    document, registered, uses, questions = _alternatives(2000, None)
    excluded = _Exclusive(questions)
    search = _Search(monkeypatch)
    found = find_circular_definitions(document, registered, uses, excluded)
    assert len(found) == 1
    assert len(found[0]) == 4000  # every alternative and every `u`
    # The 2000 paragraphs that name `t` have one presence, so it is tested
    # once against each alternative; and each alternative's reference to its
    # own `u` is tested once.
    assert excluded.calls <= 2 * 2000 + 2000
    # And a reference that reaches every alternative is one edge to the id,
    # not one for each alternative: linear, not 2000 x 2000.
    assert search.calls == search.nodes
    assert search.edges <= 3 * len(registered)


def test_references_with_presences_of_their_own_are_tested_against_each_alternative_once():
    # The bound: a pair of presences once, so references r with distinct
    # presences against a alternatives are at most r * a tests, none repeated.
    count = 150
    document, registered, uses, questions = _alternatives(count, "own")
    excluded = _Exclusive(questions)
    found = find_circular_definitions(document, registered, uses, excluded)
    assert len(found) == count  # each u_k is named by t[v_k] alone, and names t
    assert excluded.calls <= count * count + count
    again = _Exclusive(questions)
    assert find_circular_definitions(document, registered, uses, again) == found
    assert again.calls == excluded.calls


def test_a_deep_ring_in_the_validator_takes_no_recursion():
    # The validator as a whole: a ring this long would overflow a recursive search.
    n = 3000
    body = "\n\n".join(f'"D{k}" {{{{def: d{k}}}}} means {{{{term: d{(k + 1) % n}}}}}.' for k in range(n))
    document = parse(_source(body), filename="t.lgd")
    old = sys.getrecursionlimit()
    try:
        sys.setrecursionlimit(1500)
        assert len([d for d in validate(document).diagnostics if d.rule == "definition-circular"]) == 1
    finally:
        sys.setrecursionlimit(old)


def test_an_empty_document_has_no_circle():
    assert find_circular_definitions(empty_document(), [], []) == []


# ── Exclusivity and the cap on pair tests (#110) ──────────────────

_RANDOM_QUESTIONS = {
    "a": {"type": "boolean"},
    "b": {"type": "boolean"},
    "c": {"type": "choice", "choices": {"x": "X", "y": "Y", "z": "Z"}},
    "d": {"type": "choice", "choices": {"only": "The only value"}},
}
_RANDOM_CONDITIONS = ["a", "!a", "b", "!b", "c:x", "c:y", "!c:x", "!c:y", "!c:z", "d:only", "!d:only"]


def _random_presence(rng):
    """ALWAYS, one to three conditions, and contradictions, which never appear."""
    from legaldown.validator.units import own_presence

    presence = ALWAYS
    for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
        presence = presence | own_presence(rng.choice(_RANDOM_CONDITIONS), _RANDOM_QUESTIONS)
    return presence


def test_exclusivity_is_exclusive_and_satisfiable():
    import random

    from legaldown.validator.conditions import satisfiable

    rng = random.Random(110)
    exclusivity = Exclusivity(_RANDOM_QUESTIONS)
    seen = set()
    for _ in range(20000):
        first, second = _random_presence(rng), _random_presence(rng)
        expected = exclusive(first, second, _RANDOM_QUESTIONS)
        assert exclusivity.excluded(first, second) == expected, (first, second)
        assert exclusivity.appears(first) == satisfiable(first, _RANDOM_QUESTIONS)
        assert set(exclusivity.questions(first)) == {c.question for c in first}
        seen.add((expected, exclusivity.appears(first) and exclusivity.appears(second)))
    # Every kind of pair: excluded by a shared question, by one that cannot appear, and not.
    assert seen == {(False, True), (True, True), (True, False)}


def _random_graph(rng):
    """Paragraphs of one definition and one reference each, of ids with several
    alternatives, under presences of their own, many of which never appear."""
    n = rng.randint(3, 14)
    ids = [f"i{k}" for k in range(rng.randint(2, 5))]
    paragraphs = [
        f'"D{k}" {{{{def: {rng.choice(ids)}}}}} means {{{{term: {rng.choice(ids)}}}}}.' for k in range(n)
    ]
    document = _paragraph_document(paragraphs)
    registered, uses = _graph(document)
    registered = [(ref, _random_presence(rng)) for ref, _presence in registered]
    uses = [use._replace(presence=_random_presence(rng)) for use in uses]
    return document, registered, uses


def _members(circles):
    return {member for circle in circles for member in circle}


@pytest.mark.parametrize("seed", range(300))
def test_circles_through_exclusivity_equal_circles_through_the_call(seed):
    import random

    document, registered, uses = _random_graph(random.Random(110_000 + seed))
    expected = find_circular_definitions(document, registered, uses, _EveryPair(_RANDOM_QUESTIONS))
    assert find_circular_definitions(document, registered, uses, Exclusivity(_RANDOM_QUESTIONS)) == expected


_NEVER_APPEARS = """---
title: T
questions:
  q:
    type: boolean
  r:
    type: boolean
---

# Terms {#terms when=q}

- "A" {{def: a}} means {{term: b}}. {when=!q}

# More {#more}

"B" {{def: b}} means {{term: a}}. {when=r}
"""


def test_a_definition_that_never_appears_is_no_part_of_a_circle():
    """``a`` is declared when ``q`` and not ``q``: never. Nothing reaches it, whichever question the
    reference to it names (a prefilter by shared questions found a circle here)."""
    result = validate(parse(_NEVER_APPEARS, filename="t.lgd"))
    assert [(d.rule, d.line) for d in result.diagnostics] == [
        ("sides-absent", 2),
        ("condition-never-true", 12),
        ("definition-used-before-declaration", 12),  # `b`, which `a` uses, is declared below
        ("condition-reference-unsafe", 16),
    ]


def _two_questions(count: int, same: bool):
    """An id declared by *count* alternatives, one for each value of the choice
    question ``c``, and *count* definitions of other ids that name it, each
    under a value of ``c`` (*same*) or of another choice question ``d``; the
    alternatives name those, so every one is on a circle."""
    questions = {
        "c": {"type": "choice", "choices": {f"v{k}": str(k) for k in range(count)}},
        "d": {"type": "choice", "choices": {f"w{k}": str(k) for k in range(count)}},
    }
    paragraphs = [f'"T" {{{{def: t}}}} means {{{{term: u{k}}}}}.' for k in range(count)]
    paragraphs += [f'"U" {{{{def: u{k}}}}} means {{{{term: t}}}}.' for k in range(count)]
    document = _paragraph_document(paragraphs)
    registered, uses = _graph(document)
    from legaldown.validator.units import own_presence

    registered = [
        (ref, own_presence(f"c:v{k}" if k < count else ("c:v" if same else "d:w") + f"{k - count}", questions))
        for k, (ref, _presence) in enumerate(registered)
    ]
    uses = [
        use._replace(presence=own_presence(f"c:v{k}" if k < count else ("c:v" if same else "d:w") + f"{k - count}", questions))
        for k, use in enumerate(uses)
    ]
    return document, registered, uses, questions


class _Tests:
    """Counts the pairs ``Exclusivity.excluded`` is asked about."""

    def __init__(self, monkeypatch):
        self.calls = 0
        wrapped = Exclusivity.excluded

        def counted(exclusivity, first, second):
            self.calls += 1
            return wrapped(exclusivity, first, second)

        monkeypatch.setattr(Exclusivity, "excluded", counted)


def test_references_on_another_question_test_no_pair(monkeypatch):
    count = 300
    document, registered, uses, questions = _two_questions(count, same=False)
    tests = _Tests(monkeypatch)
    search = _Search(monkeypatch)
    found = find_circular_definitions(document, registered, uses, Exclusivity(questions))
    assert len(found) == 1 and len(found[0]) == 2 * count
    assert tests.calls == 0  # an alternative of `c` is not excluded by a reference that names `d`
    assert search.edges <= 3 * len(registered)  # each reference reaches the id, not each alternative


def test_references_on_the_same_question_test_each_pair_once(monkeypatch):
    count = 150
    document, registered, uses, questions = _two_questions(count, same=True)
    tests = _Tests(monkeypatch)
    found = find_circular_definitions(document, registered, uses, Exclusivity(questions))
    assert len(found) == count
    assert tests.calls <= count * count + count


def test_pair_tests_stop_at_the_cap_and_only_add_circles(monkeypatch):
    count = 150
    document, registered, uses, questions = _two_questions(count, same=True)
    exact = find_circular_definitions(document, registered, uses, Exclusivity(questions))
    exact_members = _members(exact)
    previous = exact_members
    for cap in (count * count, 10_000, 1000, 100, 1, 0):
        monkeypatch.setattr(circular_module, "MAX_EXCLUSION_TESTS", cap)
        tests = _Tests(monkeypatch)
        found = find_circular_definitions(document, registered, uses, Exclusivity(questions))
        assert tests.calls <= cap
        assert _members(found) >= previous  # a lower cap flags every definition a higher one did
        previous = _members(found)
        again = find_circular_definitions(document, registered, uses, Exclusivity(questions))
        assert again == found  # whichever pairs were tested, the same each time
    assert len(found) == 1 and len(found[0]) == 2 * count  # nothing told apart: one circle through the id
    assert len(exact) == count  # which the exact answer splits


@pytest.mark.parametrize("seed", range(100))
def test_a_cap_never_hides_a_circular_definition(seed, monkeypatch):
    import random

    rng = random.Random(110_500 + seed)
    document, registered, uses = _random_graph(rng)
    exact = _members(find_circular_definitions(document, registered, uses, Exclusivity(_RANDOM_QUESTIONS)))
    monkeypatch.setattr(circular_module, "MAX_EXCLUSION_TESTS", rng.randint(0, 12))
    capped = _members(find_circular_definitions(document, registered, uses, Exclusivity(_RANDOM_QUESTIONS)))
    assert capped >= exact


def _with_a_dead_alternative(count: int):
    """``_two_questions`` with the first alternative of the id under ``c:v0`` and not ``c:v0``: never."""
    from legaldown.validator.units import own_presence

    document, registered, uses, questions = _two_questions(count, same=False)
    dead = own_presence("c:v0", questions) | own_presence("!c:v0", questions)
    registered[0] = (registered[0][0], dead)
    uses[0] = uses[0]._replace(presence=dead)
    return document, registered, uses, questions


def test_a_dead_alternative_is_left_out_of_the_alternatives_a_reference_reaches():
    """The same circles as testing every pair, and the references on another question still reach the id as one
    node (a dead alternative among them once made each of them an edge to every alternative)."""
    count = 40
    document, registered, uses, questions = _with_a_dead_alternative(count)
    found = find_circular_definitions(document, registered, uses, Exclusivity(questions))
    assert found == find_circular_definitions(document, registered, uses, _EveryPair(questions))
    assert 0 not in _members(found) and len(found) == 1  # nothing reaches the dead one


def test_a_dead_alternative_does_not_make_each_reference_an_edge_to_each_alternative(monkeypatch):
    count = 1000
    document, registered, uses, questions = _with_a_dead_alternative(count)
    search = _Search(monkeypatch)
    tests = _Tests(monkeypatch)
    found = find_circular_definitions(document, registered, uses, Exclusivity(questions))
    assert len(found) == 1 and len(found[0]) == 2 * count - 2
    assert tests.calls == 0
    assert search.edges <= 3 * len(registered)  # 4 million before


def test_a_validation_builds_one_exclusivity_for_the_identifiers_and_the_circles(monkeypatch):
    built = []
    init = Exclusivity.__init__

    def counted(self, questions):
        built.append(questions)
        init(self, questions)

    monkeypatch.setattr(Exclusivity, "__init__", counted)
    body = (
        '"Services" {{def: services}} means work under the {{term: agreement}}. {when=q}\n\n'
        '"Agreement" {{def: agreement}} means the {{term: services}}.\n'
    )
    document = parse(_source(body, _Q), filename="t.lgd")
    result = validate(document)
    assert [d.rule for d in result.diagnostics if d.rule == "definition-circular"] == ["definition-circular"]
    assert len(built) == 1
