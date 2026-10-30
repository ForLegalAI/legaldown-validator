"""The insertion boundary of link reference definitions (§15.7.3, #129): the oracles that the check is judged by.

The check assembles each region of the body that holds an insertion and a ``]:`` with a finite set of witness
answers, and reports an insertion when changing its answer alone changes the definitions the parser reads
(``validator/hazards.py``). It is right if three things hold, and each is an oracle that cannot be argued
away:

1. *The witnesses are enough.* For templates from the generators below, the questions that random answers
   (wide alphabet, 1 to 7 characters) change the definitions by are among those that the product of the witness
   answers finds, for the model's parser and for markdown-it. A counterexample is a hole in the witness set.
2. *A region is enough.* The verdicts of the validator, region by region, are those of the product over the
   whole document, for every template below the caps; and no region is larger than the paragraph and what
   assembly can join to it.
3. *The assembler agrees.* Templates are assembled by the public API with adversarial answers (the rule off),
   and every insertion of which the answers change the definitions read from the output is reported.

The sizes here are small; ``scratchpad`` runs of the same functions with larger counts are what the numbers of
the commit message come from.
"""
from __future__ import annotations

import datetime
import itertools
import random
import re
from collections import Counter
from functools import partial

import pytest
from helpers_renderers import markdown_it, needs_markdown_it

from legaldown import parse
from legaldown.assembly import _Answers, _decide, _emit, _finish, _read
from legaldown.definitions import nested_blocks
from legaldown.directives import lex
from legaldown.positions import Locator
from legaldown.template import parse_template
from legaldown.validator import hazards, templates, validate
from legaldown.validator.hazards import TEXT_WITNESSES

# ── Generators ────────────────────────────────────────────────────

_HEAD = (
    "---\ntitle: T\nquestions:\n"
    + "".join(f"  p{n}:\n    type: text\n    prompt: P?\n" for n in (1, 2, 3, 4, 5))
    + "  d1:\n    type: date\n    prompt: D?\n  m1:\n    type: money\n    prompt: M?\n  u1:\n    type: duration\n"
    "    prompt: U?\n"
    + "".join(f"  c{n}:\n    type: boolean\n    prompt: C?\n" for n in (1, 2, 3, 4, 5, 6, 7, 8))
    + "  k:\n    type: boolean\n    prompt: K?\n"
    + "  q:\n    type: boolean\n    prompt: Q?\n  r:\n    type: boolean\n    prompt: R?\n"
    + "  t:\n    type: boolean\n    prompt: T?\n  v:\n    type: boolean\n    prompt: V?\n"
    + "  s:\n    type: choice\n    prompt: S?\n    choices:\n      one: One\n      two: Two\n---\n\n"
)
#: The insertions a template may hold, by the question they ask: the directive, and its kind.
_INSERTIONS = {
    "p1": ("{{placeholder: p1}}", "text"), "p2": ("{{placeholder: p2}}", "text"), "p3": ("{{placeholder: p3}}", "text"),
    "d1": ("{{placeholder: d1}}", "date"), "m1": ("{{placeholder: m1, currency=EUR}}", "money"),
    "u1": ("{{placeholder: u1, unit=MO}}", "duration"),
    "c1": ('{{choose: c1, true="", false="z"}}', "choice"), "c2": ('{{choose: c2, true="\\"", false="(t)"}}', "choice"),
    "c3": ('{{choose: c3, true="\'t\'", false=")"}}', "choice"),
    "c8": ('{{choose: c8, true=":", false=" is"}}', "choice"),  # a phrase that supplies the colon of a label
}
#: Text that a definition's paragraph may hold (the kinds of lines of the issue and of real templates).
_FIXED = [
    "[a]: u", "[a]:", '"t"', '"t', 't"', "(t)", "x", " ", "[b]: v", "(as amended)", "2. ", "[Option A]: The Seller shall pay",
    "<u v>", "'t'", '[a]: u "t"', "u", "y", "[a", "]: u", "[a\\]b]: u", "[gdpr]: https://x", "Pay ", "[a]: u", "[a]:",
    "[gdpr]: https://x", "[a]: ", '"', "(", "{when=q}", "{#i when=q}", "[a]", "]", ": u",
    "[Annex]", "https://x/annex((v", "u x))", "z(y(",  # parentheses that nest in a destination, and a label to end
    "u((", "))", "u(", ' "t', 'more"', '{{choose: c8, true=":", false=" is"}}',  # open parentheses; a title over a choice
]
#: Blocks that hold blank lines of their own (fences, indented code, raw HTML, comments): no blank line in them
#: separates anything.
_RAW = [["```", "x", "", "```"], ["<!--", "", "-->"], ["    code", "", "    more"], ["~~~", "", "", "~~~"],
        ["<div>", "</div>"], ["<!-- n -->"]]
_NOTE = ["> [!DRAFTING]", "> n"]
_BARE = '{{choose: c1, true="", false="z"}}'


def _content(rng, pool, heading=False):
    """Some tokens, the insertions among them taken from *pool* (a heading holds no ``{{choose:}}``, §4.2)."""
    parts = []
    for _ in range(rng.randint(1, 3)):
        usable = [each for each in pool if not (heading and "choose" in each)]
        if usable and rng.random() < 0.45:
            pool.remove(usable[0])
            parts.append(usable[0])
        else:
            parts.append(rng.choice(_FIXED))
    return "".join(parts).strip(" ") or "x"


def _hold(rng, max_text):
    """The insertions a template holds: 1 to 4 of the kinds above, at most *max_text* of them text (a text
    question is 16 witnesses: the whole-document product is the size of their product)."""
    held = rng.sample(sorted(_INSERTIONS), rng.randint(1, 4))
    texts = [key for key in held if _INSERTIONS[key][1] == "text"]
    for key in texts[max_text:]:
        held.remove(key)
    return held


def _template(lines, rng):
    """The template of body *lines*: under a heading that is first, or after some of them (they are the preamble)."""
    at = 0 if rng.random() < 0.7 else rng.randint(0, len(lines))
    return _HEAD + "\n".join([*lines[:at], "", "# A", "", *lines[at:]]) + "\n"


def body_of(source):
    """The body of a template made by ``_template``."""
    return source.split("---\n\n", 1)[1]


def gen_real(rng, max_text=2):
    """Sections, paragraphs, setext headings, definitions, list items (a second paragraph, a nested item), block
    quotes, drafting notes and condition markers, with the insertions of a few kinds."""
    held = _hold(rng, max_text)
    pool = [_INSERTIONS[key][0] for key in held]
    rng.shuffle(pool)
    out, section = [], 0
    for _ in range(rng.randint(1, 5)):
        kind = rng.choice(["para", "para", "def", "item", "item", "quote", "note", "setext", "heading", "raw"])
        if kind == "heading":
            section += 1
            out += ["", f"## B{section}", ""]
            continue
        if kind == "raw":
            lines = list(rng.choice(_RAW))
        elif kind == "note":
            lines = [*_NOTE] + ([">"] if rng.random() < 0.6 else [])
        elif kind == "def":
            lines = [rng.choice(["[a]: u", "[a]:", '[a]: u "t"', "[b]: <u v>", "[gdpr]: https://x", "[gdpr]: https://x (GDPR)"])]
        elif kind == "item":
            prefix = rng.choice(["- ", "1. "])
            pad = " " * len(prefix)
            first = rng.choice(["[a]: u", "[gdpr]: https://x"]) if rng.random() < 0.4 else _content(rng, pool)
            lines = [prefix + first]
            lines += [pad + _content(rng, pool) for _ in range(rng.randint(0, 2))]
            if rng.random() < 0.3:
                lines += ["", pad + _content(rng, pool)]
            if rng.random() < 0.2:
                lines += [pad + "- " + _content(rng, pool)]
        elif kind == "quote":
            lines = ["> " + _content(rng, pool) for _ in range(rng.randint(1, 2))]
        else:
            lines = [_content(rng, pool, kind == "setext") for _ in range(rng.randint(1, 3))]
            if kind == "setext":
                lines.append(rng.choice(["===", "---"]))
        if rng.random() < 0.15 and kind not in ("note", "raw"):
            lines[-1 if kind != "setext" else -2] += " {when=q}"
        out += lines
        if rng.random() < 0.7:
            out.append("")
    out += ["", *pool] if pool else []
    return _template(out, rng)


