"""Reading the files a document refers to.

A document names other files by a path relative to itself: the document it
amends (§7.5), its include fragments (§12.2), its LegalDown attachment files
(§12.4). ``LoadFile`` is how a caller answers for such a path;
``file_loader`` is the answer for files on disk.
"""
from __future__ import annotations

import contextlib
import os
import posixpath
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: Reads a file the document refers to, by its path as the document writes it
#: (relative, ``/``-separated); ``None`` when there is no such file.
LoadFile = Callable[[str], "str | None"]


#: A URI scheme at the start of a reference (RFC 3986): ``https:``, ``file:``,
#: ``data:`` and, for the same reason, a drive letter, ``C:``.
_URI_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def has_uri_scheme(path: str) -> bool:
    """True if *path* begins with a URI scheme (§2.3): ``https://x``, ``file:///x``,
    ``data:x``, ``mailto:x``, and ``C:/x`` or ``C:x`` with a drive letter. A
    relative file whose name holds a colon before any ``/`` reads as having one
    (RFC 3986 says to write ``./a:b.lgd``)."""
    return _URI_SCHEME_RE.match(path) is not None


def is_absolute_reference(path: str) -> bool:
    """True if *path*, as a document names a file (§2.3), is not a relative path: it
    begins with a URI scheme, or with ``/`` or ``\\`` (an absolute path, ``//host``,
    ``\\\\host``). Judged by its text alone: no file is looked at, and ``../x``
    is relative (``leaves_root`` tells where it leads)."""
    return has_uri_scheme(path) or path[:1] in ("/", "\\")


def names_below(directory: str | os.PathLike[str], root: str | os.PathLike[str]) -> int | None:
    """How many names *directory* is below *root*, by their text alone (both are
    made absolute, and no file is looked at), or None when it is not at or below it."""
    try:
        below = Path(os.path.abspath(directory)).relative_to(os.path.abspath(root))
    except ValueError:
        return None
    return len(below.parts)


def leaves_root(path: str, depth: int) -> bool:
    """True if the relative *path*, read from a directory *depth* names below the
    root (``names_below``), leads out of the root (§2.3). Lexical: ``..`` is taken
    off the name before it, and either separator splits a name."""
    for name in re.split(r"[/\\]", path):
        if name == "..":
            depth -= 1
            if depth < 0:
                return True
        elif name not in ("", "."):
            depth += 1
    return False


def containment(path: str, *, rooted: bool, depth: int | None) -> Literal["document-outside", "leaves"] | None:
    """Whether the relative *path*, by its text alone, is outside the document root (§2.3): the one
    judgement that ``FileReferences`` (the paths of a document) and ``Reach`` (the files it reads) share.
    *rooted*: a root is configured, else the containment check does not run (None); *depth*: how
    many names the document is below it (``names_below``), None when it is not within it
    (``"document-outside"``: every path of it leads out); else ``"leaves"`` when the path leads out of the
    root and None when it stays within."""
    if not rooted:
        return None
    if depth is None:
        return "document-outside"
    return "leaves" if leaves_root(path, depth) else None


def _check_relative(path: str, depth: int | None = None) -> tuple[str, Literal["leads-out", "invalid"] | None]:
    """The rules ``relative_path`` and ``relative_path_problem`` share, held once: *path*
    normalised (empty when it is none, and not worth normalising) and what is wrong with it.
    *depth* is how many names the document is below its document root (``names_below``), when
    one is configured: a leading ``../`` run that stays within the root is then a path to read."""
    if not path or "\\" in path or "\0" in path or is_absolute_reference(path):
        return "", "invalid"
    normalized = posixpath.normpath(path)
    if normalized == ".":
        return normalized, "invalid"
    if normalized == ".." or normalized.startswith("../"):
        if depth is None or containment(normalized, rooted=True, depth=depth) is not None:
            return normalized, "leads-out"
        # Within the root, but ``..`` alone is a directory, as ``.`` is.
        return normalized, "invalid" if normalized == ".." else None
    return normalized, None


def relative_path_problem(path: str, *, depth: int | None = None) -> Literal["leads-out", "invalid"] | None:
    """Why ``relative_path`` refuses *path*: ``"leads-out"`` when it is a relative path that
    leads out of the document's directory (``../x``, §2.3), which is valid as a path though
    not one a ``LoadFile`` is asked for (unless *depth* says it stays within the document
    root); ``"invalid"`` when it is none (empty, absolute,
    with a URI scheme or a drive letter, a backslash or a null byte, or the directory
    itself); None when it is a path to read."""
    return relative_path_check(path, depth=depth)[1]


