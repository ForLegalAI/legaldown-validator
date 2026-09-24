"""CommonMark block syntax shared by the parser, the directive lexer, and the
serializer: fenced code blocks and indentation.

Keeping these rules in one place means the parser (finding code blocks), the
lexer (skipping fenced code inside block quotes and list items, §11.4), and
the serializer (writing code back) cannot disagree about where a code block
starts or ends.
"""
from __future__ import annotations

import re

# A fenced code block opens with three or more backticks or tildes, indented
# at most three columns; a backtick fence's info string cannot contain a
# backtick.
FENCE_OPEN_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}(?=[^`]*$)|~{3,})")

# HTML blocks (CommonMark 0.31, 4.6). Kinds 1–5 run until a line holding
# their end marker, the start line included, or to the end of the text.
_HTML_BLOCKS_TO_MARKER: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(r" {0,3}<(?:script|pre|style|textarea)(?:[ \t>]|$)", re.IGNORECASE),
        re.compile(r"</(?:script|pre|style|textarea)>", re.IGNORECASE),
    ),
    (re.compile(r" {0,3}<!--"), re.compile(r"-->")),
    (re.compile(r" {0,3}<\?"), re.compile(r"\?>")),
    (re.compile(r" {0,3}<![A-Za-z]"), re.compile(r">")),
    (re.compile(r" {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
)
# Kind 6: a block-level tag. Kinds 6 and 7 run until a blank line.
_HTML_BLOCK_6 = (
    r" {0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup"
    r"|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset"
    r"|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol"
    r"|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr"
    r"|track|ul)(?:[ \t]|/?>|$)"
)
# Kind 7: a line holding only a complete open or closing tag of any other
# name. It cannot interrupt a paragraph.
_ATTRIBUTE = r"""(?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*(?:[^ \t"'=<>`]+|'[^']*'|"[^"]*"))?)"""
_HTML_BLOCK_7_RE = re.compile(
    r" {0,3}(?:<(?!(?:script|style|pre|textarea)\b)[A-Za-z][A-Za-z0-9-]*" + _ATTRIBUTE + r"*[ \t]*/?>"
    r"|</[A-Za-z][A-Za-z0-9-]*[ \t]*>)[ \t]*$",
    re.IGNORECASE,
)

# The start of an HTML block that may interrupt a paragraph (CommonMark
# kinds 1–6): script, pre, style, or textarea; a comment; a processing
# instruction; a declaration; CDATA; or a block-level tag. A line starting
# with any other ``<`` — an autolink, an inline tag — is paragraph text.
HTML_BLOCK_START_RE = re.compile(
    "|".join([*(start.pattern for start, _end in _HTML_BLOCKS_TO_MARKER), _HTML_BLOCK_6]),
    re.IGNORECASE,
)
_HTML_BLOCK_6_RE = re.compile(_HTML_BLOCK_6, re.IGNORECASE)


def html_block_end(lines: list[str], index: int) -> int | None:
    """The index just past the HTML block (CommonMark kinds 1–7) starting
    at ``lines[index]``, or None when none starts there. The caller rules
    out a paragraph that the line would continue, which only kinds 1–6
    interrupt (``HTML_BLOCK_START_RE``)."""
    line = lines[index]
    for start, end_marker in _HTML_BLOCKS_TO_MARKER:
        if start.match(line):
            return next(
                (end + 1 for end in range(index, len(lines)) if end_marker.search(lines[end])),
                len(lines),
            )
    if _HTML_BLOCK_6_RE.match(line) or _HTML_BLOCK_7_RE.match(line):
        end = index + 1
        while end < len(lines) and lines[end].strip():
            end += 1
        return end
    return None


# An HTML comment (CommonMark 0.31): ``<!-->``, ``<!--->``, or ``<!--``, text
# not containing ``-->``, and ``-->``. The empty forms end at their own ``>``.
HTML_COMMENT_RE = re.compile(r"<!--(?:-?>|.*?-->)", re.DOTALL)

_TAB = 4  # a tab advances to the next multiple of four columns


def indent_width(line: str) -> int:
    """Columns of leading whitespace, a tab counting to the next tab stop."""
    column = 0
    for char in line:
        if char == " ":
            column += 1
        elif char == "\t":
            column += _TAB - column % _TAB
        else:
            break
    return column


def dedent(line: str, columns: int) -> str:
    """*line* with up to *columns* columns of leading whitespace removed; a
    tab straddling the cut leaves its remaining columns as spaces. Only
    leading whitespace changes."""
    column = index = 0
    while index < len(line) and line[index] in " \t" and column < columns:
        column += 1 if line[index] == " " else _TAB - column % _TAB
        index += 1
    return " " * max(column - columns, 0) + line[index:]


def closes_fence(line: str, fence: str) -> bool:
    """True if *line* closes a code block opened with *fence*: at most three
    columns of indentation, then the same character at least as many times,
    and nothing else."""
    stripped = line.strip()
    return (
        indent_width(line) <= 3
        and len(stripped) >= len(fence)
        and set(stripped) == {fence[0]}
    )


def fence_end(lines: list[str], index: int, fence: str) -> int:
    """Index just past the fenced code block opening at ``lines[index]``;
    an unclosed fence runs to the end of *lines*."""
    for end in range(index + 1, len(lines)):
        if closes_fence(lines[end], fence):
            return end + 1
    return len(lines)


def close_fences(text: str) -> str:
    """*text* with a closing fence appended if a fenced code block in it is
    left open, so that it cannot run on into what follows it."""
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        opening = FENCE_OPEN_RE.match(lines[index])
        if opening is None:
            index += 1
            continue
        fence = opening.group("fence")
        end = fence_end(lines, index, fence)
        closed = end - 1 > index and closes_fence(lines[end - 1], fence)
        if not closed:
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip(" "))]
            return text + "\n" + indent + fence
        index = end
    return text
