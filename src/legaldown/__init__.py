"""legaldown — Reference implementation of the LegalDown document format.

Parse, serialize, and validate LegalDown documents, and assemble templates
(§15.7). Every diagnostic carries the specification's stable rule id (§16.1),
so tooling can filter, suppress, or escalate individual checks. Only
external dependency: PyYAML.

``legaldown`` is the workflow and the document model; tools that read
LegalDown source the way the validator does use ``legaldown.syntax`` and
``legaldown.grammar``.

Quick start::

    from legaldown import load, serialize, validate

    doc = load("contract.lgd")
    result = validate(doc)
    for diagnostic in result.diagnostics:
        print(diagnostic.level, diagnostic.rule, diagnostic.message)
    if result.is_valid:
        print(serialize(doc))  # save(doc) writes it back to the file
"""
from __future__ import annotations

# The category legaldown's deprecation warnings are raised with (none are, in 0.5.0)
from ._deprecation import LegaldownDeprecationWarning

# Assembly (§15.7): a named capability (§17.6)
from .assembly import AssemblyError, AssemblyResult, Question
from .files import LoadFile, file_loader

# Compatibility: the ``grammar`` and ``syntax`` names imported here, and the
# dict factories with ``BLOCK_DEFAULTS``, left ``__all__`` in 0.4.0 but still
# import from ``legaldown``, as the same objects and without a warning. Their
# supported home is the tooling API, ``legaldown.grammar`` and
# ``legaldown.syntax``; for ``document_from_dict`` and ``document_to_dict`` it is
# ``Document.from_dict`` and ``Document.to_dict`` (``models`` is internal, and
# the other factories and ``empty_document`` have no home but this one).
from .grammar import DELIMITER_PAIRS, DIRECTIVE_PARAMS, KNOWN_DIRECTIVES, slugify_identifier  # noqa: F401
from .models import (  # noqa: F401 -- BLOCK_DEFAULTS and the *_from_dict factories are kept for compatibility
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
    metadata_from_dict,
    party_from_dict,
    section_from_dict,
    side_from_dict,
)

# Parser & serializer
from .parser import FrontmatterError, load, parse
from .serializer import save, serialize

# The LegalDown specification version this implementation targets.
from .specification import SPEC_VERSION
from .syntax import (  # noqa: F401 -- kept for compatibility
    DefinitionAnchor,
    DefinitionRef,
    Directive,
    Fragment,
    Lexed,
    ListFragment,
    block_fragments,
    collect_definitions,
    collect_source_directives,
    definition_lookup,
    find_definition_anchors,
    id_term,
    is_drafting_note,
    is_escaped,
    item_text,
    iter_directives,
    lex,
    list_fragments,
    list_items,
    render_block,
    render_item,
)
from .template import AnswersError, Form, Template, load_answers, load_template, parse_template

# Validator
from .validator import (
    Blank,
    Diagnostic,
    DocumentIndex,
    InlineValues,
    PlacedMarker,
    SectionIndexEntry,
    ValidationResult,
    is_template,
    validate,
)

__version__ = "0.5.0"

#: Conformance level per specification §17. "core" — parse and validate a
#: single document. Rendering and Full (multi-file: includes, attachments,
#: bilingual sets) are not claimed. Exact rule coverage:
#: https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md
CONFORMANCE_LEVEL = "core"

#: Named capabilities claimed besides the level (§17.6): template assembly,
#: for single-file templates; a caller-supplied ``resolve`` also lets it
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
    "FrontmatterError",
    "save",
    "serialize",
    "validate",
    # Assembly (§15.7, §17.6)
    "load_template",
    "parse_template",
    "load_answers",
    "AnswersError",
    "Template",
    "Form",
    "AssemblyResult",
    "AssemblyError",
    "Question",
    "LoadFile",
    "file_loader",
    # Result types
    "ValidationResult",
    "DocumentIndex",
    "InlineValues",
    "SectionIndexEntry",
    "Blank",
    "Diagnostic",
    "PlacedMarker",
    # What validate decides, on its own (§15.1)
    "is_template",
    # Document model
    "Document",
    "Metadata",
    "Section",
    "Block",
    "ListItem",
    "Side",
    "Party",
    "Representative",
    "CustomField",
    "Amends",
    "Attachment",
    # The category of the deprecation warnings legaldown raises (none in 0.5.0), kept so that a
    # ``warnings`` filter naming it keeps working
    "LegaldownDeprecationWarning",
]
