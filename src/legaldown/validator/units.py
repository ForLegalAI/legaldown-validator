"""The structural units of a document and their presence conditions
(spec §5.7, §12.2, §15.3).

Sections, list items, top-level and preamble paragraphs, and include
paragraphs are the units a condition may attach to. This module finds every
marker in the body, decides whether it is in a marker position, and gives
each unit its *presence*: its own condition together with those of the
sections enclosing it.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..directives import Directive, Lexed, is_escaped
from ..markers import MARKER_RE, Marker, parse_marker
from ..models import Document
from .conditions import ALWAYS, Condition, Presence, condition_problem, parse_condition

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_PARAGRAPHS = ("paragraph", "definition", "ref", "term")
_LISTS = ("ordered_list", "unordered_list")


def is_include_only(text: str, directives: list[Directive]) -> bool:
    """True if *text* holds a single ``{{include:}}`` directive and nothing
    else but comments, which are not rendered (§8.6). *directives* are lexed
    from a string that begins with *text*."""
    inside = [d for d in directives if d.end <= len(text)]
    return (
        len(inside) == 1
        and inside[0].name == "include"
        and not inside[0].malformed
        and not HTML_COMMENT_RE.sub("", text[: inside[0].start]).strip()
        and not HTML_COMMENT_RE.sub("", text[inside[0].end:]).strip()
    )


@dataclass(frozen=True, slots=True)
class FoundMarker:
    """A marker, or a look-alike, found in body text."""

    source: str  # as written
    marker: Marker | None  # None when it is not a marker (§15.3)
    section: int | None  # the section's index, or None in the preamble
    block: int
    fragment: int  # the index of its text in block_fragments(block)
    #: Why it is literal text (anchor-misplaced), or "" when it is in a
    #: marker position. PREAMBLE_CONDITION is a condition in a place that
    #: only a template gives it.
    misplaced: str
    #: True for the marker of a paragraph holding only an {{include:}}: its
    #: #id is ignored (§12.2), its condition applies.
    include_only: bool

    def placed(self, template: bool) -> bool:
        """True if the marker is in a marker position: its #id and condition
        apply. A preamble paragraph's condition is placed only in a
        template (§5.7)."""
        return not self.misplaced or (self.misplaced == PREAMBLE_CONDITION and template)


_ANYWHERE = (
    "not in a marker position and is literal text: markers go after a heading's "
    "text, or at the end of a list item's first paragraph or of a paragraph directly "
    "inside a section (§5.7, §15.3)"
)
_NOT_A_MARKER = (
    "not a marker and is literal text: a marker holds '#id', 'when=condition', or "
    "both, each at most once (§15.3)"
)
PREAMBLE_CONDITION = (
    "a condition on a preamble paragraph, which applies only in a template (§5.7, "
    "§15.3); in this document it is literal text"
)
_PREAMBLE_ITEM = (
    "in the preamble, where only a paragraph may carry a condition (§5.7, §15.3); "
    "on a list item it is literal text"
)
_PREAMBLE_ANCHOR = (
    "in the preamble, which carries no anchors (§4.4); it is literal text. A "
    "preamble paragraph in a template may carry a condition alone (§15.3)"
)


def marker_matches(text: str, lexed: Lexed) -> Iterator[re.Match[str]]:
    """The markers and look-alikes in *text*, whose lexing is *lexed*: not
    in code or comments (blanked in the lexer's view), not escaped, and not
    in a directive's value (§11.4)."""
    for match in MARKER_RE.finditer(lexed.view):
        if not is_escaped(text, match.start()) and not any(
            d.start <= match.start() < d.end for d in lexed.directives if not d.malformed
        ):
            yield match


def find_markers(document: Document, lex_fragment: Callable[[str], Lexed]) -> list[FoundMarker]:
    """Every marker and look-alike in the document's body text, outside code,
    comments, directives, and escapes (§11.4), with its place."""
    # Imported here, as in core.py: definitions -> validator.helpers ->
    # validator/__init__ -> core -> units would otherwise be a cycle.
    from ..definitions import block_fragments

    found: list[FoundMarker] = []
    for section, block_index, block in document.iter_indexed_blocks():
        for fragment_index, (fragment, position) in enumerate(block_fragments(block)):
            if "{#" not in fragment and "{when=" not in fragment:
                continue
            lexed = lex_fragment(fragment)
            for m in marker_matches(fragment, lexed):
                marker = parse_marker(m.group(0))
                # Only whitespace and comments may follow a marker: a
                # comment is not rendered (§8.6), but a code span is text.
                # An item's marker ends its first paragraph, its first
                # line here: a note or code may follow (§5.7).
                line_end = fragment.find("\n", m.end())
                rest = fragment[m.end():line_end] if line_end >= 0 else fragment[m.end():]
                at_end = (
                    position
                    and "\n" not in fragment[:m.start()]
                    and not HTML_COMMENT_RE.sub("", rest).strip()
                )
                include_only = (
                    at_end
                    and block.kind == "paragraph"
                    and is_include_only(fragment[:m.start()], lexed.directives)
                )
                preamble_condition = bool(
                    section is None
                    and at_end
                    and block.kind in _PARAGRAPHS
                    and marker
                    and marker.condition
                    and not marker.identifier
                )
                if marker is None:
                    misplaced = _NOT_A_MARKER
                elif not at_end:
                    misplaced = _ANYWHERE
                elif section is None and marker.identifier and not include_only:
                    misplaced = _PREAMBLE_ANCHOR
                elif preamble_condition and not include_only:
                    misplaced = PREAMBLE_CONDITION
                elif section is None and block.kind in _LISTS and marker.condition:
                    misplaced = _PREAMBLE_ITEM
                elif section is None and not include_only:
                    misplaced = _ANYWHERE
                else:
                    misplaced = ""
                found.append(
                    FoundMarker(
                        m.group(0), marker, section, block_index, fragment_index,
                        misplaced, include_only,
                    )
                )
    return found


def own_presence(text: str, questions: Any) -> Presence:
    """The presence a unit's own condition *text* adds: nothing when it has
    none, or when it is invalid (reported as condition-invalid), so reasoning
    never rests on a broken condition."""
    if not text or condition_problem(text, questions):
        return ALWAYS
    condition: Condition = parse_condition(text)  # valid: never None
    return frozenset({condition})


class Units:
    """The presence of every unit in a document (§15.3).

    Built from the markers found in the body; *template* decides whether a
    preamble paragraph's condition applies (in any other document it is
    literal text, §5.7). Only valid conditions (no condition-invalid) enter a
    presence, so an error is reported once and reasoning stays sound.
    """

    def __init__(
        self,
        document: Document,
        markers: list[FoundMarker],
        questions: Any,
        *,
        template: bool,
    ) -> None:
        self._blocks = [document.preamble, *(s.blocks for s in document.sections)]
        self._section_presence: list[Presence] = []
        self._section_enclosing: list[Presence] = []
        stack: list[tuple[int, Presence]] = []  # (level, presence) of open sections
        for section in document.sections:
            while stack and stack[-1][0] >= section.level:
                stack.pop()
            enclosing = stack[-1][1] if stack else ALWAYS
            presence = enclosing | own_presence(section.condition, questions)
            self._section_enclosing.append(enclosing)
            self._section_presence.append(presence)
            stack.append((section.level, presence))
        self._own_conditions: dict[tuple[int | None, int, int | None], Presence] = {}
        for found in markers:
            if not found.placed(template):
                continue
            if not found.marker or not found.marker.condition:
                continue
            key = self._unit(found.section, found.block, found.fragment)
            if key is not None:
                self._own_conditions[key] = own_presence(found.marker.condition, questions)

    def _unit(
        self, section: int | None, block: int, fragment: int | None
    ) -> tuple[int | None, int, int | None] | None:
        """The key of the unit holding *fragment* of a block: a list item
        (section, block, fragment), a paragraph (section, block, None), or
        None for a block that carries no condition."""
        kind = self._blocks[0 if section is None else section + 1][block].kind
        if kind in _LISTS:
            return section, block, fragment
        if kind in _PARAGRAPHS:
            return section, block, None
        return None

    def enclosing(self, section: int) -> Presence:
        """The presence of the sections enclosing a section."""
        return self._section_enclosing[section]

    def own(self, section: int | None, block: int, fragment: int | None) -> Presence:
        """The condition a body unit carries itself (for condition-never-true)."""
        key = self._unit(section, block, fragment)
        return ALWAYS if key is None else self._own_conditions.get(key, ALWAYS)

    def presence(self, section: int | None, block: int | None = None, fragment: int | None = None) -> Presence:
        """The presence of a section (*block* None), or of the text at
        *fragment* of a block: its sections' conditions, and its list item's
        or paragraph's own."""
        enclosing = ALWAYS if section is None else self._section_presence[section]
        if block is None:
            return enclosing
        return enclosing | self.own(section, block, fragment)
