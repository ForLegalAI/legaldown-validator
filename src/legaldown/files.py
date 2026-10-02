"""Reading the files a document refers to.

A document names other files by a path relative to itself: the document it
amends (§7.5), its include fragments (§12.2), its LegalDown attachment files
(§12.4). ``LoadFile`` is how a caller answers for such a path;
``file_loader`` is the answer for files on disk.
"""
from __future__ import annotations

import os
import posixpath
from collections.abc import Callable
from pathlib import Path

#: Reads a file the document refers to, by its path as the document writes it
#: (relative, ``/``-separated); ``None`` when there is no such file.
LoadFile = Callable[[str], "str | None"]


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
