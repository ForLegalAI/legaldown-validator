"""``include-heading-skip`` under every combination of answers (§15.3, §15.4, §16.11, #108).

The combined document must satisfy §4.1 under every combination of answers to the questions that the
presence of an ``{{include:}}`` paragraph names, with every unit a combination makes absent removed (the
include, and the sections around it). Each combination is tried; a heading is reported once, under the first
that skips; and a ``heading-skip`` of the document that the fragments fill in every combination the heading is
there for is dropped."""
from __future__ import annotations

import pytest

from legaldown import parse, validate
from legaldown.grammar import SKIPPED_RULE
from legaldown.validator.conditions import answer_combinations
from legaldown.validator.fragments import MAX_COMBINATION_QUESTIONS, MAX_COMBINATIONS

_FRONT = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    label: Provider\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    label: Client\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)


def _front(*questions: str, choices: dict[str, list[str]] | None = None) -> str:
    """The fixture's frontmatter with these boolean questions and these choice questions."""
    lines = "".join(f"  {q}:\n    type: boolean\n" for q in questions)
    for q, values in (choices or {}).items():
        lines += f"  {q}:\n    type: choice\n    choices:\n" + "".join(f"      {v}: {v.title()}\n" for v in values)
    return _FRONT.replace("---\n\n", f"questions:\n{lines}---\n\n")


class _Loader:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def __call__(self, path: str) -> str | None:
        return self.files.get(path)


def _run(text: str, files: dict[str, str]):
    return validate(parse(text, filename="main.lgd"), resolve=_Loader(files))


def _found(result, *rules: str):
    wanted = set(rules) or {"include-heading-skip", "heading-skip"}
    return [(d.rule, d.file, d.line) for d in result.diagnostics if d.rule in wanted]


def _messages(result, rule: str = "include-heading-skip") -> list[str]:
    return [d.message for d in result.diagnostics if d.rule == rule]


def _at(text: str, needle: str) -> int:
    return next(number for number, line in enumerate(text.split("\n"), 1) if needle in line)


# ── What a combination decides ───────────────────────────────────────────────

def test_a_skip_that_only_a_present_include_makes_is_reported_once_under_the_answers_that_make_it():
    text = _front("x", "y") + "# Scope {#scope}\n\n{{include: p.lgd}} {when=x}\n\n{{include: q.lgd}} {when=y}\n"
    # p skips (1 to 3) when it is there; q does not. Four combinations, one finding.
    result = _run(text, {"p.lgd": "### Deep {#deep}\n", "q.lgd": "## Fine {#fine}\n"})
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    (message,) = _messages(result)
    assert message.endswith("It is so when x=true, y=true.")


def test_the_first_combination_that_skips_is_the_one_named():
    # True before false, the questions in the order they are first used: x=true, y=true skips (p alone), and so does x=true, y=false.
    text = _front("y", "x") + "# Scope {#scope}\n\n{{include: q.lgd}} {when=y}\n\n{{include: p.lgd}} {when=x}\n"
    result = _run(text, {"p.lgd": "### Deep {#deep}\n", "q.lgd": "## Fine {#fine}\n"})
    (message,) = _messages(result)
    # y is used first, so it changes slowest; with y=true the fragment q is before p, and 2 to 3 is no skip.
    assert message.endswith("It is so when y=false, x=true.")


def test_a_skip_only_the_section_that_holds_the_include_makes_is_found_under_that_sections_condition():
    text = _front("x") + "# Scope {#scope}\n\n## Opt {#opt when=x}\n\n{{include: p.lgd}}\n"
    result = _run(text, {"p.lgd": "#### Deep {#deep}\n"})
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    assert _messages(result)[0].endswith("It is so when x=true.")


