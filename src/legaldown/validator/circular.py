"""Circular definitions (spec §16.3 ``definition-circular``, §7.2).

Per §7.2 a definition's scope, "for purposes such as circular-reference
detection", is the paragraph containing its ``{{def:}}``: one definition is
explained in terms of another when a ``{{term:}}`` of the other is in that
paragraph. A set of definitions that are each explained in terms of the next,
round to the first, is circular (#105).

The relation is a graph with two kinds of node: the definitions that are
registered (``DefinitionRef``) and the paragraphs they are in. A definition
has an edge to its own paragraph, and a paragraph to every definition one of
its ``{{term:}}`` names. A circle is a *strongly connected component* with at
least two distinct definitions, reported once however many circles it holds (a
large one holds exponentially many).

A reference in a definition's own paragraph does not make it circular: the
specification's own fixtures write ``"Services" {{def: services}} means x. See
{{term: services}}.`` as ordinary wording, so one definition and its paragraph
are never a circle. The one self-reference reported is the one the definition
cannot mean anything else by: a ``{{term:}}`` to itself in its own quoted term
(``"Self {{term: s}}" {{def: s}}``). The paragraph cannot tell which definition
in it a reference explains, so two definitions in one paragraph that it
references both are taken to explain each other: the approximation of
paragraph scope (#105).

The graph is built from the paragraphs and references, so the work is linear
in them, even when one paragraph holds thousands of definitions and
references, and the components are found without recursion: a document can be
a ring of any length (#94).
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
from typing import NamedTuple

from ..definitions import DefinitionRef, find_definition_anchors, fragment_scopes
from ..directives import Directive, Lexed
from ..models import Document
from .conditions import Exclusivity, Presence
from .result import Line

#: The most pairs of presences ``find_circular_definitions`` tests, given an
#: ``Exclusivity``. Past it alternatives are no longer told apart: a reference
#: reaches all of an id's alternatives, as it does in a document that is no
#: template. That can only add circles, never hide one.
MAX_EXCLUSION_TESTS = 4_000_000


class TermUse(NamedTuple):
    """A ``{{term:}}`` outside a drafting note (assembly removes those, §15.6):
    the *target* it names, its *presence* (§15.3), and where it is --
    ``fragment`` indexes ``block_fragments(block)``, and is None for the
    target the parser lifted into the block's fields."""

    section: int | None
    block: int
    fragment: int | None
    target: str
    presence: Presence
    #: The offset in its fragment of the ``{{def:}}`` whose quoted term holds
    #: this use (``"Self {{term: s}}" {{def: s}}``), or None.
    owner: int | None = None
    #: Its offset in its fragment, and its line (§16.9, found only if asked): for
    #: ``used_before_declaration``. For the target the parser lifted (no fragment) the offset is
    #: where it was read in its paragraph: the length of the text before it.
    offset: int = 0
    line: Line = None


def term_owners(
    fragment: str, lexed: Lexed, language: str | None = None
) -> Callable[[Directive], int | None]:
    """For the text *fragment* (lexed as *lexed*), a function that gives, for
    a directive in it, the offset of the ``{{def:}}`` whose quoted term holds
    it (``TermUse.owner``), or None."""
    if not any(directive.name == "def" for directive in lexed.directives):
        return lambda _directive: None
    # Each quoted term, from its opening mark to the offset of its {{def:}}.
    spans = sorted(
        (anchor.start, anchor.directive.start)
        for anchor in find_definition_anchors(fragment, language=language, lexed=lexed)
        if anchor.term is not None and not anchor.directive.malformed
    )
    starts = [start for start, _def in spans]

    def owner(directive: Directive) -> int | None:
        at = bisect_right(starts, directive.start) - 1
        return spans[at][1] if at >= 0 and directive.end <= spans[at][1] else None

    return owner


def own_term_of(registered: Sequence[tuple[DefinitionRef, Presence]]) -> Callable[[TermUse], int | None]:
    """For a use, the index in *registered* of the definition whose own quoted term holds it, when
    the use names that definition (``"Self {{term: s}}" {{def: s}}``), else None: the one
    self-reference ``definition-circular`` reports, and no ordering one."""
    defined_at = {
        (ref.section_index, ref.block_index, ref.fragment_index, ref.offset): number
        for number, (ref, _presence) in enumerate(registered)
    }

    def owner(use: TermUse) -> int | None:
        if use.owner is None:
            return None
        number = defined_at.get((use.section, use.block, use.fragment, use.owner))
        return number if number is not None and registered[number][0].id == use.target else None

    return owner


