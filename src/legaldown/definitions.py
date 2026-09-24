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

from collections.abc import Callable
from dataclasses import dataclass

from .directives import Directive, Lexed, lex, mask_directives
from .markdown import is_indented_code
from .models import Block, Document
from .validator.helpers import generate_identifier

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
    lexed: Lexed | None = None,
) -> list[DefinitionAnchor]:
    """Every ``{{def:}}`` in *text*, each with the quoted term preceding it.

    Per §7.2 the term is found by scanning back from the directive: only
    spacing may separate it from the closing quotation mark, and the span
    opens at the nearest prior matching opening mark on the same line.
    Directives in code spans, code blocks, and comments are literal (§11.4).
    *lexed* is ``lex(text)`` when the caller has it.
    """
    closing = {pair[1]: pair for pair in accepted_delimiters(language)}
    lexed = lexed or lex(text)
    # A directive in the term is opaque: its quoted values are not the
    # term's quotation marks.
    scan = mask_directives(lexed.view, lexed.directives)
    anchors: list[DefinitionAnchor] = []
    for directive in lexed.directives:
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
    section_index: int | None  # index in document.sections; None for the preamble (§4.4)
    block_index: int
    #: Index of the text it is in, in block_fragments(block); None for the
    #: term and id a definition block holds in its own fields.
    fragment_index: int | None
    inline: bool       # True: mid-text anchor; False: leading anchor of a definition block
    auto_id: bool      # True if the id was derived from the term (omitted in source)
    #: True if deriving the id dropped letters or digits without an ASCII
    #: form (§5.3): an explicit id is recommended (def-lossy-slug).
    lossy_id: bool = False


def block_fragments(block: Block) -> list[tuple[str, bool]]:
    """The free-text fragments of *block* that may contain inline directives,
    each with whether a ``{#id}`` at its very end is in an anchor position.

    Anchor positions (§5.7) are the end of a top-level paragraph (including
    one the parser split around a lifted {{ref:}} or {{term:}}) and the end
    of a list item; a block quote or a table cell never is one.
    """
    if block.kind == "html" or (block.kind == "code" and is_indented_code(block.text)):
        # Raw HTML and indented code: no directive or marker is recognized
        # (§11.4). A fenced block's text is lexed, which blanks the fence:
        # text a model puts after its closing fence is checked.
        return []
    paragraph = block.kind in ("paragraph", "definition")
    lifted = block.kind in ("ref", "term")
    listed = block.kind in ("ordered_list", "unordered_list")
    fragments: list[tuple[str, bool]] = []
    if block.text:
        fragments.append((block.text, paragraph))
    if block.prefix:
        fragments.append((block.prefix, False))
    if block.suffix:
        fragments.append((block.suffix, lifted))
    fragments.extend((item, listed) for item in block.items if item)
    fragments.extend((cell, False) for cell in block.headers if cell)
    fragments.extend((cell, False) for row in block.rows for cell in row if cell)
    return fragments


def text_fragments(block: Block) -> list[str]:
    """All free-text fragments of a block that may contain inline directives."""
    return [fragment for fragment, _anchor_position in block_fragments(block)]


def collect_definitions(
    document: Document,
    *,
    language: str | None = None,
    lex_fragment: Callable[[str], Lexed] = lex,
) -> list[DefinitionRef]:
    """Collect every definition declared in *document*, in document order.

    Scans both ``definition`` blocks (a paragraph whose leading token is a
    definition anchor) and inline anchors inside any text fragment.
    *lex_fragment* lets a caller that lexes the same fragments share results.
    """
    lang = language or document.metadata.language or "en"
    refs: list[DefinitionRef] = []
    for section_index, block_index, block in document.iter_indexed_blocks():
        if block.kind == "definition":
            term = (block.term or "").strip()
            raw_id = (block.definition_id or "").strip()
            did, lossy = (raw_id, False) if raw_id else generate_identifier(term)
            refs.append(
                DefinitionRef(
                    id=did,
                    term=term or did.replace("-", " ").title(),
                    section_index=section_index,
                    block_index=block_index,
                    fragment_index=None,
                    inline=False,
                    auto_id=not raw_id,
                    lossy_id=lossy,
                )
            )
        for fragment_index, (fragment, _position) in enumerate(block_fragments(block)):
            for anchor in find_definition_anchors(
                fragment, language=lang, lexed=lex_fragment(fragment)
            ):
                # A malformed {{def:}} has no id to register; the validator
                # reports it as directive-malformed.
                if anchor.term is None or anchor.directive.malformed:
                    continue
                term = anchor.term
                raw_id = anchor.directive.positional or ""
                did, lossy = (raw_id, False) if raw_id else generate_identifier(term)
                refs.append(
                    DefinitionRef(
                        id=did,
                        term=term,
                        section_index=section_index,
                        block_index=block_index,
                        fragment_index=fragment_index,
                        inline=True,
                        auto_id=not raw_id,
                        lossy_id=lossy,
                    )
                )
    return refs


def definition_lookup(refs: list[DefinitionRef]) -> dict[str, str]:
    """Build an ``{id: term}`` lookup from definition refs (first wins)."""
    lookup: dict[str, str] = {}
    for ref in refs:
        lookup.setdefault(ref.id, ref.term)
    return lookup
