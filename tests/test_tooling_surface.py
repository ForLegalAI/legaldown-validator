"""The tooling surface: ``legaldown.grammar`` and ``legaldown.syntax``, the
semver-covered home of what downstream tools used to import from private
modules, and the small helpers they were given with it."""
from __future__ import annotations

import importlib

import pytest

import legaldown
import legaldown.grammar as grammar
import legaldown.syntax as syntax
from legaldown import load_answers, parse, serialize, validate
from legaldown.directives import lex
from legaldown.syntax import find_markers
from legaldown.validator.helpers import generate_identifier
from legaldown.validator.patterns import FINAL_CHECK_RULES
from legaldown.validator.templates import check_choose, choose_problem

GRAMMAR = [
    "SPEC_VERSION", "LEGALDOWN_EXTENSIONS",
    "KNOWN_DIRECTIVES", "DIRECTIVE_PARAMS", "PLACEHOLDER_TYPE_PARAMS",
    "IDENTIFIER_RE", "RESERVED_VALUE_TYPES", "VALID_DOC_TYPES", "PARTY_TYPES",
    "VALID_PLACEHOLDER_TYPES", "DURATION_UNITS", "VALID_DURATION_UNITS", "KNOWN_CURRENCIES",
    "DELIMITER_PAIRS",
    "LIST_KINDS", "MAX_SECTION_LEVEL", "MAX_QUOTE_DEPTH", "MAX_LIST_DEPTH",
    "VALUE_QUESTION_TYPES", "DECISION_QUESTION_TYPES", "QUESTION_TYPES",
    "DRAFTING_MARKER", "FINAL_CHECK_RULES",
    "slugify_identifier", "format_section_number", "is_valid_iso_date", "is_valid_numeric",
    "is_valid_money_amount", "is_positive_numeric",
    "parse_condition", "Condition", "condition_problem", "exclusive", "Presence", "ALWAYS",
    "choose_problem",
]

SYNTAX = [
    "lex", "Lexed", "Directive", "iter_directives", "is_escaped", "format_value",
    "collect_source_directives", "iter_document_directives", "DirectiveLocation",
    "Marker", "MARKER_RE", "parse_marker", "format_marker", "is_look_alike",
    "FoundMarker", "find_markers", "is_include_only",
    "Fragment", "ListFragment", "block_fragments", "list_fragments", "text_fragments",
    "Quote", "block_quotes", "is_drafting_note", "quote_blocks", "drafting_note_blocks", "code_content", "CodeContent",
    "list_items", "item_text",
    "collect_definitions", "DefinitionRef", "definition_lookup", "id_term",
    "find_definition_anchors", "DefinitionAnchor",
    "render_block", "render_item",
    "FRONTMATTER_RE", "LINE_ENDING_RE", "HTML_COMMENT_RE", "FENCE_OPEN_RE",
    "closes_fence", "fence_end", "dedent", "indent_width", "strip_text",
    "body_layout", "SourceLayout", "SectionSpan", "HeadingSpan", "BlockSpan", "ItemSpan",
]

