"""Anchor and condition markers (spec §5.2, §5.7, §15.3).

A marker follows a heading's text, or ends a list item's first paragraph or
a top-level paragraph::

    marker    ::= "{" attribute ( ws+ attribute )* "}"
    attribute ::= "#" identifier | "when=" condition

Shared by the parser, which splits a heading's marker from its text, and the
validator, which finds markers in body text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Something that looks like a marker: braces around a first word starting
# with ``#`` or ``when=`` and further words each holding ``#`` or ``=``, such
# as ``{#id}``, ``{#a #b}``, or ``{#id class=x}``. Whether it is one is
# decided by parse_marker; a look-alike is literal text (anchor-misplaced).
# Prose in braces, such as ``{#1 and #2}``, is not a look-alike.
_LOOK_ALIKE = (
    r"\{[ \t]*(?:#|when=)[^{}\s]+(?:[ \t]+[^{}\s]*[#=][^{}\s]*)*[ \t]*\}"
)
MARKER_RE = re.compile(_LOOK_ALIKE)

_ATTRIBUTE_RE = re.compile(r"#(?P<id>\S+)|when=(?P<when>\S+)")


@dataclass(frozen=True, slots=True)
class Marker:
    """A marker's attributes, as written: ``identifier`` for ``#id`` and
    ``condition`` for ``when=``, each ``""`` when absent. Their format is
    checked by the validator (anchor-format, condition-invalid)."""

    identifier: str = ""
    condition: str = ""


def parse_marker(text: str) -> Marker | None:
    """The marker *text* (braces included) holds, or None when it is not a
    marker: an attribute other than ``#id`` and ``when=``, one written
    twice, or none at all."""
    inner = text[1:-1]
    if not (text.startswith("{") and text.endswith("}")) or inner != inner.strip():
        return None
    words = inner.split()
    identifier = condition = ""
    for word in words:
        attribute = _ATTRIBUTE_RE.fullmatch(word)
        if attribute is None:
            return None
        if attribute.group("id") is not None:
            if identifier:
                return None
            identifier = attribute.group("id")
        else:
            if condition:
                return None
            condition = attribute.group("when")
    if not words:
        return None
    return Marker(identifier, condition)


# A heading's trailing marker, after its text and at least one space. Only
# comments may follow it: they are not rendered (§8.6), as after a body marker.
_TRAILING_MARKER_RE = re.compile(
    rf"\s+({_LOOK_ALIKE})((?:\s*<!--(?:(?!-->).)*-->)*)\s*$"
)


def split_heading(text: str) -> tuple[str, Marker]:
    """A heading's text without its trailing marker, and the marker (empty
    when there is none). Comments after the marker stay in the text, before
    it. A trailing look-alike that is not a marker stays in the text, where
    the validator reports it (anchor-misplaced)."""
    trailing = _TRAILING_MARKER_RE.search(text)
    if trailing:
        marker = parse_marker(trailing.group(1))
        if marker is not None:
            title = f"{text[: trailing.start()]} {trailing.group(2).strip()}"
            return title.strip(), marker
    return text.strip(), Marker()


def format_marker(marker: Marker) -> str:
    """The source of *marker*, ``#id`` first (§15.3); ``""`` when empty."""
    attributes = [f"#{marker.identifier}"] if marker.identifier else []
    if marker.condition:
        attributes.append(f"when={marker.condition}")
    return "{" + " ".join(attributes) + "}" if attributes else ""