def gen_fz2(rng, max_text=2):
    """The shapes of the issue: a definition line (maybe in an item or a quote), then lines of tokens, notes, a
    heading, a setext underline and a marker."""
    head = ["{when=q}", "{#i when=q}", "<u v>", "<u", "\t", '\\"', "\xa0", "2. ", "3) ", "(", ")", "'", "[a]: u", "[a]:",
            '"t"', '"t', 't"', "(t)", "x", " ", "[b]: v", "{{placeholder: p1}}", _BARE, "{{placeholder: d1}}",
            '{{choose: c2, true="\\"", false="(t)"}}', "u", "y", "[a]", ": u", "u((", "))", ' "t', 'more"']
    lines = [rng.choice(["[a]: u", "[a]:", "- [a]: u", "> [a]: u", '[a]: u "t"', "x", "[{{placeholder: d1}}]:",
                         "[a]: {{placeholder: d1}}"])]
    for _ in range(rng.randint(1, 3)):
        kind = rng.random()
        if kind < 0.15:
            lines.append("")
        elif kind < 0.3:
            lines += _NOTE + ([">"] if rng.random() < 0.4 else [])
        elif kind < 0.4:
            lines += ["", "# B", ""]
        elif kind < 0.5:
            lines += list(rng.choice(_RAW))
        indent = "  " if lines[0].startswith("- ") and rng.random() < 0.5 else (
            "> " if lines[0].startswith("> ") and rng.random() < 0.5 else "")
        body = [indent + ("".join(rng.choice(head) for _ in range(rng.randint(1, 3))) or "x")
                for _ in range(rng.randint(1, 3))]
        if rng.random() < 0.2:
            body.append("===")
        if rng.random() < 0.15:
            body[-1] += " {when=q}"
        lines += body
    return _template(lines, rng)


def gen_nest(rng, max_text=2):
    """Nested, loose and tight lists, bare-marker items (an empty ``{{choose:}}`` leaves a marker alone), notes
    inside items, conditional items, quotes."""
    held = _hold(rng, max_text)
    pool = [_INSERTIONS[key][0] for key in held]
    rng.shuffle(pool)
    out = []
    bares = [_BARE] * 4
    for _ in range(rng.randint(1, 3)):
        depth = rng.randint(1, 3)
        for _ in range(rng.randint(1, 3)):
            level = rng.randint(0, depth - 1)
            prefix = "    " * level + rng.choice(["- ", "1. ", "2. ", "* ", "10. "])
            pad = " " * len(prefix)
            roll = rng.random()
            if roll < 0.12:  # nothing after the marker: the item's text is on the next line
                out.append(prefix.rstrip())
                out.append(pad + _content(rng, pool))
                first = None
            elif roll < 0.3 and bares:  # (each its own question: a question asked twice is two to a region)
                first = bares.pop().replace("c1", f"c{4 + len(bares)}")
            elif roll < 0.5:
                first = rng.choice(["[a]: u", "[a]:", '[a]: u "t"', "[gdpr]: https://x"])
                if rng.random() < 0.3 and out and out[-1].strip():  # a label line directly above, in the item before
                    out.append(rng.choice(["[a]:", '[a]: u "t', "[a]: u"]))
            else:
                first = _content(rng, pool)
            if first is not None:
                out.append(prefix + first + (rng.choice([" {when=q}", " {when=!r}"]) if rng.random() < 0.15 else ""))
            for _ in range(rng.randint(0, 2)):
                if rng.random() < 0.4:
                    out.append("")
                out.append(pad + _content(rng, pool))
            if rng.random() < 0.3:
                out += [pad + _NOTE[0], pad + _NOTE[1]]
            if rng.random() < 0.1:  # a block that is no paragraph ends the item's text, and a line follows it directly
                out += [pad + "```", pad + "code", pad + "", pad + "```", _content(rng, pool)]
            if rng.random() < 0.15:
                out += ["", "> " + _content(rng, pool)]
        if rng.random() < 0.6:
            out.append("")
    out += ["", *pool] if pool else []
    return _template(out, rng)


def gen_cond(rng, max_text=2):
    """Conditional sections (``## S {when=q}``, with and without a blank line before and after the heading, nested
    ones, a section that follows a paragraph directly), choices whose question also drives a condition of their own
    item (the question ``k``, held by that item alone), typed placeholders, and the blocks of the other generators
    inside them."""
    held = _hold(rng, max_text)
    pool = [_INSERTIONS[key][0] for key in held]
    rng.shuffle(pool)
    out = []

    def block():
        kind = rng.choice(["def", "para", "item", "typed", "choice", "note", "setext", "quote", "raw"])
        if kind == "raw":
            return [*rng.choice(_RAW), rng.choice(["[a]: u", "[a]:", "x"]), _content(rng, pool)]
        if kind == "quote":
            return ["> " + _content(rng, pool) for _ in range(rng.randint(1, 2))]
        if kind == "def":
            return [rng.choice(["[a]: u", "[a]:", '[a]: u "t"']), _content(rng, pool)]
        if kind == "item":
            lines = ["- " + _content(rng, pool)]
            lines += ["  " + _content(rng, pool) for _ in range(rng.randint(0, 2))]
            return lines + (["  - " + _content(rng, pool)] if rng.random() < 0.3 else [])
        if kind == "typed":
            return [rng.choice(["[Effective Date]: ", "[Option A]: ", "[b]: v ", "[a]: u "]) + _content(rng, pool)]
        if kind == "choice":  # a choice whose own question drives the condition of its item
            phrases = rng.choice(['true="", false="z"', 'true="\\"", false="u"', 'true="(", false=""'])
            return [rng.choice(["[a]: u", "[a]:", "x"]), f"- {{{{choose: k, {phrases}}}}} {{when=k}}"]
        if kind == "note":
            return [*_NOTE, rng.choice(["[a]: u", "x"])]
        return [_content(rng, pool, True), "==="]

    for number in range(rng.randint(1, 4)):
        if rng.random() < 0.85:
            out.append("")  # a blank line before the heading; none: it follows a line directly
        # (Each section has a question of its own, which no marker in the body has: a section under a condition that
        # a marker inside it contradicts is a region that is never present, which the region does not know.)
        test = ["r", "s:one", "t", "v"][number % 4]
        marker = rng.choice(["", f" {{when={test}}}", f" {{when=!{test}}}"])
        level = rng.choice(["##", "##", "###"])
        out.append(f"{level} S{number}{marker}")
        if rng.random() < 0.8:
            out.append("")
        for _ in range(rng.randint(1, 3)):
            out += block()
            out.append("" if rng.random() < 0.7 else _FIXED[rng.randrange(3)])
    out += ["", *pool] if pool else []
    return _template(out, rng)


GENERATORS = {"real": gen_real, "fz2": gen_fz2, "nest": gen_nest, "cond": gen_cond}


# ── The whole-document reference: the product of the witnesses ────

#: What the whole-document product tries a text question with: every witness there is, and the runs of parentheses
#: that insertions ask for (more than any one of them is asked).
REFERENCE_WITNESSES = [
    *TEXT_WITNESSES, "))", ")))", "((", "(((", *(")" * k + title for k in (1, 2, 3) for title in hazards._TITLES),
]
_WITNESSES = {"boolean": [True, False]}
_DATES = [datetime.date(2024, 1, 1), datetime.date(2025, 12, 31)]
_DEFINITIONS = {}


def _witnesses(t, qid):
    declaration = t.declared[qid]
    kind = declaration["type"]
    if kind == "text":
        return REFERENCE_WITNESSES
    if kind == "choice":
        return list(declaration["choices"])
    return {"boolean": [True, False], "date": _DATES, "money": ["100", "250.50"], "duration": ["12", "3"]}[kind]


