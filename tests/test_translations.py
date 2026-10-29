"""Language codes and translations at the Core level (§3.2, §16.6, §16.7, §17.2):
``language-code-invalid`` and ``authoritative-not-declared`` (#116), and the two single-file
translation rules, ``translation-implicit-id`` and ``translation-authoritative-absent``.

A value that is no ISO 639-1 code is no language: the rules that compare languages judge only
the codes that pass, so one typo draws one Warning. The translation rules need the document
alone: the files its ``translations`` link are Full (§17.4), and ``ldv-check-skipped`` says so."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from legaldown import Document, parse, validate
from legaldown.grammar import ISO_639_1_CODES
from legaldown.parser import FrontmatterError

_RULES = {"language-code-invalid", "authoritative-not-declared"}
#: The five rules of #116, all Core (§17.2).
_NEW_RULES = _RULES | {
    "translation-implicit-id", "translation-authoritative-absent", "definition-used-before-declaration",
}


def _corpus():
    """The valid and assembly fixtures and the specification's examples, when the corpus is there."""
    root = os.environ.get("LEGALDOWN_FIXTURES_DIR", "")
    if not root or not Path(root).is_dir():
        return []
    spec = Path(root).parent
    paths = [
        *sorted((spec / "fixtures" / "valid").glob("*.lgd")),
        *sorted((spec / "fixtures" / "assembly").rglob("*.lgd")),
        *sorted((spec / "examples").rglob("*.lgd")),
    ]
    return [pytest.param(path, id=path.relative_to(spec).as_posix()) for path in paths]


def _source(front: str = "", body: str = "# Scope {#scope}\n\nText.\n") -> str:
    return f"---\ntitle: Fixture\n{front}---\n\n{body}"


def _found(front: str = "", body: str = "# Scope {#scope}\n\nText.\n", rules: set[str] = _RULES):
    """The (rule, level, line) of each diagnostic of *rules* that *front* draws; the
    frontmatter's first line is 2."""
    result = validate(parse(_source(front, body), filename="t.lgd"))
    return [(d.rule, d.level, d.line) for d in result.diagnostics if d.rule in rules]


# ── language-code-invalid ─────────────────────────────────────────


def test_the_fixture_case_is_reported_at_the_language():
    # fixtures/invalid/language-code-invalid/basic.lgd
    assert _found("language: english\n") == [("language-code-invalid", "warning", 3)]


@pytest.mark.parametrize("front", ["", "language: en\n", "language: cs\n", "language: no\n", "language: bh\n"])
def test_a_language_code_draws_nothing(front):
    assert _found(front) == []


@pytest.mark.parametrize(
    "value", ["english", "EN", "En", "en-US", "en_US", "eng", "e", "12", "[en]", "x y"]
)
def test_a_value_that_is_no_lowercase_iso_639_1_code_draws_a_warning(value):
    # Two lowercase letters are not enough (`qq`), and neither is an alpha-3 or a region tag.
    assert _found(f"language: {value}\n") == [("language-code-invalid", "warning", 3)]
    assert _found("language: qq\n") == [("language-code-invalid", "warning", 3)]


def test_there_are_184_codes_and_all_are_accepted_wherever_a_code_is_written():
    assert len(ISO_639_1_CODES) == 184
    assert all(len(code) == 2 and code.isascii() and code.islower() for code in ISO_639_1_CODES)
    codes = sorted(ISO_639_1_CODES)
    keys = "".join(f"  {code}: {code}.lgd\n" for code in codes)
    for code in codes:
        assert _found(f"language: {code}\nauthoritative: {code}\n") == [], code
    assert _found(f"language: en\nauthoritative: en\ntranslations:\n{keys}") == []


def test_an_authoritative_language_that_is_no_code_is_reported_at_its_line():
    front = "language: en\nauthoritative: English\ntranslations:\n  cs: x.lgd\n"
    # The comparison is not made with a value that is no language: one Warning.
    assert _found(front) == [("language-code-invalid", "warning", 4)]
    assert _found("authoritative: ''\n") == []


def test_each_bad_translations_key_is_reported_at_its_own_line():
    front = "language: en\nauthoritative: en\ntranslations:\n  cs: x.cs.lgd\n  czech: x.czech.lgd\n  DE: x.de.lgd\n"
    assert _found(front) == [("language-code-invalid", "warning", 7), ("language-code-invalid", "warning", 8)]


