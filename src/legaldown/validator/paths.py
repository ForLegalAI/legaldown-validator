"""The files a document names, by their paths (§2.3, §16.2): path-not-relative
and path-outside-root (#104).

Both rules are syntactic (§2.3, §17.2): they read the text of a path and never
a file, so existence (Full, §17.4) is not asked, and a symbolic link is not
followed. The paths are the ones §2.3 lists: ``{{include:}}`` (§12),
``attachments[].file`` (§3.9), ``amends.file`` (§3.8), ``supersedes.file``
(§3.2), ``translations`` (§14) and image paths (§8.7). A link or an autolink
is no file reference, and raw HTML is ``raw-html``'s.
"""
from __future__ import annotations

import html
import os
import re
import string
from collections.abc import Callable
from functools import cache, partial
from typing import NamedTuple
from urllib.parse import unquote

from ..directives import Lexed, is_escaped, mask_directives
from ..files import is_absolute_reference, is_within_root, resolves_within
from ..models import Amends, Document, Metadata
from ..positions import Locator
from .result import Line, _Recorder


class FileReferences:
    """Checks the file references of one document against §2.3, recording
    path-not-relative and path-outside-root in *result*.

    *root* is the configured document root (None: the containment check does not
    run, and ``../shared/definitions.lgd`` is valid, §2.3). *base* is the directory
    the paths are relative to: that of a loaded document; a parsed string has
    none and is assumed to sit at the root, so a leading ``..`` leaves it. *line*
    is where a document outside the root is reported."""

    def __init__(
        self,
        result: _Recorder,
        root: str | os.PathLike[str] | None,
        base: str | os.PathLike[str] | None,
        line: Line,
    ) -> None:
        self._result = result
        self._root = None if root is None else os.fspath(root)
        self._base = self._root if base is None else os.fspath(base)
        self._line = line
        # Every path of a document outside the root leads out of it: one finding
        # says so, not one for each path (#104).
        self._outside = self._root is not None and not is_within_root(self._base, self._root)
        self._said_outside = False

    def check(self, path: str, what: str, line: Line, *, percent_decoded: bool = False) -> None:
        """Check *path*, which *what* names (``attachments[0].file``, ``image``),
        found at *line*. With *percent_decoded* the path is also read as a
        renderer or a tool opening the file would, with ``%2F`` as ``/`` and
        ``%2E%2E`` as ``..`` (image destinations are URLs, §8.7). A path that is
        empty is not this rule's concern (``attachment-file-missing``)."""
        written = path.strip()
        if not written:
            return
        forms = [written]
        if percent_decoded and "%" in written and (decoded := unquote(written)) != written:
            forms.append(decoded)
        for form in forms:
            if is_absolute_reference(form):
                self._result.error(
                    "path-not-relative",
                    f"File reference '{written}' ({what}{_read_as(form, written)}) is not relative: an absolute "
                    f"path, or one beginning with a URI scheme (https://, file://, a drive letter), is an Error. "
                    f"Write a path relative to the document (§2.3).",
                    line=line,
                )
                return  # not also outside the root: one finding for one path
        if self._root is None:
            return
        if self._outside:
            if not self._said_outside:
                self._said_outside = True
                self._result.error(
                    "path-outside-root",
                    f"The document is outside the document root '{self._root}', so none of the files it names "
                    f"lies within it (§2.3). The first is '{written}' ({what}).",
                    line=self._line,
                )
            return
        for form in forms:
            if not resolves_within(form, self._base, self._root):
                self._result.error(
                    "path-outside-root",
                    f"File reference '{written}' ({what}{_read_as(form, written)}) leads out of the document root "
                    f"'{self._root}' (§2.3).",
                    line=line,
                )
                return


