"""Differential tests for the linear-time scanners of #94: each is compared
with a verbatim copy of the code it replaced, on many random inputs from a
fixed seed. The copies are kept here so that the behaviour they define cannot
drift unnoticed. Small alphabets hit the edge cases: ``<!-->``, ``<!--->``,
``<!---->``, unclosed comments, markers beside code spans and comments."""
from __future__ import annotations

import random
import re
from dataclasses import replace

import pytest

from legaldown import directives as directives_module
from legaldown import markdown
from legaldown.definitions import block_fragments
from legaldown.directives import Directive, lex, mask_directives
from legaldown.markdown import HTML_COMMENT_RE, is_comment_only
from legaldown.markers import MARKER_RE, is_look_alike, parse_marker
from legaldown.parser import parse
from legaldown.validator import core, templates
from legaldown.validator import units as units_module
from legaldown.validator.conditions import ALWAYS
from legaldown.validator.units import FoundMarker, find_markers, own_presence

# ── The code that was replaced (#94), verbatim ────────────────────

_OLD_COMMENT_PATTERN = r"<!--(?:-?>|.*?-->)"
OLD_HTML_COMMENT_RE = re.compile(_OLD_COMMENT_PATTERN, re.DOTALL)


def old_is_comment_only(html):
    rest = OLD_HTML_COMMENT_RE.sub("", html)
    opened = rest.find("<!--")
    return not (rest[:opened] if opened >= 0 else rest).strip()


def old_is_include_only(text, directives):
    inside = [d for d in directives if d.end <= len(text)]
    return (
        len(inside) == 1
        and inside[0].name == "include"
        and not inside[0].malformed
        and not OLD_HTML_COMMENT_RE.sub("", text[: inside[0].start]).strip()
        and not OLD_HTML_COMMENT_RE.sub("", text[inside[0].end:]).strip()
    )


def old_marker_matches(text, lexed):
    for match in MARKER_RE.finditer(lexed.view):
        if not is_look_alike(match.group(0)):
            continue
        if not units_module.is_escaped(text, match.start()) and not any(
            d.start <= match.start() < d.end for d in lexed.directives if not d.malformed
        ):
            yield match


def old_find_markers(document, lex_fragment=lex):
    """``find_markers`` as of 0.4.0."""
    _NOT_A_MARKER = units_module._NOT_A_MARKER
    _ANYWHERE = units_module._ANYWHERE
    _PREAMBLE_ANCHOR = units_module._PREAMBLE_ANCHOR
    _PREAMBLE_CONDITION = units_module._PREAMBLE_CONDITION
    _PREAMBLE_ITEM = units_module._PREAMBLE_ITEM
    _PARAGRAPHS = units_module._PARAGRAPHS
    _LISTS = units_module._LISTS

    found = []
    for section, block_index, block in document.iter_indexed_blocks():
        for fragment_index, (fragment, position) in enumerate(block_fragments(block)):
            if "{" not in fragment or ("#" not in fragment and "when=" not in fragment):
                continue
            lexed = lex_fragment(fragment)
            for m in old_marker_matches(fragment, lexed):
                marker = parse_marker(m.group(0))
                at_end = position and not OLD_HTML_COMMENT_RE.sub("", fragment[m.end():]).strip()
                include_only = (
                    at_end
                    and block.kind == "paragraph"
                    and old_is_include_only(fragment[:m.start()], lexed.directives)
                )
                template_only = bool(
                    section is None
                    and at_end
                    and block.kind in _PARAGRAPHS
                    and marker
                    and marker.condition
                    and not marker.identifier
                    and not include_only
                )
                if marker is None:
                    misplaced = _NOT_A_MARKER
                elif not at_end:
                    misplaced = _ANYWHERE
                elif section is None and marker.identifier and not include_only:
                    misplaced = _PREAMBLE_ANCHOR
                elif template_only:
                    misplaced = _PREAMBLE_CONDITION
                elif section is None and block.kind in _LISTS and marker.condition:
                    misplaced = _PREAMBLE_ITEM
                elif section is None and not include_only:
                    misplaced = _ANYWHERE
                else:
                    misplaced = ""
                found.append(
                    FoundMarker(
                        m.group(0), marker, section, block_index, fragment_index,
                        misplaced, include_only, template_only, m.start(),
                    )
                )
    return found