def test_a_padded_translations_key_is_found_by_its_stripped_form():
    front = "language: en\nauthoritative: en\ntranslations:\n  ' fr ': x.fr.lgd\n  ' english ': x.en.lgd\n"
    assert _found(front) == [("language-code-invalid", "warning", 7)]


def test_the_message_names_the_field_and_the_value():
    (warning,) = [
        d for d in validate(parse(_source("language: english\n"), filename="t.lgd")).diagnostics
        if d.rule == "language-code-invalid"
    ]
    assert warning.message == (
        "The language 'english' must be an ISO 639-1 language code: two lowercase letters, such as 'en' or 'cs'."
    )


def test_a_placeholder_in_a_code_is_the_placeholder_rules_alone():
    # `placeholder-in-structural-field` says what is wrong; the value is no code to judge.
    for front in ("authoritative: '{{placeholder: p}}'\n", "language: '{{placeholder: p}}'\n"):
        result = validate(parse(_source(front), filename="t.lgd"))
        rules = {d.rule for d in result.diagnostics}
        assert "placeholder-in-structural-field" in rules
        assert not rules & _RULES


def test_unreadable_frontmatter_and_a_document_without_it_draw_none():
    unreadable = parse(_source("language: [\n"), filename="t.lgd", on_frontmatter_error="keep")
    assert not {d.rule for d in validate(unreadable).diagnostics} & _RULES
    result = validate(parse("# Scope {#scope}\n\nText.\n", filename="t.lgd"))
    assert not {d.rule for d in result.diagnostics} & _RULES


# ── authoritative-not-declared ────────────────────────────────────


def test_the_fixture_case_is_reported_at_the_authoritative():
    # fixtures/invalid/authoritative-not-declared/basic.lgd: the linked file is never read.
    front = "language: en\nauthoritative: de\ntranslations:\n  fr: doc-fr.lgd\n"
    assert _found(front) == [("authoritative-not-declared", "warning", 4)]


@pytest.mark.parametrize(
    "front",
    [
        "language: en\nauthoritative: en\n",
        "language: en\nauthoritative: fr\ntranslations:\n  fr: x.fr.lgd\n",
        "language: fr\nauthoritative: en\ntranslations:\n  en: x.en.lgd\n",
        "language: en\nauthoritative: de\ntranslations:\n  fr: x.fr.lgd\n  de: x.de.lgd\n  it: x.it.lgd\n",
        "language: en\ntranslations:\n  fr: x.fr.lgd\n",  # no authoritative
        "language: en\nauthoritative: ''\n",
    ],
)
def test_an_authoritative_language_that_is_declared_draws_nothing(front):
    assert _found(front) == []


@pytest.mark.parametrize("authoritative", ["fr", "en"])
def test_without_a_language_the_authoritative_must_be_a_key_whichever_code_it_is(authoritative):
    # The model's "en" is its own default, not a declaration (§3.2 has none): `en` is no more
    # declared by a missing `language` than `fr` is.
    front = f"authoritative: {authoritative}\n"
    assert _found(front) == [("authoritative-not-declared", "warning", 3)]
    (warning,) = [
        d for d in validate(parse(_source(front), filename="t.lgd")).diagnostics
        if d.rule == "authoritative-not-declared"
    ]
    assert warning.message == (
        f"authoritative '{authoritative}' is neither the document's language (not declared) "
        f"nor a translations key (none)."
    )
    assert _found(f"authoritative: {authoritative}\ntranslations:\n  {authoritative}: x.lgd\n") == []


def test_the_message_names_the_language_and_the_keys():
    front = "language: en\nauthoritative: de\ntranslations:\n  fr: x.fr.lgd\n  it: x.it.lgd\n"
    (warning,) = [
        d for d in validate(parse(_source(front), filename="t.lgd")).diagnostics
        if d.rule == "authoritative-not-declared"
    ]
    assert warning.message == (
        "authoritative 'de' is neither the document's language ('en') nor a translations key ('fr', 'it')."
    )


