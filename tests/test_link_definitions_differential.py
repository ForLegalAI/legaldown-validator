"""Link reference definitions against the CommonMark implementations (§4.7, #92).

Generated documents hold definitions where they are met in practice: in
paragraphs, after text (where they are none), in list items, in block quotes,
under setext underlines and as lazy lines. Each is read by this package, by
pandoc (``-f commonmark``, the reference) and by markdown-it-py, and what each
makes of them is compared:

* the definitions, by label: which labels are defined, to which destination
  and title (pandoc is asked by a link to each label, markdown-it by its
  ``references``);
* the blocks, with a definition no block: a paragraph that was only definitions
  is gone, one that had text after them is that text.

Hand-written expectations are in ``test_link_definitions.py``; this is the
cross-check, over many shapes.
"""
from __future__ import annotations

import html
import json
import random
import re
import shutil
import subprocess
from typing import Any
from urllib.parse import unquote

import pytest

from legaldown import parse, serialize
from legaldown.markdown import normalize_label
from legaldown.parser import quote_blocks
from legaldown.syntax import collect_link_definitions, list_items

markdown_it = pytest.importorskip("markdown_it")
pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")

FRONTMATTER = "---\ntitle: T\n---\n\n"

# ── The documents ─────────────────────────────────────────────────

#: Labels, written the way a document would: with a case, a spacing and a
#: bracket that another definition of the same label need not share.
LABELS = ["foo", "FOO", "Bar Baz", "bar   baz", "ΑΓΩ", "αγω", "x\\[y", "a1", "A1", "Ǆ", "ǆ", "ß", "SS"]
#: What the probes ask for: each label once, in its folded form.
PROBES = sorted({normalize_label(label) for label in LABELS} | {"foo bar", "nothing"})
DESTINATIONS = ["/url", "<a b>", "<>", "a(b)c", "/ü", "<a\\>b>", "http://e.com/x?y=1&amp;z", "\\(a\\)", "a\\\\", "</u>"]
TITLES = ['"t"', "'t'", "(t)", '"it\'s"', "'a\"b'", '"a\\"b"', "(a\\(b)", '"two words"', '"&amp; x"']
TEXT = ["text", "more text", "word", "see [foo]"]
ATX = ["# Head", "## Sub"]


def chunk(rng: random.Random) -> list[str]:
    """The lines of one thing a paragraph may hold: a definition in one of its
    forms, or a line that is none."""
    label = rng.choice(LABELS)
    dest = rng.choice(DESTINATIONS)
    title = rng.choice(TITLES)
    kind = rng.choice([
        "plain", "plain", "plain", "title", "dest-below", "title-below", "title-multi", "label-multi",
        "junk-after-title", "junk-below", "junk-after-dest", "empty-dest", "text", "text", "blank-label",
        "no-colon", "bad-parens", "tab",
    ])
    match kind:
        case "plain":
            return [f"[{label}]: {dest}"]
        case "title":
            return [f"[{label}]: {dest} {title}"]
        case "dest-below":
            return [f"[{label}]:", f"{dest} {title}" if rng.random() < 0.5 else dest]
        case "title-below":
            return [f"[{label}]: {dest}", title]
        case "title-multi":
            return [f'[{label}]: {dest} "t', 'u"']
        case "label-multi":
            return ["[foo", f"bar]: {dest}"]
        case "junk-after-title":
            return [f"[{label}]: {dest} {title} junk"]
        case "junk-below":
            return [f"[{label}]: {dest}", f"{title} junk"]
        case "junk-after-dest":
            return [f"[{label}]: {dest} junk"]
        case "empty-dest":
            return [f"[{label}]:"]
        case "blank-label":
            return [f"[ ]: {dest}"]
        case "no-colon":
            return [f"[{label}] {dest}"]
        case "bad-parens":
            return [f"[{label}]: a)b"]
        case "tab":
            return [f"[{label}]:\t{dest}\t{title}"]
        case _:
            return [rng.choice(TEXT)]


def paragraph(rng: random.Random, *, underline: bool = False) -> list[str]:
    """A paragraph's lines: definitions and text, and maybe a setext underline."""
    lines: list[str] = []
    for _ in range(rng.randint(1, 4)):
        lines += chunk(rng)
    if underline:
        lines.append(rng.choice(["===", "---", "  ===", "-----"]))
    return lines


