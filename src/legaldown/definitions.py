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

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import NamedTuple

from .directives import Directive, Lexed, blank_fenced_code, lex, mask_directives
from .markdown import FENCE_OPEN_RE, is_indented_code, normalize_label
from .models import LIST_KINDS, Block, Document, list_items
from .validator.helpers import generate_identifier
from .validator.patterns import IDENTIFIER_RE
from .validator.templates import Quote  # templates reads this module lazily (see block_quotes), so no cycle

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

# Per-language accepted delimiter names, by lowercase language code. A language with no entry
# accepts all pairs, and so does every language today: the table is empty. ``accepted_delimiters``
# reads a document that says no language (``""``) as English.
DELIMITERS_BY_LANGUAGE: dict[str, list[str]] = {}


def accepted_delimiters(language: str | None) -> list[tuple[str, str, str, bool]]:
    """Return the delimiter pairs accepted for *language*: an empty or missing language is read
    as ``"en"`` (§3.2 gives ``language`` no default), and a language with no entry in
    ``DELIMITERS_BY_LANGUAGE`` accepts all pairs, which is every language while the table is empty."""
    names = DELIMITERS_BY_LANGUAGE.get((language or "en").lower())
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
    """A single declared definition with its source location (none in a drafting note, §15.6)."""

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
    #: The ``{{def:}}``'s offset in its text, for an inline anchor.
    offset: int = 0


class Fragment(NamedTuple):
    """A text of a block that may hold directives and markers
    (``block_fragments``): the *text*, and whether its end is an anchor
    position (§5.7)."""

    text: str
    anchor: bool


class ListFragment(NamedTuple):
    """A ``Fragment`` of a list (``list_fragments``), with the *items* it is
    in: each item's number, outermost first."""

    text: str
    anchor: bool
    items: tuple[int, ...]


def block_fragments(block: Block) -> list[Fragment]:
    """The free-text fragments of *block* that may contain inline directives,
    each with whether a ``{#id}`` at its very end is in an anchor position.

    Anchor positions (§5.7) are the end of a top-level paragraph (including
    one the parser split around a lifted {{ref:}} or {{term:}}) and the end
    of a list item's first paragraph; a block quote or a table cell never is
    one. A list's fragments are its items' blocks', in order
    (``list_fragments``); a block quote's, those of the blocks it holds
    (each read as its own text), so that nothing written in one block — a
    code span or comment left open — runs into the next, and code and raw
    HTML in it hold none (§11.4).
    """
    return [Fragment(text, position) for text, position, _items, _scope, _kind in _fragments(block)]


def list_fragments(block: Block) -> list[ListFragment]:
    """The fragments of a list's items, as ``block_fragments`` lists them:
    each with whether its end is an anchor position — the end of an item's
    first paragraph, the block it opens with (§5.7) — and the items it is
    in, outermost first. Items are numbered in pre-order among all the
    list's items: an item before the items nested in it, empty items
    included (§15.3). A list in a block quote is no unit: its fragments are
    in the items the quote is in."""
    return [ListFragment(text, position, items) for text, position, items, _scope, _kind in _fragments(block)]


def fragment_scopes(block: Block) -> list[int]:
    """The scope of each fragment of *block*, parallel to
    ``block_fragments(block)``: fragments with the same number are in one
    paragraph, the scope of a definition in it (§7.2, §16.3). A top-level
    block's text, prefix and suffix are one paragraph (a lifted
    ``{{term:}}`` target belongs to it too), as are those of a list item's
    paragraph and of a block quote's; each block of an item or quote has its
    own. A table's header row and each of its rows are scopes of their own,
    not its cells: a glossary row (``| "Foo" {{def: foo}} | means … |``) is
    one definition's paragraph. A quote too deep to read (``MAX_QUOTE_DEPTH``)
    is one fragment, hence one scope. Numbers are per block, from 0."""
    return [scope for _text, _position, _items, scope, _kind in _fragments(block)]


def fragment_kinds(block: Block) -> list[tuple[str, str]]:
    """The fragments of *block*, as ``block_fragments`` lists them, each as ``(text, kind)``: the text
    and the kind of the block it is in (a quote too deep to read is one fragment, of kind ``quote``)."""
    return [(text, kind) for text, _position, _items, _scope, kind in _fragments(block)]