_OLD_LINK_REFERENCE_RE = re.compile(
    r" {0,3}\[[^\]]+\]:[ \t]*\S*(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?"
)


def old_insertion_boundary_problem(text, template, insertion, directives):
    """``insertion_boundary_problem`` as of 0.4.0 (now ``_InsertionScan``)."""
    start, end = insertion.start, insertion.end
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line_end = len(text) if line_end < 0 else line_end
    content_start = min(templates._CONTAINER_RE.match(text, line_start).end(), start)

    if start > content_start:
        before = text[start - 1]
        if not (
            before.isalnum()
            or before in " \t"
            or any(directive.end == start for directive in directives)
            or (
                before in templates._BEFORE_INSERTION
                and not (before == "(" and text[start - 2:start - 1] == "]")
            )
        ):
            return f"'{before}' directly before it"
        run_start = max(
            template.rfind(" ", content_start, start),
            template.rfind("\t", content_start, start),
            content_start - 1,
        ) + 1
        combining = next((ch for ch in template[run_start:start] if ch in templates._COMBINING), None)
        if combining:
            return f"'{combining}' in the text just before it"
    if end < line_end:
        after = text[end]
        if not (
            after.isalnum()
            or after in " \t"
            or text.startswith("{{", end)
            or after in templates._AFTER_INSERTION
        ):
            return f"'{after}' directly after it"
    reference = _OLD_LINK_REFERENCE_RE.match(template, content_start)
    if reference and start < reference.end() and not template[reference.end():line_end].strip():
        return "it is in a link reference definition"
    destination = template.rfind("](", line_start, start)
    if destination >= 0 and template.find(")", destination + 2, start) < 0:
        return "it is inside a link or image destination"
    return None


# ── Random inputs ─────────────────────────────────────────────────


def _random_strings(alphabet, count, max_length, seed):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(alphabet) for _ in range(rng.randint(0, max_length)))


def _random_texts(tokens, count, max_tokens, seed):
    rng = random.Random(seed)
    for _ in range(count):
        yield "".join(rng.choice(tokens) for _ in range(rng.randint(0, max_tokens)))


# Characters of comments; ``\x1c`` and the no-break space differ between
# the whitespace ``str.strip`` removes and the whitespace Markdown knows.
_COMMENT_ALPHABET = ["<", "!", "-", ">", "`", " ", "\n", "a", "{", "}", "<!--", "-->", "--", "\xa0", "\x1c"]


def test_the_comment_regex_is_the_one_copied_here():
    assert HTML_COMMENT_RE.pattern == _OLD_COMMENT_PATTERN
    assert HTML_COMMENT_RE.flags == OLD_HTML_COMMENT_RE.flags


@pytest.mark.parametrize("alphabet", [_COMMENT_ALPHABET, list("<!->"), ["<!--", "-->", "->", ">", "-", " ", "a"]])
def test_comment_spans_are_the_regex_matches(alphabet):
    for s in _random_strings(alphabet, 6000, 24, seed=94):
        expected = [m.span() for m in OLD_HTML_COMMENT_RE.finditer(s)]
        assert markdown.comment_spans(s) == expected, repr(s)


@pytest.mark.parametrize("alphabet", [_COMMENT_ALPHABET, list("<!->"), ["<!--", "-->", "->", ">", "-", " ", "a"]])
def test_strip_comments_is_sub(alphabet):
    for s in _random_strings(alphabet, 6000, 24, seed=95):
        assert markdown.strip_comments(s) == OLD_HTML_COMMENT_RE.sub("", s), repr(s)
        assert markdown.strip_comments(s, blank=True) == OLD_HTML_COMMENT_RE.sub(lambda c: " " * len(c.group()), s), repr(s)


def test_comment_end_is_the_match_at_a_position():
    for s in _random_strings(_COMMENT_ALPHABET, 4000, 24, seed=96):
        closers = markdown.comment_closers(s)
        for at in (i for i in range(len(s)) if s.startswith("<!--", i)):
            match = OLD_HTML_COMMENT_RE.match(s, at)
            assert markdown.comment_end(s, at, closers) == (match.end() if match else None), (s, at)