def test_a_translation_with_no_translations_is_the_authoritative_rules_alone():
    # `fr` against `en`, linking nothing: the authoritative names no declared language. The
    # document is not a translation for the identifier rules (it links no file).
    result = validate(parse(_source("language: fr\nauthoritative: en\n", "# Scope\n\nText.\n"), filename="t.lgd"))
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule.startswith(("translation-", "authoritative-"))] == [
        ("authoritative-not-declared", 4)
    ]


@pytest.mark.parametrize(
    "front",
    [
        "language: english\nauthoritative: en\n",  # the language is no code
        "language: EN\nauthoritative: en\n",
        "language: en-US\nauthoritative: en\ntranslations:\n  fr: x.fr.lgd\n",
        "language: en\nauthoritative: EN\n",  # the authoritative is no code
    ],
)
def test_a_value_that_is_no_code_draws_language_code_invalid_alone(front):
    rules = {d.rule for d in validate(parse(_source(front, "# Scope\n\nText.\n"), filename="t.lgd")).diagnostics}
    assert rules & {"language-code-invalid", "authoritative-not-declared"} == {"language-code-invalid"}
    assert not rules & {"translation-implicit-id", "translation-authoritative-absent"}


def test_a_translations_key_that_is_no_code_is_not_compared_with_the_authoritative():
    # `english` might have been `en`: authoritative-not-declared would say a second thing about
    # the same value. (The document is a translation all the same: `fr` of `en`, linking a file.)
    front = "language: fr\nauthoritative: en\ntranslations:\n  english: x.lgd\n"
    assert _found(front) == [("language-code-invalid", "warning", 6)]
    assert _identifiers(front, "# Definitions\n") == [("translation-implicit-id", "error", 9)]


# ── translation-implicit-id and translation-authoritative-absent ───

_IDENTIFIERS = {"translation-implicit-id", "translation-authoritative-absent"}
_FR = "language: fr\nauthoritative: en\ntranslations:\n  en: doc-en.lgd\n"
_EN = "language: en\nauthoritative: en\ntranslations:\n  fr: doc-fr.lgd\n"
_DEF = "\"Services\" {{def: services}} means x. See {{term: services}}.\n"


def _identifiers(front: str, body: str):
    return _found(front, body, _IDENTIFIERS)


def test_the_fixture_case_is_reported_for_the_translation_at_its_heading():
    # fixtures/invalid/translation-implicit-id/doc-fr.lgd (the case's entry is doc-en.lgd, the
    # primary, which a Core validator finds clean: the harness validates doc-fr.lgd, CONFORMANCE.md).
    body = "# Definitions\n\n« Services » {{def: services}} désigne x. Voir {{term: services}}.\n"
    assert _identifiers(_FR, body) == [("translation-implicit-id", "error", 9)]
    assert _identifiers(_EN, "# Definitions {#definitions}\n\n" + _DEF) == []


def test_the_fixture_case_for_an_absent_authoritative_is_reported_for_the_document_itself():
    # fixtures/invalid/translation-authoritative-absent/doc-en.lgd
    front = "language: en\ntranslations:\n  fr: doc-fr.lgd\n"
    assert _identifiers(front, "# Definitions\n\n" + _DEF) == [("translation-authoritative-absent", "warning", 8)]


def test_the_pair_of_section_14_2_is_clean():
    # contract-en.lgd and contract-fr.lgd: identifiers explicit in both, `{{def:}}` included.
    body = "# Definitions {#definitions}\n\n\"Confidential Information\" {{def: confidential-info}} means any information.\n"
    assert _identifiers("language: en\ntranslations:\n  fr: contract-fr.lgd\nauthoritative: en\n", body) == []
    assert _identifiers("language: fr\ntranslations:\n  en: contract-en.lgd\nauthoritative: en\n", body) == []


def test_every_heading_and_def_without_an_identifier_is_one_error_at_its_line():
    body = (
        "# Definitions {#definitions}\n\n"
        "« Services » {{def: services}} désigne x.\n\n"
        "« Droit applicable » {{def:}} désigne y.\n\n"
        "# Droit applicable\n\n"
        "## Sous-section {#sub}\n\n"
        "## Autre\n"
    )
    # The headings, then the definitions.
    found = _identifiers(_FR, body)
    assert found == [
        ("translation-implicit-id", "error", 15),
        ("translation-implicit-id", "error", 19),
        ("translation-implicit-id", "error", 13),
    ]
    assert all(rule == "translation-implicit-id" and level == "error" for rule, level, _line in found)