def quote_ranges(block: Block) -> list[tuple[str, range]]:
    """The block quotes in *block*, nested ones included, outermost first:
    each quote's first line, and the indices of its fragments in
    ``block_fragments(block)``."""
    quotes: list[tuple[str, range]] = []
    _fragments(block, quotes)
    return quotes


def _fragments(
    block: Block, quotes: list[tuple[str, range]] | None = None, definitions: list[int] | None = None
) -> list[tuple[str, bool, tuple[int, ...], int, str]]:
    """``list_fragments`` of any block, each with its scope
    (``fragment_scopes``) and the kind of block it is in (``fragment_kinds``): the one walk that numbers fragments, items and
    scopes, and finds the quotes (``quote_ranges``) when *quotes* is given, and,
    when *definitions* is, how many fragments come before each link reference
    definition (#92), in the order of the definitions."""
    from .parser import MAX_QUOTE_DEPTH, quote_content  # the parser builds on this module

    fragments: list[tuple[str, bool, tuple[int, ...], int, str]] = []
    count = 0
    scope = 0  # the next scope number

    def walk(block: Block, depth: int, path: tuple[int, ...], anchor: bool, units: bool) -> None:
        # *depth*: the items and quotes *block* is in; *anchor*: whether its
        # end may be an anchor position; *units*: whether its items are
        # units, numbered — not in a quote.
        nonlocal count, scope
        if block.kind in LIST_KINDS:
            for item in list_items(block):
                inner = path
                if units:
                    inner = (*path, count)
                    count += 1
                k = 0  # the blocks before child that are text: a link reference definition is none (§4.7, #92)
                for child in item.blocks:
                    # A nested list's items' first paragraphs are anchor
                    # positions as its own are; of the item's other blocks,
                    # the first paragraph it opens with.
                    first = child.kind in LIST_KINDS or (k == 0 and child.kind == "paragraph")
                    walk(child, depth + 1, inner, anchor and first, units)
                    k += child.kind != "link_definition"
        elif block.kind == "quote":
            at, slot = len(fragments), len(quotes) if quotes is not None else 0
            if quotes is not None:
                quotes.append((block.text.split("\n", 1)[0].strip(), range(0)))
            if depth < MAX_QUOTE_DEPTH:
                children, _spans = quote_content(block.text, depth + 1)
                for child in children:
                    walk(child, depth + 1, path, False, False)
            elif block.text:
                # Too deep to read: its text as one, its fenced code blanked.
                fragments.append((blank_fenced_code(block.text), False, path, scope, block.kind))
                scope += 1
            if quotes is not None:
                quotes[slot] = (quotes[slot][0], range(at, len(fragments)))
        else:
            if definitions is not None and block.kind == "link_definition":
                definitions.append(len(fragments))
            own = _scoped_fragments(block)
            fragments.extend((text, position and anchor, path, scope + number, block.kind) for text, position, number in own)
            if own:
                scope += max(number for _text, _position, number in own) + 1

    walk(block, 0, (), True, True)
    return fragments


def nested_blocks(block: Block) -> Iterator[Block]:
    """*block* and every block in it, in document order: its list items'
    blocks and the blocks its quotes hold (``parser.quote_content``), as
    ``block_fragments`` reads them — a quote in ``MAX_QUOTE_DEPTH`` items and
    quotes holding none but its text."""
    from .parser import MAX_QUOTE_DEPTH, quote_content  # the parser builds on this module

    def walk(block: Block, depth: int) -> Iterator[Block]:
        yield block
        if block.kind in LIST_KINDS:
            for item in list_items(block):
                for child in item.blocks:
                    yield from walk(child, depth + 1)
        elif block.kind == "quote" and depth < MAX_QUOTE_DEPTH:
            children, _spans = quote_content(block.text, depth + 1)
            for child in children:
                yield from walk(child, depth + 1)

    yield from walk(block, 0)


def _own_fragments(block: Block) -> list[tuple[str, bool]]:
    """The fragments of a block that holds no blocks: ``block_fragments``."""
    return [(text, position) for text, position, _scope in _scoped_fragments(block)]


