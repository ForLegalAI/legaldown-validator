"""Differential tests for the one scanner of link destinations and titles (#113).

``markdown.scan_destination``, ``scan_title`` and ``scan_inline_link`` replaced four readers of the same
syntax: the definitions parser (``markdown._read_definition``), the image paths (``paths._destination``),
the raw-HTML check (``core._LINK_DESTINATION_RE``) and, for its safety rule only in spirit, the insertion
boundary. Each is compared with a verbatim copy of what it replaced (the names have an ``old`` prefix; nothing
else is changed) on random token strings and on every ``whitespace destination whitespace title whitespace``
product, all from fixed seeds.

The new scanner differs from the old readers in a few classes, each right by the specification, and
markdown-it (CONFORMANCE.md). No class exempts its inputs: every one is a *value-level* oracle, the new
result being compared with the old code run on a transformed input (``new(s) == old(substituted(s))``, the
substitution being length-preserving), or a pair (old value, new value) that a predicate on the
input pins exactly. Every input is checked by one of them, and each class must occur, so that the
list stays exact:

* definitions: ``nul`` (a NUL is text, §2.3; the old reader ended a bare destination at it);
* image paths: ``del-in-bare`` (DEL is a control character, §2.1, and ends a bare destination),
  ``glued-title`` (a title directly after ``<…>`` is no title, so no link), ``extra-line-break``
  (a gap holds up to one line break);
* raw HTML: ``glued-title``, ``line-break-around`` (a line break may precede ``<`` and ``)``),
  ``escaped-delimiter`` (a backslash escapes a ``>``, ``"`` or ``)`` in a destination or a title);
* the insertion boundary has none: ``_InsertionScan._reference_end`` is the old pattern, at every offset.
"""
from __future__ import annotations

import itertools
import random
import re
import shutil
import string
import subprocess
from collections import Counter

import pytest

from legaldown import markdown
from legaldown.directives import Lexed, is_escaped, lex, mask_directives
from legaldown.markdown import INLINE_HTML_RE, LinkDefinitionSource, inline_html_unclosed, label_end
from legaldown.validator import core, paths, templates

# ── The code that was replaced (#113), verbatim ───────────────────

MAX_DESTINATION_PARENTHESES = 32
_PUNCTUATION = frozenset(string.punctuation)
_OLD_MAX_PARENTHESES = 32

# markdown.py: the definitions parser

def _old_md_escapes(text: str, at: int) -> bool:
    """True if the backslash at ``text[at]`` escapes the character after it:
    ASCII punctuation (§2.4)."""
    return text[at] == "\\" and at + 1 < len(text) and text[at + 1] in _PUNCTUATION


def _old_bare_end(text: str, at: int) -> int | None:
    """Where the bare link destination (§6.3) at ``text[at:]`` ends: it stops
    at a space or a control character, or at a ``)`` that closes no ``(`` of
    it. None for one that is empty, whose parentheses are not balanced, or
    that opens with ``<``."""
    depth, end = 0, at
    while end < len(text):
        char = text[end]
        if _old_md_escapes(text, end):
            end += 2
            continue
        if char <= " " or char == "\x7f":
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
    return end if end > at and not depth and text[at] != "<" else None


def _old_title_end(text: str, at: int) -> int | None:
    """The offset after the link title opening at ``text[at]``: in ``"``,
    ``'`` or ``()``, backslash escapes honoured; None when it is not closed
    (a ``(`` title holds no ``(``)."""
    closer = ")" if text[at] == "(" else text[at]
    end = at + 1
    while end < len(text):
        if _old_md_escapes(text, end):
            end += 2
            continue
        if text[end] == closer:
            return end + 1
        if closer == ")" and text[end] == "(":
            return None
        end += 1
    return None


def _old_skip_blanks(text: str, at: int) -> int:
    while at < len(text) and text[at] in " \t":
        at += 1
    return at


def _old_skip_to_next_line(text: str, at: int) -> int:
    """*at* past spaces and tabs and at most one line break."""
    at = _old_skip_blanks(text, at)
    if text[at:at + 1] == "\n":
        at = _old_skip_blanks(text, at + 1)
    return at