def _read_as(form: str, written: str) -> str:
    """What a finding says of the form of a path other than the one written."""
    return "" if form == written else f", read as '{form}' once percent-decoded"


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
    and the offsets in the text of the image from ``!`` to ``)``."""

    path: str
    start: int
    end: int


#: A backslash escape of an ASCII punctuation character, or a character reference.
_ESCAPE_OR_ENTITY_RE = re.compile(
    r"\\([!-/:-@\[-`{-~])|&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
#: ``[`` and ``]``, and a backslash with what it escapes: the brackets that count.
_BRACKET_RE = re.compile(r"\\.|[\[\]]", re.DOTALL)
#: A raw destination may hold balanced parentheses (CommonMark); more than this
#: many deep is taken as not a destination, which keeps ``![a](![a](…`` linear:
#: no scan runs past the 33rd ``(``.
_MAX_PARENTHESES = 32


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


def _escapes(raw: str, at: int) -> bool:
    """True if a backslash at ``raw[at]`` escapes the character after it: ASCII punctuation."""
    return raw[at] == "\\" and raw[at + 1:at + 2] != "" and raw[at + 1] in string.punctuation


def _skip_space(raw: str, at: int) -> int:
    while at < len(raw) and raw[at] in " \t\n":
        at += 1
    return at


def _destination(raw: str, opening: int) -> tuple[str, int] | None:
    """The inline link destination that follows the ``(`` at ``raw[opening]``
    (CommonMark): ``<…>``, or a raw one with balanced parentheses, then an
    optional title and the ``)``. ``(destination as written, offset after the
    ")")``, or None when what follows is no inline link. A destination with a
    directive in it (``mask_directives`` fills it with NUL) is none: its path is
    not known until the directive is, and assembly's output is validated again
    (§15.7.4)."""
    size = len(raw)
    at = _skip_space(raw, opening + 1)
    if raw.startswith("<", at):
        end = at + 1
        while end < size and raw[end] not in "<>\n":
            end += 2 if _escapes(raw, end) else 1
        if raw[end:end + 1] != ">" or "\0" in raw[at:end]:
            return None
        destination, at = raw[at + 1:end], end + 1
    else:
        depth = 0
        end = at
        while end < size:
            char = raw[end]
            if _escapes(raw, end):
                end += 2
                continue
            if char == " " or ord(char) < 32:
                break
            if char == "(":
                depth += 1
                if depth > _MAX_PARENTHESES:
                    return None
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        if depth:
            return None
        destination, at = raw[at:end], end
    at = _skip_space(raw, at)
    if raw[at:at + 1] in ('"', "'", "("):
        closer = ")" if raw[at] == "(" else raw[at]
        end = at + 1
        while end < size and raw[end] != closer:
            if _escapes(raw, end):
                end += 2
                continue
            if closer == ")" and raw[end] == "(":
                return None  # a title in parentheses holds none: this scan ends at the next ``(``, not at the end
            end += 1
        if end >= size:
            return None
        at = _skip_space(raw, end + 1)
    return (destination, at + 1) if raw[at:at + 1] == ")" else None


def images(text: str, lexed: Lexed) -> list[Image]:
    """The inline images of *text*, whose lexing is *lexed*, in order (§8.7): outside
    code spans and comments, which the lexer blanks. An image in the alt text of
    another is one too. ``\\![a](x)`` is a link, with a literal ``!``. An image
    whose destination is empty, or holds a directive, has no path. Linear in the
    length of *text*, however it is bracketed."""
    # The lexer's view finds the images (brackets in a code span count for
    # nothing); the destination is read from the text as it is written, since a
    # backtick there is no code span: the link is parsed before it.
    view = mask_directives(lexed.view, lexed.directives)
    raw = mask_directives(text, lexed.directives)
    closing = _closing_brackets(view)
    found: list[Image] = []
    at = view.find("![")
    while at >= 0:
        close = closing.get(at + 1)
        if close is not None and view.startswith("(", close + 1) and not is_escaped(view, at):
            link = _destination(raw, close + 1)
            if link is not None:
                found.append(Image(_decoded(link[0]), at, link[1]))
        at = view.find("![", at + 2)
    return found


def check_image_paths(
    document: Document, lex_fragment: Callable[[str], Lexed], references: FileReferences, where: Locator
) -> None:
    """Check the path of each inline image (§8.7) of the headings and the free
    text of the body: the paragraphs, list items, quotes and table cells, but not
    code or raw HTML blocks, whose fragments ``block_fragments`` leaves out. A
    path is percent-decoded as well, and a reference-style image
    (``![a][r]``, ``[r]: path``) is not read: link reference definitions are not
    in the model (#92)."""
    from ..definitions import block_fragments  # see the import note in _validate

    for index, section in enumerate(document.sections):
        title = section.title
        if "![" in title:
            for image in images(title, lex_fragment(title)):
                references.check(image.path, "image", where.heading(index), percent_decoded=True)
    for section_index, block_index, block in document.iter_indexed_blocks():
        for fragment_index, (text, _position) in enumerate(block_fragments(block)):
            if "![" not in text:
                continue
            found = images(text, lex_fragment(text))
            if not found:
                continue
            # The first image is looked for in the source; each of the others is as many
            # lines below it as its text has line breaks before it (a fragment's lines are
            # its source's). ``Locator.find`` looks for a needle that is no directive from
            # the start of its block, which for every image of a long paragraph is
            # quadratic (#94, #104).
            first = found[0]
            first_line = cache(
                partial(where.find, section_index, block_index, text[first.start:first.end], fragment_index, first.start)
            )
            breaks, previous = 0, first.start
            for image in found:
                breaks += text.count("\n", previous, image.start)
                previous = image.start
                references.check(image.path, "image", partial(_below, first_line, breaks), percent_decoded=True)


def _below(first: Callable[[], int | None], lines: int) -> int | None:
    """The line *lines* below the one ``first()`` gives."""
    line = first()
    return None if line is None else line + lines
