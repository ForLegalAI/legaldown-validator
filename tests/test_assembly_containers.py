"""The line-start check reads each template line's containers as the parser's layout has them (§15.7.3, #122).

Where the content of a template line begins, and how far in the item it lies is indented, used to be guessed
from the line's text: a ``>`` after the first marker counted as a marker wherever it was, and an item in a block
quote had no content column. Two checks hold the replacement (``_Reading.frame``) to account:

* the old text guess, copied, on lines that no quote touches: where the guess was right, the frame is the same;
* documents of quotes and items, with a placeholder on some lines (and a deleted line next to them), assembled
  and compared with pandoc and markdown-it-py, which read the output line in the containers the template line is
  in (``member``), begin no construct there that the template line does not (``under``), have no backslash that
  can go (``over``), and keep the template's own words and spacing (``content``).

``judge`` and ``documents`` are what a larger run takes too (the sizes here are for a few seconds).
"""
from __future__ import annotations

import random
import re
import warnings
from collections import Counter

import pytest
from helpers_renderers import (
    container_paths,
    markdown_it_lines,
    needs_markdown_it,
    needs_pandoc,
    pandoc_begun,
    pandoc_blocks,
    pandoc_words,
)

from legaldown import assemble
from legaldown import assembly as assembly_module
from legaldown.assembly import _item_column, _parse_body, _Reading, _signatures
from legaldown.markdown import indent_width
from legaldown.parser import _Layout

pytestmark = pytest.mark.filterwarnings(r"ignore:legaldown\.assemble\(\) is deprecated:DeprecationWarning")



@pytest.fixture(autouse=True)
def _frames_follow_the_layout(monkeypatch):
    """No walk along a template line may miss a marker that the layout says it has."""
    missed: list[str] = []
    monkeypatch.setattr(assembly_module, "_frame_missed", missed.append)
    yield
    assert missed == []


# ── The guess, as it was: the oracle for lines without a quote ───

_OLD_QUOTE_RE = re.compile(r"([ \t]*)>[ \t]?")
_OLD_ITEM_MARKER_RE = re.compile(r"[ \t]*(?:[-*+]|[0-9]{1,9}[.)])(?:[ \t]+|$)")


def _old_marker_end(text: str, items: int, limit: int | None, base: int = 0) -> int:
    pos = 0
    while True:
        found = _OLD_QUOTE_RE.match(text, pos)
        if found is not None and pos == 0 and indent_width(found.group(1)) > base + 3:
            found = None
        if found is None and items:
            found = _OLD_ITEM_MARKER_RE.match(text, pos)
            if found:
                items -= 1
        if found is None or (limit is not None and found.end() > limit):
            return pos
        pos = found.end()


def _old_columns(lines: list[str]) -> dict[int, int]:
    """The content column of the innermost list item that each line lies in (no quote is read)."""
    layout = _Layout()
    blocks, sections = _parse_body(lines, layout)
    columns: dict[int, int] = {}

    def walk(spans, model, opened):
        for span, block in zip(spans, model, strict=True):
            for item_span, item in zip(span.items, block.items, strict=True):
                first = item_span.start
                inside = (*opened, first)
                column = _item_column(lines[first], inside.count(first))
                columns.update(dict.fromkeys(range(first, item_span.end), column))
                walk(item_span.blocks, item.blocks, inside)

    walk(layout.preamble, blocks, ())
    for (_heading, section_blocks), (_span, spans) in zip(sections, layout.sections, strict=True):
        walk(spans, section_blocks, ())
    return columns


#: What leads a line: nothing, item markers with the spacing the parser allows (a gap of more than four columns,
#: a tab), and indentation of every depth, so that the lines are in items, partly in them, or in none.
_LEADS = ["", "", "- ", "1. ", "10) ", "  ", "   ", "\t", "-\t", "1.\t", "- - ", "- 1. ", "  - ", "   1. ", "1. - ", "    ",
          "      ", "- - - ", "-      ", "1.      ", "2.   ", "10.  ", "*\t", " ", "     ", "        ", "-    ", "+   "]
