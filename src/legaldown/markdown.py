"""CommonMark block syntax shared by the parser, the directive lexer, and the
serializer: fenced code blocks and indentation.

Keeping these rules in one place means the parser (finding code blocks), the
lexer (skipping fenced code inside block quotes and list items, §11.4), and
the serializer (writing code back) cannot disagree about where a code block
starts or ends.
"""
from __future__ import annotations

import re
import string
from bisect import bisect_left
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

    from .models import Block

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
# Kind 7: a line holding only a complete open or closing tag. It cannot
# interrupt a paragraph. The four kind-1 names are tested first, as kind 1,
# so here they only match forms kind 1 does not take, such as <pre/>, which
# CommonMark implementations read as kind 7.
_ATTRIBUTE = r"""(?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*(?:[^ \t"'=<>`]+|'[^']*'|"[^"]*"))?)"""
_HTML_BLOCK_7_RE = re.compile(
    r" {0,3}(?:<[A-Za-z][A-Za-z0-9-]*" + _ATTRIBUTE + r"*[ \t]*/?>"
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


def html_block_opening(line: str, *, in_paragraph: bool = False) -> tuple[re.Pattern[str] | None] | None:
    """How the HTML block that *line* starts ends: ``(marker,)``, the
    pattern whose match on a line, this one included, ends it, or None for
    a kind that ends before a blank line. None when *line* starts none —
    after paragraph text (*in_paragraph*), kind 7 does not."""
    for start, end_marker in _HTML_BLOCKS_TO_MARKER:
        if start.match(line):
            return (end_marker,)
    if _HTML_BLOCK_6_RE.match(line) or (not in_paragraph and _HTML_BLOCK_7_RE.match(line)):
        return (None,)
    return None


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
        while end < len(lines) and not is_blank(lines[end]):
            end += 1
        return end
    return None


def split_lone_tag(text: str) -> str | None:
    """*text*, with a line break in place of its first plain space, if
    *text* (no line break of its own) is by itself a complete HTML tag
    (kind 7): written on one line, it would misread as an HTML block
    rather than the paragraph text it is, and it cannot interrupt a
    paragraph, so a preceding blank line is enough to start it. None when
    *text* is not one, or holds no plain space to split at (a tab-only
    attribute separator, or a bare tag): the caller falls back to escaping
    it. Only a model that joined a paragraph's two lines at a space holds
    one: one built in code, or read by an earlier version, which joined
    them so."""
    if not _HTML_BLOCK_7_RE.match(text):
        return None
    at = text.find(" ")
    if at < 0:
        return None
    first, second = text[:at], text[at + 1:]
    # Neither line may open a block of its own: a block-level tag name
    # (``<div``) would, and so would a second line opening like a quote.
    if HTML_BLOCK_START_RE.match(first) or second.lstrip(" ").startswith(">"):
        return None
    return f"{first}\n{second}"


# An HTML comment (CommonMark 0.31): ``<!-->``, ``<!--->``, or ``<!--``, text
# not containing ``-->``, and ``-->``. The empty forms end at their own ``>``.
# This is the definition; to scan text for comments, use comment_spans or
# strip_comments below: with many unclosed ``<!--`` the regex is quadratic,
# as each opener's ``.*?-->`` scans to the end of the text before it fails (#94).
HTML_COMMENT_RE = re.compile(r"<!--(?:-?>|.*?-->)", re.DOTALL)


def find_all(text: str, needle: str, *, overlapping: bool = False) -> list[int]:
    """The offsets of *needle* in *text*, in order: those of ``str.find``
    called again after each, from the end of the match, or with *overlapping*
    from the next character. *needle* is not empty."""
    if not needle:
        raise ValueError("find_all needs a needle")
    step = 1 if overlapping else len(needle)
    found: list[int] = []
    at = text.find(needle)
    while at >= 0:
        found.append(at)
        at = text.find(needle, at + step)
    return found


def comment_closers(text: str) -> list[int]:
    """The offsets of each ``-->`` in *text*, in order (it cannot overlap itself)."""
    return find_all(text, "-->")


def comment_end(text: str, start: int, closers: list[int]) -> int | None:
    """Where the comment that ``HTML_COMMENT_RE`` matches at *start* ends, or
    None when it matches nothing there. *start* is at ``<!--``, and *closers*
    are ``comment_closers(text)``: the comment ends at the first one from its
    body on, found by bisect, not by a scan."""
    body = start + 4
    if text.startswith(">", body):
        return body + 1  # ``<!-->``
    if text.startswith("->", body):
        return body + 2  # ``<!--->``
    nearest = bisect_left(closers, body)
    return closers[nearest] + 3 if nearest < len(closers) else None  # None: unclosed, literal text


def comment_spans(text: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each comment in *text*, as
    ``HTML_COMMENT_RE.finditer`` finds them, in linear time."""
    spans: list[tuple[int, int]] = []
    closers = comment_closers(text)
    pos = 0
    while (start := text.find("<!--", pos)) >= 0:
        end = comment_end(text, start, closers)
        if end is None:
            pos = start + 4
        else:
            spans.append((start, end))
            pos = end
    return spans


def strip_comments(text: str, *, blank: bool = False) -> str:
    """*text* without its comments: ``HTML_COMMENT_RE.sub("", text)``. With
    *blank*, each comment is replaced by as many spaces, so that offsets are
    kept."""
    spans = comment_spans(text)
    if not spans:
        return text
    kept: list[str] = []
    pos = 0
    for start, end in spans:
        kept.append(text[pos:start])
        if blank:
            kept.append(" " * (end - start))
        pos = end
    kept.append(text[pos:])
    return "".join(kept)


# Inline raw HTML other than a comment (CommonMark 0.31.2 §6.6): an open tag,
# whose ``/`` is directly before its ``>``; a closing tag, which takes no
# attributes; a processing instruction; a declaration, ``<!`` and an ASCII
# letter, then anything up to the next ``>`` (before 0.30 it was capital
# letters and whitespace); a CDATA section.
_INLINE_ATTRIBUTE = r"""(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s"'=<>`]+|'[^']*'|"[^"]*"))?)"""
INLINE_HTML_RE = re.compile(
    r"<[A-Za-z][A-Za-z0-9-]*" + _INLINE_ATTRIBUTE + r"*\s*/?>"
    r"|</[A-Za-z][A-Za-z0-9-]*\s*>"
    r"|<\?.*?\?>"
    r"|<![A-Za-z][^>]*>"
    r"|<!\[CDATA\[.*?\]\]>",
    re.DOTALL,
)

# What INLINE_HTML_RE reads on to a closer: for each opener, the closer, and the offset from the opener at which
# the closer may start (a declaration needs its letter first). With no closer the pattern reads to the end of the
# text, so it must not be tried at every opener. The CDATA section is before the declaration, as it also starts ``<!``.
_INLINE_HTML_CLOSERS = (("<?", "?>", 2), ("<![CDATA[", "]]>", 9), ("<!", ">", 3))


def inline_html_unclosed(view: str) -> Callable[[int], bool]:
    """A function of an offset in *view* that is True when a processing instruction, CDATA section or declaration
    starting there has no closer after it, so INLINE_HTML_RE cannot match there and need not be tried. The last
    closer of each kind is found once, so asking costs nothing that grows with the text."""
    lasts = [(opener, view.rfind(closer), first) for opener, closer, first in _INLINE_HTML_CLOSERS]

    def unclosed(at: int) -> bool:
        for opener, last, first in lasts:
            if view.startswith(opener, at):
                return last < at + first
        return False

    return unclosed


def is_comment_only(html: str) -> bool:
    """True if raw HTML block text *html* holds nothing but comments (§8.6)
    and whitespace: an unclosed ``<!--`` runs to its end."""
    rest = strip_comments(html)
    opened = rest.find("<!--")
    return not (rest[:opened] if opened >= 0 else rest).strip()

# A line ending (CommonMark): LF, CR, or CRLF. Other characters that
# str.splitlines() breaks at — form feed, U+2028, … — are characters.
LINE_ENDING_RE = re.compile(r"\r\n|\r|\n")

TAB_STOP = 4  # a tab advances to the next multiple of four columns


def is_blank(line: str) -> bool:
    """True if *line* is blank: nothing but spaces and tabs (CommonMark).
    Other whitespace, such as a no-break space, is text."""
    return not line.strip(" \t")


def strip_text(text: str) -> str:
    """*text* without the spaces, tabs and line endings at its ends — the
    whitespace CommonMark strips from a paragraph. Other whitespace, such as
    a no-break space, is text: ``\u00a0- item`` is a paragraph, and stays
    one when written back."""
    return text.strip(" \t\r\n")


def paragraph_text(lines: list[str]) -> str:
    """The text of a paragraph of *lines* (CommonMark paragraph content):
    each line without its leading spaces and tabs, joined with LF, and the
    whole ``strip_text``-ed. A line's trailing whitespace is kept but for the
    last line's: before a line break it may be a hard break (two spaces or
    more), as may a backslash ending a line."""
    return strip_text("\n".join(line.lstrip(" \t") for line in lines))


# ── Link reference definitions (CommonMark 0.31 §4.7, #92) ────────

#: The longest label, in characters between the brackets (§4.7).
MAX_LABEL_LENGTH = 999
#: How many parentheses a bare destination may nest (cmark's limit), which also
#: keeps a long run of them linear.
MAX_DESTINATION_PARENTHESES = 32
_PUNCTUATION = frozenset(string.punctuation)
_BLANK_LINES_RE = re.compile(r"[ \t]*\n(?:[ \t]*\n)+[ \t]*")
_WHITESPACE_RUN_RE = re.compile(r"[ \t\n\r]+")
#: What a label holds up to its ``]``: characters but a bracket or a backslash, and a backslash
#: with the punctuation it escapes (or alone).
_LABEL_TEXT_RE = re.compile(r"(?:[^\[\]\\]|\\[!-/:-@\[-`{-~]|\\)*")


class LinkDefinitionSource(NamedTuple):
    """A link reference definition read from a paragraph's lines
    (``split_link_definitions``): its *label* (as written, without the
    spaces around it), *target* (raw: without ``<>``, backslash escapes and
    character references not decoded) and *title* (raw, without its
    delimiters), and how many *lines* it took."""

    label: str
    target: str
    title: str
    lines: int


def collapse_label_whitespace(label: str) -> str:
    """*label* without the spaces, tabs and line breaks at its ends, each run of them
    one space (CommonMark's whitespace: other, a no-break space for one, is text)."""
    return _WHITESPACE_RUN_RE.sub(" ", label.strip(" \t\n\r"))


def normalize_label(label: str) -> str:
    """The key a link label is matched by (§4.7): without the spaces and line
    breaks at its ends, each run of them one space, case-folded. ``[Foo  Bar]``
    and ``[foo bar]`` are one label."""
    return collapse_label_whitespace(label).casefold()


def no_blank_lines(text: str) -> str:
    """*text* with each blank line (and the line breaks around it) a space:
    a blank line would end the paragraph that holds a label or a title
    (§4.7)."""
    return _BLANK_LINES_RE.sub(" ", LINE_ENDING_RE.sub("\n", text))


def escapes(text: str, at: int) -> bool:
    """True if the backslash at ``text[at]`` escapes the character after it:
    ASCII punctuation (§2.4)."""
    return text[at] == "\\" and at + 1 < len(text) and text[at + 1] in _PUNCTUATION


def _unescaped(text: str, characters: str) -> bool:
    """True if *text* holds one of *characters* that no backslash escapes."""
    at = 0
    while at < len(text):
        if escapes(text, at):
            at += 2
            continue
        if text[at] in characters:
            return True
        at += 1
    return False


def _complete(text: str) -> str:
    """*text*, but for a backslash that ends it and escapes nothing, doubled:
    before the delimiter that follows it, it would escape that."""
    at = 0
    while at < len(text):
        if escapes(text, at):
            at += 2
            continue
        if at == len(text) - 1 and text[at] == "\\":
            return text + "\\"
        at += 1
    return text


def _escape(text: str, characters: str) -> str:
    """*text* with a backslash before each of *characters* that none escapes."""
    pieces, at = [], 0
    while at < len(text):
        if escapes(text, at):
            pieces.append(text[at:at + 2])
            at += 2
        else:
            pieces.append("\\" + text[at] if text[at] in characters else text[at])
            at += 1
    return "".join(pieces)


def is_bare_destination(target: str) -> bool:
    """True if *target* can be written as a link destination without ``<>``
    (§6.3): not empty, with no space, control character or ``<`` first, and
    balanced parentheses."""
    if not target or target[0] == "<":
        return False
    found = scan_destination(target, 0, nonempty=True)
    return found is not None and found[2] == len(target)


def label_end(text: str, at: int) -> int | None:
    """The offset of the ``]`` that ends the link label opening at
    ``text[at]``, a ``[`` (§4.7); None when it holds an unescaped bracket or
    is longer than ``MAX_LABEL_LENGTH``. The label may be blank (``[]``, the
    collapsed form of a reference, §6.3): a definition's is not."""
    stop = min(len(text), at + MAX_LABEL_LENGTH + 2)  # reads no further than a label may be long
    end = _LABEL_TEXT_RE.match(text, at + 1, stop).end()
    return end if end < stop and text[end] == "]" else None


def skip_blanks(text: str, at: int) -> int:
    """*at* past spaces and tabs."""
    while at < len(text) and text[at] in " \t":
        at += 1
    return at


def skip_line_space(text: str, at: int) -> int:
    """*at* past spaces and tabs and at most one line break (§6.3: what may separate the parts of a
    link, "optional spaces, tabs, and up to one line ending")."""
    at = skip_blanks(text, at)
    if text[at:at + 1] == "\n":
        at = skip_blanks(text, at + 1)
    return at


def back_line_space(text: str, at: int) -> int:
    """The offset where the spaces and tabs and at most one line break that end at ``text[at]`` begin: the
    gap ``skip_line_space`` crosses, read backwards."""
    while at > 0 and text[at - 1] in " \t":
        at -= 1
    if text[at - 1:at] == "\n":
        at -= 1
        while at > 0 and text[at - 1] in " \t":
            at -= 1
    return at


def scan_destination(
    text: str, at: int, *, nonempty: bool = False, permissive: bool = False
) -> tuple[int, int, int] | None:
    """The link destination (§6.3) at ``text[at:]``: ``(start, stop, after)``, the destination being
    ``text[start:stop]`` and *after* the offset after it (past the ``>`` of a ``<…>`` one); None when there is
    none. ``<…>`` holds no ``<``, ``>`` or line break but escaped. A bare one stops at a space, a control
    character (DEL included, §2.1) or a ``)`` that closes no ``(`` of it; its parentheses must balance, to
    ``MAX_DESTINATION_PARENTHESES`` deep, which also keeps a run of them linear. A NUL is text
    like any other (§2.3 makes it U+FFFD), which lets a text with its directives masked as NULs be scanned.
    With *nonempty* a bare destination is not empty (a definition's is not; an inline link's may be). With
    *permissive* a bare destination ends only at a space, a tab or a line break, as pandoc reads it (it keeps a control
    character or DEL, which the specification and markdown-it end it at): for a rule that must cover what any
    renderer reads (``path-not-relative``), not for the model."""
    if text.startswith("<", at):
        stop = at + 1
        while stop < len(text) and text[stop] not in "<>\n":
            stop += 2 if escapes(text, stop) else 1
        return (at + 1, stop, stop + 1) if text.startswith(">", stop) else None
    depth, end = 0, at
    while end < len(text):
        char = text[end]
        if escapes(text, end):
            end += 2
            continue
        if (char in " \t\n") if permissive else ((char <= " " and char != "\0") or char == "\x7f"):
            break
        if char == "(":
            depth += 1
            if depth > MAX_DESTINATION_PARENTHESES:
                return None
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        end += 1
    return (at, end, end) if not depth and (end > at or not nonempty) else None


def scan_title(text: str, at: int) -> int | None:
    """The offset after the link title opening at ``text[at]``: in ``"``,
    ``'`` or ``()``, backslash escapes honoured; None when it is not closed
    (a ``(`` title holds no unescaped ``(``). It reads no further than the next
    closer of its kind, so the scans of a text that has none are linear."""
    closer = ")" if text[at] == "(" else text[at]
    end = at + 1
    while end < len(text):
        if escapes(text, end):
            end += 2
            continue
        if text[end] == closer:
            return end + 1
        if closer == ")" and text[end] == "(":
            return None
        end += 1
    return None


def scan_inline_link(text: str, opening: int, *, permissive: bool = False) -> tuple[int, int, int] | None:
    """The inline link whose ``(`` is ``text[opening]`` (§6.3): ``(start, stop, after)``, the destination
    (``scan_destination``, which may be empty) being ``text[start:stop]`` and *after* the offset after the ``)``;
    None when what follows is no inline link. An optional title is told apart from the destination by the
    space before it, and the parts are separated by spaces, tabs and up to one line break (``skip_line_space``),
    so ``<b>"t"`` is no link, and neither is an empty destination with a blank line after it. With *permissive*
    (``scan_destination``) a bare destination holds a control character or DEL, and a title may follow the ``>``
    of a ``<…>`` directly, as pandoc reads both."""
    at = skip_line_space(text, opening + 1)
    found = scan_destination(text, at, permissive=permissive)
    if found is None:
        return None
    start, stop, after = found
    gap = after if after == stop == start else skip_line_space(text, after)  # (an empty one has had its gap)
    if (gap > after or permissive) and text[gap:gap + 1] in ('"', "'", "("):
        closing = scan_title(text, gap)
        if closing is None:
            return None
        gap = skip_line_space(text, closing)
    return (start, stop, gap + 1) if text.startswith(")", gap) else None


def _read_definition(text: str, at: int) -> tuple[str, str, str, int] | None:
    """The link reference definition that opens at ``text[at]`` (§4.7), as
    cmark reads it: ``(label, target, title, end)``, *end* past its last
    line's ending; None when it is none."""
    close = label_end(text, at)
    if close is None or text[close + 1:close + 2] != ":":
        return None
    label = text[at + 1:close].strip(" \t\n")
    if not label:
        return None
    found = scan_destination(text, skip_line_space(text, close + 2), nonempty=True)
    if found is None:
        return None
    start, stop, after = found
    target = text[start:stop]
    # The title is told apart from the destination by the space before it, and
    # from the paragraph after it by ending its line: else it is dropped, and
    # the definition ends with the destination's line, if that ends there.
    opening = skip_line_space(text, after)
    if opening > after and text[opening:opening + 1] in ('"', "'", "("):
        closing = scan_title(text, opening)
        if closing is not None:
            line_end = skip_blanks(text, closing)
            if line_end == len(text) or text[line_end] == "\n":
                return label, target, text[opening + 1:closing - 1], min(line_end + 1, len(text))
    line_end = skip_blanks(text, after)
    if line_end == len(text) or text[line_end] == "\n":
        return label, target, "", min(line_end + 1, len(text))
    return None


def split_link_definitions(lines: list[str]) -> tuple[list[LinkDefinitionSource], int]:
    """The link reference definitions (§4.7) a paragraph of *lines* opens
    with, and how many lines they took: ``([(label, target, title, lines)],
    count)``. They are read from the closed paragraph, as CommonMark does,
    so a definition cannot interrupt a paragraph (``a`` then ``[x]: u`` is
    one) and lazy lines are already part of it. A definition holding ``{{``
    ends the run and stays paragraph text: its destination or title is not
    final until the directive is filled in (#92)."""
    text = "\n".join(line.lstrip(" \t") for line in lines)
    found: list[LinkDefinitionSource] = []
    at = taken = 0
    while text.startswith("[", at):
        read = _read_definition(text, at)
        if read is None or "{{" in text[at:read[3]]:
            break
        label, target, title, end = read
        # The lines it took: its line endings, and its last line if the text ends there. (Counted
        # from where it begins, not from the start: a paragraph of many definitions is linear.)
        size = text.count("\n", at, end) + (1 if end == len(text) and text[end - 1] != "\n" else 0)
        found.append(LinkDefinitionSource(label, target, title, size))
        at, taken = end, taken + size
    return found, taken


def format_link_definition(label: str, target: str, title: str = "") -> str:
    """A link reference definition as source (§4.7): ``[label]: target
    "title"``. The target is wrapped in ``<>`` unless it is a bare
    destination (``is_bare_destination``) that holds no NUL; the title takes the first of
    ``"``, ``'`` and ``()`` that it holds no unescaped one of, else its
    ``"`` are escaped. A backslash that would escape the closing bracket or
    delimiter is doubled, an unescaped bracket in the label is escaped, and a
    blank line in the label or title is a space: only a definition built in
    code holds any of them, as ``split_link_definitions`` reads none. Line
    breaks in the label and the title are kept, for the caller to write as
    the lines of a paragraph."""
    label = _escape(_complete(no_blank_lines(label).strip(" \t\n")), "[]")
    target = LINE_ENDING_RE.sub("", target).strip(" \t")
    if "\0" in target or not is_bare_destination(target):  # (a NUL is written in ``<>`` as it always was)
        target = "<" + _escape(_complete(target), "<>") + ">"
    written = f"[{label}]: {target}"
    title = _complete(no_blank_lines(title))
    if not title:
        return written
    if not _unescaped(title, '"'):
        return f'{written} "{title}"'
    if not _unescaped(title, "'"):
        return f"{written} '{title}'"
    if not _unescaped(title, "()"):
        return f"{written} ({title})"
    return f'{written} "{_escape(title, chr(34))}"'


def indent_width(line: str) -> int:
    """Columns of leading whitespace, a tab counting to the next tab stop."""
    return advance_column(0, line[: len(line) - len(line.lstrip(" \t"))])


# A list item's marker (CommonMark): ``-``, ``*``, or ``+`` for an unordered
# list, a number of at most nine ASCII digits and ``.`` or ``)`` for an
# ordered one, with only spaces and tabs around it: its blanks before, the
# marker, and the blanks after it, if any. The parser's ``LIST_ITEM_RE`` is
# this anchored at the start of a line; here it matches at an offset in a long
# line (``item_marker_at``). The runs of blanks are possessive, as what
# follows one is never a blank.
LIST_ITEM_PATTERN = (
    r"(?P<lead>[ \t]*+)(?P<marker>(?P<number>[0-9]{1,9})(?P<delimiter>[.)])|(?P<bullet>[-*+]))"
    r"(?:(?P<gap>[ \t]+)|$)"
)
_MARKER_AT_RE = re.compile(LIST_ITEM_PATTERN)


def advance_column(column: int, text: str) -> int:
    """The column reached from *column* over *text*: a tab counts to the next
    tab stop, any other character one column. The one place tab stops are
    counted."""
    if "\t" not in text:
        return column + len(text)
    for char in text:
        column += TAB_STOP - column % TAB_STOP if char == "\t" else 1
    return column


class ItemMarker(NamedTuple):
    """A list item marker found in a text (``item_marker_at``) with where its
    content starts, in the text and in columns."""

    start: int  #: the index where it was looked for: its blanks before it begin there
    start_column: int  #: and its column
    lead: int  #: blanks before the marker, in characters
    indent: int  #: and in columns
    gap: int  #: columns of spacing after the marker
    end: int  #: the index just past the marker and its spacing
    end_column: int  #: its column
    cut: int  #: the index where the content starts: past all the spacing of one to four columns, past one column of more
    column: int  #: the column of that
    blank: bool  #: nothing follows the marker and its spacing: an empty item
    remainder: int  #: columns of a tab that ``cut`` passes, which the content starts inside of
    number: str | None  #: the digits of an ordered item's number


def item_marker_at(text: str, position: int = 0, column: int = 0) -> ItemMarker | None:
    """The list item marker that *text* has at *position*, a text whose
    character there is at *column*, or None. The columns are the ones the line
    has: a tab counts to the next tab stop from column 0 (CommonMark), so a
    text cut out of a line says where it was cut. Nothing but the marker and
    its spacing is read, so that a line of markers is walked, not copied, one
    marker at a time (#94). The content starts after one to four columns of
    spacing, or one column when there are more, or when the item is empty."""
    found = _MARKER_AT_RE.match(text, position)
    if found is None:
        return None
    lead_end, marker_end, gap_start, end = found.end("lead"), found.end("marker"), found.start("gap"), found.end()
    number = found.group("number")
    marker_column = advance_column(column, text[position:lead_end])
    after = marker_column + marker_end - lead_end
    end_column = advance_column(after, text[marker_end:end])
    gap = end_column - after
    indent = marker_column - column
    lead = lead_end - position
    if end == len(text) or (end == len(text) - 1 and text[end] == "\n"):  # an empty item: only a line ending is left
        return ItemMarker(position, column, lead, indent, gap, end, end_column, end, after + 1, True, 0, number)
    if gap > 4:
        tab = TAB_STOP - after % TAB_STOP if text[gap_start] == "\t" else 1
        return ItemMarker(position, column, lead, indent, gap, end, end_column, gap_start + 1, after + 1, False, tab - 1, number)
    return ItemMarker(position, column, lead, indent, gap, end, end_column, end, after + gap, False, 0, number)


class ItemMarkers:
    """The list item markers that a text opens with, one after the other
    (``- 1. a``), each with the columns it really has: the one walk of them,
    to which each reading of a line of markers applies its own stop. It goes on
    from the end of a marker and its spacing; *between*, an expression, is
    what to skip before each (block quote markers). ``position`` and
    ``column`` are where the walk is: after what it skipped, at the start of
    the next marker or of what is not one."""

    __slots__ = ("text", "position", "column", "_between")

    def __init__(self, text: str, position: int = 0, column: int = 0, between: re.Pattern[str] | None = None) -> None:
        self.text, self.position, self.column, self._between = text, position, column, between

    def __iter__(self) -> ItemMarkers:
        return self

    def __next__(self) -> ItemMarker:
        text, position = self.text, self.position
        if self._between is not None:
            skipped = self._between.match(text, position).end()  # type: ignore[union-attr]
            self.column = advance_column(self.column, text[position:skipped])
            self.position = position = skipped
        marker = item_marker_at(text, position, self.column)
        if marker is None:
            raise StopIteration
        self.position, self.column = marker.end, marker.end_column
        return marker


def item_content_column(line: str) -> int:
    """The column where the content of the list item beginning on *line*
    starts (CommonMark): after its marker and one to four columns of spacing,
    or one column when there are more, or when the item is empty."""
    found = item_marker_at(line)
    return found.column if found is not None else indent_width(line) + 2  # never: *line* is an item


class NestedContent(NamedTuple):
    """Where the innermost content of items nested on a line starts
    (``nested_content``)."""

    columns: int  #: further in than the content of the first item, in columns
    characters: int  #: and in characters of the text, which differ where a tab is spacing
    end: int  #: how far the walk read: the end of the last marker it counted
    levels: tuple[int, ...]  #: the columns the content of each item counted is further in, the last being ``columns``


def nested_content(first: str, column: int = 0, is_rule: Callable[[int], bool] | None = None) -> NestedContent:
    """How much further in than a list item's content its innermost content
    starts, when its text *first* opens with items nested on its line (``1. y``
    in ``- 1. y``): its later content is measured from there (CommonMark).
    *column*: where *first* starts in its line. *is_rule*: whether *first* from
    a position on is a thematic break, which holds no item (``1. * * *``). The
    last item counted is one with more than four columns of spacing: its
    content is indented code, which nests no item."""
    columns = characters = end = 0
    levels: list[int] = []
    for found in ItemMarkers(first, 0, column):
        after = first[found.end:found.end + 1]
        if found.lead or not found.gap or not after or after.isspace() or (is_rule and is_rule(found.start)):
            break  # an item nested on the line has spacing and text, and is no thematic break
        columns += found.column - found.start_column
        characters += found.cut - found.start
        end = found.end
        levels.append(columns)
        if found.gap > 4:
            break
    return NestedContent(columns, characters, end, tuple(levels))


def dedent(line: str, columns: int, *, expand: bool = False) -> str:
    """*line* with up to *columns* columns of leading whitespace removed; a
    tab straddling the cut leaves its remaining columns as spaces. Only
    leading whitespace changes. *expand*: the rest of the leading
    whitespace is written as spaces too, each tab as wide as it is where it
    stands — a list item's later lines, whose tabs would otherwise measure
    differently once the item's marker is written another width."""
    column = index = 0
    while index < len(line) and line[index] in " \t" and column < columns:
        column = advance_column(column, line[index])
        index += 1
    rest = line[index:]
    lead = len(rest) - len(rest.lstrip(" \t"))
    if expand and "\t" in rest[:lead]:
        return " " * (advance_column(column, rest[:lead]) - columns) + rest[lead:]
    return " " * max(column - columns, 0) + rest


def indented_code_end(lines: list[str], index: int) -> int:
    """Index just past the indented code block starting at ``lines[index]``
    (CommonMark): its lines are blank or indented four or more columns, and
    it ends before its trailing blank lines."""
    end = last = index + 1
    while end < len(lines) and (is_blank(lines[end]) or indent_width(lines[end]) >= 4):
        end += 1
        if not is_blank(lines[end - 1]):
            last = end
    return last


def is_indented_code(text: str) -> bool:
    """True if every non-blank line of *text* is indented four or more
    columns, as an indented code block's are (CommonMark)."""
    lines = [line for line in text.split("\n") if not is_blank(line)]
    return bool(lines) and all(indent_width(line) >= 4 for line in lines)


def closes_fence(line: str, fence: str) -> bool:
    """True if *line* closes a code block opened with *fence*: at most three
    columns of indentation, then the same character at least as many times,
    and nothing else."""
    stripped = line.strip(" \t")
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


class CodeContent(NamedTuple):
    """What a ``code`` block holds, read as CommonMark reads it
    (``code_content``)."""

    #: A fenced block's info string, without the spaces around it; ``""``
    #: for an indented block, or a fence with none.
    info: str
    #: The code itself, each line ending with LF; ``""`` for no lines.
    text: str
    #: True for a fenced block, false for an indented one.
    fenced: bool


def code_content(block: Block) -> CodeContent:
    """The content of the ``code`` block *block*, as CommonMark reads it
    (§11.4): of a fenced block, its info string and the lines between its
    fences, the closing fence left out when it has one (``closes_fence``),
    each line without as much indentation as the opening fence had
    (``dedent``); of an indented block, its lines without their four columns
    of indentation. Raises ``ValueError`` for a block that is not code."""
    if block.kind != "code":
        raise ValueError(f"not a code block: {block.kind}")
    lines = block.text.split("\n") if block.text else []
    opening = FENCE_OPEN_RE.match(lines[0]) if lines else None
    if opening is None:
        return CodeContent("", "".join(dedent(line, 4) + "\n" for line in lines), False)
    body = lines[1:]
    if body and closes_fence(body[-1], opening.group("fence")):
        body = body[:-1]
    indent = indent_width(lines[0])
    return CodeContent(
        lines[0][opening.end():].strip(), "".join(dedent(line, indent) + "\n" for line in body), True
    )
