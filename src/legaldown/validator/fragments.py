"""The files a document includes or attaches, and what §12.2, §12.4, §16.10,
§16.11 and §16.12 require of them (#35).

One implementation serves the three places that look at such a file: ``validate``
when it reads the fragments of the document it validates (``resolve=``),
``validate(fragment=True)`` when the file is validated on its own, and
assembly, which reads the same files for a template. Each asks for the same
findings and gets them at the same lines, so a fragment is never judged one way
by the validator and another by assembly.

A finding is made once for each offender (a heading, a drafting note, a
condition, an include), at its line in the file it is in (§16.9). The fragment
is read as it is written: its frontmatter is not read as declarations, and
frontmatter that cannot be read is a finding of its own, not an exception (#43).
"""
from __future__ import annotations

import os
import posixpath
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Literal, NamedTuple

from ..directives import Lexed, lex
from ..files import LoadFile, Reach, is_absolute_reference, within
from ..models import Document, Metadata
from ..positions import Locator
from .conditions import Exclusivity, Presence, answer_combinations, holds_under
from .patterns import IDENTIFIER_RE, LEGALDOWN_EXTENSIONS, MAX_SECTION_LEVEL
from .result import Line, _Recorder
from .templates import BodyScan, IncludeDirective, check_template_body
from .units import FoundMarker, Units, find_markers, include_key, iter_include_paragraphs

