"""legaldown — Reference implementation of the LegalDown document format.

Parse, serialize, and validate LegalDown documents. Every diagnostic carries
the specification's stable rule id (§16.1), so tooling can filter, suppress,
or escalate individual checks. Only external dependency: PyYAML.

Quick start::

    from legaldown import load, serialize_document, validate_document

    doc = load("contract.lgd")
    result = validate_document(doc)
    for diagnostic in result.diagnostics:
        print(diagnostic.level, diagnostic.rule, diagnostic.message)
    if result.is_valid:
        print(serialize_document(doc))
"""
from __future__ import annotations

# Assembly (§15.7): a named capability (§17.6)
from .assembly import (
    AssemblyError,
    AssemblyResult,
    LoadFile,
    Question,
    assemble,
    needed_questions,
    template_questions,
)

# Definitions (§7)
from .definitions import (
    DELIMITER_PAIRS,
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
)
from .directives import (
    DIRECTIVE_PARAMS,
    KNOWN_DIRECTIVES,
    Directive,
    Lexed,
    is_escaped,
    iter_directives,
    lex,
)
from .models import (
    BLOCK_DEFAULTS,
    Amends,
    Attachment,
    Block,
    CustomField,
    Document,
    ListItem,
    Metadata,
    Party,
    Representative,
    Section,
    Side,
    block_from_dict,
    document_from_dict,
    document_to_dict,
    empty_document,
    item_text,
    list_items,
    metadata_from_dict,
    party_from_dict,
    section_from_dict,
    side_from_dict,
)

# Parser & serializer
from .parser import FrontmatterError, collect_source_directives, load, parse, parse_document
from .serializer import render_block, render_item, serialize_document

# The LegalDown specification version this implementation targets.
from .specification import SPEC_VERSION

# Validator
from .validator import (
    AttachmentDefinitionsImporter,
    DefinitionsImporter,
    Diagnostic,
    PlacedMarker,
    SectionIndexEntry,
    ValidationResult,
    is_drafting_note,
    is_template,
    slugify_identifier,
    validate_document,
)

__version__ = "0.3.0"

#: Conformance level per specification §17. "core" — parse and validate a
#: single document. Rendering and Full (multi-file: includes, attachments,
#: bilingual sets) are not claimed. Exact rule coverage:
#: https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md
CONFORMANCE_LEVEL = "core"

#: Named capabilities claimed besides the level (§17.6): template assembly,
#: for single-file templates; a caller-supplied ``load_file`` also lets it
#: read include fragments and LegalDown attachment files.
CAPABILITIES: frozenset[str] = frozenset({"assembly"})

__all__ = [
    # Package metadata
    "__version__",
    "SPEC_VERSION",
    "CONFORMANCE_LEVEL",
    "CAPABILITIES",
    # Core workflow
    "load",
    "parse",
    "parse_document",  # deprecated since 0.4.0, removed in 0.5.0: use ``parse`` or ``load``
    "FrontmatterError",
    "serialize_document",
    "validate_document",
    # Assembly (§15.7, §17.6)
    "assemble",
    "template_questions",
    "needed_questions",
    "AssemblyResult",
    "AssemblyError",
    "Question",
    "LoadFile",
    # Definitions (§7)
    "collect_definitions",
    "definition_lookup",
    "id_term",
    "DefinitionRef",
    "DELIMITER_PAIRS",
    "find_definition_anchors",
    "DefinitionAnchor",
    # Directives (§11): the lexer, and where text holding them is
    "iter_directives",
    "lex",
    "Lexed",
    "is_escaped",
    "block_fragments",
    "list_fragments",
    "Fragment",
    "ListFragment",
    "Directive",
    "DIRECTIVE_PARAMS",
    "KNOWN_DIRECTIVES",
    "render_block",
    "render_item",
    "AttachmentDefinitionsImporter",
    # Result types
    "ValidationResult",
    "SectionIndexEntry",
    "Diagnostic",
    "PlacedMarker",
    # What validate_document decides, on its own (§15.1, §15.6)
    "is_template",
    "is_drafting_note",
    # Document model
    "Document",
    "Metadata",
    "Section",
    "Block",
    "ListItem",
    "item_text",
    "list_items",
    "Side",
    "Party",
    "Representative",
    "CustomField",
    "Amends",
    "Attachment",
    "BLOCK_DEFAULTS",
    # Factory functions
    "document_from_dict",
    "document_to_dict",
    "metadata_from_dict",
    "section_from_dict",
    "block_from_dict",
    "side_from_dict",
    "party_from_dict",
    "empty_document",
    # Utilities
    "collect_source_directives",
    "slugify_identifier",
    "DefinitionsImporter",
]
