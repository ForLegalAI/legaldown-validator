"""Language codes at the Core level (§3.2, §16.6): ``language-code-invalid`` and
``authoritative-not-declared`` (#116).

A value that is no ISO 639-1 code is no language: the rules that compare languages judge only
the codes that pass, so one typo draws one Warning."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from legaldown import parse, validate
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
        "language: fr\nauthoritative: en\ntranslations:\n  english: x.lgd\n",  # a key that might have been `en`
    ],
)
def test_a_value_that_is_no_code_draws_language_code_invalid_alone(front):
    rules = {d.rule for d in validate(parse(_source(front, "# Scope\n\nText.\n"), filename="t.lgd")).diagnostics}
    assert rules & {"language-code-invalid", "authoritative-not-declared"} == {"language-code-invalid"}
    assert not rules & {"translation-implicit-id", "translation-authoritative-absent"}


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