_ATOMS = ["text", "a", "- a", "* c", "2. x", "1.\tx", "    code", "\tcode", "===", "lazy", "  lazy", "# h", "<x>", "1.", "-",
          "", "", "", "zzz", "zzz", "zzz"]


def _quote_free_lines(rng: random.Random) -> list[str]:
    return [rng.choice(_LEADS) + rng.choice(_ATOMS) for _ in range(rng.randint(2, 9))]


def test_a_frame_is_what_the_text_guess_was_where_that_was_right():
    """On lines with no quote the frame puts the content where the guess did, and keeps the item's indentation up
    to the same column: for 4000 documents of items in every spacing, indented to every depth."""
    rng = random.Random(122)
    compared = 0
    for _ in range(4000):
        lines = _quote_free_lines(rng)
        begun, _containers, _layout, _ = _signatures(lines)
        columns = _old_columns(lines)
        reading = _Reading(list(lines), [])
        for slot, line in enumerate(lines):
            frame = reading.frame(slot)
            old = _old_marker_end(line, begun[slot]["item"], None, columns.get(slot, 0))
            assert frame.prefix == old, (lines, slot)
            if old or begun[slot]["item"]:
                continue
            blanks, base = indent_width(line), columns.get(slot, 0)
            assert min(frame.keep, blanks) == min(base, blanks), (lines, slot)
            # What ``_fix`` keeps of the line's indentation: all of the column, if it has it, else none.
            assert (frame.keep if frame.full and blanks >= frame.keep else 0) == (base if blanks >= base else 0), (lines, slot)
            compared += 1
    assert compared > 6000


# ── Documents of quotes and items, against pandoc and markdown-it ──

_HEADS = ["> - a", "- > - a", "10. > - a", "> 10. a", "> > a", "> 1. - a", "- - a", "> > - a", "- > a", "1. > a", ">\t- a",
          "-\t> - a", "> a", "> - a", ">   - a", "- >  - a", "text", "2.   a", "> text",
          "> -    a", "-    a", "- >    - a"]  # (the last three: a marker spaced by four, item content five columns in)
_GAPPED = ["> -    a", "-    a", "- >    - a", "> 1.   a", "> -     a"]
_INDENTS = [">   ", ">  ", "   ", "  >   ", ">     ", ">    ", "     ", "  >     ", ">      ", ">"]
_STEPS = ["", "", ">", "> ", ">  ", "> > ", ">   ", ">     ", ">      ", "    ", "  ", "   ", "      ", "\t", ">\t", "  >", "    >",
          "    > ", "    >   ", "  >   > ", "  >   ", ">   > ", ">      > ", ">      >   ", "> - ", "- ", "1. ", ">       ", "  >\t",
          "     >   ", ">    > "]
_TEXT = ["text", "lazy", "more", "- b", "", "", ">", "> q", "2. c"]
_DELETED = '{{choose: c, true="", false="Then:"}}'
_BLANKS = ["", ">", "  >", "> >", ">"]
ANSWERS = ["y", "Ann", "# h", "> q", "- a", "2. b", "===", "---", "1) z", "-", "> > a", "~~~"]
QUESTIONS = "  x:\n    type: text\n  y:\n    type: text\n  z:\n    type: text\n  c:\n    type: boolean\n"
_SIDES = """sides:
  - name: providers
    parties:
      - name: acme
        type: legal_entity
        legal_name: Acme Corporation
  - name: clients
    parties:
      - name: beta
        type: legal_entity
        legal_name: Beta Industries Inc.
"""
_PLACEHOLDER_RE = re.compile(r"\{\{placeholder: ([xyz])\}\}")


def _template(body: str) -> str:
    return f"---\ntitle: Fixture\ndocument_type: contract\nquestions:\n{QUESTIONS}{_SIDES}---\n\n{body}"


