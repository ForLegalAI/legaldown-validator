"""Entries of ``translations`` and ``field_types`` that another replaces (#126): the model reads the keys of
these maps as text, stripped, so the same key written twice, ``1`` and ``'1'``, and ``fr`` and ``' fr'`` are
one entry. Each entry that does not stay is reported at its own line as ``ldv-frontmatter-key-duplicate``,
and a padded key is found at its own line by the rules that report on one. The other mappings are
``test_frontmatter_key_duplicates_generic.py``'s (#131)."""
from __future__ import annotations

import random
import time

import pytest

from legaldown import parse
from legaldown.grammar import FRONTMATTER_KEY_DUPLICATE_RULE, IMPLEMENTATION_RULE_PREFIX
from legaldown.models import Section
from legaldown.positions import line_breaks, read_maps, written_maps
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


def test_stripped_keys_of_field_types_are_a_warning_too():
    # Nothing else sees a padded `field_types` key (`field-type-key-format` judges it stripped): the same level.
    front = "field_types:\n  a: x\n  ' a': y\n  'a ': z\n"
    assert _found(front) == [(RULE, "warning", 4), (RULE, "warning", 5)]
    assert _found("field_types:\n  ' amount': date\n") == []


def test_the_message_names_both_entries():
    result = validate(parse(_source("translations:\n  ' fr': a.lgd\n  fr: b.lgd\n")))
    (diagnostic,) = [d for d in result.diagnostics if d.rule == RULE]
    assert diagnostic.message == (
        "The translations key `' fr'` is the same key as `fr` once stripped: this entry is "
        "ignored, and the entry at line 5 is the one read."
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
    assert _found("field_types:\n  a: x\n  ' a': ' '\n") == [(RULE, "warning", 4)]  # a blank description is kept


def test_the_mix_of_an_exact_repeat_and_a_padded_key():
    # `fr`, `' fr'`, `fr`: the model holds {'fr': 'c.lgd'} first and then `' fr'` over it, so the entry at
    # line 5 stays: the repeat at line 4 and the entry at line 6 are ignored, though line 6 is the last written.
    front = "translations:\n  fr: a.lgd\n  ' fr': b.lgd\n  fr: c.lgd\n"
    assert parse(_source(front)).metadata.translations == {"fr": "b.lgd"}
    assert _found(front) == [(RULE, "error", 4), (RULE, "warning", 6)]
    result = validate(parse(_source(front)))
    messages = sorted((d.line, d.message) for d in result.diagnostics if d.rule == RULE)
    # The repeat names the entry that repeats it (line 6); the padded one is the entry the model keeps (line 5).
    assert messages[0][1] == "The translations key `fr` is written again at line 6: this entry is ignored."
    assert messages[1][1].endswith("once stripped: this entry is ignored, and the entry at line 5 is the one read.")


def test_a_repeat_names_the_entry_that_repeats_it_not_the_one_the_model_keeps():
    front = "translations:\n  fr: a.lgd\n  fr: b.lgd\n  'fr ': c.lgd\n"
    assert parse(_source(front)).metadata.translations == {"fr": "c.lgd"}
    assert _found(front) == [(RULE, "error", 4), (RULE, "warning", 5)]
    messages = [d.message for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert messages == [
        "The translations key `fr` is written again at line 5: this entry is ignored.",
        "The translations key `fr` is the same key as `'fr '` once stripped: this entry is ignored, and the entry at line 6 is the one read.",
    ]
    (diagnostic,) = [d for d in validate(parse(_source("translations:\n  fr: a.lgd\n  fr: b.lgd\n"))).diagnostics if d.rule == RULE]
    assert diagnostic.message == "The translations key `fr` is written again at line 5: this entry is ignored."


def test_the_entry_is_blank_only_if_it_is_the_one_the_model_would_keep():
    # `1`, `1` and `"1": ""`: one key to the model, whose last entry is blank, so none is kept.
    front = 'translations:\n  1: x\n  1: y\n  "1": ""\n'
    assert parse(_source(front)).metadata.translations == {}
    messages = [d.message for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert messages == [
        "The translations key `1` is written again: this entry is ignored, and the entry at line 6 is skipped "
        "(its value is empty), so no entry is kept.",
        "The translations key `1` is the same key as `\"1\"` once read as text: this entry is ignored, "
        "and the entry at line 6 is skipped (its value is empty), so no entry is kept.",
    ]
    # a blank entry that is not the last of its key is no reason to say so
    front = "translations:\n  a: x\n  a: ''\n  ' a': z\n"
    assert not any("skipped" in d.message for d in validate(parse(_source(front))).diagnostics if d.rule == RULE)


# ── the same key written twice, or read as one ──

@pytest.mark.parametrize("name", ["translations", "field_types"])
def test_a_value_that_refers_to_itself_is_read_as_before(name):
    # The values are the model's, not built again: a self-referencing one parsed before #126 and still does.
    front = f"{name}:\n  fr: &a [*a]\n  ' fr': &b [*b]\n  de: x\n  de: y\n"
    document = parse(_source(front))
    assert set(getattr(document.metadata, name)) == {"fr", "de"}
    assert _found(front) == [(RULE, "warning", 4), (RULE, "error", 6)]


def test_a_key_merged_into_a_map_and_written_is_found_at_the_written_entry():
    document = parse("---\nbase: &base\n  fr: x\nfield_types:\n  <<: *base\n  fr: y\n---\n\n# S {#s}\n")
    assert document.source_map.key("field_types", "fr") == 6


@pytest.mark.parametrize("name", ["translations", "field_types"])
def test_keys_that_are_different_yaml_keys_read_as_one_text_are_a_warning(name):
    front = f"{name}:\n  1: a\n  '1': b\n"
    assert getattr(parse(_source(front)).metadata, name) == {"1": "b"}
    assert _found(front) == [(RULE, "warning", 4)]
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert "is the same key as `'1'` once read as text:" in diagnostic.message  # no padding is involved


@pytest.mark.parametrize("name", ["translations", "field_types"])
@pytest.mark.parametrize("keys", [("fr", "fr"), ("fr", '"fr"'), ("'fr'", "fr"), ("1", "1"), ("null", "null")])
def test_the_same_yaml_key_written_twice_is_an_error(name, keys):
    # Invalid YAML (a strict reader fails the document), though the model reads it: an Error in both maps.
    front = f"{name}:\n  {keys[0]}: a\n  {keys[1]}: b\n"
    assert _found(front) == [(RULE, "error", 4)]


def test_a_key_is_shown_as_it_is_written_in_the_source():
    front = 'translations:\n  "\\x66r": x\n  "fr ": y\n'
    assert parse(_source(front)).metadata.translations == {"fr": "y"}
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message.startswith('The translations key `"\\x66r"` is the same key as `"fr "` once stripped')


def test_keys_the_model_reads_as_one_through_their_text_are_grouped_by_what_it_holds():
    # `!!binary Zm8=` is the bytes b'fo', which the model holds as the text "b'fo'", the other key's text.
    front = "translations:\n  !!binary Zm8=: a.lgd\n  \"b'fo'\": b.lgd\n"
    document = parse(_source(front))
    assert document.metadata.translations == {"b'fo'": "b.lgd"}
    assert set(document.source_map.maps["translations"].stays) == {"b'fo'"}
    assert _found(front) == [(RULE, "warning", 4)]


def test_keys_python_holds_as_one_are_held_under_the_first_written_as_text():
    # `True` and `1` are one dict key: the model holds `"True"`, with the value of the second.
    front = 'translations:\n  !!bool "true": a.lgd\n  !!int "1": b.lgd\n'
    document = parse(_source(front))
    assert document.metadata.translations == {"True": "b.lgd"}
    assert {key: entry.line for key, entry in document.source_map.maps["translations"].stays.items()} == {"True": 5}
    assert document.source_map.key("translations", "True") == 5
    (diagnostic,) = [d for d in validate(document).diagnostics if d.rule == RULE]
    assert (diagnostic.level, diagnostic.line) == ("warning", 4)
    assert diagnostic.message.endswith(
        "once read as a YAML value (Python treats them as equal): this entry is ignored, and the entry at line 5 is the one read."
    )


def test_keys_python_holds_as_equal_numbers_are_a_warning_too():
    front = "translations:\n  !!int \"1\": a.lgd\n  !!float \"1.0\": b.lgd\n"
    document = parse(_source(front))
    assert document.metadata.translations == {"1": "b.lgd"}
    assert _found(front) == [(RULE, "warning", 4)]
    (diagnostic,) = [d for d in validate(document).diagnostics if d.rule == RULE]
    assert "once read as a YAML value" in diagnostic.message


@pytest.mark.parametrize("front,tail", [
    ("translations: {fr: a.lgd, fr: b.lgd}\n", "is written again at line 3: this entry is ignored."),
    ("translations: {fr: a.lgd, ' fr': b.lgd}\n", "once stripped: this entry is ignored, and the entry at line 3 is the one read."),
])
def test_two_entries_on_one_line_still_name_the_other_entry(front, tail):
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.line == 3 and diagnostic.message.endswith(tail)


@pytest.mark.parametrize("front,reason", [
    ("translations:\n  fr: a.lgd\n  fr: !!bool \"false\"\n", "its value is `False`"),
    ("translations:\n  fr: a.lgd\n  fr: !!int \"0\"\n", "its value is `0`"),
    ("translations:\n  !!bool \"false\": a.lgd\n  !!bool \"false\": b.lgd\n", "its key is `False`"),
    ("translations:\n  !!null \"x\": a.lgd\n  !!null \"x\": b.lgd\n", "its key is null"),
    ("translations:\n  fr: a.lgd\n  fr: []\n", "its value is empty"),
    ("translations:\n  fr: a.lgd\n  fr: !!binary \"\"\n", "its value is empty"),
])
def test_the_message_says_what_the_skipped_key_or_value_is(front, reason):
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message.endswith(f"is skipped ({reason}), so no entry is kept.")


def test_internal_spaces_of_a_key_are_kept_when_it_is_shown():
    front = 'translations:\n  "fr  ": x\n  "fr ": y\n'
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message.startswith('The translations key `"fr  "` is the same key as `"fr "` once stripped')


def test_ignored_entries_are_in_source_order_on_one_line():
    front = "translations: {fr: a.lgd, fr: b.lgd, ' fr': c.lgd}\n"
    document = parse(_source(front))
    assert [each.entry.start for each in document.source_map.maps["translations"].ignored] == sorted(
        each.entry.start for each in document.source_map.maps["translations"].ignored
    )
    messages = [d.message for d in validate(document).diagnostics if d.rule == RULE]
    assert "is written again" in messages[0] and "once stripped" in messages[1]


def test_a_block_scalar_key_is_shown_without_its_ends():
    front = "translations:\n  ? |\n    fr\n  : a.lgd\n  ' fr': b.lgd\n"
    assert parse(_source(front)).metadata.translations == {"fr": "b.lgd"}
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message.startswith("The translations key `| fr` is the same key as `' fr'` once stripped")


def test_a_key_over_several_lines_is_shown_on_one():
    front = 'translations:\n  ? "fr\n    "\n  : a.lgd\n  fr: b.lgd\n'
    assert parse(_source(front)).metadata.translations == {"fr": "b.lgd"}
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message.startswith('The translations key `"fr "` is the same key as `fr` once stripped')


def test_a_key_with_a_backtick_is_shown_between_double_backticks():
    front = 'translations:\n  "a`b": x\n  "a`b ": y\n'
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message.startswith('The translations key ``"a`b"`` is the same key as ``"a`b "`` once stripped')


def test_a_fence_is_one_longer_than_the_longest_run_of_backticks_and_padded_at_the_edges():
    front = 'translations:\n  "a``b": x\n  "a``b ": y\n  "`": a\n  "` ": b\n'
    messages = [d.message for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert messages[0].startswith('The translations key ```"a``b"``` is the same key as ```"a``b "``` once stripped')
    assert messages[1].startswith('The translations key ``"`"`` is the same key as ``"` "`` once stripped')


def test_a_repeat_whose_last_entry_is_blank_says_that_no_entry_is_kept():
    front = 'translations:\n  fr: a.lgd\n  fr: ""\n'
    assert parse(_source(front)).metadata.translations == {}
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert (diagnostic.level, diagnostic.line) == ("error", 4)
    assert diagnostic.message == (
        "The translations key `fr` is written again: this entry is ignored, and the entry at line 5 is skipped "
        "(its value is empty), so no entry is kept."
    )


@pytest.mark.parametrize("front,reason", [
    ("translations:\n  fr: a.lgd\n  fr: ~\n", "its value is null"),
    ("translations:\n  fr: a.lgd\n  fr: '  '\n", "its value is blank"),
    ("translations:\n  '': a.lgd\n  '': b.lgd\n", "its key is empty"),
    ("translations:\n  ' ': a.lgd\n  ' ': b.lgd\n", "its key is blank"),
])
def test_the_message_says_why_the_model_keeps_none(front, reason):
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.level == "error" and diagnostic.line == 4
    assert diagnostic.message.endswith(f"the entry at line 5 is skipped ({reason}), so no entry is kept.")


def test_an_aliased_key_written_again_is_at_its_anchor_and_does_not_cite_itself():
    # The YAML reader gives the alias its anchor's node, and so its line: the position is not recovered.
    front = "translations:\n  &k fr: a.lgd\n  *k : b.lgd\n"
    assert _found(front) == [(RULE, "error", 4)]
    (diagnostic,) = [d for d in validate(parse(_source(front))).diagnostics if d.rule == RULE]
    assert diagnostic.message == "The translations key `&k fr` is written again: this entry is ignored."


def test_a_repeat_whose_last_entry_is_blank_is_still_judged_after_an_unrelated_edit():
    document = parse(_source('translations:\n  fr: a.lgd\n  fr: ""\n'))
    document.metadata.title = "Changed"
    assert [d.line for d in validate(document).diagnostics if d.rule == RULE] == [4]


def test_an_exact_repeat_is_not_reported_once_the_model_no_longer_holds_the_key():
    for name in ("translations", "field_types"):
        document = parse(_source(f"{name}:\n  fr: a\n  fr: b\n"))
        assert [d.line for d in validate(document).diagnostics if d.rule == RULE] == [4]
        setattr(document.metadata, name, {})
        assert [d for d in validate(document).diagnostics if d.rule == RULE] == []


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
    ("field_types:\n  a: a\n  ' a': b\n", "warning"),  # as in `translations`
])
def test_the_level_depends_on_the_case(front, level):
    assert [lvl for _rule, lvl, _line in _found(front)] == [level]


# ── the line of a padded key, for the rules that report on one ──

def test_a_padded_translations_key_is_found_at_its_own_line():
    front = "language: en\nauthoritative: en\ntranslations:\n  cs: ok.lgd\n  ' xx ': /abs.lgd\n"
    rules = ("language-code-invalid", "path-not-relative")
    assert _found(front, rules) == [("language-code-invalid", "warning", 7), ("path-not-relative", "error", 7)]


def test_a_field_types_key_is_judged_stripped_and_found_at_its_own_line():
    # (A padded key that is valid once stripped draws nothing from these rules: only a bad one has a finding.)
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

_KEYS = ["fr", "' fr'", "'fr '", "'fr'", "FR", "' '", "''", "~", "null", "' FR '", "0", "false", "a", "' a'", "'1'", "1", "!!binary Zm8=", "\"b'fo'\""]
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
        ignored = {each.entry.line for each in found.ignored} if found else set()
        for each in found.ignored if found else ():
            assert each.entry.key == each.by.key, front
            if each.why != "repeated":  # a repeat is named by the entry that repeats it, which may be ignored too
                assert each.by.line not in ignored and (each.skipped or stays[each.by.key] is each.by), front
        for index, row in enumerate(rows):
            # Every row is accounted for: it stays, it is reported, or the model skips it.
            line = 4 + index
            assert line in {e.line for e in stays.values()} | ignored or alone(name, *row) == {}, (front, line)


def test_read_maps_takes_the_model_s_value_for_the_entry_that_stays():
    front = "translations:\n  fr: a.lgd\n  ' fr': b.lgd\n  de: d.lgd\n"
    document = parse(_source(front))
    stays = document.source_map.maps["translations"].stays
    assert {key: entry.line for key, entry in stays.items()} == {"fr": 5, "de": 6}
    assert [(i.entry.written, i.by.written, i.why, i.skipped) for i in document.source_map.maps["translations"].ignored] == [
        ("fr", "' fr'", "stripped", "")
    ]


def test_written_maps_and_read_maps_are_the_parsers_pair():
    import yaml

    block = "translations:\n  fr: a\n  ' fr': b\n"
    loader = yaml.SafeLoader(block)
    node = loader.get_single_node()
    breaks = line_breaks(block)
    maps = read_maps(written_maps(node, block, breaks, 2), loader.construct_object, loader.construct_document(node))
    assert [(i.entry.line, i.by.line) for i in maps["translations"].ignored] == [(3, 4)]


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