def test_is_comment_only_is_unchanged():
    for s in _random_strings(_COMMENT_ALPHABET, 4000, 24, seed=97):
        assert is_comment_only(s) == old_is_comment_only(s), repr(s)


def old_blank(text, start, end):
    blanked = "".join("\n" if ch == "\n" else " " for ch in text[start:end])
    return text[:start] + blanked + text[end:]


def old_malformed_end(text, start, body):
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    close = text.find("}}", body, line_end)
    end = close + 2 if close >= 0 else line_end
    return next((pos for pos in range(body, end) if directives_module._opens_directive(text, pos)), end)


def old_lex_directive(text, start, opener):
    """``_lex_directive`` as of 0.4.0: its malformed end searched the line."""
    d = directives_module
    try:
        positional, positional_span, params, param_spans, duplicates, unquoted, end = d._lex_arguments(
            text, opener.end()
        )
    except d._Malformed as exc:
        end = old_malformed_end(text, start, opener.end())
        return d.Directive(
            name=opener.group(1), positional=None, params={}, duplicates=(), malformed=str(exc),
            start=start, end=end, source=text[start:end].rstrip(d._WS),
        )
    return d.Directive(
        name=opener.group(1), positional=positional, params=params, duplicates=tuple(duplicates), malformed="",
        start=start, end=end, source=text[start:end], unquoted=tuple(unquoted),
        positional_span=positional_span, param_spans=param_spans,
    )


def old_lex(text):
    """``lex`` as of 0.4.0: a comment is read with the regex at each opener."""
    d = directives_module
    view = text or ""
    found, stray_braces, literals = [], [], []
    pos = 0
    while token := d._INLINE_START_RE.search(view, pos):
        start = token.start()
        if d.is_escaped(view, start):
            pos = start + 1
            continue
        if token.group(0) == "<!--":
            comment = OLD_HTML_COMMENT_RE.match(view, start)
            if comment is None:
                pos = start + 4
            else:
                view = old_blank(view, start, comment.end())
                literals.append(("comment", start, comment.end()))
                pos = comment.end()
            continue
        if token.group(0).startswith("`"):
            ticks = len(token.group(0))
            close = next(
                (m for m in d._BACKTICKS_RE.finditer(view, token.end()) if len(m.group(0)) == ticks),
                None,
            )
            if close is None:
                pos = token.end()
            else:
                view = old_blank(view, start, close.end())
                literals.append(("code", start, close.end()))
                pos = close.end()
            continue
        opener = d._OPENER_RE.match(view, start)
        if opener is None:
            stray_braces.append(start)
            pos = start + 1
            continue
        directive = old_lex_directive(text, start, opener)
        found.append(directive)
        pos = directive.end
    return d.Lexed(found, stray_braces, view, literals)


def test_the_lexer_equals_the_old_one_on_malformed_directives_and_code_spans():
    """Unclosed directives, quoted values, runs of backticks of every length,
    and backslashes before braces and backticks: what the old lexer searched
    the rest of the line or the text for."""
    tokens = ["{{ref: a", "{{ref: a}}", "{{ref: \"", "{{x:", "{{", "}}", "}", ",", " ", "\n", "\n", "a", "b=",
              "`", "``", "```", "\\", "\\`", "<!--", "-->", "\"", "{{placeholder: p, note=", "{{include: f.md}}"]
    reached = 0
    for s in _random_texts(tokens, 12000, 14, seed=981):
        lexed = lex(s)
        assert lexed == old_lex(s), repr(s)
        reached += any(d.malformed for d in lexed.directives) and bool(lexed.literals)
    assert reached > 300  # texts with both malformed directives and literals


def test_the_lexer_reads_comments_as_the_regex_does():
    tokens = ["<!--", "-->", "<!-->", "<!--->", "<!---->", "`", "``", " ", "\n", "a", "{{placeholder: p}}",
              "{{", "}}", "\\", "{{placeholder: \"<!--\"}}"]
    for s in _random_texts(tokens, 6000, 12, seed=98):
        assert lex(s) == old_lex(s), repr(s)


