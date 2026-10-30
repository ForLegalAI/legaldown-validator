"""A key written twice in any frontmatter mapping (#131): the reader keeps the last and says nothing of the others.
Each key that a later key of its mapping replaces is reported at its own line as ``ldv-frontmatter-key-duplicate``,
as the entries of ``translations`` and ``field_types`` are (#126, ``test_frontmatter_key_duplicates.py``), which this
leaves to that check."""
from __future__ import annotations

import time

import pytest

from legaldown import document_from_dict, parse
from legaldown.validator import validate

RULE = "ldv-frontmatter-key-duplicate"


def _source(front):
    # The frontmatter's lines: `---` is 1, and `front` starts on 2.
    return f"---\n{front}---\n\n# S {{#s}}\n\nText.\n"


def _found(front, **options):
    result = validate(parse(_source(front), filename="t.lgd"), **options)
    return sorted(((d.line, d.level, d.message) for d in result.diagnostics if d.rule == RULE), key=lambda found: found[0])


def _lines(front):
    return [(line, level) for line, level, _message in _found(front)]


def _questions(default):
    return f"questions:\n  q1:\n    type: text\n    default:\n{default}"


# ── a key written twice, anywhere ──

def test_a_top_level_key_written_twice_is_an_error_at_the_one_dropped():
    front = "title: a\ntitle: b\n"
    assert _found(front) == [
        (2, "error", "The frontmatter key `title` is written again at line 3: this entry is ignored.")
    ]
    assert parse(_source(front)).metadata.title == "b"  # the model keeps the last


def test_a_key_written_three_times_draws_a_finding_for_each_one_dropped():
    assert _lines("title: a\ntitle: b\ntitle: c\n") == [(2, "error"), (3, "error")]


def test_a_key_in_a_mapping_in_a_sequence_names_its_place():
    front = "sides:\n  - name: A\n    parties:\n      - name: X\n        name: Y\n"
    assert _found(front) == [
        (5, "error", "The `sides[0].parties[0]` key `name` is written again at line 6: this entry is ignored.")
    ]


def test_the_index_in_the_message_is_the_entrys_position_in_the_sequence():
    front = "parties:\n  - name: X\n  - name: Y\n    name: Z\n"
    (found,) = _found(front)
    assert found[2].startswith("The `parties[1]` key `name`")
    # not the model's index, which counts mappings only: a scalar entry before it still takes its place
    (found,) = _found("parties:\n  - text\n  - {name: Y, name: Z}\n")
    assert found[2].startswith("The `parties[1]` key `name`")


def test_a_repeat_in_a_dropped_entry_is_not_looked_at():
    # The whole entry is ignored: the model never holds what its value says.
    front = "x:\n  a: {b: 1, b: 2}\n  a: {c: 1}\n"
    assert _found(front) == [
        (3, "error", "The `x` key `a` is written again at line 4: this entry is ignored.")
    ]


def test_a_mapping_without_a_repeat_draws_nothing():
    assert _found("title: a\nx:\n  a: 1\n  b: 2\ny:\n  - a: 1\n  - a: 2\n") == []


# ── what is one key ──

def test_the_same_key_written_alike_or_quoted_is_an_error():
    assert _lines("x:\n  fr: a\n  fr: b\n") == [(3, "error")]
    assert _lines('x: {fr: a, "fr": b}\n') == [(2, "error")]
    assert _lines("x:\n  1: a\n  1: b\n") == [(3, "error")]


def test_keys_that_read_as_the_same_text_are_a_warning():
    # Outside a question's `default` every plain scalar is text: `1` and `'1'` are the model's one key.
    front = "x:\n  1: a\n  '1': b\n"
    assert _found(front) == [
        (
            3,
            "warning",
            "The `x` key `1` is the same key as `'1'` once read as text: this entry is ignored, "
            "and the entry at line 4 is the one read.",
        )
    ]


@pytest.mark.parametrize("front", [
    "x:\n  1: a\n  !!str 1: b\n",
    "x:\n  true: a\n  'true': b\n",
    "x:\n  ~: a\n  '~': b\n",
])
def test_keys_that_differ_in_their_yaml_type_are_a_warning(front):
    assert [level for _line, level, _message in _found(front)] == ["warning"]


