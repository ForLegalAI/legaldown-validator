"""What the tests that compare with other renderers share (markdown-it-py, pandoc)."""
from __future__ import annotations

import re
import shutil

import pytest

try:
    import markdown_it
except ImportError:  # (a development dependency: the tests that ask it are skipped)
    markdown_it = None

needs_markdown_it = pytest.mark.skipif(markdown_it is None, reason="markdown-it-py is not installed")
needs_pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")

#: A blank line or a quote line ends the paragraph a link is in.
PARAGRAPH_END_RE = re.compile(r"\n[ \t]*(?:\n|>)")
#: markdown-it reads a backslash before any character as an escape pair, not only before punctuation (cmark and
#: pandoc do not): the images it alone reads that way are not judged (CONFORMANCE.md, #121).
BACKSLASH_PAIR_RE = re.compile(r"\\(?![!-/:-@\[-`{-~])")
#: What markdown-it normalizes in a destination that looks like a URL with a scheme (``b:[r]<`` is ``b:r<``).
URL_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,31}:")
