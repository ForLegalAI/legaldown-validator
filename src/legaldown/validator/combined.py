"""The combined document (§12.2, §15.7.2 step 7, §16.11): the document with the fragments its include
paragraphs take in spliced in where they stand, for the checks that look across them (#117).

§16.11 requires the combined document to be validated, and ``validate`` does so by running its checks over
it. The document is built from the parsed fragments (``Files`` reads and parses each once), not from their
text: the blocks of a fragment are the ones the file was parsed to, shared and never copied, and each
combined section and block remembers where it came from (``Combined.section_origin``, ``block_origin``), so
that a finding is placed at its line in its own file (``CombinedLocator``) and a unit has the presence it has
there (``CombinedUnits``). A document built from a dict or edited since it was parsed has no text to splice,
and has these as much as any other.

The splice is what splicing the text parses to (§12.2: "spliced verbatim at the directive position"): the
include paragraph stays where it is, the preamble of the fragment follows it, its sections after those, and
what comes after the include in the document goes on in the last section of the expansion. It differs from
a splice of the text only for a fragment that is itself malformed, since each file keeps its own structure:
a fence a fragment leaves open does not swallow the document that follows, and a list the fragment ends
with is not continued by one the document starts after it. Assembly writes the fragments as files of their
own too. The markers are read over the combined structure, as a splice of the text would read them (a
fragment's first paragraph, spliced into a section, is a paragraph of that section: it may carry an anchor),
while a unit's presence is what its own file says (a section extends to the end of its file, §15.3).

What is spliced is what the walk of the includes followed (``fragments._Walk``, ``Plan``), and only when it
followed every one and the fragments are not past ``fragments.MAX_SPLICED_BLOCKS`` and ``MAX_SPLICED_LINES``.
"""
from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ..directives import Lexed
from ..models import Block, Document, Section
from ..positions import Locator
from .conditions import ALWAYS, Presence
from .patterns import SKIPPED_RULE
from .result import Diagnostic, Spot
from .units import FoundMarker, Units, find_markers

if TYPE_CHECKING:
    from .fragments import Files, FragmentFile


@dataclass(slots=True)
class Plan:
    """What the walk of the includes followed in a file (the document, or a fragment): for each include
    paragraph of it that took in a fragment, by its (section, block) in the file, that fragment and the plan
    of what it followed in turn. ``weave`` splices it."""
    children: dict[tuple[int | None, int], tuple[FragmentFile, Plan]] = field(default_factory=dict)


@dataclass(eq=False, slots=True)
class Instance:
    """A place a file has in the combined document: the document itself (*path* ``""``, *number* 0) or a
    fragment at one of its includes (a fragment included twice is in two places), numbered in the order
    they are spliced. *parent* is the place holding the include paragraph and *at* its (section, block) in
    the file of *parent*; they give the presence of the include (§15.3), which the whole fragment has."""
    number: int
    path: str
    document: Document
    where: Locator
    parent: Instance | None = None
    at: tuple[int | None, int] = (None, 0)

    def spot(self, line: int | None) -> int | None:
        """*line* of this file, as a diagnostic names it: the file goes with a line of a fragment."""
        return line if not self.path else Spot(line, self.path, self.number)