def _old_read_definition(text: str, at: int) -> tuple[str, str, str, int] | None:
    """The link reference definition that opens at ``text[at]`` (§4.7), as
    cmark reads it: ``(label, target, title, end)``, *end* past its last
    line's ending; None when it is none."""
    close = label_end(text, at)
    if close is None or text[close + 1:close + 2] != ":":
        return None
    label = text[at + 1:close].strip(" \t\n")
    if not label:
        return None
    start = _old_skip_to_next_line(text, close + 2)
    if text.startswith("<", start):
        stop = start + 1
        while stop < len(text) and text[stop] not in ">\n<":
            stop += 2 if _old_md_escapes(text, stop) else 1
        if text[stop:stop + 1] != ">":
            return None
        target, after = text[start + 1:stop], stop + 1
    else:
        stop = _old_bare_end(text, start)
        if stop is None:
            return None
        target, after = text[start:stop], stop
    # The title is told apart from the destination by the space before it, and
    # from the paragraph after it by ending its line: else it is dropped, and
    # the definition ends with the destination's line, if that ends there.
    opening = _old_skip_to_next_line(text, after)
    if opening > after and text[opening:opening + 1] in ('"', "'", "("):
        closing = _old_title_end(text, opening)
        if closing is not None:
            line_end = _old_skip_blanks(text, closing)
            if line_end == len(text) or text[line_end] == "\n":
                return label, target, text[opening + 1:closing - 1], min(line_end + 1, len(text))
    line_end = _old_skip_blanks(text, after)
    if line_end == len(text) or text[line_end] == "\n":
        return label, target, "", min(line_end + 1, len(text))
    return None


def old_split_link_definitions(lines: list[str]) -> tuple[list[LinkDefinitionSource], int]:
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
        read = _old_read_definition(text, at)
        if read is None or "{{" in text[at:read[3]]:
            break
        label, target, title, end = read
        # The lines it took: its line endings, and its last line if the text ends there. (Counted
        # from where it begins, not from the start: a paragraph of many definitions is linear.)
        size = text.count("\n", at, end) + (1 if end == len(text) and text[end - 1] != "\n" else 0)
        found.append(LinkDefinitionSource(label, target, title, size))
        at, taken = end, taken + size
    return found, taken


# paths.py: the inline image destination

def _old_paths_escapes(raw: str, at: int) -> bool:
    """True if a backslash at ``raw[at]`` escapes the character after it: ASCII punctuation."""
    return raw[at] == "\\" and raw[at + 1:at + 2] != "" and raw[at + 1] in string.punctuation


def _old_skip_space(raw: str, at: int) -> int:
    while at < len(raw) and raw[at] in " \t\n":
        at += 1
    return at


def old_destination(raw: str, opening: int) -> tuple[int, int, int] | None:
    """The inline link destination that follows the ``(`` at ``raw[opening]``
    (CommonMark): ``<…>``, or a raw one with balanced parentheses, then an
    optional title and the ``)``. ``(start, stop, after)``: the destination is
    ``raw[start:stop]`` and ``after`` is the offset after the ``)``; None when
    what follows is no inline link. A directive in the destination
    (``mask_directives`` fills it with NUL) is part of it."""
    size = len(raw)
    at = _old_skip_space(raw, opening + 1)
    if raw.startswith("<", at):
        end = at + 1
        while end < size and raw[end] not in "<>\n":
            end += 2 if _old_paths_escapes(raw, end) else 1
        if raw[end:end + 1] != ">":
            return None
        start, stop, at = at + 1, end, end + 1
    else:
        depth = 0
        end = at
        while end < size:
            char = raw[end]
            if _old_paths_escapes(raw, end):
                end += 2
                continue
            if char == " " or (ord(char) < 32 and char != "\0"):
                break
            if char == "(":
                depth += 1
                if depth > _OLD_MAX_PARENTHESES:
                    return None
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        if depth:
            return None
        start, stop, at = at, end, end
    at = _old_skip_space(raw, at)
    if raw[at:at + 1] in ('"', "'", "("):
        closer = ")" if raw[at] == "(" else raw[at]
        end = at + 1
        while end < size and raw[end] != closer:
            if _old_paths_escapes(raw, end):
                end += 2
                continue
            if closer == ")" and raw[end] == "(":
                return None  # a title in parentheses holds none: this scan ends at the next ``(``, not at the end
            end += 1
        if end >= size:
            return None
        at = _old_skip_space(raw, end + 1)
    return (start, stop, at + 1) if raw[at:at + 1] == ")" else None


