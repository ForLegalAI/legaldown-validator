"""legaldown — Reference implementation of the LegalDown document format.

Parse, serialize, and validate LegalDown documents. Every diagnostic carries
the specification's stable rule id (§15.1), so tooling can filter, suppress,
or escalate individual checks. Only external dependency: PyYAML.

Quick start::

    from legaldown import parse_document, serialize_document, validate_document

    doc = parse_document(open("contract.lgd").read())
    result = validate_document(doc)
    for diagnostic in result.diagnostics:
        print(diagnostic.level, diagnostic.rule, diagnostic.message)
    if result.is_valid:
        print(serialize_document(doc))
"""
from __future__ import annotations

# Definitions (§7)
from .definitions import (
    DEF_ANCHOR_RE,
    DELIMITER_PAIRS,
    DefinitionRef,
    collect_definitions,
    definition_lookup,
    migrate_legacy_definitions,
    migrate_legacy_directives,
)
from .models import (
    BLOCK_DEFAULTS,
    Amends,
    Attachment,
    Block,
    CustomField,
    Document,
    Metadata,
    Party,
    Representative,
    Section,
    Side,
    block_from_dict,
    document_from_dict,
    document_to_dict,
    empty_document,
    metadata_from_dict,
    party_from_dict,
    repair_legacy_metadata,
    section_from_dict,
    side_from_dict,
)

# Parser & serializer
from .parser import collect_source_directives, parse_document
from .serializer import render_block, serialize_document

# Validator
from .validator import (
    AttachmentDefinitionsImporter,
    DefinitionsImporter,
    Diagnostic,
    SectionIndexEntry,
    ValidationResult,
    slugify_identifier,
    validate_document,
)

__version__ = "0.1.0"

#: The LegalDown specification version this implementation targets.
SPEC_VERSION = "0.1"

#: Conformance level per specification §16. "core" — parse and validate a
#: single document. Rendering and Full (multi-file: includes, attachments,
#: bilingual sets) are not claimed; see the README for exact rule coverage.
CONFORMANCE_LEVEL = "core"

__all__ = [
    # Package metadata
    "__version__",
    "SPEC_VERSION",
    "CONFORMANCE_LEVEL",
    # Core workflow
    "parse_document",
    "serialize_document",
    "validate_document",
    # Definitions (§7)
    "collect_definitions",
    "definition_lookup",
    "DefinitionRef",
    "DELIMITER_PAIRS",
    "DEF_ANCHOR_RE",
    "migrate_legacy_definitions",
    "migrate_legacy_directives",
    "repair_legacy_metadata",
    "render_block",
    "AttachmentDefinitionsImporter",
    # Result types
    "ValidationResult",
    "SectionIndexEntry",
    "Diagnostic",
    # Document model
    "Document",
    "Metadata",
    "Section",
    "Block",
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
