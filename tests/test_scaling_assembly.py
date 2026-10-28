"""Linear time in the line-start check of assembly (§15.7.3, #106).

The check reads the parser's view of the template once, and of the output only from
the last top-level block on. A sub-list inside one top-level item is a single block,
so a check that read it again for every line took quadratic time: 29 s for 600 lines.
A count is what can be counted (the parses of the template and of the output). A time
budget is for what cannot be, at a size where reading the output from the start took
several times the budget (29 s for the 600 lines, 30 s for the 800 below) and a run takes
about a tenth of it, so that it fails only on a gross regression, never on a slow machine.

What stays quadratic, and is accepted: lines whose answer begins a block (a ``- `` or
``# `` in each blank) inside one quote or one item are each read against everything
before them in it, as that is one top-level block.
"""
from __future__ import annotations

import time

import pytest

from legaldown import assemble
from legaldown import assembly as assembly_module

pytestmark = [
    pytest.mark.scaling,
    pytest.mark.filterwarnings(r"ignore:legaldown\.assemble\(\) is deprecated:DeprecationWarning"),
]

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


def _assembled(lines: list[str], answer: str, *, others: dict[str, str] | None = None) -> tuple[str, int, float]:
    """The assembled body, how many times the parser was asked, and how long it took. Every
    ``p<n>`` is *answer*, but those in *others*; ``c`` is true, for ``{{choose: c, true="", false="T"}}``."""
    questions = "".join(f"  p{n}:\n    type: text\n" for n in range(len(lines))) + "  c:\n    type: boolean\n"
    template = f"---\ntitle: Fixture\ndocument_type: contract\nquestions:\n{questions}{_SIDES}---\n\n" + "\n".join(lines) + "\n"
    answers = {f"p{n}": answer for n in range(len(lines))} | (others or {}) | {"c": True}
    start = time.perf_counter()
    result = assemble(template, answers)
    seconds = time.perf_counter() - start
    assert result.ok, result.diagnostics
    return result.output.split("\n---\n", 1)[1], PARSES.calls, seconds


class _Parses:
    def __init__(self) -> None:
        self.calls = 0  # how often the parser was asked to read lines
        self.lines = 0  # and how many lines it was given in all


PARSES = _Parses()


@pytest.fixture(autouse=True)
def _count_parses(monkeypatch):
    PARSES.calls = PARSES.lines = 0
    parse_body = assembly_module._parse_body

    def counted(lines, *args, **kwargs):
        PARSES.calls += 1
        PARSES.lines += len(lines)
        return parse_body(lines, *args, **kwargs)

    monkeypatch.setattr(assembly_module, "_parse_body", counted)


def test_numbered_sub_items_in_one_item_are_read_without_parsing_the_output() -> None:
    """``1. Terms`` and 600 lines ``   2. {{placeholder}} applies.``: each line begins the
    one item its marker says, and its answer begins nothing."""
    lines = ["1. Terms"] + [f"   {n + 1}. {{{{placeholder: p{n + 1}}}}} applies." for n in range(600)]
    body, parses, seconds = _assembled(lines, "Ann")
    assert body.splitlines()[-1] == "   600. Ann applies."
    assert parses <= 2  # the template, once
    assert seconds < 8


@pytest.mark.parametrize("shape", ["bullets", "numbers", "paragraphs"])
def test_a_long_list_with_answers_that_begin_blocks_is_read_by_item(shape: str) -> None:
    """Every answer is ``- a``: each line is read against its own item, not the whole list."""
    if shape == "bullets":
        lines = [f"- {{{{placeholder: p{n}}}}} applies." for n in range(800)]
    elif shape == "numbers":
        lines = [f"{n + 1}. {{{{placeholder: p{n}}}}} applies." for n in range(800)]
    else:
        lines = [f"{{{{placeholder: p{n}}}}} applies.\n" for n in range(800)]
    body, _parses, seconds = _assembled(lines, "- a")
    assert "\\- a applies." in body
    assert seconds < 12


@pytest.mark.parametrize("shape", ["blanks", "after-text"])
def test_a_long_paragraph_is_read_from_its_first_line_and_the_line_before(shape: str) -> None:
    """1600 lines of one paragraph, none of them blank, each answer one that begins a block
    (``- a``), or text after a paragraph (``2. a``): whether a line begins a block there depends
    on the paragraph being open, not on the lines in it, so each is read after two lines, not
    after all the lines before it (some twenty seconds for the first shape, thirteen for the second)."""
    if shape == "blanks":
        lines = [f"{{{{placeholder: p{n}}}}} applies." for n in range(1600)]
        body, _parses, seconds = _assembled(lines, "- a")
        assert body.splitlines()[-1] == "\\- a applies."
    else:
        lines = ["text"] + [f"{{{{placeholder: p{n + 1}}}}}" for n in range(1600)]
        body, _parses, seconds = _assembled(lines, "2. a")
        assert body.splitlines()[-1] == "2. a"
    assert PARSES.lines < 8 * len(lines)  # two or three lines for each, and the template once
    assert seconds < 8


