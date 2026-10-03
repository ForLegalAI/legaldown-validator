"""The answers in the validation result and the locations of directives
(#31, #32, #33): ``DocumentIndex.blanks``, ``SectionIndexEntry.alternative``,
directive argument spans, ``Lexed.literals``, and ``iter_document_directives``."""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

import legaldown.validator
from legaldown import parse, validate
from legaldown.definitions import text_fragments
from legaldown.directives import Directive, iter_directives, lex
from legaldown.parser import (
    DirectiveLocation,
    FrontmatterError,
    collect_source_directives,
    iter_document_directives,
)
from legaldown.serializer import render_block
from legaldown.validator import Blank
from legaldown.validator.result import SectionIndexEntry

_FRONTMATTER = "---\ntitle: T\n---\n\n"


def _blanks(body: str, frontmatter: str = "") -> dict[str, Blank]:
    return validate(parse(f"---\ntitle: T\n{frontmatter}---\n\n{body}\n")).index.blanks


# ── Blanks (#31) ──────────────────────────────────────────────────


def test_blank_is_public_and_frozen():
    assert "Blank" in legaldown.validator.__all__
    assert legaldown.validator.Blank is Blank
    blank = Blank(id="a", type="text")
    assert (blank.fixed, blank.in_frontmatter) == ("", False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        blank.type = "date"  # type: ignore[misc]


def test_a_document_without_blanks_has_none():
    assert _blanks("# A\n\nText.") == {}


def test_blanks_are_in_the_order_of_their_first_occurrence_in_a_document_that_is_no_template():
    result = validate(
        parse(
            _FRONTMATTER
            + "# A\n\n{{placeholder: second}} {{placeholder: first, type=date}} {{placeholder: second}}\n"
        )
    )
    assert not result.index.is_template
    assert list(result.index.blanks) == ["second", "first"]
    assert result.index.blanks["first"] == Blank(id="first", type="date")
    assert result.index.blanks["second"] == Blank(id="second", type="text")


def test_a_blank_with_no_type_written_is_text():
    assert _blanks("# A\n\n{{placeholder: name}}")["name"].type == "text"


@pytest.mark.parametrize(
    "occurrences, fixed",
    [
        ("{{placeholder: p, type=money, currency=EUR}}", "EUR"),
        ("{{placeholder: p, type=money, currency=EUR}} {{placeholder: p, type=money, currency=EUR}}", "EUR"),
        ("{{placeholder: p, type=money}}", ""),
        ("{{placeholder: p, type=money, currency=EUR}} {{placeholder: p, type=money}}", ""),
        ("{{placeholder: p, type=money, currency=EUR}} {{placeholder: p, type=money, currency=USD}}", ""),
    ],
)
def test_a_money_blank_fixes_the_currency_all_its_occurrences_fix(occurrences, fixed):
    blank = _blanks(f"# A\n\n{occurrences}")["p"]
    assert (blank.type, blank.fixed) == ("money", fixed)


@pytest.mark.parametrize(
    "occurrences, fixed",
    [
        ("{{placeholder: p, type=duration, unit=D}}", "D"),
        ("{{placeholder: p, type=duration}} {{placeholder: p, type=duration, unit=D}}", ""),
        ("{{placeholder: p, type=duration, unit=D}} {{placeholder: p, type=duration, unit=W}}", ""),
    ],
)
def test_a_duration_blank_fixes_the_unit_all_its_occurrences_fix(occurrences, fixed):
    blank = _blanks(f"# A\n\n{occurrences}")["p"]
    assert (blank.type, blank.fixed) == ("duration", fixed)


def test_a_blank_has_the_type_of_its_first_occurrence_with_a_valid_one():
    blank = _blanks("# A\n\n{{placeholder: p, type=bogus}} {{placeholder: p, type=date}}")["p"]
    assert blank.type == "date"


def test_a_blank_takes_its_type_from_its_question():
    blanks = _blanks("# A\n\n{{placeholder: fee}}", "questions:\n  fee:\n    type: money\n")
    assert (blanks["fee"].type, blanks["fee"].fixed) == ("money", "")


def test_a_blank_is_in_frontmatter_when_one_occurrence_is():
    blanks = _blanks(
        "# A\n\n{{placeholder: subject}} {{placeholder: other}}",
        'subtitle: "{{placeholder: subject}}"\n',
    )
    assert blanks["subject"].in_frontmatter
    assert not blanks["other"].in_frontmatter
    assert list(blanks) == ["subject", "other"]  # frontmatter is read first


def test_a_placeholder_with_a_malformed_id_or_arguments_is_no_blank():
    assert _blanks("# A\n\n{{placeholder: Bad_Id}}") == {}
    assert _blanks('# A\n\n{{placeholder: "unterminated}}') == {}


# ── Alternatives (#31) ────────────────────────────────────────────

_QUESTIONS = "questions:\n  forum:\n    type: choice\n    choices:\n      courts: Courts\n      arbitration: Arbitration\n"


def test_a_section_sharing_its_predecessors_number_is_an_alternative():
    body = (
        "# Disputes {#disputes when=forum:courts}\n\n## Venue\n\nText.\n\n"
        "# Disputes {#disputes when=forum:arbitration}\n\n## Seat\n\nText.\n\n# Notices\n\nText."
    )
    result = validate(parse(f"---\ntitle: T\n{_QUESTIONS}---\n\n{body}\n"))
    sections = result.index.sections
    assert [s.number for s in sections] == ["1", "1.1", "1", "1.1", "2"]
    assert [s.alternative for s in sections] == [False, False, True, False, False]


def test_sections_with_one_identifier_that_can_appear_together_are_not_alternatives():
    result = validate(parse(_FRONTMATTER + "# A {#same}\n\nText.\n\n# A {#same}\n\nText.\n"))
    assert not any(s.alternative for s in result.index.sections)
    assert [s.number for s in result.index.sections] == ["1", "2"]


def test_alternative_is_the_last_field_and_defaults_to_false():
    entry = SectionIndexEntry("T", "t", "t", 1, "1")
    assert entry.alternative is False
    assert [f.name for f in dataclasses.fields(SectionIndexEntry)][-1] == "alternative"


# ── Directive argument spans (#32) ────────────────────────────────

_DIRECTIVES = [
    "{{ref: services}}",
    "{{ref:services}}",
    "{{ref:   services  }}",
    '{{ref: "services"}}',
    '{{ref:  "a, b"  }}',
    r'{{ref: "say \"hi\" \\ there"}}',
    "{{term: foo, label=Foo}}",
    '{{term: foo, label="Foo, Inc."}}',
    "{{term: foo, label=}}",
    "{{term: foo, label=  , note=x}}",
    "{{money: 100, currency=EUR, note=n}}",
    "{{money: 100, currency=EUR, currency=USD}}",
    "{{placeholder: p, type=money, currency=EUR}}",
    "{{def:}}",
    "{{date:}}",
    "{{unknown: a b, x=y z}}",
    '{{choose: forum, courts="In court", arbitration=Arbitrate}}',
]


def _decode(span_text: str, param: str | None) -> str | None:
    """What the lexer reads in *span_text* as the value of *param* (the
    positional value when None)."""
    argument = span_text if param is None else f"{param}={span_text}"
    directive = lex("{{x: " + argument + "}}").directives[0]
    return directive.positional if param is None else directive.params.get(param)


@pytest.mark.parametrize("source", _DIRECTIVES)
def test_spans_cover_the_value_as_written_and_decode_to_it(source):
    directive = lex(source).directives[0]
    assert not directive.malformed
    if directive.positional is None:
        assert directive.positional_span is None
    else:
        start, end = directive.positional_span
        assert _decode(source[start:end], None) == directive.positional
    assert list(directive.param_spans) == list(directive.params)
    for param, (start, end) in directive.param_spans.items():
        assert _decode(source[start:end], param) == directive.params[param]


def test_spans_cover_a_quoted_value_with_its_quotes_and_an_unquoted_one_trimmed():
    text = 'a {{term:  "x, y"  , label=  Foo  }} b'
    directive = lex(text).directives[0]
    start, end = directive.positional_span
    assert text[start:end] == '"x, y"'
    start, end = directive.param_spans["label"]
    assert text[start:end] == "Foo"


def test_a_repeated_parameter_has_the_span_of_its_first_occurrence():
    text = "{{money: 1, currency=EUR, currency=USD}}"
    directive = lex(text).directives[0]
    assert directive.duplicates == ("currency",)
    start, end = directive.param_spans["currency"]
    assert text[start:end] == "EUR"


def test_a_parameter_without_a_value_has_an_empty_span_where_it_goes():
    text = "{{term: foo, label=, note=x}}"
    directive = lex(text).directives[0]
    start, end = directive.param_spans["label"]
    assert start == end
    assert text[:start].endswith("label=")
    assert directive.params["label"] == ""


def test_spans_are_offsets_into_the_lexed_text():
    text = "Before `code` {{ref: a}} and {{ref: b}}."
    first, second = lex(text).directives
    assert text[slice(*first.positional_span)] == "a"
    assert text[slice(*second.positional_span)] == "b"


@pytest.mark.parametrize(
    "text",
    ['{{ref: "unterminated}}', "{{ref: a, b}}", "{{ref: a, x=1, b}}", "{{ref: a", "{{ref: a,, b}}", "{{ref: a} {{ref: b}}"],
)
def test_a_malformed_directive_has_no_spans(text):
    directive = lex(text).directives[0]
    assert directive.malformed
    assert directive.positional_span is None
    assert directive.param_spans == {}


def test_the_span_fields_come_last_and_default_so_a_directive_can_still_be_built_positionally():
    directive = Directive("ref", "a", {}, (), "", 0, 8, "{{ref: a}}")
    assert directive.positional_span is None and directive.param_spans == {}
    names = [f.name for f in dataclasses.fields(Directive)]
    assert names[-2:] == ["positional_span", "param_spans"]
    assert directive.param_spans is not Directive("ref", "a", {}, (), "", 0, 8, "{{ref: a}}").param_spans


# ── Literal regions (#32) ─────────────────────────────────────────


def test_literals_list_the_comments_and_code_spans_the_lexer_blanked():
    text = "a `{{x: 1}}` b <!-- {{y: 2}} --> c ``d ` e`` {{ref: z}}"
    lexed = lex(text)
    assert [(kind, text[start:end]) for kind, start, end in lexed.literals] == [
        ("code", "`{{x: 1}}`"),
        ("comment", "<!-- {{y: 2}} -->"),
        ("code", "``d ` e``"),
    ]
    for _kind, start, end in lexed.literals:
        assert lexed.view[start:end].strip() == ""
        assert len(lexed.view) == len(text)
    assert [d.name for d in lexed.directives] == ["ref"]


def test_a_comment_or_backtick_inside_a_directive_is_no_literal_region():
    text = '{{note: "`<!--"}} `real`'
    assert [(kind, text[start:end]) for kind, start, end in lex(text).literals] == [("code", "`real`")]


def test_unclosed_comments_and_backtick_runs_are_no_literals_and_escaped_ones_neither():
    assert lex("a <!-- open {{ref: x}}").literals == []
    assert lex("a ` open").literals == []
    assert lex(r"a \`{{ref: x}}`").literals == []


def test_literals_default_empty_so_lexed_can_still_be_built_positionally():
    from legaldown.directives import Lexed

    assert Lexed([], [], "").literals == []
    assert lex("plain").literals == []


# ── Directives with locations (#33) ───────────────────────────────

_BODY = """Preamble {{date: 2026-01-01}}.

# First {#first}

See {{ref: second}} and `{{ref: hidden}}` <!-- {{ref: hidden}} -->.

> Quoted {{term: foo, label=Foo}}.
>
> ```
> {{ref: in-code}}
> ```

- Item one {{ref: third}}
  - Nested {{term: bar}}

```
{{ref: fenced}}
```

{{ref: lifted}} after {{date: 2026-02-02}}

Before {{term: tgt}} done

# Second {#second}

| H |
|---|
| {{ref: cell}} |

{{ref: "a b"}}
"""


def test_directives_come_in_document_order_with_where_they_are():
    document = parse(_FRONTMATTER + _BODY)
    found = list(iter_document_directives(document))
    assert all(isinstance(loc, DirectiveLocation) for loc in found)
    assert [(loc.directive.name, loc.directive.positional) for loc in found] == [
        ("date", "2026-01-01"),
        ("ref", "second"),
        ("term", "foo"),
        ("ref", "third"),
        ("term", "bar"),
        ("ref", "lifted"),
        ("date", "2026-02-02"),
        ("term", "tgt"),
        ("ref", "cell"),
        ("ref", "a b"),
    ]
    assert found[0][:3] == (None, 0, 0)  # the preamble has section None
    assert [loc.section for loc in found] == [None] + [0] * 7 + [1, 1]


def test_a_directive_is_at_its_offset_in_its_fragment():
    from legaldown import block_fragments

    document = parse(_FRONTMATTER + _BODY)
    blocks = {(s, b): block for s, b, block in document.iter_indexed_blocks()}
    for loc in iter_document_directives(document):
        if loc.fragment is None:
            continue
        text = block_fragments(blocks[loc.section, loc.block])[loc.fragment].text
        assert text[loc.directive.start:loc.directive.end] == loc.directive.source


def test_a_lifted_ref_or_term_has_no_fragment_and_comes_between_its_prefix_and_suffix():
    document = parse(_FRONTMATTER + "# A\n\nBefore {{date: 2026-01-01}} {{ref: x}} after {{date: 2026-02-02}}\n")
    block = document.sections[0].blocks[0]
    assert block.kind == "ref"
    found = list(iter_document_directives(document))
    assert [(loc.directive.name, loc.fragment) for loc in found] == [("date", 0), ("ref", None), ("date", 1)]
    lifted = found[1].directive
    assert lifted.source == "{{ref: x}}"
    assert render_block(dataclasses.replace(block, prefix="", suffix="")) == lifted.source
    assert not lifted.malformed and lifted.positional == "x"


def test_a_lifted_term_carries_its_label_and_a_target_that_needs_quotes_is_decoded():
    document = parse(_FRONTMATTER + '# A\n\n{{term: "a, b", label=Foo}}\n')
    (loc,) = iter_document_directives(document)
    assert loc.fragment is None
    assert (loc.directive.positional, loc.directive.params) == ("a, b", {"label": "Foo"})
    start, end = loc.directive.positional_span
    assert loc.directive.source[start:end] == '"a, b"'


def test_a_lifted_directive_with_no_target_is_not_read():
    from legaldown import document_from_dict

    document = document_from_dict(
        {"metadata": {"title": "T"}, "sections": [{"title": "A", "blocks": [{"kind": "ref", "target": ""}]}]}
    )
    assert list(iter_document_directives(document)) == []


def test_a_malformed_directive_is_yielded_too():
    document = parse(_FRONTMATTER + '# A\n\nText {{date: "oops}} more\n')
    (loc,) = iter_document_directives(document)
    assert loc.directive.malformed


def test_directives_agree_with_what_the_validator_reads():
    """The validator's reference diagnostics come from the same reading."""
    document = parse(_FRONTMATTER + "# A {#a}\n\nSee {{ref: a}} and {{ref: nowhere}} and {{term: nobody}}.\n")
    result = validate(document)
    targets = {loc.directive.positional for loc in iter_document_directives(document) if loc.directive.name == "ref"}
    assert targets == {"a", "nowhere"}
    assert result.rules("error") == {"ref-broken", "term-undefined"}


def test_collect_source_directives_is_built_on_it():
    document = parse(_FRONTMATTER + _BODY)
    refs, terms = collect_source_directives(document)
    assert refs == {"second", "third", "lifted", "cell", "a b"}
    assert terms == {"foo", "bar", "tgt"}


def test_collect_source_directives_skips_malformed_and_empty_targets():
    document = parse(_FRONTMATTER + '# A\n\n{{ref: "oops}} {{ref:}} {{term:}} {{ref: ok}}\n')
    assert collect_source_directives(document) == ({"ok"}, set())


# ── Over the specification fixtures ───────────────────────────────

_FIXTURES = Path(os.environ.get("LEGALDOWN_FIXTURES_DIR", ""))
_FIXTURE_DOCUMENTS = sorted(_FIXTURES.rglob("*.lgd")) if _FIXTURES.is_dir() else []


def _collected_before(document) -> tuple[set[str], set[str]]:
    """The ref and term targets as ``collect_source_directives`` found them before it was built
    on ``iter_document_directives``: from the lifted fields, and the lexed text fragments."""
    refs: set[str] = set()
    terms: set[str] = set()
    for _section, _index, block in document.iter_blocks():
        if block.kind == "ref" and block.target:
            refs.add(block.target)
        if block.kind == "term" and block.target:
            terms.add(block.target)
        for fragment in text_fragments(block):
            for directive in iter_directives(fragment):
                if directive.malformed or not directive.positional:
                    continue
                if directive.name == "ref":
                    refs.add(directive.positional)
                elif directive.name == "term":
                    terms.add(directive.positional)
    return refs, terms


def _decoded(source: str, span: tuple[int, int], param: str | None) -> str | None:
    """The value the lexer reads in ``source[span]``, as *param*'s (the positional when None)."""
    return _decode(source[span[0]:span[1]], param)


@pytest.mark.skipif(not _FIXTURE_DOCUMENTS, reason="LEGALDOWN_FIXTURES_DIR not set (specification fixtures)")
@pytest.mark.parametrize(
    "path", _FIXTURE_DOCUMENTS, ids=lambda p: "/".join(p.parts[-3:]),
)
def test_every_fixture_document_reads_consistently(path):
    try:
        document = parse(path.read_text(encoding="utf-8"), filename=path.name)
    except FrontmatterError:
        pytest.skip("unreadable frontmatter")
    # The locations agree with the validator's reading of the same document.
    for loc in iter_document_directives(document):
        directive = loc.directive
        if directive.malformed:
            assert directive.positional_span is None and directive.param_spans == {}
            continue
        # Spans decode, through the lexer, to the values.
        if directive.positional is None:
            assert directive.positional_span is None
        else:
            assert _decoded(directive.source, _relative(directive, directive.positional_span), None) == directive.positional
        for param, span in directive.param_spans.items():
            assert _decoded(directive.source, _relative(directive, span), param) == directive.params[param]
    assert collect_source_directives(document) == _collected_before(document)

    # The blanks agree with the placeholders the validator met.
    result = validate(document)
    placeholders = result.index.values.placeholders
    for pid, blank in result.index.blanks.items():
        assert blank.id == pid
        met = [ptype for met_id, ptype in placeholders if met_id == pid]
        assert met, pid
        assert blank.type in met or blank.type == "text"
        if blank.fixed:
            assert blank.type in ("money", "duration")
    assert set(result.index.blanks) <= {pid for pid, _ptype in placeholders}
    for entry in result.index.sections:
        assert isinstance(entry.alternative, bool)


def _relative(directive: Directive, span: tuple[int, int] | None) -> tuple[int, int]:
    """*span*, which is an offset into the lexed text, as one into ``directive.source``."""
    assert span is not None
    return span[0] - directive.start, span[1] - directive.start
