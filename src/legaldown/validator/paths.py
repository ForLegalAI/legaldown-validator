"""The files a document names, by their paths (§2.3, §16.2): path-not-relative
and path-outside-root (#104).

Both rules are syntactic (§2.3, §17.2): they read the text of a path and never
a file, so existence (Full, §17.4) is not asked, and a symbolic link is not
followed. The paths are the ones §2.3 lists: ``{{include:}}`` (§12),
``attachments[].file`` (§3.9), ``amends.file`` (§3.8), ``supersedes.file``
(§3.2), ``translations`` (§14) and image paths (§8.7), a reference-style image's
(``![a][r]``) being the destination of the link reference definition it names (§4.7, #92).
A link or an autolink is no file reference, and raw HTML is ``raw-html``'s.
"""
from __future__ import annotations

import html
import itertools
import os
import re
from bisect import bisect_left
from collections.abc import Callable, Container
from functools import cache, partial
from typing import TYPE_CHECKING, Literal, NamedTuple, assert_never
from urllib.parse import unquote

from ..directives import Directive, Lexed, is_escaped, mask_directives
from ..files import containment, is_absolute_reference, names_below
from ..markdown import collapse_label_whitespace, label_end, normalize_label, scan_inline_link
from ..models import Amends, Document, Metadata
from ..positions import Locator
from .result import Line, _Recorder

if TYPE_CHECKING:
    from ..definitions import PlacedLinkDefinition


#: What is wrong with a path (``FileReferences._finding``): it is absolute, the document is outside the
#: root (so every path leaves it), or the path leads out of the root.
FindingKind = Literal["not-relative", "document-outside", "leaves"]


class FileReference(NamedTuple):
    """A file reference met, to be judged in document order (``FileReferences.finish``)."""

    at: tuple[int, ...]
    path: str
    what: str
    line: Line
    url: bool
    literal: str | None
    rest: bool
    #: Whether it is in a drafting note, which is still judged (§15.6).
    noted: bool = False


#: A drive once decoded: one letter, of either case, and a colon (``C%3A/x``, ``C%3Ax``). On Windows a
#: drive letter is case-blind and ``a:b.png`` is drive-relative, as written ``a:b`` is read too (§2.3).
_DRIVE_RE = re.compile(r"[A-Za-z]:")


def _whole_names(path: str) -> str:
    """*path* up to its last separator: the names of it that a directive after it
    cannot change. ``../x/`` of ``../x/``, ``../x`` of ``../x/y``, ``..`` of ``..``."""
    return path[: max(path.rfind("/"), path.rfind("\\")) + 1]