@pytest.mark.parametrize("shape", ["paragraphs", "one-paragraph"])
def test_paragraphs_in_one_item_are_read_from_the_item_and_the_lines_before(shape: str) -> None:
    """Every answer is ``- a``, in the paragraphs of one item: 600 paragraphs, each after a blank
    line, or one paragraph of 800 lines. The lines around the item and its paragraph say what a line
    begins there; the other paragraphs and lines do not (some eleven seconds for the first shape)."""
    if shape == "paragraphs":
        lines = ["- Terms"]
        while len(lines) < 1200:
            lines += ["", f"  {{{{placeholder: p{len(lines) + 1}}}}} applies."]
    else:
        lines = ["- Terms", ""] + [f"  {{{{placeholder: p{n + 2}}}}} applies." for n in range(800)]
    body, _parses, seconds = _assembled(lines, "- a")
    assert body.splitlines()[-1] == "  \\- a applies."
    assert PARSES.lines < 8 * len(lines)  # a few lines for each, and the template once
    assert seconds < 8


_EMPTIED = '{{choose: c, true="", false="T"}}'


@pytest.mark.parametrize("shape", ["sub-items", "quote", "table"])
def test_one_emptied_line_does_not_make_the_parser_read_every_line_after_it(shape: str) -> None:
    """A choice that empties its whole line, before 800 lines that the parser need not be asked about
    once the block it is in is closed (sub-items numbered 2, a quote, a table, each with an innocuous
    answer): the lines after it are read as the template reads them, not each by the parser (some
    twenty seconds for the sub-items, seven for the quote, three for the table)."""
    if shape == "sub-items":
        lines = [_EMPTIED, "", "1. Terms"] + [f"   {n + 1}. {{{{placeholder: p{n + 3}}}}} applies." for n in range(800)]
    elif shape == "quote":
        lines = [_EMPTIED, ""] + [f"> {{{{placeholder: p{n + 2}}}}} applies." for n in range(800)]
    else:
        lines = [_EMPTIED, "", "| A | B |", "|---|---|"] + [f"| {{{{placeholder: p{n + 4}}}}} | x |" for n in range(800)]
    body, _parses, seconds = _assembled(lines, "Ann")
    assert "Ann" in body.splitlines()[-1]
    assert PARSES.lines < 8 * len(lines)  # the template once, and the block of the emptied line
    assert seconds < 8


def test_a_whole_line_filled_by_an_answer_that_looks_like_markers_is_no_deleted_line() -> None:
    """The answer ``2024.`` (or ``-``, ``>``) fills its line and is only escaped: it holds the paragraph that
    the template has, so the lines after it are read as the template reads them."""
    lines = ["{{placeholder: p0}}", "", "1. Terms"] + [f"   {n + 1}. {{{{placeholder: p{n + 3}}}}} applies." for n in range(800)]
    body, _parses, seconds = _assembled(lines, "Ann", others={"p0": "2024."})
    assert body.startswith("\n2024\\.\n")
    assert PARSES.lines < 8 * len(lines)
    assert seconds < 8


def test_a_paragraph_whose_first_line_has_a_pipe_is_still_read_from_two_lines() -> None:
    """``Rates a | b apply:`` and 800 lines: a pipe in the first line of a paragraph does not make
    each line after it be read with all the lines before."""
    lines = ["Rates a | b apply:"] + [f"{{{{placeholder: p{n + 1}}}}} applies." for n in range(800)]
    body, _parses, seconds = _assembled(lines, "- a")
    assert body.splitlines()[-1] == "\\- a applies."
    assert PARSES.lines < 8 * len(lines)
    assert seconds < 8


def test_a_line_of_thousands_of_quote_markers_is_read_in_one_pass() -> None:
    """``>\\t`` six thousand times and an insertion: each marker is matched where the last ended, not
    searched for from the start of the line (the search from the start took eight seconds, against a few hundredths)."""
    lines = [">\t" * 6000 + "{{placeholder: p0}}"]
    body, _parses, seconds = _assembled(lines, "# h")
    assert body.splitlines()[-1].endswith("\\# h")
    assert seconds < 1