def block(rng: random.Random, depth: int = 0) -> list[str]:
    """The lines of a block, with the blank line after it when it needs one."""
    kind = rng.choice(["paragraph"] * 4 + ["setext"] * 2 + ["list", "list", "quote", "quote", "heading", "rule"])
    if depth >= 2 and kind in ("list", "quote"):
        kind = "paragraph"
    match kind:
        case "paragraph":
            return paragraph(rng)
        case "setext":
            return paragraph(rng, underline=True)
        case "heading":
            return [rng.choice(ATX)]
        case "rule":
            return ["***"]
        case "list":
            lines = []
            bullet = rng.choice(["-", "*", "1."])
            for _ in range(rng.randint(1, 3)):
                inner = block(rng, depth + 1) if rng.random() < 0.3 else paragraph(rng)
                if rng.random() < 0.15:
                    inner = [*inner[:1], "lazy " + rng.choice(TEXT)] + inner[1:]  # a line that continues, not indented
                    lines += [f"{bullet} {inner[0]}", inner[1], *("  " + each for each in inner[2:])]
                else:
                    lines += [f"{bullet} {inner[0]}", *("  " + each for each in inner[1:])]
                if rng.random() < 0.3:
                    lines.append("")
            return lines
        case _:
            inner = block(rng, depth + 1) if rng.random() < 0.3 else paragraph(rng)
            lines = ["> " + each if each else ">" for each in inner]
            # (A line after an underline under only definitions, which CommonMark reads into a
            # paragraph the underline starts: a known difference, CONFORMANCE.md, #92.)
            if rng.random() < 0.2 and not re.fullmatch(r" *(?:=+|-+) *", inner[-1]):
                lines.append("lazy " + rng.choice(TEXT))
            return lines


def generate(seed: int) -> str:
    """A document body of a few blocks, then a probe for each label."""
    rng = random.Random(seed)
    lines: list[str] = []
    for _ in range(rng.randint(1, 4)):
        lines += block(rng)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def with_probes(body: str) -> str:
    """*body* and, after it, a paragraph for each probe: a link if its label is defined."""
    return body + "\n" + "".join(f"\n[{probe}]\n" for probe in PROBES)


# ── What each reads ───────────────────────────────────────────────

# Cases the references take differently from a document the format means to
# keep: a source that is one of them is left out, with its seed, not read.
_SKIPPED_HERE = re.compile(r"\A\Z")  # none, for now


def words(text: str) -> str:
    """Text as the references and this package both give it: no brackets (a
    link's, or a label's), no backslash, no entity (a reference decodes it),
    one space between words."""
    return " ".join(html.unescape(text).replace("[", "").replace("]", "").replace("\\", "").split())


def pandoc(source: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["pandoc", "-f", "commonmark", "-t", "json"], input=source, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)["blocks"]


def inline_text(inlines: list[dict[str, Any]]) -> str:
    out = []
    for each in inlines:
        match each["t"]:
            case "Str":
                out.append(each["c"])
            case "Space" | "SoftBreak":
                out.append(" ")
            case "LineBreak":
                out.append(" ")
            case "Code":
                out.append(each["c"][1])
            case "RawInline":
                out.append(each["c"][1])
            case "Link" | "Image":
                out.append(inline_text(each["c"][1]))
            case "Emph" | "Strong" | "Span" | "Strikeout":
                out.append(inline_text(each["c"][-1] if each["t"] == "Span" else each["c"]))
            case other:
                out.append(f"<{other}>")
    return "".join(out)


def pandoc_nodes(blocks: list[dict[str, Any]]) -> list[Any]:
    """The blocks pandoc gives, as ``nodes`` gives this package's."""
    out: list[Any] = []
    for each in blocks:
        match each["t"]:
            case "Para" | "Plain":
                out.append(("P", words(inline_text(each["c"]))))
            case "Header":
                out.append(("H", each["c"][0], words(inline_text(each["c"][2]))))
            case "HorizontalRule":
                out.append(("R",))
            case "BulletList":
                out.append(("UL", [pandoc_nodes(item) for item in each["c"]]))
            case "OrderedList":
                out.append(("OL", [pandoc_nodes(item) for item in each["c"][1]]))
            case "BlockQuote":
                out.append(("Q", pandoc_nodes(each["c"])))
            case "CodeBlock":
                out.append(("C",))
            case other:
                out.append((other,))
    return out


def pandoc_definitions(source: str) -> tuple[list[Any], dict[str, tuple[str, str]]]:
    """pandoc's blocks of *source*, and the definitions it applies: a link to each label asked
    for by a probe after it."""
    blocks = pandoc(with_probes(source))
    probes = blocks[-len(PROBES):]
    found: dict[str, tuple[str, str]] = {}
    for probe, each in zip(PROBES, probes, strict=True):
        links = [inline for inline in each["c"] if inline["t"] == "Link"]
        if links:
            found[probe] = (links[0]["c"][2][0], links[0]["c"][2][1])
    return pandoc_nodes(blocks[:-len(PROBES)]), found


