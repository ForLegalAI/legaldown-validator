"""The include fragments a document refers to, read by ``validate(resolve=)`` and
checked as the Full level does (#35): ``include-file-missing``,
``include-has-frontmatter``, ``include-has-h1``, ``include-heading-skip`` (§16.11)
and the parts of ``template-fragment-invalid`` (§16.12) that read a fragment, each at
its line in the file it is in; ``include-not-legaldown`` (Core); and the kind of a
fragment validated on its own (``fragment_kind=``): an include fragment or an
attachment file (§12.2, §12.4, §16.10)."""
from __future__ import annotations

import dataclasses
import json

import pytest

from legaldown import Template, load, parse, validate
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_OK, main
from legaldown.files import file_loader
from legaldown.validator.fragments import Files, FragmentFile, fragment_findings

# The frontmatter of the corpus's include cases: 17 lines, so a body begins at line 19.
_FRONT = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    label: Provider\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    label: Client\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)
_TEMPLATE_FRONT = _FRONT.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n")
_RULES = {
    "include-file-missing", "include-has-frontmatter", "include-has-h1", "include-heading-skip",
    "include-not-legaldown", "template-fragment-invalid", "attachment-has-frontmatter", "attachment-has-h1",
    "path-not-relative", "path-outside-root", "heading-skip",
}


class _Loader:
    """A loader over a dict that remembers what it was asked for."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.asked: list[str] = []

    def __call__(self, path: str) -> str | None:
        self.asked.append(path)
        return self.files.get(path)


def _run(main_text: str, files: dict[str, str] | None = None, **kwargs):
    """Validate *main_text* (named ``main.lgd``) with a loader over *files*."""
    loader = _Loader(files or {})
    result = validate(parse(main_text, filename="main.lgd", on_frontmatter_error="keep"), resolve=loader, **kwargs)
    return result, loader


def _at(text: str, needle: str, nth: int = 0) -> int:
    """The line (from 1) of the *nth* line of *text* that holds *needle*."""
    return [number for number, line in enumerate(text.split("\n"), 1) if needle in line][nth]


def _found(result, *rules: str) -> list[tuple[str, str, int | None]]:
    """``(rule, file, line)`` of the findings of *rules* (by default those of #35 and the paths)."""
    wanted = set(rules) or _RULES
    return [(d.rule, d.file, d.line) for d in result.diagnostics if d.rule in wanted]


# ── The fixtures of the specification's corpus, as the harness runs them ─────────

_SCOPE = "# Scope {#scope}\n\n"


@pytest.mark.parametrize(("main_text", "files", "expected"), [
    (_FRONT + _SCOPE + "{{include: absent.lgd}}\n", {}, [("include-file-missing", "main.lgd", 21)]),
    (_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": "---\ntitle: Not allowed\n---\n\nFragment body.\n"},
     [("include-has-frontmatter", "part.lgd", 2)]),
    (_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": "# Not allowed {#part-heading}\n\nFragment body.\n"},
     [("include-has-h1", "part.lgd", 1)]),
    (_FRONT + _SCOPE + "{{include: part.lgd}}\n",
     {"part.lgd": "FragmentFile body.\n\n### Too deep {#too-deep}\n\nSkips level 2 at the splice point.\n"},
     [("include-heading-skip", "part.lgd", 3)]),
    (_TEMPLATE_FRONT + "# Support {#support}\n\nStandard.\n\n{{include: extended.lgd}} {when=x}\n",
     {"extended.lgd": "## Extended Support\n\nOutside business hours.\n"},
     [("template-fragment-invalid", "extended.lgd", 1)]),
    (_FRONT + _SCOPE + "{{include: notes.txt}}\n", {}, [("include-not-legaldown", "main.lgd", 21)]),
])
def test_the_full_include_fixtures_are_reported_in_their_file_at_their_line(main_text, files, expected):
    result, _ = _run(main_text, files)
    assert _found(result) == expected


# ── What is read, and what is not ────────────────────────────────

def test_a_document_that_is_loaded_reads_the_fragments_beside_it(tmp_path):
    (tmp_path / "main.lgd").write_text(_FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{include: gone.lgd}}\n", encoding="utf-8")
    (tmp_path / "part.lgd").write_text("# Not allowed {#h}\n", encoding="utf-8")
    document = load(tmp_path / "main.lgd")
    assert _found(validate(document)) == [("include-has-h1", "part.lgd", 1), ("include-file-missing", "main.lgd", 23)]
    # Without a path there is nothing to read from, and nothing is asked: parse(text), or a loaded one without its path.
    assert _found(validate(dataclasses.replace(document, path=None))) == []
    assert _found(validate(parse(_FRONT + _SCOPE + "{{include: gone.lgd}}\n"))) == []
    # A loader that finds nothing says every fragment is missing (`resolve=lambda p: None` does not opt out).
    assert _found(validate(document, resolve=lambda _path: None)) == [
        ("include-file-missing", "main.lgd", 21), ("include-file-missing", "main.lgd", 23)
    ]


def test_only_an_include_paragraph_is_an_include_and_is_read():
    text = (
        _FRONT + _SCOPE + "See {{include: a.lgd}} here.\n\n- {{include: b.lgd}}\n\n> {{include: c.lgd}}\n\n"
        "{{include: d.lgd}} {#anchor-ignored}\n\n{{include: e.lgd}} <!-- a comment -->\n\n"
        "| a |\n|---|\n| {{include: f.lgd}} |\n"
    )
    result, loader = _run(text, {"d.lgd": "## D {#d}\n", "e.lgd": "## E {#e}\n"})
    assert loader.asked == ["d.lgd", "e.lgd"]
    assert _found(result) == []


def test_an_include_in_a_drafting_note_is_not_read_outside_a_template_either():
    text = _FRONT + _SCOPE + "> [!DRAFTING]\n> {{include: gone.lgd}}\n"
    result, loader = _run(text)
    assert loader.asked == [] and _found(result) == []


def test_a_file_is_asked_for_once_and_reported_once_however_often_it_is_included():
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{include: ./part.lgd}}\n\n{{include: gone.lgd}}\n\n{{include: gone.lgd}}\n"
    result, loader = _run(text, {"part.lgd": "# H {#h}\n"})
    assert loader.asked == ["part.lgd", "gone.lgd"]
    # The file is the normalised path; a missing one is reported at each include.
    assert _found(result) == [
        ("include-has-h1", "part.lgd", 1), ("include-file-missing", "main.lgd", 25), ("include-file-missing", "main.lgd", 27)
    ]


def test_an_attachment_file_and_an_include_of_one_file_share_its_reading():
    text = (
        _TEMPLATE_FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n---\n\n")
        + "# A {#a}\n\nSee {{attach: s}}.\n\n{{include: s.lgd}}\n"
    )
    result, loader = _run(text, {"s.lgd": "## S {#s}\n\n\"Fee\" {{def: fee}} is due. {{term: fee}}\n"})
    assert loader.asked == ["s.lgd"]
    assert "template-fragment-invalid" in {d.rule for d in result.diagnostics}  # an attachment file also used as a fragment


def test_a_document_that_includes_itself_is_not_its_own_fragment(tmp_path):
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: main.lgd}}\n", encoding="utf-8")
    # include-cycle is not implemented; reading the document as a fragment would say it has frontmatter.
    assert _found(validate(load(path))) == []


def test_a_fragment_with_a_bom_and_crlf_is_reported_at_its_line():
    text = "\ufeff---\r\ntitle: T\r\n---\r\n\r\nBody.\r\n"
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": text})
    assert _found(result) == [("include-has-frontmatter", "part.lgd", 2)]


# ── Paths a loader is not asked for ──────────────────────────────