@dataclass(eq=False, slots=True)
class Combined:
    """The combined document (*document*) and where its parts are from: *section_origin* has each of its
    sections as the (place, section index) it is a copy of, and *block_origin* each block of the preamble
    (first) and of the sections, as the (place, section index or None, block index) of the file it is
    from. *where* is the locator of the document the includes are in; *instances* are the places of its
    files; *files* and *lex_fragment* are the validation's, so that nothing is read or lexed twice.
    *template* is whether that document is a template (§15.1): the combined pass judges as it does, since the
    assembler decides so from the document alone. *imports* is what the validation of the document imported
    from the files it names (``core._check``), for the combined pass not to ask again."""
    document: Document
    where: Locator
    files: Files | None
    lex_fragment: Callable[[str], Lexed]
    instances: list[Instance]
    section_origin: list[tuple[Instance, int]]
    block_origin: list[list[tuple[Instance, int | None, int]]]
    template: bool = False
    imports: dict[Any, Any] = field(default_factory=dict)
    _markers: list[FoundMarker] | None = None
    _local: dict[Instance, list[FoundMarker]] = field(default_factory=dict)
    _first: dict[str, int] = field(default_factory=dict)

    def of_block(self, section: int | None, block: int) -> tuple[Instance, int | None, int]:
        """The place, section index and block index in its file of block *block* of the preamble
        (*section* None) or of section *section*."""
        return self.block_origin[0 if section is None else section + 1][block]

    def markers(self) -> list[FoundMarker]:
        """The markers of the combined document (``find_markers``): read over its structure, as a splice of the
        text would be (§12.2). Each is also kept as the file it is in has it, at its own section and block
        (``local_markers``), for the presence of the units there."""
        if self._markers is None:
            self._markers = find_markers(self.document, self.lex_fragment)
            for marker in self._markers:
                place, section, block = self.of_block(marker.section, marker.block)
                self._local.setdefault(place, []).append(replace(marker, section=section, block=block))
        return self._markers

    def local_markers(self, place: Instance) -> list[FoundMarker]:
        """The markers of the combined document that are in the file at *place*, at their section and block there."""
        self.markers()
        return self._local.get(place, [])

    def position(self, diagnostic: Diagnostic, number: int | None) -> tuple[int, int, int, int]:
        """Where a finding is in the combined document, to put the findings in its order: a line of the document
        at itself, a line of a fragment at the include paragraph that took it in (the outermost one, if it is nested),
        after the findings at that line, those of one line of the fragment together. A fragment included more than
        once is at its first place, as its findings are kept once. *number* is the place of the fragment the finding
        was made at, if it was; a finding of the document's own pass in a fragment (its shape, say) has none."""
        if not self._first:
            for place in reversed(self.instances):
                self._first[place.path] = place.number
        if number is not None:
            number = self._first[self.instances[number].path]
        elif diagnostic.file:
            number = self._first.get(diagnostic.file)
            if number is None:  # a file that is not in the body: an attachment file, after it (§15.7.2)
                return (sys.maxsize, 0, 0, diagnostic.line or 0)
        if number is None:
            return (diagnostic.line or 0, 0, 0, 0)
        top = self.instances[number]
        while top.parent is not None and top.parent.parent is not None:
            top = top.parent
        return (self.where.block(*top.at) or 0, 1, number, diagnostic.line or 0)


def weave(
    document: Document,
    where: Locator,
    plan: Plan,
    files: Files | None,
    lex_fragment: Callable[[str], Lexed],
) -> Combined:
    """The combined document of *document*, with the fragments of *plan*, what the walk of its includes
    followed, spliced in at their include paragraphs. *where* is the document's own."""
    sections: list[Section] = []
    preamble: list[Block] = []
    instances: list[Instance] = []
    section_origin: list[tuple[Instance, int]] = []
    block_origin: list[list[tuple[Instance, int | None, int]]] = [[]]
    blocks, origin = preamble, block_origin[0]  # where the blocks go: the last section of what is spliced

    def splice(place: Instance, plan: Plan) -> None:
        nonlocal blocks, origin
        source = place.document
        for section in (None, *range(len(source.sections))):
            if section is not None:
                blocks, origin = [], []
                sections.append(replace(source.sections[section], blocks=blocks))
                section_origin.append((place, section))
                block_origin.append(origin)
            for index, block in enumerate(source.preamble if section is None else source.sections[section].blocks):
                blocks.append(block)
                origin.append((place, section, index))
                if (followed := plan.children.get((section, index))) is not None:
                    fragment, child = followed
                    taken = Instance(len(instances), fragment.path, fragment.document, fragment.where, place, (section, index))
                    instances.append(taken)
                    splice(taken, child)

    host = Instance(0, "", document, where)
    instances.append(host)
    splice(host, plan)
    return Combined(
        document=replace(document, sections=sections, preamble=preamble, source_map=None),
        where=where,
        files=files,
        lex_fragment=lex_fragment,
        instances=instances,
        section_origin=section_origin,
        block_origin=block_origin,
    )


