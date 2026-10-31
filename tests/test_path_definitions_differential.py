"""The path rules against pandoc and markdown-it, on documents with link reference definitions (#121).

An image is displayed by the renderer that reads it, and the path rules must judge the destination of every image any
of them displays (``path-not-relative``, ``path-outside-root``): the definitions pandoc (``-f commonmark``) and
markdown-it read are not always the specification's (a control character or DEL in a bare destination, a backslash
before a tab, a space or a line break), nor are the labels they match (a no-break space, U+3000, a canonical
equivalent). The documents are generated with the structures definitions are met in (paragraphs, setext headings,
list items, block quotes, tables, one definition twice) and images that use them; each is read by pandoc and by
markdown-it, with the table extension and without, and two things are asserted of what they display:

* (A) every image a renderer displays has its destination judged;
* (B) every destination judged is displayed by some renderer: nothing is judged that no renderer uses (a later
  definition of a label, a definition that is none, a title).

The generator is asserted to reach each pitfall (``test_the_generator_reaches_every_pitfall``), counted in the
documents where the judged destinations are more than the specification's alone: a quiet generator proves nothing."""
from __future__ import annotations

import json
import random
import re
import subprocess
import unicodedata
from collections import Counter
from typing import NamedTuple
from urllib.parse import unquote

import pytest
from helpers_renderers import markdown_it, needs_markdown_it, needs_pandoc

from legaldown import parse, validate
from legaldown.markdown import Reading, label_key
from legaldown.validator import paths as paths_module

pytestmark = [needs_markdown_it, needs_pandoc]

_FRONT = "---\ntitle: T\n---\n\n# A {#a}\n\n"

# ── The documents ─────────────────────────────────────────────────

#: Labels that one reading matches as one (``label_key``) and another does not: written the way a document may, in
#: groups. Within a group are the variants a reading reads as one label, some of them only some readings.
_LABEL_GROUPS = [
    ["foo", "FOO", "Foo"],
    ["bar baz", "Bar   Baz", "bar\u00a0baz", "bar\u3000baz", "bar\u2005baz", "bar\x0bbaz", "bar\x0cbaz", "bar\u2028baz"],
    ["\u0390", "\u03b9\u0308\u0301", "\u0399\u0308\u0301", "\u03aa\u0301", "\u03ca\u0301"],
    ["\u00e9", "e\u0301", "\u00c9", "E\u0301"],
    ["i", "I", "\u0131", "\u0130"],
    ["\u03a3\u03b1", "\u03c3\u03b1", "\u03c2\u03b1"],
    ["stra\u00dfe", "STRASSE", "strasse"],
    ["a\x1fb", "a b"],
    ["x", "X"],
]
#: What a destination is read as, by the readings, in a definition: ``/etc/p`` and ``../x`` are what the rules judge,
#: ``ok.png`` and ``sub/ok.png`` are not wrong. A control character, DEL or a backslash before a tab, a control
#: character or a line break is read by one reading and not another.
_PLAIN = ["ok.png", "/etc/p", "../x", "sub/ok.png", "/abs/q"]
_ODD = [
    "/etc/p\x7f", "/etc/p\x01", "../x\x1f", "/e\x01\\)", "a\\\tb", "/etc/b\\\tx", "a\\\x01b", "/etc/c\\\x7f",
    "<a\\\nb>", "</etc/d\\\ne>", "/etc/x\\ \"t\"", "x\\ y",
]
#: Destinations of inline images (those of definitions, and a title glued to ``<…>``).
_INLINE = [*_PLAIN, *_ODD, "<x>\"t\"", "<../u>'t'", "</abs>(t)", "x\\ ", "/etc/z\\ \"t\""]
_TITLES = ["", "", "", ' "t"', " 't'", " (t)"]
#: Labels of white space only: blank to the readings whose white space it is (the specification's are a space, a tab and
#: the line breaks), so the line is text to them.
_BLANK_LABELS = ["\u00a0", "\u3000", "\u2005", "\x0b", "\x1c", "\x85", "\u2028", " \u00a0 "]
#: A destination that ends with a backslash, in a definition: markdown-it takes it with the line break as a pair, so
#: a title on the next line is none to it.
_BACKSLASH_END = ["ok.png\\", "/etc/t\\", "/etc/t\\\\"]
_TEXT = ["text", "more text", "word"]