# validator/core.py: a link's pointy-bracket destination, and the check that used it

_OLD_LINK_DESTINATION_RE = re.compile(r"""<[^<>\n]*>[ \t]*(?:(?:"[^"]*"|'[^']*'|\([^()]*\))[ \t]*)?\)""")


def old_inline_html(text: str, lexed: Lexed) -> str | None:
    """The first inline raw HTML in *text* other than a comment (§8.7), as
    written, or None: outside code spans, comments and directives (``lex``),
    not after a backslash, and not a link's ``<…>`` destination."""
    # What the lexer blanked, a comment or a code span, is filled with a
    # character no tag holds outside a quoted value: no tag runs across it, and
    # neither does a declaration, processing instruction or CDATA section. (In
    # CommonMark the HTML that starts first wins, so ``<!a `>` b`` is a
    # declaration, ending in the code span, and so is ``<!a <!-- c --> b``, ending
    # in the comment's ``>``; those are not found here: CONFORMANCE.md, #111.)
    view = "".join(
        "`" if seen == " " and written not in " \t\n" else seen
        for seen, written in zip(mask_directives(lexed.view, lexed.directives), text, strict=True)
    )
    # A processing instruction, declaration or CDATA section is read to its closer, which takes the rest of
    # the text to look for when there is none: once there is no closer after an offset, none is searched
    # for there again, and every ``<`` costs what it reads.
    unclosed = inline_html_unclosed(view)
    opened = -1  # the last unescaped ``[`` before *at* with no ``]`` after it but a link's
    scanned = 0
    pos = 0
    while (at := view.find("<", pos)) >= 0:
        pos = at + 1
        if is_escaped(view, at):
            continue
        for k in range(scanned, at):
            if view[k] == "[" and not is_escaped(view, k):
                opened = k
            elif view[k] == "]" and view[k + 1:k + 2] != "(":
                opened = -1
        scanned = at
        before = at
        while before > 0 and view[before - 1] in " \t":
            before -= 1
        if (
            opened >= 0 and view[before - 2:before] == "]("
            and (destination := _OLD_LINK_DESTINATION_RE.match(view, at))
        ):
            pos = scanned = destination.end()
            opened = -1
            continue
        if not unclosed(at) and (tag := INLINE_HTML_RE.match(view, at)):
            return text[at:tag.end()]
    return None


# validator/templates.py: the link reference definition of the insertion boundary (also in test_linear_oracles)

_OLD_LINK_REFERENCE_RE = re.compile(
    r" {0,3}\[[^\]]+\]:[ \t]*\S*(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?"
)


# ── Random inputs ─────────────────────────────────────────────────

#: Spaces, tabs and line breaks around the parts of a link: none, one line break, and two (which are not skipped).
_GAPS = ["", " ", "\t", "\n", " \n ", "\n\n", "\n \n", "\n\t\n"]
_SHORT_GAPS = ["", " ", "\n", "\n\n"]
#: What stands for a destination: bare and angle ones, with parentheses, escapes, a DEL, a control character,
#: a NUL, and what is none.
_DESTINATIONS = [
    "b", "<b>", "<>", "", "b(c)", "<b c>", "b\x7f", "<b\x7f>", "b\0c", "<b\0>", "\0", "<b", "(", "b)", "b\\)",
    "<b\\>c>", "<b\\", "\x01", "<b>>", "b\\", "((", "<b\n>",
]
_TITLES = [
    "", '"t"', "'t'", "(t)", '"t', '"t\\"u"', "(t(u)", "(t\\)u)", '"t\x7f"', '"t\0"', "(t)u", "'t\\'", '"t\nu"',
    '"t\\\\"', "(t\\(u)",
]
#: The tokens random inputs are made of (a link's parts, and what could end or extend them).
_TOKENS = [
    "<b>", "<", ">", "(", ")", "\\", '"', "'", " ", "\t", "\n", "\x7f", "\0", "\\)", '\\"', "\\>", '"t"', "'x'",
    "(t)", "b", "a", "\x01", "]", "[", ":", "](", "\\\\", "\r", "\x1f", "\x0b",
]