#: Where each public name is implemented.
IMPLEMENTATION = {
    "DELIMITER_PAIRS": "legaldown.definitions",
    "DefinitionAnchor": "legaldown.definitions",
    "DefinitionRef": "legaldown.definitions",
    "collect_definitions": "legaldown.definitions",
    "definition_lookup": "legaldown.definitions",
    "find_definition_anchors": "legaldown.definitions",
    "id_term": "legaldown.definitions",
    "render_block": "legaldown.serializer",
    "render_item": "legaldown.serializer",
    "Fragment": "legaldown.definitions",
    "ListFragment": "legaldown.definitions",
    "block_fragments": "legaldown.definitions",
    "list_fragments": "legaldown.definitions",
    "text_fragments": "legaldown.definitions",
    "DIRECTIVE_PARAMS": "legaldown.directives",
    "KNOWN_DIRECTIVES": "legaldown.directives",
    "PLACEHOLDER_TYPE_PARAMS": "legaldown.directives",
    "Directive": "legaldown.directives",
    "Lexed": "legaldown.directives",
    "format_value": "legaldown.directives",
    "is_escaped": "legaldown.directives",
    "iter_directives": "legaldown.directives",
    "lex": "legaldown.directives",
    "FENCE_OPEN_RE": "legaldown.markdown",
    "HTML_COMMENT_RE": "legaldown.markdown",
    "LINE_ENDING_RE": "legaldown.markdown",
    "closes_fence": "legaldown.markdown",
    "dedent": "legaldown.markdown",
    "fence_end": "legaldown.markdown",
    "indent_width": "legaldown.markdown",
    "strip_text": "legaldown.markdown",
    "MARKER_RE": "legaldown.markers",
    "Marker": "legaldown.markers",
    "format_marker": "legaldown.markers",
    "is_look_alike": "legaldown.markers",
    "parse_marker": "legaldown.markers",
    "LIST_KINDS": "legaldown.models",
    "item_text": "legaldown.models",
    "list_items": "legaldown.models",
    "FRONTMATTER_RE": "legaldown.parser",
    "MAX_LIST_DEPTH": "legaldown.parser",
    "MAX_QUOTE_DEPTH": "legaldown.parser",
    "collect_source_directives": "legaldown.parser",
    "SPEC_VERSION": "legaldown.specification",
    "ALWAYS": "legaldown.validator.conditions",
    "Condition": "legaldown.validator.conditions",
    "Presence": "legaldown.validator.conditions",
    "condition_problem": "legaldown.validator.conditions",
    "exclusive": "legaldown.validator.conditions",
    "parse_condition": "legaldown.validator.conditions",
        "format_section_number": "legaldown.validator.helpers",
    "is_positive_numeric": "legaldown.validator.helpers",
    "is_valid_iso_date": "legaldown.validator.helpers",
    "is_valid_money_amount": "legaldown.validator.helpers",
    "is_valid_numeric": "legaldown.validator.helpers",
    "slugify_identifier": "legaldown.validator.helpers",
    "DURATION_UNITS": "legaldown.validator.patterns",
    "FINAL_CHECK_RULES": "legaldown.validator.patterns",
    "IDENTIFIER_RE": "legaldown.validator.patterns",
    "KNOWN_CURRENCIES": "legaldown.validator.patterns",
    "LEGALDOWN_EXTENSIONS": "legaldown.validator.patterns",
    "MAX_SECTION_LEVEL": "legaldown.validator.patterns",
    "PARTY_TYPES": "legaldown.validator.patterns",
    "RESERVED_VALUE_TYPES": "legaldown.validator.patterns",
    "VALID_DOC_TYPES": "legaldown.validator.patterns",
    "VALID_DURATION_UNITS": "legaldown.validator.patterns",
    "VALID_PLACEHOLDER_TYPES": "legaldown.validator.patterns",
    "DECISION_QUESTION_TYPES": "legaldown.validator.templates",
    "DRAFTING_MARKER": "legaldown.validator.templates",
    "QUESTION_TYPES": "legaldown.validator.templates",
    "Quote": "legaldown.validator.templates",
    "VALUE_QUESTION_TYPES": "legaldown.validator.templates",
    "block_quotes": "legaldown.validator.templates",
    "is_drafting_note": "legaldown.validator.templates",
    "choose_problem": "legaldown.validator.templates",
    "FoundMarker": "legaldown.validator.units",
    "find_markers": "legaldown.validator.units",
    "is_include_only": "legaldown.validator.units",
    "DirectiveLocation": "legaldown.parser",
    "iter_document_directives": "legaldown.parser",
    "quote_blocks": "legaldown.parser",
    "drafting_note_blocks": "legaldown.validator.templates",
    "CodeContent": "legaldown.markdown",
    "code_content": "legaldown.markdown",
    "body_layout": "legaldown.positions",
    "SourceLayout": "legaldown.positions",
    "SectionSpan": "legaldown.positions",
    "HeadingSpan": "legaldown.positions",
    "BlockSpan": "legaldown.positions",
    "ItemSpan": "legaldown.positions",
}