def test_a_def_without_an_identifier_is_an_error_though_its_heading_has_one():
    body = "# Definitions {#definitions}\n\n« Services » {{def:}} désigne x.\n"
    assert _identifiers(_FR, body) == [("translation-implicit-id", "error", 11)]


def test_the_message_names_the_node_and_the_remedy():
    body = "# Définitions\n\n« Services » {{def:}} désigne x.\n"
    result = validate(parse(_source(_FR, body), filename="t.lgd"))
    heading, definition = [d for d in result.diagnostics if d.rule == "translation-implicit-id"]
    assert heading.message == (
        "Heading 'Définitions' has no explicit identifier, and the document is a translation (fr, of the "
        "authoritative en): write the primary's identifier, {#id} (§14.2)."
    )
    assert definition.message == (
        "Definition 'Services' has no explicit identifier ('services' is generated from the term), and the "
        "document is a translation (fr, of the authoritative en): write the primary's identifier, "
        "{{def: id}} (§14.2)."
    )


def test_the_primary_is_not_a_translation():
    assert _identifiers(_EN, "# Definitions\n\n" + _DEF.replace("{{def: services}}", "{{def:}}")) == [], (
        "the primary's identifiers are generated, and mirrored into the translation (§14.2)"
    )


def test_the_same_language_as_the_authoritative_is_not_a_translation():
    front = "language: en\nauthoritative: en\ntranslations:\n  en: other.lgd\n"
    assert _identifiers(front, "# Definitions\n") == []


def test_a_document_that_links_nothing_is_no_translation():
    # §16.7 is about linked files: `fr` against `en` with no `translations` is the authoritative
    # rule's Warning (above) alone, not an Error on every heading.
    assert _identifiers("language: fr\nauthoritative: en\n", "# Definitions\n\n" + _DEF.replace("{{def: services}}", "{{def:}}")) == []


def test_without_a_language_written_the_document_is_no_translation():
    # The model's "en" is its own default: a document that does not say its language is not
    # said to differ from `authoritative: fr` (its Warning is authoritative-not-declared).
    front = "authoritative: fr\ntranslations:\n  fr: doc-fr.lgd\n"
    assert _identifiers(front, "# Definitions\n") == []
    assert _found(front) == []


@pytest.mark.parametrize(
    "front",
    [
        "language: english\nauthoritative: en\ntranslations:\n  en: a.lgd\n",
        "language: fr\nauthoritative: EN\ntranslations:\n  en: a.lgd\n",
        "language: en-US\nauthoritative: en\ntranslations:\n  fr: a.lgd\n",
        "language: fr\nauthoritative: eng\ntranslations:\n  en: a.lgd\n",
    ],
)
def test_a_code_that_is_no_language_makes_no_translation(front):
    body = "# Definitions\n\n« Services » {{def:}} désigne x.\n"
    assert _identifiers(front, body) == []
    assert [rule for rule, _level, _line in _found(front)] == ["language-code-invalid"]


def test_an_invalid_identifier_is_anchor_format_not_an_implicit_one():
    body = "# Définitions {#Bad_Id}\n\n« Services » {{def: Bad_Id}} désigne x.\n"
    result = validate(parse(_source(_FR, body), filename="t.lgd"))
    rules = {d.rule for d in result.diagnostics}
    assert "anchor-format" in rules and "translation-implicit-id" not in rules


def test_a_duplicate_generated_identifier_is_an_error_at_each_declaration():
    body = (
        "# Définitions {#definitions}\n\n« Services » {{def:}} désigne x.\n\n« Services » {{def:}} désigne y.\n"
    )
    result = validate(parse(_source(_FR, body), filename="t.lgd"))
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule in {*_IDENTIFIERS, "def-duplicate-id"}] == [
        ("def-duplicate-id", 13),
        ("translation-implicit-id", 11),
        ("translation-implicit-id", 13),
    ]


def test_a_heading_in_a_quote_or_a_list_is_no_section():
    body = "# Définitions {#definitions}\n\n> ## Quoted\n\n- ## Item\n"
    assert _identifiers(_FR, body) == []


