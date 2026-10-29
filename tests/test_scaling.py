"""Linear time on untrusted input (#94, validator side).

Each shape below took quadratic time on text an attacker or a careless
generator can produce: many markers or directives in one paragraph, a
thousand sections of one title, thousands of blanks on one line, unclosed
comments. Each is checked twice. The first check counts calls, which is
deterministic: the work the old code repeated per item is done once. The
second is one generous time budget at a size where the old code took more
than ten times as long as the budget; it fails only on a gross regression,
never on a slow machine.
"""
from __future__ import annotations

import random
import time

import pytest

from legaldown import directives as directives_module
from legaldown import markdown
from legaldown.directives import lex, mask_directives
from legaldown.parser import parse
from legaldown.validator import circular, core, paths, templates, validate
from legaldown.validator import conditions as conditions_module
from legaldown.validator import units as units_module
from legaldown.validator.conditions import ALWAYS
from legaldown.validator.units import find_markers

_SIDES = """sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
  - name: clients
    parties:
      - name: beta
        type: legal_entity
"""
_QUESTIONS = "questions:\n  a:\n    type: text\n"


def _document(body: str, frontmatter: str = ""):
    return parse(f"---\ntitle: Fixture\n{_SIDES}{frontmatter}---\n\n# Terms {{#terms}}\n\n{body}\n", filename="t.lgd")


def _seconds(call, *args) -> float:
    start = time.perf_counter()
    call(*args)
    return time.perf_counter() - start


class _Counter:
    """Counts the calls of a function it wraps."""

    def __init__(self, monkeypatch, owner, name):
        self.calls = 0
        wrapped = getattr(owner, name)

        def counted(*args, **kwargs):
            self.calls += 1
            return wrapped(*args, **kwargs)

        monkeypatch.setattr(owner, name, counted)


class _CountedPattern:
    """A compiled pattern whose ``match`` calls are counted."""

    def __init__(self, monkeypatch, owner, name):
        self.calls = 0
        self._pattern = getattr(owner, name)
        monkeypatch.setattr(owner, name, self)

    def match(self, *args):
        self.calls += 1
        return self._pattern.match(*args)


# ── Markers in one paragraph (find_markers) ───────────────────────

_MARKER_BODIES = {
    "when": "a {when=x} ",
    "placeholder and anchor": "{{placeholder: p}} {#a} ",
}


@pytest.mark.parametrize("shape", _MARKER_BODIES)
def test_markers_in_one_paragraph_share_one_scan(shape, monkeypatch):
    builds = _Counter(monkeypatch, units_module, "blank_from")
    document = _document(_MARKER_BODIES[shape] * 3000)
    found = find_markers(document)
    assert len(found) == 3000
    assert builds.calls == 1  # the tail test is built once per paragraph, not once per marker


@pytest.mark.parametrize("shape", _MARKER_BODIES)
def test_markers_in_one_paragraph_in_a_time_budget(shape):
    document = _document(_MARKER_BODIES[shape] * 20000)
    assert _seconds(find_markers, document) < 3.0  # 0.2 s now; the old scan took a minute


# ── Sections of one title (free_identifier) ───────────────────────


def test_sections_of_one_title_probe_each_suffix_once(monkeypatch):
    n = 1500
    clashes = _Counter(monkeypatch, core, "_clashes")
    result = validate(parse("# Same\n\ntext\n\n" * n, filename="t.lgd"))
    assert len(result.index.sections) == n
    assert clashes.calls <= 4 * n  # 3 per section; the old probe made about n² / 2


def test_sections_of_one_title_in_a_time_budget():
    document = parse("# Same\n\ntext\n\n" * 10000, filename="t.lgd")
    assert _seconds(validate, document) < 5.0  # 1.4 s now; the old probe took a minute


def _boolean_questions(count):
    questions = {f"q{k}": {"type": "boolean"} for k in range(count + 2)}
    questions["c"] = {"type": "choice", "choices": {f"v{k}": str(k) for k in range(count)}}
    return questions