#: What PactTrack and legaldown-render import today from private modules:
#: (module, name) -> (public module, public name).
DOWNSTREAM = {
    # PactTrack
    ("legaldown.definitions", "text_fragments"): ("legaldown.syntax", "text_fragments"),
    ("legaldown.definitions", "DELIMITER_PAIRS"): ("legaldown.grammar", "DELIMITER_PAIRS"),
    ("legaldown.directives", "PLACEHOLDER_TYPE_PARAMS"): ("legaldown.grammar", "PLACEHOLDER_TYPE_PARAMS"),
    ("legaldown.directives", "format_value"): ("legaldown.syntax", "format_value"),
    ("legaldown.markdown", "FENCE_OPEN_RE"): ("legaldown.syntax", "FENCE_OPEN_RE"),
    ("legaldown.markdown", "HTML_COMMENT_RE"): ("legaldown.syntax", "HTML_COMMENT_RE"),
    ("legaldown.markdown", "LINE_ENDING_RE"): ("legaldown.syntax", "LINE_ENDING_RE"),
    ("legaldown.markdown", "dedent"): ("legaldown.syntax", "dedent"),
    ("legaldown.markdown", "fence_end"): ("legaldown.syntax", "fence_end"),
    ("legaldown.markdown", "strip_text"): ("legaldown.syntax", "strip_text"),
    ("legaldown.markers", "MARKER_RE"): ("legaldown.syntax", "MARKER_RE"),
    ("legaldown.markers", "Marker"): ("legaldown.syntax", "Marker"),
    ("legaldown.markers", "format_marker"): ("legaldown.syntax", "format_marker"),
    ("legaldown.markers", "is_look_alike"): ("legaldown.syntax", "is_look_alike"),
    ("legaldown.markers", "parse_marker"): ("legaldown.syntax", "parse_marker"),
    ("legaldown.models", "LIST_KINDS"): ("legaldown.grammar", "LIST_KINDS"),
    ("legaldown.parser", "FRONTMATTER_RE"): ("legaldown.syntax", "FRONTMATTER_RE"),
    ("legaldown.parser", "MAX_QUOTE_DEPTH"): ("legaldown.grammar", "MAX_QUOTE_DEPTH"),
    ("legaldown.parser", "quote_content"): ("legaldown.syntax", "quote_blocks"),
    ("legaldown.parser", "_layout"): ("legaldown.syntax", "body_layout"),  # for bare body text
    ("legaldown.validator.patterns", "LEGALDOWN_EXTENSIONS"): ("legaldown.grammar", "LEGALDOWN_EXTENSIONS"),
    ("legaldown.validator.templates", "DECISION_QUESTION_TYPES"): ("legaldown.grammar", "DECISION_QUESTION_TYPES"),
    ("legaldown.validator.templates", "QUESTION_TYPES"): ("legaldown.grammar", "QUESTION_TYPES"),
    ("legaldown.validator.templates", "block_quotes"): ("legaldown.syntax", "block_quotes"),
    ("legaldown.validator.templates", "check_choose"): ("legaldown.grammar", "choose_problem"),
    ("legaldown.validator.units", "find_markers"): ("legaldown.syntax", "find_markers"),
    ("legaldown.validator.units", "is_include_only"): ("legaldown.syntax", "is_include_only"),
    ("legaldown.validator", "IDENTIFIER_RE"): ("legaldown.grammar", "IDENTIFIER_RE"),
    ("legaldown.validator", "KNOWN_CURRENCIES"): ("legaldown.grammar", "KNOWN_CURRENCIES"),
    ("legaldown.validator", "VALID_DOC_TYPES"): ("legaldown.grammar", "VALID_DOC_TYPES"),
    ("legaldown.validator", "VALID_DURATION_UNITS"): ("legaldown.grammar", "VALID_DURATION_UNITS"),
    ("legaldown.validator", "VALID_PLACEHOLDER_TYPES"): ("legaldown.grammar", "VALID_PLACEHOLDER_TYPES"),
    ("legaldown.validator", "is_valid_iso_date"): ("legaldown.grammar", "is_valid_iso_date"),
    ("legaldown.validator", "parse_condition"): ("legaldown.grammar", "parse_condition"),
    ("legaldown.validator", "slugify_identifier"): ("legaldown.grammar", "slugify_identifier"),
    # legaldown-render
    ("legaldown.markdown", "closes_fence"): ("legaldown.syntax", "closes_fence"),
    ("legaldown.markdown", "indent_width"): ("legaldown.syntax", "indent_width"),
    ("legaldown.cli", "_read_answers"): ("legaldown", "load_answers"),
    ("legaldown.validator", "ALWAYS"): ("legaldown.grammar", "ALWAYS"),
    ("legaldown.validator", "Presence"): ("legaldown.grammar", "Presence"),
    ("legaldown.validator", "condition_problem"): ("legaldown.grammar", "condition_problem"),
    ("legaldown.validator", "exclusive"): ("legaldown.grammar", "exclusive"),
    ("legaldown.validator", "is_positive_numeric"): ("legaldown.grammar", "is_positive_numeric"),
    ("legaldown.validator", "is_valid_money_amount"): ("legaldown.grammar", "is_valid_money_amount"),
}

