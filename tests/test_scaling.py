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

from legaldown import assembly, markdown, positions
from legaldown import directives as directives_module
from legaldown import parser as parser_module
from legaldown.directives import lex
from legaldown.parser import parse
from legaldown.validator import circular, core, hazards, paths, templates, validate
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
    scans = _Counter(monkeypatch, templates, "_InsertionScan")
    containers = _CountedPattern(monkeypatch, templates, "_CONTAINER_RE")
    result = validate(_document("{{placeholder: a}}" * 2000, _QUESTIONS))
    assert result.diagnostics == []
    assert scans.calls == 1  # one index for the paragraph, however many blanks
    assert containers.calls == 1  # its line's container markers are read once


def test_blanks_on_one_line_in_a_time_budget():
    document = _document("{{placeholder: a}}" * 40000, _QUESTIONS)
    assert _seconds(validate, document) < 3.0  # 0.8 s now; the old checks took minutes


# ── Link reference definitions of the insertion boundary (#129), and link destinations (#113) ──

_REFERENCE_LINES = {
    "unclosed labels": "[a {{placeholder: a}}\n",
    "unclosed parenthesis titles": "[a]: u ({{placeholder: a}}\n",
    "unclosed quoted titles": '[a]: u "{{placeholder: a}}\n',
    "unclosed single-quoted titles": "[a]: u '{{placeholder: a}}\n",
    "empty labels": "[]: u {{placeholder: a}}\n",
    "angle destinations with blanks": "[a]: <x {{placeholder: a}} y>\n",
    "title slots": "[a]: u {{placeholder: a}}\n",
    "unclosed angle destinations": "[a]: <x {{placeholder: a}} y\n",
}


@pytest.mark.parametrize("shape", _REFERENCE_LINES)
def test_a_label_or_title_with_no_closer_costs_no_more_than_a_line_each(shape):
    """Twenty thousand insertions in lines of one paragraph: the paragraph is one region of far more combinations of
    answers than are tried (``hazards.CAP``), so it is judged by the conservative rule, in one pass; and where
    no line has a ``]:`` (the first shape) nothing but the lines is read. The old pattern read an unclosed label
    or title to the end of the text at every line: 5 s for 20000 of them."""
    document = _document(_REFERENCE_LINES[shape] * 20000, _QUESTIONS)
    limit = 2.0 if shape == "unclosed labels" else 3.0  # 0.6 s and 1.5 s now
    assert _seconds(validate, document) < limit


def test_the_insertions_of_a_paragraph_that_is_too_large_to_try_are_not_assembled(monkeypatch):
    """The cap is by combinations: nothing is assembled for a region over it, however many lines it has."""
    assembled = _Counter(monkeypatch, hazards._Assembler, "_enumerate")
    result = validate(_document("[a]: u {{placeholder: a}}\n" * 3000, _QUESTIONS))
    assert assembled.calls == 0
    assert len([d for d in result.diagnostics if d.rule == "insertion-boundary"]) == 3000


def test_the_body_is_read_once_and_with_the_layout_and_the_markers_the_parser_and_the_validator_made(monkeypatch):
    """The regions are small templates, each read for itself; the body is read once, and is not laid out again by
    ``_read_body`` (the parser's layout is the document's), nor are its markers found again."""
    found = []
    real_find = assembly.find_markers
    monkeypatch.setattr(
        assembly, "find_markers",
        lambda document, *args: found.append(sum(len(s.blocks) for s in document.sections)) or real_find(document, *args),
    )
    large = []

    def counting(owner):
        real = owner._layout

        def layout(lines):
            if len(lines) > 100:
                large.append(len(lines))
            return real(lines)

        monkeypatch.setattr(owner, "_layout", layout)

    counting(assembly)
    counting(parser_module)
    works = _Counter(monkeypatch, hazards._Assembler, "__init__")
    validate(_document("[a]: u {{placeholder: a}}\n\n" * 500, _QUESTIONS))
    assert works.calls == 1
    assert large == []
    assert max(found) <= 2  # the regions' (a section of one or two blocks); the body's are the validator's


def test_a_document_with_no_label_is_not_read_for_definitions(monkeypatch):
    works = _Counter(monkeypatch, hazards._Assembler, "__init__")
    validate(_document("[a {{placeholder: a}}\n\nPay {{placeholder: a}}: now\n" * 500, _QUESTIONS))
    assert works.calls == 0  # no line holds ``]:``: only the lines are looked at