def used_before_declaration(
    registered: Sequence[tuple[DefinitionRef, Presence]],
    uses: Iterable[TermUse],
    imported: Collection[str] = (),
    exclusivity: Exclusivity | None = None,
) -> list[tuple[int, list[TermUse]]]:
    """The definitions that *uses* name before their declaration, in the order the document is
    written, preamble first (``definition-used-before-declaration``, §16.3, #116).

    Each is an index into *registered* (the definitions that count, as for
    ``find_circular_definitions``), with its uses before the declaration in document order,
    ordered by the first of them. A use is judged against the first declaration of its id that
    can appear with it: given an *exclusivity* (a template's, §15.4), one that can appear and is
    not excluded from the use's presence, so a use between two alternatives is judged against the
    one it resolves to, and a declaration that can never appear is no declaration. A definition
    of an id in *imported* is in force before the document's text (§7.5), and an id none of
    *registered* declares is no definition of the document (an imported one is a target only, as
    there); a ``{{term:}}`` in the definition's own quoted term is the circle's
    (``own_term_of``). A use in a drafting note is not in *uses*."""
    own_term = own_term_of(registered)

    def place(section: int | None, block: int, fragment: int, offset: int) -> tuple[int, int, int, int]:
        # The preamble before the first section; within a block, the fragments in order.
        return (-1 if section is None else section, block, fragment, offset)

    declared: dict[str, list[tuple[tuple[int, int, int, int], int]]] = {}  # per id: where, and the index
    for number, (ref, _presence) in enumerate(registered):
        # The anchor a definition block holds in its own fields comes before its text.
        fragment = -1 if ref.fragment_index is None else ref.fragment_index
        declared.setdefault(ref.id, []).append((place(ref.section_index, ref.block_index, fragment, ref.offset), number))
    for group in declared.values():
        group.sort()

    first: dict[tuple[str, Presence], tuple[tuple[int, int, int, int], int] | None] = {}

    def first_declaration(target: str, presence: Presence) -> tuple[tuple[int, int, int, int], int] | None:
        if (target, presence) not in first:
            first[target, presence] = next(
                (
                    each for each in declared[target]
                    if exclusivity is None
                    or (exclusivity.appears(registered[each[1]][1]) and not exclusivity.excluded(presence, registered[each[1]][1]))
                ),
                None,
            )
        return first[target, presence]

    earlier: dict[int, list[tuple[tuple[int, int, int, int], TermUse]]] = {}
    for use in uses:
        if use.target not in declared or use.target in imported or own_term(use) is not None:
            continue
        if exclusivity is not None and not exclusivity.appears(use.presence):
            continue
        declaration = first_declaration(use.target, use.presence)
        if declaration is None:
            continue
        # A lifted target is in the first fragment, where the parser read it: at its offset in the paragraph.
        written = place(use.section, use.block, 0 if use.fragment is None else use.fragment, use.offset)
        if written < declaration[0]:
            earlier.setdefault(declaration[1], []).append((written, use))
    for group in earlier.values():
        group.sort(key=lambda each: each[0])
    return [
        (number, [use for _written, use in group])
        for number, group in sorted(earlier.items(), key=lambda each: each[1][0][0])
    ]


def strongly_connected_components(
    count: int, successors: Callable[[int], Iterable[int]]
) -> list[list[int]]:
    """The strongly connected components of the graph of the nodes
    ``range(count)`` whose edges from a node are ``successors(node)``
    (Tarjan's algorithm; ``successors`` is called once for each node).

    Iterative: a chain of any length takes no recursion, and the time is linear
    in the nodes and edges. A component is a list of nodes, in no order."""
    index = [-1] * count  # the order a node was first reached in
    low = [0] * count  # the lowest index a node reaches through a descendant
    on_stack = [False] * count
    stack: list[int] = []
    components: list[list[int]] = []
    order = 0
    for root in range(count):
        if index[root] != -1:
            continue
        index[root] = low[root] = order
        order += 1
        stack.append(root)
        on_stack[root] = True
        work: list[tuple[int, Iterator[int]]] = [(root, iter(successors(root)))]
        while work:
            node, remaining = work[-1]
            for follow in remaining:
                if index[follow] == -1:
                    index[follow] = low[follow] = order
                    order += 1
                    stack.append(follow)
                    on_stack[follow] = True
                    work.append((follow, iter(successors(follow))))
                    break
                if on_stack[follow]:
                    low[node] = min(low[node], index[follow])
            else:
                work.pop()
                if low[node] == index[node]:
                    component: list[int] = []
                    while True:
                        member = stack.pop()
                        on_stack[member] = False
                        component.append(member)
                        if member == node:
                            break
                    components.append(component)
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
    return components