class FileReferences:
    """Checks the file references of one document against §2.3, recording
    path-not-relative and path-outside-root in *result*.

    *root* is the configured document root (None: the containment check does not
    run, and ``../shared/definitions.lgd`` is valid, §2.3). *base* is the directory
    the paths are relative to: that of a loaded document; a parsed string has
    none and is assumed to sit at the root, so a leading ``..`` leaves it. *line*
    is where a document outside the root is reported.

    References are met as the document is walked, in no order that reads well, and
    recorded by ``check`` with their place in the document (*at*); ``finish`` judges
    them in document order, so the findings and the one that names a path as an
    example (a document outside the root) are in it too."""

    def __init__(
        self,
        result: _Recorder,
        root: str | os.PathLike[str] | None,
        base: str | os.PathLike[str] | None,
        line: Line,
    ) -> None:
        self._result = result
        self._root = None if root is None else os.fspath(root)
        self._line = line
        # How many names the document is below the root, once: None when it is
        # not within it. Every path of a document outside the root leads out of
        # it, and one finding says so, not one for each path (#104).
        self._depth: int | None = None
        if self._root is not None:
            self._depth = names_below(self._root if base is None else base, self._root)
        self._met: list[FileReference] = []
        #: Whether a document outside the root has been said to be (once, however many paths).
        self._said_outside = False
        #: The places (*at*) of the references found wrong, once ``finish`` has judged
        #: them: a path that is reported is not also reported for the file it names
        #: (``include-file-missing``, #35).
        self.reported: set[tuple[int, ...]] = set()
        #: The first image path in the document that names a file, outside drafting notes and
        #: no finding, once ``finish`` has judged them: the position of the existence checks
        #: of image paths that are not made (``ldv-check-skipped``, §17.4).
        self.first_image: FileReference | None = None

    def check(
        self,
        path: str,
        what: str,
        line: Line,
        *,
        at: tuple[int, ...] = (),
        url: bool = False,
        literal: str | None = None,
        rest: bool = True,
        noted: bool = False,
    ) -> None:
        """Record *path*, which *what* names (``attachments[0].file``, ``image``),
        found at *line* and at place *at* in the document: the frontmatter's
        before the body's, the body's by (section, block, fragment, offset), a
        section's heading before its blocks. With *url* it is an image
        destination (§8.7), which is read as a URL is: without a query or
        fragment (``x.png?p=/../..`` is ``x.png``), and also percent-decoded, as
        a tool opening the file would read it, with ``%2F`` as ``/`` and
        ``%2E%2E`` as ``..``. A decoded path is absolute when it begins with
        ``/`` or ``\\`` or a drive (``C%3A/x``); a scheme is only the one
        written, since ``%3A`` in ``a%3Ab.png`` is a character of the name. With
        *literal*, only that start of *path* is known, since a directive follows
        it: it is judged for being absolute, and for the names of it that are
        whole leaving the root; *rest* is whether more than directives (and a
        query or fragment) follows it, which a path of directives alone has not.
        *noted*: it is in a drafting note, which is judged all the same.
        A path that is empty is not this rule's concern (``attachment-file-missing``)."""
        if path.strip():
            self._met.append(FileReference(at, path.strip(), what, line, url, literal, rest, noted))

    def is_wrong(self, path: str) -> bool:
        """Whether *path*, a path of the frontmatter, is a finding of ``finish``: not relative
        (``path-not-relative``) or out of the root (``path-outside-root``). The same
        judgement (``_finding``), asked before the references are judged."""
        path = path.strip()
        return self._finding(path, path, True) is not None

    def _finding(self, judged: str, decoded: str, whole: bool) -> tuple[FindingKind, str, str] | None:
        """What is wrong with a path, as written (*judged*) and percent-decoded (*decoded*, the
        same when it is not an image's), or None: ``("not-relative", form, form)`` when it is
        absolute, in the *form* that makes it so; ``("document-outside", judged, judged)`` when the
        document itself is outside the root, so that every path leaves it; ``("leaves", form, names)``
        when it leads out of the root, with *names*, the part of the *form* judged (*whole*: all of it)."""
        # What makes it absolute: as written, or once decoded (``%2Fetc``, ``C%3A/x``).
        absolute = judged if is_absolute_reference(judged) else (
            decoded if decoded[:1] in ("/", "\\") or _DRIVE_RE.match(decoded) else None
        )
        if absolute is not None:
            return "not-relative", absolute, absolute
        for form in (judged, decoded):
            names = form if whole else _whole_names(form)  # what is judged, and what a finding says
            # (The same judgement as the files a document reads: ``files.containment``.)
            contained = containment(names, rooted=self._root is not None, depth=self._depth)
            if contained == "document-outside":
                return "document-outside", judged, judged
            if contained == "leaves":
                return "leaves", form, names
        return None

    def finish(self) -> None:
        """Judge the references recorded, in the order of the document."""
        for reference in sorted(self._met, key=lambda met: met.at):
            if (
                self._judge(reference) and self.first_image is None and reference.what.startswith("image")
                and not reference.noted and reference.at not in self.reported
            ):
                self.first_image = reference
        self._met = []

    def _judge(self, reference: FileReference) -> bool:
        """Record what is wrong with *reference*, and say whether it names a file at all: not a
        query or a fragment alone (``#top``, ``?q``), nor a directive alone
        (``{{placeholder: logo}}``), which is not known until it is filled in."""
        at, written, what, line, url, literal, rest, _noted = reference
        known = written if literal is None else literal.strip()
        judged = known.split("?", 1)[0].split("#", 1)[0] if url else known
        # What is known is the whole path when there is no directive, or when a
        # query or a fragment ends it before the directive: else its last name is
        # partial, as ``..`` may be ``..x`` once filled in.
        whole = literal is None or judged != known
        if not judged and (whole or not rest):
            return False  # a query or a fragment alone, or a directive alone, names no file
        decoded = unquote(judged) if url and "%" in judged else judged
        found = self._finding(judged, decoded, whole)
        if found is None:
            return True
        kind, form, names = found
        self.reported.add(at)
        if kind == "not-relative":
            self._result.error(
                "path-not-relative",
                f"File reference '{written}' ({what}{_reading(known, judged, form)}) is not relative: an "
                f"absolute path, or one beginning with a URI scheme (https://, file://, a drive letter), is an "
                f"Error. Write a path relative to the document (§2.3).",
                line=line,
            )
        elif kind == "document-outside":
            # Every path of the document leads out of the root, and one finding says so, not one for each.
            if not self._said_outside:
                self._result.error(
                    "path-outside-root",
                    f"The document is outside the document root '{self._root}', so none of the files it names "
                    f"lies within it (§2.3), such as '{written}' ({what}).",
                    line=self._line,
                )
            self._said_outside = True
        elif kind == "leaves":
            self._result.error(
                "path-outside-root",
                f"File reference '{written}' ({what}{_reading(known, judged, form, names)}) leads out of the "
                f"document root '{self._root}' (§2.3).",
                line=line,
            )
        else:
            assert_never(kind)
        return True