def test_a_document_without_a_source_map_is_judged_by_the_model():
    document = Document.from_dict({
        "metadata": {"title": "Contrat", "language": "fr", "authoritative": "en", "translations": {"en": "doc-en.lgd"}},
        "sections": [{"title": "Définitions", "identifier": "", "blocks": []}, {"title": "Objet", "identifier": "objet"}],
    })
    found = [(d.rule, d.level, d.line) for d in validate(document).diagnostics if d.rule in _IDENTIFIERS]
    assert found == [("translation-implicit-id", "error", None)]


def test_a_fragment_is_no_translation():
    document = parse("# Scope\n\nText.\n")
    assert [d.rule for d in validate(document, fragment=True).diagnostics if d.rule in _IDENTIFIERS] == []


# ── translation-authoritative-absent ──


def test_without_an_authoritative_each_node_without_an_identifier_is_a_warning():
    front = "language: en\ntranslations:\n  fr: doc-fr.lgd\n"
    body = (
        "# Scope\n\n" + _DEF.replace("{{def: services}}", "{{def:}}")
        + "\n## Sub\n\n" + _DEF.replace("services", "other")  # explicit
        + "\n# Last {#last}\n"
    )
    assert _identifiers(front, body) == [
        ("translation-authoritative-absent", "warning", 8),
        ("translation-authoritative-absent", "warning", 12),
        ("translation-authoritative-absent", "warning", 10),
    ]


def test_both_files_of_a_pair_without_an_authoritative_warn_for_their_own_nodes():
    for front in ("language: en\ntranslations:\n  fr: doc-fr.lgd\n", "language: fr\ntranslations:\n  en: doc-en.lgd\n"):
        assert [rule for rule, _level, _line in _identifiers(front, "# Definitions\n")] == ["translation-authoritative-absent"]


def test_the_warning_says_how_to_end_it():
    front = "language: en\ntranslations:\n  fr: doc-fr.lgd\n"
    result = validate(parse(_source(front, "# Definitions\n"), filename="t.lgd"))
    (warning,) = [d for d in result.diagnostics if d.rule == "translation-authoritative-absent"]
    assert warning.message == (
        "Heading 'Definitions' has no explicit identifier, and authoritative is not declared, so the primary "
        "cannot be told from its translations: declare authoritative, and write the primary's "
        "identifier, {#id} (§14.2)."
    )


def test_explicit_identifiers_and_no_translations_draw_no_warning():
    front = "language: en\ntranslations:\n  fr: doc-fr.lgd\n"
    assert _identifiers(front, "# Definitions {#definitions}\n\n" + _DEF) == []
    assert _identifiers("language: en\n", "# Definitions\n\n" + _DEF.replace("{{def: services}}", "{{def:}}")) == []
    assert _identifiers("language: en\ntranslations: {}\n", "# Definitions\n") == []


def test_an_authoritative_that_is_no_code_ends_neither_rule():
    # Declared, so not absent; no language, so no translation: language-code-invalid alone.
    front = "language: en\nauthoritative: English\ntranslations:\n  fr: doc-fr.lgd\n"
    assert _identifiers(front, "# Definitions\n") == []


def test_a_mistyped_authoritative_makes_the_primary_no_translation():
    # `de` is not a language of the group: authoritative-not-declared is the finding, and it is not
    # said besides that every heading of the document, which may well be the primary, is an Error.
    front = "language: en\nauthoritative: de\ntranslations:\n  fr: a.lgd\n"
    body = "# Definitions\n\n" + _DEF.replace("{{def: services}}", "{{def:}}")
    assert _identifiers(front, body) == []
    assert _found(front) == [("authoritative-not-declared", "warning", 4)]
    # ... where `de` is a key it is the primary, and this document its translation.
    assert [rule for rule, _level, _line in _identifiers(front + "  de: b.lgd\n", body)] == ["translation-implicit-id"] * 2


def test_the_authoritative_must_be_a_key_for_a_translation():
    body = "# Definitions\n"
    assert _identifiers("language: fr\nauthoritative: en\ntranslations:\n  de: a.lgd\n", body) == []
    assert _identifiers("language: fr\nauthoritative: en\ntranslations:\n  en: a.lgd\n", body) != []