def test_a_path_that_is_not_relative_is_path_not_relative_alone(tmp_path):
    text = _FRONT + _SCOPE + "{{include: /abs.lgd}}\n\n{{include: https://x/y.lgd}}\n\n{{include: C:/z.lgd}}\n\n{{include: /notes.txt}}\n"
    result, loader = _run(text)
    assert loader.asked == []
    assert _found(result) == [("path-not-relative", "main.lgd", 21 + 2 * k) for k in range(4)]


def test_a_path_a_loader_is_not_asked_for_is_missing_only_when_it_is_no_valid_path(tmp_path):
    text = _FRONT + _SCOPE + "{{include: ../shared.lgd}}\n\n{{include: a\\b.lgd}}\n"
    result, loader = _run(text)
    assert loader.asked == []
    # ../shared.lgd is a valid path (§2.3) that is not read (#109): no finding, with a root or without one. A
    # backslash is none: include-file-missing. Neither is path-not-relative nor path-outside-root (no root).
    assert _found(result) == [("include-file-missing", "main.lgd", 23)]
    assert "relative path" in result.diagnostics[-1].message
    # Assembly still refuses the template for ../shared.lgd (a file it cannot read), which validation does not say.
    template = Template(_TEMPLATE_FRONT + _SCOPE + "{{include: ../shared.lgd}}\n", resolve=_Loader({}))
    assert [d.rule for d in template.problems] == ["include-file-missing"]
    assert [d.rule for d in template.validation.diagnostics if d.rule == "include-file-missing"] == []
    # With a root it leaves, path-outside-root says where it leads and is the only finding; within it, the
    # path is valid (§2.3) and not read outside the document's directory: skipped, not missing.
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    path = root / "sub" / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: ../shared.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path), document_root=root)) == []
    assert _found(validate(load(path), document_root=root / "sub")) == [("path-outside-root", "main.lgd", 21)]
    # Without a resolver (parse(text)) nothing is read, and nothing is said.
    assert _found(validate(parse(text, filename="main.lgd"))) == []


def test_a_path_that_is_not_a_legaldown_file_is_reported_without_a_resolver_and_is_never_read():
    for ending in ("notes.txt", "a.md", "a.LGD", "sub/dir", "lgd"):
        text = _FRONT + _SCOPE + f"{{{{include: {ending}}}}}\n"
        result = validate(parse(text, filename="main.lgd"))
        assert _found(result) == [("include-not-legaldown", "main.lgd", 21)], ending
        _, loader = _run(text)
        assert loader.asked == []
    for ending in ("a.lgd", "a.legaldown", "a.legal.md", "./a.lgd"):
        assert _found(validate(parse(_FRONT + _SCOPE + f"{{{{include: {ending}}}}}\n"))) == [], ending
    # An include in a sentence, a note or a list is text: the extension is not asked of it.
    other = _FRONT + _SCOPE + "See {{include: n.txt}}.\n\n- {{include: n.txt}}\n\n> [!DRAFTING]\n> {{include: n.txt}}\n"
    assert _found(validate(parse(other))) == []


def test_a_fragment_validated_on_its_own_still_judges_the_extension_of_its_includes():
    result = validate(parse("## A\n\n{{include: n.txt}}\n"), fragment=True, resolve=lambda _path: pytest.fail("read"))
    assert [(d.rule, d.line) for d in result.diagnostics] == [("include-not-legaldown", 3)]


# ── The fragment's shape: frontmatter and a level 1 heading ───────

def test_a_fragment_with_frontmatter_and_h1_is_reported_for_each_offender():
    part = "---\ntitle: T\n---\n\n# One {#one}\n\nText.\n\n# Two {#two}\n"
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(result) == [
        ("include-has-frontmatter", "part.lgd", 2), ("include-has-h1", "part.lgd", 5), ("include-has-h1", "part.lgd", 9)
    ]


def test_frontmatter_that_cannot_be_read_is_reported_at_the_line_of_the_error():
    part = "---\ntitle: [unclosed\n---\n\n## P {#p}\n"
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(result) == [("include-has-frontmatter", "part.lgd", 2)]
    # ... and not frontmatter-invalid-yaml as well, which is for a document whose block is allowed.
    assert "frontmatter-invalid-yaml" not in {d.rule for d in result.diagnostics}
    # An empty block is at its first line.
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": "---\n---\n\n## P {#p}\n"})
    assert _found(result) == [("include-has-frontmatter", "part.lgd", 1)]


def test_the_main_document_whose_own_frontmatter_cannot_be_read_still_reads_its_includes():
    text = "---\ntitle: [unclosed\n---\n\n# A {#a}\n\n{{include: part.lgd}}\n\n{{include: gone.lgd}}\n"
    result, _ = _run(text, {"part.lgd": "# H {#h}\n"})
    assert _found(result, "include-has-h1", "include-file-missing") == [
        ("include-has-h1", "part.lgd", 1), ("include-file-missing", "main.lgd", 9)
    ]
    assert "frontmatter-invalid-yaml" in {d.rule for d in result.diagnostics}


def test_a_fragment_at_a_deeper_path_is_named_by_its_path_from_the_documents_directory():
    result, loader = _run(_FRONT + _SCOPE + "{{include: parts/../parts/a.lgd}}\n", {"parts/a.lgd": "# H {#h}\n"})
    assert loader.asked == ["parts/a.lgd"] and _found(result) == [("include-has-h1", "parts/a.lgd", 1)]


# ── A nested include, outside a template ─────────────────────────

def test_a_nested_include_outside_a_template_is_allowed_and_not_followed():
    part = "## P {#p}\n\n{{include: nested.lgd}}\n\n{{include: notes.txt}}\n"
    result, loader = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    # Not read, so neither missing nor judged: include-cycle is not implemented (CONFORMANCE.md).
    assert loader.asked == ["part.lgd"] and _found(result) == []


# ── A template's fragments (§15.3, §16.12) ───────────────────────

_TFR = "template-fragment-invalid"


def _template(part: str, *, front: str = _TEMPLATE_FRONT, body: str = "{{include: part.lgd}} {when=x}\n"):
    return _run(front + "# A {#a}\n\n" + body, {"part.lgd": part})


@pytest.mark.parametrize(("part", "lines"), [
    ("## B {#b when=x}\n\nText.\n", [1]),                       # a condition on a section
    ("## B {#b}\n\nText. {when=x}\n", [3]),                     # on a paragraph
    ("## B {#b}\n\n- Item. {when=x}\n", [3]),                   # on a list item
    ("## B {#b}\n\n> [!DRAFTING]\n> A note.\n", [3]),           # a drafting note
    ("## B\n\nText.\n", [1]),                                   # a heading without an identifier
    ("## B {#b}\n\n{{include: g.lgd}}\n", [3]),                 # a nested include
    ("## B {#b}\n\nSee {{include: g.lgd}} inline.\n", [3]),     # ... in a sentence, every include directive
    ("## B {#b}\n\n- {{include: g.lgd}}\n", [3]),               # ... or a list item
])
def test_a_template_fragment_holds_none_of_these_each_reported_once_at_its_line(part, lines):
    result, loader = _template(part)
    assert _found(result, _TFR) == [(_TFR, "part.lgd", line) for line in lines]
    assert loader.asked == ["part.lgd"]


def test_a_template_fragment_with_several_violations_reports_each():
    part = "## B {when=x}\n\n> [!DRAFTING]\n> note\n\n## C\n\nSee {{include: g.lgd}} inline.\n\n- {{include: g.lgd}}\n\nZ. {when=x}\n"
    result, _ = _template(part)
    assert sorted(line for _rule, _file, line in _found(result, _TFR)) == [1, 1, 3, 6, 8, 10, 12]
    assert {file for _rule, file, _line in _found(result, _TFR)} == {"part.lgd"}


def test_a_fragment_that_is_fit_for_a_template_is_clean():
    part = "## B {#b}\n\nPay {{placeholder: fee}}. The text {when=x} goes on.\n\n{{choose: x, true=\"a\", false=\"b\"}}\n"
    result, _ = _template(part)
    assert _found(result) == []


