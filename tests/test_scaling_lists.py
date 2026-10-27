"""Large list inputs are read in time linear in their size (#94): deeply nested
lists, long flat lists, one item with a long run of continuation lines, and
lines of items or quotes nested on one line.

What can be counted is counted: how often the scan runs, how many lines it
walks, how often a table of lines is traversed, how much text it holds, how
much text the line tests read. A time budget is for what cannot be, and is
at least ten times what a run takes and less than what the earlier code took.
No test compares two timings.

The tests carry the marker ``scaling``, so that ``-m scaling`` runs them
alone; the default run includes them. The sizes are small by design: each
shape that took seconds, or minutes, is shown here by a count that grows with
it."""
from __future__ import annotations

import time
import tracemalloc
from typing import Any

import pytest

from legaldown import markdown as markdown_module
from legaldown import parse
from legaldown import parser as parser_module
from legaldown.parser import LIST_ITEM_RE, MAX_LIST_DEPTH, RULE_RE, _scan_list, quote_blocks

pytestmark = pytest.mark.scaling


def _nested(depth: int) -> str:
    return "".join("  " * i + "- a\n" for i in range(depth))


def test_nested_lists_are_walked_once_per_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each list reads its lines again, one level of nesting at a time, down to
    ``MAX_LIST_DEPTH`` levels: the work is that many times the input, not the
    number of lines times it."""
    depth = 3 * MAX_LIST_DEPTH
    walked: list[int] = []

    def counting(lines: list[str], index: int, **kwargs: Any) -> Any:
        result = _scan_list(lines, index, **kwargs)
        walked.append(result[1] - index)
        return result

    monkeypatch.setattr(parser_module, "_scan_list", counting)
    parse(_nested(depth))
    assert len(walked) <= MAX_LIST_DEPTH + 1
    assert sum(walked) <= (MAX_LIST_DEPTH + 1) * depth


def test_a_flat_list_is_scanned_once_and_each_line_measured_a_few_times(monkeypatch: pytest.MonkeyPatch) -> None:
    items = 3000
    scans: list[int] = []
    widths = [0]
    measure = parser_module.indent_width

    def counting_scan(lines: list[str], index: int, **kwargs: Any) -> Any:
        scans.append(index)
        return _scan_list(lines, index, **kwargs)

    def counting_width(line: str) -> int:
        widths[0] += 1
        return measure(line)

    monkeypatch.setattr(parser_module, "_scan_list", counting_scan)
    monkeypatch.setattr(parser_module, "indent_width", counting_width)
    document = parse("- a\n" * items)
    assert len(document.preamble[0].items) == items
    assert scans == [0]
    assert widths[0] <= 10 * items


def test_a_lists_lazy_lines_are_handed_to_its_items_in_one_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each item gets the lazy lines that lie in it. Filtering them all for
    every item took as many steps as items times lines; the numbered item's
    wide marker makes the next line, at column four, a lazy one."""
    items = 2000
    traversed = [0]

    class Counted(dict[int, int]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            traversed[0] += len(self)
            return super().__iter__()

        def items(self):  # type: ignore[no-untyped-def]
            traversed[0] += len(self)
            return super().items()

    def counting(lines: list[str], index: int, **kwargs: Any) -> Any:
        own, end, lazy, text = _scan_list(lines, index, **kwargs)
        return own, end, lazy, Counted(text)

    monkeypatch.setattr(parser_module, "_scan_list", counting)
    document = parse("123456789. a\n    - b\n" * items)
    assert len(document.preamble[0].items) == items
    assert 0 < traversed[0] <= 2 * items


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param(["- a"] + ["  " + "b" * 1000] * 1500, id="continuation"),
        pytest.param(["- a"] + ["b" * 1000] * 1500, id="lazy"),
    ],
)
def test_an_item_does_not_collect_its_lines(lines: list[str]) -> None:
    """The scan reads of an item whether it is empty and nothing more, so the
    megabyte and a half of text is never built up in it (a string grown a
    line at a time is copied each time)."""
    text = sum(len(line) for line in lines)
    tracemalloc.start()
    try:
        _scan_list(lines, 0, list_type="-")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < text // 8


