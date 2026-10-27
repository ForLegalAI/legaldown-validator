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

import time

import pytest

from legaldown import directives as directives_module
from legaldown import markdown
from legaldown.directives import lex
from legaldown.parser import parse
from legaldown.validator import conditions as conditions_module
from legaldown.validator import core, templates, validate
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
    decisions = _Counter(monkeypatch, core, "satisfiable_in")
    decisions_in_exclusive = _Counter(monkeypatch, conditions_module, "satisfiable_in")
    pool = core._FreeIdentifiers(set(), questions)
    for presence in presences[shape]:
        pool.declare(pool("same", presence), presence)
    assert clashes.calls <= 2 * n
    assert decisions.calls + decisions_in_exclusive.calls <= 4 * n  # n² / 2 and more before


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
    scans = _Counter(monkeypatch, templates, "_InsertionScan")
    containers = _CountedPattern(monkeypatch, templates, "_CONTAINER_RE")
    references = _CountedPattern(monkeypatch, templates, "_LINK_REFERENCE_RE")
    result = validate(_document("{{placeholder: a}}" * 2000, _QUESTIONS))
    assert result.diagnostics == []
    assert scans.calls == 1  # one index for the paragraph, however many blanks
    assert containers.calls == 1  # its line's container markers are read once
    assert references.calls == 1


def test_blanks_on_one_line_in_a_time_budget():
    document = _document("{{placeholder: a}}" * 40000, _QUESTIONS)
    assert _seconds(validate, document) < 3.0  # 0.8 s now; the old checks took minutes


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