def test_all_lists_are_the_expected_names():
    assert sorted(grammar.__all__) == sorted(GRAMMAR)
    assert sorted(syntax.__all__) == sorted(SYNTAX)
    assert len(set(grammar.__all__)) == len(grammar.__all__)
    assert len(set(syntax.__all__)) == len(syntax.__all__)


def test_the_tooling_names_stay_out_of_the_top_level_package():
    top = set(legaldown.__all__)
    assert top.isdisjoint({"PARTY_TYPES", "MAX_SECTION_LEVEL", "choose_problem", "find_markers"})
    # Since 0.4.0 none is listed there: those that were still import from it (``test_public_api``).
    assert top.isdisjoint(set(grammar.__all__) - {"SPEC_VERSION"})
    assert top.isdisjoint(syntax.__all__)


def test_the_definitions_read_from_source_are_the_validators():
    document = parse('---\ntitle: T\n---\n\n# A\n\n"Late Fee" {{def: late-fee}} means x. "" {{def: bad_id}}\n')
    refs = syntax.collect_definitions(document)
    assert [(ref.id, ref.term, ref.inline) for ref in refs] == [("late-fee", "Late Fee", False), ("bad_id", "", True)]
    assert syntax.definition_lookup(refs) == {"late-fee": "Late Fee"} == validate(document).index.definition_lookup
    assert syntax.id_term("late-fee") == "Late Fee"
    text = 'A "Late Fee" {{def: late-fee}} is due.'
    [anchor] = syntax.find_definition_anchors(text)
    assert (anchor.term, anchor.directive.positional, anchor.start) == ("Late Fee", "late-fee", text.index('"'))


def test_a_block_and_an_item_are_written_as_the_serializer_writes_them():
    document = parse("---\ntitle: T\n---\n\n# A\n\nText **bold**.\n\n- one\n\n  two\n")
    [paragraph, listing] = document.sections[0].blocks
    assert syntax.render_block(paragraph) == "Text **bold**."
    item = syntax.render_item(listing.items[0])
    assert item == "one\n\ntwo"  # later lines are indented from the item's content column
    assert syntax.render_block(listing) == "- one\n\n  two"
    assert serialize(document).endswith(syntax.render_block(paragraph) + "\n\n" + syntax.render_block(listing) + "\n")