@pytest.mark.parametrize("front", [
    "x:\n  ~: a\n  null: b\n",
    "x:\n  1: a\n  01: b\n",
    "x:\n  true: a\n  True: b\n",
])
def test_keys_that_yaml_resolves_equal_but_are_written_differently_are_not_repeats(front):
    # The reader takes each as written, so they are two text keys, and the model keeps both.
    assert _found(front) == []


def test_a_question_default_keeps_the_types_of_its_keys():
    # `1` and `'1'` are an int and a text there: two keys. `1`, `1.0` and `true` are one to a dict.
    assert _found(_questions("      1: a\n      '1': b\n")) == []
    front = _questions("      1: a\n      1.0: b\n      true: c\n")
    assert _found(front) == [
        (
            6,
            "warning",
            "The `questions.q1.default` key `1` is the same key as `true` once read as a YAML value "
            "(Python treats them as equal): this entry is ignored, and the entry at line 8 is the one read.",
        ),
        (
            7,
            "warning",
            "The `questions.q1.default` key `1.0` is the same key as `true` once read as a YAML value "
            "(Python treats them as equal): this entry is ignored, and the entry at line 8 is the one read.",
        ),
    ]
    assert _lines(_questions("      a: 1\n      a: 2\n")) == [(6, "error")]


def test_the_typed_keys_of_a_default_are_a_path_in_braces_not_taken_for_a_position():
    front = _questions("      1: {a: 1, a: 2}\n      2: x\n")
    (found,) = _found(front)
    assert found[2].startswith("The `questions.q1.default{1}` key `a` is written again at line 6")
    # a sequence position is in brackets, so the two differ
    (other,) = _found(_questions("      - x\n      - {a: 1, a: 2}\n"))
    assert other[2].startswith("The `questions.q1.default[1]` key `a` is written again at line 7")
    # and so does the text `'1'`
    (text,) = _found(_questions("      '1': {a: 1, a: 2}\n"))
    assert text[2].startswith('The `questions.q1.default["1"]` key `a`')


@pytest.mark.parametrize("default", [
    "      yes: a\n      true: b\n",
    "      ~: a\n      null: b\n",
    "      1: a\n      01: b\n",
    "      a: 1\n      \"a\": 2\n",
])
def test_keys_of_one_type_that_a_dict_holds_as_one_are_the_same_key_written_again(default):
    # In a `default` the YAML types stay: both keys are a bool, a null, an int, a text: an Error, however written.
    assert [level for _line, level, _message in _found(_questions(default))] == ["error"]


@pytest.mark.parametrize("default", ["      1: a\n      1.0: b\n", "      1: a\n      true: b\n", "      1.0: a\n      true: b\n"])
def test_keys_of_different_types_that_a_dict_holds_as_one_are_a_warning_by_value(default):
    ((_line, level, message),) = _found(_questions(default))
    assert level == "warning" and "once read as a YAML value" in message


def test_a_key_written_as_an_equals_sign_is_a_different_key_from_the_quoted_one():
    # YAML resolves a plain `=` to its own type, which the reader turns into text before it builds the mapping.
    assert [level for _l, level, _m in _found("x: {=: a, '=': b}\n")] == ["warning"]
    assert [level for _l, level, _m in _found("x:\n  =: a\n  =: b\n")] == ["error"]
    assert [level for _l, level, _m in _found("translations: {=: a, '=': b}\n")] == ["warning"]


# ── aliases ──

def test_a_key_aliased_into_two_mappings_is_reported_under_each():
    front = "x: {&k a: 1, *k : 2}\ny: {*k : 1, *k : 2}\n"
    assert _found(front) == [
        (2, "error", "The `x` key `&k a` is written again: this entry is ignored."),
        (2, "error", "The `y` key `&k a` is written again: this entry is ignored."),
    ]


def test_a_key_used_three_or_more_times_in_a_mapping_is_reported_once():
    front = "x: {&k a: 1, *k : 2, *k : 3, *k : 4}\n"
    assert _found(front) == [(2, "error", "The `x` key `&k a` is written again: this entry is ignored.")]