#: How a file is used by the document that names it: an include fragment
#: (``{{include:}}``, §12.2) or a LegalDown attachment file (``attachments[].file``,
#: §12.4). They share a file model, and differ in the ids of their findings
#: (§16.10, §16.11) and in what a template may hold in them (§15.3).
FragmentKind = Literal["include", "attachment"]


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing wrong with a file: the rule that reports it, why, and the line of
    the offender in the file (None where it cannot be told)."""
    rule: str
    message: str
    line: int | None


class FragmentFile:
    """A file read as a fragment: its normalised relative *path* (the name the
    document refers to it by, ``relative_path``) and its *document*, parsed with
    the frontmatter it should not have kept as an error, not raised (#43). *text* is the file as the
    loader gave it, for a reader that edits it (assembly)."""

    def __init__(self, path: str, document: Document, text: str = "") -> None:
        self.path = path
        self.document = document
        self.text = text

    @cached_property
    def where(self) -> Locator:
        """The lines of the file's parts."""
        return Locator(self.document)

    @cached_property
    def spliceable(self) -> bool:
        """Whether the file is a fragment in the sense of §12.2: it has no frontmatter and
        no level-1 heading (``shape_findings`` reports a file that has either), so
        the including document can take it in as it is. A file that is not is reported
        and not spliced (``include-heading-skip``). The same test as the findings,
        so the two cannot part."""
        has_frontmatter, headings = _shape(self.document, self.where)
        return not has_frontmatter and not headings


class Files:
    """The files one validation reads, each asked for once and parsed once,
    whoever wants it: the definitions of an attachment file (§7, §12.4), and the
    checks of an include fragment or an attachment file (#35). *resolve* is the
    ``LoadFile`` of the validation; *document* is the document being validated,
    which a path may name itself.

    A file is the same file by the path it has from the document's directory, however it is
    written (``a.lgd``, and ``../dir/a.lgd`` from a document in ``dir``): ``key``, which does not follow
    symbolic links. It is read once, judged once, and named in a finding by the path it was first met by."""

    def __init__(
        self, resolve: LoadFile, document: Document, root: str | os.PathLike[str] | None = None
    ) -> None:
        self._resolve = resolve
        self._path = document.path
        self._filename = posixpath.normpath(document.filename) if document.filename else ""
        #: Where the document is, to tell two spellings of one path apart; a document with no path is
        #: taken to be at the root of the file system, where nothing it names can lead out.
        self._directory = os.path.abspath(document.path.parent) if document.path is not None else os.sep
        #: Which paths may be read (§2.3): given a document *root*, those that stay within it,
        #: ``../`` included, and none when the document is not itself within it.
        self._reach = Reach.of(root, document.path.parent if document.path is not None else None)
        self._read: dict[str, FragmentFile | None] = {}
        self._own: dict[str, bool] = {}

    def relative(self, path: str) -> str | None:
        """*path*, as a document names a file, normalized, when a loader may be asked for it
        (``relative_path``, given the document root: a path that leads out of the document's
        directory is read when it stays within the root), else None."""
        return self._reach.relative(path)

    def relative_check(self, path: str) -> tuple[str | None, Literal["leads-out", "invalid"] | None]:
        """``relative`` and why not (``relative_path_check``): ``leads-out`` is a valid path
        that is not read, as no document root says it may be."""
        return self._reach.check(path)

    @property
    def root(self) -> Path | None:
        """The document root, when one is configured (§2.3)."""
        return self._reach.root

    def key(self, relative: str) -> str:
        """What makes two spellings of *relative*, a path from the document's directory, one file: that
        path joined to the directory and normalized. Symbolic links are not followed (a link is another
        file), nor the case of a name."""
        return os.path.normpath(os.path.join(self._directory, relative))

    def locate(
        self, base: str, written: str
    ) -> tuple[str | None, Literal["not-relative", "outside-root", "leads-out", "invalid"] | None]:
        """What *written*, a path in a file at the directory *base* (``""``: the document's), names, as
        a path from the document's directory that a loader may be asked for, else why not:
        ``not-relative`` (an absolute path or a URI scheme, ``path-not-relative``), ``outside-root``
        (it leaves the document root, ``path-outside-root``), ``leads-out`` (it is valid and leads out
        of the document's directory, which is not read without a root) or ``invalid`` (a backslash, a
        null byte, or no file). A leave is judged by ``relative_path_check`` (``files.leaves_root``), as ``FileReferences`` judges
        the document's paths."""
        if is_absolute_reference(written):
            return None, "not-relative"
        if not written or "\\" in written or "\0" in written:
            return None, "invalid"
        relative, problem = self.relative_check(posixpath.join(base, written))
        if relative is not None:
            return relative, None
        if problem == "leads-out":  # leaving a root is path-outside-root; with none, it is valid and not read
            return None, "outside-root" if self.root is not None else "leads-out"
        return None, "invalid"

    def is_own(self, relative: str) -> bool:
        """True if *relative* names the document being validated: it adds nothing to
        itself, and an include of itself is a cycle (``include-cycle``), not a file to
        read. A document with no path is known by its ``filename``, if it has one: the
        file of that name beside it."""
        if self._path is None:
            return bool(self._filename) and relative == self._filename
        key = self.key(relative)
        if key not in self._own:
            # The file system is asked only for a file the loader has: it is the loader's. (The one place
            # a link is followed: the document's own file is the file it is, however it is reached.)
            self._own[key] = (
                self.read(relative) is not None
                and within(self._path.parent, relative, self._reach.root) == self._path.resolve()
            )
        return self._own[key]

    def read(self, path: str) -> FragmentFile | None:
        """The file at *path*, or None when it cannot be asked for (``relative``:
        not relative, or out of the document's directory and root, §2.3) or is not there. The
        loader is asked once for each file (``key``)."""
        relative = self.relative(path)
        if relative is None:
            return None
        key = self.key(relative)
        if key not in self._read:
            # Imported here, as in core.py: parser -> definitions -> validator would
            # otherwise be a cycle at import time.
            from ..parser import parse

            text = self._resolve(relative)
            self._read[key] = None if text is None else FragmentFile(
                relative, parse(text, on_frontmatter_error="keep"), text
            )
        return self._read[key]


def _line(line: Line) -> int | None:
    return line() if callable(line) else line


def noun(kind: FragmentKind, article: Literal["a", "the", ""] = "") -> str:
    """What a message calls a file of *kind*, with its article: ``fragment``,
    ``a fragment``, ``an attachment file``, ``the fragment``."""
    name = "fragment" if kind == "include" else "attachment file"
    if article == "a":
        return f"{'a' if kind == 'include' else 'an'} {name}"
    return f"the {name}" if article == "the" else name


def _subject(kind: FragmentKind) -> str:
    """How a message names the file: ``The fragment``. Not by its path: ``Diagnostic.file``
    names it, and the path from each including document's directory would make the same
    finding read differently from two documents in two directories."""
    return noun(kind, "the").capitalize()


def _shape(document: Document, where: Locator) -> tuple[bool, list[int]]:
    """Whether a file has frontmatter, and the indexes of its level-1 headings: what
    §12.2 and §12.4 forbid in it, for ``shape_findings`` and ``FragmentFile.spliceable``."""
    # A parsed one that opens with the block, empty or not (the Locator knows,
    # while the document still fits its source), or one built in code with any
    # metadata.
    has_frontmatter = not document.metadata.frontmatter_absent and (where.has() or document.metadata != Metadata())
    return has_frontmatter, [index for index, section in enumerate(document.sections) if section.level == 1]


def shape_findings(document: Document, where: Locator, kind: FragmentKind) -> list[Finding]:
    """What §12.2 and §12.4 require of a file's own text: no frontmatter, which
    the including document supplies, and no level-1 heading, which it writes
    around the file (include-has-frontmatter, include-has-h1, §16.11;
    attachment-has-frontmatter, attachment-has-h1, §16.10, which are the same
    two checks under the other name). *document* is the file as given, with the
    frontmatter it wrongly has; *where* locates its parts."""
    subject = _subject(kind)
    found = []
    has_frontmatter, headings = _shape(document, where)
    if has_frontmatter:
        # A block that could not be read (#43) has no key to name: the line of
        # the problem in it. frontmatter-invalid-yaml is not drawn as well: the
        # file must not have the block, readable or not, which this says.
        error = document.frontmatter_error
        found.append(Finding(
            f"{kind}-has-frontmatter",
            f"{subject} has frontmatter, which {noun(kind, 'a')} cannot have: the document that "
            f"{'includes' if kind == 'include' else 'attaches'} it supplies the metadata "
            f"({'§12.2' if kind == 'include' else '§12.4'}). It is not read.",
            (error.line or 1) if error is not None else where.first_key(),
        ))
    for index in headings:
        found.append(Finding(
            f"{kind}-has-h1",
            f"{subject} has a level 1 heading, '{document.sections[index].title}', which {noun(kind, 'a')} cannot have: "
            + ("the including document writes the heading around it (§12.2)."
               if kind == "include" else "the renderer writes the attachment's heading from its title (§12.4)."),
            where.heading(index),
        ))
    return found


def content_findings(
    document: Document,
    where: Locator,
    scan: BodyScan,
    markers: list[FoundMarker],
    kind: FragmentKind,
    *,
    noted: bool = False,
) -> list[Finding]:
    """What §15.3 requires of a file a *template* includes or attaches
    (template-fragment-invalid, §16.12), for each thing that breaks it. No
    ``{{include:}}`` is in a file, whatever it is used for and wherever the
    directive is, for the template's own body is the only place (§12.2, §15.3).
    An include fragment further holds no condition (the condition goes on its
    ``{{include:}}`` paragraph), no drafting note, and gives every heading an
    explicit identifier, so that assembly never has to change a fragment; an
    attachment file may hold conditions of its own, beneath its attachment's
    ``when``. *scan* is what ``check_template_body`` met in *document*, and
    *markers* its ``find_markers``. *noted*: the caller's own
    ``check_template_body(template=True)`` has reported the includes in drafting
    notes already (``validate(fragment=True)``), so they are not reported twice."""
    subject = _subject(kind)
    found = []
    if kind == "include":
        for index, section in enumerate(document.sections):
            if section.condition:
                found.append(Finding(
                    "template-fragment-invalid",
                    f"{subject} holds a condition, on the heading '{section.title}'; to make a fragment "
                    f"conditional, put the condition on its {{{{include:}}}} paragraph (§15.3).",
                    where.heading(index),
                ))
        for marker in markers:
            if marker.marker is not None and marker.marker.condition and marker.placed(True):
                found.append(Finding(
                    "template-fragment-invalid",
                    f"{subject} holds a condition, '{marker.source}'; to make a fragment conditional, put "
                    f"the condition on its {{{{include:}}}} paragraph (§15.3).",
                    where.find(marker.section, marker.block, marker.source, marker.fragment, marker.offset),
                ))
        for _quote, line in scan.notes:
            found.append(Finding(
                "template-fragment-invalid",
                f"{subject} holds a drafting note, which belongs in the template's own body (§15.3).",
                _line(line),
            ))
        for index, section in enumerate(document.sections):
            if not section.identifier:
                found.append(Finding(
                    "template-fragment-invalid",
                    f"{subject} has a heading without an explicit identifier, '{section.title}', which "
                    f"every heading of a fragment has (§15.3).",
                    where.heading(index),
                ))
    for include in scan.includes:
        if noted and include.in_note:
            continue
        found.append(Finding(
            "template-fragment-invalid",
            f"{subject} holds '{include.source}'; in a template an include belongs in the template's "
            f"own body (§15.3).",
            _line(include.line),
        ))
    # In the order of the file; what has no line last.
    return sorted(found, key=lambda finding: (finding.line is None, finding.line or 0))


def fragment_findings(
    fragment: FragmentFile,
    *,
    kind: FragmentKind,
    template: bool,
    shape: bool = True,
    lex_fragment: Callable[[str], Lexed] | None = None,
) -> list[Finding]:
    """The findings on a file read for a document: its shape (``shape_findings``)
    unless *shape* is False, and, when the document is a *template*, what §15.3
    requires of it (``content_findings``). Nothing is reported of a file that is
    not in a template beyond its shape: its nested includes are allowed (§12.2)
    and followed by ``check_includes``, not here. Nor is anything the file's own body draws in
    ``validate(fragment=True)`` (``insertion-boundary`` and the like): this is a
    check of what the file may hold, not a validation of it."""
    document = fragment.document
    found = shape_findings(document, fragment.where, kind) if shape else []
    lex_fragment = lex_fragment or cache(lex)  # each text lexed once for the scan and the markers
    if template:
        # The throwaway recorder takes what the body check says of the file for itself
        # (insertion-boundary, drafting-note-def, def-term-variable, ...): the file is not
        # validated here, and those are template rules (TEMPLATE_RULES) that would newly
        # stop the assembly of a template through a fragment's own finding.
        scan = check_template_body(document, lex_fragment, _Recorder(), template=False, where=fragment.where)
        found += content_findings(
            document, fragment.where, scan, find_markers(document, lex_fragment), kind
        )
    return found


def fragment_includes(
    fragment: FragmentFile, lex_fragment: Callable[[str], Lexed] | None = None
) -> list[IncludeDirective]:
    """The include paragraphs a file holds, in order: what is allowed outside a template
    (§12.2). A drafting note is a quote, so only a paragraph of its own counts. A
    paragraph with an ``{{include:}}`` that names no path is one too: assembly refuses
    it, as it does any other (``iter_include_paragraphs`` is the validator's reading).
    Each is an ``IncludeDirective`` of the file: its section and block are the file's,
    and its line is in it."""
    return [
        IncludeDirective(
            directive.positional or "", include_key(directive), directive.source,
            fragment.where.find(section, index, directive.source, 0, directive.start),
            section, index, 0, directive.start, False, True,
        )
        for section, index, directive in iter_include_paragraphs(fragment.document, lex_fragment or cache(lex))
    ]


def nested_includes(
    fragment: FragmentFile, lex_fragment: Callable[[str], Lexed] | None = None
) -> list[tuple[str, int | None]]:
    """The include paragraphs a file holds (``fragment_includes``), each as written with its
    line: what assembly does not assemble."""
    return [(include.source, _line(include.line)) for include in fragment_includes(fragment, lex_fragment)]


def skips(previous: int | None, level: int) -> bool:
    """True if a heading at *level* after one at *previous* breaks §4.1: *previous*
    is 0 before the first heading of a document that must start at level 1, and
    None for one whose first heading sets the level."""
    return (previous == 0 and level > 1) or bool(previous and level - previous > 1)


def _level(level: int) -> int:
    return min(max(level, 1), MAX_SECTION_LEVEL)  # clamped, as numbering does


@dataclass(frozen=True, slots=True)
class HeadingSkip:
    """A heading that skips a level once fragments are included: in *file* ("" for the
    document itself), at *line*."""
    file: str
    line: int | None
    message: str


class Heading(NamedTuple):
    """A heading of a fragment in the combined document: the section *index* of *fragment*."""
    fragment: FragmentFile
    index: int

    @property
    def level(self) -> int:
        return _level(self.fragment.document.sections[self.index].level)


@dataclass(frozen=True, slots=True)
class Splice:
    """What one include paragraph of the document brings into the combined document (§12.2): its
    *headings*, in the order they have there (a nested fragment's after the heading of the section of
    the fragment that holds its include, ``include_stream``), and its *presence* (§15.3: a fragment
    is there when its include is; a fragment holds no condition of its own)."""
    include: IncludeDirective
    presence: Presence
    headings: list[Heading]


#: How many questions the conditions on the includes of a template may name, and how many combinations of
#: their answers are tried, for ``include-heading-skip`` (§15.3). A template is the author's, and its
#: questions few; past either it is judged once, with every include present, and the Warning of §17.5
#: says so.
MAX_COMBINATION_QUESTIONS = 12
MAX_COMBINATIONS = 4096


def _suffix(combination: dict[str, Any]) -> str:
    """What a message says of the combination it was found under: ``when forum=arbitration, nda=false``
    (nothing for no question: the document has one way to be)."""
    if not combination:
        return ""
    shown = ", ".join(f"{q}={str(a).lower() if isinstance(a, bool) else a}" for q, a in combination.items())
    return f" It is so when {shown}."


@dataclass(slots=True)
class HeadingSkips:
    """What ``include_heading_skips`` found: the *skips* of the combined document, in its order; the
    headings of the document it *filled* (a ``heading-skip`` of the document that the combined document
    does not have); and whether the combinations of answers were too many to try (*capped*: the
    pass was made once, with every include present)."""
    skips: list[HeadingSkip] = field(default_factory=list)
    filled: set[int] = field(default_factory=set)
    capped: bool = False


class _Placed(NamedTuple):
    """A heading in the combined document: the index of a section of the document, or a ``Heading`` of a
    fragment, with the *position* of its item and its *number* in it, which order the findings."""
    position: int
    number: int
    head: int | Heading


@dataclass(slots=True)
class _Run:
    """Consecutive items of the combined document with one presence: they are there together or not at all.
    *entry*: the first heading, judged each time (it depends on what comes before); *after*: the others, each
    with the level before it and whether that heading is a fragment's, judged once (*judged*); *exit_level*
    and *exit_fragment*: the last heading, what comes after the run is judged by."""
    presence: Presence
    entry: _Placed | None = None
    after: list[tuple[_Placed, int | None, bool]] = field(default_factory=list)
    exit_level: int | None = None
    exit_fragment: bool = False
    judged: bool = False


def include_heading_skips(
    document: Document,
    where: Locator,
    splices: dict[int | None, list[Splice]],
    first: int | None,
    unread: Collection[int | None],
    units: Units,
    questions: Any,
) -> HeadingSkips:
    """The headings that skip a level when the fragments are spliced in where their include paragraphs
    are (§12.2, §16.11): *splices* are the includes after the heading of each section (None: before the
    first heading), in order, each with the headings of its fragment and of the fragments that one includes,
    and *first* is the level before the document's first heading (0, or None; ``skips``).

    §15.3 asks for the combined document to satisfy §4.1 under every combination of answers to the questions
    that the presence of an include names, with every unit a combination makes absent removed (the
    include, and the section that holds it, with its subsections, since a presence is cumulative,
    ``Units.presence``). The questions tried are those the presences of the includes name, as §15.3 says; a
    question that only a section, a paragraph or an item names is not varied, and its condition holds. Each
    combination is tried (``answer_combinations``: the limits are ``MAX_COMBINATION_QUESTIONS`` and
    ``MAX_COMBINATIONS``; past them, one pass with every include present, *capped*, and an include that is
    conditional fills nothing). A heading is reported where it is, once however many combinations
    skip at it, and under the first (in the order of ``answer_combinations``) with the answers in the
    message: a fragment's heading in its file, and a heading of the document after a fragment's at its own
    line. A heading of the document after one of its own is ``heading-skip``'s, not reported again.

    Also returned: the headings of the document that skip a level after the one before them in
    the document (``heading-skip``), and do not in the combined document under any combination in which
    they are there, because a fragment between them fills the level. *unread* are the sections (None: the
    preamble) that hold an include paragraph whose fragment was not read, or not all of it: what lies before
    the heading after such a section is not known, and its ``heading-skip`` stands."""
    sections = document.sections
    levels = [_level(section.level) for section in sections]
    own = [first, *levels[:-1]] if sections else []  # the level of the document's previous heading, for each
    # The combined document, in order: sections and the splices after them, each with its presence.
    items: list[tuple[int | None, Splice | None]] = [(None, each) for each in splices.get(None, [])]
    for index in range(len(sections)):
        items.append((index, None))
        items += [(index, each) for each in splices.get(index, [])]
    presences = [
        each.presence if each is not None else units.presence(section) for section, each in items  # type: ignore[arg-type]
    ]
    # The questions to try are those the presences of the includes name (§15.3, "Heading hierarchy"); a section
    # that a combination takes out goes with its subsections, and a question only a section names is not varied.
    tried = answer_combinations(
        (each.presence for _section, each in items if each is not None), questions,
        max_questions=MAX_COMBINATION_QUESTIONS, max_combinations=MAX_COMBINATIONS,
    )
    result = HeadingSkips(capped=tried is None)
    combinations = [{}] if tried is None else tried[1]
    if tried is None:  # one pass with every include present: a conditional one may not be there, and fills nothing
        unread = {*unread, *(each.include.section for _section, each in items if each is not None and each.presence)}

    # The items between two changes of presence are there together or not at all: each such run is judged for
    # its first heading, which depends on what comes before, and once for the headings after it, which do not.
    runs: list[_Run] = []
    for position, ((section, each), presence) in enumerate(zip(items, presences, strict=True)):
        if not runs or runs[-1].presence != presence:
            runs.append(_Run(presence))
        run = runs[-1]
        for number, head in enumerate([section] if each is None else each.headings):
            heading = _Placed(position, number, head)  # a heading of the document, or of a fragment
            level = levels[head] if isinstance(head, int) else head.level
            if run.entry is None:
                run.entry = heading
            else:
                run.after.append((heading, run.exit_level, run.exit_fragment))
            run.exit_level, run.exit_fragment = level, each is not None

    reported: set[tuple[str, int]] = set()  # fragment headings, however often included or found
    found: list[tuple[tuple[int, int], HeadingSkip]] = []
    reported_own: set[int] = set()  # headings of the document reported
    seen: set[tuple[bool, ...]] = set()  # the combinations that leave the same units present
    present_any: set[int] = set()  # the headings of the document there in some combination
    skipped_any: set[int] = set()  # ... that skip, whatever is before them, in some

    def judge(placed: _Placed, last: int | None, from_fragment: bool, said: str) -> None:
        """Whether the heading *placed* skips after one at level *last* (a fragment's if *from_fragment*)."""
        head = placed.head
        if isinstance(head, int):  # a heading of the document
            present_any.add(head)
            if skips(last, levels[head]):
                skipped_any.add(head)
                # (Not after the one before it in the document: heading-skip says so.)
                if from_fragment and not skips(own[head], levels[head]) and head not in reported_own:
                    reported_own.add(head)
                    found.append(((placed.position, placed.number), HeadingSkip(
                        "", where.heading(head), _message(sections[head].title, last, levels[head]) + said
                    )))
        else:
            fragment, index = head
            if skips(last, head.level) and (fragment.path, index) not in reported:
                reported.add((fragment.path, index))
                found.append(((placed.position, placed.number), HeadingSkip(
                    fragment.path, _line(fragment.where.heading(index)),
                    _message(fragment.document.sections[index].title, last, head.level) + said,
                )))

    distinct = {run.presence for run in runs}
    for combination in combinations:
        held = {presence: holds_under(presence, combination) for presence in distinct}
        present = [held[run.presence] for run in runs]
        if (signature := tuple(present)) in seen:
            continue  # the same document as one already judged
        seen.add(signature)
        said = _suffix(combination)
        last, from_fragment = first, False  # the level of the heading before, and whether it is a fragment's
        for run, there in zip(runs, present, strict=True):
            if not there or run.entry is None:
                continue
            judge(run.entry, last, from_fragment, said)
            if not run.judged:
                run.judged = True
                for placed, before, after_fragment in run.after:
                    judge(placed, before, after_fragment, said)
            last, from_fragment = run.exit_level, run.exit_fragment
    result.skips = [skip for _position, skip in sorted(found, key=lambda entry: entry[0])]
    result.filled = {
        index for index in present_any
        if skips(own[index], levels[index]) and index not in skipped_any
        and (None if index == 0 else index - 1) not in unread
    }
    return result


def include_anchor_duplicates(
    document: Document,
    where: Locator,
    splices: dict[int | None, list[Splice]],
    units: Units,
    exclusivity: Exclusivity,
) -> list[HeadingSkip]:
    """The section identifiers that are declared twice in the combined document (§12.2,
    ``include-anchor-duplicate``, §16.11), each at the later declaration in the order the
    document has once its fragments are in: a fragment's heading in its file, a heading of the
    document at its own line, however often a fragment is included (the same heading is reported
    once). Explicit identifiers only: an auto-generated one is a Warning of its own
    (``anchor-autogen-collision``), which is not judged across the fragments. Two declarations in the
    document itself are ``anchor-duplicate``'s, and two that can never be there together (an
    alternative: their presences are exclusive, §15.4, as *exclusivity* tells) are no duplicate."""
    found: list[HeadingSkip] = []
    reported: set[tuple[str, int]] = set()
    declared: dict[str, list[tuple[Presence, str]]] = {}  # of each identifier: presence and file ("" the document)

    def declare(identifier: str, title: str, presence: Presence, file: str, index: int, line: Line) -> None:
        identifier = identifier.strip()
        if not identifier or not IDENTIFIER_RE.fullmatch(identifier):
            return
        earlier = declared.setdefault(identifier, [])
        # (A heading already reported is not looked for a clash again: a fragment included many times
        # is reported once, and its later expansions cost nothing.)
        if (file, index) not in reported:
            clash = next(
                (
                    (other, other_file) for other, other_file in earlier
                    if not (file == other_file == "") and not exclusivity.excluded(presence, other)
                ),
                None,
            )
            if clash is not None:
                reported.add((file, index))
                found.append(HeadingSkip(file, _line(line), (
                    f"Duplicate section identifier '{identifier}' on '{title}': once the fragments are "
                    f"included, the combined document declares it earlier, in "
                    f"{'the document' if clash[1] == '' else repr(clash[1])} (§12.2, §16.11)."
                )))
        earlier.append((presence, file))

    def splice(after: list[Splice]) -> None:
        for each in after:
            for fragment, index in each.headings:
                section = fragment.document.sections[index]
                declare(section.identifier, section.title, each.presence, fragment.path, index,
                        fragment.where.heading(index))

    splice(splices.get(None, []))
    for index, section in enumerate(document.sections):
        declare(section.identifier, section.title, units.presence(index), "", index, where.heading(index))
        splice(splices.get(index, []))
    return found


def _message(title: str, last: int | None, level: int) -> str:
    if last == 0:
        return (
            f"Heading levels must not skip. '{title}' is at level {level}, but the combined document "
            f"has no level-1 heading before it (§4.1, §12.2)."
        )
    return (
        f"Heading levels must not skip. '{title}' jumps from level {last} to {level} in the "
        f"combined document, since a fragment is spliced in as it is (§4.1, §12.2)."
    )


#: Why a file a document names was not read, for ``skipped_categories`` (§17.5): no
#: loader to ask (``resolve``), a path that leads out of the document's directory (valid, §2.3,
#: and never asked for unless a document root holds it, #109), a loader that has no such file or a
#: path it is never asked for that is no finding of its own (a backslash, a null byte; for an include
#: ``include-file-missing`` is reported), the document itself (for an include a cycle, which is
#: reported, ``include-cycle``, and so not counted as unread), or a path refused with a finding of its
#: own (``path-not-relative``, ``path-outside-root``), which an include is not counted as unread for either.
UnreadReason = Literal["no-resolver", "leads-out", "unreadable", "own", "rejected", "in-template"]

#: How deep (in fragments, the document being none) and how far (in includes and headings of
#: fragments) the includes of a document are followed (§12.2): a document that is not the author's
#: can chain includes into a tree of any size, and ``include-cycle`` only stops a chain from
#: coming back. Past either, nothing more is followed and the Warning of §17.5 says so.
MAX_INCLUDE_DEPTH = 32
MAX_INCLUDE_EXPANDED = 10_000
#: What the Warning adds to the phrase of a cap, which assembly (where a cap is an Error) leaves out.
NOT_CHECKED = " (not checked past that point)"


@dataclass(frozen=True, slots=True)
class UnreadInclude:
    """An include paragraph whose fragment was not read, and why."""
    include: IncludeDirective
    reason: UnreadReason


@dataclass(slots=True)
class IncludeOutcome:
    """What ``check_includes`` leaves for its caller: the *filled* headings (below), and
    what it did not do, for the Warning that §17.5 asks for (``ldv-check-skipped``): the
    include paragraphs it did not read (*unread*, in order, those of fragments too; not one already judged as a
    path or found not LegalDown, which are Errors of their own, nor a cycle), how many of the include
    paragraphs it met are in fragments (*nested*), and the limits it
    reached (*caps*, each as a phrase): the depth and the count of the includes followed (#114), and
    the combinations of answers that ``include-heading-skip`` tries (#108)."""
    filled: set[int] = field(default_factory=set)
    unread: list[UnreadInclude] = field(default_factory=list)
    nested: int = 0
    caps: list[str] = field(default_factory=list)


def not_legaldown(path: str, written: str) -> str | None:
    """The message of ``include-not-legaldown`` (§12.2) for an include of *path* (normalised) as it was *written*,
    whose path does not end in a LegalDown extension, or None when it does or names no file. It needs no file."""
    if not path or path.endswith(LEGALDOWN_EXTENSIONS):
        return None
    return (
        f"'{written}' is not a LegalDown file: an include target is a "
        f"{', '.join(LEGALDOWN_EXTENSIONS)} file (§12.2). It is not read."
    )


class _Chain(NamedTuple):
    """The files being included, outermost first (*paths*, as they are named), and the same as ``Files.key``
    (*keys*, a set, to tell in one step that a file is among them)."""
    paths: tuple[str, ...] = ()
    keys: frozenset[str] = frozenset()


def extend(files: Files, chain: _Chain | None, path: str) -> _Chain:
    """*chain* (None: the document's, which has included nothing yet) with the file at *path* (as ``Files.locate``
    gives it) last."""
    chain = chain or _Chain()
    return _Chain((*chain.paths, path), chain.keys | {files.key(path)})


def closes(files: Files, chain: _Chain | None, relative: str) -> bool:
    """Whether following the include of *relative* (a path from the document's directory, as ``Files.locate``
    gives it) closes a circular chain (§12.2): it is a file of *chain*, or the document itself. The one test of
    ``include-cycle``, for the validator and for assembly."""
    return files.key(relative) in (chain or _Chain()).keys or files.is_own(relative)


def cycle_message(files: Files, source: str, chain: _Chain | None, relative: str) -> str:
    """The message of ``include-cycle`` for the include *source* of *relative*, which ``closes`` the *chain*."""
    said = " -> ".join(["the document", *(chain or _Chain()).paths, "the document" if files.is_own(relative) else relative])
    return (
        f"'{source}' closes a circular include chain ({said}); a file cannot include "
        f"itself, directly or through others (§12.2)."
    )


def too_deep(chain: _Chain) -> str | None:
    """The phrase that says the depth cap is reached, when *chain* is as long as the includes are followed
    (``MAX_INCLUDE_DEPTH``), else None. The caps are read when asked, so that a test can lower them."""
    if len(chain.paths) < MAX_INCLUDE_DEPTH:
        return None
    return f"following includes of fragments deeper than {MAX_INCLUDE_DEPTH} levels"


class IncludeBudget:
    """How many includes and headings of fragments one document has had followed (``MAX_INCLUDE_EXPANDED``):
    the count is spent before an include is judged or a heading joined, and nothing is followed once it is reached."""

    def __init__(self) -> None:
        self.spent = 0

    def spend(self) -> str | None:
        """Count one more include or heading followed, or, when the count is reached, the phrase that says so
        (and nothing is spent)."""
        if self.spent >= MAX_INCLUDE_EXPANDED:
            return f"following the includes of fragments past {MAX_INCLUDE_EXPANDED:,} includes and headings"
        self.spent += 1
        return None


class _Walk:
    """The include fragments reached from a document, those nested in them too (§12.2): each is read and
    judged once, and expanded into the headings it brings into the combined document, in order. A
    chain of includes that comes back to a file in it is ``include-cycle`` (§16.11) at the include that
    closes it, and followed no further; a fragment included from two places (not a cycle) is expanded at
    both. An include is judged by ``attempt``, whether it is the document's or a fragment's: a path is
    judged as ``FileReferences`` judges the document's, from the directory of the file that holds
    it. *incomplete* tells that something a fragment includes was not followed, so that what lies after
    it is not known."""

    def __init__(
        self, files: Files, result: _Recorder, outcome: IncludeOutcome, *, template: bool,
        lex_fragment: Callable[[str], Lexed], findings: dict[str, list[Finding]],
    ) -> None:
        self._files = files
        self._result = result
        self._outcome = outcome
        self._template = template
        self._lex_fragment = lex_fragment
        self._findings = findings
        #: The include paragraphs of each fragment by the section that holds them, with their number: once.
        self._includes: dict[str, dict[int | None, list[tuple[int, IncludeDirective]]]] = {}
        self._met: set[tuple[str, int]] = set()  # the includes of fragments (file, number) already judged
        self._budget = IncludeBudget()
        self.incomplete = False

    def shape(self, fragment: FragmentFile) -> None:
        """The findings on *fragment* as a file, once however often it is included."""
        if fragment.path not in self._findings:
            self._findings[fragment.path] = fragment_findings(
                fragment, kind="include", template=self._template, lex_fragment=self._lex_fragment
            )
            for finding in self._findings[fragment.path]:
                self._result.error(finding.rule, finding.message, line=finding.line, file=fragment.path)

    def attempt(
        self, include: IncludeDirective, owner: FragmentFile | None, chain: _Chain, first: bool = True
    ) -> FragmentFile | None:
        """The fragment that *include* takes in, an include paragraph of the document (*owner* None) or of
        a fragment, *chain* being the files being included (outermost first, *owner* last), or None when
        there is none to splice: there is nothing to follow, or the file is not a fragment. What is
        wrong is reported (``include-not-legaldown``, ``path-not-relative``, ``path-outside-root``,
        ``include-file-missing``, ``include-cycle``, the shape of the file), in the file that holds the
        include and at its line, and what could not be read is recorded as unread (and said by the
        Warning of §17.5); both only if *first*, the first time this include is judged."""
        file = "" if owner is None else owner.path

        def refuse(rule: str, message: str, reason: UnreadReason | None) -> None:
            if first:
                self._result.error(rule, message, line=include.line, file=file)
                if reason is not None:
                    self._outcome.unread.append(UnreadInclude(include, reason))

        if not include.path:
            refuse(
                "include-file-missing",
                f"'{include.source}' names no file: an include paragraph names the LegalDown file it takes in (§12.2).",
                "unreadable",
            )
            return None
        if (message := not_legaldown(include.path, include.written)) is not None:
            refuse("include-not-legaldown", message, None)
            return None
        relative, problem = self._files.locate("" if owner is None else posixpath.dirname(owner.path), include.written)
        if relative is None:
            what = "an include" if owner is None else "an include in a fragment"
            if problem == "not-relative":
                refuse(
                    "path-not-relative",
                    f"File reference '{include.written}' ({what}) is not relative: an absolute "
                    f"path, or one beginning with a URI scheme (https://, file://, a drive letter), is an Error. "
                    f"Write a path relative to the {'document' if owner is None else 'fragment'} (§2.3).",
                    None,
                )
            elif problem == "outside-root":
                refuse(
                    "path-outside-root",
                    f"File reference '{include.written}' ({what}) leads out of the document "
                    f"root '{self._files.root}' (§2.3).",
                    None,
                )
            elif problem == "leads-out":  # valid, and not read without a root that holds it (#109)
                if first:
                    self._outcome.unread.append(UnreadInclude(include, "leads-out"))
            else:
                refuse(
                    "include-file-missing",
                    f"The fragment '{include.written}' cannot be read: it is not a relative path that can be read (§2.3).",
                    "unreadable",
                )
            return None
        if closes(self._files, chain, relative):
            refuse("include-cycle", cycle_message(self._files, include.source, chain, relative), None)
            return None
        if (cap := too_deep(chain)) is not None:
            if cap + NOT_CHECKED not in self._outcome.caps:
                self._outcome.caps.append(cap + NOT_CHECKED)
            return None
        target = self._files.read(relative)
        if target is None:
            refuse(
                "include-file-missing",
                f"The fragment '{include.written}' cannot be read: there is no such file, or it is not UTF-8 text "
                f"(§12.2, §16.11).",
                "unreadable",
            )
            return None
        self.shape(target)
        return target if target.spliceable else None  # a file that is not a fragment is reported, not spliced

    def extend(self, chain: _Chain, fragment: FragmentFile) -> _Chain:
        """*chain* with *fragment* last."""
        return extend(self._files, chain, fragment.path)

    def expand(self, fragment: FragmentFile, chain: _Chain) -> list[Heading]:
        """The headings *fragment* brings into the combined document: its own, each followed by those of the
        fragments its section includes (those of the preamble first). *chain* are the files being
        included, outermost first, *fragment* last."""
        if fragment.path not in self._includes:
            by_section: dict[int | None, list[tuple[int, IncludeDirective]]] = {}
            for number, include in enumerate(fragment_includes(fragment, self._lex_fragment)):
                by_section.setdefault(include.section, []).append((number, include))
            self._includes[fragment.path] = by_section
            self._outcome.nested += sum(len(each) for each in by_section.values())
        by_section = self._includes[fragment.path]
        headings: list[Heading] = []

        def follow(section: int | None) -> None:
            for number, include in by_section.get(section, ()):
                headings.extend(self._follow(fragment, number, include, chain))

        follow(None)
        for index in range(len(fragment.document.sections)):
            if self._spend():
                break
            headings.append(Heading(fragment, index))
            follow(index)
        return headings

    def _spend(self) -> bool:
        """Count one more include or heading followed (``IncludeBudget``), or, when the count is reached, say so
        once and return True: nothing is followed past it."""
        if (cap := self._budget.spend()) is None:
            return False
        if cap + NOT_CHECKED not in self._outcome.caps:
            self._outcome.caps.append(cap + NOT_CHECKED)
        self.incomplete = True
        return True

    def _follow(
        self, fragment: FragmentFile, number: int, include: IncludeDirective, chain: _Chain
    ) -> list[Heading]:
        """The headings of the fragment that *include*, the *number*th include paragraph of *fragment*,
        takes in. In a template there are none to follow: an include in a fragment is
        ``template-fragment-invalid`` (§15.3), and the Warning of §17.5 says it is not followed; that
        leaves the rest of the fragment as it is. Otherwise, when it cannot be followed there are none, and
        what lies after it is not known (``incomplete``)."""
        key = (fragment.path, number)
        first = key not in self._met
        self._met.add(key)
        if self._template:
            if first:
                self._outcome.unread.append(UnreadInclude(include, "in-template"))
            return []
        if self._spend():
            return []
        target = self.attempt(include, fragment, chain, first)
        if target is None:
            self.incomplete = True
            return []
        return self.expand(target, self.extend(chain, target))


def check_includes(
    document: Document,
    includes: list[IncludeDirective],
    result: _Recorder,
    where: Locator,
    *,
    judged: Collection[tuple[int, ...]],
    files: Files | None,
    template: bool,
    first_level: int | None,
    units: Units,
    questions: Any,
    exclusivity: Exclusivity,
    lex_fragment: Callable[[str], Lexed] = lex,
) -> IncludeOutcome:
    """What the include paragraphs of *document* require (§12.2, §16.11), and what the
    fragments they name require of themselves, recorded in *result* at the line
    of the include or, in a fragment, of its own text, with the fragment's path as
    the file. *includes* are the document's (``BodyScan.includes``). Only an
    include paragraph, which a fragment replaces, is looked at: an include in a
    sentence, a list item or a quote is text (§12.2).

    ``include-not-legaldown`` needs only the path, so it is made without *files*.
    With *files* (the validation has a ``resolve``) each fragment is read once.
    A path that is not there is ``include-file-missing`` at its include, one
    finding for each include, as is a path a loader is never asked for because it
    holds a backslash or a null byte: assembly refuses the template for it too. A
    path that only leads out of the document's directory (``../x.lgd``) is no
    such finding: it is valid (§2.3). With a ``document_root`` that holds it, it is read like
    any other (``Files.relative``); without one this implementation does not read
    it, so it is skipped (#109) and unread (below). A fragment
    that is there is checked for its shape and, in a *template*, its content
    (``fragment_findings``), once for each file however often it is included, and
    its headings are joined to the document's for ``include-heading-skip``
    (*first_level*) and ``include-anchor-duplicate`` (*units* has the presence of the
    document's sections, which with *questions* tell two declarations that can never be
    together). In a template, each LegalDown attachment file is read too,
    for the ``{{include:}}`` it must not hold. A fragment's own includes are followed
    outside a template, where they are allowed (§12.2; in a template they are
    ``template-fragment-invalid``): each is judged as the document's are, from the
    fragment's directory, at its line in the fragment, and an include that comes back to a file of the chain
    (the document itself included) is ``include-cycle`` at the include that closes it
    (``_Walk``).

    Returned (``IncludeOutcome``): the headings of the document whose ``heading-skip`` the combined document
    makes valid (``include_heading_skips``), which the caller does not report, as the combined
    document is the one §4.1 judges when every fragment between is read (§12.2). A
    fragment does not count for that when it is not known to be there (a condition
    on its include paragraph or on a section around it) or when it is
    not read as a fragment (frontmatter, or a level 1 heading: it is reported, and not
    spliced), or when something it includes was not followed.

    A path that file reference checks judged (*judged*: the places they reported,
    ``FileReferences.reported``) draws that finding alone (path-not-relative, an
    absolute path or one with a URI scheme; path-outside-root): the same path is
    not also missing, or not LegalDown. Those are not *unread* of the outcome either: an include
    paragraph is, when its fragment was not read for a reason that is no finding of its own."""
    outcome = IncludeOutcome()
    splices: dict[int | None, list[Splice]] = {}
    unread: set[int | None] = set()  # the sections with an include paragraph whose fragment is not known
    findings: dict[str, list[Finding]] = {}  # of each fragment, once
    walk = _Walk(files, result, outcome, template=template, lex_fragment=lex_fragment, findings=findings) if files else None
    for include in includes:
        if not include.paragraph:
            continue
        if include.at in judged:
            unread.add(include.section)
            continue
        if walk is None:
            # No file can be read: only the extension is judged (Core), and the rest is unread.
            if (message := not_legaldown(include.path, include.written)) is not None:
                result.error("include-not-legaldown", message, line=include.line)
            else:
                outcome.unread.append(UnreadInclude(include, "no-resolver"))
            continue
        fragment = walk.attempt(include, None, _Chain())
        if fragment is None:
            unread.add(include.section)
            continue
        walk.incomplete = False
        presence = units.presence(include.section, include.block, include.fragment)
        splices.setdefault(include.section, []).append(
            Splice(include, presence, walk.expand(fragment, walk.extend(_Chain(), fragment)))
        )
        if walk.incomplete:
            unread.add(include.section)  # something it includes is not known: it fills nothing
    if files is not None and template:
        # The attachment files of a template hold no include (§16.12); only that is checked
        # of them here: their frontmatter and heading are assembly's to refuse, and are not
        # reported by this validation (CONFORMANCE.md).
        attached: set[str] = set()
        include_keys = {files.key(path) for path in findings}  # the files already checked as includes
        for attachment in document.metadata.attachments:
            relative = files.relative(attachment.file) if attachment.file.endswith(LEGALDOWN_EXTENSIONS) else None
            if relative is None:
                continue
            # (A file that was also an include was checked above, and is a finding of its own; however either
            # is spelled, it is one file, named by the path it was first met by.)
            key = files.key(relative)
            if key in attached or key in include_keys or files.is_own(relative):
                continue
            attached.add(key)
            fragment = files.read(relative)
            if fragment is None:
                continue
            for finding in fragment_findings(
                fragment, kind="attachment", template=True, shape=False, lex_fragment=lex_fragment
            ):
                result.error(finding.rule, finding.message, line=finding.line, file=fragment.path)
    if not splices:
        return outcome
    judgement = include_heading_skips(document, where, splices, first_level, unread, units, questions)
    outcome.filled = judgement.filled
    for skip in judgement.skips:
        result.error("include-heading-skip", skip.message, line=skip.line, file=skip.file)
    if judgement.capped:
        outcome.caps.append(
            f"include-heading-skip under every combination of answers to the conditions on the includes "
            f"(more than {MAX_COMBINATION_QUESTIONS} questions, or {MAX_COMBINATIONS:,} combinations), "
            f"which is judged with every include present instead"
        )
    for duplicate in include_anchor_duplicates(document, where, splices, units, exclusivity):
        result.error("include-anchor-duplicate", duplicate.message, line=duplicate.line, file=duplicate.file)
    return outcome