def _is_odd(destination: str) -> bool:
    """Whether the specification reads it otherwise than pandoc or markdown-it: a control character or DEL, or a
    backslash before a tab, a line break or a space in it."""
    return destination in _ODD or "\x7f" in destination or any(c < " " for c in destination if c not in "\t\n")


class _Generator:
    """One document: its lines and what it holds (``tags``), for the labels of the groups it uses."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.prefix = f"d{seed}-"
        self.groups = self.rng.sample(_LABEL_GROUPS, 2)
        self.tags: set[str] = set()
        #: The label groups defined so far by an odd destination and by a plain one, for the tags of one defined twice.
        self.defined: dict[int, str] = {}
        #: Whether what is written is on one line (a table's cell).
        self.flat = False

    def label(self) -> str:
        group = self.rng.choice(self.groups)
        return self.prefix + self.rng.choice(group)

    def destination(self, inline: bool = False) -> str:
        pool = _INLINE if inline else (_PLAIN if self.rng.random() < 0.45 else _ODD)
        destination = self.rng.choice(pool)
        while self.flat and "\n" in destination:
            destination = self.rng.choice(pool)
        if "\\\t" in destination:
            self.tags.add("backslash-tab")
        if "\\\n" in destination:
            self.tags.add("backslash-lf")
        if "\\\x01" in destination or "\\\x7f" in destination:
            self.tags.add("backslash-control")
        return destination

    def image(self) -> str:
        rng = self.rng
        label = self.label()
        match rng.choice(["full", "full", "collapsed", "shortcut", "inline", "inline"]):
            case "full":
                return f"![a][{label}]"
            case "collapsed":
                return f"![{label}][]"
            case "shortcut":
                return f"![{label}]"
            case _:
                return f"![a]({self.destination(inline=True)})"

    def definition(self) -> str:
        """The lines of a definition, or of something that looks like one."""
        rng = self.rng
        label = self.label()
        destination = self.destination()
        group = next(i for i, each in enumerate(self.groups) if label.removeprefix(self.prefix) in each)
        kind = "odd" if _is_odd(destination) else "plain"
        for tag in (f"{kind}-first" if group not in self.defined else f"{kind}-later-{self.defined[group]}",):
            self.tags.add(tag)
        self.defined.setdefault(group, kind)
        for each in ("\u0390", "\u3000"):
            if each in label or (each == "\u0390" and "\u03b9\u0308\u0301" in label):
                self.tags.add("u0390" if each == "\u0390" else "u3000")
        title = rng.choice(_TITLES)
        if rng.random() < 0.15:
            title = rng.choice([' "![i](/etc/t.png)"', f' "![i][{self.label()}]"', " '![i](ok.png)'", ' "![i"\n](/etc/t.png)'])
            self.tags.add("title-image")
        match rng.choice(["plain"] * 6 + ["below", "multi-label", "junk", "empty", "title-below", "blank", "blank", "end", "end"]):
            case "blank":
                self.tags.add("blank-label")
                return f"[{rng.choice(_BLANK_LABELS)}]: {destination}{title}"
            case "end":
                self.tags.add("backslash-end")
                return f"[{label}]: {rng.choice(_BACKSLASH_END)}\n'![i](/etc/z.png)'" if rng.random() < 0.5 else (
                    f"[{label}]: {rng.choice(_BACKSLASH_END)}{title}"
                )
            case "plain":
                return f"[{label}]: {destination}{title}"
            case "below":
                return f"[{label}]:\n{destination}{title}"
            case "multi-label":
                return f"[{label.split()[0] if ' ' in label else label}\n{label.split()[-1]}]: {destination}"
            case "junk":
                return f"[{label}]: {destination} junk"
            case "empty":
                return f"[{label}]:"
            case _:
                return f"[{label}]: {destination}\n't'"

    def paragraph(self, *, setext: bool = False) -> list[str]:
        """The lines of a paragraph: definitions, then text with images, maybe then a setext underline."""
        rng = self.rng
        parts = [self.definition() for _ in range(rng.randint(0, 3))]
        if not setext or rng.random() < 0.5 or not parts:
            self.flat = setext  # (the model joins the lines of a setext heading's text with spaces)
            parts += [f"{rng.choice(_TEXT)} {self.image()} {rng.choice(_TEXT)}" for _ in range(rng.randint(0, 2))]
            self.flat = False
            if not parts:
                self.flat = setext
                parts = [self.image()]
                self.flat = False
        elif setext:
            self.tags.add("setext")
            if any(_is_odd(part) for part in parts):
                self.tags.add("setext-odd")
        if setext and parts[-1].endswith(":"):
            parts.append(rng.choice(_TEXT))  # (markdown-it reads an underline under a lone label as its destination: #92)
        lines = "\n".join(parts).split("\n")
        if setext:
            lines.append(rng.choice(["===", "---", "  ==="]))
        return lines

    def table(self) -> list[str]:
        rng = self.rng
        cell = lambda: rng.choice([  # noqa: E731
            self.image(), "x", f"[{self.label()}]: {self.destination()}", f"{self.image()} {self.image()}",
        ])
        self.tags.add("table")
        self.flat = True
        columns = rng.randint(1, 3)
        lines = ["| " + " | ".join(cell() for _ in range(columns)) + " |", "|" + "---|" * columns]
        for _ in range(rng.randint(0, 2)):
            lines.append("| " + " | ".join(cell() for _ in range(columns)) + " |")
        if rng.random() < 0.4:
            lines.append(f"[{self.label()}]: {self.destination()}")  # a row of one cell, which is no definition
            self.tags.add("table-row-definition")
        if any(re.search(r"\[[^\]]*\]: ", line) for line in lines):
            self.tags.add("table-definition-cell")
        self.flat = False
        return lines

    def block(self, depth: int = 0) -> list[str]:
        rng = self.rng
        kind = rng.choice(["paragraph"] * 4 + ["setext"] * 2 + ["list", "list", "quote", "quote", "table", "heading", "rule"])
        if depth >= 2 and kind in ("list", "quote"):
            kind = "paragraph"
        match kind:
            case "paragraph":
                return self.paragraph()
            case "setext":
                return self.paragraph(setext=True)
            case "heading":
                return [rng.choice(["# ", "## "]) + rng.choice([f"{self.image()}", f"[{self.label()}]: {self.destination()}"])]
            case "rule":
                return ["***"]
            case "table":
                return self.table()
            case "list":
                lines: list[str] = []
                bullet = rng.choice(["-", "*", "1."])
                indent = " " * (len(bullet) + 1)
                for _ in range(rng.randint(1, 3)):
                    inner = self.block(depth + 1) if rng.random() < 0.35 else self.paragraph()
                    lines += [f"{bullet} {inner[0]}", *(indent + each if each else "" for each in inner[1:])]
                    if rng.random() < 0.3:
                        lines.append("")
                if depth:
                    self.tags.add("nested")
                else:
                    self.tags.add("list")
                if any("|---" in line for line in lines):
                    self.tags.add("nested-table")
                return lines
            case _:
                inner = self.block(depth + 1) if rng.random() < 0.35 else self.paragraph()
                self.tags.add("quote")
                if any("|---" in line for line in inner):
                    self.tags.add("nested-table")
                return ["> " + each if each else ">" for each in inner]

    def document(self) -> str:
        lines: list[str] = []
        for _ in range(self.rng.randint(1, 4)):
            lines += [*self.block(), ""]
        # Images after the definitions, wherever they were written: a definition is used from the whole document.
        lines += [f"{self.image()} {self.image()}", ""]
        return "\n".join(lines)


def generate(seed: int) -> tuple[str, set[str]]:
    """A document body and what it holds."""
    made = _Generator(seed)
    return made.document(), made.tags


# ── What each renderer displays ───────────────────────────────────


def _walk_pandoc(node, found):
    if isinstance(node, dict):
        if node.get("t") == "Image":
            found.append(node["c"][2][0])
        for value in node.values():
            _walk_pandoc(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_pandoc(value, found)


def pandoc_sources(documents: list[str]) -> list[list[str] | None]:
    """The destinations of the images of each of *documents*, by one run of pandoc: each follows a paragraph that is
    a marker, and the labels of one are its own (the generator prefixes them). A document that leaves a construct
    open swallows the next marker: it and the one before it are None."""
    source = "".join(f"MARKER{n}\n\n{text}\n\n" for n, text in enumerate(documents))
    out = subprocess.run(
        ["pandoc", "-f", "commonmark", "-t", "json"], input=source, capture_output=True, text=True, check=True
    ).stdout
    found: list[list[str] | None] = [[] for _ in documents]
    current, markers = 0, set()
    for block in json.loads(out)["blocks"]:
        marker = block["c"][0].get("c", "") if block.get("t") == "Para" and len(block["c"]) == 1 else ""
        if isinstance(marker, str) and marker.startswith("MARKER") and marker[6:].isdigit():
            current = int(marker[6:])
            markers.add(current)
        else:
            _walk_pandoc(block, found[current])  # type: ignore[arg-type]
    for n in range(len(documents)):
        if n not in markers:
            for each in (n - 1, n):
                if each >= 0:
                    found[each] = None
    return found


def markdown_it_sources(text: str, *, tables: bool) -> list[str]:
    parser = markdown_it.MarkdownIt("commonmark")
    if tables:
        parser.enable("table")
    out: list[str] = []

    def walk(tokens):
        for token in tokens:
            if token.type == "image":
                out.append(unquote(token.attrGet("src")))
            walk(token.children or [])

    walk(parser.parse(text))
    return out


# ── What the path rules judge ─────────────────────────────────────


def _judger(monkeypatch):
    """A function giving the paths the path rules judge in a document body."""
    judged: list[str] = []
    real = paths_module.FileReferences.check
    monkeypatch.setattr(
        paths_module.FileReferences, "check",
        lambda self, path, *args, **kwargs: judged.append(path.strip()) or real(self, path, *args, **kwargs),
    )

    def judge(text: str) -> list[str]:
        judged.clear()
        validate(parse(_FRONT + text))
        return list(judged)

    return judge


#: What a destination markdown-it normalizes: one that looks like a URL with a scheme.
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]{1,31}:")

DOCUMENTS = 6_000
#: The seed of the first document (a larger run outside the suite moves it).
FIRST_SEED = 0


class Row(NamedTuple):
    """A document, what the path rules judge in it, what the specification's reading alone judges, the destinations
    each renderer displays (pandoc's is None when the document leaves a construct open) and the document's tags."""

    text: str
    tags: set[str]
    judged: list[str]
    specification: list[str]
    pandoc: list[str] | None
    markdown_it: list[str]
    tables: list[str]


def analysed() -> list[Row]:
    """Every document, with the path rules' findings and what the renderers display."""
    with pytest.MonkeyPatch.context() as patch:
        judge = _judger(patch)
        generated = [generate(seed) for seed in range(FIRST_SEED, FIRST_SEED + DOCUMENTS)]
        texts = [text for text, _tags in generated]
        pandoc = pandoc_sources(texts)
        judged = [judge(text) for text in texts]
        with patch.context() as only:
            only.setattr(paths_module, "READINGS", (Reading.SPEC,))
            specification = [judge(text) for text in texts]
    return [
        Row(
            text, tags, judged[n], specification[n], by_pandoc,
            markdown_it_sources(_FRONT + text, tables=False), markdown_it_sources(_FRONT + text, tables=True),
        )
        for n, ((text, tags), by_pandoc) in enumerate(zip(generated, pandoc, strict=True))
    ]


@pytest.fixture(scope="module")
def analysis() -> list[Row]:
    """The documents, analysed once for the tests below."""
    return analysed()


def _norm(path: str) -> str:
    """A path as the comparison reads it: a run of spaces, tabs and line breaks one space. Each renderer, and the model,
    reads the break and the indentation of a destination that goes over lines as it does (the model's heading text
    joins its lines with a space, pandoc reads a tab as spaces): the rules look at the structure of a path, which
    those never change."""
    return re.sub(r"[ \t\n]+", " ", path.strip())


def _shown(source: str) -> bool:
    return bool(source.strip()) and not _SCHEME_RE.match(source)


def test_every_image_a_renderer_displays_has_its_destination_judged(analysis):
    """(A) Safety. pandoc, markdown-it and markdown-it with tables: the destination of each image is judged."""
    compared = 0
    for row in analysis:
        judged = {_norm(path) for path in row.judged}
        for renderer, sources in (("pandoc", row.pandoc), ("markdown-it", row.markdown_it), ("markdown-it tables", row.tables)):
            for source in sources or ():
                if _shown(source):
                    assert _norm(source) in judged, (renderer, row.text, source, row.judged)
                    compared += 1
    assert compared > 20_000


def test_every_destination_judged_is_displayed_by_some_renderer(analysis):
    """(B) Minimality: a path judged is the destination of an image one of them displays. (Or of one that the
    specification's reading displays, which is judged as it always was: neither renderer displays it where a definition
    they read stands before the one the specification reads, #121.)"""
    compared = 0
    for row in analysis:
        if row.pandoc is None:
            continue
        shown = {_norm(source) for source in (*row.pandoc, *row.markdown_it, *row.tables)}
        specification = {_norm(path) for path in row.specification}
        for path in row.judged:
            assert _norm(path) in shown or _norm(path) in specification, (row.text, path, sorted(shown))
            compared += 1
    assert compared > 10_000


#: What the generator must reach, each in this many documents where the readings add to what the specification judges.
_FLOORS = {
    "odd-first": 500, "odd-later-plain": 300, "plain-later-odd": 300, "plain-later-plain": 100, "odd-later-odd": 300,
    "table": 200, "table-definition-cell": 100, "table-row-definition": 50, "nested-table": 30, "setext": 100,
    "setext-odd": 100, "u0390": 50, "u3000": 50, "backslash-tab": 200, "backslash-lf": 200, "backslash-control": 200,
    "list": 200, "nested": 50, "quote": 200, "blank-label": 200, "title-image": 200, "backslash-end": 200,
}


def test_the_generator_reaches_every_pitfall(analysis):
    """Each pitfall is in documents where the readings matter: judged destinations that the specification's reading
    of the definitions alone (and the inline images any renderer reads) does not give. A quiet generator would pass the
    two tests above for nothing."""
    reached = Counter()
    for row in analysis:
        if Counter(row.judged) != Counter(row.specification):
            reached.update(row.tags)
    missing = {tag: (reached[tag], floor) for tag, floor in _FLOORS.items() if reached[tag] < floor}
    assert not missing, missing


# ── The label keys, against every code point ──────────────────────

#: Every code point the labels are brute-forced over, to U+30000 (the planes with letters, spaces and compatibility
#: characters in them), but for the surrogates.
_CODE_POINTS = [c for c in map(chr, range(0x30001)) if not 0xD800 <= ord(c) < 0xE000]


def _pandoc_matches(pairs: list[tuple[str, str]]) -> set[int]:
    """Which of the *pairs* of labels pandoc matches: ``[a]: /dN`` and ``![x][b]``, each pair with a label of its own."""
    source = "".join(f"MARK{n}\n\n[k{n}z{a}]: /d{n}\n\n![x][k{n}z{b}]\n\n" for n, (a, b) in enumerate(pairs))
    out = subprocess.run(
        ["pandoc", "-f", "commonmark", "-t", "json"], input=source, capture_output=True, text=True, check=True
    ).stdout
    matched: set[int] = set()
    current = 0
    for block in json.loads(out)["blocks"]:
        marker = block["c"][0].get("c", "") if block.get("t") == "Para" and len(block["c"]) == 1 else ""
        if isinstance(marker, str) and marker.startswith("MARK") and marker[4:].isdigit():
            current = int(marker[4:])
        else:
            found: list[str] = []
            _walk_pandoc(block, found)
            if found:
                matched.add(current)
    return matched


def _markdown_it_matches(pairs: list[tuple[str, str]]) -> set[int]:
    source = "".join(f"[k{n}z{a}]: /d{n}\n\n![x][k{n}z{b}]\n\n" for n, (a, b) in enumerate(pairs))
    env: dict = {}
    tokens = markdown_it.MarkdownIt("commonmark").parse(source, env)
    return {
        int(re.fullmatch(r"/d(\d+)", child.attrGet("src")).group(1))  # type: ignore[union-attr]
        for token in tokens for child in token.children or [] if child.type == "image"
    }


def _brackets(c: str) -> bool:
    """A character that is no part of a label as these tests write it: a bracket or backslash, white space the
    labels are read for, DEL, ``<`` and ``>`` (HTML), and a CR (read as a line break)."""
    return c in "[]\\\n\r\t \x7f<>"


def test_the_label_whitespace_of_each_reading_is_the_renderers_over_every_code_point():
    """A code point is white space to pandoc when ``label_key`` makes ``a<c>b`` and ``a b`` one label, the same as
    pandoc matches them: the Unicode space separators and the ASCII white space (Haskell's ``isSpace``). For
    markdown-it, ``str.isspace`` (its labels are split by ``\\s``). The specification's are a space, a tab and a line
    break. Every code point to U+30000, U+3000 (the ideographic space) among them."""
    points = [c for c in _CODE_POINTS if c not in "[]\\\r\x7f<>"]
    pairs = [(f"a{c}b", "a b") for c in points]
    by_pandoc = _pandoc_matches(pairs)
    by_markdown_it = _markdown_it_matches(pairs)
    for n, (a, b) in enumerate(pairs):
        assert (label_key(a, Reading.PANDOC) == label_key(b, Reading.PANDOC)) == (n in by_pandoc), hex(ord(a[1]))
        assert (label_key(a, Reading.MDIT) == label_key(b, Reading.MDIT)) == (n in by_markdown_it), hex(ord(a[1]))
    pandoc_space = {c for n, c in enumerate(points) if n in by_pandoc}
    assert pandoc_space == {c for c in points if c in " \t\n\x0b\x0c" or unicodedata.category(c) == "Zs"}
    assert {c for n, c in enumerate(points) if n in by_markdown_it} == {c for c in points if c.isspace()}
    assert {c for c in points if label_key(f"a{c}b") == "a b"} == set(" \t\n")
    assert "\u3000" in pandoc_space and "\u00a0" in pandoc_space and "\u2028" not in pandoc_space


def test_the_case_folds_of_each_reading_are_the_renderers_over_every_code_point():
    """Each code point against what it folds to, and to its canonical equivalents and the upper case and lower case
    forms: pandoc reads its input as NFC and folds with ``casefold`` after it (so the three code points ``ΐ`` and
    its upper case are not one label, but ``é`` and ``e`` with an accent are); markdown-it folds with
    ``lower().upper()`` (so ``ı`` is ``I``)."""
    pairs: list[tuple[str, str]] = []
    for c in _CODE_POINTS:
        if _brackets(c) or c.isascii() and not c.isalpha():
            continue
        for other in {c.casefold(), c.lower().upper(), c.upper(), c.lower(), unicodedata.normalize("NFC", c),
                      unicodedata.normalize("NFD", c), unicodedata.normalize("NFKC", c)}:
            if other != c and not any(_brackets(each) for each in other):
                pairs.append((c, other))
    assert len(pairs) > 15_000
    by_pandoc = _pandoc_matches(pairs)
    by_markdown_it = _markdown_it_matches(pairs)
    for n, (a, b) in enumerate(pairs):
        assert (label_key(a, Reading.PANDOC) == label_key(b, Reading.PANDOC)) == (n in by_pandoc), (a, b)
        assert (label_key(a, Reading.MDIT) == label_key(b, Reading.MDIT)) == (n in by_markdown_it), (a, b)