def test_a_condition_in_a_fragment_that_is_not_in_a_template_is_nothing_to_report():
    part = "## B {#b when=x}\n\nText. {when=x}\n\n> [!DRAFTING]\n> note\n"
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(result) == []


def test_the_includes_a_template_makes_twice_or_in_a_note_are_the_templates_own_findings_in_the_template():
    body = "{{include: part.lgd}}\n\n{{include: part.lgd}}\n\n> [!DRAFTING]\n> {{include: gone.lgd}}\n"
    result, _ = _template("## B {#b}\n", body=body)
    text = _TEMPLATE_FRONT + "# A {#a}\n\n" + body
    assert sorted(_found(result, _TFR)) == [
        (_TFR, "main.lgd", _at(text, "part.lgd", 1)), (_TFR, "main.lgd", _at(text, "gone.lgd"))
    ]
    # ... and not read as fragments: the note's include is removed unread, the second is the same file.


def test_an_attachment_file_of_a_template_holds_no_include_and_may_hold_the_rest():
    front = _TEMPLATE_FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: ./s.lgd\n---\n\n")
    attachment = "## S\n\nText. {when=x}\n\n> [!DRAFTING]\n> note\n\n{{include: g.lgd}}\n\n- {{include: g.lgd}}\n"
    result, loader = _run(front + "# A {#a}\n\nSee {{attach: s}}.\n", {"s.lgd": attachment})
    assert loader.asked == ["s.lgd"]
    assert _found(result, _TFR) == [(_TFR, "s.lgd", 8), (_TFR, "s.lgd", 10)]
    # Its frontmatter and level 1 heading are assembly's to refuse: not reported here (CONFORMANCE.md).
    result, _ = _run(front + "# A {#a}\n\nSee {{attach: s}}.\n", {"s.lgd": "---\ntitle: T\n---\n\n# H {#h}\n"})
    assert _found(result, "attachment-has-frontmatter", "attachment-has-h1", _TFR) == []
    # Outside a template its includes are allowed.
    plain = _FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n---\n\n")
    result, _ = _run(plain + "# A {#a}\n\nSee {{attach: s}}.\n", {"s.lgd": attachment})
    assert _found(result, _TFR) == []


def test_fragment_level_checks_of_the_body_are_not_reported_by_the_document_that_includes_it():
    # insertion-boundary, drafting-note-def, def-term-variable and drafting-note-unrecognized are in the
    # fragment's own validation (validate(fragment=True)); in the template they would newly stop assembly.
    part = "## B {#b}\n\nPay {{placeholder: fee}}*.\n\n> [!NOTE]\n> n\n"
    result, _ = _template(part)
    assert result.rules() & {"insertion-boundary", "drafting-note-unrecognized", "def-term-variable"} == set()
    assert "insertion-boundary" in validate(parse(part), fragment=True, template=True).rules()


# ── include-heading-skip ─────────────────────────────────────────

def test_the_headings_of_every_fragment_are_joined_to_the_documents_in_one_pass():
    text = _FRONT + "{{include: p.lgd}}\n\n# A {#a}\n\n{{include: q.lgd}}\n\n#### Z {#z}\n"
    result, _ = _run(text, {"p.lgd": "## P {#p}\n", "q.lgd": "## Q {#q}\n\n#### R {#r}\n"})
    # p.lgd comes before the first heading, which must be at level 1; q.lgd's R skips two levels.
    # The document's own Z skips after A, but follows R in the combined document: no heading-skip.
    assert sorted(_found(result, "include-heading-skip", "heading-skip")) == [
        ("include-heading-skip", "p.lgd", 1), ("include-heading-skip", "q.lgd", 3),
    ]


def test_a_heading_of_the_document_after_a_fragment_is_reported_at_its_own_line():
    text = _FRONT + "# A {#a}\n\n## B {#b}\n\n### C {#c}\n\n{{include: p.lgd}}\n\n#### D {#d}\n"
    # C, then the fragment's Q at level 2, then D: D follows C without a skip, and Q with one.
    result, _ = _run(text, {"p.lgd": "## Q {#q}\n"})
    assert _found(result, "include-heading-skip", "heading-skip") == [
        ("include-heading-skip", "main.lgd", _at(text, "#### D"))
    ]
    ok, _ = _run(text, {"p.lgd": "### Q {#q}\n"})
    assert _found(ok, "include-heading-skip", "heading-skip") == []


def test_a_level_a_fragment_fills_is_not_skipped_in_the_combined_document():
    text = _FRONT + "# One {#one}\n\n{{include: part.lgd}}\n\n### Three {#three}\n"
    result, _ = _run(text, {"part.lgd": "## Deep {#deep}\n"})
    assert _found(result, "include-heading-skip", "heading-skip") == []
    # Judged alone, the document skips: without the fragment read, heading-skip stands.
    assert _found(validate(parse(text, filename="main.lgd")), "heading-skip") == [
        ("heading-skip", "main.lgd", _at(text, "### Three"))
    ]


def test_heading_skip_stands_when_an_include_before_the_heading_is_not_known():
    text = _FRONT + "# One {#one}\n\n{{include: part.lgd}}\n\n{{include: gone.lgd}}\n\n### Three {#three}\n"
    wanted = [("heading-skip", "main.lgd", _at(text, "### Three"))]
    # One include of the segment is missing, so what precedes Three is not known.
    result, _ = _run(text, {"part.lgd": "## Deep {#deep}\n"})
    assert _found(result, "heading-skip") == wanted
    # ... or is not a LegalDown file, or is a path judged, or cannot be asked for.
    for other in ("notes.txt", "/abs.lgd", "../out.lgd"):
        text2 = text.replace("gone.lgd", other)
        result, _ = _run(text2, {"part.lgd": "## Deep {#deep}\n"})
        assert _found(result, "heading-skip") == [("heading-skip", "main.lgd", _at(text2, "### Three"))], other
    # An include in another section does not matter.
    text3 = _FRONT + "# One {#one}\n\n{{include: part.lgd}}\n\n## Two {#two}\n\n{{include: gone.lgd}}\n\n### Three {#three}\n\n#### Four {#four}\n"
    result, _ = _run(text3, {"part.lgd": "## Deep {#deep}\n"})
    assert _found(result, "heading-skip") == []
    # A fragment that does not fill the level leaves the skip.
    result, _ = _run(_FRONT + "# One {#one}\n\n{{include: part.lgd}}\n\n#### Four {#four}\n", {"part.lgd": "## Deep {#deep}\n"})
    assert [r[0] for r in _found(result, "heading-skip", "include-heading-skip")] == ["heading-skip"]


def test_a_conditional_include_fills_no_level():
    front = _TEMPLATE_FRONT
    part = {"p.lgd": "## Deep {#deep}\n"}
    for include in ("{{include: p.lgd}} {when=x}\n", "{{include: p.lgd}}\n"):
        text = front + "# One {#one}\n\n" + include + "\n### Three {#three}\n"
        result, _ = _run(text, part)
        expected = [("heading-skip", "main.lgd", _at(text, "### Three"))] if "when" in include else []
        assert _found(result, "heading-skip") == expected, include
    # ... nor does one under a conditional section.
    text = front + "# One {#one when=x}\n\n{{include: p.lgd}}\n\n### Three {#three}\n"
    result, _ = _run(text, part)
    assert [rule for rule, *_ in _found(result, "heading-skip")] == ["heading-skip"]