def _random_texts(tokens, count, max_tokens, seed):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(tokens) for _ in range(rng.randint(0, max_tokens)))


def _products(first, second, third, destinations=_DESTINATIONS, titles=_TITLES):
    """Every ``first destination second title third``."""
    for gaps in itertools.product(first, destinations, second, titles, third):
        yield "".join(gaps)


# ── The definitions parser ────────────────────────────────────────


def _as_text(read):
    """A definition read, with the NULs of its strings made the ``a`` they are read as by the old code."""
    if read is None:
        return None
    if isinstance(read, tuple) and len(read) == 4 and isinstance(read[3], int):
        return tuple(part.replace("\0", "a") if isinstance(part, str) else part for part in read)
    found, taken = read
    return [LinkDefinitionSource(*(part.replace("\0", "a") if isinstance(part, str) else part for part in each))
            for each in found], taken


def _definition_inputs():
    yield from (f"[a]:{text}" for text in _products(_GAPS, _GAPS, ["", " ", "\n", "\n\n", " x", "\n[b]: v", "\n[b]: v\n"]))
    yield from (f"[a]: {text}" for text in _random_texts(_TOKENS, 40_000, 9, seed=113))
    yield from _random_texts(["[a]:", "[", "]", ":", " ", "\n", "u", "<u>", '"t"', "\0", "\x7f", "(", ")", "\\", "\t"], 20_000, 9, seed=1130)


def test_a_definition_is_read_as_before_but_a_nul_is_text():
    """``nul``: the old reader ended a bare destination at a NUL. Read as the ``a`` it is the same as the old,
    for every input; where the two differ the input holds a NUL."""
    seen = Counter()
    for text in _definition_inputs():
        new, old = markdown._read_definition(text, 0), _old_read_definition(text, 0)
        assert _as_text(new) == _old_read_definition(text.replace("\0", "a"), 0), repr(text)
        if new != old:
            assert "\0" in text, repr(text)
            seen["nul"] += 1
        lines = text.split("\n")
        split = markdown.split_link_definitions(lines)
        assert _as_text(split) == old_split_link_definitions([line.replace("\0", "a") for line in lines]), repr(text)
        if split != old_split_link_definitions(lines):
            assert "\0" in text, repr(text)
            seen["nul-split"] += 1
    assert seen["nul"] > 100 and seen["nul-split"] > 100, seen


def test_a_target_is_bare_as_before_but_a_nul_is_text():
    """``is_bare_destination`` (``format_link_definition`` writes a target that is not bare in ``<>``, and one with a NUL: below)."""
    seen = 0
    for target in _random_texts(_TOKENS, 40_000, 7, seed=1132):
        old = bool(target) and _old_bare_end(target, 0) == len(target)
        new = markdown.is_bare_destination(target)
        assert new == (bool(target.replace("\0", "a")) and _old_bare_end(target.replace("\0", "a"), 0) == len(target)), repr(target)
        if new != old:
            assert "\0" in target and new and not old, repr(target)
            seen += 1
    assert seen > 50


def test_a_target_is_written_in_angle_brackets_as_before():
    """``format_link_definition``: a bare target as before, and one with a NUL in ``<>`` though a NUL is text."""
    for target in _random_texts(_TOKENS, 20_000, 7, seed=1138):
        if target != target.strip(" \t") or "\n" in target or "\r" in target:
            continue  # (the target is written without its line breaks and its outer blanks)
        written = markdown.format_link_definition("a", target)
        assert written.startswith("[a]: ")
        bare = not written.startswith("[a]: <")
        assert bare == (bool(target) and _old_bare_end(target, 0) == len(target)), repr(target)
        if bare:
            assert written == f"[a]: {target}"
        if not any(char in target for char in "<>\\"):  # (those are escaped in ``<>``, and read back as they were written)
            read = markdown.split_link_definitions([written])[0]
            assert read == [LinkDefinitionSource("a", target, "", 1)], repr(target)