def test_many_separate_definitions_are_assembled_by_shape_and_within_the_budget(monkeypatch):
    """The same paragraph a thousand times is assembled once; paragraphs that differ are assembled one after
    another until the document's budget (of work: the lines of each assembly) is spent, and the rest are judged by
    the conservative rule."""
    emitted = _Counter(monkeypatch, assembly, "_emit")
    result = validate(_document("[x]: u {{placeholder: a}}\n\n" * 2000, _QUESTIONS))
    assert 0 < emitted.calls <= len(hazards.TEXT_WITNESSES)  # (decided by the first answers that show it)
    assert len([d for d in result.diagnostics if d.rule == "insertion-boundary"]) == 2000
    emitted.calls = 0
    monkeypatch.setattr(hazards, "BUDGET", 1500)
    body = "".join(f"[Option {n}]: pay {{{{placeholder: a}}}}.\n\n" for n in range(600))
    result = validate(_document(body, _QUESTIONS))
    tried = 1500 // (31 * 4 + 100 + 20)  # 31 answers, each a line, an insertion and two for an assembly; and a region's
    assert emitted.calls == tried * 31
    # The first paragraphs are tried and clean; the rest are over-reported (the conservative rule).
    assert len([d for d in result.diagnostics if d.rule == "insertion-boundary"]) == 600 - tried


def test_a_large_table_and_a_large_paragraph_are_bounded_by_the_work_not_by_the_lines(monkeypatch):
    """A table of four thousand rows is one region, and no definition can begin in it: it is not assembled, and
    nothing is reported. A paragraph of four thousand lines that a definition begins is one too, of more work than
    the cap allows: it is not assembled either, and is judged by the conservative rule, in time."""
    emitted = _Counter(monkeypatch, assembly, "_emit")
    table = "| Item | Amount |\n|---|---|\n" + "".join(f"| [Fee]: base{n} | {{{{placeholder: a}}}} |\n" for n in range(4000))
    start = time.perf_counter()
    result = validate(_document(table, _QUESTIONS))
    assert time.perf_counter() - start < 3.0  # 0.4 s now
    assert [d for d in result.diagnostics if d.rule == "insertion-boundary"] == []
    paragraph = "[x]: u\n" + "".join(f"Line {n} has {{{{placeholder: a}}}} in it.\n" for n in range(4000))
    start = time.perf_counter()
    result = validate(_document(paragraph, _QUESTIONS))
    assert time.perf_counter() - start < 3.0  # 0.3 s now
    assert len([d for d in result.diagnostics if d.rule == "insertion-boundary"]) == 4000
    assert emitted.calls == 0


def test_a_long_region_costs_its_lines_for_each_assembly():
    """Five or more assemblies of a hundred lines are about 500 of the document's work; of four thousand lines, more than
    a region's (16384): the conservative rule judges the paragraph."""
    short = "[x]: u\n" + "Plain.\n" * 50 + "Pay {{placeholder: a}} now.\n" + "Plain.\n" * 49
    long = "[x]: u\n" + "Plain.\n" * 2000 + "Pay {{placeholder: a}} now.\n" + "Plain.\n" * 1999
    assert not [d for d in validate(_document(short, _QUESTIONS)).diagnostics if d.rule == "insertion-boundary"]
    assert len([d for d in validate(_document(long, _QUESTIONS)).diagnostics if d.rule == "insertion-boundary"]) == 1


_QUESTIONS_P1 = "questions:\n  p1:\n    type: text\n"
_CHOOSE_QUESTIONS = "questions:\n  p1:\n    type: text\n" + "".join(f"  c{n}:\n    type: boolean\n" for n in range(1, 7))


def _choices(count: int) -> str:
    return " ".join(f'{{{{choose: c{n}, true="a", false="b"}}}}' for n in range(1, count + 1))


def test_the_work_of_a_region_is_counted_in_its_characters_too(monkeypatch):
    """A paragraph with a line of 300 KB, six choices and a text blank is 31 x 64 combinations of 300 KB each: it is
    far over what a region may cost, and is judged by the conservative rule without being assembled (it took
    107 s for four of them)."""
    emitted = _Counter(monkeypatch, assembly, "_emit")
    words = " ".join(["word"] * 50000)
    body = "\n\n".join(f"[a{k}]: u\nx {_choices(6)} {{{{placeholder: p1}}}} {words}" for k in range(4))
    document = _document(body, _CHOOSE_QUESTIONS)
    assert _seconds(validate, document) < 8.0  # 0.3 s now
    assert emitted.calls == 0
    one = _document(f"[a]: u\nx {_choices(6)} {{{{placeholder: p1}}}} " + " ".join(["word"] * 60000), _CHOOSE_QUESTIONS)
    assert _seconds(validate, one) < 8.0


def test_questions_with_one_answer_are_not_combinations_and_a_line_of_choices_is_read_in_linear_time():
    """A fragment (its questions are its host's) of 8000 choices of one phrase each: none varies, so none is
    enumerated (the combinations had 8000 members each, for 8000 insertions: quadratic); and 32000 on one line are
    read for a label once (the line was cut at each, again quadratic)."""
    lines = "[a]: u\n" + '{{choose: zz, a="w"}}\n' * 8000
    assert _seconds(lambda: validate(parse(lines), fragment=True)) < 4.0  # 0.5 s now
    one = "[a]: u " + '{{choose: zz, a="w"}}' * 32000 + "\n"
    assert _seconds(lambda: validate(parse(one), fragment=True)) < 6.0