def test_deep_indentation_is_read_in_one_pass() -> None:
    """A line indented five million columns is tested for a thematic break and
    an item marker in one pass each: the spaces are not given back one at a
    time to try every alternative again. (Every line of a deeply nested list
    is tested at each level.) Only the matching is timed: about 15 ms for five
    million, against 0.6 s before; the budget is 0.25 s."""
    line = " " * 5_000_000 + "a"
    started = time.perf_counter()
    RULE_RE.match(line)
    LIST_ITEM_RE.match(line)
    assert time.perf_counter() - started < 0.25


def test_a_heading_in_an_item_with_a_long_run_of_spaces() -> None:
    """The closing sequence of a heading in an item is looked for from the end
    of its text, not from each space in a run of tens of thousands of them
    (some ten seconds for this one before, a few milliseconds now). The
    budget is 2 s."""
    started = time.perf_counter()
    document = parse("- # a" + " " * 40_000 + "b\n")
    assert document.preamble[0].items[0].blocks[0].kind == "heading"
    assert time.perf_counter() - started < 2.0


class _CountingPattern:
    """A compiled expression that adds up the length of the text it is asked to
    match."""

    def __init__(self, pattern: Any, total: list[int]) -> None:
        self._pattern = pattern
        self._total = total

    def match(self, string: str, *args: Any) -> Any:
        self._total[0] += len(string)
        return self._pattern.match(string, *args)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pattern, name)


def _read_text(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the text the line tests read: what ``RULE_RE`` is asked to match,
    what ``item_content_column`` measures, and what the tests for a thematic
    break from the end of the line (``_Rule``) read: the line, to find where
    one could begin. (Asking costs a step, but for a count over the end of the
    line where a break could begin, and that is a few characters.)"""
    total = [0]
    measure = markdown_module.item_content_column
    find, ask = parser_module._Rule._find, parser_module._Rule.__call__

    def counting_find(rule: Any) -> None:
        total[0] += len(rule._content)
        find(rule)

    def counting_ask(rule: Any, position: int) -> bool:
        total[0] += 1
        return ask(rule, position)

    def counting(line: str) -> int:
        total[0] += len(line)
        return measure(line)

    monkeypatch.setattr(parser_module, "RULE_RE", _CountingPattern(RULE_RE, total))
    monkeypatch.setattr(parser_module._Rule, "_find", counting_find)
    monkeypatch.setattr(parser_module._Rule, "__call__", counting_ask)
    monkeypatch.setattr(parser_module, "item_content_column", counting)
    monkeypatch.setattr(markdown_module, "item_content_column", counting)
    return total


@pytest.mark.parametrize(
    "prefix",
    [
        pytest.param("", id="list"),
        pytest.param("> ", id="quote"),
        pytest.param("- > ", id="quote-in-item"),
    ],
)
def test_items_nested_on_one_line_are_measured_on_themselves(prefix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``- - - - a``, in a quote too: each marker was measured on the whole
    rest of the line, copied, so that the text read grew with the square of its
    length (190,000 times it, at 1,500 markers; some two minutes for a 16 kB
    line). It is some 400 times the line now, at any length: each level of
    nesting reads the line a few times."""
    source = prefix + "- " * 1500 + "a\n"
    total = _read_text(monkeypatch)
    block = parse(source).preamble[0]
    while block.kind != "quote" and block.items:
        block = block.items[0].blocks[0]
    if block.kind == "quote":
        quote_blocks(block)
    assert total[0] < 1000 * len(source)


@pytest.mark.parametrize("prefix", ["", "> ", "- > "])
def test_each_level_of_nesting_reads_its_own_marker_of_a_line_of_them(prefix: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A line of items nested on it is read at each level of list nesting, down
    to sixty-four, with its first marker gone. What a level finds is told to the
    next, so that the markers are read two or six times each in all (as the
    levels need them), not sixty-four: 648,000 reads for a line of 20,000 before."""
    markers = 2000
    calls = [0]

    def counted(real: Any) -> Any:
        def reading(*args: Any, **kwargs: Any) -> Any:
            calls[0] += 1
            return real(*args, **kwargs)

        return reading

    for module, name in [(markdown_module, "item_marker_at"), (parser_module, "item_marker_at"),
                         (parser_module, "item_marker_end")]:
        monkeypatch.setattr(module, name, counted(getattr(module, name)))
    block = parse(prefix + "- " * markers + "a\n").preamble[0]
    while block.kind != "quote" and block.items:
        block = block.items[0].blocks[0]
    if block.kind == "quote":
        quote_blocks(block)
    assert 0 < calls[0] < 10 * markers