def definitions_read(body, parser=None):
    """The definitions of an assembled body: as the model's parser reads them (label, destination, title, in
    order), and as markdown-it does."""
    key = body, parser is None
    if key not in _DEFINITIONS:
        document = parse("\n" + body)
        model = tuple(
            (block.label, block.target, block.title)
            for top in [*document.preamble, *(each for section in document.sections for each in section.blocks)]
            for block in nested_blocks(top)
            if block.kind == "link_definition"
        )
        other = None
        if parser is not None:
            env = {}
            parser.parse(body, env)
            other = frozenset((label, found["href"], found["title"]) for label, found in env.get("references", {}).items())
        _DEFINITIONS[key] = model, other
    return _DEFINITIONS[key]


_EMPTY_TITLE_RE = re.compile(r"(?:\"\"|''|\(\))[ \t]*[^\s]")


def assemble_body(t, answers):
    """The assembled body of template *t* (steps 1 to 8), or None when an answer is missing."""
    resolved = _Answers(answers, t.declared)
    decision = _decide(t, resolved, inline=True)
    if decision.missing:
        return None
    return _finish("", _emit(t.main, decision.removed[""], resolved))


def _asked(t):
    """The questions that the body of template *t* asks, in conditions and outside drafting notes."""
    main = t.main
    asked = {occ.qid for occ in main.occurrences if occ.line not in main.notes} | {
        unit.test.question for unit in main.units if unit.test
    }
    return [q for q in t.declared if q in asked]


def whole_document(source, parser=None):
    """The questions of template *source* of which an answer changes the definitions read, over the whole
    document and the product of the witness answers: ``(model, markdown-it)`` and the number of assemblies. None
    if the template is not readable."""
    t = _read(source, None)
    if t.problems:
        return None
    keys = _asked(t)
    sets = [_witnesses(t, q) for q in keys]
    reads = {}
    for combo in itertools.product(*(range(len(each)) for each in sets)):
        output = assemble_body(t, {q: sets[n][at] for n, (q, at) in enumerate(zip(keys, combo, strict=True))})
        if output is not None:
            reads[combo] = definitions_read(output, parser)
    flagged_model, flagged_other = set(), set()
    for number, key in enumerate(keys):
        seen = {}
        for combo, (model, other) in reads.items():
            rest = combo[:number] + combo[number + 1:]
            before = seen.setdefault(rest, (model, other))
            if before[0] != model:
                flagged_model.add(key)
            if parser is not None and before[1] != other:
                flagged_other.add(key)
    return flagged_model, flagged_other, len(reads)


_ALPHABET = list("x") * 6 + [" ", " ", '"', '"', "'", "(", ")", ">", "<", ":", "\\", "&", "#", "-", "=", ".", "[", "]",
                             "{", "}", "*", "\t", "\xa0", "1", "/", "!", "`", "_", "|", "~", "\x01",
                             "((", "))", "(((", ")))", ": u", ' "t"', "t)", 't"']


def _random_text(rng):
    while True:
        text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(1, 7)))
        if text == text.strip(" \t") and "\n" not in text:
            return text


def _random_answer(rng, declaration):
    kind = declaration["type"]
    return {
        "text": lambda: _random_text(rng),
        "boolean": lambda: rng.random() < 0.5,
        "choice": lambda: rng.choice(list(declaration["choices"])),
        "date": lambda: datetime.date(2020 + rng.randint(0, 9), rng.randint(1, 12), rng.randint(1, 28)),
        "money": lambda: str(rng.randint(1, 99999)),
        "duration": lambda: str(rng.randint(1, 99)),
    }[kind]()


def random_hazards(source, rng, parser=None, bases=12, alternatives=10):
    """The questions of template *source* of which a random answer changes the definitions read: random answers to
    all, then each question varied alone."""
    t = _read(source, None)
    qids = _asked(t)
    flagged_model, flagged_other = set(), set()
    for _ in range(bases):
        base = {q: _random_answer(rng, t.declared[q]) for q in qids}
        output = assemble_body(t, base)
        if output is None:
            continue
        read = definitions_read(output, parser)
        for q in qids:
            for _ in range(alternatives):
                other = assemble_body(t, {**base, q: _random_answer(rng, t.declared[q])})
                if other is None:
                    continue
                again = definitions_read(other, parser)
                if again[0] != read[0]:
                    flagged_model.add(q)
                if parser is not None and again[1] != read[1] and not (
                    _EMPTY_TITLE_RE.search(output) or _EMPTY_TITLE_RE.search(other)
                ):
                    # markdown-it takes an empty title with text after it for no definition; cmark and the model
                    # read the destination alone.
                    flagged_other.add(q)
    return flagged_model, flagged_other


def run_witness_oracle(generator, count, seed, parser=None, **search):
    """Oracle 1 over *count* templates: ``(templates, hazards found by the witnesses, misses)``; a miss is a
    question that random answers change the definitions by and the witness product does not."""
    rng = random.Random(seed)
    seen = found = 0
    misses = []
    for _ in range(count):
        source = generator(rng)
        by_witness = whole_document(source, parser)
        if by_witness is None:
            continue
        seen += 1
        found += len(by_witness[0])
        by_random = random_hazards(source, rng, parser, **search)
        for number, which in enumerate(("model", "markdown-it")):
            if number and parser is None:
                break
            missed = by_random[number] - by_witness[number]
            if missed:
                misses.append((which, sorted(missed), body_of(source)))
    return seen, found, misses


# ── Oracle 1: the witnesses are enough ────────────────────────────


#: How many templates each generator gives the suite: the shapes of ``fz2`` are cheap and rarely hazards.
_COUNT = {"real": 40, "fz2": 120, "nest": 20, "cond": 14}
#: And the templates of the end-to-end oracle, which has fewer hazards.
_ASSEMBLED = {"real": 80, "fz2": 240, "nest": 60, "cond": 30}


def small(generator):
    """*generator* with at most one text question in a template: the product over the whole document is then small
    enough for the suite (the runs of the commit message have two)."""
    return partial(generator, max_text=1)


@pytest.mark.parametrize("name", GENERATORS)
def test_random_answers_change_no_definition_that_the_witnesses_do_not(name):
    seen, found, misses = run_witness_oracle(small(GENERATORS[name]), _COUNT[name], seed=1291, bases=6, alternatives=6)
    assert not misses, misses[:2]
    assert seen > 10 and found > 0, (seen, found)


@needs_markdown_it
@pytest.mark.parametrize("name", ["real", "cond"])
def test_random_answers_change_no_definition_markdown_it_reads_that_the_witnesses_do_not(name):
    parser = markdown_it.MarkdownIt("commonmark")
    seen, found, misses = run_witness_oracle(small(GENERATORS[name]), 6, seed=1292, parser=parser, bases=6, alternatives=6)
    assert not misses, misses[:2]
    assert seen > 5


# ── Oracle 2: a region is enough ──────────────────────────────────


def _region_sizes(document, lines):
    """The number of lines of each region of the body."""
    text = "\n".join(lines) + "\n"
    work = hazards._Assembler(document, text, True)
    by_line = work.by_line
    return [end - start for start, end, _context in work.regions(hazards._items(work.layout), by_line)]


def region_verdicts(source):
    """The questions the validator's regions report in template *source*, whether any region fell back to the
    conservative rule, and the size of its largest region."""
    document = parse(source)
    lines, _ = Locator(document).body()
    found = hazards.definition_hazards(document, lines)
    questions = {lex(each.source).directives[0].positional for each in found}
    return questions, any(each.fallback for each in found), max(_region_sizes(document, lines), default=0)