def test_a_section_taken_out_changes_what_comes_before_a_fragments_heading():
    # With x false the section is gone, and the fragment after it follows # Scope: 1 to 3.
    text = _front("x", "y") + (
        "# Scope {#scope}\n\n## Opt {#opt when=x}\n\nText.\n\n{{include: p.lgd}} {when=y}\n"
    )
    # ... but the include is in Opt's section, so it goes with it: nothing to skip.
    assert _found(_run(text, {"p.lgd": "### Three {#three}\n"})) == []
    text = _front("x", "y") + (
        "# Scope {#scope}\n\n## Opt {#opt when=x}\n\nText.\n\n# Two {#two}\n\n{{include: p.lgd}} {when=y}\n"
    )
    assert _found(_run(text, {"p.lgd": "### Three {#three}\n"})) == [("include-heading-skip", "p.lgd", 1)]


def test_a_choice_with_three_values_is_tried_for_each():
    text = _front(choices={"forum": ["courts", "arbitration", "mediation"]}) + (
        "# Scope {#scope}\n\n{{include: p.lgd}} {when=forum:arbitration}\n"
    )
    result = _run(text, {"p.lgd": "### Deep {#deep}\n"})
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    assert _messages(result)[0].endswith("It is so when forum=arbitration.")
    # A value that does not skip is no finding.
    assert _found(_run(text, {"p.lgd": "## Fine {#fine}\n"})) == []


def test_alternatives_that_are_never_together_do_not_skip_through_each_other():
    # After # Scope, ## Mid and ### P, a (## A2) lowers the level and b (#### B4) rises by one from P: each is
    # fine on its own, and a before b would skip 2 to 4 if both were there, which they never are.
    text = _front("x") + (
        "# Scope {#scope}\n\n## Mid {#mid}\n\n### P {#p}\n\n"
        "{{include: a.lgd}} {when=x}\n\n{{include: b.lgd}} {when=!x}\n"
    )
    files = {"a.lgd": "## A2 {#a2}\n", "b.lgd": "#### B4 {#b4}\n"}
    assert _found(_run(text, files)) == []
    # Not alternatives: the same two, both there, skip.
    both = text.replace("{when=!x}", "{when=x}")
    assert _found(_run(both, files)) == [("include-heading-skip", "b.lgd", 1)]
    assert _messages(_run(both, files))[0].endswith("It is so when x=true.")


def test_a_template_with_no_conditional_include_is_judged_once_and_the_message_is_as_it_was():
    text = _front("x") + "# Scope {#scope}\n\n{{include: p.lgd}}\n\nText. {when=x}\n"
    result = _run(text, {"p.lgd": "### Deep {#deep}\n"})
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    (message,) = _messages(result)
    assert "It is so when" not in message and message.endswith("(§4.1, §12.2).")


def test_a_document_that_is_not_a_template_is_judged_as_before():
    text = _FRONT + "# Scope {#scope}\n\n{{include: p.lgd}}\n"
    result = _run(text, {"p.lgd": "### Deep {#deep}\n"})
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    assert "It is so when" not in _messages(result)[0]


def test_a_question_no_include_names_is_not_varied():
    # y is on a paragraph, not on the include: the include is there whatever y is.
    text = _front("x", "y") + "# Scope {#scope}\n\n{{include: p.lgd}} {when=x}\n\nText. {when=y}\n"
    (message,) = _messages(_run(text, {"p.lgd": "### Deep {#deep}\n"}))
    assert message.endswith("It is so when x=true.") and "y=" not in message


# ── The heading-skip of the document the fragments fill ──────────────────────

def test_an_unconditional_include_fills_the_level_so_the_documents_heading_skip_goes():
    text = _front("x") + "# One {#one}\n\n{{include: p.lgd}}\n\n### Three {#three}\n"
    assert _found(_run(text, {"p.lgd": "## Deep {#deep}\n"})) == []


def test_a_conditional_include_leaves_the_documents_heading_skip_and_adds_no_second_finding():
    text = _front("x") + "# One {#one}\n\n{{include: p.lgd}} {when=x}\n\n### Three {#three}\n"
    result = _run(text, {"p.lgd": "## Deep {#deep}\n"})
    # With x false, # One is followed by ### Three: that is heading-skip's, and the include has nothing to add.
    assert _found(result) == [("heading-skip", "main.lgd", _at(text, "### Three"))]