def documents(rng: random.Random, count: int) -> list[tuple[str, dict]]:
    """*count* templates (their bodies) with the answers to give them: a quote or an item, a few lines that continue it
    in every way (lazy, indented, with markers of their own), a placeholder on some, a blank line or a line that a
    ``choose`` deletes between them."""
    out = []
    for _ in range(count):
        if rng.random() < 0.12:  # a marker spaced by four, a choice that empties a line, and a line short of the item
            lines = [rng.choice(_GAPPED), rng.choice(_INDENTS) + _DELETED, rng.choice(_INDENTS) + "{{placeholder: x}}" + rng.choice(["", " more"])]
        elif rng.random() < 0.5:
            lines = [rng.choice(_HEADS), rng.choice(_BLANKS), rng.choice(_STEPS[2:]) + "{{placeholder: x}}" + rng.choice(["", " more"])]
        else:
            lines = [rng.choice(_HEADS)]
            for _ in range(rng.randint(1, 5)):
                lines.append(rng.choice(_STEPS) + rng.choice(_TEXT))
            for number in range(rng.randint(1, 3)):
                text = rng.choice(_STEPS) + f"{{{{placeholder: {'xyz'[number]}}}}}" + rng.choice(["", "", " more", ". more"])
                lines.insert(rng.randint(1, len(lines)), text)
                if rng.random() < 0.4:
                    lines.insert(rng.randint(1, len(lines)), rng.choice(_BLANKS))
            if rng.random() < 0.3:  # a line of nothing but a deleted choice, next to lines that follow it
                lines.insert(rng.randint(1, len(lines)), _DELETED)
            if rng.random() < 0.2:  # a deleted choice behind a quote's marker: bare ``>`` before a lazy or indented line
                lines.insert(rng.randint(1, len(lines)), rng.choice(_STEPS[2:]) + _DELETED)
        answers = {name: rng.choice(ANSWERS) for name in "xyz"} | {"c": rng.random() < 0.6}
        out.append(("\n".join(lines) + "\n", answers))
    return out