def run_locality_oracle(generator, count, seed):
    """Oracle 2 over *count* templates: ``(templates, hazards, mismatches, fallbacks, largest region)``. A
    template is skipped if its whole-document product is too large to run."""
    rng = random.Random(seed)
    seen = found = 0
    mismatches, fallbacks, largest = [], 0, 0
    for _ in range(count):
        source = generator(rng)
        by_witness = whole_document(source)
        if by_witness is None:
            continue
        questions, fell_back, size = region_verdicts(source)
        largest = max(largest, size)
        fallbacks += fell_back
        seen += 1
        found += len(by_witness[0])
        # A question of a condition is the region's too: only the questions that an insertion asks are compared.
        main = _read(source, None).main
        counts = Counter(each.directive.positional for each in main.occurrences if each.line not in main.notes)
        whole = by_witness[0] & set(counts)
        # The region must see all the document sees; and no more than it, but in a template that asks a question in two
        # places, whose places are two questions to a region and one to the document (more combinations, never fewer).
        repeated = any(each > 1 for each in counts.values())
        if not fell_back and (whole - questions or (not repeated and questions - whole)):
            mismatches.append((sorted(questions), sorted(whole), body_of(source)))
    return seen, found, mismatches, fallbacks, largest


@pytest.mark.parametrize("name", GENERATORS)
def test_the_verdicts_of_the_regions_are_those_of_the_whole_document(name):
    seen, found, mismatches, fallbacks, largest = run_locality_oracle(small(GENERATORS[name]), _COUNT[name], seed=1301)
    assert not mismatches, mismatches[:2]
    assert seen > 10 and found > 0, (seen, found)
    assert fallbacks <= max(1, seen // 5)  # three text questions in a paragraph (``fz2`` asks one thrice) are over the cap
    assert largest <= 30, largest  # a region is a paragraph and what assembly can join to it, not a section


# ── Oracle 3: the assembler, by the public API ────────────────────

_TEXT_ANSWERS = [
    "x", '"', '"t"', "u", "(t)", "x y", "'", "(", 'z"', "z)", "x)", "[a]: u", "> '", ") (", "> (", "))", "((", ": u",
    ': u "t"', "t)", 't"', ")))",
]


def _answers(rng, held):
    """Answers to the questions of the insertions *held*: a random one each."""
    answers = {"q": True, "r": True, "t": True, "v": True, "k": True, "s": "one"}
    for key in held:
        answers[key] = {
            "text": lambda: rng.choice(["x", "u", "x y", "(t)"]), "choice": lambda: rng.random() < 0.5,
            "date": lambda: datetime.date(2024, 1, 1), "money": lambda: "100", "duration": lambda: "12",
        }[_INSERTIONS[key][1]]()
    return answers


def assembled_hazards(generator, count, seed):
    """Of *count* random templates, assemble each through ``Template.form(...).assemble()`` (the boundary rule off,
    which would stop assembly) with adversarial answers to each insertion in turn and random answers to the others,
    and read the definitions of the output. Returns ``(assembled, hazards, missed)``: the insertions of which the
    answers change what the model's parser or markdown-it reads, and those the validator does not report. (The
    reading of markdown-it is judged where the model's is: apart from the two things in the loop.)"""
    parser = markdown_it.MarkdownIt("commonmark")
    rng = random.Random(seed)
    assembled = found = 0
    missed = []
    for _ in range(count):
        source = generator(rng)
        held = [key for key, (directive, _kind) in _INSERTIONS.items() if directive in source]
        body = body_of(source)
        reported = {
            diagnostic.message[1:diagnostic.message.index("' is not kept apart")]
            for diagnostic in validate(parse(source)).diagnostics
            if diagnostic.rule == "insertion-boundary"
        }
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(hazards, "check_definition_hazards", lambda *_args, **_kwargs: None)
            patch.setattr(templates, "_check_insertion", lambda *_args, **_kwargs: None)
            template = parse_template(source)
            for key in held:
                directive, kind = _INSERTIONS[key]
                if kind not in ("text", "choice"):
                    continue
                values = _TEXT_ANSWERS if kind == "text" else [True, False]
                for _ in range(2):
                    base = _answers(rng, held)
                    reads = []
                    for value in values:
                        result = template.form({**base, key: value}).assemble()
                        if result.output:
                            output = result.output.split("---\n", 2)[2]
                            # markdown-it reads what the parser leaves as text and the check does not judge: an
                            # empty title with text after it, and a definition with a directive in it, which the
                            # model keeps as paragraph text (CONFORMANCE.md).
                            reads.append((*definitions_read(output, parser), bool(_EMPTY_TITLE_RE.search(output)) or "{{" in output))
                    assembled += bool(reads)
                    if reads and (
                        len({each[0] for each in reads}) > 1
                        or len({each[1] for each in reads}) > 1 and not any(each[2] for each in reads)
                    ):
                        found += 1
                        if directive not in reported:
                            missed.append((body, directive, base))
                        break
    return assembled, found, missed


@needs_markdown_it
@pytest.mark.parametrize("name", GENERATORS)
def test_an_answer_that_changes_the_definitions_of_an_assembled_template_is_reported(name):
    """Where the answers to an insertion change the definitions the model's parser or markdown-it reads in the
    assembled document, whatever the others are answered, the validator reports the insertion. The templates are
    those of the generators: sections (conditional ones), lists, quotes, drafting notes between blocks, titles,
    labels that end the line, ``2. …`` lines, bare-marker items, typed placeholders and condition markers."""
    assembled, found, missed = assembled_hazards(small(GENERATORS[name]), _ASSEMBLED[name], seed=1294)
    assert not missed, missed[:2]
    assert assembled > 15 and found > 0, (assembled, found)


def test_the_generators_reach_what_they_are_for():
    """The shapes that the oracles are about occur in the generated templates."""
    rng = random.Random(1295)
    seen = Counter()
    for name, generator in GENERATORS.items():
        for _ in range(60):
            source = generator(rng)
            body = body_of(source)
            seen[f"{name}-note"] += "[!DRAFTING]" in body
            seen[f"{name}-marker"] += "{when=" in body
            seen[f"{name}-quote"] += bool(re.search(r"^ *> (?!\[!DRAFTING\]|n$)", body, re.M))
            seen[f"{name}-nested"] += "\n    - " in body or "\n    1. " in body or "\n  - " in body
            seen[f"{name}-section"] += bool(re.search(r"^##+ S\d+ \{when=", body, re.M))
            seen[f"{name}-bare"] += bool(re.search(r"^ *(?:[-*]|[0-9]+\.) \{\{choose: c\d, true=\"\", false=\"z\"\}\}", body, re.M))
            seen[f"{name}-raw"] += bool(re.search(r"^(?:```|~~~|<!--|    code)$|^<!--$", body, re.M))
            seen[f"{name}-empty-first"] += bool(re.search(r"^ *(?:[-*]|[0-9]+\.)$", body, re.M))
            seen[f"{name}-after-label"] += bool(re.search(r"\]\{\{", body))
    assert seen["real-note"] and seen["real-marker"] and seen["real-quote"] and seen["real-nested"], seen
    assert seen["nest-bare"] and seen["nest-nested"] and seen["nest-note"] and seen["nest-marker"], seen
    assert seen["cond-section"] > 20 and seen["cond-note"] and seen["cond-nested"] and seen["cond-quote"], seen
    assert seen["fz2-quote"] and seen["fz2-note"] and seen["fz2-marker"], seen
    assert seen["real-after-label"] + seen["nest-after-label"] + seen["cond-after-label"] > 5, seen
    assert seen["real-raw"] and seen["fz2-raw"] and seen["cond-raw"] and seen["nest-empty-first"], seen


# ── Pins ──────────────────────────────────────────────────────────

_PIN_HEAD = _HEAD + "# A\n\n"
_FIRST = _PIN_HEAD.count("\n") + 1
_TRUE_FALSE = ", ".join(f'{{{{choose: c{n}, true="", false="z"}}}}' for n in (1, 2, 3))


def reported(body, **options):
    """The lines (counted from the body's first) and messages of the insertion-boundary findings in *body*."""
    result = validate(parse(_PIN_HEAD + body), **options)
    return [(d.line - _FIRST, d.message) for d in result.diagnostics if d.rule == "insertion-boundary"]


def test_the_caps_are_pinned():
    assert (hazards.CAP, hazards.WORK_CAP, hazards.BUDGET) == (4096, 24576, 240000)
    assert len(TEXT_WITNESSES) == 31


def test_three_text_insertions_in_a_paragraph_with_a_label_are_reported_by_the_conservative_rule():
    """31 × 31 × 31 combinations are over the cap: nothing is tried, every insertion in the paragraph is reported, and
    the author is told what to write."""
    found = reported("[a]: u {{placeholder: p1}} {{placeholder: p2}} {{placeholder: p3}}\n")
    assert [line for line, _message in found] == [0, 0, 0]
    assert all("too many answers" in message for _line, message in found), found
    assert all("write '\\[' if the bracket is text, or put a blank line after the definition" in m for _l, m in found)
    # Two are tried, and an insertion no answer of which changes a definition is not reported.
    assert reported("[Option A]: pay {{placeholder: p1}} or {{placeholder: p2}}.\n") == []
    found = reported("[a]: u {{placeholder: p1}} {{placeholder: p2}}\n")
    assert [line for line, _message in found] == [0, 0]
    assert not any("too many answers" in message for _line, message in found)


def test_five_text_insertions_in_a_paragraph_with_a_label_are_over_the_cap():
    body = "[a]: u " + " ".join(f"{{{{placeholder: p{n}}}}}" for n in range(1, 6)) + "\n"
    found = reported(body)
    assert len(found) == 5 and all("too many answers" in message for _line, message in found), found


@pytest.mark.parametrize(
    "body",
    [
        # Two text blanks, alone and with a condition or a choice: judged, and clean.
        "[Option A]: The Seller shall pay {{placeholder: p1}} to {{placeholder: p2}}.\n",
        "- [Option A]: The Seller shall pay {{placeholder: p1}} to {{placeholder: p2}}. {when=q}\n",
        '[Option A]: The Seller shall pay {{placeholder: p1}} to {{placeholder: p2}} {{choose: c1, true="now", false="later"}}.\n',
        "[gdpr]: https://x\n\nThe Seller shall pay {{placeholder: p1}} to {{placeholder: p2}}.\n",
        "[gdpr]: https://x\nThe Seller shall pay {{placeholder: p1}} to {{placeholder: p2}}.\n",
    ],
)
def test_two_text_blanks_alone_and_with_a_condition_or_a_choice_are_judged_not_over_reported(body):
    assert reported(body) == []


@pytest.mark.parametrize(
    "body",
    [
        "[Option A]: The Seller shall pay {{placeholder: p1}} to {{placeholder: p2}} at {{placeholder: p3}}.\n",
        "[gdpr]: https://x\nThe Seller shall pay {{placeholder: p1}} to {{placeholder: p2}} at {{placeholder: p3}}.\n",
        "[Option A]: The Seller shall pay {{placeholder: p1}} to {{placeholder: p1}} and {{placeholder: p2}}.\n",
    ],
)
def test_three_text_blanks_in_a_paragraph_a_definition_begins_are_over_reported_and_told_how_to_stop(body):
    """The accepted over-flag: three answers are more combinations than are tried. The author writes `\\[` if the
    bracket is text, or puts a blank line after the definition."""
    found = reported(body)
    assert len(found) == 3 and all("too many answers" in message and "write '\\['" in message for _l, message in found)


def test_three_text_blanks_in_a_paragraph_no_definition_begins_in_are_not_reported():
    assert reported("The Seller shall pay {{placeholder: p1}} to {{placeholder: p2}} at {{placeholder: p3}}.\n") == []
    assert reported("See [GDPR] ({{placeholder: p1}}), {{placeholder: p2}} and {{placeholder: p3}}.\n") == []


@pytest.mark.parametrize(
    ("body", "answers"),
    [
        # What the review found of the pruned witnesses: each assembles to a definition for an answer or two.
        ('[a]: u({{placeholder: p1}} "t{{placeholder: p1}}\n', {"p1": 't)t"'}),  # one answer, two roles
        ('[a]: u(({{choose: c1, true="", false="z"}}{{placeholder: p1}}\n', {"p1": "))", "c1": True}),
        ('[a]: u{{placeholder: p1}}{{choose: c1, true="", false="z"}}))\n', {"p1": "((", "c1": True}),
        ("[a]: u((x{{placeholder: p1}}\n", {"p1": "))"}),
        ('[a]{{choose: c8, true=":", false=" is"}} u{{placeholder: p1}} more"\n', {"p1": 'x "t', "c8": True}),
        ('[a]{{choose: c8, true=":", false=" is"}}\nu{{placeholder: p1}} more"\n', {"p1": 'x "t', "c8": True}),
        ('[a]: u{{placeholder: p1}} more"\n', {"p1": 'x "t'}),
        ('[a]: u {{placeholder: p1}}\n"x\n', {"p1": '"t"'}),
        ("[a]: https://x/{{placeholder: p1}}\nSee {{placeholder: p1}} now.\n", {"p1": 'x "'}),
    ],
)
def test_what_the_pruned_witnesses_missed_is_reported(body, answers):
    found = reported(body)
    assert found and not any("too many" in message for _line, message in found), found
    template = _read(_PIN_HEAD + body, None)
    base = {"p1": "x", "c1": True, "c8": True}
    assert definitions_read(assemble_body(template, {**base, **answers}))[0] != definitions_read(
        assemble_body(template, base)
    )[0], body


@pytest.mark.parametrize(
    "body",
    [
        # A phrase of a choice cannot supply a bracket: assembly escapes it.
        '{{choose: c1, true="[a]", false="x"}}: u {{placeholder: p1}}\n',
        '[a{{choose: c1, true="]", false="b]"}}: u {{placeholder: p1}}\n',
        # No label begins a line: a clause with many choices is clean.
        'The {{choose: c1, true="Seller", false="Buyer"}} and {{choose: c2, true="Lessor", false="Lessee"}} agree that '
        '{{choose: c3, true="each", false="either"}} party {{choose: c4, true="shall", false="may"}} pay '
        '{{placeholder: p1}} to {{placeholder: p2}} {{choose: c5, true="monthly", false="yearly"}} '
        '{{choose: c6, true="in advance", false="in arrears"}} {{choose: c7, true="by transfer", false="in cash"}}.\n'
        "\nSee the [GDPR] rules.\n\n[GDPR]: https://eur-lex.europa.eu\n",
    ],
)
def test_a_phrase_that_cannot_supply_a_bracket_and_a_clause_without_a_label_are_clean(body):
    assert reported(body) == []


def test_a_region_without_a_label_is_never_reported_by_the_conservative_rule():
    assert reported("Pay {{placeholder: p1}} {{placeholder: p2}} {{placeholder: p3}} {{placeholder: p1}}.\n") == []
    assert reported("[a {{placeholder: p1}} {{placeholder: p2}} {{placeholder: p3}} {{placeholder: p1}}\n") == []


def test_what_a_note_holds_is_no_insertion_of_a_region():
    body = "[a]: u\n> [!DRAFTING]\n> pay {{placeholder: p1}} {{placeholder: p2}} {{placeholder: p3}}\n"
    assert reported(body) == []


def test_the_paragraphs_after_the_budget_are_reported_by_the_conservative_rule(monkeypatch):
    """Paragraphs that differ are tried one after another until the work is spent; the rest are reported."""
    monkeypatch.setattr(hazards, "BUDGET", 7320)  # thirty paragraphs of 244: 31 answers, each of four, and 100 and 20
    count = 80
    body = "".join(f"[Option {n}]: pay {{{{placeholder: p1}}}}.\n\n" for n in range(count))
    found = reported(body)
    tried = found[0][0] // 2
    assert tried == 30  # the first paragraphs are tried, and clean
    assert [line for line, _message in found] == [2 * n for n in range(tried, count)]
    assert all("too many answers" in message for _line, message in found)


def test_ten_option_paragraphs_with_two_blanks_each_are_clean():
    body = "".join(
        f"[Option A]: The Seller shall pay {{{{placeholder: p1}}}} within {{{{placeholder: p2}}}} days of clause {n}.\n\n"
        for n in range(10)
    )
    assert reported(body) == []


@pytest.mark.parametrize(
    "body",
    [
        "The Buyer shall pay the Price [as set out in Schedule 1]: {{placeholder: p1}} to {{placeholder: p2}} at "
        "{{placeholder: p3}}.\n",
        "| Item | Amount |\n|---|---|\n| [Fee]: base | {{placeholder: p1}} |\n| [Fee]: next | {{placeholder: p2}} |\n"
        "| [Fee]: last | {{placeholder: p3}} |\n",
    ],
)
def test_prose_with_a_colon_after_a_bracket_and_a_table_hold_no_definition(body):
    """A text answer cannot make a definition of either, however many there are: nothing is reported, tried or not."""
    assert reported(body) == []


def test_a_paragraph_a_definition_can_begin_in_is_reported_by_the_conservative_rule():
    five = " ".join(f"{{{{placeholder: p{n}}}}}" for n in range(1, 6))
    found = reported(f"[Fee]: base {five}\n\n| a | b |\n|---|---|\n| [x]: y | z |\n")
    assert len(found) == 5 and all("too many answers" in message for _line, message in found)


def test_a_large_table_and_a_large_paragraph_are_judged_by_where_a_definition_can_begin():
    table = "| Item | Amount |\n|---|---|\n" + "".join(f"| [Fee]: base{n} | {{{{placeholder: p1}}}} |\n" for n in range(2000))
    assert reported(table) == []
    prose = "".join(f"Line {n} has {{{{placeholder: p1}}}} in it.\n" for n in range(2000))
    assert reported(prose) == []
    found = reported("[x]: u\n" + prose)  # (a question asked 2000 times: 2000 lines for each answer is over the cap)
    assert len(found) == 2000 and all("too many answers" in message for _line, message in found)


@pytest.mark.parametrize(
    ("body", "line"),
    [
        # A region does not begin at a blank line inside a fence, indented code or raw HTML.
        ("```\nx\n\n```\n[a]: {{placeholder: p1}}\n", 4),
        ("~~~\n\n\n~~~\n[a]: u\n\"{{placeholder: p1}}\"\n", 5),
        ("<!--\n\n-->\n[a]: u\n\"{{placeholder: p1}}\"\n", 4),
        ("- x\n\n  ```\n  c\n\n  ```\n  [a]: u\n  \"{{placeholder: p1}}\"\n", 7),
        # An item whose first line holds its marker alone: the lines around it are read as they are in it.
        ("10.\n    Clause\n\n    [a]: {{placeholder: p1}}\n", 3),
        ("1.\n   x\n\n   1.\n      y\n\n      [a]: u {{placeholder: p1}}\n", 6),
        ("-\n  x\n\n  [a]: u\n  \"{{placeholder: p1}}\"\n", 4),
        # An answer that begins with the colon, after a label that the template ends.
        ('[a]{{choose: c1, true="", false="z"}}{{placeholder: p1}}\n', 0),
        ("[a]{{placeholder: p1}}\n", 0),
        ("[a]{{placeholder: p1}} u\n", 0),
        # One answer that opens a title in one place and closes it in another.
        ("[a]: u\n{{placeholder: p1}} <u\n\"t\"\n{{placeholder: p1}}\n", 3),
        # Parentheses that nest in a destination, and a colon that a choice supplies.
        ("[Annex 1]: https://example.com/annex((v{{placeholder: p1}}\n", 0),
        ("[a]: u{{placeholder: p1}}x))\n", 0),
        ("[a]: u(x(y{{placeholder: p1}} \"t\"\n", 0),
        ('[Annex]{{choose: c1, true=":", false=" is"}} https://x {{placeholder: p1}}\n', 0),
        # A label begun after an insertion that may be empty.
        ('{{choose: c1, true="", false="z"}}[a]:\n{{placeholder: p1}}\n', 0),
    ],
)
def test_a_region_is_read_as_its_block_and_its_context_and_its_label_end(body, line):
    found = reported(body)
    assert line in {each for each, _message in found}, found
    model, _other, _ = whole_document(_PIN_HEAD + body)
    assert model, body


def test_a_dict_built_model_is_not_deduplicated_by_a_line_it_has_not():
    from legaldown import Document

    document = Document.from_dict({
        "metadata": {"title": "T", "questions": {"p": {"type": "text", "prompt": "P?"}}},
        "preamble": [
            {"kind": "paragraph", "text": "Pay &{{placeholder: p}} on [x]: {{placeholder: p}}."},
            {"kind": "paragraph", "text": "[a]: u {{placeholder: p}}"},
            {"kind": "paragraph", "text": "[b]: v {{placeholder: p}}"},
        ],
    })
    found = [d for d in validate(document).diagnostics if d.rule == "insertion-boundary"]
    assert len(found) == 3 and {d.line for d in found} == {None}  # the one the character rule found, and two


def test_a_region_that_is_tried_costs_the_budget_once_for_each_shape():
    """The same paragraph is judged once, wherever it is: a thousand of them are not a budget."""
    found = reported("[Option]: pay {{placeholder: p1}}.\n\n" * 1000 + "[a]: u {{placeholder: p1}}\n")
    assert [line for line, _message in found] == [2000]  # the last one, which is a hazard


@pytest.mark.parametrize("kind", ["include", "attachment"])
def test_a_fragment_on_its_own_is_judged_like_a_document(kind):
    part = '[a]: u\n"{{placeholder: p}}"\n\n[Option]: pay {{placeholder: p}}.\n'
    result = validate(parse(part), fragment=True, fragment_kind=kind)
    assert [d.line for d in result.diagnostics if d.rule == "insertion-boundary"] == [2]


def test_the_check_of_what_a_fragment_may_hold_does_not_assemble(monkeypatch):
    """It throws the findings of the fragment's body away (they are the fragment's own validation's): it does not
    assemble to find them."""
    from legaldown.validator.fragments import FragmentFile, fragment_findings

    calls = []
    real = hazards._Assembler.hazards
    monkeypatch.setattr(hazards._Assembler, "hazards", lambda self, *args: calls.append(1) or real(self, *args))
    document = parse("## S\n\n[a]: u {{placeholder: p}}\n")
    fragment_findings(FragmentFile("part.lgd", document), kind="include", template=True)
    assert calls == []
    validate(document, fragment=True, template=True)
    assert calls == [1]


def test_a_model_built_in_code_is_judged_as_serialized():
    from legaldown import Document

    document = Document.from_dict({
        "metadata": {"title": "T", "questions": {"p": {"type": "text", "prompt": "P?"}}},
        "preamble": [
            {"kind": "unordered_list", "items": [{"blocks": [
                {"kind": "paragraph", "text": "[a]: u {{placeholder: p}}"},
                {"kind": "heading", "level": 1, "text": "Text"},
            ]}]},
        ],
    })
    assert document.source_map is None
    found = [d for d in validate(document).diagnostics if d.rule == "insertion-boundary"]
    # The definition is in a paragraph of its own once written; the heading's text is not read into it.
    assert [d.line for d in found] == [None]


# The issue's shapes, by markdown-it as well as by the model's parser: some answer changes what is read.
_ISSUE = {
    'a title that follows the destination': '[a]: u "t" {{choose: c1, true="", false="z"}}\n',
    "the title's place": '[a]: u {{choose: c1, true="", false="\\""}} "t"\n',
    "an answer that opens a title": '[a]: u {{placeholder: p1}}"\n',
    "a destination on the next line": "[a]:\n{{placeholder: p1}}\n",
    "a title on the next line": '[a]: u\n"{{placeholder: p1}}"\n',
    "a title past a drafting note": '[a]: u\n> [!DRAFTING]\n> n\n>\n"{{placeholder: p1}}"\n',
    "a destination": "[a]: {{placeholder: p1}}\n",
}


@needs_markdown_it
@pytest.mark.parametrize("name", _ISSUE)
def test_the_holes_of_the_issue_change_what_markdown_it_reads_and_are_reported(name):
    source = _PIN_HEAD + _ISSUE[name]
    model, other, _ = whole_document(source, markdown_it.MarkdownIt("commonmark"))
    assert model and other, (model, other)
    assert reported(_ISSUE[name]), name


_CLEAN = [
    "[Option A]: The Seller shall pay {{placeholder: p1}}.\n",
    '[a]: u\n"Buyer" {{def: buyer}} means {{placeholder: p1}}\n',
    "[a]: u\n(a) {{placeholder: p1}}\n",
    "[gdpr]: https://x\n(as amended) {{placeholder: p1}}\n",
    "[Effective Date]: {{placeholder: d1}}\n",
    "[Fee]: {{placeholder: m1, currency=EUR}}\n",
    "1. Clause one\n   > [!DRAFTING]\n   > a note\n2. Clause {{placeholder: p1}} two\n3. [Option]: pay {{placeholder: p2}}.\n",
    "- [Option A]: pay {{placeholder: p1}}.\n  > [!DRAFTING]\n  > n\n- [Option B]: pay {{placeholder: p2}}.\n",
]


@needs_markdown_it
@pytest.mark.parametrize("body", _CLEAN)
def test_the_shapes_of_real_templates_change_no_definition_and_are_not_reported(body):
    model, other, _ = whole_document(_PIN_HEAD + body, markdown_it.MarkdownIt("commonmark"))
    assert not model and not other, (model, other)
    assert reported(body) == []


def test_a_region_is_tried_with_the_conditions_it_holds():
    """An insertion in a conditional item is tried with the item present and removed; the marker is not text."""
    body = "[a]: u\n- [b]: v {{choose: c1, true=\"\", false=\"z\"}} {when=q}\n"
    assert [line for line, _message in reported(body)] == [1]
    assert reported("[a]: u\n\n- pay {{placeholder: p1}} {when=q}\n") == []


@pytest.mark.parametrize(
    "body",
    [
        # What a removed item joins: the line before it with the one after it.
        "[a]:\n- x {when=q}\n  ```\n  c\n  ```\n{{placeholder: p1}}\n",
        "[a]: u \"t\n1. one {when=q}\n2. {{placeholder: p1}}\"\n",
        # A line of nothing but a choice, deleted if its phrase is empty, ends an item if it is not: what is indented
        # after it is the item's or not.
        "1. [gdpr]: https://x\n\n   {{placeholder: p3}}\n   ```\n   code\n   ```\n"
        "{{choose: c1, true=\"\", false=\"z\"}}\n\n    10.\n        [a][a]\n\n        [a]: 't'[a]:\n"
        "        https://x/annex((v[gdpr]: https://x[a\n1. [a]: u\n",
        # A quote line of a choice only is empty if the phrase is, which ends the paragraph in the quote: what follows
        # is read one way or the other.
        "> {{choose: c1, true=\"Note\", false=\"\"}}\nsee below\n2. [b]: https://x\n",
        # A bare marker ends the item, and what it held is read in another structure.
        "1. {{choose: c1, true=\"\", false=\"z\"}}\n\n   x\n2. [a]: u\n   \"{{placeholder: p1}}\"\n",
        "- {{choose: c1, true=\"\", false=\"z\"}}\n\n  [a]: u\n  \"{{placeholder: p1}}\"\n",
        "- {{choose: c1, true=\"\", false=\"z\"}} {when=q}\n\n  [a]: u\n  \"{{placeholder: p1}}\"\n",
    ],
)
def test_what_assembly_joins_is_judged_in_one_region(body):
    """Each of these is a hazard of an insertion that is not in the paragraph of the definition but joins it when
    assembly removes a unit or leaves a marker bare (``regions``); oracle 2 is the evidence that it is all."""
    source = _PIN_HEAD + body
    model, _other, _ = whole_document(source)
    assert model, body
    assert {each for each, _message in reported(body)}, body


# ── Files that include files (§16.11, #117) ───────────────────────

_FILES_HEAD = "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n---\n\n# A\n\n"


def found_in_files(body, files, **options):
    """The insertion-boundary findings of a template with *body* and the fragments *files*, as (file, line)."""
    result = validate(parse(_FILES_HEAD + body), resolve=lambda path: files.get(path), **options)
    return [(d.file, d.line) for d in result.diagnostics if d.rule == "insertion-boundary"]


def test_a_hazard_in_a_fragment_is_reported_once_at_the_fragments_file_and_line():
    files = {"part.lgd": "Text.\n\n[a]: u {{placeholder: p}}\n"}
    assert found_in_files("{{include: part.lgd}}\n", files) == [("part.lgd", 3)]


def test_a_hazard_of_the_host_is_where_it_was_with_a_fragment_included():
    files = {"part.lgd": "Text. {{placeholder: p}}\n"}
    body = "[a]: u {{placeholder: p}}\n\n{{include: part.lgd}}\n"
    assert found_in_files(body, files) == [("", _FIRST_OF_FILES)]
    assert found_in_files("[a]: u {{placeholder: p}}\n", {}) == [("", _FIRST_OF_FILES)]


_FIRST_OF_FILES = _FILES_HEAD.count("\n") + 1


def test_both_the_hosts_and_the_fragments_hazards_are_reported_once_each_and_in_order():
    files = {"part.lgd": "[b]: v {{placeholder: p}}\n"}
    body = "[a]: u {{placeholder: p}}\n\n{{include: part.lgd}}\n\n[c]: w {{placeholder: p}}\n"
    assert found_in_files(body, files) == [
        ("", _FIRST_OF_FILES), ("part.lgd", 1), ("", _FIRST_OF_FILES + 4)
    ]


def test_a_fragment_included_twice_reports_its_hazard_once():
    files = {"part.lgd": "[b]: v {{placeholder: p}}\n"}
    assert found_in_files("{{include: part.lgd}}\n\n{{include: part.lgd}}\n", files) == [("part.lgd", 1)]


def test_a_definition_line_of_the_host_is_not_joined_to_the_fragment_that_follows_it():
    """The include paragraph stays a line of the host, and assembly writes the fragment as a file of its own: a title
    on the fragment's first line is no title of the host's definition, and the fragment's own check is the fragment's."""
    files = {"part.lgd": '"{{placeholder: p}}"\n'}
    assert found_in_files("[a]: u\n{{include: part.lgd}}\n", files) == []


def test_a_document_whose_includes_are_not_all_followed_is_judged_by_its_own_body_alone():
    files = {"part.lgd": "[b]: v {{placeholder: p}}\n"}  # 'gone.lgd' is not there
    body = "[a]: u {{placeholder: p}}\n\n{{include: part.lgd}}\n\n{{include: gone.lgd}}\n"
    found = found_in_files(body, files)
    assert found == [("", _FIRST_OF_FILES)]


def test_a_fragment_on_its_own_is_still_judged_as_a_document():
    result = validate(parse("[b]: v {{placeholder: p}}\n"), fragment=True)
    assert [(d.file, d.line) for d in result.diagnostics if d.rule == "insertion-boundary"] == [("", 1)]


def test_a_region_the_assembler_cannot_read_is_judged_by_the_conservative_rule(monkeypatch):
    from legaldown import assembly

    def refuse(*_args, **_kwargs):
        raise assembly.AssemblyError("The template's source does not match its parsed structure.")

    monkeypatch.setattr(assembly, "_read", refuse)
    found = reported("[a]: u {{placeholder: p1}}\n\n[Option]: pay {{placeholder: p1}}.\n\nPlain {{placeholder: p1}}.\n")
    assert [line for line, _message in found] == [0, 2]  # a region a definition can begin in; not the prose
    assert all("too many answers" in message for _line, message in found)


def test_a_file_the_assembler_cannot_read_is_judged_by_the_conservative_rule_at_its_labels(monkeypatch):
    from legaldown import assembly

    def refuse(*_args, **_kwargs):
        raise assembly.AssemblyError("The template's source does not match its parsed structure.")

    monkeypatch.setattr(assembly, "_read_body", refuse)
    found = reported("[a]: u {{placeholder: p1}}\n\nPlain {{placeholder: p1}}.\n\n- [b]: v\n  {{placeholder: p2}}\n")
    assert [line for line, _message in found] == [0, 5]  # the paragraphs a `[label]:` begins; not the prose
    assert all("too many answers" in message for _line, message in found)
    files = {"part.lgd": "[b]: v {{placeholder: p}}\n"}
    assert found_in_files("[a]: u {{placeholder: p}}\n\n{{include: part.lgd}}\n", files) == [
        ("", _FIRST_OF_FILES), ("part.lgd", 1)
    ]


def test_the_conservative_rule_of_a_file_skips_notes_and_code_and_what_the_character_rules_report(monkeypatch):
    from legaldown import assembly

    def refuse(*_args, **_kwargs):
        raise assembly.AssemblyError("The template's source does not match its parsed structure.")

    monkeypatch.setattr(assembly, "_read_body", refuse)
    body = (
        "[a]: u {{placeholder: p1}}\n\n```\n[b]: v {{placeholder: p1}}\n```\n\n> [!DRAFTING]\n> [c]: w {{placeholder: p1}}\n\n"
        "[d]{{placeholder: p2}}: x\n\n[e]:&{{placeholder: p1}}\n"
    )
    found = reported(body)
    # `[b]` is in code and `[c]` in a note; `[d]{{…}}` (an insertion may be its colon) and `[e]:&{{…}}` are the character
    # rules' (once each); `[a]` is the conservative rule's.
    assert sorted(line for line, _message in found) == [0, 9, 11]
    assert [line for line, message in found if "too many answers" in message] == [0]


_DECLARED_HEAD = (
    "---\ntitle: T\nquestions:\n  p:\n    type: text\n    prompt: P?\n  d:\n    type: date\n    prompt: D?\n---\n\n# A\n\n"
)


def test_a_fragment_is_judged_with_the_questions_of_the_document_that_includes_it():
    """The assembler reads it so: a date the host declares is a typed placeholder in the fragment, whose answer is not text."""
    files = {"part.lgd": "[Effective Date]: {{placeholder: d}}\n"}
    result = validate(parse(_DECLARED_HEAD + "{{include: part.lgd}}\n"), resolve=lambda path: files.get(path))
    assert [d for d in result.diagnostics if d.rule == "insertion-boundary"] == []
    files = {"part.lgd": "[Effective Date]: {{placeholder: p}}\n"}
    result = validate(parse(_DECLARED_HEAD + "{{include: part.lgd}}\n"), resolve=lambda path: files.get(path))
    assert [(d.file, d.line) for d in result.diagnostics if d.rule == "insertion-boundary"] == [("part.lgd", 1)]


def test_the_budget_is_the_validations_not_each_files(monkeypatch):
    """Eight fragments of paragraphs that differ cost what the same paragraphs in the document do: one budget."""
    import time

    monkeypatch.setattr(hazards, "BUDGET", 3000)
    paragraphs = [
        "".join(f"[Option {f}-{n}]: pay {{{{placeholder: p}}}}.\n\n" for n in range(150)) for f in range(8)
    ]
    files = {f"part{f}.lgd": text for f, text in enumerate(paragraphs)}
    body = "".join(f"{{{{include: part{f}.lgd}}}}\n\n" for f in range(8))
    start = time.perf_counter()
    in_files = found_in_files(body, files)
    split = time.perf_counter() - start
    start = time.perf_counter()
    inline = found_in_files("".join(paragraphs), {})
    together = time.perf_counter() - start
    # What was tried (a budget of 3000 is 12 paragraphs of 244) is clean, the rest is reported:
    # as many in the files as in the document.
    assert len(in_files) == len(inline) == 8 * 150 - 3000 // 244  # (a paragraph is 31 answers of four, a region's 100 and a declared question's 20)
    assert split < 10 * together + 2.0 and together < 10 * split + 2.0


def test_an_insertion_the_character_rules_report_is_not_reported_again_whatever_is_known_of_its_line(monkeypatch):
    from legaldown import Document
    from legaldown.positions import Locator

    body = "[a]:&{{placeholder: p}}\n\n[b]: v {{placeholder: p}}\n"
    assert len([d for d in validate(parse(_FILES_HEAD + body)).diagnostics if d.rule == "insertion-boundary"]) == 2
    # A line that cannot be found (a fragment's, say): the same two, one each.
    monkeypatch.setattr(Locator, "find", lambda *_args, **_kwargs: None)
    found = [d for d in validate(parse(_FILES_HEAD + body)).diagnostics if d.rule == "insertion-boundary"]
    assert len(found) == 2
    monkeypatch.undo()
    # A model built in code has no lines at all.
    document = Document.from_dict({
        "metadata": {"title": "T", "questions": {"p": {"type": "text", "prompt": "P?"}}},
        "preamble": [
            {"kind": "paragraph", "text": "[a]:&{{placeholder: p}}"},
            {"kind": "paragraph", "text": "[b]: v {{placeholder: p}}"},
        ],
    })
    found = [d for d in validate(document).diagnostics if d.rule == "insertion-boundary"]
    assert len(found) == 2 and {d.line for d in found} == {None}


def _refuse(*_args, **_kwargs):
    raise RuntimeError("forced")


@pytest.mark.parametrize(
    ("body", "lines"),
    [
        ("[a\nb]: u {{placeholder: p1}}\n", [1]),  # a label over lines
        ("[a\\]b]: u {{placeholder: p1}}\n", [0]),  # an escaped bracket in it
        ('{{choose: c1, true="", false="z"}}[a]: u {{placeholder: p1}}\n', [0, 0]),  # a choice that may be empty before it
        ("[a]{{placeholder: p1}} u\n", [0]),  # an insertion that may be the colon (the character rules' too)
        ('{{choose: s, one="}}", two=""}}[a]: u {{placeholder: p1}}\n', [0, 0]),  # a `}}` in a phrase ends no choice
        # Every insertion of the paragraph a label begins a line of; code and notes end a paragraph.
        ("text {{placeholder: p1}}\n```\ncode\n```\n[a]: u {{placeholder: p2}}\n", [4]),
        ('{{choose: c1, true="", false="z"}}\n[a]: u {{placeholder: p1}}\n', [0, 1]),
        ("text {{placeholder: p2}}\n[a]: u {{placeholder: p1}}\n", [0, 1]),  # all of the paragraph
        ('- {{choose: c1, true="", false="z"}}\n[a]: u\n', [0]),  # a choice in an item above the label
        ('- a\n- {{choose: c1, true="", false="z"}}\n[a]: u\n', [1]),
        ("[a] b]: u {{placeholder: p1}}\n", []),  # no label: a bracket in it
        ("See [a]: u {{placeholder: p1}}\n", []),  # a label that begins no line
    ],
)
def test_the_safety_net_reads_a_label_as_the_main_path_does(monkeypatch, body, lines):
    monkeypatch.setattr(hazards, "definition_hazards", _refuse)
    found = reported(body)
    assert sorted(line for line, _message in found) == lines, found


def test_nothing_is_raised_where_the_safety_net_fails_too(monkeypatch):
    monkeypatch.setattr(hazards, "definition_hazards", _refuse)
    monkeypatch.setattr(hazards, "_conservative", _refuse)
    assert reported("[a]: u {{placeholder: p1}}\n") == []
    files = {"part.lgd": "[b]: v {{placeholder: p}}\n"}
    assert found_in_files("[a]: u {{placeholder: p}}\n\n{{include: part.lgd}}\n", files) == []


def test_the_serialized_reading_is_tried_where_the_safety_net_fails_on_the_one_as_written(monkeypatch):
    real = hazards._conservative
    calls = []

    def once(lines, layout=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("forced")
        return real(lines, layout)

    monkeypatch.setattr(hazards, "definition_hazards", _refuse)
    monkeypatch.setattr(hazards, "_conservative", once)
    result = validate(parse(_PIN_HEAD + "[a]: u {{placeholder: p1}}\n"))
    found = [d for d in result.diagnostics if d.rule == "insertion-boundary"]
    assert len(found) == 1 and found[0].line is None  # the serialized reading has no lines
    assert len(calls) == 2


def test_a_region_in_which_nothing_can_vary_is_clean_and_not_over_reported():
    """No insertion has more than one answer, so none changes anything: the cap is not asked."""
    body = "> [a]: u\n" + '> {{choose: z, a="w"}}\n> x\n' * 3000
    result = validate(parse("---\ntitle: T\n---\n\n" + body), fragment=True)
    assert [d for d in result.diagnostics if d.rule == "insertion-boundary"] == []