# ── find_markers ──────────────────────────────────────────────────

_MARKER_TOKENS = [
    "{#x}", "{#a}", "{when=a}", "{when=!a}", "{#x when=a}", "{ #x}", "{#1 and #2}", "{#x class=y}",
    "`", "``", "<!--", "-->", "<!-->", "<!--->", "<!---->", "{{include: f.md}}", "{{placeholder: p}}",
    "{{placeholder: q, note=\"{#x}\"}}", "{{", "}}", "\\", " ", " ", "\n", "a", "b", "{", "}", "#", "when=",
]
_CONTEXTS = [
    "{text}",  # a preamble paragraph
    "# T\n\n{text}",  # a paragraph in a section
    "# T\n\n- {text}",
    "# T\n\n- first\n\n  {text}",
    "# T\n\n1. a\n   - {text}\n   - b",
    "# T\n\n{text}\n\nnext",
    "# T\n\n> {text}",
    "# T\n\n| h |\n|---|\n| {text} |",
    "# T\n\n<div>\n{text}\n</div>",
    "- {text}",
    "# T {text}",
    "# T\n\n## U\n\n{text}",
]


def test_blank_from_is_the_old_tail_test_at_every_offset():
    alphabet = ["<!--", "-->", "->", ">", "-", " ", "\n", "a", "\xa0", "\x1c", "<", "!", "`"]
    for s in _random_texts(alphabet, 6000, 14, seed=939):
        blank = units_module.blank_from(s)
        for offset in range(len(s) + 1):
            assert blank(offset) == (not OLD_HTML_COMMENT_RE.sub("", s[offset:]).strip()), (s, offset)


def test_find_markers_equals_the_old_scan():
    rng = random.Random(9494)
    checked = 0
    for text in _random_texts(_MARKER_TOKENS, 2500, 14, seed=940):
        source = rng.choice(_CONTEXTS).format(text=text)
        document = parse(source, filename="t.lgd")
        expected = old_find_markers(document)
        assert find_markers(document) == expected, source
        checked += len(expected)
    assert checked > 1500  # the generator reaches markers


def test_find_markers_equals_the_old_scan_on_comment_heavy_text():
    rng = random.Random(9495)
    tokens = ["{#x}", "{when=a}", "<!--", "-->", "<!-->", "<!--->", "`", " ", "\n", "a"]
    for text in _random_texts(tokens, 2500, 12, seed=941):
        source = rng.choice(_CONTEXTS).format(text=text)
        document = parse(source, filename="t.lgd")
        assert find_markers(document) == old_find_markers(document), source


def test_find_markers_equals_the_old_scan_on_marker_tails():
    """A marker is in position when only whitespace and comments follow it
    (§8.6): tails of comment fragments, closed, unclosed, or opened before
    the marker, where the scan must look only at what follows."""
    rng = random.Random(9496)
    heads = ["a ", "`<!--` ", "<!-- c --> ", "<!-- ", "{{include: f.md}} ", "x\n\n", "-->", "`", "<!-->", " "]
    ids = ["{#x}", "{when=a}", "{#x when=a}", "{#a}"]
    tails = [" ", "\n", "<!-- c -->", "<!--", "-->", "<!-->", "<!--->", "<!---->", "`", "a", "\xa0", "\x1c", "{#b}"]
    placed = in_include = 0
    for _ in range(4000):
        text = (
            "".join(rng.choice(heads) for _ in range(rng.randint(0, 3)))
            + rng.choice(ids)
            + "".join(rng.choice(tails) for _ in range(rng.randint(0, 6)))
        )
        document = parse(rng.choice(_CONTEXTS).format(text=text), filename="t.lgd")
        expected = old_find_markers(document)
        assert find_markers(document) == expected, text
        placed += sum(1 for f in expected if not f.misplaced)
        in_include += sum(1 for f in expected if f.include_only)
    assert placed > 500 and in_include > 20  # both verdicts are reached