def find_circular_definitions(
    document: Document,
    registered: Sequence[tuple[DefinitionRef, Presence]],
    uses: Iterable[TermUse],
    exclusivity: Exclusivity | None = None,
) -> list[list[int]]:
    """The circles among the definitions of *document*.

    *registered* are the definitions that count -- those registered, so not a
    duplicate or one with an invalid id -- each with its presence, in document
    order; *uses* are the document's ``{{term:}}`` references. A reference to
    an id none of *registered* declares (an imported definition, §7.5,
    §12.4, or none) leads nowhere: imported definitions are targets only,
    and a circle through several files is Full (§17.4).

    A circle is a set of definitions with at least two ids that lead round to
    each other, or a single definition whose quoted term holds a
    ``{{term:}}`` to its own id. A reference in a definition's own paragraph
    is no circle by itself.

    *exclusivity* tells whether two presences can never hold together (§15.4);
    it is given only for a template. A reference does not lead to a
    declaration it can never appear with: of the alternatives of one id
    (§15.4) it reaches those it can appear with. That prunes a circle only
    where an edge is impossible, one reference and one alternative at a time:
    a circle whose members exclude each other further round (``d1`` when
    ``q``, ``d3`` when not ``q``, with ``d2`` and ``d4`` between) is still
    found, as a Warning is better given than withheld. A reference is tested
    only against the alternatives that name a question it names (the others
    can appear with it, unless one of them can never appear: those are never
    reached), and at most ``MAX_EXCLUSION_TESTS`` pairs are tested in all.
    Past that, a reference reaches every alternative of the id it names that
    can appear, which can only join circles, never split one: no circular
    definition goes unreported, though circles may merge into fewer Warnings,
    at other lines.

    Each circle is a list of indices into *registered*, ascending. They are
    ordered by their last index: the definition where the circle closes, in
    document order, which a diagnostic is reported at."""
    count = len(registered)
    # The scopes of the blocks that hold a definition: a reference in another
    # block cannot be on a circle, and its scopes are not worked out.
    scopes: dict[tuple[int | None, int], list[int]] = {}
    for ref, _presence in registered:
        section, index = ref.section_index, ref.block_index
        if (section, index) not in scopes:
            block = document.preamble[index] if section is None else document.sections[section].blocks[index]
            scopes[section, index] = fragment_scopes(block)

    def scope_of(section: int | None, block: int, fragment: int | None) -> tuple[int | None, int, int]:
        # Everything in a top-level block that has no fragments of its own (a
        # definition block, a lifted target) is its scope 0.
        return section, block, 0 if fragment is None else scopes[section, block][fragment]

    # Nodes: the definitions, then their paragraphs, then the ids of several
    # alternatives (below).
    scope_node: dict[tuple[int | None, int, int], int] = {}
    own_scope: list[int] = []
    declarations: dict[str, list[int]] = {}
    for number, (ref, _presence) in enumerate(registered):
        scope = scope_of(ref.section_index, ref.block_index, ref.fragment_index)
        own_scope.append(scope_node.setdefault(scope, count + len(scope_node)))
        declarations.setdefault(ref.id, []).append(number)
    # What a reference can reach of an id: its declarations, but for those that can
    # never appear, which no reference reaches in a template (they are excluded
    # with every presence).
    reachable = declarations
    if exclusivity is not None:
        reachable = {
            def_id: [number for number in numbers if exclusivity.appears(registered[number][1])]
            for def_id, numbers in declarations.items()
        }
    # The reachable declarations of an id are one node when a reference reaches
    # them all: a paragraph that mentions thousands of alternatives of one id, or
    # a thousand paragraphs that mention the same alternatives, are then not a
    # product of edges.
    id_node: dict[str, int] = {}
    for def_id, numbers in reachable.items():
        if len(numbers) > 1:
            id_node[def_id] = count + len(scope_node) + len(id_node)

    # The distinct (target, presence) each paragraph with a definition names.
    references: dict[int, dict[tuple[str, Presence], None]] = {}  # in order
    owner_of = own_term_of(registered)
    own_term: set[int] = set()  # the definitions whose quoted term holds a {{term:}} to their id
    for use in uses:
        if use.target not in declarations or (use.section, use.block) not in scopes:
            continue
        owner = owner_of(use)
        if owner is not None:
            own_term.add(owner)
        scope = scope_of(use.section, use.block, use.fragment)
        if scope in scope_node:
            references.setdefault(scope_node[scope], {})[use.target, use.presence] = None

    reached: dict[tuple[str, Presence], list[int]] = {}
    tests = 0  # the pairs of presences tested: pruning stops at MAX_EXCLUSION_TESTS
    named: dict[str, dict[str, list[int]]] = {}  # per id, as needed: its numbers by the questions they name

    def reach_excluding(target: str, presence: Presence) -> list[int] | None:
        """What a reference with *presence* reaches among the alternatives of
        *target* that can appear, tested only against those that name a question
        it names: one that can appear cannot exclude a reference that can appear
        unless they share a question. None when the tests would pass the cap."""
        nonlocal tests
        numbers = reachable[target]
        if not exclusivity.appears(presence):
            return []  # it excludes every alternative
        if target not in named:
            named[target] = {}
            for number in numbers:
                for question in exclusivity.questions(registered[number][1]):
                    named[target].setdefault(question, []).append(number)
        by_question = named[target]
        questions = exclusivity.questions(presence)
        if len(questions) == 1:
            candidates: Iterable[int] = by_question.get(next(iter(questions)), ())  # in order
        else:
            candidates = sorted({n for question in questions for n in by_question.get(question, ())})
        excluded_numbers: set[int] = set()
        for number in candidates:
            if tests >= MAX_EXCLUSION_TESTS:
                return None
            tests += 1
            if exclusivity.excluded(presence, registered[number][1]):
                excluded_numbers.add(number)
        return [number for number in numbers if number not in excluded_numbers] if excluded_numbers else numbers

    def reach(target: str, presence: Presence) -> list[int]:
        if (target, presence) not in reached:
            numbers = reachable[target]
            keep = numbers
            if exclusivity is not None:
                pruned = reach_excluding(target, presence)
                keep = numbers if pruned is None else pruned  # past the cap: the alternatives are not told apart
            reached[target, presence] = [id_node[target]] if len(keep) == len(numbers) > 1 else keep
        return reached[target, presence]

    total = count + len(scope_node) + len(id_node)
    ids_of_node = {node: def_id for def_id, node in id_node.items()}

    def successors(node: int) -> Iterable[int]:
        if node < count:
            return (own_scope[node],)
        if node < count + len(scope_node):
            return (
                reached_node
                for target, presence in references.get(node, ())
                for reached_node in reach(target, presence)
            )
        return reachable[ids_of_node[node]]

    circles = []
    for component in strongly_connected_components(total, successors):
        members = sorted(member for member in component if member < count)
        if len({registered[member][0].id for member in members}) > 1:
            circles.append(members)
    # A definition whose own quoted term holds a {{term:}} to itself.
    in_circle = {member for circle in circles for member in circle}
    circles.extend([number] for number in sorted(own_term - in_circle))
    circles.sort(key=lambda circle: circle[-1])
    return circles


def circular_message(ids: Iterable[str]) -> str:
    """The Warning for a circle of definitions with these *ids*, in document
    order (§16.3): at most five are named."""
    named = list(dict.fromkeys(ids))
    quoted = [f"'{def_id}'" for def_id in named[:5]]
    if len(named) == 1:
        return f"Definition {quoted[0]} is circular: its own term refers to it (§7.2)."
    listed = ", ".join(quoted[:-1]) + f" and {quoted[-1]}"
    if len(named) > 5:
        listed = ", ".join(quoted) + f" and {len(named) - 5} more"
    return (
        f"Definitions {listed} are circular: each is explained, directly or through the others, "
        f"in terms of itself (§7.2)."
    )
