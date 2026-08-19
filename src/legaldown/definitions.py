"""LegalDown defined-term handling (spec section 7).

A definition is declared by writing the term in quotation marks followed
immediately by a ``{{def: id}}`` anchor (``"Services" {{def: services}}``). The
quotation marks are a *source-only* delimiter -- never rendered -- and the
``{{def:}}`` directive emits no visible output of its own; it anchors the
preceding term and registers the id. Definitions may appear anywhere: at the
top of a Definitions section, or inline at first use.

This module is the single source of truth for recognising definition anchors.
It is reused by the parser, serializer, validator, HTML/DOCX renderers, clause
analysis, and the editor glossary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .models import Block, Document
from .validator.helpers import slugify_identifier

# ---------------------------------------------------------------------------
# Accepted quotation-mark delimiters (spec 7.2)
# ---------------------------------------------------------------------------
#
# Each entry: (open, close, name, is_single). Double-quote forms are
# recommended; single-quote forms are accepted but flagged by the validator
# when ambiguous with an apostrophe (U+2019). All pairs are accepted by default;
# ``DELIMITERS_BY_LANGUAGE`` can narrow the set per document ``language``.

DELIMITER_PAIRS: list[tuple[str, str, str, bool]] = [
    ("\x22", "\x22", "straight-double", False),  # straight double
    ("“", "”", "curly-double", False),          # left/right double
    ("«", "»", "guillemets", False),            # << >>
    ("»", "«", "reversed-guillemets", False),   # >> <<
    ("„", "“", "low-high-double", False),       # low/high double
    ("‘", "’", "curly-single", True),           # left/right single
    ("‚", "‘", "low-high-single", True),        # low/high single
    ("‹", "›", "single-guillemets", True),      # < >
]

# Per-language accepted delimiter names. Empty / missing -> all pairs accepted.
DELIMITERS_BY_LANGUAGE: dict[str, list[str]] = {}

# The apostrophe code point that single-quote close marks collide with.
APOSTROPHE = "’"


def accepted_delimiters(language: str | None) -> list[tuple[str, str, str, bool]]:
    """Return the delimiter pairs accepted for *language* (all by default)."""
    names = DELIMITERS_BY_LANGUAGE.get((language or "").lower())
    if not names:
        return list(DELIMITER_PAIRS)
    return [p for p in DELIMITER_PAIRS if p[2] in names]


def _build_anchor_re(pairs: tuple[tuple[str, str, str, bool], ...]) -> re.Pattern[str]:
    """Build a regex matching ``<quoted term> {{def: id}}`` for *pairs*.

    Optional ``**``/``*``/``++`` emphasis markers around the quoted term are
    tolerated (the term is still recognised); the validator warns about them.
    """
    emphasis = r"(?:\*\*|\*|\+\+)?"
    alternation = "|".join(
        f"{emphasis}{re.escape(open_)}(?P<t{i}>.*?){re.escape(close)}{emphasis}"
        for i, (open_, close, _name, _single) in enumerate(pairs)
    )
    return re.compile(
        rf"(?:{alternation})[ \t ]*\{{\{{\s*def:\s*(?P<defid>[^}}]*?)\s*\}}\}}"
    )


@lru_cache(maxsize=16)
def _anchor_re_for_names(names: tuple[str, ...]) -> re.Pattern[str]:
    pairs = tuple(p for p in DELIMITER_PAIRS if p[2] in names)
    return _build_anchor_re(pairs)


def def_anchor_re(language: str | None = None) -> re.Pattern[str]:
    """Return the (cached) def-anchor regex for *language*."""
    names = tuple(p[2] for p in accepted_delimiters(language))
    return _anchor_re_for_names(names)


# All-pairs anchor regex -- convenient default for language-agnostic callers.
DEF_ANCHOR_RE: re.Pattern[str] = _build_anchor_re(tuple(DELIMITER_PAIRS))

# A bare ``{{def: id}}`` tag (used to detect anchors with no preceding term).
DEF_TAG_RE: re.Pattern[str] = re.compile(r"\{\{\s*def:\s*[^}]*\}\}")

# A defined term wrapped in emphasis markers right before a def anchor
# (e.g. **"Term"** {{def: id}}) — accepted but discouraged (validator warns).
_EMPHASIS = r"(?:\*\*|\*|\+\+)"
_BARE_PAIRS = "|".join(
    f"{re.escape(o)}.*?{re.escape(c)}" for o, c, _n, _s in DELIMITER_PAIRS
)
EMPHASIS_DEF_RE: re.Pattern[str] = re.compile(
    rf"{_EMPHASIS}\s*(?:{_BARE_PAIRS})\s*{_EMPHASIS}?[ \t]*\{{\{{\s*def:"
)


def _matched_pair_index(match: re.Match[str]) -> int:
    """Index into ``DELIMITER_PAIRS`` of the delimiter pair that matched."""
    for i in range(len(DELIMITER_PAIRS)):
        group_name = f"t{i}"
        if group_name in match.re.groupindex and match.group(group_name) is not None:
            return i
    return -1


def extract_def(match: re.Match[str]) -> tuple[str, str]:
    """Return ``(term, raw_id)`` from a ``DEF_ANCHOR_RE`` match (id may be '')."""
    i = _matched_pair_index(match)
    term = match.group(f"t{i}").strip() if i >= 0 else ""
    raw_id = (match.group("defid") or "").strip()
    return term, raw_id


def is_single_quoted(match: re.Match[str]) -> bool:
    """True if the matched anchor used a single-quote delimiter pair."""
    i = _matched_pair_index(match)
    return i >= 0 and DELIMITER_PAIRS[i][3]


# ---------------------------------------------------------------------------
# Definition collection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DefinitionRef:
    """A single declared definition with its source location."""

    id: str
    term: str
    section_identifier: str
    block_index: int
    inline: bool       # True: mid-text anchor; False: leading anchor of a definition block
    auto_id: bool      # True if the id was derived from the term (omitted in source)


def text_fragments(block: Block) -> list[str]:
    """All free-text fragments of a block that may contain inline directives."""
    fragments: list[str] = []
    if block.text:
        fragments.append(block.text)
    if block.prefix:
        fragments.append(block.prefix)
    if block.suffix:
        fragments.append(block.suffix)
    fragments.extend(item for item in block.items if item)
    fragments.extend(cell for row in block.rows for cell in row if cell)
    return fragments


def collect_definitions(
    document: Document, *, language: str | None = None
) -> list[DefinitionRef]:
    """Collect every definition declared in *document*, in document order.

    Scans both ``definition`` blocks (a paragraph whose leading token is a
    definition anchor) and inline anchors inside any text fragment.
    """
    lang = language or document.metadata.language or "en"
    rx = def_anchor_re(lang)
    refs: list[DefinitionRef] = []
    for section in document.sections:
        for block_index, block in enumerate(section.blocks):
            if block.kind == "definition":
                term = (block.term or "").strip()
                raw_id = (block.definition_id or "").strip()
                did = raw_id or slugify_identifier(term, fallback="term")
                refs.append(
                    DefinitionRef(
                        id=did,
                        term=term or did.replace("-", " ").title(),
                        section_identifier=section.identifier,
                        block_index=block_index,
                        inline=False,
                        auto_id=not raw_id,
                    )
                )
            for fragment in text_fragments(block):
                for match in rx.finditer(fragment):
                    term, raw_id = extract_def(match)
                    did = raw_id or slugify_identifier(term, fallback="term")
                    refs.append(
                        DefinitionRef(
                            id=did,
                            term=term,
                            section_identifier=section.identifier,
                            block_index=block_index,
                            inline=True,
                            auto_id=not raw_id,
                        )
                    )
    return refs


def definition_lookup(refs: list[DefinitionRef]) -> dict[str, str]:
    """Build an ``{id: term}`` lookup from definition refs (first wins)."""
    lookup: dict[str, str] = {}
    for ref in refs:
        lookup.setdefault(ref.id, ref.term)
    return lookup