def test_a_heading_that_is_there_only_when_the_conditional_include_is_has_its_skip_filled():
    text = _front("x") + "# One {#one}\n\n{{include: p.lgd}} {when=x}\n\n### Three {#three when=x}\n"
    assert _found(_run(text, {"p.lgd": "## Deep {#deep}\n"})) == []
    # Not when it is there whatever x is.
    text = text.replace("{#three when=x}", "{#three}")
    assert [r for r, *_ in _found(_run(text, {"p.lgd": "## Deep {#deep}\n"}))] == ["heading-skip"]


def test_a_documents_heading_that_a_fragment_leaves_a_skip_before_is_reported_at_its_own_line():
    # # One, an include of ## Deep (when x), # Two... no: ### Four after a fragment ending in ## Deep.
    text = _front("x") + "# One {#one}\n\n## Mid {#mid}\n\n{{include: p.lgd}} {when=x}\n\n### Three {#three}\n"
    result = _run(text, {"p.lgd": "#### Four {#four}\n"})
    # x true: Mid(2), Four(4) skips, in the fragment; and Three(3) after Four is fine.
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    text = _front("x") + "# One {#one}\n\n## Mid {#mid}\n\n{{include: p.lgd}} {when=x}\n\n#### Four {#four}\n"
    result = _run(text, {"p.lgd": "### Deep {#deep}\n"})
    assert _found(result) == [("heading-skip", "main.lgd", _at(text, "#### Four"))]  # as with x false, 2 to 4


def test_a_heading_of_the_document_after_a_fragments_is_reported_at_its_own_line_under_the_answers_that_make_it():
    # # One, ## Two, ### Three, then (x) a fragment that goes back up to ## Back, then #### Four: fine in the
    # document (3 to 4), and a skip (2 to 4) when the fragment is there.
    text = _front("x") + (
        "# One {#one}\n\n## Two {#two}\n\n### Three {#three}\n\n{{include: p.lgd}} {when=x}\n\n#### Four {#four}\n"
    )
    result = _run(text, {"p.lgd": "## Back {#back}\n"})
    assert _found(result) == [("include-heading-skip", "main.lgd", _at(text, "#### Four"))]
    assert _messages(result)[0].endswith("It is so when x=true.")
    # The fragment that leaves it as it was (a level the heading can follow) leaves nothing to report.
    assert _found(_run(text, {"p.lgd": "#### Back {#back}\n"})) == []
    # Where the document already skips, that is heading-skip's, found with or without the fragment.
    text = text.replace("#### Four", "##### Four")
    result = _run(text, {"p.lgd": "## Back {#back}\n"})
    assert _found(result) == [("heading-skip", "main.lgd", _at(text, "##### Four"))]


def test_a_missing_include_before_the_heading_blocks_the_fill():
    text = _front("x") + "# One {#one}\n\n{{include: p.lgd}}\n\n{{include: gone.lgd}}\n\n### Three {#three}\n"
    result = _run(text, {"p.lgd": "## Deep {#deep}\n"})
    assert sorted(_found(result)) == [("heading-skip", "main.lgd", _at(text, "### Three"))]


def test_a_heading_the_include_fills_under_a_conditional_section_is_filled_when_the_section_is_there():
    text = _front("x") + "# One {#one when=x}\n\n{{include: p.lgd}}\n\n### Three {#three}\n"
    assert _found(_run(text, {"p.lgd": "## Deep {#deep}\n"})) == []


def test_a_section_with_a_condition_is_varied_too_whether_or_not_an_include_is_under_it():
    # r is on a section that holds no include, and is tried all the same (a combination takes the section out):
    # the first combination names it, before x, which an include after it names.
    text = _front("r", "x") + (
        "# Scope {#scope}\n\n## Opt {#opt when=r}\n\nText.\n\n## Other {#other}\n\n{{include: p.lgd}} {when=x}\n"
    )
    result = _run(text, {"p.lgd": "#### Deep {#deep}\n"})
    assert _found(result) == [("include-heading-skip", "p.lgd", 1)]
    assert _messages(result)[0].endswith("It is so when r=true, x=true.")