def markdown_it_definitions(source: str) -> tuple[list[Any], dict[str, tuple[str, str]]]:
    """markdown-it's tokens of *source*, and its references."""
    from markdown_it import MarkdownIt

    env: dict[str, Any] = {}
    tokens = MarkdownIt("commonmark").parse(source, env)
    found = {
        normalize_label(key): (entry["href"], entry["title"]) for key, entry in env.get("references", {}).items()
    }
    return tokens, found


def nodes(blocks: list[Any]) -> list[Any]:
    """This package's *blocks* as pandoc gives them: a link reference definition is no block."""
    out: list[Any] = []
    for each in blocks:
        match each.kind:
            case "link_definition":
                pass
            case "paragraph":
                out.append(("P", words(each.text)))
            case "heading":
                out.append(("H", each.level, words(each.text)))
            case "rule":
                out.append(("R",))
            case "unordered_list" | "ordered_list":
                out.append(
                    ("UL" if each.kind == "unordered_list" else "OL", [nodes(item.blocks) for item in list_items(each)])
                )
            case "quote":
                out.append(("Q", nodes(quote_blocks(each))))
            case "code":
                out.append(("C",))
            case "html":
                out.append(("RawBlock",))
            case other:
                out.append((other,))
    return out


def document_nodes(source: str) -> list[Any]:
    """The blocks of *source* in document order, as ``nodes`` gives them, a section's
    heading among them."""
    document = parse(FRONTMATTER + source)
    out = nodes(document.preamble)
    for section in document.sections:
        out.append(("H", section.level, words(section.title)))
        out += nodes(section.blocks)
    return out


def ours(source: str) -> dict[str, tuple[str, str]]:
    """The definitions of *source* by label, their destination and title as the references
    give them: escapes and character references decoded."""
    definitions = collect_link_definitions(parse(FRONTMATTER + source))
    return {key: (decoded(each.url), decoded(each.title)) for key, each in definitions.items()}


def decoded(text: str) -> str:
    return html.unescape(re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", text))


def comparable(destination: str) -> str:
    """A destination as the references may give it: markdown-it percent-encodes what is not ASCII."""
    return unquote(destination)


# ── The comparisons ───────────────────────────────────────────────

SEEDS = range(300)


def asked(definitions: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """The definitions of the labels the probes ask for, their destinations as ``comparable`` gives them."""
    return {key: (comparable(url), title) for key, (url, title) in definitions.items() if key in PROBES}


def odd_ones_out(
    mine: dict[str, tuple[str, str]], pandoc_reads: dict[str, tuple[str, str]], markdown_it_reads: dict[str, tuple[str, str]]
) -> list[str]:
    """The labels whose definition (or absence of one) here is neither pandoc's nor markdown-it's.

    pandoc is the reference, but it takes a title or a destination on a lazy continuation line
    (``1. [a]: /u`` and ``  "t"`` under a marker of three columns) out of the definition, which
    markdown-it and cmark's algorithm (the lazy line is paragraph text) keep in; markdown-it
    leaves a line after ``<a b>`` and a setext underline under a bare label different
    from pandoc. The two disagree, and what is asked of this package is not to be alone."""
    mine, left, right = asked(mine), asked(pandoc_reads), asked(markdown_it_reads)
    return [key for key in PROBES if mine.get(key) not in (left.get(key), right.get(key))]


@pytest.mark.parametrize("seed", SEEDS)
def test_the_definitions_are_a_references(seed):
    source = generate(seed)
    _blocks, pandoc_reads = pandoc_definitions(source)
    _tokens, markdown_it_reads = markdown_it_definitions(source)
    assert odd_ones_out(ours(source), pandoc_reads, markdown_it_reads) == [], source


@pytest.mark.parametrize("seed", SEEDS)
def test_the_blocks_are_pandocs(seed):
    source = generate(seed)
    blocks, _reference = pandoc_definitions(source)
    assert document_nodes(source) == blocks, source


@pytest.mark.parametrize("seed", SEEDS)
def test_a_document_with_definitions_reads_back_the_same(seed):
    source = generate(seed)
    document = parse(FRONTMATTER + source)
    written = parse(serialize(document))
    assert (written.preamble, written.sections) == (document.preamble, document.sections), source