def test_an_aliased_key_that_is_also_written_again_cites_the_other_node():
    front = "x: {&k a: 1, *k : 2, a: 3}\n"
    assert _found(front) == [(2, "error", "The `x` key `&k a` is written again at line 2: this entry is ignored.")]


def test_a_mapping_shared_through_an_alias_is_reported_once_under_the_first_path():
    front = "x: &m\n  a: 1\n  a: 2\ny: *m\nz:\n  - *m\n"
    assert _found(front) == [(3, "error", "The `x` key `a` is written again at line 4: this entry is ignored.")]


# ── merges ──

def test_a_key_written_over_a_merged_one_is_no_repeat():
    assert _found("base: &b\n  a: 1\nx:\n  <<: *b\n  a: 2\n") == []
    assert _found("base: &b\n  a: 1\nx:\n  a: 2\n  <<: *b\n") == []


def test_the_same_key_from_two_sources_is_no_repeat():
    assert _found("x:\n  <<: [{a: 1}, {a: 3}]\n") == []
    assert _found("x:\n  <<: [{a: 1}, {a: 3}]\n  a: 4\n") == []


def test_a_key_repeated_in_a_source_is_reported_at_the_source():
    assert _found("x:\n  <<: &b {a: 1, a: 2}\n  c: 1\n") == [
        (3, "error", "The `x.<<` key `a` is written again at line 3: this entry is ignored.")
    ]
    assert _found("x:\n  <<: [{a: 1, a: 2}, {a: 3}]\n") == [
        (3, "error", "The `x.<<[0]` key `a` is written again at line 3: this entry is ignored.")
    ]


def test_a_source_reachable_by_its_anchor_too_is_reported_once():
    front = "base: &b\n  a: 1\n  a: 2\nx:\n  <<: *b\n"
    assert _found(front) == [(3, "error", "The `base` key `a` is written again at line 4: this entry is ignored.")]


def test_a_key_repeated_in_the_mapping_with_a_merge_is_reported():
    front = "base: &b\n  c: 1\nx:\n  <<: *b\n  a: 1\n  a: 2\n"
    assert _found(front) == [(6, "error", "The `x` key `a` is written again at line 7: this entry is ignored.")]


def test_a_merge_into_the_root_counts_the_entries_written_there():
    front = "<<: {title: a}\nx: 1\nx: 2\n"
    assert _lines(front) == [(3, "error")]


def test_a_mapping_that_merges_itself_ends():
    assert _lines("a: &a\n  <<: *a\n  b: 1\n  b: 2\n") == [(4, "error")]


def test_an_entry_that_the_mapping_overrides_is_not_read_and_not_judged():
    # The merged pair is one key to the model, but the mapping's own `'1'` takes its place: nothing is read of it.
    assert _found("x:\n  <<: {1: a, '1': b}\n  '1': c\n") == []
    # Its value is not read either.
    assert _found("x:\n  <<: {party: {name: A, name: B}}\n  party: {name: C}\n") == []
    assert _found("x:\n  party: {name: C}\n  <<: {party: {name: A, name: B}}\n") == []


def test_an_entry_that_contributes_is_judged_with_its_value():
    ((line, level, message),) = _found("x:\n  <<: {1: a, '1': b}\n")
    assert (line, level) == (3, "warning") and message.endswith("and the entry at line 3 is the one read.")
    assert _found("x:\n  <<: {party: {name: A, name: B}}\n  other: 1\n") == [
        (3, "error", "The `x.<<.party` key `name` is written again at line 3: this entry is ignored.")
    ]


def test_an_entry_of_a_later_source_that_an_earlier_one_overrides_is_not_read():
    front = "x:\n  <<: [{a: {b: 1, b: 2}}, {a: {c: 1, c: 2}, d: {e: 1, e: 2}}]\n"
    assert [message for _l, _lvl, message in _found(front)] == [
        "The `x.<<[0].a` key `b` is written again at line 3: this entry is ignored.",
        "The `x.<<[1].d` key `e` is written again at line 3: this entry is ignored.",
    ]