def test_a_definition_in_a_drafting_note_is_no_missing_identifier():
    # Assembly removes the note, and drafting-note-def says what is wrong with it.
    body = "# Définitions {#definitions}\n\n> [!DRAFTING]\n> « Services » {{def:}} désigne x.\n\n« Autre » {{def:}} désigne y.\n"
    result = validate(parse(_source(_FR, body), filename="t.lgd"))
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule in {*_IDENTIFIERS, "drafting-note-def"}] == [
        ("translation-implicit-id", 14),
        ("drafting-note-def", 12),
    ]


def test_a_generated_identifier_that_anchor_format_rejects_is_not_also_implicit():
    # A generated identifier is always valid (§5.3), so this takes a ref built by hand.
    from legaldown.definitions import DefinitionRef
    from legaldown.positions import Locator
    from legaldown.validator.core import _check_implicit_identifiers
    from legaldown.validator.result import _Recorder

    document = parse(_source(_FR, "# Définitions {#definitions}\n\n« Services » {{def:}} désigne x.\n"), filename="t.lgd")
    good = DefinitionRef("services", "Services", 0, 0, 0, True, True, offset=0)
    bad = DefinitionRef("Bad_Id", "Bad", 0, 0, 0, True, True, offset=0)
    result = _Recorder()
    _check_implicit_identifiers(document, [good, bad], Locator(document), lambda _ref: None, result)
    assert [d.message[:21] for d in result.diagnostics] == ["Definition 'Services'"]


def test_an_entry_whose_key_or_value_is_blank_is_ignored():
    # The frontmatter reader drops an entry that names nothing: `""`, `"  "` and `' '` keys alike (so the
    # rules see none), and a value that is blank once stripped (`de: ' '`), as an empty one.
    for key in ('""', '"  "', "' '"):
        front = f"language: de\nauthoritative: cs\ntranslations:\n  {key}: y.lgd\n"
        assert parse(_source(front), filename="t.lgd").metadata.translations == {}, key
        assert _found(front) == [("authoritative-not-declared", "warning", 4)], key
        assert _identifiers(front, "# Definitions\n") == [], key
    assert parse(_source("translations:\n  fr:\n  de: ' '\n  cs: c.lgd\n")).metadata.translations == {"cs": "c.lgd"}
    assert parse(_source("field_types:\n  '  ': d\n  a: ' '\n  b: d\n")).metadata.field_types == {"b": "d"}
    # ... a blank entry beside a valid one changes nothing about that one.
    front = "language: de\nauthoritative: cs\ntranslations:\n  '  ': y.lgd\n  cs: z.lgd\n"
    assert _found(front) == []
    assert _identifiers(front, "# Definitions\n") == [("translation-implicit-id", "error", 10)]
    # A blank value does not make the document link anything.
    assert _identifiers("language: de\nauthoritative: cs\ntranslations:\n  cs: ' '\n", "# Definitions\n") == []


def test_keys_that_are_alike_once_stripped_are_one_and_the_last_stays():
    front = "language: de\nauthoritative: fr\ntranslations:\n  fr: a.lgd\n  ' fr': b.lgd\n"
    assert parse(_source(front)).metadata.translations == {"fr": "b.lgd"}
    assert _found(front) == []


def test_each_spelling_of_a_bad_key_is_reported_at_its_own_line():
    front = "language: de\nauthoritative: cs\ntranslations:\n  ' Fr ': a.lgd\n  Fr: b.lgd\n"
    result = validate(parse(_source(front), filename="t.lgd"))
    found = [(d.line, d.message.split(" must")[0]) for d in result.diagnostics if d.rule == "language-code-invalid"]
    assert found == [(6, "The translations key ' Fr '"), (7, "The translations key 'Fr'")]
    # The model keeps the last; a bad spelling of a good key is no finding.
    assert _found("language: de\nauthoritative: cs\ntranslations:\n  ' cs ': a.lgd\n  cs: b.lgd\n") == []


# ── The language a document says ──


def test_an_empty_language_is_no_language_said():
    for front in ("language:\n", "language: ''\n", ""):
        assert _found(front + "authoritative: en\n") == [("authoritative-not-declared", "warning", 3 + front.count("\n"))]
        assert not _identifiers(front + "authoritative: en\ntranslations:\n  en: a.lgd\n", "# Definitions\n")