# ── The image paths ───────────────────────────────────────────────


class _Gaps:
    """The line breaks in the gaps an old destination was read through: the whitespace after the ``(``, after the
    destination and after the title, found from what ``old_destination`` returned, as the text it passed over
    between the destination and the ``)`` is ``gap title gap`` or one gap."""

    @staticmethod
    def of(text, found):
        start, stop, after = found
        angle = text[start - 1:start] == "<"
        first = text[1:start - 1 if angle else start]
        rest = text[stop + 1 if angle else stop:after - 1]
        if not rest.strip(" \t\n"):
            return [first, rest]
        return [first, rest[:len(rest) - len(rest.lstrip(" \t\n"))], rest[len(rest.rstrip(" \t\n")):]]


def _glued(text, found):
    """Whether the old destination is a ``<…>`` one with a title directly after its ``>``."""
    start, stop, _after = found
    return text[start - 1:start] == "<" and text[stop + 1:stop + 2] in ('"', "'", "(")


def _paths_inputs():
    yield from (f"({text})" for text in _products(_GAPS, _GAPS, _GAPS))
    yield from (f"({text}" for text in _random_texts(_TOKENS, 60_000, 9, seed=1131))


def test_the_strict_reading_of_an_inline_link_is_the_old_but_for_del_a_glued_title_and_two_line_breaks():
    """``scan_inline_link`` as the raw-HTML check reads a link (the paths read it permissively, below)."""
    seen = Counter()
    for text in _paths_inputs():
        new = markdown.scan_inline_link(text, 0)
        old = old_destination(text, 0)
        # ``del-in-bare``: DEL ends a bare destination like any control character (§2.1), so the old code
        # run on text where it is one reads what the new reads, but for the classes below.
        controlled = old_destination(text.replace("\x7f", "\x01"), 0)
        if "\x7f" in text and old != new:
            seen["del-in-bare"] += 1
        if controlled is not None and _glued(text.replace("\x7f", "\x01"), controlled):
            # ``glued-title``: a title directly after the ``>`` is no title (§6.3 separates them), so no link.
            assert new is None, repr(text)
            seen["glued-title"] += 1
        elif controlled is not None and any(gap.count("\n") >= 2 for gap in _Gaps.of(text.replace("\x7f", "\x01"), controlled)):
            # ``extra-line-break``: a gap holds up to one line break.
            assert new is None, repr(text)
            seen["extra-line-break"] += 1
        else:
            assert new == controlled, repr(text)
    assert {name: count > 50 for name, count in seen.items()} == {
        "del-in-bare": True, "glued-title": True, "extra-line-break": True
    }, seen


#: The control characters a bare destination may hold when it is read permissively (pandoc): DEL and the C0 ones
#: but NUL (text), tab and line break (which end it), read by the old code as the ``a`` they are made.
_CONTROLS = {code: "a" for code in [*range(1, 32), 127] if code not in (9, 10)}


def test_an_image_destination_is_read_as_before_but_across_no_blank_line_and_with_controls_as_text():
    """The path checks over-approximate: they read what any renderer reads as an image, so a DEL or other control
    character in a bare destination and a title glued to ``<…>`` (pandoc) are images (``permissive``). The old code
    ended a bare destination at a C0 control, so the one value-level substitution is that: ``new(s)`` is the old code's
    reading of ``s`` with its controls made ``a`` (``control-in-bare``). The other class is ``extra-line-break``:
    where the old code read across a blank line, which ends the paragraph for every renderer, the new reads none."""
    seen = Counter()
    for text in _paths_inputs():
        new = markdown.scan_inline_link(text, 0, permissive=True)
        plain = text.translate(_CONTROLS)
        old = old_destination(plain, 0)
        if old is not None and any(gap.count("\n") >= 2 for gap in _Gaps.of(plain, old)):
            assert new is None, repr(text)
            seen["extra-line-break"] += 1
        else:
            assert new == old, repr(text)
        if new != old_destination(text, 0):
            seen["del-in-bare" if "\x7f" in text else "c0-in-bare"] += 1
    assert {name: count > 50 for name, count in seen.items()} == {
        "extra-line-break": True, "del-in-bare": True, "c0-in-bare": True
    }, seen


