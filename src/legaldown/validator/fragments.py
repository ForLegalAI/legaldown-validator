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

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from functools import cache, cached_property
from typing import Literal

from ..directives import Lexed, lex
from ..files import LoadFile, relative_path, relative_path_problem, within
from ..models import Document, Metadata
from ..positions import Locator
from .patterns import LEGALDOWN_EXTENSIONS, MAX_SECTION_LEVEL
from .result import Line, _Recorder
from .templates import BodyScan, IncludeDirective, check_template_body
from .units import FoundMarker, find_markers, iter_include_paragraphs

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
    the frontmatter it should not have kept as an error, not raised (#43)."""

    def __init__(self, path: str, document: Document) -> None:
        self.path = path
        self.document = document

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
    which a path may name itself."""

    def __init__(self, resolve: LoadFile, document: Document) -> None:
        self._resolve = resolve
        self._path = document.path
        self._read: dict[str, FragmentFile | None] = {}
        self._own: dict[str, bool] = {}

    def is_own(self, relative: str) -> bool:
        """True if *relative* names the document being validated: it adds nothing to
        itself, and an include of itself is a cycle (``include-cycle``, not
        implemented), not a file to read."""
        if self._path is None:
            return False
        if relative not in self._own:
            self._own[relative] = within(self._path.parent, relative) == self._path.resolve()
        return self._own[relative]

    def read(self, path: str) -> FragmentFile | None:
        """The file at *path*, or None when it cannot be asked for (``relative_path``:
        not relative, or out of the document's directory, §2.3) or is not there. The
        loader is asked once for each file."""
        relative = relative_path(path)
        if relative is None:
            return None
        if relative not in self._read:
            # Imported here, as in core.py: parser -> definitions -> validator would
            # otherwise be a cycle at import time.
            from ..parser import parse

            text = self._resolve(relative)
            self._read[relative] = None if text is None else FragmentFile(
                relative, parse(text, on_frontmatter_error="keep")
            )
        return self._read[relative]


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
    and not followed here. Nor is anything the file's own body draws in
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


def nested_includes(
    fragment: FragmentFile, lex_fragment: Callable[[str], Lexed] | None = None
) -> list[tuple[str, int | None]]:
    """The include paragraphs a file holds, in order, each as written with its line:
    what is allowed outside a template (§12.2) and that assembly does not
    assemble. A drafting note is a quote, so only a paragraph of its own counts. A
    paragraph with an ``{{include:}}`` that names no path is one too: assembly refuses
    it, as it does any other (``iter_include_paragraphs`` is the validator's reading)."""
    return [
        (directive.source, fragment.where.find(section, index, directive.source, 0, directive.start))
        for section, index, directive in iter_include_paragraphs(fragment.document, lex_fragment or cache(lex))
    ]


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


def include_heading_skips(
    document: Document,
    where: Locator,
    splices: dict[int | None, list[FragmentFile]],
    first: int | None,
    unread: Collection[int | None] = (),
) -> tuple[list[HeadingSkip], set[int]]:
    """The headings that skip a level when every fragment is spliced in where its
    include paragraph is (§12.2, §16.11): *splices* are the fragments after the
    heading of each section (None: before the first heading), in order, and *first*
    is the level before the document's first heading (0, or None; ``skips``).

    The check is one pass, with every include present: §16.11 asks for it under
    every combination of answers to the conditions on the includes of a template,
    which this does not try. A heading is reported where it is: a fragment's heading
    in its file, and a heading of the document after a fragment's at its own line.
    A heading of the document after one of its own is ``heading-skip``'s, not
    reported again.

    Also returned: the headings of the document that skip a level after the one
    before them in the document (``heading-skip``), and do not in the combined
    document, because a fragment between them fills the level. *unread* are the
    sections (None: the preamble) that hold an include paragraph whose fragment
    was not read: what lies before the heading after such a section is not
    known, and its ``heading-skip`` stands."""
    found: list[HeadingSkip] = []
    reported: set[tuple[str, int]] = set()
    last = first  # the level of the heading before, in the combined document
    from_fragment = False  # whether that heading is a fragment's

    def splice(fragments: list[FragmentFile]) -> None:
        nonlocal last, from_fragment
        for fragment in fragments:
            for index, section in enumerate(fragment.document.sections):
                level = _level(section.level)
                # A file included twice is the same heading twice: reported once.
                if skips(last, level) and (fragment.path, index) not in reported:
                    reported.add((fragment.path, index))
                    line = fragment.where.heading(index)
                    found.append(HeadingSkip(fragment.path, line, _message(section.title, last, level)))
                last, from_fragment = level, True

    splice(splices.get(None, []))
    own = first  # the level of the document's own previous heading
    filled: set[int] = set()
    for index, section in enumerate(document.sections):
        level = _level(section.level)
        if from_fragment and skips(last, level) and not skips(own, level):
            found.append(HeadingSkip("", where.heading(index), _message(section.title, last, level)))
        elif skips(own, level) and not skips(last, level) and (None if index == 0 else index - 1) not in unread:
            filled.add(index)
        last, from_fragment, own = level, False, level
        splice(splices.get(index, []))
    return found, filled


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


#: Why an include paragraph's fragment was not read, for ``skipped_categories`` (§17.5): no
#: loader to ask (``resolve``), a path that leads out of the document's directory (valid, §2.3,
#: and never asked for, #109), a loader that has no such file (``include-file-missing`` is
#: reported), or the document itself (a cycle, which is not detected).
UnreadReason = Literal["no-resolver", "leads-out", "unreadable", "own"]


@dataclass(frozen=True, slots=True)
class UnreadInclude:
    """An include paragraph whose fragment was not read, and why."""
    include: IncludeDirective
    reason: UnreadReason


@dataclass(slots=True)
class IncludeOutcome:
    """What ``check_includes`` leaves for its caller: the *filled* headings (below), and
    what it did not do, for the Warning that §17.5 asks for (``ldv-check-skipped``): the
    include paragraphs it did not read (*unread*, in order; not one already judged as a
    path or found not LegalDown, which are Errors of their own), whether a
    fragment it read is conditional (*conditional*: ``include-heading-skip`` is judged with
    every include present, not under every combination of answers), and the limits it
    reached (*caps*, each as a phrase)."""
    filled: set[int] = field(default_factory=set)
    unread: list[UnreadInclude] = field(default_factory=list)
    conditional: bool = False
    caps: list[str] = field(default_factory=list)


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
    unconditional: Callable[[IncludeDirective], bool],
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
    such finding, with a ``document_root`` or without one: it is valid (§2.3) and
    this implementation does not read it, so it is skipped (#109) and unread (below). A fragment
    that is there is checked for its shape and, in a *template*, its content
    (``fragment_findings``), once for each file however often it is included, and
    its headings are joined to the document's for ``include-heading-skip``
    (*first_level*). In a template, each LegalDown attachment file is read too,
    for the ``{{include:}}`` it must not hold. A fragment's own includes are not
    followed: ``include-cycle`` is not implemented, and outside a template they
    are allowed (§12.2).

    Returned (``IncludeOutcome``): the headings of the document whose ``heading-skip`` the combined document
    makes valid (``include_heading_skips``), which the caller does not report, as the combined
    document is the one §4.1 judges when every fragment between is read (§12.2). A
    fragment does not count for that when it is not known to be there (a condition
    on its include paragraph or on a section around it, *unconditional*) or when it is
    not read as a fragment (frontmatter, or a level 1 heading: it is reported, and not
    spliced).

    A path that file reference checks judged (*judged*: the places they reported,
    ``FileReferences.reported``) draws that finding alone (path-not-relative, an
    absolute path or one with a URI scheme; path-outside-root): the same path is
    not also missing, or not LegalDown. Those are not *unread* of the outcome either: an include
    paragraph is, when its fragment was not read for a reason that is no finding of its own."""
    outcome = IncludeOutcome()
    splices: dict[int | None, list[FragmentFile]] = {}
    unread: set[int | None] = set()  # the sections with an include paragraph whose fragment is not known
    findings: dict[str, list[Finding]] = {}  # of each fragment, once
    for include in includes:
        if not include.paragraph:
            continue
        if include.at in judged:
            unread.add(include.section)
            continue
        if not include.path:
            # ``{{include:}}``: a paragraph that names no file replaces nothing, and assembly
            # refuses the template for it (§12.2). Said when files can be read, as for a file that is
            # not there.
            if files is not None:
                result.error(
                    "include-file-missing",
                    f"'{include.source}' names no file: an include paragraph names the LegalDown file "
                    f"it takes in (§12.2).",
                    line=include.line,
                )
            outcome.unread.append(UnreadInclude(include, "unreadable" if files is not None else "no-resolver"))
            unread.add(include.section)
            continue
        if not include.path.endswith(LEGALDOWN_EXTENSIONS):
            result.error(
                "include-not-legaldown",
                f"'{include.written}' is not a LegalDown file: an include target is a {', '.join(LEGALDOWN_EXTENSIONS)} "
                f"file (§12.2). It is not read.",
                line=include.line,
            )
            unread.add(include.section)
            continue
        if files is None:
            outcome.unread.append(UnreadInclude(include, "no-resolver"))
            continue
        # A path a LoadFile is never asked for (a backslash, a null byte, or leading out of the
        # document's directory, §2.3) is not read. One that leads out is valid and skipped below;
        # any other is include-file-missing, as assembly refuses the template for it. It is not
        # path-not-relative (an absolute path or a URI scheme, judged above), nor
        # path-outside-root, which judges where it leads.
        relative = relative_path(include.written)
        if relative is None and relative_path_problem(include.written) == "leads-out":
            # Not judged above, so (given a root) within it: a valid path (§2.3), with a root or
            # without, that this implementation does not read, as it reads nothing outside the
            # document's directory. A known limitation (CONFORMANCE.md, #109), and no finding:
            # it is *unread*, which the Warning of §17.5 says. A backslash or a null byte is not
            # such a path, nor any other that cannot be asked for: still missing.
            outcome.unread.append(UnreadInclude(include, "leads-out"))
            unread.add(include.section)
            continue
        fragment = None if relative is None else files.read(relative)
        # Asked about itself only when something was read: the file system is the loader's.
        if fragment is not None and files.is_own(fragment.path):
            outcome.unread.append(UnreadInclude(include, "own"))
            unread.add(include.section)
            continue
        if fragment is None:
            result.error(
                "include-file-missing",
                f"The fragment '{include.written}' cannot be read: "
                + ("it is not a relative path that can be read (§2.3)." if relative is None
                   else "there is no such file, or it is not UTF-8 text (§12.2, §16.11)."),
                line=include.line,
            )
            outcome.unread.append(UnreadInclude(include, "unreadable"))
            unread.add(include.section)
            continue
        if fragment.path not in findings:
            findings[fragment.path] = fragment_findings(
                fragment, kind="include", template=template, lex_fragment=lex_fragment
            )
            for finding in findings[fragment.path]:
                result.error(finding.rule, finding.message, line=finding.line, file=fragment.path)
        # A file with frontmatter or a level 1 heading is not a fragment: it is reported, not spliced.
        if not fragment.spliceable:
            unread.add(include.section)
            continue
        splices.setdefault(include.section, []).append(fragment)
        if not unconditional(include):
            outcome.conditional = True
            unread.add(include.section)  # it may not be there: it fills nothing
    if files is not None and template:
        # The attachment files of a template hold no include (§16.12); only that is checked
        # of them here: their frontmatter and heading are assembly's to refuse, and are not
        # reported by this validation (CONFORMANCE.md).
        attached: set[str] = set()
        for attachment in document.metadata.attachments:
            relative = relative_path(attachment.file) if attachment.file.endswith(LEGALDOWN_EXTENSIONS) else None
            # (A file that is also an include was checked above, and is a finding of its own.)
            if relative is None or relative in attached or relative in findings:
                continue
            attached.add(relative)
            fragment = files.read(relative)
            if fragment is None or files.is_own(relative):
                continue
            for finding in fragment_findings(
                fragment, kind="attachment", template=True, shape=False, lex_fragment=lex_fragment
            ):
                result.error(finding.rule, finding.message, line=finding.line, file=fragment.path)
    if not splices:
        return outcome
    skipped, outcome.filled = include_heading_skips(document, where, splices, first_level, unread)
    for skip in skipped:
        result.error("include-heading-skip", skip.message, line=skip.line, file=skip.file)
    return outcome