def test_find_markers_reads_the_lexer_it_is_given():
    """A custom lexer's directives need not be sorted or disjoint."""
    document = parse("# T\n\na {#x} b {when=c}\n", filename="t.lgd")

    def lex_fragment(text):
        lexed = lex(text)
        spans = [
            Directive(name="x", positional=None, params={}, duplicates=(), malformed="", start=s, end=e, source="")
            for s, e in [(5, 9), (0, 3), (4, 6)]
        ]
        return replace(lexed, directives=spans)

    assert find_markers(document, lex_fragment) == old_find_markers(document, lex_fragment)


# ── Insertion boundaries ──────────────────────────────────────────


def fresh_problem(text, template, insertion, directives):
    """The verdict of a scan made for this insertion alone."""
    return templates._InsertionScan(text, template, directives).problem(insertion)



_BOUNDARY_TOKENS = [
    "{{placeholder: a}}", "{{placeholder: a}}", "{{choose: q, x=y}}", "{{placeholder: \"a b\"}}", "{{def: t}}",
    "{{placeholder: a", " ", "\t", "\n", "\n", "a", "1", "(", ")", "[", "]", "](", "]:", "[a]:", "&", "<", "\\",
    "> ", "- ", "1. ", "* ", ".", ",", "\"", "'", "/", "-", "`", "<!--", "-->", "é", "\xa0", "{", "}", "x y",
]


def _boundary_cases(count, max_tokens, seed, tokens=_BOUNDARY_TOKENS):
    for text in _random_texts(tokens, count, max_tokens, seed):
        lexed = lex(text)
        template = mask_directives(text, lexed.directives)
        for directive in lexed.directives:
            if directive.name in ("placeholder", "choose") and not directive.malformed:
                yield text, template, directive, lexed.directives


def test_the_boundary_verdict_equals_the_old_one():
    checked = problems = 0
    for text, template, directive, directives in _boundary_cases(7000, 14, seed=943):
        expected = old_insertion_boundary_problem(text, template, directive, directives)
        assert fresh_problem(text, template, directive, directives) == expected, (
            text, directive.start,
        )
        checked += 1
        problems += expected is not None
    assert checked > 4000 and problems > 1000  # both verdicts are reached


def test_the_boundary_verdict_equals_the_old_one_around_links():
    tokens = ["[a]: ", "[a]:", "[b] ", "{{placeholder: a}}", "{{placeholder: a}}", " ", "\n", "x", "> ", "- ",
              "](", ")", "\"t\"", "(", "<"]
    reference = destination = 0
    for text, template, directive, directives in _boundary_cases(9000, 10, seed=945, tokens=tokens):
        expected = old_insertion_boundary_problem(text, template, directive, directives)
        assert fresh_problem(text, template, directive, directives) == expected, (
            text, directive.start,
        )
        reference += expected == "it is in a link reference definition"
        destination += expected == "it is inside a link or image destination"
    assert reference > 50 and destination > 100  # both verdicts are reached


def test_an_insertion_scan_serves_every_directive_of_a_text():
    """The checker reuses one index for all of a text's insertions."""
    for text in _random_texts(_BOUNDARY_TOKENS, 3000, 18, seed=944):
        lexed = lex(text)
        template = mask_directives(text, lexed.directives)
        scan = templates._InsertionScan(text, template, lexed.directives)
        for directive in lexed.directives:
            if directive.name in ("placeholder", "choose") and not directive.malformed:
                expected = old_insertion_boundary_problem(text, template, directive, lexed.directives)
                assert scan.problem(directive) == expected, (text, directive.start)


# ── free_identifier ───────────────────────────────────────────────


def old_free_identifier(base, presence, explicit_ids, anchors, questions):
    """The closure ``free_identifier`` of ``_validate`` as of 0.4.0."""
    candidate, suffix = base, 1
    while candidate in explicit_ids or core._clashes(presence, anchors.get(candidate, []), questions):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


_QUESTIONS = {
    "a": {"type": "boolean"},
    "b": {"type": "boolean"},
    "c": {"type": "choice", "choices": {"x": "X", "y": "Y"}},
}
_CONDITIONS = ["a", "!a", "b", "c:x", "c:y", "!b"]


def _presences(rng):
    """ALWAYS, one or two conditions, and contradictions (which never appear)."""
    presence = ALWAYS
    for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
        presence = presence | own_presence(rng.choice(_CONDITIONS), _QUESTIONS)
    return presence