def test_sections_count_towards_the_limit_of_questions():
    # 13 questions, each on a section of its own, and one include with no condition: too many to try.
    count = MAX_COMBINATION_QUESTIONS + 1
    text = _front(*(f"q{i}" for i in range(count))) + "# Scope {#scope}\n\n" + "".join(
        f"## S{i} {{#s{i} when=q{i}}}\n\nText.\n\n" for i in range(count)
    ) + "{{include: p.lgd}}\n"
    result = _run(text, {"p.lgd": "## Fine {#fine}\n"})
    warning = next(d for d in result.diagnostics if d.rule == SKIPPED_RULE)
    assert "include-heading-skip under every combination of answers" in warning.message
    # ... and 12 are not.
    text = text.replace(f"## S{count - 1} {{#s{count - 1} when=q{count - 1}}}", f"## S{count - 1} {{#s{count - 1}}}")
    result = _run(text, {"p.lgd": "## Fine {#fine}\n"})
    assert "every combination" not in next(d for d in result.diagnostics if d.rule == SKIPPED_RULE).message


def test_the_work_does_not_grow_with_the_combinations_times_the_headings():
    # 12 questions (4,096 combinations), each with a fragment of 800 headings (the walk follows at most 10,000):
    # a run of headings that are there together is judged once, not once for each combination.
    import time

    count = MAX_COMBINATION_QUESTIONS
    files = {f"p{i}.lgd": "".join(f"## H{i}x{k} {{#h{i}x{k}}}\n\n" for k in range(800)) for i in range(count)}
    document = parse(_many(count), filename="main.lgd")
    started = time.perf_counter()
    result = validate(document, resolve=_Loader(files))
    elapsed = time.perf_counter() - started
    assert _found(result) == []
    assert "every combination" not in next(d for d in result.diagnostics if d.rule == SKIPPED_RULE).message
    assert elapsed < 6, f"{elapsed:.1f}s"  # about 0.3 s; 13 s when each combination walked every heading


# ── The limits ───────────────────────────────────────────────────────────────

def _many(count: int, **kwargs) -> str:
    names = [f"q{i}" for i in range(count)]
    body = "# Scope {#scope}\n\n" + "".join(f"{{{{include: p{i}.lgd}}}} {{when=q{i}}}\n\n" for i in range(count))
    return _front(*names, **kwargs) + body


def test_twelve_questions_are_tried_in_all_their_combinations():
    count = MAX_COMBINATION_QUESTIONS
    assert 2 ** count == MAX_COMBINATIONS
    files = {f"p{i}.lgd": "## Fine\n" for i in range(count)}
    files["p0.lgd"] = "### Deep {#deep}\n"
    result = _run(_many(count), files)
    assert _found(result) == [("include-heading-skip", "p0.lgd", 1)]
    # Under the first combination in order, every question named.
    assert _messages(result)[0].endswith(f"It is so when {', '.join(f'q{i}=true' for i in range(count))}.")
    # A fragment before it that fills the level is what makes the skip: only without it (q0..q6 false) does p7 skip.
    files["p0.lgd"] = "## Fine\n"
    files["p7.lgd"] = "### Deep {#deep}\n"
    result = _run(_many(count), files)
    (message,) = _messages(result)
    assert "q0=false, q1=false, q2=false, q3=false, q4=false, q5=false, q6=false, q7=true" in message
    assert "every combination" not in next(d for d in result.diagnostics if d.rule == SKIPPED_RULE).message


def test_more_questions_than_the_cap_are_judged_once_with_every_include_present_and_the_warning_says_so():
    count = MAX_COMBINATION_QUESTIONS + 1
    files = {f"p{i}.lgd": "## Fine\n" for i in range(count)}
    files["p0.lgd"] = "### Deep {#deep}\n"
    result = _run(_many(count), files)
    assert _found(result) == [("include-heading-skip", "p0.lgd", 1)]
    assert "It is so when" not in _messages(result)[0]
    warning = next(d for d in result.diagnostics if d.rule == SKIPPED_RULE)
    assert (
        f"include-heading-skip under every combination of answers to the conditions on the includes (more than "
        f"{MAX_COMBINATION_QUESTIONS} questions, or {MAX_COMBINATIONS:,} combinations), which is judged with every "
        f"include present instead"
    ) in warning.message