def test_a_source_of_a_source_is_overridden_by_the_source_that_merges_it():
    front = "x:\n  <<: {<<: {a: {b: 1, b: 2}}, a: 1}\n"
    assert _found(front) == []


def test_an_overridden_translations_in_a_source_gives_nothing():
    assert _found("<<: {translations: {fr: a, fr: b}}\ntranslations: {de: x}\n") == []
    assert _found("<<: {translations: {fr: a, fr: b}}\ntranslations: {de: x}\ntitle: T\n") == []
    # one that contributes is #126's
    assert [m for _l, _lvl, m in _found("<<: {translations: {fr: a, fr: b}}\n")] == [
        "The translations key `fr` is written again at line 2: this entry is ignored."
    ]


def test_a_source_judged_for_all_its_keys_and_for_some_is_reported_once():
    front = "x:\n  <<: &b {a: 1, a: 2}\n  c: 1\ny:\n  <<: *b\n  a: 3\n"
    assert _lines(front) == [(3, "error")]
    front = "y:\n  <<: &b {a: 1, a: 2}\n  a: 3\nx:\n  <<: *b\n  c: 1\n"
    # `y` overrides `a`, so the source is read for `x` alone: the finding is under `x.<<`
    ((line, _level, message),) = _found(front)
    assert line == 3 and message.startswith("The `x.<<` key `a`")


# ── messages and paths ──

def test_a_name_with_a_hyphen_is_not_bracketed_and_an_odd_key_is_escaped():
    front = "my-map:\n  a: 1\n  a: 2\nweird key:\n  b: 1\n  b: 2\n"
    assert [message for _l, _lvl, message in _found(front)] == [
        "The `my-map` key `a` is written again at line 4: this entry is ignored.",
        'The `["weird key"]` key `b` is written again at line 7: this entry is ignored.',
    ]
    assert _found('"a\\"b": {c: 1, c: 2}\n')[0][2].startswith('The `["a\\"b"]` key `c`')
    assert _found("\"é\": {c: 1, c: 2}\n")[0][2].startswith('The `["é"]` key `c`')


def test_a_path_goes_through_the_questions():
    front = "questions:\n  my-q:\n    type: text\n    default:\n      a: 1\n      a: 2\n"
    (found,) = _found(front)
    assert found[2].startswith("The `questions.my-q.default` key `a` is written again at line 7")


def test_a_key_is_shown_as_written_and_a_backtick_gets_a_longer_fence():
    assert _found('x: {"a": 1, a: 2}\n')[0][2].startswith('The `x` key `"a"` is written again')
    assert _found("x: {'a`b': 1, 'a`b': 2}\n")[0][2].startswith("The `x` key ``'a`b'`` is written again")
    assert _found('"x`y": {a: 1, a: 2}\n')[0][2].startswith('The ``["x`y"]`` key `a`')


def test_the_findings_are_in_source_order_on_one_line():
    front = "x: {a: 1, b: 1, a: 2, b: 2}\n"
    assert [message.split(" key ")[1].split(" ")[0] for _l, _lvl, message in _found(front)] == ["`a`", "`b`"]


# ── translations and field_types are #126's alone ──

def test_translations_and_field_types_keep_their_own_findings():
    front = "translations:\n  fr: a\n  fr: b\nfield_types:\n  a: x\n  a: y\n"
    assert [message for _l, _lvl, message in _found(front)] == [
        "The translations key `fr` is written again at line 4: this entry is ignored.",
        "The field_types key `a` is written again at line 7: this entry is ignored.",
    ]


def test_translations_written_twice_is_one_finding_of_the_generic_check_and_none_of_the_other():
    front = "translations:\n  fr: a\n  fr: b\ntranslations:\n  de: a\n  de: b\n"
    assert _found(front) == [
        (2, "error", "The frontmatter key `translations` is written again at line 5: this entry is ignored."),
        (6, "error", "The translations key `de` is written again at line 7: this entry is ignored."),
    ]


def test_translations_shared_through_an_alias_is_reported_once():
    for front in ("x: &t {fr: a, fr: b}\ntranslations: *t\n", "translations: &t {fr: a, fr: b}\nx: *t\n"):
        assert _found(front) == [(2, "error", "The translations key `fr` is written again at line 2: this entry is ignored.")]