def _condition_shapes(n):
    """The presences of n headings that share a title, by shape."""
    from legaldown.validator.units import own_presence

    questions = _boolean_questions(n)

    def when(text):
        return own_presence(text, questions)

    return questions, {
        "distinct questions": [when(f"q{k}") for k in range(n)],
        "identical presences": [when("q0")] * n,
        "one question and another each": [when("q0") | when(f"q{k + 1}") for k in range(n)],
        "pure alternation": [when("!q0" if k % 2 == 0 else "q0") for k in range(n)],
        "unconditional and distinct": [when(f"q{k}") if k % 2 else ALWAYS for k in range(n)],
        "the values of one choice question": [when(f"c:v{k}") for k in range(n)],
        "the refused values of one choice question": [when(f"!c:v{k}") for k in range(n)],
        "values and refusals of one choice question": [
            when(f"c:v{k // 2}" if k % 2 else f"!c:v{k // 2 + 1}") for k in range(n)
        ],
    }


@pytest.mark.parametrize("shape", [
    "distinct questions", "identical presences", "one question and another each", "pure alternation",
    "unconditional and distinct", "the values of one choice question",
    "the refused values of one choice question", "values and refusals of one choice question",
])
def test_headings_of_one_title_under_conditions_decide_in_linear_work(shape, monkeypatch):
    n = 1500
    questions, presences = _condition_shapes(n)
    clashes = _Counter(monkeypatch, core, "_clashes")
    decisions = _Counter(monkeypatch, conditions_module.Exclusivity, "excluded")
    pool = core._FreeIdentifiers(set(), questions)
    for presence in presences[shape]:
        pool.declare(pool("same", presence), presence)
    assert clashes.calls <= 2 * n
    assert decisions.calls <= 4 * n  # n² / 2 and more before


def test_headings_with_pairs_of_conditions_decide_without_testing_conditions(monkeypatch):
    """``p_k ∧ q`` and ``p_k ∧ !q`` for every k: each clashes with every
    earlier suffix, which the counts show without testing a condition (the
    suffixes are still looked at, so the time is not linear)."""
    from legaldown.validator.units import own_presence

    n = 800
    questions = _boolean_questions(n)
    decisions = _Counter(monkeypatch, conditions_module.Exclusivity, "excluded")
    pool = core._FreeIdentifiers(set(), questions)
    for k in range(n):
        presence = own_presence(f"q{k // 2 + 1}", questions) | own_presence("q0" if k % 2 else "!q0", questions)
        pool.declare(pool("same", presence), presence)
    assert decisions.calls <= 4 * n  # about 1.5 n² / 2 before


def test_headings_of_one_title_under_conditions_get_their_suffixes():
    count = 300
    questions = "".join(f"  q{k}:\n    type: boolean\n" for k in range(count))
    headings = "".join(f"# Same {{when=q{k}}}\n\ntext\n\n" for k in range(count))
    result = validate(parse(f"---\ntitle: T\nquestions:\n{questions}---\n\n{headings}", filename="t.lgd"))
    assert [entry.identifier for entry in result.index.sections] == ["same"] + [
        f"same-{k}" for k in range(2, count + 1)
    ]


# ── Blanks on one line (insertion-boundary) ───────────────────────


def test_blanks_on_one_line_share_one_index(monkeypatch):
    references = _Counter(monkeypatch, templates._InsertionScan, "_read_reference")
    scans = _Counter(monkeypatch, templates, "_InsertionScan")
    containers = _CountedPattern(monkeypatch, templates, "_CONTAINER_RE")
    result = validate(_document("{{placeholder: a}}" * 2000, _QUESTIONS))
    assert result.diagnostics == []
    assert scans.calls == 1  # one index for the paragraph, however many blanks
    assert containers.calls == 1  # its line's container markers are read once
    assert references.calls == 1


def test_blanks_on_one_line_in_a_time_budget():
    document = _document("{{placeholder: a}}" * 40000, _QUESTIONS)
    assert _seconds(validate, document) < 3.0  # 0.8 s now; the old checks took minutes


# ── Link reference definitions of the insertion boundary, and link destinations (#113) ──

