"""legaldown.syntax — Reading LegalDown source: lexer, markers, fragments, Markdown helpers.

What a tool needs to read the text of a document the way the validator does:
the directive lexer (§11.4), anchor and condition markers (§5.7, §15.3), the
text fragments of a block that may hold either, the definitions written in
the source (§7), block quotes and drafting notes (§15.6), and the Markdown
helpers (fences, indentation, comments) those readings are built on; and,
the other way, a block or list item written back as LegalDown source.

This module is part of the supported API and covered by semantic versioning.
Every name here is the validator's own object, not a copy, so a tool that
reads source with it finds what the validator finds. Import them from here;
the modules they are defined in are internal and may be reorganised.

    from legaldown import load
    from legaldown.syntax import find_markers

    for found in find_markers(load("contract.lgd")):
        print(found.source, found.misplaced or "placed")
"""
from __future__ import annotations

from .definitions import (
    DefinitionAnchor,
    DefinitionRef,
    Fragment,
    ListFragment,
    block_fragments,
    collect_definitions,
    definition_lookup,
    find_definition_anchors,
    id_term,
    list_fragments,
    text_fragments,
)
from .directives import Directive, Lexed, format_value, is_escaped, iter_directives, lex
from .markdown import (
    FENCE_OPEN_RE,
    HTML_COMMENT_RE,
    LINE_ENDING_RE,
    CodeContent,
    closes_fence,
    code_content,
    dedent,
    fence_end,
    indent_width,
    strip_text,
)
from .markers import MARKER_RE, Marker, format_marker, is_look_alike, parse_marker
from .models import item_text, list_items
from .parser import (
    FRONTMATTER_RE,
    DirectiveLocation,
    collect_source_directives,
    iter_document_directives,
    quote_blocks,
)
from .positions import BlockSpan, HeadingSpan, ItemSpan, SectionSpan, SourceLayout, body_layout
from .serializer import render_block, render_item
from .validator.templates import Quote, block_quotes, drafting_note_blocks, is_drafting_note
from .validator.units import FoundMarker, find_markers, is_include_only

__all__ = [
    # Directives (§11)
    "lex",
    "Lexed",
    "Directive",
    "iter_directives",
    "is_escaped",
    "format_value",
    "collect_source_directives",
    "iter_document_directives",
    "DirectiveLocation",
    # Markers (§5.7, §15.3)
    "Marker",
    "MARKER_RE",
    "parse_marker",
    "format_marker",
    "is_look_alike",
    "FoundMarker",
    "find_markers",
    "is_include_only",
    # Where text is: fragments, lists, quotes
    "Fragment",
    "ListFragment",
    "block_fragments",
    "list_fragments",
    "text_fragments",
    "Quote",
    "block_quotes",
    "is_drafting_note",
    "quote_blocks",
    "drafting_note_blocks",
    "code_content",
    "CodeContent",
    "list_items",
    "item_text",
    # Definitions in the source (§7): the terms a document defines, and where
    "collect_definitions",
    "DefinitionRef",
    "definition_lookup",
    "id_term",
    "find_definition_anchors",
    "DefinitionAnchor",
    # A block or list item written as LegalDown source
    "render_block",
    "render_item",
    # Markdown
    "FRONTMATTER_RE",
    "LINE_ENDING_RE",
    "HTML_COMMENT_RE",
    "FENCE_OPEN_RE",
    "closes_fence",
    "fence_end",
    "dedent",
    "indent_width",
    "strip_text",
    # Where a parsed document's parts are in its file (``Document.layout``),
    # and where a body text's are (``body_layout``)
    "body_layout",
    "SourceLayout",
    "SectionSpan",
    "HeadingSpan",
    "BlockSpan",
    "ItemSpan",
]
