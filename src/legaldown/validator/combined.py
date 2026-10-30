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
own too. And the preamble of a file is its preamble wherever it is spliced: a marker in it is classified as
the file has it (``units.find_markers``).
"""
from __future__ import annotations

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


@dataclass(eq=False, slots=True)
class Instance:
    """A place a file has in the combined document: the document itself (*path* ``""``) or a fragment at
    one of its includes (a fragment included twice is in two places). *parent* is the place holding the
    include paragraph and *at* its (section, block) in the file of *parent*; they give the presence of
    the include (§15.3), which the whole fragment has."""
    path: str
    document: Document
    where: Locator
    parent: Instance | None = None
    at: tuple[int | None, int] = (None, 0)

    def spot(self, line: int | None) -> int | None:
        """*line* of this file, as a diagnostic names it: the file goes with a line of a fragment."""
        return line if not self.path else Spot(line, self.path)


@dataclass(eq=False, slots=True)
class Combined:
    """The combined document (*document*) and where its parts are from: *section_origin* has each of its
    sections as the (place, section index) it is a copy of, and *block_origin* each block of the preamble
    (first) and of the sections, as the (place, section index or None, block index) of the file it is
    from. *host* is the document the includes are in, with its *where* and *markers* (the validation's
    own); *files* and *lex_fragment* are the validation's, so that nothing is read or lexed twice."""
    host: Document
    document: Document
    where: Locator
    host_markers: list[FoundMarker]
    files: Files | None
    lex_fragment: Callable[[str], Lexed]
    section_origin: list[tuple[Instance, int]]
    block_origin: list[list[tuple[Instance, int | None, int]]]
    _markers: dict[str, list[FoundMarker]] = field(default_factory=dict)
    _grouped: dict[str, dict[tuple[int | None, int], list[FoundMarker]]] = field(default_factory=dict)

    def of_block(self, section: int | None, block: int) -> tuple[Instance, int | None, int]:
        """The place, section index and block index in its file of block *block* of the preamble
        (*section* None) or of section *section*."""
        return self.block_origin[0 if section is None else section + 1][block]

    def file_markers(self, place: Instance) -> list[FoundMarker]:
        """The markers of the file at *place* (``find_markers``), as that file has them, once for the file."""
        if place.path not in self._markers:
            self._markers[place.path] = (
                self.host_markers if not place.path else find_markers(place.document, self.lex_fragment)
            )
        return self._markers[place.path]

    def markers(self) -> list[FoundMarker]:
        """The markers of the combined document, in its order: those of each file, at the section and block
        they have in it. A marker is classified in its own file (the preamble of a fragment is a preamble,
        wherever it is spliced)."""
        grouped = self._grouped
        found: list[FoundMarker] = []
        for container, origin in enumerate(self.block_origin):
            section = None if container == 0 else container - 1
            for block, (place, local_section, local_block) in enumerate(origin):
                if place.path not in grouped:
                    by_block: dict[tuple[int | None, int], list[FoundMarker]] = {}
                    for marker in self.file_markers(place):
                        by_block.setdefault((marker.section, marker.block), []).append(marker)
                    grouped[place.path] = by_block
                for marker in grouped[place.path].get((local_section, local_block), ()):
                    found.append(marker if (local_section, local_block) == (section, block) else replace(
                        marker, section=section, block=block
                    ))
        return found


def weave(
    document: Document,
    where: Locator,
    markers: list[FoundMarker],
    targets: dict[tuple[str, int | None, int], FragmentFile],
    files: Files | None,
    lex_fragment: Callable[[str], Lexed],
) -> Combined:
    """The combined document of *document*, whose include paragraphs *targets* has taken in: each
    paragraph, by the path of the file that holds it (``""``: the document), its section and its block, and
    the fragment it takes in. *where* and *markers* are the document's own. Every include paragraph must have been followed
    (``fragments._Walk.complete``): the splice is what the walk expanded, so that the two cannot part, and
    it follows the walk's bounds (no cycle, at most ``MAX_INCLUDE_DEPTH`` fragments deep)."""
    sections: list[Section] = []
    preamble: list[Block] = []
    section_origin: list[tuple[Instance, int]] = []
    block_origin: list[list[tuple[Instance, int | None, int]]] = [[]]
    blocks, origin = preamble, block_origin[0]  # where the blocks go: the last section of what is spliced

    def splice(place: Instance) -> None:
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
                if (target := targets.get((place.path, section, index))) is not None:
                    splice(Instance(target.path, target.document, target.where, place, (section, index)))

    splice(Instance("", document, where))
    return Combined(
        host=document,
        document=replace(document, sections=sections, preamble=preamble, source_map=None),
        where=where,
        host_markers=markers,
        files=files,
        lex_fragment=lex_fragment,
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
        self._units: dict[str, Units] = {}  # of each file
        self._context: dict[Instance, Presence] = {}  # of each place

    def _of(self, place: Instance) -> Units:
        if place.path not in self._units:
            self._units[place.path] = Units(
                place.document, self._combined.file_markers(place), self._questions, template=self._template
            )
        return self._units[place.path]

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
#: combination of answers; a path of a fragment is relative to the fragment, not to the document that is
#: validated (``path-not-relative``, ``path-outside-root``); and the Warning of §17.5 is for the whole validation.
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
    "path-not-relative",
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
})

#: A rule of the combined pass that another rule of the document's pass reports for the same thing, at the
#: same place: the combined pass says nothing of it where the owner has.
#: Two section identifiers that clash across a fragment and the document are ``include-anchor-duplicate``'s.
OWNED_BY: dict[str, str] = {"anchor-duplicate": "include-anchor-duplicate"}


def merge_diagnostics(host: list[Diagnostic], whole: list[Diagnostic]) -> list[Diagnostic]:
    """What the validation of a document says once its combined document is validated too (§16.11): the
    findings of *whole*, the pass over the combined document, which reads every part of the document and of
    its fragments; and of *host*, the pass over the document alone, those only it can make (``HOST_ONLY``).

    A finding in a fragment that is included twice is made twice: it is one finding (the same rule, file, line
    and message, as the command line reports a finding reached from two targets). What ``OWNED_BY`` names is
    left to its owner."""
    owned = {(d.rule, d.file, d.line) for d in host if d.rule in OWNED_BY.values()}
    merged: list[Diagnostic] = []
    seen: set[tuple[str, str, int | None, str]] = set()
    for d in whole:
        if d.rule in HOST_ONLY or (OWNED_BY.get(d.rule), d.file, d.line) in owned:
            continue
        if d.file and d.line is not None:
            key = (d.rule, d.file, d.line, d.message)
            if key in seen:
                continue
            seen.add(key)
        merged.append(d)
    return merged + [d for d in host if d.rule in HOST_ONLY]