def test_a_line_break_in_a_gap_is_read_as_a_space_by_the_old_code():
    """What the new accepts and the old does not in a gap is a line break: with it a space the old agrees."""
    for text in _products(_GAPS, _GAPS, _GAPS):
        text = f"({text})"
        new = markdown.scan_inline_link(text, 0)
        if new is not None:
            assert old_destination(text.replace("\n", " "), 0) is not None, repr(text)


@pytest.mark.parametrize(("text", "found"), [
    ('![a](<x.png> "t")', ["x.png"]),
    ('![a](<x.png>"t")', ["x.png"]),
    ("![a](<x.png>\n\"t\")", ["x.png"]),
    ("![a](\n<x.png>)", ["x.png"]),
    ("![a](\n\nx.png)", []),
    ("![a](x.png\n\n)", []),
    ("![a](x.png\x7f)", ["x.png\x7f"]),
    ("![a](x.png\x01)", ["x.png\x01"]),
    ("![a](/etc/passwd\x1f)", ["/etc/passwd\x1f"]),
    ("![a](x.png\ty)", []),
    ("![a](x\0y.png)", ["x\0y.png"]),
    ('![a](x.png "t\\"u")', ["x.png"]),
    ("![a](<x.png> (t\\)u))", ["x.png"]),
])
def test_images_by_the_scanner(text, found):
    assert [image.path for image in paths.images(text, lex(text))] == found


def test_a_definition_with_a_nul_in_its_destination():
    assert markdown.split_link_definitions(["[a]: u\0v", "[b]: <w\0>"])[0] == [
        LinkDefinitionSource("a", "u\0v", "", 1), LinkDefinitionSource("b", "w\0", "", 1)
    ]


# ── The raw-HTML check ────────────────────────────────────────────


def old_destination_end(view, at):
    """What ``_inline_html`` did before ``_link_destination_end``: where the link whose ``<…>`` destination opens
    at ``view[at]`` ends, if ``](`` precedes it, spaces and tabs between."""
    before = at
    while before > 0 and view[before - 1] in " \t":
        before -= 1
    if view[before - 2:before] == "](" and (destination := _OLD_LINK_DESTINATION_RE.match(view, at)):
        return destination.end()
    return None


def _plain_escapes(view, at):
    """*view*, but for each backslash escape from *at* on two plain characters: the old pattern knows no escape."""
    plain = list(view)
    index = at
    while index < len(view):
        if view[index] == "\\" and view[index + 1:index + 2] != "" and view[index + 1] in string.punctuation:
            plain[index] = plain[index + 1] = "x"
            index += 2
        else:
            index += 1
    return "".join(plain)


_VIEW_TOKENS = [
    "](", "[", "]", "<b>", "<", ">", "(", ")", "\\", '"', "'", " ", "\t", "\n", "\0", '\\"', "\\)", "\\>", '"t"',
    "'x'", "(t)", "a", "\\\\", "\r",
]
_LINK_DESTINATIONS = ["<b>", "<b c>", "<b\\>c>", "<b", "<b\\", "<>", "<b>>", "<b<c>"]
_LINK_TITLES = _TITLES + ['"t\\")"', "'t\\''", "(t\\))", '"t\\"']


def _view_inputs():
    yield from (f"[a]({text})" for text in _products(_GAPS, _GAPS, _GAPS, _LINK_DESTINATIONS, _LINK_TITLES))
    yield from _random_texts(_VIEW_TOKENS, 60_000, 11, seed=1133)