def relative_path_check(
    path: str, *, depth: int | None = None
) -> tuple[str | None, Literal["leads-out", "invalid"] | None]:
    """``relative_path`` and ``relative_path_problem`` of *path* at once: the normalized path to read
    (None when it is none) and why not (None when it is a path to read)."""
    normalized, problem = _check_relative(path, depth)
    return (None if problem else normalized), problem


def relative_path(path: str, *, depth: int | None = None) -> str | None:
    """*path*, as a document names a file it refers to, normalized — ``/``-separated,
    within the directory of the document (§2.3) — or None when it is not such a path:
    empty, absolute, with a URI scheme (``https://x``, ``file:///x``, a drive letter),
    a backslash or a null byte, or leading out of the directory
    (``relative_path_problem`` says which). A ``LoadFile`` is asked for these paths
    and no others.

    With *depth*, how many names the document is below its document root
    (``names_below``), a path that leads out of the document's directory but stays
    within the root (``../shared/x.lgd``, from a document one name below it) is one to
    read too, normalized with its leading ``../``: ``../x.lgd``. Without it (no root is
    configured, which has no containment check, §2.3) such a path is valid and not
    read."""
    return relative_path_check(path, depth=depth)[0]


@dataclass(frozen=True, slots=True)
class Reach:
    """Which of the paths a document names may be read (§2.3): with a *root* configured,
    the document's *depth* below it (``names_below``; None when the document is not
    within it, so that nothing it names is); without one, nothing that leads out of the
    document's directory (``../x`` is valid, and not read)."""
    root: Path | None = None
    depth: int | None = None

    @classmethod
    def of(
        cls, root: str | os.PathLike[str] | None, directory: str | os.PathLike[str] | None
    ) -> Reach:
        """The reach of a document in *directory* (None: at the root, as a document that is
        not loaded is taken to be) under *root* (None: none is configured)."""
        if root is None:
            return cls()
        return cls(Path(os.path.abspath(root)), names_below(root if directory is None else directory, root))

    def check(self, path: str) -> tuple[str | None, Literal["leads-out", "invalid"] | None]:
        """``relative_path_check`` of *path* for this reach."""
        if self.root is not None and self.depth is None:
            return None, "leads-out"  # the document is outside the root: so is everything it names
        return relative_path_check(path, depth=self.depth)

    def relative(self, path: str) -> str | None:
        """``relative_path`` of *path* for this reach."""
        return self.check(path)[0]


def file_loader(base: str | os.PathLike[str], *, root: str | os.PathLike[str] | None = None) -> LoadFile:
    """A ``LoadFile`` reading from directory *base* on disk.

    A path that is absolute, leads out of *base* (§2.3), is no path at all (a
    null byte, a symbolic-link loop), or names no UTF-8 file reads as ``None``.
    The text is returned as written, with no line-break translation, since
    assembly edits it byte for byte (§15.7.2).

    With a document *root* (§2.3), a path may lead out of *base* as long as the file
    it names, once symbolic links are followed, is within the root: nothing outside
    the root is read, whatever *base* is.
    """
    # Made absolute now: a later change of the working directory changes nothing it reads.
    directory = Path(os.path.abspath(base))
    boundary = None if root is None else Path(os.path.abspath(root))

    def load_file(relative: str) -> str | None:
        target = within(directory, relative, boundary)
        try:
            return target.read_bytes().decode("utf-8") if target is not None and target.is_file() else None
        except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
            return None

    return load_file


def within(base: Path, relative: str, root: str | os.PathLike[str] | Path | None = None) -> Path | None:
    """*relative* under directory *base*, or None when it is absolute, leads
    out of it, or is no path at all. With a document *root*, it is the root it
    must be under (both resolved, so a symbolic link cannot lead out of it) and
    *base* is only where *relative* is joined: a file beside a document that is
    itself outside the root is outside it too."""
    if not relative or posixpath.isabs(relative) or Path(relative).is_absolute():
        return None
    try:
        directory = base.resolve()
        target = (directory / relative).resolve()
        boundary = directory if root is None else Path(root).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    return target if target.is_relative_to(boundary) else None


def write_atomically(path: Path, text: str) -> None:
    """*text* to *path* as UTF-8: all of it, or the file as it was — written
    beside it and moved into place, since the file may be a document or the
    answers a person wrote. A symbolic link is written through, the file keeps
    its permissions (a new one gets those a file made now would have), and
    missing directories on the way are created."""
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        mode = stat.S_IMODE(target.stat().st_mode)
    else:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(name, mode)
        os.replace(name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise
