"""Frontmatter entries that another replaces (#126): a key written twice in a mapping, which YAML's reader
resolves to the last, and the keys of ``translations`` and ``field_types`` that are one once stripped
(``fr`` and ``' fr'``). Each is reported at its own line as ``ldv-frontmatter-key-duplicate``, and a padded
key is found at its own line by the rules that report on one."""
from __future__ import annotations

import random
import time

import pytest

from legaldown import parse
from legaldown.grammar import FRONTMATTER_KEY_DUPLICATE_RULE, IMPLEMENTATION_RULE_PREFIX
from legaldown.models import Section
from legaldown.positions import read_maps, written_maps
from legaldown.validator import validate

RULE = "ldv-frontmatter-key-duplicate"


def _source(front):
    # The frontmatter's lines: `---` is 1, `title: T` 2, and `front` starts on 3.
    return f"---\ntitle: T\n{front}---\n\n# S {{#s}}\n\nText.\n"


def _found(front, rules=(RULE,)):
    result = validate(parse(_source(front), filename="t.lgd"))
    return [(d.rule, d.level, d.line) for d in result.diagnostics if d.rule in rules]


def test_the_rule_is_an_implementation_rule():
    assert RULE == FRONTMATTER_KEY_DUPLICATE_RULE and RULE.startswith(IMPLEMENTATION_RULE_PREFIX)


# ── two keys that are one once stripped ──

def test_stripped_keys_of_translations_are_a_warning_at_each_ignored_entry():
    # The last stays (the model's rule); the two before it are ignored, each at its line. The
    # specification judges a padded language code a Warning (`language-code-invalid`), so is this.
    front = "translations:\n  fr: a.lgd\n  ' fr': b.lgd\n  'fr ': c.lgd\n"
    assert _found(front) == [(RULE, "warning", 4), (RULE, "warning", 5)]


def test_stripped_keys_of_field_types_are_an_error_at_each_ignored_entry():
    # `field-type-key-format` is an Error for the padded key (§3.2): so is the collision.
    front = "field_types:\n  a: x\n  ' a': y\n  'a ': z\n"
    assert _found(front) == [(RULE, "error", 4), (RULE, "error", 5)]


def test_the_message_names_both_entries():
    result = validate(parse(_source("translations:\n  ' fr': a.lgd\n  fr: b.lgd\n")))
    (diagnostic,) = [d for d in result.diagnostics if d.rule == RULE]
    assert diagnostic.message == (
        "The translations key ' fr' is the same key as 'fr' once stripped: the entry at line 5 stays, "
        "and this one is ignored."
    )


def test_the_model_still_keeps_the_last():
    document = parse(_source("translations:\n  fr: a.lgd\n  ' fr': b.lgd\nfield_types:\n  a: x\n  ' a': y\n"))
    assert document.metadata.translations == {"fr": "b.lgd"}
    assert document.metadata.field_types == {"a": "y"}


def test_no_collision_no_finding():
    assert _found("translations:\n  fr: a.lgd\n  ' cs ': b.lgd\nfield_types:\n  a: x\n  b: y\n") == []


def test_a_blank_sibling_is_skipped_not_a_collision():
    assert _found("translations:\n  fr: a.lgd\n  ' fr': ''\n") == []  # #116: a blank entry is skipped
    assert _found("translations:\n  fr: a.lgd\n  ' fr':\n") == []
    assert _found("field_types:\n  a: x\n  ' a': ' '\n") == [(RULE, "error", 4)]  # a blank description is kept


def test_the_mix_of_an_exact_repeat_and_a_padded_key():
    # `fr`, `' fr'`, `fr`: the model holds {'fr': 'c.lgd'} first and then `' fr'` over it, so the entry at
    # line 5 stays: the repeat at line 4 and the entry at line 6 are ignored, though line 6 is the last written.
    front = "translations:\n  fr: a.lgd\n  ' fr': b.lgd\n  fr: c.lgd\n"
    assert parse(_source(front)).metadata.translations == {"fr": "b.lgd"}
    assert _found(front) == [(RULE, "error", 4), (RULE, "warning", 6)]
    result = validate(parse(_source(front)))
    messages = sorted((d.line, d.message) for d in result.diagnostics if d.rule == RULE)
    assert "entry at line 5 stays" in messages[1][1] and "last is the one read" in messages[0][1]


# ── a key written twice, in any mapping ──

@pytest.mark.parametrize("front,lines", [
    ("translations:\n  fr: x\n  other: y\n  fr: z\n", [4]),
    ("field_types:\n  a: x\n  other: y\n  a: z\n", [4]),
    ("title: Again\n", [2]),
    ("governing_law: a\ngoverning_law: b\ngoverning_law: c\n", [3, 4]),
    ("sides:\n  - name: a\n    name: b\n    parties:\n      - name: p\n        type: person\n", [4]),
])
def test_a_repeated_key_is_reported_at_each_earlier_entry(front, lines):
    assert [line for rule, level, line in _found(front) if level == "error"] == lines
    assert {rule for rule, *_ in _found(front)} == {RULE}


def test_title_written_twice_is_reported_at_the_earlier_line_and_the_model_keeps_the_last():
    document = parse("---\ntitle: First\nlegaldown: '0.1'\ntitle: Last\n---\n\n# S {#s}\n\nText.\n")
    assert document.metadata.title == "Last"
    assert document.source_map.key("title") == 4  # the line of the entry the model holds
    result = validate(document)
    (diagnostic,) = [d for d in result.diagnostics if d.rule == RULE]
    assert (diagnostic.level, diagnostic.line) == ("error", 2)
    assert diagnostic.message == (
        "The frontmatter key 'title' is written again at line 4, and the last is the one read: this entry is ignored."
    )


def test_a_repeat_names_the_mapping_it_is_in():
    result = validate(parse(_source("sides:\n  - name: a\n    name: b\n")))
    (diagnostic,) = [d for d in result.diagnostics if d.rule == RULE]
    assert diagnostic.message.startswith("The sides[0] key 'name' is written again at line 5")


def test_a_key_merged_in_and_written_is_not_a_repeat():
    front = "base: &base\n  fr: a.lgd\ntranslations:\n  <<: *base\n  fr: b.lgd\n"
    document = parse(_source(front))
    assert document.metadata.translations == {"fr": "b.lgd"}  # the written key wins over the merged one
    assert _found(front) == []


def test_a_root_key_merged_in_and_written_is_not_a_repeat():
    front = "base: &base\n  governing_law: x\n<<: *base\ngoverning_law: y\n"
    assert parse(_source(front)).metadata.governing_law == "y"
    assert _found(front) == []


def test_two_merge_keys_are_not_a_repeat():
    front = "one: &one\n  a: 1\ntwo: &two\n  b: 2\ntranslations:\n  <<: *one\n  <<: *two\n"
    assert _found(front) == []


def test_a_repeat_inside_an_anchor_is_reported_once_though_merged_twice():
    front = "base: &base\n  fr: a\n  fr: b\ntranslations:\n  <<: *base\nfield_types:\n  <<: *base\n"
    assert _found(front) == [(RULE, "error", 4)]


def test_an_aliased_key_is_at_the_line_of_its_anchor():
    front = "a: &k fr\ntranslations:\n  *k : a.lgd\n  ' fr': b.lgd\n"
    assert _found(front) == [(RULE, "warning", 3)]


# ── the findings follow the model and the source ──

def test_a_model_edited_after_parsing_draws_no_stale_finding():
    document = parse(_source("translations:\n  fr: a.lgd\n  ' fr': b.lgd\n"))
    assert [d.line for d in validate(document).diagnostics if d.rule == RULE] == [4]
    document.metadata.translations = {}
    assert [d for d in validate(document).diagnostics if d.rule == RULE] == []
    document.metadata.translations = {"fr": "b.lgd"}
    assert [d.line for d in validate(document).diagnostics if d.rule == RULE] == [4]


def test_a_null_by_its_tag_is_skipped_as_the_model_skips_it():
    # The model drops `!!null "x"`, so `a: x` stays and the padded key is no entry.
    front = "field_types:\n  a: x\n  ' a': !!null \"x\"\n"
    document = parse(_source(front))
    assert document.metadata.field_types == {"a": "x"}
    assert _found(front) == []
    assert document.source_map.key("field_types", "a") == 4


def test_a_document_whose_body_no_longer_fits_the_source_draws_no_finding():
    document = parse(_source("translations:\n  fr: a.lgd\n  fr: b.lgd\n  ' fr': c.lgd\n"))
    assert len([d for d in validate(document).diagnostics if d.rule == RULE]) == 2
    document.sections.append(Section(title="Added"))
    assert [d for d in validate(document).diagnostics if d.rule == RULE] == []


def test_a_document_built_from_a_dict_has_no_source_map_and_no_finding():
    from legaldown import document_from_dict

    document = document_from_dict({"metadata": {"translations": {" fr": "a.lgd", "fr": "b.lgd"}}})
    assert [d for d in validate(document).diagnostics if d.rule == RULE] == []


def test_source_maps_of_the_same_text_stay_equal_after_one_is_validated():
    source = _source("translations:\n  fr: a.lgd\n  ' fr': b.lgd\n")
    first, second = parse(source), parse(source)
    validate(first)
    assert first.source_map == second.source_map


def test_fragments_are_not_checked():
    # A fragment is not a document's frontmatter (`mode.empty_metadata`).
    result = validate(parse(_source("translations:\n  fr: a.lgd\n  fr: b.lgd\n")), fragment=True)
    assert RULE not in result.rules()


# ── the level of each case ──

@pytest.mark.parametrize("front,level", [
    ("translations:\n  fr: a\n  fr: b\n", "error"),  # invalid YAML to a strict reader
    ("translations:\n  fr: a\n  ' fr': b\n", "warning"),  # `language-code-invalid` is a Warning
    ("field_types:\n  a: a\n  a: b\n", "error"),
    ("field_types:\n  a: a\n  ' a': b\n", "error"),  # `field-type-key-format` is an Error
    ("title: a\n", "error"),  # `title: T` is written above
])
def test_the_level_depends_on_the_case(front, level):
    assert [lvl for _rule, lvl, _line in _found(front)] == [level]


# ── the line of a padded key, for every rule that reports on one ──

def test_a_padded_translations_key_is_found_at_its_own_line():
    front = "language: en\nauthoritative: en\ntranslations:\n  cs: ok.lgd\n  ' xx ': /abs.lgd\n"
    rules = ("language-code-invalid", "path-not-relative")
    assert _found(front, rules) == [("language-code-invalid", "warning", 7), ("path-not-relative", "error", 7)]


def test_a_padded_field_types_key_is_found_at_its_own_line():
    front = "field_types:\n  ok: x\n  ' Bad ': y\n  ' date ': z\n"
    assert _found(front, ("field-type-key-format", "field-type-key-reserved")) == [
        ("field-type-key-format", "error", 5), ("field-type-key-reserved", "error", 6)]


def test_a_placeholder_in_a_padded_key_or_value_is_found_at_its_line():
    front = "translations:\n  fr: x.lgd\n  ' de ': '{{placeholder: p}}'\nfield_types:\n  ' {{placeholder: q}} ': d\n"
    assert [line for _r, _l, line in _found(front, ("placeholder-in-structural-field",))] == [5, 7]


def test_the_path_of_the_entry_that_stays_is_judged_at_its_line():
    # The entry that stays is the one whose path is judged; the one ignored is never resolved.
    front = "translations:\n  fr: /abs.lgd\n  ' fr': ok.lgd\n"
    assert _found(front, ("path-not-relative", RULE)) == [(RULE, "warning", 4)]
    front = "translations:\n  fr: ok.lgd\n  ' fr': /abs.lgd\n"
    assert _found(front, ("path-not-relative", RULE)) == [(RULE, "warning", 4), ("path-not-relative", "error", 5)]


# ── what read_maps makes of a map is what the model holds ──

_KEYS = ["fr", "' fr'", "'fr '", "'fr'", "FR", "' '", "''", "~", "null", "' FR '", "0", "false", "a", "' a'", "'1'", "1"]
_VALUES = ["V", "''", "' '", "~", "null", "'  '", "0", "false", "[]", "{}", "[1]", "x y", '!!null "x"', "!!str", "'~'"]


@pytest.mark.parametrize("seed", range(4))
def test_read_maps_agrees_with_the_model_on_generated_maps(seed):
    import yaml

    def alone(name, key, value):
        return getattr(parse(_source(f"{name}:\n  {key}: {value}\n")).metadata, name)

    rng = random.Random(seed)
    for _ in range(300):
        name = rng.choice(["translations", "field_types"])
        rows = [(rng.choice(_KEYS), rng.choice(_VALUES).replace("V", "v")) for _ in range(rng.randint(1, 5))]
        front = f"{name}:\n" + "".join(f"  {key}: {value}\n" for key, value in rows)
        try:
            document = parse(_source(front))
        except (yaml.YAMLError, ValueError):
            continue  # e.g. an unhashable key
        held = getattr(document.metadata, name)
        found = document.source_map.maps.get(name)
        stays = found.stays if found else {}
        assert set(stays) == set(held), (front, stays, held)
        for key, entry in stays.items():
            # The row of the entry that stays, read alone, is what the model holds for the key.
            assert alone(name, *rows[entry.line - 4]) == {key: held[key]}, (front, key)
        for ignored, by in found.ignored if found else ():
            assert ignored.key == by.key and stays[by.key] is by and ignored.line != by.line, front


def test_read_maps_takes_the_model_s_value_for_the_entry_that_stays():
    front = "translations:\n  fr: a.lgd\n  ' fr': b.lgd\n  de: d.lgd\n"
    document = parse(_source(front))
    stays = document.source_map.maps["translations"].stays
    assert {key: entry.line for key, entry in stays.items()} == {"fr": 5, "de": 6}
    assert [(e.written, by.written) for e, by in document.source_map.maps["translations"].ignored] == [("fr", " fr")]


def test_written_maps_and_read_maps_are_the_parsers_pair():
    import yaml

    block = "translations:\n  fr: a\n  ' fr': b\n"
    loader = yaml.SafeLoader(block)
    node = loader.get_single_node()
    maps = read_maps(written_maps(node, block, 2), lambda each: loader.construct_object(each, deep=True))
    assert [(e.line, by.line) for e, by in maps["translations"].ignored] == [(3, 4)]


# ── the work is linear ──

def _time(count):
    front = (
        "field_types:\n" + "".join(f"  ' k{i} ': d{i}\n" for i in range(count))
        + "translations:\n" + "".join(f"  ' x{i} ': f{i}.lgd\n" for i in range(count))
    )
    best = float("inf")
    for _ in range(2):
        start = time.perf_counter()
        validate(parse(_source(front)))
        best = min(best, time.perf_counter() - start)
    return best


def test_thousands_of_padded_keys_take_linear_time():
    small, large = _time(1000), _time(4000)
    assert large < 10 * small + 0.5, (small, large)