def test_the_destination_of_a_link_is_read_as_before_but_for_three_classes():
    """For each ``<``: ``glued-title`` (the old pattern took a title directly after the ``>``, which is
    none), ``line-break-around`` (one line break may precede the ``<`` and the ``)``) and ``escaped-delimiter``
    (``\\>``, ``\\"`` and ``\\)`` escape in a destination and a title). Where the new scanner reads a link,
    the old pattern reads the same one from the text with its escapes plain and the line breaks of its span
    spaces; where it reads none, the old reads none from the plain-escape text, or a glued title."""
    seen = Counter()
    for view in _view_inputs():
        for at in (found.start() for found in re.finditer("<", view)):
            new = core._link_destination_end(view, at)
            plain = _plain_escapes(view, at)
            old_plain = old_destination_end(plain, at)
            if new is not None:
                spaced = plain[:new].replace("\n", " ") + plain[new:]
                assert old_destination_end(spaced, at) == new, (view, at)
                if old_plain != new:
                    assert old_plain is None, (view, at)
                    seen["line-break-around"] += 1
            elif old_plain is not None:
                # ``glued-title``: the first ``>`` after the ``<`` ends the old destination, a title opens on it.
                assert plain[plain.index(">", at) + 1:][:1] in ('"', "'", "("), (view, at)
                seen["glued-title"] += 1
            if old_destination_end(view, at) != old_plain:
                seen["escaped-delimiter"] += 1
    assert all(seen[name] > 50 for name in ("glued-title", "line-break-around", "escaped-delimiter")), seen
    assert set(seen) == {"glued-title", "line-break-around", "escaped-delimiter"}


_HTML_TOKENS = [
    "](", "[", "]", "<b>", "<a href=\"x\">", ")", " ", "\n", "`", "<!-- c -->", "\\", '"t"', "{{placeholder: p}}",
    "<", ">", "'x'", "(t)", "<?p ", "?>", "<!d ", "<![CDATA[ ", "]]>", "a",
]


def test_inline_html_is_found_as_before_with_the_old_link_destination(monkeypatch):
    """Nothing of ``_inline_html`` but the destination changed: with the old reading of it, the same HTML is found."""
    monkeypatch.setattr(core, "_link_destination_end", old_destination_end)
    for text in _random_texts(_HTML_TOKENS, 20_000, 12, seed=1134):
        assert core._inline_html(text, lex(text)) == old_inline_html(text, lex(text)), repr(text)


@pytest.mark.parametrize(("text", "found"), [
    ('[a](<b> "t") <i>', "<i>"),
    ('[a](<b>"t") <i>', "<b>"),
    ("[a](\n<b>) <i>", "<i>"),
    ("[a](<b>\n) <i>", "<i>"),
    ("[a](\n\n<b>) <i>", "<b>"),
    ('[a](<b> "t\\"u") <i>', "<i>"),
    ("[a](<b\\>c>) <i>", "<i>"),
    ("[a](<b> (t\\)u)) <i>", "<i>"),
])
def test_inline_html_after_a_link_destination(text, found):
    assert core._inline_html(text, lex(text)) == found


# ── The insertion boundary ────────────────────────────────────────

_REFERENCE_TOKENS = [
    "[", "]", "[]", "[]:", "]:", ":", "[a]:", "[a]", " ", "    ", "  ", "\t", "\n", "\r", "u", "x", "\"", "'", "(", ")",
    "\xa0", "\x1c", "\0", "\0\0", "{{placeholder: p}}", "{{choose: q | a: b}}", "t", " ",
]


def test_the_link_reference_of_the_insertion_boundary_is_the_old_pattern_at_every_offset():
    """No class: ``_InsertionScan._reference_end`` is where the old pattern, run at that offset of the template (its
    directives masked), ended, or -1. A pinned over-approximation, not CommonMark's rule."""
    matches = 0
    for text in _random_texts(_REFERENCE_TOKENS + ["[a]: ", "u ", '"t"', "'t'", "(t)"] * 2, 20_000, 14, seed=1135):
        lexed = lex(text)
        template = mask_directives(text, lexed.directives)
        scan = templates._InsertionScan(text, template, lexed.directives)
        for offset in range(len(template) + 1):
            old = _OLD_LINK_REFERENCE_RE.match(template, offset)
            assert scan._reference_end(offset) == (old.end() if old else -1), (text, offset)
            matches += bool(old)
    assert matches > 10_000, matches