@pytest.mark.parametrize("seed", range(40))
def test_free_identifiers_are_the_lowest_free_suffix(seed):
    """Headings of a few titles, under a few presences, in a document that
    also has explicit identifiers: the new pool hands out what the old probe
    did, heading by heading (the suffixes are taken in the order of the
    document, as the caller declares each one)."""
    rng = random.Random(seed)
    bases = ["same", "other", "x"]
    explicit = {f"{rng.choice(bases)}{rng.choice(['', '-2', '-3', '-5', '-9'])}" for _ in range(rng.randint(0, 5))}
    presences = [_presences(rng) for _ in range(rng.randint(1, 5))]
    new_anchors: dict[str, list] = {}
    old_anchors: dict[str, list] = {}
    pool = core._FreeIdentifiers(explicit, new_anchors, _QUESTIONS)
    for _ in range(rng.randint(5, 60)):
        base, presence = rng.choice(bases), rng.choice(presences)
        got = pool(base, presence)
        assert got == old_free_identifier(base, presence, explicit, old_anchors, _QUESTIONS)
        new_anchors.setdefault(got, []).append(presence)
        old_anchors.setdefault(got, []).append(presence)
        if rng.random() < 0.1:  # an explicit anchor declared in between, as sections with ids do
            other = rng.choice(bases)
            new_anchors.setdefault(other, []).append(presence)
            old_anchors.setdefault(other, []).append(presence)


# ── Offsets ───────────────────────────────────────────────────────


def test_find_all_is_repeated_find():
    for s in _random_strings(list("}{a\n]()-"), 3000, 16, seed=991):
        for needle in ("}}", "\n", "-->", "](", "--"):
            for overlapping in (False, True):
                expected, at = [], s.find(needle)
                while at >= 0:
                    expected.append(at)
                    at = s.find(needle, at + (1 if overlapping else len(needle)))
                assert markdown.find_all(s, needle, overlapping=overlapping) == expected
    assert markdown.find_all("}}}", "}}") == [0]
    assert markdown.find_all("}}}", "}}", overlapping=True) == [0, 1]


def test_landmarks_answer_either_question_first():
    """``close`` is asked before ``line_end`` in some orders: both lists are
    found on first use of either."""
    for s in _random_strings(list("}{a\n"), 1500, 14, seed=992):
        for body in range(len(s) + 1):
            line_end = s.find("\n", body)
            line_end = len(s) if line_end < 0 else line_end
            landmarks = directives_module._Landmarks(s)
            assert landmarks.close(body, line_end) == s.find("}}", body, line_end), (s, body)
            assert landmarks.line_end(body) == line_end, (s, body)


def test_find_all_needs_a_needle():
    for overlapping in (False, True):
        with pytest.raises(ValueError):
            markdown.find_all("abc", "", overlapping=overlapping)


def test_comment_closers_are_the_overlapping_offsets_too():
    """``-->`` cannot overlap itself, so the two readings agree."""
    for s in _random_strings(list("-->a"), 3000, 16, seed=993):
        assert markdown.comment_closers(s) == markdown.find_all(s, "-->", overlapping=True)


def test_one_scan_per_text_equals_a_fresh_scan_per_insertion():
    """Several insertions over several lines, containers, links and
    definitions: the scan's per-line caches give the verdicts that a scan
    made for each insertion does."""
    tokens = _BOUNDARY_TOKENS + ["{{placeholder: a}}"] * 8 + ["\n"] * 6 + ["[a]: x ", "> - ", "](x"]
    insertions = 0
    for text in _random_texts(tokens, 3000, 30, seed=946):
        lexed = lex(text)
        template = mask_directives(text, lexed.directives)
        shared = templates._InsertionScan(text, template, lexed.directives)
        found = [d for d in lexed.directives if d.name in ("placeholder", "choose") and not d.malformed]
        for directive in reversed(found):  # in any order
            assert shared.problem(directive) == fresh_problem(text, template, directive, lexed.directives), (
                text, directive.start,
            )
        insertions += len(found)
    assert insertions > 8000