@pytest.mark.parametrize("module", [grammar, syntax])
def test_every_name_is_documented_and_defined_elsewhere(module):
    for name in module.__all__:
        implementation = importlib.import_module(IMPLEMENTATION[name])
        assert getattr(module, name) is getattr(implementation, name), name
        # A constant has no docstring of its own; everything else must.
        value = getattr(module, name)
        if callable(value) or isinstance(value, type):
            assert (value.__doc__ or "").strip(), name


def test_every_implementation_is_listed():
    assert set(IMPLEMENTATION) == set(GRAMMAR) | set(SYNTAX)


@pytest.mark.parametrize(("deep", "public"), DOWNSTREAM.items(), ids=lambda key: ".".join(key))
def test_a_downstream_import_has_a_public_home(deep, public):
    home = getattr(importlib.import_module(public[0]), public[1])
    old = getattr(importlib.import_module(deep[0]), deep[1])  # the deep path keeps working
    if deep[1] == public[1]:
        assert home is old


def test_the_source_layout_is_reached_from_the_document():
    # ``parser._layout`` gave body-relative spans; ``Document.layout()`` gives
    # file lines, and ``Document.line_of`` one of them.
    document = parse("---\ntitle: T\n---\n\n# A\n\nText.\n")
    layout = document.layout()
    assert isinstance(layout, syntax.SourceLayout)
    assert layout.sections[0].blocks[0].start == document.line_of(0, 0) == 7
    assert load_answers is not None


def _spans(layout, shift=0):
    """Every span of *layout* as ``(what, start, end)``, moved up by *shift* lines."""

    def block(span):
        yield span.kind, span.start - shift, span.end - shift
        for item in span.items:
            yield "item", item.start - shift, item.end - shift
            for inner in item.blocks:
                yield from block(inner)

    found = [each for span in layout.preamble for each in block(span)]
    for section in layout.sections:
        heading = section.heading
        found.append(("heading", heading.start - shift, heading.end - shift))
        found.append(("marker", heading.marker_line - shift, 0))
        found.extend(each for span in section.blocks for each in block(span))
    return found


def test_body_layout_reads_text_as_a_body_with_no_frontmatter():
    layout = syntax.body_layout("---\ntitle: x\n---\nClause\n")
    assert layout.frontmatter is None
    # What the body parser reads: a thematic break, then a heading ("title: x" underlined) and a paragraph.
    assert [(span.kind, span.start, span.end) for span in layout.preamble] == [("rule", 1, 2)]
    (section,) = layout.sections
    assert (section.heading.start, section.heading.end) == (2, 4)
    assert [(span.kind, span.start, span.end) for span in section.blocks] == [("paragraph", 4, 5)]


def test_body_layout_agrees_with_the_layout_of_a_document_shifted_by_its_body_start():
    body = "Pre.\n\n# A\n\nText\nmore\n\n1. one\n   - x\n   - y\n2. two\n\n> q\n\n## B\n\n| h |\n|---|\n| c |\n\nEnd.\n"
    source = "---\ntitle: T\nparties: []\n---\n\n" + body
    document = parse(source)
    layout = document.layout()
    start = document.line_of(None, 0) - 1
    assert start == 5
    assert layout.frontmatter == (1, 5)
    assert _spans(syntax.body_layout(body)) == _spans(layout, start)
    assert syntax.body_layout(body).frontmatter is None


def test_body_layout_counts_lines_from_the_start_of_the_text_and_reads_every_line_ending():
    assert syntax.body_layout("A\r\n\r\n# H\r\n\rText\r") == syntax.body_layout("A\n\n# H\n\nText\n")
    layout = syntax.body_layout("A\n\n# H\n\nText")
    assert (layout.preamble[0].start, layout.sections[0].blocks[0].start) == (1, 5)
    assert syntax.body_layout("") == syntax.SourceLayout(None, (), ())