@pytest.mark.parametrize(("template", "end"), [
    ("[a]: u", 6),
    ("[a]: u 'ab", 6),
    ("[a]: u 'ab'", 11),
    ("[a]: u (a\nb)", 12),
    ("   [a]: u", 9),
    ("    [a]: u", -1),
    ("[]x]:", -1),
    ("[]:", -1),
    ("[a]x]:", -1),
    ("[a\nb]: u", 8),
    ("[a]:\nu", 4),
    ("[a]:  \t\"t\"", 10),
    ("[a]: u\t\"t\"", 10),
    ("[a]: u\t\"t", 6),
    ("[a]: u\r\"t\"", 6),
])
def test_the_link_reference_end_of_the_cases_that_differ_from_commonmark(template, end):
    scan = templates._InsertionScan(template, template, [])
    assert scan._reference_end(0) == end


# ── The implementations of CommonMark ─────────────────────────────


_BLOCKS_RE = re.compile(r"\n[ \t]*(?:\n|>)")  # a blank line or a quote ends the paragraph a link is in
#: markdown-it reads a backslash before any character as a pair, not only before punctuation (cmark does not).
_BACKSLASH_RE = re.compile(r"\\(?![!-/:-@\[-`{-~])")


def _links_by_markdown_it(text):
    markdown_it = pytest.importorskip("markdown_it")  # (only the tests that ask markdown-it need it)
    for token in markdown_it.MarkdownIt("commonmark").parse(text):
        if any(child.type == "link_open" for child in token.children or []):
            return True
    return False


def test_an_inline_link_is_one_as_markdown_it_reads_it():
    """``[a](…)`` is a link to ``scan_inline_link`` when it is one to markdown-it, but for what the paragraph
    would not hold (a blank line, a quote) and the backslash before a character that is no punctuation. (A CR is
    left out, as a document's line endings are LF, and so are brackets, which would make links of their own.)"""
    pytest.importorskip("markdown_it")
    read = 0
    for body in _random_texts([token for token in _TOKENS if token not in ("\r", "[", "](", "]")], 30_000, 9, seed=1136):
        text = f"[a]({body}"
        if _BLOCKS_RE.search(text) or _BACKSLASH_RE.search(text) or text.endswith("\\"):
            continue
        assert (markdown.scan_inline_link(text, 3) is not None) == _links_by_markdown_it(text), repr(text)
        read += 1
    assert read > 20_000


#: Where pandoc (``-f commonmark``) deviates, and the specification and markdown-it do not (CONFORMANCE.md):
#: it reads a title directly after ``<…>``, and keeps a control character or DEL in a bare destination.
_PANDOC_DEVIATIONS = ['[a](<b>"t")', "[a](b\x7fc)", "[a](b\x01c)", "[a](b\x1fc)"]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")
@pytest.mark.parametrize("text", _PANDOC_DEVIATIONS, ids=repr)
def test_pandoc_reads_a_link_where_the_specification_reads_none(text):
    """Strictly it is none; permissively (the path checks) it is one, as pandoc reads it."""
    pandoc = subprocess.run(
        ["pandoc", "-f", "commonmark", "-t", "json"], input=text, capture_output=True, text=True, check=True
    ).stdout
    assert '"t":"Link"' in pandoc.replace(" ", "")
    assert markdown.scan_inline_link(text, 3) is None
    assert markdown.scan_inline_link(text, 3, permissive=True) is not None


@pytest.mark.parametrize("text", _PANDOC_DEVIATIONS, ids=repr)
def test_markdown_it_reads_no_link_where_pandoc_reads_one(text):
    assert not _links_by_markdown_it(text)


def test_nothing_a_renderer_reads_as_a_link_is_none_to_the_permissive_reading():
    """The safety direction, on random text: where markdown-it reads a link (an image is one), so does
    ``scan_inline_link(permissive=True)`` (pandoc: see ``test_pandoc_reads_a_link_where_the_specification_reads_none``)."""
    pandoc_less = [token for token in _TOKENS if token not in ("\r", "[", "](", "]")]
    for body in _random_texts(pandoc_less, 20_000, 9, seed=1137):
        text = f"[a]({body}"
        if _BLOCKS_RE.search(text) or _BACKSLASH_RE.search(text) or text.endswith("\\"):
            continue
        if _links_by_markdown_it(text):
            assert markdown.scan_inline_link(text, 3, permissive=True) is not None, repr(text)
