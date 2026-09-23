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

from dataclasses import dataclass

from .directives import Directive, scan_directives
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


def accepted_delimiters(language: str | None) -> list[tuple[str, str, str, bool]]:
    """Return the delimiter pairs accepted for *language* (all by default)."""
    names = DELIMITERS_BY_LANGUAGE.get((language or "").lower())
    if not names:
        return list(DELIMITER_PAIRS)
    return [p for p in DELIMITER_PAIRS if p[2] in names]


# Emphasis markers a defined term must not carry in source (§7.2); the term
# is still recognized, and the validator warns (def-emphasis).
_EMPHASIS_MARKERS = ("**", "__", "++", "*", "_")

# Spacing allowed between a defined term's closing quotation mark and its
# {{def:}} (§7.2 "spaces or tabs"), including the no-break spaces French
# typography sets there.
_ANCHOR_GAP = " \t\u00a0\u202f"


def _trailing_emphasis(text: str, start: int, end: int) -> int:
    """Length of the emphasis markers ending at *end* (not before *start*);
    nested markers such as bold-italic ``***`` count together. An underscore
    run preceded by a letter or digit is part of that word, not emphasis
    (CommonMark)."""
    length = 0
    while True:
        for marker in _EMPHASIS_MARKERS:
            at = end - length - len(marker)
            if at >= start and text.startswith(marker, at):
                length += len(marker)
                break
        else:
            break
    at = end - length
    if length and text[at] == "_" and at > start and text[at - 1].isalnum():
        return 0
    return length


@dataclass(slots=True)
class DefinitionAnchor:
    """A ``{{def:}}`` directive and the quoted term it anchors (§7.2).

    ``term`` is ``None`` when no recognized quoted span immediately precedes
    the directive. ``start`` is the offset of the anchored span (its opening
    quotation mark or emphasis marker), or of the directive when there is no
    term.
    """

    directive: Directive
    term: str | None
    pair: tuple[str, str, str, bool] | None
    emphasis: bool
    start: int

    @property
    def single_quoted(self) -> bool:
        return self.pair is not None and self.pair[3]


def find_definition_anchors(
    text: str,
    *,
    language: str | None = None,
    scanned: list[tuple[Directive, str]] | None = None,
) -> list[DefinitionAnchor]:
    """Every ``{{def:}}`` in *text*, each with the quoted term preceding it.

    Per §7.2 the term is found by scanning back from the directive: only
    spacing may separate it from the closing quotation mark, and the span
    opens at the nearest prior matching opening mark on the same line.
    Directives in code spans, code blocks, and comments are literal (§11.4).
    *scanned* is ``list(scan_directives(text))`` when the caller has it.
    """
    closing = {pair[1]: pair for pair in accepted_delimiters(language)}
    anchors: list[DefinitionAnchor] = []
    for directive, scan in scan_directives(text) if scanned is None else scanned:
        if directive.name != "def":
            continue
        line_start = scan.rfind("\n", 0, directive.start) + 1
        end = directive.start
        while end > line_start and scan[end - 1] in _ANCHOR_GAP:
            end -= 1
        trailing = _trailing_emphasis(scan, line_start, end)
        end -= trailing
        pair = closing.get(scan[end - 1]) if end > line_start else None
        opening = scan.rfind(pair[0], line_start, end - 1) if pair else -1
        if pair is None or opening < 0:
            anchors.append(DefinitionAnchor(directive, None, None, False, directive.start))
            continue
        leading = _trailing_emphasis(scan, line_start, opening)
        anchors.append(
            DefinitionAnchor(
                directive=directive,
                term=text[opening + 1:end - 1].strip(),
                pair=pair,
                emphasis=bool(leading or trailing),
                start=opening - leading,
            )
        )
    return anchors


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
                for anchor in find_definition_anchors(fragment, language=lang):
                    # A malformed {{def:}} has no id to register; the validator
                    # reports it as directive-malformed.
                    if anchor.term is None or anchor.directive.malformed:
                        continue
                    term = anchor.term
                    raw_id = anchor.directive.positional or ""
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
