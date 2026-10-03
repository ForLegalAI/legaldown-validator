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
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

#: Reads a file the document refers to, by its path as the document writes it
#: (relative, ``/``-separated); ``None`` when there is no such file.
LoadFile = Callable[[str], "str | None"]


def relative_path(path: str) -> str | None:
    """*path*, as a document names a file it refers to, normalized — ``/``-separated,
    within the directory of the document (§2.3) — or None when it is not such a path:
    empty, absolute, with a drive letter or a backslash or a null byte, or leading out
    of the directory. A ``LoadFile`` is asked for these paths and no others."""
    if not path or "\\" in path or "\0" in path or posixpath.isabs(path) or (len(path) > 1 and path[0].isalpha() and path[1] == ":"):
        return None
    normalized = posixpath.normpath(path)
    if normalized in (".", "..") or normalized.startswith("../"):
        return None
    return normalized


def file_loader(base: str | os.PathLike[str]) -> LoadFile:
    """A ``LoadFile`` reading from directory *base* on disk.

    A path that is absolute, leads out of *base* (§2.3), is no path at all (a
    null byte, a symbolic-link loop), or names no UTF-8 file reads as ``None``.
    The text is returned as written, with no line-break translation, since
    assembly edits it byte for byte (§15.7.2).
    """
    root = Path(base)

    def load_file(relative: str) -> str | None:
        target = within(root, relative)
        try:
            return target.read_bytes().decode("utf-8") if target is not None and target.is_file() else None
        except (OSError, ValueError):  # UnicodeDecodeError is a ValueError
            return None

    return load_file


def within(base: Path, relative: str) -> Path | None:
    """*relative* under directory *base*, or None when it is absolute, leads
    out of it, or is no path at all."""
    if not relative or posixpath.isabs(relative) or Path(relative).is_absolute():
        return None
    try:
        root = base.resolve()
        target = (root / relative).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    return target if target.is_relative_to(root) else None


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