class CombinedLocator:
    """The lines of the combined document's parts (``positions.Locator``): each is looked for in the file
    it is from, by the section and block it has there, and the line of a fragment's part is a ``Spot``
    that names the file. The document's own are plain lines. What is not about the body (the frontmatter) is
    the document's own ``Locator``'s."""

    def __init__(self, combined: Combined) -> None:
        self._combined = combined
        self._host = combined.where

    def __getattr__(self, name: str) -> Any:
        return getattr(self._host, name)

    def heading(self, section: int) -> int | None:
        place, local = self._combined.section_origin[section]
        return place.spot(place.where.heading(local))

    def setext_section(self, section: int) -> bool:
        place, local = self._combined.section_origin[section]
        return place.where.setext_section(local)

    def setext_fragment(self, section: int | None, index: int, fragment: int) -> bool:
        place, local, block = self._combined.of_block(section, index)
        return place.where.setext_fragment(local, block, fragment)

    def block(self, section: int | None, index: int) -> int | None:
        place, local, block = self._combined.of_block(section, index)
        return place.spot(place.where.block(local, block))

    def find(
        self,
        section: int | None,
        index: int,
        needle: str,
        fragment: int | None = None,
        offset: int = 0,
        *,
        nth: int = 0,
    ) -> int | None:
        place, local, block = self._combined.of_block(section, index)
        return place.spot(place.where.find(local, block, needle, fragment, offset, nth=nth))

    def definition(self, section: int | None, index: int, ordinal: int) -> int | None:
        place, local, block = self._combined.of_block(section, index)
        return place.spot(place.where.definition(local, block, ordinal))

    def lifted(self, section: int | None, index: int, needle: str) -> int | None:
        place, local, block = self._combined.of_block(section, index)
        return place.spot(place.where.lifted(local, block, needle))


class CombinedUnits:
    """The presence of every unit of the combined document (``units.Units``, §15.3). A unit has the presence
    it has in its own file, with the sections and list items around it there (a section extends to the end
    of its file, so a block of the document after an include does not take the presence of the last section
    of the fragment), and that of the include paragraphs it was brought in by: a fragment is there when its
    include paragraph is."""

    def __init__(self, combined: Combined, questions: Any, *, template: bool) -> None:
        self._combined = combined
        self._questions = questions
        self._template = template
        self._units: dict[Instance, Units] = {}  # of each place
        self._context: dict[Instance, Presence] = {}  # of each place

    def _of(self, place: Instance) -> Units:
        if place not in self._units:
            self._units[place] = Units(
                place.document, self._combined.local_markers(place), self._questions, template=self._template
            )
        return self._units[place]

    def _brought_in_by(self, place: Instance) -> Presence:
        """The presence of the include paragraphs the fragment at *place* is in."""
        if place.parent is None:
            return ALWAYS
        if place not in self._context:
            section, block = place.at
            self._context[place] = self._brought_in_by(place.parent) | self._of(place.parent).presence(
                section, block, 0
            )
        return self._context[place]

    def enclosing(self, section: int) -> Presence:
        place, local = self._combined.section_origin[section]
        return self._of(place).enclosing(local) | self._brought_in_by(place)

    def enclosing_unit(self, section: int | None, block: int, fragment: int | None) -> Presence:
        place, local, index = self._combined.of_block(section, block)
        return self._of(place).enclosing_unit(local, index, fragment) | self._brought_in_by(place)

    def own(self, section: int | None, block: int, fragment: int | None) -> Presence:
        place, local, index = self._combined.of_block(section, block)
        return self._of(place).own(local, index, fragment)

    def presence(self, section: int | None, block: int | None = None, fragment: int | None = None) -> Presence:
        if block is None:
            if section is None:
                return ALWAYS
            place, local = self._combined.section_origin[section]
            return self._of(place).presence(local) | self._brought_in_by(place)
        place, local, index = self._combined.of_block(section, block)
        return self._of(place).presence(local, index, fragment) | self._brought_in_by(place)


#: The rules only the pass over the document itself can make (``merge_diagnostics``): what is about the
#: includes and the files they name, which the combined document no longer holds as such, and what the
#: combined document would judge by the wrong standard. ``heading-skip`` is the combined document's to
#: judge (§12.2: a level a fragment fills is not skipped), which ``include-heading-skip`` does under every
#: combination of answers; a path is judged against the document root from the document's directory, which a
#: fragment's is not relative to (``path-outside-root``; ``path-not-relative`` needs no base, and is the
#: combined pass's); and the Warning of §17.5 is for the whole validation.
HOST_ONLY: frozenset[str] = frozenset({
    "heading-skip",
    "include-file-missing",
    "include-not-legaldown",
    "include-cycle",
    "include-has-frontmatter",
    "include-has-h1",
    "include-heading-skip",
    "include-anchor-duplicate",
    "template-fragment-invalid",
    "path-outside-root",
    SKIPPED_RULE,
})

#: The rules whose verdict on a part of the document depends on what is declared elsewhere in it (§16.11):
#: with the fragments in, the combined document says what the document alone cannot. Every other rule
#: judges a part by itself, so that the two passes agree on a part of the document wherever it is
#: (the combined pass is the one that reads the fragments' parts too). A rule that is in neither set
#: is a bug in this list: the tests run both passes over every case and compare them.
WHOLE_DOCUMENT_RULES: frozenset[str] = frozenset({
    "ref-broken",
    "ref-targets-attachment",
    "term-undefined",
    "amend-term-undefined",
    "amend-term-unresolvable",
    "condition-reference-unsafe",
    "def-unreferenced",
    "attachment-unreferenced",
    "question-unused",
    "definition-circular",
    "definition-used-before-declaration",
    "anchor-duplicate",
    "anchor-autogen-collision",
    "def-duplicate-id",
    "def-autogen-collision",
    "attachment-id-collision",
    "amend-def-override",
    "placeholder-type-inconsistent",
    "placeholder-question-mismatch",
    # An absolute path or a URI scheme needs no base, so a fragment's is judged (and the document's definition of
    # an image that only a fragment uses, which only the combined document sees to be used).
    "path-not-relative",
})

#: A rule of the combined pass that another rule of the document's pass reports for the same thing, at the
#: same place: the combined pass says nothing of it where the owner has.
#: Two section identifiers that clash across a fragment and the document are ``include-anchor-duplicate``'s.
OWNED_BY: dict[str, str] = {"anchor-duplicate": "include-anchor-duplicate"}


def merge_diagnostics(
    host: list[Diagnostic], whole: list[Diagnostic], places: list[int | None], combined: Combined
) -> list[Diagnostic]:
    """What the validation of a document says once its combined document is validated too (§16.11): the
    findings of *whole*, the pass over the combined document, which reads every part of the document and of
    its fragments, each with the place of its fragment it was made at (*places*); and of *host*, the pass over
    the document alone, those only it can make (``HOST_ONLY``), in the order of the combined document
    (``Combined.position``).

    A finding in a fragment that is included more than once is made once in each place: it is one finding. Of
    the findings in the places of a fragment, those with one rule, file, line and message are kept as often as the
    place that makes it most often makes it (two on a line of one place are two). Findings that differ, another
    target or a message with what is of the place (``anchor-autogen-collision`` says ``part-2`` in one place and
    ``part-3`` in the next), are each kept. What ``OWNED_BY`` names is left to its owner. What a reference in a fragment
    says is its own: ``path-not-relative`` says the path is to be relative to the fragment."""
    owned = {(d.rule, d.file, d.line) for d in host if d.rule in OWNED_BY.values()}
    merged: list[tuple[Diagnostic, int | None]] = []
    made: dict[tuple[str, str, int | None, str], dict[int, int]] = {}  # of each finding, how often each place made it
    kept: Counter[tuple[str, str, int | None, str]] = Counter()
    for d, number in zip(whole, places, strict=True):
        if d.rule in HOST_ONLY or (OWNED_BY.get(d.rule), d.file, d.line) in owned:
            continue
        if d.file and d.rule == "path-not-relative":
            d = replace(d, message=d.message.replace("relative to the document", "relative to the fragment"))
        if number is not None:
            key = (d.rule, d.file, d.line, d.message)
            made_here = made.setdefault(key, {})
            made_here[number] = made_here.get(number, 0) + 1
            if made_here[number] <= kept[key]:
                continue  # a place has made it as often already
            kept[key] = made_here[number]
        merged.append((d, number))
    merged += [(d, None) for d in host if d.rule in HOST_ONLY]
    merged.sort(key=lambda each: combined.position(*each))
    return [d for d, _number in merged]