def test_is_drafting_note_is_the_validators_and_reads_a_quote_block():
    from legaldown import Block

    assert syntax.is_drafting_note is legaldown.is_drafting_note
    assert syntax.is_drafting_note(Block(kind="quote", text="[!drafting]\nAdvise."))
    assert not syntax.is_drafting_note(Block(kind="quote", text="Advise."))
    assert not syntax.is_drafting_note(Block(kind="paragraph", text="[!DRAFTING]"))


# ── The constants the validator now reads ─────────────────────────


_PARTY = "---\ntitle: T\nsides:\n  - name: providers\n    parties:\n      - name: acme\n        type: {type}\n"
_SECOND = "  - name: clients\n    parties:\n      - name: beta\n        type: legal_entity\n---\n\n# A\n\nText.\n"


def test_party_types_are_what_the_validator_accepts():
    assert grammar.PARTY_TYPES == ("legal_entity", "natural_person")
    for party_type in grammar.PARTY_TYPES:
        assert "party-type-invalid" not in validate(parse(_PARTY.format(type=party_type) + _SECOND)).rules()
    result = validate(parse(_PARTY.format(type="company") + _SECOND))
    [message] = [d.message for d in result.diagnostics if d.rule == "party-type-invalid"]
    assert message == (
        "Party 'acme' has invalid type 'company'. Must be 'legal_entity' or 'natural_person'."
    )


def test_the_section_level_limit_is_what_the_validator_enforces():
    assert grammar.MAX_SECTION_LEVEL == 5
    deepest = "---\ntitle: T\n---\n\n" + "".join(f"{'#' * n} H{n}\n\nText.\n\n" for n in range(1, 6))
    assert "heading-depth" not in validate(parse(deepest)).rules()
    result = validate(parse(deepest + "###### H6\n\nText.\n"))
    [message] = [d.message for d in result.diagnostics if d.rule == "heading-depth"]
    assert message == (
        "Section 'H6' uses unsupported heading level 6. LegalDown supports levels 1-5 (§4.1)."
    )


def test_the_drafting_marker_makes_a_drafting_note():
    assert grammar.DRAFTING_MARKER == "[!DRAFTING]"
    note = parse(f"---\ntitle: T\n---\n\n# A\n\n> {grammar.DRAFTING_MARKER}\n> Guidance.\n")
    [quote] = syntax.block_quotes(note.sections[0].blocks[0])
    assert quote.is_drafting_note


_TEMPLATE = """---
title: Fixture
sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
  - name: clients
    parties:
      - name: beta
        type: legal_entity
questions:
  vat:
    type: boolean
---

# Terms {#terms when=vat}

Pay {{placeholder: fee, currency=EUR}} {{choose: vat, true=a, false=b}}.

> [!DRAFTING]
> Check the fee.
"""


def test_the_final_check_reports_only_its_rules_and_both_of_them():
    assert frozenset({"placeholder-unfilled", "template-construct-present"}) == grammar.FINAL_CHECK_RULES
    assert FINAL_CHECK_RULES is grammar.FINAL_CHECK_RULES
    document = parse(_TEMPLATE)
    template_rules = validate(document).rules()
    final_rules = validate(document, final=True).rules()
    assert not FINAL_CHECK_RULES & template_rules
    assert (final_rules - template_rules) == set(FINAL_CHECK_RULES)


# ── slugify_identifier(fallback=) ─────────────────────────────────


@pytest.mark.parametrize("text", ["", "   ", "---", "日本語", "<!-- c -->", "!?"])
def test_a_text_without_an_identifier_yields_the_fallback(text):
    assert grammar.slugify_identifier(text) == "section"
    assert grammar.slugify_identifier(text, fallback="") == ""
    assert grammar.slugify_identifier(text, fallback="x") == "x"
    assert generate_identifier(text, "")[0] == ""