def test_a_file_that_is_not_a_fragment_is_not_spliced():
    # A level 1 heading, or frontmatter, is reported (include-has-h1, include-has-frontmatter) and the file
    # is not joined to the document: it fills no level, and has no heading to skip.
    for part in ("# Deep {#deep}\n\n## Under {#under}\n", "---\ntitle: T\n---\n\n## Under {#under}\n"):
        text = _FRONT + "# One {#one}\n\n{{include: part.lgd}}\n\n### Three {#three}\n"
        result, _ = _run(text, {"part.lgd": part})
        assert [rule for rule, *_ in _found(result, "heading-skip", "include-heading-skip")] == ["heading-skip"]
        assert {rule for rule, *_ in _found(result)} & {"include-has-h1", "include-has-frontmatter"}
    text = _FRONT + "{{include: part.lgd}}\n\n## Two {#two}\n"
    result, _ = _run(text, {"part.lgd": "# One {#one}\n"})
    assert [rule for rule, *_ in _found(result, "heading-skip", "include-heading-skip")] == ["heading-skip"]


def test_a_fragment_included_twice_is_reported_once_per_heading():
    text = _FRONT + "# A {#a}\n\n{{include: p.lgd}}\n\n{{include: p.lgd}}\n"
    result, _ = _run(text, {"p.lgd": "### P {#p}\n"})
    assert _found(result, "include-heading-skip") == [("include-heading-skip", "p.lgd", 1)]


def test_an_include_in_a_drafting_note_or_a_missing_one_adds_no_headings():
    text = _FRONT + "# A {#a}\n\n> [!DRAFTING]\n> {{include: p.lgd}}\n\n{{include: gone.lgd}}\n\n## B {#b}\n"
    result, _ = _run(text, {"p.lgd": "##### P {#p}\n"})
    assert _found(result, "include-heading-skip") == []


def test_a_document_without_frontmatter_takes_the_level_of_its_first_heading():
    result = validate(parse("{{include: p.lgd}}\n\n## A {#a}\n"), resolve={"p.lgd": "### P {#p}\n"}.get)
    assert _found(result, "include-heading-skip", "heading-skip") == []


# ── The shared module: assembly, validation and fragment mode say the same ──

_ASSEMBLY_FRAGMENTS = [
    "---\ntitle: T\n---\n\n## B {#b}\n",
    "# B {#b}\n",
    "## B {#b when=x}\n\nText.\n",
    "## B\n\n> [!DRAFTING]\n> note\n",
    "## B {#b}\n\n- {{include: g.lgd}}\n",
]


@pytest.mark.parametrize("part", _ASSEMBLY_FRAGMENTS)
@pytest.mark.parametrize("kind", ["include", "attachment"])
def test_assembly_and_validation_report_a_file_with_the_same_findings(part, kind):
    files = {"part.lgd": part}
    front = _TEMPLATE_FRONT
    body = "{{include: part.lgd}}\n"
    if kind == "attachment":
        front = front.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: part.lgd\n---\n\n")
        body = "See {{attach: s}}.\n"
    template = Template(front + "# A {#a}\n\n" + body, resolve=files.get)
    from_assembly = [(d.rule, d.file, d.line) for d in template.problems if d.rule != "include-file-missing"]
    document = parse(part, on_frontmatter_error="keep")
    findings = fragment_findings(FragmentFile("part.lgd", document), kind=kind, template=True)
    assert from_assembly == [(f.rule, "part.lgd", f.line) for f in findings]
    # Each finding once in the template's problems, although the validator reports its own as well.
    assert len(set(from_assembly)) == len(from_assembly)


def test_a_rule_of_a_file_assembly_read_is_counted_once_in_the_problems_of_a_template():
    front = _TEMPLATE_FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: ./s.lgd\n---\n\n")
    template = Template(
        front + "# A {#a}\n\nSee {{attach: s}}.\n\n{{include: part.lgd}}\n",
        resolve={"s.lgd": "## S\n\n{{include: g.lgd}}\n", "part.lgd": "## B\n\nText.\n"}.get,
    )
    assert [(d.rule, d.file, d.line) for d in template.problems] == [
        (_TFR, "part.lgd", 1), (_TFR, "s.lgd", 3)
    ]
    # The validator reports the same findings in the same files: advice, which problems does not double.
    assert [(d.rule, d.file, d.line) for d in template.validation.diagnostics if d.rule == _TFR] == [
        (_TFR, "part.lgd", 1), (_TFR, "s.lgd", 3)
    ]


def test_assembly_diagnostics_carry_the_file_and_line_of_the_fragment():
    template = Template(
        _TEMPLATE_FRONT + "# A {#a}\n\n{{include: part.lgd}}\n",
        resolve={"part.lgd": "## One {#one}\n\n## Two\n\n## Three\n"}.get,
    )
    result = template.form({"x": True}).assemble()
    assert [(d.rule, d.file, d.line) for d in result.diagnostics] == [(_TFR, "part.lgd", 3), (_TFR, "part.lgd", 5)]
    assert [(d["file"], d["line"]) for d in template.form({"x": True}).as_dict()["problems"]] == [
        ("part.lgd", 3), ("part.lgd", 5)
    ]


def test_the_assemble_command_names_the_fragment_and_line(tmp_path, capsys):
    (tmp_path / "t.lgd").write_text(_TEMPLATE_FRONT + "# A {#a}\n\n{{include: part.lgd}}\n", encoding="utf-8")
    (tmp_path / "part.lgd").write_text("## One {#one}\n\n## Two\n", encoding="utf-8")
    assert main(["assemble", str(tmp_path / "t.lgd"), "-o", str(tmp_path / "out")]) == EXIT_DIAGNOSTICS
    assert f"{tmp_path / 'part.lgd'}:3: " in capsys.readouterr().err


def test_the_files_of_one_validation_are_read_and_parsed_once():
    loader = _Loader({"a.lgd": "## A {#a}\n"})
    document = parse(_FRONT + _SCOPE, filename="main.lgd")
    files = Files(loader, document)
    first = files.read("a.lgd")
    assert files.read("./a.lgd") is first and files.read("missing.lgd") is None and files.read("missing.lgd") is None
    assert files.read("../a.lgd") is None and files.read("/a.lgd") is None
    assert loader.asked == ["a.lgd", "missing.lgd"]


# ── The finding already has its file ─────────────────────────────

def test_a_finding_keeps_the_file_it_was_made_in_and_the_others_get_the_documents():
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n\nSee {{ref: nowhere}}.\n", {"part.lgd": "# H {#h}\n"})
    files = {d.rule: d.file for d in result.diagnostics}
    assert files["include-has-h1"] == "part.lgd" and files["ref-broken"] == "main.lgd"
    nameless = validate(parse(_FRONT + _SCOPE + "{{include: part.lgd}}\n"), resolve={"part.lgd": "# H {#h}\n"}.get)
    assert [(d.rule, d.file) for d in nameless.diagnostics if d.rule == "include-has-h1"] == [("include-has-h1", "part.lgd")]