def test_what_the_language_says_does_not_depend_on_a_source_map_or_on_writing_it_back():
    from collections import Counter
    from dataclasses import replace

    from legaldown import serialize

    def rules(each):
        return Counter((d.rule, d.level) for d in validate(each).diagnostics)

    for language in ("", "language:\n", "language: ''\n", "language: en\n", "language: fr\n", "language: english\n"):
        for rest in ("", "authoritative: en\n", "authoritative: en\ntranslations:\n  en: a.lgd\n"):
            document = parse(_source(language + rest, "# Definitions\n"), filename="t.lgd")
            again = parse(serialize(document), filename="t.lgd")
            assert rules(replace(document, source_map=None)) == rules(document), (language, rest)
            assert rules(Document.from_dict(document.to_dict())) == rules(document), (language, rest)
            assert rules(again) == rules(document), (language, rest)
            assert again.metadata.language == document.metadata.language


def test_a_language_is_the_models_only_when_the_document_says_one():
    from legaldown import empty_document, serialize
    from legaldown.models import Metadata

    # §3.2 gives `language` no default: the model holds none, as it holds no `authoritative`.
    assert Metadata().language == "" and Document().metadata.language == ""
    for front in ("", "language:\n", "language: ''\n"):
        unsaid = parse(_source(front), filename="t.lgd")
        assert unsaid.metadata.language == ""
        assert "language" not in serialize(unsaid)  # writing it back does not say one
    said = parse(_source("language: en\n"), filename="t.lgd")
    assert said.metadata.language == "en" and "language: en" in serialize(said)
    # ... and one built by hand says what it holds, "en" or not: the starter document says it.
    assert "language: en" in serialize(empty_document())
    assert "language: en" in serialize(Document(metadata=Metadata(language="en")))
    assert "language: fr" in serialize(Document(metadata=Metadata(language="fr")))
    assert Document.from_dict({"metadata": {"language": "en"}}).metadata.language == "en"
    assert Document.from_dict({}).metadata.language == ""


def test_a_language_declared_key_is_an_unknown_key():
    # An earlier build of #116 kept such a flag; frontmatter cannot set anything of the sort.
    document = parse(_source("language_declared: true\nauthoritative: en\n"), filename="t.lgd")
    assert document.metadata.language == "" and not hasattr(document.metadata, "language_declared")
    assert _found("language_declared: true\nauthoritative: en\n") == [("authoritative-not-declared", "warning", 4)]
    assert Document.from_dict({"metadata": {"language_declared": True}}).metadata.language == ""


def test_a_translations_key_that_is_no_code_does_not_silence_the_identifier_rule():
    # authoritative-not-declared judges only valid codes, and is silent here (`Fr` might be `cs`);
    # the document is a translation all the same, as `de` of `cs`, and its headings are Errors.
    front = "language: de\nauthoritative: cs\ntranslations:\n  Fr: y.lgd\n"
    assert _found(front) == [("language-code-invalid", "warning", 6)]
    assert _identifiers(front, "# Definitions\n") == [("translation-implicit-id", "error", 9)]
    # With every key valid, the authoritative that is none of them is a mistake, and the document no translation.
    front = "language: de\nauthoritative: cs\ntranslations:\n  fr: y.lgd\n"
    assert _found(front) == [("authoritative-not-declared", "warning", 4)]
    assert _identifiers(front, "# Definitions\n") == []
    # A valid key that is the authoritative makes it a translation, an invalid one beside it or not.
    assert _identifiers("language: de\nauthoritative: cs\ntranslations:\n  Fr: y.lgd\n  cs: z.lgd\n", "# Definitions\n") != []


# ── The documents the specification gives as right ────────────────


@pytest.mark.parametrize("path", _corpus())
def test_a_document_the_specification_gives_as_valid_draws_none_of_the_new_rules(path: Path):
    """``fixtures/valid``, ``fixtures/assembly`` and ``examples/`` (the bilingual pair included:
    its identifiers are all explicit) are no cases of #116's rules."""
    try:
        document = parse(path.read_text(encoding="utf-8"), filename=path.name)
    except FrontmatterError:
        pytest.skip("unreadable frontmatter")
    assert [(d.rule, d.line) for d in validate(document).diagnostics if d.rule in _NEW_RULES] == []