@pytest.mark.parametrize("text", ["Fees", "1st Term", "Zahlung & Frist", "Section"])
def test_the_fallback_does_not_change_a_text_with_an_identifier(text):
    assert grammar.slugify_identifier(text, fallback="") == grammar.slugify_identifier(text)
    assert grammar.slugify_identifier(text, fallback="") != ""


def test_a_leading_digit_still_gets_the_section_prefix():
    assert grammar.slugify_identifier("1st Term", fallback="") == "section-1st-term"


def test_the_fallback_is_keyword_only():
    with pytest.raises(TypeError):
        grammar.slugify_identifier("x", "y")  # type: ignore[misc]


# ── choose_problem ────────────────────────────────────────────────

_QUESTIONS = {
    "vat": {"type": "boolean"},
    "forum": {"type": "choice", "choices": {"courts": "Courts", "arbitration": "Arbitration"}},
    "broken": {"type": "choice", "choices": "nope"},
    "fee": {"type": "money"},
}


@pytest.mark.parametrize(
    "source",
    [
        "{{choose: vat, true=a, false=b}}",
        "{{choose: forum, courts=a, arbitration=b}}",
        "{{choose: forum, courts=a}}",
        "{{choose: vat, true=x, false=y, maybe=z}}",
        "{{choose: forum, courts=a, arbitration=b, note=c, other=d}}",
        "{{choose: vat, true=x, false=y, true=z}}",
        "{{choose: forum, courts=a, courts=b}}",
        "{{choose: fee, a=x, b=y}}",
        "{{choose: missing, true=x, false=y}}",
        "{{choose: broken, a=x}}",
        "{{choose: vat}}",
        "{{choose: }}",
        "{{choose: vat, true=x{{y, false=z}}",
    ],
)
def test_choose_problem_is_the_first_message_check_choose_records(source):
    from legaldown.validator.result import _Recorder

    [directive] = lex(source).directives
    recorded = _Recorder()
    check_choose(directive, _QUESTIONS, recorded)
    messages = [d.message for d in recorded.diagnostics if d.rule == "choose-invalid"]
    assert choose_problem(directive, _QUESTIONS) == (messages[0] if messages else None)


def test_choose_problem_is_none_for_a_valid_choose():
    [directive] = lex("{{choose: vat, true=a, false=b}}").directives
    assert grammar.choose_problem(directive, _QUESTIONS) is None
    [directive] = lex("{{choose: forum, courts=a}}").directives
    assert "arbitration" in (grammar.choose_problem(directive, _QUESTIONS) or "")


def test_choose_problem_without_questions():
    [directive] = lex("{{choose: vat, true=a, false=b}}").directives
    assert "declared boolean or choice question" in (choose_problem(directive, None) or "")


@pytest.mark.parametrize(
    "source",
    ["{{ref: vat}}", "{{placeholder: vat}}", '{{choose: vat, true="oops}}', "{{choose: vat, true=a, false=b"],
)
def test_choose_problem_refuses_what_is_no_well_formed_choose(source):
    [directive] = lex(source).directives[:1]
    with pytest.raises(ValueError, match="choose"):
        choose_problem(directive, _QUESTIONS)
    # The validator never reports one as choose-invalid.
    result = validate(parse(f"---\ntitle: T\nquestions:\n  vat:\n    type: boolean\n---\n\n# A\n\n{source}\n"))
    assert "choose-invalid" not in result.rules()


# ── find_markers ──────────────────────────────────────────────────


def test_find_markers_reads_with_the_package_lexer_by_default():
    document = parse("---\ntitle: T\n---\n\n# A {#a}\n\nText {#b} more `{#c}` {#d when=}\n\nEnd. {#e}\n")
    assert find_markers(document) == find_markers(document, lex)
    found = find_markers(document)
    # A heading's marker is not body text; a code span's is literal.
    assert [f.source for f in found] == ["{#b}", "{#d when=}", "{#e}"]
    assert [f.source for f in found if f.placed(False)] == ["{#e}"]
