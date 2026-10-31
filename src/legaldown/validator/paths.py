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
import unicodedata
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Container
from functools import cache, partial
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, assert_never
from urllib.parse import unquote

from ..directives import Directive, Lexed, is_escaped, lex, mask_directives
from ..files import containment, is_absolute_reference, names_below
from ..markdown import (
    READINGS,
    Reading,
    collapse_label_whitespace,
    label_end,
    label_key,
    normalize_label,
    scan_inline_link,
)
from ..models import Amends, Document, Metadata
from ..positions import Locator
from .path_definitions import (
    MDIT_BACKSLASH_RE,
    Definition,
    Origin,
    Read,
    ReadText,
    definitions,
    parts,
    read_definitions,
    texts,
)
from .result import Line, Spot, _Recorder

if TYPE_CHECKING:
    from .combined import Combined


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
    is where a document outside the root is reported. In the combined document of
    a document and its files (#135) a path is relative to the file it is written in:
    *base_of* gives the directory of that file from the document's (``Combined.base_of``,
    for the section and block of the place of a reference), and the depth of each below the
    root is worked out once.

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
        base_of: Callable[[int, int], str] | None = None,
    ) -> None:
        self._result = result
        self._root = None if root is None else os.fspath(root)
        self._line = line
        self._base = None if base is None else os.fspath(base)
        self._base_of = base_of
        # How many names the document is below the root, once: None when it is
        # not within it. Every path of a document outside the root leads out of
        # it, and one finding says so, not one for each path (#104).
        self._depth: int | None = None
        if self._root is not None:
            self._depth = names_below(self._root if base is None else base, self._root)
        #: The same of the directories of the other files of the combined document, by their path from the document's.
        self._depths: dict[str, int | None] = {"": self._depth}
        self._met: list[FileReference] = []
        #: Whether a document outside the root has been said to be (once, however many paths).
        self._said_outside = False
        #: What has been reported of a path (``_judge``).
        self._said: set[tuple[object, ...]] = set()
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
        return self._finding(path, path, True, self._depth) is not None

    def _depth_of(self, base: str) -> int | None:
        """How many names the directory *base* (from the document's) is below the root, once for each."""
        if base not in self._depths:
            self._depths[base] = (
                None if self._root is None else names_below(os.path.join(self._base or self._root, base), self._root)
            )
        return self._depths[base]

    def _finding(
        self, judged: str, decoded: str, whole: bool, depth: int | None
    ) -> tuple[FindingKind, str, str] | None:
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
            contained = containment(names, rooted=self._root is not None, depth=depth)
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
        base = self._base_of(at[0], at[1]) if self._base_of is not None and len(at) >= 2 else ""
        found = self._finding(judged, decoded, whole, self._depth_of(base))
        if found is None:
            return True
        kind, form, names = found
        self.reported.add(at)
        if kind != "document-outside":
            # One finding for a destination, though several readings read it (pandoc reads it as NFC): the same
            # place, the same path as reported, up to its form.
            said = (at, kind, unicodedata.normalize("NFC", written), what)
            if said in self._said:
                return True
            self._said.add(said)
        if kind == "not-relative":
            # (The line is a line of a fragment in the combined document: the path is the fragment's own.)
            where = line() if callable(line) else line
            relative_to = "fragment" if isinstance(where, Spot) and where.file else "document"
            self._result.error(
                "path-not-relative",
                f"File reference '{written}' ({what}{_reading(known, judged, form)}) is not relative: an "
                f"absolute path, or one beginning with a URI scheme (https://, file://, a drive letter), is an "
                f"Error. Write a path relative to the {relative_to} (§2.3).",
                line=where,
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
    #: Which readings (``markdown.Reading``) read it as an image with this path: the specification, pandoc and
    #: markdown-it differ in what a destination is (a DEL in it, a title glued to ``<…>``, a backslash before a
    #: tab), so one image may be several, and by the specification it may be a reference-style image,
    #: ``![r](x\x7f)`` being ``![r]`` and text (``reference_images``).
    readers: Reading = Reading.SPEC


#: A backslash escape of an ASCII punctuation character, or a character reference.
_ESCAPE_OR_ENTITY_RE = re.compile(
    r"\\([!-/:-@\[-`{-~])|&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
#: pandoc's: a backslash before a control character or DEL is an escape too (but for the tab, which it reads
#: as spaces).
_PANDOC_ESCAPE_OR_ENTITY_RE = re.compile(
    r"\\([!-/:-@\[-`{-~\x00-\x08\n-\x1f\x7f])|&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
#: ``[`` and ``]``, and a backslash with what it escapes: the brackets that count.
_BRACKET_RE = re.compile(r"\\.|[\[\]]", re.DOTALL)


def _decoded(destination: str, reading: Reading = Reading.SPEC) -> str:
    """*destination* with its escapes and character references decoded, as *reading* decodes them: pandoc also
    drops a backslash before a control character, and reads the text as NFC (which makes ``K:`` of the Kelvin
    sign a drive)."""
    if reading is Reading.PANDOC:
        decoded = _PANDOC_ESCAPE_OR_ENTITY_RE.sub(lambda m: m.group(1) or html.unescape(m.group()), destination)
        return decoded if decoded.isascii() else unicodedata.normalize("NFC", decoded)
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
    whose destination is empty has no path. Each is as the specification, pandoc and
    markdown-it read it (``Image.readers``): the readings that read it with one
    path are one image, and those that read it with others are others, at the same
    place. Linear in the length of *text*, however it is bracketed. *scanned* is
    ``scan(text, lexed)`` when the caller has it."""
    view, raw, closing = scanned or scan(text, lexed)
    starts = [directive.start for directive in lexed.directives]
    differs = MDIT_BACKSLASH_RE.search(raw) is not None  # (else markdown-it reads what the specification does)
    found: list[Image] = []
    at = view.find("![")
    while at >= 0:
        close = closing.get(at + 1)
        if close is not None and view.startswith("(", close + 1) and not is_escaped(view, at):
            # What any renderer reads as an image: pandoc's reading covers the specification's, which is the
            # same link when it holds nothing pandoc alone reads.
            pandoc = scan_inline_link(raw, close + 1, reading=Reading.PANDOC)
            spec = pandoc if pandoc is not None and pandoc.strict else None
            mdit = scan_inline_link(raw, close + 1, reading=Reading.MDIT) if differs else spec
            distinct: dict[tuple[str, str | None, bool], Image] = {}
            for reading, link in ((Reading.SPEC, spec), (Reading.PANDOC, pandoc), (Reading.MDIT, mdit)):
                if link is not None:
                    each = _image(text, lexed.directives, starts, at, link.start, link.stop, link.after, reading)
                    key = (each.path, each.literal, each.rest)
                    if key in distinct:
                        each = distinct[key]._replace(readers=distinct[key].readers | reading)
                    distinct[key] = each
            found.extend(distinct.values())
        at = view.find("![", at + 2)
    return found


def _image(
    text: str, directives: list[Directive], starts: list[int], at: int, start: int, stop: int, after: int,
    reading: Reading,
) -> Image:
    """The image at *at* whose destination is ``text[start:stop]``, as *reading* reads it."""
    # Whether a directive is in it is the lexer's to say: a NUL in the text is only a character.
    first = bisect_left(starts, start)
    if first < len(starts) and starts[first] < stop:
        return Image(
            text[start:stop], at, after, _decoded(text[start:starts[first]], reading),
            _more_than_directives(text, directives, first, stop), reading,
        )
    return Image(_decoded(text[start:stop], reading), at, after, readers=reading)


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
    text: str, lexed: Lexed, labels: Container[str], inline: Container[int] = (), scanned: Scan | None = None,
    key: Callable[[str], str] = normalize_label, skip: Callable[[int], bool] | None = None,
) -> list[str]:
    """The labels of the reference-style images of *text*, whose lexing is *lexed*, in order
    (§6.3, §8.7): ``![a][r]``, ``![a][]`` and ``![a]``, as the normalised keys
    (*key*: ``normalize_label``, or that of a reading's, ``label_key``) of the *labels* defined, which only a defined
    label is. *inline* holds the offsets of the inline images (``images``), which are not these; *skip* says of the
    offset of an image whether it is left out (a reading took the text it is in for the title of a definition). As in
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
        if close is not None and at not in inline and not is_escaped(view, at) and not (skip and skip(at)):
            end = label_end(raw, close + 1) if raw.startswith("[", close + 1) else None
            if end is None:
                label = raw[at + 2:close]  # the shortcut form, also where what follows is no label
            elif end == close + 2:
                label = raw[at + 2:close]  # the collapsed form
            else:
                label = raw[close + 2:end]  # the full form, which a blank label cannot be
            name = key(label)
            if name in labels:  # a blank label (``[ ]``) is none of them
                found.append(name)
        at = view.find("![", at + 2)
    return found


class ReferenceImages:
    """The images of a document that name their file by a link reference
    definition (``![a][r]`` and ``[r]: path``, §4.7, §8.7, #92): the path is the
    definition's, written once however many images use it, so it is checked once,
    where it is written, when an image names it. A definition no image names is no
    file reference, nor is a link's (§2.3). The document's definitions
    (``definitions.iter_link_definitions``) are found when the first text with an
    image in it asks, so a document with none pays nothing.

    Each of the specification, pandoc and markdown-it (``markdown.Reading``) has its own definitions, and
    its own images: what a reading names by a label is the first definition of it that *it* reads, the label as
    *it* matches labels (``label_key``). The model's definitions are the specification's; the others are
    those of the document read again under their reading (``path_definitions``), for a document that has text a
    reading differs on (a control character in a destination, a backslash before a tab). A definition that any
    reading's image names is judged, once, though several readings name it. In the combined document of the
    document's includes (§16.11, #117) each file is read again by itself, and the files' definitions are laid end
    to end as the files are spliced (``path_definitions.definitions``)."""

    def __init__(self, document: Document, where: Locator, combined: Combined | None = None) -> None:
        self._document = document
        self._where = where
        self._combined = combined
        #: What each reading made of each file's definitions (``path_definitions.read_definitions``), by the file's
        #: source and the reading: the combined pass has that of the document's own pass for its files, and of
        #: the fragments, each file once however often it is included.
        self.reads: dict[tuple[int, Reading], Read | None] = {} if combined is None else combined.definition_reads
        self._tables: dict[Reading, dict[str, Definition]] | None = None
        #: The lines a reading takes for definitions that the model has as text, by file (the number of its place):
        #: their starts and ends, sorted. The reading's images are not in them (``note``).
        self._consumed: dict[Reading, dict[int, tuple[list[int], list[int]]]] = {}
        #: The inline images of the text a reading has where the model has a definition, with where each lies.
        self._text_images: list[tuple[Image, tuple[int, ...], Line]] = []
        #: The labels of the definitions noted, by reading, each with whether an image outside a drafting note names it.
        self._used: dict[Reading, dict[str, bool]] = {reading: {} for reading in READINGS}

    def _files(self) -> list[Origin]:
        """The files of the document: itself, or the files its combined document is spliced from."""
        if self._combined is None:
            return [Origin(self._document, self._where, lambda line: line)]
        return [Origin(place.document, place.where, place.spot) for place in self._combined.instances]

    def _read(self, origin: Origin, reading: Reading) -> Read | None:
        key = (id(origin.document.source_map), reading)
        if key not in self.reads:
            self.reads[key] = read_definitions(origin.document, origin.where, reading)
        return self.reads[key]

    def _definitions(self) -> dict[Reading, dict[str, Definition]]:
        """The first definition of each label, as each reading reads the definitions and matches the labels."""
        if self._tables is None:
            # definitions -> validator.helpers -> validator/__init__ -> core -> paths: a cycle at import
            from ..definitions import iter_link_definitions

            where = self._where
            model: list[Definition] = []
            by_part: dict[tuple[int | None, int], list[Definition]] = {}
            for each in iter_link_definitions(self._document):
                found = Definition(
                    each.definition.label, each.definition.target,
                    (-1 if each.section is None else each.section, each.block, each.fragment, -1, each.ordinal),
                    partial(where.definition, each.section, each.block, each.ordinal),
                )
                model.append(found)
                by_part.setdefault((each.section, each.block), []).append(found)
            files = self._files()
            layout = None
            self._tables = {}
            for reading in READINGS:
                listing = model
                if reading is not Reading.SPEC:
                    reads = {id(origin.document): self._read(origin, reading) for origin in files}
                    if any(each is not None for each in reads.values()):
                        layout = layout or parts(self._document, files[0], self._combined)
                        listing = definitions(reading, layout, by_part, reads)
                        self._consumed[reading] = self._lines_of_definitions(files, reads)
                table: dict[str, Definition] = {}
                for each in listing:
                    key = label_key(each.label, reading)
                    if key:  # a blank label is none
                        table.setdefault(key, each)
                self._tables[reading] = table
                if reading in self._consumed:
                    for slot, origin, text in texts(layout or [], reads):
                        self._note_text(reading, table, slot, origin, text)
        return self._tables

    @staticmethod
    def _lines_of_definitions(files: list[Origin], reads: dict[int, Read | None]) -> dict[int, tuple[list[int], list[int]]]:
        consumed = {}
        for origin in files:
            got = reads[id(origin.document)]
            if got is not None:
                taken = sorted((each.line, each.end) for each_part in got.definitions.values() for each in each_part)
                consumed[getattr(origin.spot(1), "instance", 0)] = ([a for a, _b in taken], [b for _a, b in taken])
        return consumed

    def _skipped(self, reading: Reading, line: int | None) -> bool:
        """Whether *line*, a line of the document (a ``Spot`` in a fragment), is one *reading* takes for a definition."""
        starts_ends = self._consumed[reading].get(getattr(line, "instance", 0))
        if starts_ends is None or line is None:
            return False
        starts, ends = starts_ends
        number = bisect_right(starts, int(line)) - 1
        return number >= 0 and int(line) < ends[number]

    def _note_text(
        self, reading: Reading, table: dict[str, Definition], slot: tuple[int | None, int], origin: Origin, read: ReadText
    ) -> None:
        """Take note of the images of text that *reading* has where the model has a definition and its title (so that
        the model never scanned it): the definitions its reference-style images name, and its inline images."""
        text = read.text
        if "![" not in text:
            return
        lexed = lex(text)
        scanned = scan(text, lexed)
        found = images(text, lexed, scanned)
        inline = {image.start for image in found if image.readers & reading}
        if table:
            used = self._used[reading]
            skip = lambda offset: not read.counts(offset)  # noqa: E731 (the model scans the rest)
            for key in reference_images(text, lexed, table, inline, scanned, partial(label_key, reading=reading), skip):
                used[key] = True
        line, previous = read.line, 0  # (the line of an image, on from the last one's: linear, #94)
        for image in found:
            line += text.count("\n", previous, image.start)
            previous = image.start
            if image.readers & reading and read.counts(image.start):
                column = image.start - (text.rfind("\n", 0, image.start) + 1)
                at = (-1 if slot[0] is None else slot[0], slot[1], 0, -1, line, column)
                self._text_images.append((image, at, origin.spot(line)))

    def note(
        self, text: str, lexed: Lexed, images: Container[Image] = (), scanned: Scan | None = None, *,
        noted: bool = False, line_of: Callable[[int], int | None] | None = None,
    ) -> None:
        """Take note of the definitions the images of *text* name, for each reading. *images*: its inline
        images (``images``), which are not reference-style ones; *scanned*: ``scan(text, lexed)``; *noted*:
        *text* is in a drafting note; *line_of*: the line of an offset in *text*, which tells an image that a
        reading's definition holds (in its title) from one in text (the reading displays only the second)."""
        if "![" not in text:
            return
        for reading, table in self._definitions().items():
            if table:
                inline = {image.start for image in images if image.readers & reading}
                used = self._used[reading]
                skip = None
                if reading in self._consumed and line_of is not None:
                    skip = partial(self._skipped_at, reading, line_of)
                for key in reference_images(text, lexed, table, inline, scanned, partial(label_key, reading=reading), skip):
                    used[key] = used.get(key, False) or not noted

    def heading_lines(self, index: int) -> Callable[[int], int | None]:
        """The line of an offset in the title of heading *index* (its lines are joined, a space between). The lines are
        walked on from where the last offset was, so that the offsets in increasing order are linear (#94)."""
        walk: list[Any] = []  # the origin, its lines, the line index and the offset it begins at; once asked

        def line_of(offset: int) -> int | None:
            if not walk:
                if self._combined is None:
                    origin, local = self._files()[0], index
                else:
                    place, local = self._combined.section_origin[index]
                    origin = Origin(place.document, place.where, place.spot)
                start, body = origin.where.heading(local), origin.where.body()
                if start is None or body is None:
                    walk.append(None)
                else:
                    walk.extend([origin, body[0], body[1], start - body[1], 0, start - body[1]])
            if walk[0] is None:
                return None
            origin, lines, first, number, position, begin = walk
            if offset < position:  # (an earlier offset than the last: from the start)
                number, position = begin, 0
            while 0 <= number < len(lines):
                size = len(lines[number].strip(" \t")) + 1
                if offset < position + size:
                    walk[3], walk[4] = number, position
                    return origin.spot(first + number)
                position += size
                number += 1
            return origin.spot(first + begin)

        return line_of

    def live_readers(self, image: Image, line_of: Callable[[int], int | None]) -> Reading:
        """The readings that read *image* as an image of the text, not of a definition's title: one that takes the
        line for a definition shows no image there (``line_of``: the line of an offset)."""
        readers = image.readers
        for reading in self._consumed:
            if readers & reading and self._skipped(reading, line_of(image.start)):
                readers &= ~reading
        return readers

    def _skipped_at(self, reading: Reading, line_of: Callable[[int], int | None], offset: int) -> bool:
        return self._skipped(reading, line_of(offset))

    def check(self, references: FileReferences) -> None:
        """Record the path of each definition noted, in document order (the top-level
        block it is in, then its place among that block's texts, then its line), at the line
        of the definition's own block. A definition is judged once, as it is written
        (its line), however many readings name it, and once for each path they read it as."""
        if self._tables is None:
            # No text of the model has an image: but a reading may have one where the model has a definition's title.
            if not any(
                (body := origin.where.body()) is not None and "![" in (text := "\n".join(body[0])) and "]:" in text
                for origin in self._files()
            ):
                return
            self._definitions()
        judged_text: set[tuple[object, ...]] = set()
        for image, at, line in self._text_images:
            # (Both readings that have the text have the image: once.)
            if (key := (getattr(line, "instance", 0), at[4:], image.path)) not in judged_text:
                judged_text.add(key)
                references.check(
                    image.path, "image", line, at=at, url=True, literal=image.literal, rest=image.rest
                )
        if not any(self._used.values()):
            return
        # What is judged, by the definition (where it is written) and the path it is read as: where it lies, its
        # line, and whether an image outside a drafting note names it.
        chosen: dict[tuple[object, str], tuple[Definition, tuple[int, ...], Line, bool]] = {}
        for reading, used in self._used.items():
            table = self._tables[reading]
            for key, outside in used.items():
                each = table[key]
                line = each.line() if callable(each.line) else each.line
                if not line:  # (a fragment's, unknown: a ``Spot`` of 0)
                    # A model without lines (built in code): the specification's definitions alone, told
                    # apart by where they lie.
                    at, identity = each.at, each.at
                else:
                    at, identity = (*each.at[:4], int(line)), (getattr(line, "instance", 0), int(line))
                slot = (identity, _decoded(each.target, reading))
                first = chosen.get(slot)
                chosen[slot] = (each, at, line, outside) if first is None else (*first[:3], outside or first[3])
        for (_identity, path), (each, at, line, outside) in sorted(chosen.items(), key=lambda item: item[1][1]):
            # (The finding names the label as CommonMark compares it, line breaks and runs of spaces one space.)
            label = collapse_label_whitespace(each.label)
            references.check(path, f"image, by the definition of [{label}]", line, at=at, url=True, noted=not outside)


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
            line_of = None
            if named is not None:
                line_of = named.heading_lines(index)
                named.note(title, lexed, found, scanned, line_of=line_of)
            for image in found:
                if named is not None and line_of is not None and not named.live_readers(image, line_of):
                    continue  # (in the title of a definition to every reading that reads it)
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
    line_of = None
    if named is not None:
        # The line of an offset: found from the first image, and on from the last offset asked (they come in order,
        # and only when a reading asks).
        anchor = text.find("![")
        anchor_line = cache(partial(where.find, section, block, "![", fragment, anchor))
        last = [anchor, 0]
        exact: list[bool] = []  # whether the lines of the text are those of the source: not a heading's, joined

        def line_of(offset: int) -> int | None:
            line = anchor_line()
            if line is None:
                return None
            if not exact:
                end = text.rfind("![")
                there = where.find(section, block, "![", fragment, end)
                exact.append(there is not None and int(there) == int(line) + text.count("\n", anchor, end))
            if not exact[0]:
                return None
            if offset < last[0] or not last[1]:
                last[:] = [anchor, line]
            last[1] += text.count("\n", last[0], offset)
            last[0] = offset
            return last[1]

        named.note(text, lexed, found, scanned, noted=noted, line_of=line_of)
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
        if named is not None and line_of is not None and not named.live_readers(image, line_of):
            continue  # (in the title of a definition to every reading that reads it)
        references.check(
            image.path, "image", partial(_below, first_line, breaks),
            at=(-1 if section is None else section, block, fragment, image.start), url=True, literal=image.literal,
            rest=image.rest, noted=noted,
        )


def _below(first: Callable[[], int | None], lines: int) -> int | None:
    """The line *lines* below the one ``first()`` gives."""
    line = first()
    return None if line is None else line + lines
