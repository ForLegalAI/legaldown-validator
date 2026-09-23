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

# A candidate marker: braces on one line opening with ``#`` or ``when=``.
# Whether it looks like a marker is decided by is_look_alike, and whether it
# is one by parse_marker. A single character class keeps the scan linear.
_CANDIDATE = r"\{[ \t]*(?:#|when=)[^{}\n]*\}"
MARKER_RE = re.compile(_CANDIDATE)

_ATTRIBUTE_RE = re.compile(r"#(?P<id>\S+)|when=(?P<when>\S+)")


@dataclass(frozen=True, slots=True)
class Marker:
    """A marker's attributes, as written: ``identifier`` for ``#id`` and
    ``condition`` for ``when=``, each ``""`` when absent. Their format is
    checked by the validator (anchor-format, condition-invalid)."""

    identifier: str = ""
    condition: str = ""


def is_look_alike(text: str) -> bool:
    """True if the candidate *text* (braces included) looks like a marker:
    it opens with ``when=``, or with ``#id`` followed only by words holding
    ``#`` or ``=``, or by ``when`` in any spacing. A marker that is not one, such as
    ``{#id class=x}``, is literal text (anchor-misplaced); prose in braces,
    such as ``{#1 and #2}`` or ``{# note}``, is not a look-alike."""
    words = text[1:-1].split()
    if not words:
        return False
    first, rest = words[0], words[1:]
    if first.startswith("when="):
        return True
    return (
        first.startswith("#")
        and len(first) > 1
        and (
            all("#" in word or "=" in word for word in rest)
            or any(word.split("=")[0] == "when" for word in rest)
        )
    )


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


HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# A heading's trailing marker, after its text and at least one space. It is
# sought with comments blanked: a marker inside a comment is not one, and a
# comment may follow the marker, since it is not rendered (§8.6). The
# look-behind starts a match only at the start of a run of whitespace, which
# keeps a long run from being rescanned at every position.
_TRAILING_MARKER_RE = re.compile(rf"(?<!\s)\s+({_CANDIDATE})\s*$")


def split_heading(text: str) -> tuple[str, Marker]:
    """A heading's text without its trailing marker, and the marker (empty
    when there is none). Comments after the marker stay in the text, before
    it. A trailing look-alike that is not a marker stays in the text, where
    the validator reports it (anchor-misplaced)."""
    view = HTML_COMMENT_RE.sub(lambda comment: " " * len(comment.group()), text)
    trailing = _TRAILING_MARKER_RE.search(view)
    if trailing:
        marker = parse_marker(trailing.group(1))
        if marker is not None:
            title = f"{text[: trailing.start(1)].strip()} {text[trailing.end(1):].strip()}"
            return title.strip(), marker
    return text.strip(), Marker()


def format_marker(marker: Marker) -> str:
    """The source of *marker*, ``#id`` first (§15.3); ``""`` when empty."""
    attributes = [f"#{marker.identifier}"] if marker.identifier else []
    if marker.condition:
        attributes.append(f"when={marker.condition}")
    return "{" + " ".join(attributes) + "}" if attributes else ""