def test_questions_that_the_template_would_declare_are_charged_and_one_answer_ones_are_not(monkeypatch):
    """Twelve paragraphs of 3000 choices each, every one its own question with its own phrase (a 3.2 MB fragment
    with 9000 each took 55 s: the template declared them, 0.45 ms each of YAML). They do not vary, so a region of
    them is clean without being read; and with a text blank among them, they are declared in code and not charged:
    each region is tried, and the blank, which is the definition's title, is reported."""
    body = "\n\n".join(
        f"[a{r}]: u\n" + "\n".join(f'{{{{choose: z{r}x{n}, k{n}="w{n}"}}}}' for n in range(3000)) for r in range(12)
    )
    start = time.perf_counter()
    result = validate(parse(body), fragment=True)
    assert time.perf_counter() - start < 12.0  # 3 s now
    assert [d for d in result.diagnostics if d.rule == "insertion-boundary"] == []
    emitted = _Counter(monkeypatch, assembly, "_emit")
    mixed = "\n\n".join(
        f"[a{r}]: u {{{{placeholder: p1}}}}\n" + "\n".join(f'{{{{choose: z{r}x{n}, k{n}="w{n}"}}}}' for n in range(150))
        for r in range(12)
    )
    start = time.perf_counter()
    result = validate(_document(mixed, _QUESTIONS_P1), fragment=False)
    assert time.perf_counter() - start < 12.0
    assert emitted.calls <= 12 * 31  # (the regions that are tried are tried with 31 answers)
    found = [d for d in result.diagnostics if d.rule == "insertion-boundary"]
    assert [d.message.split("'")[1] for d in found] == ["{{placeholder: p1}}"] * 12  # one in each region, none a choice
    assert not any("too many" in d.message for d in found)  # and tried: none is the conservative rule's


def test_many_separate_definitions_in_a_time_budget():
    document = _document("[x]: u {{placeholder: a}}\n\n" * 2000, _QUESTIONS)
    assert _seconds(validate, document) < 5.0  # 1 s now
    document = _document("[a]: u " + "{{placeholder: a}} " * 3000, _QUESTIONS)
    assert _seconds(validate, document) < 2.0  # three thousand insertions on one line: 0.2 s now


@pytest.mark.parametrize("line", ["[a]: u {{placeholder: a}}\n", "[a {{placeholder: a}}\n"], ids=["fallback", "prefilter"])
def test_a_document_of_definitions_is_validated_in_linear_time(line):
    """Double the input, at most about double the time (the least of three runs each)."""
    small = _document(line * 10000, _QUESTIONS)
    large = _document(line * 20000, _QUESTIONS)
    first = min(_seconds(validate, small) for _ in range(3))
    second = min(_seconds(validate, large) for _ in range(3))
    assert second < 3.5 * first + 0.2, (first, second)


class _CountingPlaces(dict):
    """The places of a leaf (``positions._Leaf.places``), counting each time one is read."""

    def __init__(self, places, counts):
        super().__init__(places)
        self.counts = counts

    def __contains__(self, key):
        self.counts[0] += 1
        return super().__contains__(key)

    def __getitem__(self, key):
        self.counts[0] += 1
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.counts[0] += 1
        return super().get(key, default)

    def __iter__(self):
        self.counts[0] += 1
        return super().__iter__()


def test_the_leaf_of_each_fragment_of_a_long_list_is_found_without_a_scan_of_its_items(monkeypatch):
    """The line of a diagnostic in an item looked up the item's leaf among those of the list: 10 s for 16000
    diagnostics. A map made once for the list finds it now. Counted, so that the test does not depend on the speed of the machine: the
    leaves read are the number of items, a few times, not its square."""
    reads = [0]
    read_leaves = positions.Locator._read_leaves

    def counting(self, section, index):
        leaves = read_leaves(self, section, index)
        for leaf in leaves or ():
            leaf.places = _CountingPlaces(leaf.places, reads)
        return leaves

    monkeypatch.setattr(positions.Locator, "_read_leaves", counting)
    item = "- &{{placeholder: a}}\n"  # a diagnostic (insertion-boundary) at each item
    items = 4000
    result = validate(_document(item * items, _QUESTIONS))
    assert len(result.diagnostics) == items
    assert 0 < reads[0] <= 4 * items, reads[0]  # 1 or 2 per item now; the scan made 8,000,000
    monkeypatch.undo()
    document = _document(item * 16000, _QUESTIONS)
    assert _seconds(validate, document) < 6.0  # 2.5 s now; the scan took 10 s


def test_an_angle_destination_with_no_closer_is_read_to_the_end_of_its_line_once_per_answer():
    text = "[a]: <" + "x " * 50_000 + "{{placeholder: a}}\n"  # 100000 characters, a region of sixteen assemblies
    assert _seconds(validate, _document(text, _QUESTIONS)) < 5.0  # 0.9 s now


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
    # The alternatives of the document are told apart by pairs (n² / 2); the walk's expansions add none (n x m before).
    # The combined pass declares each expansion's heading again, against the alternatives: n x m, linear in the size
    # of the combined document (it is the one that tells an explicit identifier declared again, ``anchor-duplicate``).
    assert satisfiable.calls <= alternatives**2 + 2 * alternatives * expansions


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