def fragments_before_target(block: Block) -> int:
    """For a block the parser lifted a ``{{ref:}}`` or ``{{term:}}`` from: how many of its
    ``block_fragments`` come before the target, so that it is read between that many and the
    rest: its text and the text before it, as ``_scoped_fragments`` lists them."""
    return _fragments_and_target(block)[1]


def _scoped_fragments(block: Block) -> list[tuple[str, bool, int]]:
    """``_own_fragments``, each with its scope within the block
    (``fragment_scopes``): 0 for the text, prefix and suffix, 1 for the
    header row of a table and 2 on for its rows."""
    return _fragments_and_target(block)[0]


def _fragments_and_target(block: Block) -> tuple[list[tuple[str, bool, int]], int]:
    """``_scoped_fragments``, and where a lifted target sits among them: how many come before it
    (the text, then the text before the target; the text after it is the rest)."""
    if block.kind in ("html", "source", "link_definition") or (
        block.kind == "code" and (
            is_indented_code(block.text)
            or ("\n" not in block.text and FENCE_OPEN_RE.match(block.text))
        )
    ):
        # Raw HTML and indented code: no directive or marker is recognized
        # (§11.4). A source block is not read at all: its text is read once
        # written and parsed (``serialize``). Nor is a link reference
        # definition, which holds no text: the parser leaves a definition
        # with a directive in it a paragraph (#92), so there is none to read
        # in a block, and a model built in code is written as it is. A fenced block's text
        # is read with its fence blanked (below): text a model puts after its
        # closing fence is checked -- which needs
        # at least the fence's own line and one more, so a fence with nothing
        # after it (unclosed at EOF, one line) is code through and through.
        return [], 0
    paragraph = block.kind in ("paragraph", "definition")
    lifted = block.kind in ("ref", "term")
    fragments: list[tuple[str, bool, int]] = []
    if block.kind == "code" and block.text:
        fragments.append((blank_fenced_code(block.text), False, 0))
    elif block.text:
        fragments.append((block.text, paragraph, 0))
    if block.prefix:
        fragments.append((block.prefix, False, 0))
    before = len(fragments)
    if block.suffix:
        fragments.append((block.suffix, lifted, 0))
    fragments.extend((cell, False, 1) for cell in block.headers if cell)
    fragments.extend((cell, False, 2 + number) for number, row in enumerate(block.rows) for cell in row if cell)
    return fragments, before


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
    A ``{{def:}}`` in a drafting note is left out: assembly removes the note before
    anything else (§15.7.2), so it declares nothing, and ``drafting-note-def`` reports
    it (§15.6). *lex_fragment* lets a caller that lexes the same fragments share results.
    """
    lang = language or document.metadata.language
    refs: list[DefinitionRef] = []
    for section_index, block_index, block in document.iter_indexed_blocks():
        quotes: list[tuple[str, range]] = []
        noted: set[int] | None = None  # the fragments in the block's drafting notes, once a definition asks (§15.6)
        if block.kind == "definition":
            term = (block.term or "").strip()
            raw_id = (block.definition_id or "").strip()
            did, lossy = (raw_id, False) if raw_id else generate_identifier(term)
            refs.append(
                DefinitionRef(
                    id=did,
                    term=term or id_term(did),
                    section_index=section_index,
                    block_index=block_index,
                    fragment_index=None,
                    inline=False,
                    auto_id=not raw_id,
                    lossy_id=lossy,
                )
            )
        # (the one walk that numbers the fragments and finds the quotes among them)
        for fragment_index, (fragment, *_rest) in enumerate(_fragments(block, quotes)):
            for anchor in find_definition_anchors(
                fragment, language=lang, lexed=lex_fragment(fragment)
            ):
                # A malformed {{def:}} has no id to register; the validator
                # reports it as directive-malformed.
                if anchor.term is None or anchor.directive.malformed:
                    continue
                if noted is None:
                    noted = {i for first, span in quotes if Quote(first, span).is_drafting_note for i in span}
                if fragment_index in noted:
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
                        offset=anchor.directive.start,
                    )
                )
    return refs


class LinkDefinition(NamedTuple):
    """What a link reference definition (``[label]: url "title"``, §4.7)
    gives the links and images that name its label: its *url* and *title*,
    raw as a ``link_definition`` block holds them (without ``<>`` and the
    title's delimiters, backslash escapes and character references not
    decoded); *title* is "" for none."""

    url: str
    title: str


def collect_link_definitions(document: Document) -> dict[str, LinkDefinition]:
    """The link reference definitions of *document* (§4.7, #92), by label.

    The key is the label as CommonMark matches it (``markdown.normalize_label``):
    without the space around it, each run of spaces and line breaks in it one
    space, case-folded. The first definition in the document wins, as in
    CommonMark, wherever it is: a definition in a list item or a block quote
    counts for the whole document, and is met in document order — a list
    item's blocks and the blocks of a quote (``nested_blocks``) in the place
    they are written. A definition a quote holds past ``MAX_QUOTE_DEPTH``
    is not read, nor one a model built in code gives an empty label. A
    definition holding a directive is paragraph text (#92), so it is none:
    its destination is not final until the directive is filled in. Derived
    from the blocks on each call, not a field of the document."""
    return {key: LinkDefinition(each.definition.target, each.definition.title) for key, each in locate_link_definitions(document).items()}


class PlacedLinkDefinition(NamedTuple):
    """A link reference definition and where it is in the document
    (``locate_link_definitions``): the ``link_definition`` *definition* block
    itself, the *section* (None: the preamble) and the index *block* of the
    top-level block it is in, and its *ordinal* among the ``link_definition``
    blocks in that block, nested ones included, in document order. The
    ordinal names the block for ``Locator.definition`` (its line) and orders
    the definitions of one top-level block; *fragment* puts it among its texts. ``collect_link_definitions`` gives
    the ``LinkDefinition`` (url and title) of each, by label."""

    section: int | None
    block: int
    ordinal: int
    definition: Block
    #: How many of the top-level block's ``block_fragments`` come before it, so that
    #: it has its place among the texts: the definition lies after the fragment
    #: numbered one less, and before the one with this number.
    fragment: int


def locate_link_definitions(document: Document) -> dict[str, PlacedLinkDefinition]:
    """``collect_link_definitions`` with the blocks themselves and where they are
    (``PlacedLinkDefinition``): the first definition of each label, by the same key
    (``markdown.normalize_label``), in document order."""
    found: dict[str, PlacedLinkDefinition] = {}
    for section, index, top in document.iter_indexed_blocks():
        if not _may_hold_definition(top):
            continue  # (a quote is not read again for nothing, #92 review)
        before: list[int] = []
        _fragments(top, None, before)  # (the walk that numbers the fragments, so they agree)
        ordinal = 0
        for block in nested_blocks(top):
            if block.kind == "link_definition":
                key = normalize_label(block.label)
                if key:
                    found.setdefault(key, PlacedLinkDefinition(section, index, ordinal, block, before[ordinal]))
                ordinal += 1
    return found


def _may_hold_definition(block: Block) -> bool:
    """False if *block* holds no ``link_definition`` block, nested ones included, as
    far as a look at it can tell, without reading the quotes in it again: a quote
    holds one only if its text holds ``]:``, which ends the label of every
    definition; a list item holds the blocks it has."""
    if block.kind == "link_definition":
        return True
    if block.kind in LIST_KINDS:
        return any(_may_hold_definition(child) for item in list_items(block) for child in item.blocks)
    return block.kind == "quote" and "]:" in block.text


def id_term(def_id: str) -> str:
    """The term a definition written with an empty one reads as: its id,
    hyphens as spaces, each word capitalized (``"" {{def: late-fee}}`` is
    "Late Fee")."""
    return def_id.replace("-", " ").title()


def definition_lookup(refs: list[DefinitionRef]) -> dict[str, str]:
    """Build an ``{id: term}`` lookup from definition refs (first wins), as
    ``validate`` builds its ``index.definition_lookup``: an empty term
    reads as its id (``id_term``), and an id that is not a valid identifier
    (anchor-format) is not registered."""
    lookup: dict[str, str] = {}
    for ref in refs:
        if IDENTIFIER_RE.fullmatch(ref.id):
            lookup.setdefault(ref.id, ref.term or id_term(ref.id))
    return lookup