def assembled(body: str, answers: dict) -> str | None:
    """The body of what the template assembles to, or None if it does not (or leaves a directive)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = assemble(_template(body), answers)
    if not result.ok:
        return None
    out = result.output.split("\n---\n", 1)[1].removeprefix("\n")
    return None if "{{" in out else out


def _same_content(written: str, expected: str) -> bool:
    """*written* is *expected*, or with a backslash added: the one that a line start takes."""
    return written == expected or any(written == expected[:at] + "\\" + expected[at:] for at in range(len(expected) + 1))


def judge(body: str, answers: dict, out: str) -> tuple[set[str], int]:
    """What is wrong with the *out*put of the template *body*, by the oracles ``member``, ``under``, ``over``,
    ``content`` and ``misalign``, and how many lines the content oracle read (it reads those where pandoc and
    markdown-it agree on the containers, on how many start on the line, and on the words of the line). A line
    is checked if it holds a placeholder or follows a deleted one."""
    template = body.rstrip("\n").split("\n")
    deleted = {at for at, line in enumerate(template) if line.strip() == _DELETED and answers["c"]}
    words = [_DELETED_RE.sub("zzz" if answers["c"] else "Then:", line) for line in template]
    words = [_PLACEHOLDER_RE.sub("zzz", line) for line in words]
    expected = [
        _PLACEHOLDER_RE.sub(lambda found: answers[found.group(1)], line).replace(_DELETED, "Then:") for line in template
    ]
    emptied = min((at for at, line in enumerate(template) if _DELETED in line and answers["c"]), default=len(template))
    # A line that a choice leaves with only markers holds no paragraph, and what follows it is an open question (#120).
    bare = min(
        (at for at, line in enumerate(template) if _DELETED in line and line.strip() != _DELETED and answers["c"]
         and _MARKERS_RE.fullmatch(line.replace(_DELETED, ""))),
        default=len(template),
    )
    written = out.rstrip("\n").split("\n")
    rows = [at for at, line in enumerate(written) if line.strip()]
    kept = [at for at, line in enumerate(words) if line.strip() and at not in deleted]
    if len(rows) != len(kept):
        return {"misalign"}, 0
    plain = "\n".join(words) + "\n"
    text = out.rstrip("\n") + "\n"
    found: set[str] = set()
    read = 0
    pandoc_out, pandoc_plain = pandoc_blocks(text) or [], pandoc_blocks(plain) or []
    paths_out, paths_plain = container_paths(pandoc_out), container_paths(pandoc_plain)
    begun_out, begun_plain = pandoc_begun(pandoc_out), pandoc_begun(pandoc_plain)
    lines_out, lines_plain = markdown_it_lines(text), markdown_it_lines(plain)
    words_out, words_plain = pandoc_words(pandoc_out), pandoc_words(pandoc_plain)
    for a, b in zip(rows, kept, strict=True):
        if "{{" not in template[b] and b - 1 not in deleted:
            continue
        if b > bare:  # only that the line is not made code, which an indentation kept for it could do
            if begun_out.get(a, Counter())["code"] > begun_plain.get(b, Counter())["code"]:
                found.add("under")
            continue
        # (After a line that a choice empties, deleted or left with only markers, the containers may change, and the line start is checked for what it then begins; and a
        # line that holds no block, a bare ``>``, is in no container to compare.)
        before_deletions = b < emptied
        if before_deletions and None not in (paths_out.get(a), paths_plain.get(b)) and paths_out[a] != paths_plain[b]:
            found.add("member")
        if begun_out.get(a, Counter()) - begun_plain.get(b, Counter()):
            found.add("under")
        for backslash in re.finditer(r"\\", written[a]):
            cut = written[:]
            cut[a] = written[a][:backslash.start()] + written[a][backslash.start() + 1:]
            if pandoc_blocks("\n".join(cut) + "\n", sourcepos=False) == pandoc_blocks(text, sourcepos=False):
                found.add("over")
        agreed = paths_plain.get(b) == paths_out.get(a)
        mine, theirs = lines_out.get(a), lines_plain.get(b)
        if (
            agreed and mine and theirs and mine[0] == theirs[0] == paths_plain.get(b) and mine[2:] == theirs[2:]
            and _words(mine[1]) == words_out.get(a) and _words(theirs[1]) == words_plain.get(b)
        ):
            content, was = mine[1].strip(), theirs[1].strip()
            for placeholder in _PLACEHOLDER_RE.finditer(template[b]):
                was = was.replace("zzz", answers[placeholder.group(1)], 1)
            lead, lead_was = _lead(written[a], content), _lead(expected[b], was)
            if lead is None or lead_was is None:
                continue  # (not the line's end: a tab that markdown-it spread)
            read += 1
            if not _same_content(content, was) or not lead_was.expandtabs(4).startswith(lead.expandtabs(4)):
                found.add("content")
    return found, read


_DELETED_RE = re.compile(re.escape(_DELETED))
_MARKERS_RE = re.compile(r"[ \t>*+\-0-9.)]*")


def _words(content: str) -> str:
    """The words of a line of markdown-it's content: single-spaced, a backslash before punctuation gone."""
    return " ".join(_ESCAPE_RE.sub(r"\1", content).split())


_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")


def _lead(line: str, content: str) -> str | None:
    """What the *line* has before its *content*, or None where it is not the line's end."""
    line = line.rstrip()
    return line[:len(line) - len(content)] if line.endswith(content) else None


@needs_pandoc
@needs_markdown_it
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_assembled_lines_stay_in_the_containers_the_template_has_them_in(seed):
    """Each checked line is read by pandoc in the containers the template line is in, begins nothing it does not,
    has no backslash that can go, and (where markdown-it agrees on the containers) is the template's own text."""
    rng = random.Random(seed)
    found: Counter[str] = Counter()
    bad = []
    read = assembled_count = 0
    for body, answers in documents(rng, 150):
        out = assembled(body, answers)
        if out is None:
            continue
        assembled_count += 1
        kinds, lines = judge(body, answers, out)
        read += lines
        found.update(kinds - {"misalign"})
        if kinds - {"misalign"}:
            bad.append((body, answers, out, sorted(kinds)))
    assert assembled_count > 80
    assert read > 60  # the content oracle reads lines
    assert not found, bad[:3]