def test_translations_as_the_source_of_a_merge_is_reported_once():
    front = "translations: &t {fr: a, fr: b}\nx:\n  <<: *t\n"
    assert [message for _l, _lvl, message in _found(front)] == [
        "The translations key `fr` is written again at line 2: this entry is ignored."
    ]


def test_a_source_of_translations_is_judged_as_a_mapping_of_its_own():
    front = "translations:\n  <<: {fr: a, fr: b}\n  de: c\n"
    assert _found(front) == [
        (3, "error", "The `translations.<<` key `fr` is written again at line 3: this entry is ignored.")
    ]
    assert _found("base: &b {fr: a}\ntranslations:\n  <<: *b\n  fr: c\n") == []


def test_the_values_of_translations_are_walked():
    assert _lines("translations:\n  fr: {a: 1, a: 2}\n") == [(3, "error")]


# ── when nothing is reported ──

def test_frontmatter_that_cannot_be_read_draws_only_that_error():
    source = _source("title: a\ntitle: b\nx: [unclosed\n")
    result = validate(parse(source, on_frontmatter_error="keep"))
    assert RULE not in result.rules()
    assert "frontmatter-invalid-yaml" in result.rules()


def test_a_fragment_has_no_metadata_to_check():
    assert _found("title: a\ntitle: b\n", fragment=True) == []


def test_a_document_built_from_a_dict_has_no_source_map_and_no_finding():
    document = document_from_dict({"metadata": {"title": "T"}})
    assert [d for d in validate(document).diagnostics if d.rule == RULE] == []


def test_a_document_whose_body_no_longer_fits_the_source_draws_no_finding():
    document = parse(_source("title: a\ntitle: b\n"))
    document.sections[0].blocks.clear()
    assert [d for d in validate(document).diagnostics if d.rule == RULE] == []


def test_a_metadata_changed_after_parsing_draws_no_stale_finding():
    document = parse(_source("title: a\ntitle: b\nsides:\n  - name: X\n    name: Y\n"))
    assert len([d for d in validate(document).diagnostics if d.rule == RULE]) == 2
    document.metadata.sides = []
    document.metadata.title = "new"
    assert [d for d in validate(document).diagnostics if d.rule == RULE] == []


def test_a_metadata_that_refers_to_itself_is_fingerprinted():
    document = parse(_source("x: &a [*a]\ny: {a: 1, a: 2}\n"))
    assert len([d for d in validate(document).diagnostics if d.rule == RULE]) == 1


def test_the_findings_are_data_of_the_document_not_of_the_run():
    source = _source("title: a\ntitle: b\n")
    first, second = parse(source), parse(source)
    validate(first)
    assert first.source_map == second.source_map


# ── the work is linear ──

def _parse_time(front):
    start = time.perf_counter()
    document = parse(_source(front))
    return time.perf_counter() - start, document


def test_a_mapping_of_one_key_written_thousands_of_times_takes_linear_time():
    # The key that repeats each one is found in one pass over the group: a lookup that scans the rest of it for
    # each entry is quadratic (7 s for these 50 000 against 0.4 s). Judged against a mapping of as many keys
    # with no repeat, which costs the YAML reader as much.
    count = 50_000
    wide, _ = _parse_time("".join(f"k{i}: {i}\n" for i in range(count)))
    same, document = _parse_time("x:\n" + "".join(f"  k: {i}\n" for i in range(count)))
    assert same < 2 * wide + 1, (wide, same)
    assert len(document.source_map.repeats) == count - 1


def test_a_deep_mapping_is_walked_without_recursion():
    # YAML itself reads nesting to about 500 levels (deeper is `frontmatter-invalid-yaml`).
    depth = 300
    deep = "".join("  " * i + f"k{i}:\n" for i in range(depth)) + "  " * depth + "a: 1\n" + "  " * depth + "a: 2\n"
    (found,) = _found(deep)
    assert found[2].startswith("The `k0.k1.k2.k3") and ".k299` key `a` is written again" in found[2]
    assert _found(deep.replace("a: 2", "b: 2")) == []