def test_the_command_names_a_fragments_finding_by_its_path_beside_the_document(tmp_path, capsys):
    folder = tmp_path / "docs"
    (folder / "parts").mkdir(parents=True)
    (folder / "main.lgd").write_text(_FRONT + _SCOPE + "{{include: parts/a.lgd}}\n\n{{include: gone.lgd}}\n", encoding="utf-8")
    (folder / "parts" / "a.lgd").write_text("Body.\n\n### Deep {#deep}\n", encoding="utf-8")
    assert main(["validate", "--format", "json", str(folder / "main.lgd")]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    # By file, then line, as the command sorts them.
    assert [(d["rule"], d["file"], d["line"]) for d in records if d["rule"].startswith("include")] == [
        ("include-file-missing", str(folder / "main.lgd"), 23), ("include-heading-skip", str(folder / "parts" / "a.lgd"), 3)
    ]
    assert main(["validate", str(folder / "main.lgd")]) == EXIT_DIAGNOSTICS
    assert f"{folder / 'parts' / 'a.lgd'}:3: error: [include-heading-skip]" in capsys.readouterr().out


# ── validate(fragment=True): what the file is to its host ────────

def _own(source: str, **kwargs):
    return validate(parse(source, on_frontmatter_error="keep"), fragment=True, **kwargs)


def _rows(result):
    return [(d.rule, d.line, d.host_dependent) for d in result.diagnostics]


def test_a_fragment_is_an_include_fragment_by_default():
    source = "---\ntitle: T\n---\n\n# H {#h}\n"
    assert _rows(_own(source)) == [("include-has-frontmatter", 2, False), ("include-has-h1", 5, False)]
    assert _rows(_own(source, fragment_kind="include")) == _rows(_own(source))


def test_an_attachment_file_draws_the_ids_of_an_attachment_file():
    source = "---\ntitle: T\n---\n\n# H {#h}\n\n# G {#g}\n"
    assert _rows(_own(source, fragment_kind="attachment")) == [
        ("attachment-has-frontmatter", 2, False), ("attachment-has-h1", 5, False), ("attachment-has-h1", 7, False)
    ]
    unreadable = "---\ntitle: [x\n---\n\n## P {#p}\n"
    assert _rows(_own(unreadable, fragment_kind="attachment")) == [("attachment-has-frontmatter", 2, False)]
    assert _rows(_own("## P {#p}\n", fragment_kind="attachment")) == []


def test_the_restrictions_of_a_templates_include_fragment_are_made_when_the_host_is_said_to_be_one():
    part = "## B {when=x}\n\n> [!DRAFTING]\n> note\n\n## C\n\nSee {{include: g.lgd}} inline.\n\nZ. {when=x}\n"
    said = _own(part, template=True)
    assert [d.line for d in said.diagnostics if d.rule == _TFR] == [1, 1, 3, 6, 8, 10]
    assert all(not d.host_dependent for d in said.diagnostics if d.rule == _TFR)
    # Not said, or said not to be: not held to them (a conditional fragment is only inferred a template).
    assert _TFR not in _own(part).rules()
    assert _TFR not in _own(part, template=False).rules()


def test_an_attachment_file_of_a_template_holds_conditions_and_notes_but_no_include():
    part = "## B {when=x}\n\n> [!DRAFTING]\n> note\n\nZ. {when=x}\n\nSee {{include: g.lgd}}.\n"
    result = _own(part, template=True, fragment_kind="attachment")
    assert [(d.rule, d.line) for d in result.diagnostics if d.rule == _TFR] == [(_TFR, 8)]


def test_an_include_in_a_drafting_note_of_a_template_fragment_is_reported_once():
    result = _own("## B {#b}\n\n> [!DRAFTING]\n> See {{include: g.lgd}}\n", template=True)
    assert [(d.rule, d.line) for d in result.diagnostics] == [(_TFR, 4), (_TFR, 3)]


def test_a_fragment_on_its_own_reads_none_of_its_includes():
    loader = _Loader({"g.lgd": "# H {#h}\n"})
    result = validate(parse("## B {#b}\n\n{{include: g.lgd}}\n"), fragment=True, resolve=loader)
    assert loader.asked == [] and result.diagnostics == []


def test_fragment_kind_is_only_for_a_fragment_and_is_one_of_two():
    document = parse("## B {#b}\n")
    for kind in ("include", "attachment"):
        with pytest.raises(ValueError, match="fragment"):
            validate(document, fragment_kind=kind)
    assert validate(document, fragment_kind=None).rules() == {"frontmatter-absent"}
    for value in ("attachments", "", "Include", True):
        with pytest.raises(ValueError, match="fragment_kind"):
            validate(document, fragment=True, fragment_kind=value)  # type: ignore[arg-type]


def test_the_command_takes_the_kind_of_the_fragments(tmp_path, capsys):
    path = tmp_path / "s.lgd"
    path.write_text("# H {#h}\n", encoding="utf-8")
    assert main(["validate", "--fragment", "--fragment-kind", "attachment", str(path)]) == EXIT_DIAGNOSTICS
    assert "[attachment-has-h1]" in capsys.readouterr().out
    assert main(["validate", "--fragment", str(path)]) == EXIT_DIAGNOSTICS
    assert "[include-has-h1]" in capsys.readouterr().out
    path.write_text("## B {#b}\n\n> [!DRAFTING]\n> note\n", encoding="utf-8")
    assert main(["validate", "--fragment", "--template", "--fragment-kind", "attachment", str(path)]) == EXIT_OK
    capsys.readouterr()
    assert main(["validate", "--fragment", "--template", str(path)]) == EXIT_DIAGNOSTICS
    assert "[template-fragment-invalid]" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["validate", "--fragment-kind", "attachment", str(path)])
    assert "only valid with --fragment" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["validate", "--fragment", "--fragment-kind", "section", str(path)])


# ── The default loader and its opt-out ───────────────────────────

def test_the_loader_a_document_gets_by_default_is_the_directory_beside_it(tmp_path):
    (tmp_path / "main.lgd").write_text(_FRONT + _SCOPE + "{{include: a.lgd}}\n", encoding="utf-8")
    document = load(tmp_path / "main.lgd")
    assert _found(validate(document)) == [("include-file-missing", "main.lgd", 21)]
    (tmp_path / "a.lgd").write_text("## A {#a}\n", encoding="utf-8")
    assert _found(validate(document)) == []
    assert _found(validate(document, resolve=file_loader(tmp_path / "elsewhere"))) == [("include-file-missing", "main.lgd", 21)]


# ── Review of #35 ────────────────────────────────────────────────

@pytest.mark.parametrize("paragraph", [
    "{{include: a.lgd}}",
    "{{include: a.lgd}} {when=x}",
    "{{include: a.lgd}} {#id when=x}",
    "{{include: a.lgd}} {#id}",
    "{{include: a.lgd}} <!-- note -->",
    "{{include: a.lgd}} <!-- a --> {when=x} <!-- b -->",
    "{{include: a.lgd}} {#id class=x}",
    "{{include: a.lgd}} {when=x} {when=y}",
    "{{include: a.lgd}} {when=x} more",
    "Text {{include: a.lgd}}",
    "{{include: a.lgd}} {{include: b.lgd}}",
    "{{include: a.lgd}} `{when=x}`",
])
def test_assembly_and_the_validator_read_one_include_paragraph(paragraph):
    from legaldown.directives import lex
    from legaldown.validator.units import include_paragraph

    text = _TEMPLATE_FRONT + "# A {#a}\n\n" + paragraph + "\n"
    read_by_assembly = [include.path for include in Template(text, resolve=lambda _path: None)._t.main.includes]
    directive = include_paragraph(paragraph, lex(paragraph))
    assert read_by_assembly == ([] if directive is None else [directive.positional])
    result, loader = _run(text, {"a.lgd": "## A {#a1}\n", "b.lgd": "## B {#b1}\n"})
    assert loader.asked == read_by_assembly  # what is read is what assembly reads


def test_a_message_says_an_attachment_file_not_a_attachment_file():
    from legaldown.validator.fragments import noun

    assert [noun("include", "a"), noun("attachment", "a")] == ["a fragment", "an attachment file"]
    assert [noun("include", "the"), noun("attachment", "the"), noun("include"), noun("attachment")] == [
        "the fragment", "the attachment file", "fragment", "attachment file"
    ]
    source = "---\ntitle: T\n---\n\n# H {#h}\n"
    own = validate(parse(source), fragment=True, fragment_kind="attachment")
    assert all("a attachment" not in d.message for d in own.diagnostics)
    assert [d.message for d in own.diagnostics if d.rule == "attachment-has-h1"][0].count("which an attachment file cannot have") == 1
    included = validate(parse(source), fragment=True)
    assert "which a fragment cannot have" in included.diagnostics[0].message
    front = _TEMPLATE_FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n---\n\n")
    problems = Template(front + "# A {#a}\n\nSee {{attach: s}}.\n", resolve={"s.lgd": source}.get).problems
    assert problems and all("a attachment" not in d.message for d in problems)
    assert any(d.message.startswith("The attachment file ") and d.file == "s.lgd" for d in problems)