def test_more_combinations_than_the_cap_are_judged_the_same_way():
    # 8 questions of 3 values: 6561 combinations.
    names = [f"c{i}" for i in range(8)]
    text = _front(choices={n: ["a", "b", "c"] for n in names}) + "# Scope {#scope}\n\n" + "".join(
        f"{{{{include: p{i}.lgd}}}} {{when=c{i}:a}}\n\n" for i in range(8)
    )
    files = {f"p{i}.lgd": "## Fine\n" for i in range(8)}
    files["p0.lgd"] = "### Deep {#deep}\n"
    result = _run(text, files)
    assert _found(result) == [("include-heading-skip", "p0.lgd", 1)]
    assert "judged with every include present instead" in next(d for d in result.diagnostics if d.rule == SKIPPED_RULE).message


def test_past_the_cap_a_conditional_include_fills_nothing_as_before():
    # Past the cap there is one pass, and an include that may not be there fills no level: the heading-skip stands
    # (within the cap it is dropped for an include with no condition only; see the tests above).
    count = MAX_COMBINATION_QUESTIONS + 1
    names = [f"q{i}" for i in range(count)]
    text = _front(*names) + "# One {#one}\n\n" + "".join(
        f"{{{{include: p{i}.lgd}}}} {{when=q{i}}}\n\n" for i in range(count)
    ) + "### Three {#three}\n"
    files = {f"p{i}.lgd": "## Fine\n" for i in range(count)}
    assert [r for r, *_ in _found(_run(text, files))] == ["heading-skip"]


# ── answer_combinations ──────────────────────────────────────────────────────

def test_the_combinations_are_in_a_fixed_order_with_the_questions_in_the_order_they_are_first_used():
    questions = {
        "a": {"type": "boolean"}, "b": {"type": "boolean"},
        "c": {"type": "choice", "choices": {"one": "1", "two": "2"}},
    }
    from legaldown.validator.conditions import Condition

    presences = [frozenset({Condition("c", "two"), Condition("b")}), frozenset({Condition("a", None, True)})]
    asked, combinations = answer_combinations(presences, questions, max_questions=12, max_combinations=4096)
    # By first use; within one presence by declaration (b before c).
    assert asked == ["b", "c", "a"]
    assert combinations[0] == {"b": True, "c": "one", "a": True}
    assert combinations[1] == {"b": True, "c": "one", "a": False}
    assert combinations[2] == {"b": True, "c": "two", "a": True}
    assert len(combinations) == 8


def test_a_question_with_no_answer_is_left_out_and_its_conditions_hold():
    from legaldown.validator.conditions import Condition, holds_under

    questions = {"c": {"type": "choice", "choices": {}}, "a": {"type": "boolean"}}
    presences = [frozenset({Condition("c", "x"), Condition("a")})]
    asked, combinations = answer_combinations(presences, questions, max_questions=12, max_combinations=4096)
    assert asked == ["a"] and combinations == [{"a": True}, {"a": False}]
    assert holds_under(presences[0], {"a": True}) and not holds_under(presences[0], {"a": False})
    # Not declared at all, or no mapping of questions: no answer, no combination to try.
    assert answer_combinations(presences, None, max_questions=12, max_combinations=4096) == ([], [{}])


@pytest.mark.parametrize(("questions", "count"), [(13, 8192), (12, 4096)])
def test_the_limits_of_the_combinations(questions, count):
    declared = {f"q{i}": {"type": "boolean"} for i in range(questions)}
    from legaldown.validator.conditions import Condition

    presences = [frozenset({Condition(q)}) for q in declared]
    tried = answer_combinations(presences, declared, max_questions=12, max_combinations=4096)
    assert (tried is None) == (questions > 12) and (tried is None or len(tried[1]) == count)