_REFERENCE_LINES = {
    "unclosed labels": "[a {{placeholder: a}}\n",
    "unclosed parenthesis titles": "[a]: u ({{placeholder: a}}\n",
    "unclosed quoted titles": '[a]: u "{{placeholder: a}}\n',
    "unclosed single-quoted titles": "[a]: u '{{placeholder: a}}\n",
    "empty labels": "[]: u {{placeholder: a}}\n",
    "angle destinations with blanks": "[a]: <x {{placeholder: a}} y>\n",  # #119
    "title slots": "[a]: u {{placeholder: a}}\n",  # #119
    "unclosed angle destinations": "[a]: <x {{placeholder: a}} y\n",  # #119
}


@pytest.mark.parametrize("shape", _REFERENCE_LINES)
def test_a_label_or_title_with_no_closer_is_not_read_to_the_end_at_each_line(shape):
    """The pattern for a link reference definition read an unclosed label or title to the end of the text, at every
    line: 5 s for 20000 of them, 6 s for the parenthesis titles. Each closer is found by bisect now."""
    text = _REFERENCE_LINES[shape] * 20000
    lexed = lex(text)
    scan = templates._InsertionScan(text, mask_directives(text, lexed.directives), lexed.directives)
    insertions = [directive for directive in lexed.directives if directive.name == "placeholder"]
    assert len(insertions) == 20000
    assert _seconds(lambda: [scan.problem(insertion) for insertion in insertions]) < 1.0  # 0.1 s now


def test_an_angle_destination_with_no_closer_is_read_once_to_the_end_of_its_line():
    text = "[a]: <" + "x " * 200_000 + "{{placeholder: a}}\n"  # 400000 characters
    lexed = lex(text)
    scan = templates._InsertionScan(text, mask_directives(text, lexed.directives), lexed.directives)
    assert _seconds(lambda: [scan.problem(directive) for directive in lexed.directives]) < 1.0  # 0.2 s now


def test_a_definition_line_and_its_trailing_blanks_are_read_once_for_the_insertions_it_holds(monkeypatch):
    reads = _Counter(monkeypatch, templates._InsertionScan, "_read_reference")
    real = templates._NON_BLANK_RE

    class CountingSearch:
        calls = 0

        def search(self, *args):
            CountingSearch.calls += 1
            return real.search(*args)

    monkeypatch.setattr(templates, "_NON_BLANK_RE", CountingSearch())
    text = "[a]: " + "{{placeholder: a}}" * 3000 + " " * 20000 + "\n"
    lexed = lex(text)
    scan = templates._InsertionScan(text, mask_directives(text, lexed.directives), lexed.directives)
    problems = [scan.problem(directive) for directive in lexed.directives]
    assert reads.calls == 1  # where the definition ends, once
    assert CountingSearch.calls == 1  # and that the blanks after it end the line, once
    assert problems.count("it is in a link reference definition") == 3000


_LINKS = {
    "unclosed destinations": ("[a](x(", ""),
    "unclosed angle destinations": ("[a](<x", ""),
    "unclosed titles": ('[a](<x> "', ""),
    "unclosed single-quoted titles": ("[a](<x> '", ""),
    "unclosed parenthesis titles": ("[a](<x> (", ""),
    "escaped closers": ('[a](<x> "t\\', ""),
    "a backslash run": ("[a](", "\\"),
    "line breaks": ("[a](\n", ""),
    "definitions with angle destinations": ("[a]: <x\n", ""),
    "definitions with unclosed titles": ('[a]: u "\n', ""),
    "definitions with unclosed parenthesis titles": ("[a]: u (\n", ""),
    "definitions with their destinations below": ("[a]:\n", ""),
}