def test_assembly_refuses_a_nested_include_of_a_plain_document_at_its_line_in_the_fragment():
    template = Template(
        _FRONT + _SCOPE + "{{include: part.lgd}}\n",
        resolve={"part.lgd": "## P {#p}\n\n{{include: g.lgd}}\n\n> [!DRAFTING]\n> {{include: h.lgd}}\n\n{{include: i.lgd}}\n"}.get,
    )
    assert [(d.rule, d.file, d.line) for d in template.problems] == [
        ("include-file-missing", "part.lgd", 3), ("include-file-missing", "part.lgd", 8)
    ]
    assert "g.lgd" in template.problems[0].message


def test_a_fragment_assembly_cannot_parse_is_counted_once_in_the_problems_of_a_template():
    part = "---\ntitle: [unclosed\n---\n\n## No identifier\n"
    template = Template(_TEMPLATE_FRONT + "# A {#a}\n\n{{include: part.lgd}}\n", resolve={"part.lgd": part}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("frontmatter-invalid-yaml", "part.lgd", 2)]
    # The validator, whose advice it is, says what else is wrong with the file.
    assert {(d.rule, d.file) for d in template.validation.diagnostics if d.file} == {
        ("include-has-frontmatter", "part.lgd"), (_TFR, "part.lgd")
    }


def test_a_fragment_that_several_documents_include_is_found_wrong_once_in_the_output(tmp_path, capsys):
    for name in ("one.lgd", "two.lgd"):
        (tmp_path / name).write_text(_FRONT + _SCOPE + "{{include: part.lgd}}\n", encoding="utf-8")
    (tmp_path / "part.lgd").write_text("# H {#h}\n", encoding="utf-8")
    one, two = str(tmp_path / "one.lgd"), str(tmp_path / "two.lgd")
    assert main(["validate", "--format", "json", one, two]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["rule"], d["file"]) for d in records if d["rule"] == "include-has-h1"] == [
        ("include-has-h1", str(tmp_path / "part.lgd"))
    ]
    # ... and in the counts of the text output.
    assert main(["validate", one, two]) == EXIT_DIAGNOSTICS
    captured = capsys.readouterr()
    assert captured.out.count("[include-has-h1]") == 1 and "1 error(s)" in captured.err