def _reading(known: str, judged: str, form: str, shown: str | None = None) -> str:
    """What a finding says of a path read other than as written: *known* is what
    is written of it, *judged* is that without its query or fragment, *form* is
    the form of that which the finding is about, and *shown* the part of it that
    was judged, if not all."""
    notes = []
    if judged != known:
        notes.append("without its query or fragment")
    if form != judged:
        notes.append(f"read as '{form if shown is None else shown}' once percent-decoded")
    return "".join(f", {note}" for note in notes)


def check_frontmatter_paths(meta: Metadata, where: Locator, references: FileReferences) -> None:
    """The file references of the frontmatter (§2.3), each at the line of its
    key: ``attachments[].file``, ``amends.file``, ``supersedes.file`` and
    ``translations``. A ``supersedes`` that is a string describes the
    superseded document (§3.2): only the object's ``file`` is a path. A fragment's
    metadata is empty (``_Mode.empty_metadata``): it has nothing to check here."""
    for index, attachment in enumerate(meta.attachments):
        references.check(
            attachment.file, f"attachments[{index}].file", partial(where.key, "attachments", index, "file")
        )
    if meta.amends:
        references.check(meta.amends.file, "amends.file", partial(where.key, "amends", "file"))
    if isinstance(meta.supersedes, Amends):
        references.check(meta.supersedes.file, "supersedes.file", partial(where.key, "supersedes", "file"))
    for code, path in meta.translations.items():
        references.check(path, f"translations.{code}", partial(where.key, "translations", code))


class Image(NamedTuple):
    """An inline image ``![alt](destination)`` of a text: its destination, with
    backslash escapes and character references decoded (as written, otherwise),
    and the offsets in the text of the image from ``!`` to ``)``. When a directive
    is in the destination its path is not known until it is filled in: *path* is
    as written, and *literal* is the (decoded) text before the first directive,
    which is known; it is None for a destination with none. *rest* is then whether
    more than directives follows that text, apart from a query or fragment: a
    destination of directives alone names no file we can tell."""

    path: str
    start: int
    end: int
    literal: str | None = None
    rest: bool = True