@pytest.mark.parametrize("shape", _LINKS)
def test_link_destinations_and_titles_are_read_in_linear_time(shape):
    """The one scanner (``scan_inline_link``, ``scan_destination``, ``scan_title``), at its three callers: an unclosed
    title ends at the next closer of its kind, which is where the next scan begins; a bare destination at its 33rd
    ``(``; a gap at one line break."""
    unit, repeated = _LINKS[shape]
    text = unit + repeated * 200_000 if repeated else unit * (200_000 // len(unit))
    assert _seconds(markdown.split_link_definitions, text.split("\n")) < 3.0  # milliseconds now
    assert _seconds(lambda: paths.images(text, lex(text))) < 3.0
    assert _seconds(lambda: core._inline_html(text, lex(text))) < 3.0


# ── Unclosed declarations, processing instructions and CDATA sections (#111) ──

_UNCLOSED_HTML = {
    "processing instruction": ("x <?a ", "<?b ?>"),
    "declaration": ("x <!a ", "<!b>"),
    "CDATA section": ("x <![CDATA[ ", "<![CDATA[ b ]]>"),
}


@pytest.mark.parametrize(("opener", "closed"), _UNCLOSED_HTML.values(), ids=_UNCLOSED_HTML.keys())
def test_inline_html_without_a_closer_is_not_matched_at_each_opener(monkeypatch, opener, closed):
    """The pattern reads on to the closer, to the end of the text when there is none: it was tried at every opener.
    With a closer at the end the first opener is matched, to it, and found."""
    real = core.INLINE_HTML_RE

    class Counting:
        calls = 0

        def match(self, text, pos):
            Counting.calls += 1
            return real.match(text, pos)

    monkeypatch.setattr(core, "INLINE_HTML_RE", Counting())
    text = opener * 5000
    assert core._inline_html(text, lex(text)) is None
    assert Counting.calls == 0
    text += closed
    found = core._inline_html(text, lex(text))
    assert found is not None and found.startswith(opener[2:]) and found.endswith(closed[-2:])
    assert Counting.calls == 1


# ── Unclosed comments ─────────────────────────────────────────────


def test_the_lexer_reads_each_unclosed_opener_once(monkeypatch):
    ends = _Counter(monkeypatch, directives_module, "comment_end")
    lexed = lex("x <!-- a " * 5000)
    assert lexed.literals == []
    assert ends.calls == 5000  # no opener is scanned for a closer past the last one


def test_comment_spans_read_each_unclosed_opener_once(monkeypatch):
    ends = _Counter(monkeypatch, markdown, "comment_end")
    assert markdown.comment_spans("<!-- a " * 5000) == []
    assert ends.calls == 5000


def test_a_paragraph_of_unclosed_comments_in_a_time_budget():
    document = _document("x <!-- a " * 40000)
    assert _seconds(validate, document) < 2.0  # 0.1 s now; the old scan took a minute


def test_an_html_block_of_unclosed_comments_in_a_time_budget():
    document = _document("<!-- a " * 40000)
    assert _seconds(validate, document) < 2.0  # 0.01 s now; the old scan took 48 s


# ── The lexer ─────────────────────────────────────────────────────


class _CountedFindall:
    """A compiled pattern whose ``finditer`` calls are counted."""

    def __init__(self, monkeypatch, owner, name):
        self.calls = 0
        self._pattern = getattr(owner, name)
        monkeypatch.setattr(owner, name, self)

    def finditer(self, *args):
        self.calls += 1
        return self._pattern.finditer(*args)

    def __getattr__(self, name):
        return getattr(self._pattern, name)


def test_the_lexer_indexes_the_text_once(monkeypatch):
    """Unmatched backtick runs, malformed directives, and closed literals:
    the backtick runs, the line ends and ``}}``, and the blanked view are each
    made once for the text, not searched or rebuilt for each token."""
    runs = _CountedFindall(monkeypatch, directives_module, "_BACKTICKS_RE")
    finds = _Counter(monkeypatch, directives_module, "find_all")
    closers = _Counter(monkeypatch, directives_module, "comment_closers")
    blanks = _Counter(monkeypatch, directives_module, "_blank_spans")
    lexed = lex("`` x ` <!-- c --> {{ref: a {{ref: \"b `d` " * 3000)
    assert lexed.directives and lexed.literals
    assert runs.calls == 1
    assert finds.calls == 2  # the line ends, and ``}}``
    assert closers.calls == 1  # the ``-->``, at the first comment
    assert blanks.calls == 1


def test_a_comment_opener_in_a_quoted_value_builds_no_closer_list(monkeypatch):
    """The lexer looks for a comment's end only at an opener it reads: this
    one is a directive's text."""
    closers = _Counter(monkeypatch, directives_module, "comment_closers")
    lexed = lex('{{ref: "a <!-- b"}} x --> ' * 1000)
    assert len(lexed.directives) == 1000 and not lexed.directives[0].malformed
    assert lexed.literals == []
    assert closers.calls == 0


_LEXER_SHAPES = {
    # shape: (the unit repeated, how many times, the budget in seconds)
    "code spans": ("x `a` ", 200_000, 5.0),
    "unmatched backtick runs": ("`` x ` ", 200_000, 5.0),
    "closed comments": ("x <!-- a --> ", 200_000, 5.0),
    "unclosed directives": ("{{ref: a", 150_000, 5.0),
}


@pytest.mark.parametrize("shape", _LEXER_SHAPES)
def test_the_lexer_in_a_time_budget(shape):
    unit, n, budget = _LEXER_SHAPES[shape]
    text = unit * n
    assert _seconds(lex, text) < budget  # about 0.5-1.5 s now; the old lexer took a minute or more


# ── Remaining shapes (#110) ───────────────────────────────────────


def test_headings_with_many_distinct_refusals_of_a_choice_question_decide_in_a_time_budget():
    """Each heading refuses 20 of 40 values, in a set no other refuses: the old
    pool summed over every set already declared for each one."""
    values = {f"v{k}": str(k) for k in range(40)}
    questions = {"c": {"type": "choice", "choices": values}}
    rng = random.Random(1)
    sets: set[tuple[int, ...]] = set()
    while len(sets) < 4000:
        sets.add(tuple(sorted(rng.sample(range(40), 20))))
    presences = [
        frozenset().union(*(units_module.own_presence(f"!c:v{k}", questions) for k in refused)) for refused in sets
    ]

    def declare_all():
        pool = core._FreeIdentifiers(set(), questions)
        for presence in presences:
            pool.declare(pool("same", presence), presence)

    assert _seconds(declare_all) < 3.0  # 0.3 s now; 15 s before #110


def test_definitions_in_one_paragraph_look_at_each_insertion_once(monkeypatch):
    class Counting(tuple):
        calls = 0

        def __contains__(self, name):
            Counting.calls += 1
            return super().__contains__(name)

    n = 3000
    monkeypatch.setattr(templates, "_INSERTIONS", Counting(templates._INSERTIONS))
    result = validate(_document(" ".join(f'"Term{k}" {{{{def: t{k}}}}}' for k in range(n))))
    assert not [d for d in result.diagnostics if d.rule == "def-term-variable"]
    assert Counting.calls <= 4 * 2 * n  # the lexer's directives, a few times; about n² before (the definitions' pairs)


def test_definitions_in_one_paragraph_in_a_time_budget():
    document = _document(" ".join(f'"Term{k}" {{{{def: t{k}}}}}' for k in range(15000)))
    assert _seconds(validate, document) < 5.0  # 1.2 s now; 10 s before #110


def test_insertions_inside_many_terms_are_each_reported():
    n = 1000
    questions = "questions:\n  a:\n    type: text\n"
    body = " ".join(f'"T{k} {{{{placeholder: a}}}}" {{{{def: t{k}}}}}' for k in range(n))
    findings = [d for d in validate(_document(body, questions)).diagnostics if d.rule == "def-term-variable"]
    assert len(findings) == n


_ALTERNATIVES = 2000


def _alternatives(kind, n=_ALTERNATIVES):
    """The presences of n alternatives of one choice question: each wants a value, or refuses one."""
    questions = {"c": {"type": "choice", "choices": {f"v{k}": str(k) for k in range(n)}}}
    texts = [f"c:v{k}" if kind == "values" else f"!c:v{k}" for k in range(n)]
    return questions, [units_module.own_presence(text, questions) for text in texts]


@pytest.mark.parametrize("kind", ["values", "refusals"])
def test_a_reference_to_many_alternatives_tests_few_conditions(kind, monkeypatch):
    questions, targets = _alternatives(kind, 1500)
    holds = _Counter(monkeypatch, conditions_module.Condition, "holds")
    assert conditions_module.always_covered(ALWAYS, targets, questions)
    assert holds.calls <= 20 * 1500  # about 3.4 million before #110


@pytest.mark.parametrize("kind", ["values", "refusals"])
def test_a_reference_to_many_alternatives_in_a_time_budget(kind):
    questions, targets = _alternatives(kind)
    assert _seconds(conditions_module.always_covered, ALWAYS, targets, questions) < 1.0  # 0.03 s now; 3 s before


def test_references_of_one_presence_are_checked_once(monkeypatch):
    n = 300
    values = "".join(f"      v{k}: V{k}\n" for k in range(n))
    alternatives = "".join(f'"T" {{{{def: t}}}} means x. {{when=c:v{k}}}\n\n' for k in range(n - 1))
    references = "".join(f'"R{k}" {{{{def: r{k}}}}} means {{{{term: t}}}}.\n\n' for k in range(n))
    document = parse(
        f"---\ntitle: T\nquestions:\n  c:\n    type: choice\n    choices:\n{values}---\n\n# Terms {{#terms}}\n\n"
        f"{alternatives}{references}",
        filename="t.lgd",
    )
    checks = _Counter(monkeypatch, core, "always_covered")
    start = time.perf_counter()
    result = validate(document)
    assert time.perf_counter() - start < 5.0  # 0.1 s now; about 20 s before #110
    assert checks.calls == 1  # 300 references to the one target with the one presence
    unsafe = [d for d in result.diagnostics if d.rule == "condition-reference-unsafe"]
    assert len(unsafe) == n  # the last value has no alternative: each reference is reported


# ── Alternatives named by many references (circular definitions) ──


def _named_alternatives(count: int, same_question: bool):
    """An id of *count* alternatives, one for each value of the choice question ``c``, that name *count* definitions
    of other ids; these name the id, each under a value of ``c`` or of another choice question ``d``."""
    from legaldown.definitions import collect_definitions

    questions = {
        "c": {"type": "choice", "choices": {f"v{k}": str(k) for k in range(count)}},
        "d": {"type": "choice", "choices": {f"w{k}": str(k) for k in range(count)}},
    }
    paragraphs = [f'"T" {{{{def: t}}}} means {{{{term: u{k}}}}}.' for k in range(count)]
    paragraphs += [f'"U" {{{{def: u{k}}}}} means {{{{term: t}}}}.' for k in range(count)]
    document = parse("\n\n".join(paragraphs), filename="t.lgd")
    registered = []
    uses = []
    for number, ref in enumerate(collect_definitions(document)):
        text = f"c:v{number}" if number < count else f"{'c:v' if same_question else 'd:w'}{number - count}"
        presence = units_module.own_presence(text, questions)
        registered.append((ref, presence))
        uses.append(circular.TermUse(None, number, 0, f"u{number}" if number < count else "t", presence))
    return document, registered, uses, questions


def test_references_on_another_question_test_no_pair_of_alternatives(monkeypatch):
    n = 1000
    document, registered, uses, questions = _named_alternatives(n, same_question=False)
    tests = _Counter(monkeypatch, conditions_module.Exclusivity, "excluded")
    circles = circular.find_circular_definitions(document, registered, uses, conditions_module.Exclusivity(questions))
    assert len(circles) == 1 and len(circles[0]) == 2 * n
    assert tests.calls == 0  # n x n pair tests before #110: a reference excludes only what names a question it does


def test_references_on_another_question_in_a_time_budget():
    document, registered, uses, questions = _named_alternatives(2000, same_question=False)
    exclusivity = conditions_module.Exclusivity(questions)
    assert _seconds(circular.find_circular_definitions, document, registered, uses, exclusivity) < 2.0  # 0.06 s; 21 s


def test_pair_tests_stop_at_the_cap(monkeypatch):
    n = 1000
    document, registered, uses, questions = _named_alternatives(n, same_question=True)
    tests = _Counter(monkeypatch, conditions_module.Exclusivity, "excluded")
    monkeypatch.setattr(circular, "MAX_EXCLUSION_TESTS", 100_000)
    exclusivity = conditions_module.Exclusivity(questions)
    start = time.perf_counter()
    circles = circular.find_circular_definitions(document, registered, uses, exclusivity)
    assert time.perf_counter() - start < 5.0  # 0.2 s now; the n x n = 1M pair tests took 1.4 s, 4M take 5 s
    assert tests.calls == 100_000
    # Every definition is on a circle, as without the cap; past it the alternatives are not told
    # apart, so the references left reach them all and their circles are one.
    assert sorted(member for circle in circles for member in circle) == list(range(2 * n))
    assert len(circles) < n


def test_references_on_another_question_through_the_validator_test_no_pair(monkeypatch):
    n = 300
    values = "".join(f"      v{k}: V{k}\n" for k in range(n))
    others = "".join(f"      w{k}: W{k}\n" for k in range(n))
    alternatives = "".join(f'"T" {{{{def: t}}}} means x. {{when=c:v{k}}}\n\n' for k in range(n))
    references = "".join(f'"R{k}" {{{{def: r{k}}}}} means {{{{term: t}}}}. {{when=d:w{k}}}\n\n' for k in range(n))
    document = parse(
        f"---\ntitle: T\nquestions:\n  c:\n    type: choice\n    choices:\n{values}"
        f"  d:\n    type: choice\n    choices:\n{others}---\n\n# Terms {{#terms}}\n\n{alternatives}{references}",
        filename="t.lgd",
    )
    tests = _Counter(monkeypatch, conditions_module.Exclusivity, "excluded")
    start = time.perf_counter()
    validate(document)
    assert time.perf_counter() - start < 10.0  # 1 s now
    assert tests.calls == 0


# ── Included fragments: include-anchor-duplicate ──────────────────


def _expanded_alternatives(alternatives: int, expansions: int):
    """A template with *alternatives* headings of one id, each under a value of the choice question ``c``, and a
    fragment declaring that id, included *expansions* times under the last value: it clashes with the last
    alternative only, after the others it is told apart from."""
    values = "".join(f"      v{k}: V{k}\n" for k in range(alternatives))
    headings = "".join(f"## Part {{#part when=c:v{k}}}\n\nText.\n\n" for k in range(alternatives))
    includes = "".join(f"{{{{include: part.lgd}}}} {{when=c:v{alternatives - 1}}}\n\n" for _ in range(expansions))
    document = parse(
        f"---\ntitle: T\nquestions:\n  c:\n    type: choice\n    choices:\n{values}---\n\n"
        f"# Terms {{#terms}}\n\n{headings}## Rest {{#rest}}\n\n{includes}",
        filename="t.lgd",
    )
    return document, {"part.lgd": "Text.\n\n### Part {#part}\n\nText.\n"}


def test_a_fragment_expanded_many_times_is_looked_for_a_clash_once(monkeypatch):
    alternatives, expansions = 100, 2000
    document, files = _expanded_alternatives(alternatives, expansions)
    satisfiable = _Counter(monkeypatch, conditions_module, "satisfiable")
    tests = _Counter(monkeypatch, conditions_module.Exclusivity, "excluded")
    start = time.perf_counter()
    result = validate(document, resolve=files.get)
    assert time.perf_counter() - start < 10.0  # 0.2 s now; 1.5 s before
    duplicates = [d for d in result.diagnostics if d.rule == "include-anchor-duplicate"]
    assert [(d.file, d.line) for d in duplicates] == [("part.lgd", 3)]
    assert tests.calls <= 2 * alternatives  # the first expansion's heading against the alternatives; none after it
    # The alternatives of the document are told apart by pairs (n² / 2); the expansions add none (n x m before).
    assert satisfiable.calls <= alternatives**2


# ── A flat list: each block is built once ─────────────────────────


def test_each_block_of_a_flat_list_is_built_once(monkeypatch):
    from legaldown.models import Block

    n = 5000
    builds = _Counter(monkeypatch, Block, "__init__")
    document = parse("- item\n" * n, filename="t.lgd")
    (block,) = document.preamble
    assert len(block.items) == n
    assert builds.calls <= n + 10  # a paragraph for each item, and the list; 2 n + 2 when the parser built them twice


def test_a_flat_list_in_a_time_budget():
    source = "- item number one\n" * 100_000
    assert _seconds(parse, source) < 12.0  # 4 s now; 7.4 s before #110


# ── The shapes that stay superlinear, as CONFORMANCE.md documents them ──


def test_headings_with_pairs_of_conditions_in_a_time_budget():
    """``p_k ∧ q`` and ``p_k ∧ !q``: quadratic, documented. 1,000 take 0.7 s; 4,000 take 14 s."""
    n = 1000
    questions = _boolean_questions(n)
    presences = [
        units_module.own_presence(f"q{k // 2 + 1}", questions)
        | units_module.own_presence("q0" if k % 2 else "!q0", questions)
        for k in range(n)
    ]
    pool = core._FreeIdentifiers(set(), questions)

    def declare_all():
        for presence in presences:
            pool.declare(pool("same", presence), presence)

    assert _seconds(declare_all) < 10.0


def test_lists_nested_to_the_depth_cap_in_a_time_budget():
    """Linear in the bytes, at a larger constant: 1,000 nested items, 1 MB, take 3 s."""
    source = "".join(" " * (2 * depth) + "- item\n" for depth in range(1000))
    assert _seconds(parse, source) < 10.0


# ── Review of #110 ────────────────────────────────────────────────


def test_a_dead_alternative_among_many_does_not_make_each_reference_reach_each_alternative():
    """One alternative that never appears used to stop the references on another question from reaching the
    id as one node: 2,000 x 2,000 edges."""
    document, registered, uses, questions = _named_alternatives(2000, same_question=False)
    dead = units_module.own_presence("c:v0", questions) | units_module.own_presence("!c:v0", questions)
    registered[0] = (registered[0][0], dead)
    uses[0] = uses[0]._replace(presence=dead)
    exclusivity = conditions_module.Exclusivity(questions)
    assert _seconds(circular.find_circular_definitions, document, registered, uses, exclusivity) < 2.0  # 0.1 s; 6 s


def test_adding_negative_declarations_to_a_tally_costs_a_few_words_each():
    """The tally keeps a bitmap of the declarations refusing each answer; one bitmap of all of them, rebuilt for each
    declaration added, made adding n of them quadratic (4 s for 800,000)."""
    answers = [f"v{k}" for k in range(8)]
    tally = core._AnswerTally()
    refusals = [frozenset(answers[k % 8 :: 3]) for k in range(800_000)]
    start = time.perf_counter()
    for refusal in refusals:
        tally.add(frozenset(), refusal)
    assert time.perf_counter() - start < 5.0  # 1.5 s now; 8 s when each add rebuilt a bitmap of all
    tally.add(frozenset(), frozenset(answers[2:]))
    assert tally.negative == 800_001
    # Only the last refuses every answer a presence refusing the first two does not.
    assert tally.excluded(frozenset(), frozenset(answers[:2]), answers) == 1


def test_a_reference_to_alternatives_that_each_refuse_many_of_two_thousand_choices_in_a_time_budget():
    """Whether an answer is still possible is a lookup in a set: in a list it was a pass over 2,000 answers for
    each value each alternative refuses (4 s)."""
    n = 2000
    questions = {"c": {"type": "choice", "choices": {f"v{k}": str(k) for k in range(n)}}}
    rng = random.Random(3)
    targets = [
        frozenset(conditions_module.Condition("c", f"v{k}", True) for k in rng.sample(range(n), 100)) for _ in range(n)
    ]
    assert _seconds(conditions_module.always_covered, ALWAYS, targets, questions) < 2.0  # 0.2 s now