def test_the_findings_of_one_document_are_never_collapsed(tmp_path, capsys):
    text = _FRONT + _SCOPE + "See {{ref: nowhere}} and {{ref: nowhere}}.\n\n{{include: gone.lgd}}\n"
    (tmp_path / "doc.lgd").write_text(text, encoding="utf-8")
    doc = str(tmp_path / "doc.lgd")
    assert main(["validate", "--format", "json", doc]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [d["rule"] for d in records].count("ref-broken") == 2
    # The same document named twice is one target: validated and reported once.
    assert main(["validate", "--format", "json", doc, doc]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [d["rule"] for d in records].count("ref-broken") == 2 and [d["rule"] for d in records].count("include-file-missing") == 1


def test_assembly_refuses_only_an_include_paragraph_of_a_plain_documents_fragment():
    part = "## P {#p}\n\nSee {{include: a.lgd}} inline.\n\n- {{include: b.lgd}}\n\n> {{include: c.lgd}}\n\n{{include: d.lgd}} {when=x}\n"
    template = Template(_FRONT + _SCOPE + "{{include: part.lgd}}\n", resolve={"part.lgd": part}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("include-file-missing", "part.lgd", 9)]
    # The directive is quoted as written, once.
    message = template.problems[0].message
    assert "'{{include: d.lgd}}'" in message and message.count("{{include:") == 1


def test_a_nested_include_is_quoted_as_written_in_a_templates_finding_too():
    result, _ = _template("## B {#b}\n\nSee {{include:   g.lgd }} inline.\n")
    message = [d.message for d in result.diagnostics if d.rule == _TFR][0]
    assert "'{{include:   g.lgd }}'" in message and message.count("include") == 2


# ── A root, a path the loader is not asked for, and what is not a fragment (#35 review) ─────

def test_a_path_that_leads_out_of_the_directory_is_skipped_unless_it_leaves_the_root(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    path = root / "sub" / "main.lgd"

    def found(include: str, document_root):
        path.write_text(_FRONT + _SCOPE + f"{{{{include: {include}}}}}\n", encoding="utf-8")
        return _found(validate(load(path), document_root=document_root))

    # A backslash is no separator (§2.3): not a path to read, with a root or without one.
    assert found("parts\\x.lgd", root) == [("include-file-missing", "main.lgd", 21)]
    assert found("parts\\x.lgd", None) == [("include-file-missing", "main.lgd", 21)]
    # ../ that stays within the root is a valid path this implementation does not read: nothing to say.
    assert found("../x.lgd", root) == []
    assert found("sub/../../x.lgd", root) == []
    # Without a root it is no less valid (§2.3): nothing to say either (#109); out of the root it is #104's.
    assert found("../x.lgd", None) == []
    assert found("sub/../../x.lgd", None) == []
    assert found("../../x.lgd", root) == [("path-outside-root", "main.lgd", 21)]
    # A backslash after a ../ is still a backslash.
    assert found("..\\x.lgd", root) == [("include-file-missing", "main.lgd", 21)]


def test_a_block_with_no_text_is_no_include_paragraph():
    from legaldown.models import Block
    from legaldown.validator.fragments import nested_includes

    # An empty paragraph built in code has no text fragment at all: it holds no include, and is no error.
    document = parse(_FRONT + "# A {#a}\n\n{{include: g.lgd}}\n")
    section = document.sections[0]
    document = dataclasses.replace(document, sections=[
        dataclasses.replace(section, blocks=[Block(kind="paragraph", text=""), *section.blocks])
    ])
    assert [source for source, _line in nested_includes(FragmentFile("part.lgd", document))] == ["{{include: g.lgd}}"]
    result = validate(document, resolve=_Loader({}))
    # Only the include that is there is judged: it is missing; the empty paragraph draws nothing.
    assert [d.rule for d in result.diagnostics if d.rule.startswith("include-")] == ["include-file-missing"]


def test_assembly_refuses_an_include_paragraph_that_names_no_path_in_a_plain_documents_fragment():
    part = "## P {#p}\n\n{{include:}}\n\nSee {{include:}} inline.\n"
    template = Template(_FRONT + _SCOPE + "{{include: part.lgd}}\n", resolve={"part.lgd": part}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("include-file-missing", "part.lgd", 3)]
    from legaldown.validator.fragments import nested_includes

    assert [source for source, _ in nested_includes(FragmentFile("part.lgd", parse(part)))] == ["{{include:}}"]


def test_a_file_is_spliceable_when_it_has_neither_frontmatter_nor_a_level_one_heading():
    def part(text: str) -> FragmentFile:
        return FragmentFile("part.lgd", parse(text, on_frontmatter_error="keep"))

    assert part("## A {#a}\n").spliceable
    assert not part("# A {#a}\n").spliceable
    assert not part("---\ntitle: T\n---\n\n## A {#a}\n").spliceable
    assert not part("---\ntitle: [unclosed\n---\n\n## A {#a}\n").spliceable
    # The flag and the findings are one test.
    for text in ("## A {#a}\n", "# A {#a}\n", "---\ntitle: T\n---\n"):
        found = fragment_findings(part(text), kind="include", template=False)
        assert part(text).spliceable == (not found)


# ── The output of one run over several documents (#35 review) ─────────────────

def _diagnostic(file: str, rule: str = "include-has-h1", line: int = 1, message: str = "m") -> dict:
    return {"file": file, "rule": rule, "level": "error", "line": line, "message": message, "host_dependent": False}


def _run_cli(monkeypatch, capsys, by_target: dict[str, list[dict]], *targets: str) -> list[tuple[str, str]]:
    """The (file, rule) of each finding printed for *targets* when validating each yields *by_target*."""
    monkeypatch.setattr("legaldown.cli._validate_path", lambda path, **_kwargs: (by_target[str(path)], None))
    main(["validate", "--format", "json", *targets])
    return [(d["file"], d["rule"]) for d in json.loads(capsys.readouterr().out)["diagnostics"]]


@pytest.mark.parametrize("order", [("a.lgd", "part.lgd"), ("part.lgd", "a.lgd")])
def test_a_fragment_that_is_also_a_target_is_reported_once_whatever_the_order(monkeypatch, capsys, order):
    by_target = {
        "a.lgd": [_diagnostic("a.lgd", "ref-broken"), _diagnostic("part.lgd")],
        "part.lgd": [_diagnostic("part.lgd")],
    }
    shown = _run_cli(monkeypatch, capsys, by_target, *order)
    assert sorted(shown) == [("a.lgd", "ref-broken"), ("part.lgd", "include-has-h1")]


def test_a_fragment_found_wrong_twice_in_one_target_is_reported_once(monkeypatch, capsys):
    by_target = {"a.lgd": [_diagnostic("part.lgd"), _diagnostic("part.lgd"), _diagnostic("part.lgd", line=2)]}
    assert _run_cli(monkeypatch, capsys, by_target, "a.lgd") == [("part.lgd", "include-has-h1")] * 2


def test_the_findings_a_target_has_in_itself_are_never_collapsed_against_each_other(monkeypatch, capsys):
    by_target = {
        "a.lgd": [_diagnostic("a.lgd", "ref-broken"), _diagnostic("a.lgd", "ref-broken")],
        "b.lgd": [_diagnostic("a.lgd", "ref-broken")],  # a finding in a file b brought: a's own already say it
    }
    assert _run_cli(monkeypatch, capsys, by_target, "b.lgd", "a.lgd") == [("a.lgd", "ref-broken")] * 2


def test_a_fragment_included_through_two_spellings_is_found_wrong_once(tmp_path, capsys):
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{include: ./sub/../part.lgd}}\n"
    (tmp_path / "a.lgd").write_text(text, encoding="utf-8")
    (tmp_path / "part.lgd").write_text("# H {#h}\n", encoding="utf-8")
    assert main(["validate", "--format", "json", str(tmp_path / "a.lgd")]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [d["rule"] for d in records].count("include-has-h1") == 1


def test_a_fragment_reached_from_two_directories_is_found_wrong_once(tmp_path, capsys):
    # The message names the fragment from each including document's directory, and differs.
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.lgd").write_text(_FRONT + _SCOPE + "{{include: sub/p.lgd}}\n", encoding="utf-8")
    (tmp_path / "sub" / "b.lgd").write_text(_FRONT + _SCOPE + "{{include: p.lgd}}\n", encoding="utf-8")
    (tmp_path / "sub" / "p.lgd").write_text("# H {#h}\n", encoding="utf-8")
    for order in ((tmp_path / "a.lgd", tmp_path / "sub" / "b.lgd"), (tmp_path / "sub" / "b.lgd", tmp_path / "a.lgd")):
        assert main(["validate", "--format", "json", *map(str, order)]) == EXIT_DIAGNOSTICS
        records = json.loads(capsys.readouterr().out)["diagnostics"]
        assert [d["file"] for d in records if d["rule"] == "include-has-h1"] == [str(tmp_path / "sub" / "p.lgd")]


def test_a_file_reached_twice_as_a_target_is_validated_and_reported_once(tmp_path, capsys):
    (tmp_path / "a.lgd").write_text(_FRONT + _SCOPE + "{{include: gone.lgd}}\n\nSee {{ref: nowhere}}.\n", encoding="utf-8")
    spellings = [
        [str(tmp_path), str(tmp_path / "a.lgd")],  # a directory, then a file in it
        [str(tmp_path / "a.lgd"), str(tmp_path)],
        [str(tmp_path / "a.lgd"), str(tmp_path / "." / "a.lgd")],
    ]
    for paths in spellings:
        assert main(["validate", "--format", "json", *paths]) == EXIT_DIAGNOSTICS
        rules = [d["rule"] for d in json.loads(capsys.readouterr().out)["diagnostics"]]
        assert rules.count("include-file-missing") == 1 and rules.count("ref-broken") == 1


def test_relative_path_problem_says_why_a_path_is_refused():
    from legaldown.files import relative_path, relative_path_problem

    cases = {
        "a/b.lgd": None, "./a.lgd": None, "a/../b.lgd": None,
        "../a.lgd": "leads-out", "a/../../b.lgd": "leads-out", "..": "leads-out",
        "": "invalid", ".": "invalid", "a\\b.lgd": "invalid", "a\0b": "invalid", "/a.lgd": "invalid",
        "C:/a.lgd": "invalid", "https://x/a.lgd": "invalid", "..\\a.lgd": "invalid",
    }
    assert {path: relative_path_problem(path) for path in cases} == cases
    assert all((relative_path(path) is None) == (problem is not None) for path, problem in cases.items())


# ── An include paragraph that names no file is judged the same everywhere (#35 review) ─────

def test_an_include_paragraph_that_names_no_file_is_missing_in_validate_and_in_assembly():
    body = "# A {#a}\n\nIntro.\n\n{{include:}}\n"
    for front in (_FRONT, _TEMPLATE_FRONT):
        text = front + body
        line = _at(text, "{{include:}}")
        result, loader = _run(text)
        assert loader.asked == []  # no file to ask for
        assert _found(result) == [("include-file-missing", "main.lgd", line)]
        assert "names no file" in result.diagnostics[-1].message
        # ... and says nothing of it when nothing is read (parse without a path / resolver).
        assert _found(validate(parse(text, filename="main.lgd"))) == []
    # Assembly refuses the template for it, at the same line, saying the same.
    problems = Template(_TEMPLATE_FRONT + body, resolve=lambda _path: None).problems
    assert [(d.rule, d.file, d.line) for d in problems] == [("include-file-missing", "", _at(_TEMPLATE_FRONT + body, "{{include:}}"))]
    assert "names no file" in problems[0].message and "'.'" not in problems[0].message
    # ... quoting the directive as written, as the validator does.
    written = Template(_TEMPLATE_FRONT + "# A {#a}\n\n{{include:  }} {when=x}\n", resolve=lambda _path: None).problems
    assert "'{{include:  }}' names no file" in written[0].message


def test_an_include_paragraph_that_names_no_file_in_a_plain_fragment_is_refused_by_assembly_alone():
    part = "## P {#p}\n\nText.\n\n{{include:}}\n"
    # Nested includes are allowed outside a template and not followed (§12.2): validate says nothing.
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(result) == []
    template = Template(_FRONT + _SCOPE + "{{include: part.lgd}}\n", resolve={"part.lgd": part}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("include-file-missing", "part.lgd", 5)]


def test_an_include_that_names_no_file_in_a_templates_fragment_is_a_nested_include():
    part = "## P {#p}\n\nText.\n\n{{include:}}\n\nSee {{include:}} inline.\n"
    main = _TEMPLATE_FRONT + "# A {#a}\n\n{{include: part.lgd}}\n"
    result, _ = _run(main, {"part.lgd": part})
    expected = [(_TFR, "part.lgd", 5), (_TFR, "part.lgd", 7)]
    assert [(d.rule, d.file, d.line) for d in result.diagnostics if d.file] == expected
    template = Template(main, resolve={"part.lgd": part}.get)
    assert [(d.rule, d.file, d.line) for d in template.problems] == expected
    assert all("holds '{{include:}}'" in d.message for d in template.problems)


def test_a_link_to_a_document_is_a_target_of_its_own_that_reads_its_includes_from_beside_the_link(tmp_path, capsys):
    # load() does not follow links: a link's includes are the ones beside the link.
    (tmp_path / "other").mkdir()
    (tmp_path / "a.lgd").write_text(_FRONT + _SCOPE + "{{include: part.lgd}}\n", encoding="utf-8")
    (tmp_path / "part.lgd").write_text("# H {#h}\n", encoding="utf-8")
    link = tmp_path / "other" / "link.lgd"
    try:
        link.symlink_to(tmp_path / "a.lgd")
    except OSError:  # no links here
        pytest.skip("cannot make a symbolic link")
    assert main(["validate", "--format", "json", str(tmp_path / "a.lgd"), str(link)]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["rule"], d["file"]) for d in records if d["rule"].startswith("include-")] == [
        ("include-file-missing", str(link)),
        ("include-has-h1", str(tmp_path / "part.lgd")),
    ]
    # The fragment's message does not name it by a path.
    assert all("part.lgd" not in d["message"] for d in records if d["rule"] == "include-has-h1")


def test_a_finding_that_carries_the_including_documents_context_is_reported_for_each(tmp_path, capsys):
    # The same fragment heading skips from level 1 in one document and from level 2 in the other:
    # two diagnoses, at one file, line and rule.
    (tmp_path / "a.lgd").write_text(_FRONT + "# One {#one}\n\n{{include: part.lgd}}\n", encoding="utf-8")
    (tmp_path / "b.lgd").write_text(_FRONT + "# One {#one}\n\n## Two {#two}\n\n{{include: part.lgd}}\n", encoding="utf-8")
    (tmp_path / "part.lgd").write_text("#### Deep {#deep}\n", encoding="utf-8")
    for order in (("a.lgd", "b.lgd"), ("b.lgd", "a.lgd")):
        assert main(["validate", "--format", "json", *(str(tmp_path / name) for name in order)]) == EXIT_DIAGNOSTICS
        records = json.loads(capsys.readouterr().out)["diagnostics"]
        skips = [d["message"] for d in records if d["rule"] == "include-heading-skip"]
        assert len(skips) == 2 and any("from level 1 to 4" in m for m in skips) and any("from level 2 to 4" in m for m in skips)


@pytest.mark.parametrize("include", ["{{include: g.lgd}}", "{{include:}}"])
def test_an_include_in_a_drafting_note_is_a_drafting_note_whether_or_not_it_names_a_file(include):
    note = f"> [!DRAFTING]\n> remember {include}\n"
    files = {"g.lgd": "## G {#g}\n"}
    for front, in_template in ((_TEMPLATE_FRONT, True), (_FRONT, False)):
        # In the body: template-fragment-invalid (§15.3) in a template, in both spellings; in a
        # plain document, nothing in both. A note is removed unread (§15.6).
        text = front + "# A {#a}\n\n" + note
        result, loader = _run(text, files)
        problems = Template(text, resolve=files.get).problems
        found = [(d.rule, d.file, d.line) for d in result.diagnostics if d.rule in (_TFR, "include-file-missing")]
        assert loader.asked == []
        if in_template:
            line = _at(text, include)
            assert found == [(_TFR, "main.lgd", line)]
            assert [(d.rule, d.line) for d in problems] == [(_TFR, line)]
        else:
            assert found == [] and not problems
        # In a fragment, the same.
        part = {**files, "part.lgd": "## P {#p}\n\n" + note}
        text = front + "# A {#a}\n\n{{include: part.lgd}}\n"
        result, _ = _run(text, part)
        found = [(d.rule, d.file, d.line) for d in result.diagnostics if d.file]
        assert found == [(d.rule, d.file, d.line) for d in Template(text, resolve=part.get).problems]
        assert bool(found) == in_template


# ── The same file is one file (#35 review) ─────────────────────────────────────

def _link(link, target, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:  # no links here
        pytest.skip("cannot make a symbolic link")


def test_a_fragment_reached_through_a_linked_directory_is_one_file(tmp_path, capsys):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "p.lgd").write_text("# H {#h}\n", encoding="utf-8")
    _link(tmp_path / "linked", tmp_path / "real", directory=True)
    (tmp_path / "a.lgd").write_text(_FRONT + _SCOPE + "{{include: real/p.lgd}}\n", encoding="utf-8")
    (tmp_path / "b.lgd").write_text(_FRONT + _SCOPE + "{{include: linked/p.lgd}}\n", encoding="utf-8")
    assert main(["validate", "--format", "json", str(tmp_path / "a.lgd"), str(tmp_path / "b.lgd")]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [d["rule"] for d in records].count("include-has-h1") == 1


def test_a_link_beside_its_document_is_the_same_target(tmp_path, capsys):
    (tmp_path / "a.lgd").write_text(_FRONT + _SCOPE + "{{include: gone.lgd}}\n", encoding="utf-8")
    _link(tmp_path / "alias.lgd", tmp_path / "a.lgd")
    for order in (("a.lgd", "alias.lgd"), ("alias.lgd", "a.lgd")):
        assert main(["validate", "--format", "json", *(str(tmp_path / name) for name in order)]) == EXIT_DIAGNOSTICS
        records = json.loads(capsys.readouterr().out)["diagnostics"]
        assert [d["rule"] for d in records].count("include-file-missing") == 1


def test_a_case_variant_of_a_fragment_is_one_file_on_a_file_system_that_ignores_case(tmp_path, capsys):
    (tmp_path / "Probe").write_text("", encoding="utf-8")
    if not (tmp_path / "probe").exists():
        pytest.skip("this file system tells cases apart")
    (tmp_path / "Part.lgd").write_text("# H {#h}\n", encoding="utf-8")
    (tmp_path / "a.lgd").write_text(_FRONT + _SCOPE + "{{include: Part.lgd}}\n", encoding="utf-8")
    (tmp_path / "b.lgd").write_text(_FRONT + _SCOPE + "{{include: part.lgd}}\n", encoding="utf-8")
    assert main(["validate", "--format", "json", str(tmp_path / "a.lgd"), str(tmp_path / "b.lgd")]) == EXIT_DIAGNOSTICS
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [d["rule"] for d in records].count("include-has-h1") == 1


def test_a_file_identity_is_the_inode_where_there_is_one_and_the_path_where_not(tmp_path):
    from legaldown.cli import file_identity

    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.lgd").write_text("", encoding="utf-8")
    assert file_identity(tmp_path / "x" / ".." / "x" / "a.lgd") == file_identity(tmp_path / "x" / "a.lgd")
    assert file_identity(tmp_path / "x" / "a.lgd") != file_identity(tmp_path / "x")
    gone = file_identity(tmp_path / "x" / ".." / "gone.lgd")
    assert gone == file_identity(tmp_path / "gone.lgd") and gone[0] == "path"
    assert file_identity("a\0b")[0] == "path"  # no path at all


def test_a_file_system_without_inodes_tells_files_apart_by_their_path(tmp_path, monkeypatch):
    """Where every file reports inode 0, identity falls back to the real path, so two files are
    never taken for one (and no target is skipped)."""
    import os as os_module

    from legaldown import cli

    (tmp_path / "a.lgd").write_text("a")
    (tmp_path / "b.lgd").write_text("b")
    real_stat = os_module.stat

    def no_inodes(path, *args, **kwargs):
        status = real_stat(path, *args, **kwargs)
        return os_module.stat_result((status.st_mode, 0, *tuple(status)[2:]))

    monkeypatch.setattr(cli.os, "stat", no_inodes)
    assert cli.file_identity(tmp_path / "a.lgd") != cli.file_identity(tmp_path / "b.lgd")
    assert cli.file_identity(tmp_path / "a.lgd") == cli.file_identity(tmp_path / "x" / ".." / "a.lgd")