#: A backslash escape of an ASCII punctuation character, or a character reference.
_ESCAPE_OR_ENTITY_RE = re.compile(
    r"\\([!-/:-@\[-`{-~])|&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
#: ``[`` and ``]``, and a backslash with what it escapes: the brackets that count.
_BRACKET_RE = re.compile(r"\\.|[\[\]]", re.DOTALL)


def _decoded(destination: str) -> str:
    return _ESCAPE_OR_ENTITY_RE.sub(lambda m: m.group(1) or html.unescape(m.group()), destination)


def _closing_brackets(view: str) -> dict[int, int]:
    """The offset of the ``]`` that closes each unescaped ``[`` of *view*, in one pass."""
    opened: list[int] = []
    closing: dict[int, int] = {}
    for bracket in _BRACKET_RE.finditer(view):
        if bracket.group() == "[":
            opened.append(bracket.start())
        elif bracket.group() == "]" and opened:
            closing[opened.pop()] = bracket.start()
    return closing


class Scan(NamedTuple):
    """What the images and the reference-style images of one text are both found by
    (``scan``): the lexer's *view* of it with its directives masked, the text as
    written with them masked (*raw*), and the offset of the ``]`` that closes each
    ``[`` of the view (*closing*). Worked out once for a text, whatever finds in it."""

    view: str
    raw: str
    closing: dict[int, int]


def scan(text: str, lexed: Lexed) -> Scan:
    """The ``Scan`` of *text*, whose lexing is *lexed*. Linear in its length."""
    # The lexer's view finds the images (brackets in a code span count for
    # nothing); the destination is read from the text as it is written, since a
    # backtick there is no code span: the link is parsed before it.
    view = mask_directives(lexed.view, lexed.directives)
    return Scan(view, mask_directives(text, lexed.directives), _closing_brackets(view))


def images(text: str, lexed: Lexed, scanned: Scan | None = None) -> list[Image]:
    """The inline images of *text*, whose lexing is *lexed*, in order (§8.7): outside
    code spans and comments, which the lexer blanks. An image in the alt text of
    another is one too. ``\\![a](x)`` is a link, with a literal ``!``. An image
    whose destination is empty has no path. Linear in the length of *text*,
    however it is bracketed. *scanned* is ``scan(text, lexed)`` when the caller has it."""
    view, raw, closing = scanned or scan(text, lexed)
    starts = [directive.start for directive in lexed.directives]
    found: list[Image] = []
    at = view.find("![")
    while at >= 0:
        close = closing.get(at + 1)
        if close is not None and view.startswith("(", close + 1) and not is_escaped(view, at):
            link = scan_inline_link(raw, close + 1)
            if link is not None:
                start, stop, after = link
                # Whether a directive is in it is the lexer's to say: a NUL in the text is only a character.
                first = bisect_left(starts, start)
                if first < len(starts) and starts[first] < stop:
                    found.append(Image(
                        text[start:stop], at, after, _decoded(text[start:starts[first]]),
                        _more_than_directives(text, lexed.directives, first, stop),
                    ))
                else:
                    found.append(Image(_decoded(text[start:stop]), at, after))
        at = view.find("![", at + 2)
    return found


def _more_than_directives(text: str, directives: list[Directive], first: int, stop: int) -> bool:
    """True if the text up to *stop*, from ``directives[first]`` (which begins in it),
    holds more than directives and a query or fragment. Only the directives before
    *stop* are read, never a copy of the rest, so many images stay linear (#94)."""
    pieces = []
    at = directives[first].start
    for directive in itertools.islice(directives, first, None):
        if directive.start >= stop:
            break
        pieces.append(text[at:directive.start])
        at = directive.end
    pieces.append(text[at:stop])
    # Read as the literal before the directive is: escapes and character references decoded,
    # so ``\?`` and ``&#63;`` end the path as ``?`` does.
    return bool(_decoded("".join(pieces)).split("?", 1)[0].split("#", 1)[0].strip())


def reference_images(
    text: str, lexed: Lexed, labels: Container[str], inline: Container[int] = (), scanned: Scan | None = None
) -> list[str]:
    """The labels of the reference-style images of *text*, whose lexing is *lexed*, in order
    (§6.3, §8.7): ``![a][r]``, ``![a][]`` and ``![a]``, as the normalised keys
    (``normalize_label``) of the *labels* defined, which only a defined label is. *inline*
    holds the offsets of the inline images (``images``), which are not these. As in
    CommonMark, a label that follows the text (``[r]``) decides, and one that is not defined
    leaves no image — not the text's own label (``![a][r]`` with only ``a`` defined); the
    collapsed and shortcut forms read the text as the label, if it holds no bracket of its
    own. An image whose label holds a directive is not known. Linear in the length of *text*.
    *scanned* is ``scan(text, lexed)`` when the caller has it."""
    view, raw, closing = scanned or scan(text, lexed)
    found: list[str] = []
    at = view.find("![")
    while at >= 0:
        close = closing.get(at + 1)
        if close is not None and at not in inline and not is_escaped(view, at):
            end = label_end(raw, close + 1) if raw.startswith("[", close + 1) else None
            if end is None:
                label = raw[at + 2:close]  # the shortcut form, also where what follows is no label
            elif end == close + 2:
                label = raw[at + 2:close]  # the collapsed form
            else:
                label = raw[close + 2:end]  # the full form, which a blank label cannot be
            key = normalize_label(label)
            if key in labels:  # a blank label (``[ ]``) is none of them
                found.append(key)
        at = view.find("![", at + 2)
    return found


class ReferenceImages:
    """The images of a document that name their file by a link reference
    definition (``![a][r]`` and ``[r]: path``, §4.7, §8.7, #92): the path is the
    definition's, written once however many images use it, so it is checked once,
    where it is written, when an image names it. A definition no image names is no
    file reference, nor is a link's (§2.3). The document's definitions
    (``definitions.locate_link_definitions``) are found when the first text with an
    image in it asks, so a document with none pays nothing."""

    def __init__(self, document: Document) -> None:
        self._document = document
        self._definitions: dict[str, PlacedLinkDefinition] | None = None
        #: The labels of the definitions noted, each with whether an image outside a drafting note names it.
        self._used: dict[str, bool] = {}

    def note(
        self, text: str, lexed: Lexed, inline: Container[int] = (), scanned: Scan | None = None, *,
        noted: bool = False,
    ) -> None:
        """Take note of the definitions the images of *text* name. *inline*: the
        offsets of its inline images; *scanned*: ``scan(text, lexed)``; *noted*:
        *text* is in a drafting note."""
        if "![" not in text:
            return
        if self._definitions is None:
            # definitions -> validator.helpers -> validator/__init__ -> core -> paths: a cycle at import
            from ..definitions import locate_link_definitions

            self._definitions = locate_link_definitions(self._document)
        if self._definitions:
            for key in reference_images(text, lexed, self._definitions, inline, scanned):
                self._used[key] = self._used.get(key, False) or not noted

    def check(self, references: FileReferences, where: Locator) -> None:
        """Record the path of each definition noted, in document order (the top-level
        block it is in, then its place among that block's texts, then among its definitions), at the line
        of the definition's own block."""
        if not self._used or self._definitions is None:
            return
        # Where each lies among the texts of its top-level block: after the fragment before it,
        # as an inline image's place is (fragment, offset) — a definition has no offset, so -1.
        placed = []
        for key, outside in self._used.items():
            each = self._definitions[key]
            placed.append(
                ((-1 if each.section is None else each.section, each.block, each.fragment, -1, each.ordinal), each, outside)
            )
        placed.sort(key=lambda item: item[0])
        for at, each, kept in placed:
            block = each.definition
            # (The finding names the label as CommonMark compares it, line breaks and runs of spaces one space.)
            label = collapse_label_whitespace(block.label)
            references.check(
                _decoded(block.target), f"image, by the definition of [{label}]",
                partial(where.definition, each.section, each.block, each.ordinal), at=at, url=True,
                noted=not kept,
            )


def check_heading_images(
    document: Document,
    lex_fragment: Callable[[str], Lexed],
    references: FileReferences,
    where: Locator,
    named: ReferenceImages | None = None,
) -> None:
    """Check the path of each inline image (§8.7) in the headings; *named* takes
    note of the definitions the others name."""
    for index, section in enumerate(document.sections):
        title = section.title
        if "![" in title:
            lexed = lex_fragment(title)
            scanned = scan(title, lexed)
            found = images(title, lexed, scanned)
            if named is not None:
                named.note(title, lexed, {image.start for image in found}, scanned)
            for image in found:
                references.check(
                    image.path, "image", where.heading(index), at=(index, -1, 0, image.start), url=True,
                    literal=image.literal, rest=image.rest,
                )


def check_fragment_images(
    text: str,
    lexed: Lexed,
    references: FileReferences,
    where: Locator,
    section: int | None,
    block: int,
    fragment: int,
    named: ReferenceImages | None = None,
    *,
    noted: bool = False,
) -> None:
    """Check the path of each inline image (§8.7) in *text*, fragment *fragment*
    of ``block_fragments`` of block *block* of *section* (None: the preamble): the
    free text of a paragraph, list item, quote or table cell, but not code or raw
    HTML, which ``block_fragments`` leaves out. A reference-style image
    (``![a][r]``, ``[r]: path``) names the definition (#92) that *named* takes
    note of, to judge its path where it is written. *noted*: *text* is in a drafting
    note, whose images are judged all the same (§15.6)."""
    scanned = scan(text, lexed)
    found = images(text, lexed, scanned)
    if named is not None:
        named.note(text, lexed, {image.start for image in found}, scanned, noted=noted)
    if not found:
        return
    # The first image is looked for in the source; each of the others is as many
    # lines below it as its text has line breaks before it (a fragment's lines are
    # its source's). ``Locator.find`` looks for a needle that is no directive from
    # the start of its block, which for every image of a long paragraph is
    # quadratic (#94, #104).
    first = found[0]
    first_line = cache(partial(where.find, section, block, text[first.start:first.end], fragment, first.start))
    breaks, previous = 0, first.start
    for image in found:
        breaks += text.count("\n", previous, image.start)
        previous = image.start
        references.check(
            image.path, "image", partial(_below, first_line, breaks),
            at=(-1 if section is None else section, block, fragment, image.start), url=True, literal=image.literal,
            rest=image.rest, noted=noted,
        )


def _below(first: Callable[[], int | None], lines: int) -> int | None:
    """The line *lines* below the one ``first()`` gives."""
    line = first()
    return None if line is None else line + lines
