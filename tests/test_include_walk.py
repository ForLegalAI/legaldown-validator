"""Nested includes, ``include-cycle`` and ``include-anchor-duplicate`` (§12.2, §16.11, #114).

A fragment may itself include another (outside a template); ``validate`` follows the includes
the way the document's are judged, from the including fragment's directory, at their lines in it;
a chain that comes back is ``include-cycle`` at the include that closes it; a section identifier the
combined document declares twice is ``include-anchor-duplicate`` at the later declaration; the headings
of every fragment, nested ones after the heading of the section that includes them, are what
``include-heading-skip`` judges. The walk is bounded in depth and in size."""
from __future__ import annotations

import pytest

from legaldown import Template, load, parse, validate
from legaldown.grammar import SKIPPED_RULE
from legaldown.validator.fragments import MAX_INCLUDE_DEPTH, MAX_INCLUDE_EXPANDED

_FRONT = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    label: Provider\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    label: Client\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)
_TEMPLATE_FRONT = _FRONT.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n")
_SCOPE = "# Scope {#scope}\n\n"
_RULES = {
    "include-cycle", "include-anchor-duplicate", "include-file-missing", "include-not-legaldown",
    "include-has-frontmatter", "include-has-h1", "include-heading-skip", "heading-skip",
    "path-not-relative", "path-outside-root", "template-fragment-invalid", "anchor-duplicate",
}


class _Loader:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.asked: list[str] = []

    def __call__(self, path: str) -> str | None:
        self.asked.append(path)
        return self.files.get(path)


def _run(main_text: str, files: dict[str, str], **kwargs):
    loader = _Loader(files)
    result = validate(parse(main_text, filename="main.lgd"), resolve=loader, **kwargs)
    return result, loader


def _found(result, *rules: str):
    wanted = set(rules) or _RULES
    return [(d.rule, d.file, d.line) for d in result.diagnostics if d.rule in wanted]


def _warning(result):
    (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
    return warning


# ── include-cycle ────────────────────────────────────────────────────────────

def test_a_chain_that_comes_back_is_a_cycle_at_the_include_that_closes_it():
    # The corpus's case: main to a to b to a, at the include in b.
    result, loader = _run(
        _FRONT + _SCOPE + "{{include: part-a.lgd}}\n",
        {"part-a.lgd": "Fragment A.\n\n{{include: part-b.lgd}}\n", "part-b.lgd": "Fragment B.\n\n{{include: part-a.lgd}}\n"},
    )
    assert _found(result) == [("include-cycle", "part-b.lgd", 3)]
    assert loader.asked == ["part-a.lgd", "part-b.lgd"]
    message = next(d.message for d in result.diagnostics if d.rule == "include-cycle")
    assert "the document -> part-a.lgd -> part-b.lgd -> part-a.lgd" in message


def test_a_cycle_through_three_fragments_is_one_finding_however_often_it_is_reached():
    files = {
        "a.lgd": "{{include: b.lgd}}\n", "b.lgd": "{{include: c.lgd}}\n", "c.lgd": "{{include: a.lgd}}\n",
    }
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: b.lgd}}\n", files)
    # Reached from the document twice, and closed at c in both chains: reported at the one include.
    assert _found(result) == [("include-cycle", "c.lgd", 1)]


def test_a_fragment_that_includes_itself_is_a_cycle():
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", {"a.lgd": "Text.\n\n{{include: a.lgd}}\n"})
    assert _found(result) == [("include-cycle", "a.lgd", 3)]


def test_a_document_that_includes_itself_is_a_cycle(tmp_path):
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: main.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path))) == [("include-cycle", "main.lgd", 21)]
    # A document with no path is known by its filename: the file of that name beside it.
    loader = _Loader({"main.lgd": "Not read.\n"})
    named = validate(parse(path.read_text(encoding="utf-8"), filename="main.lgd"), resolve=loader)
    assert _found(named) == [("include-cycle", "main.lgd", 21)] and loader.asked == []
    # ... and a spelling that names the same file is the same file.
    path.write_text(_FRONT + _SCOPE + "{{include: ./sub/../main.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path))) == [("include-cycle", "main.lgd", 21)]


def test_a_fragment_that_includes_the_document_is_a_cycle(tmp_path):
    (tmp_path / "part.lgd").write_text("Text.\n\n{{include: main.lgd}}\n", encoding="utf-8")
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: part.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path))) == [("include-cycle", "part.lgd", 3)]
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": "Text.\n\n{{include: main.lgd}}\n"})
    assert _found(result) == [("include-cycle", "part.lgd", 3)]


def test_a_template_that_includes_itself_is_a_cycle(tmp_path):
    path = tmp_path / "t.lgd"
    path.write_text(_TEMPLATE_FRONT + _SCOPE + "{{include: t.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path))) == [("include-cycle", "t.lgd", 24)]


def test_a_fragment_included_from_two_places_is_not_a_cycle():
    files = {
        "a.lgd": "## A {#a}\n\n{{include: c.lgd}}\n", "b.lgd": "## B {#b}\n\n{{include: c.lgd}}\n",
        "c.lgd": "### C {#c-sub}\n",
    }
    result, loader = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: b.lgd}}\n", files)
    # c is included twice, and so is its identifier: once, at its heading.
    assert _found(result) == [("include-anchor-duplicate", "c.lgd", 1)]
    assert loader.asked == ["a.lgd", "c.lgd", "b.lgd"]  # each file asked for once


def test_a_diamond_with_no_headings_is_no_finding():
    files = {"a.lgd": "{{include: c.lgd}}\n", "b.lgd": "{{include: c.lgd}}\n", "c.lgd": "Text.\n"}
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: b.lgd}}\n", files)
    assert _found(result) == []


# ── Where a nested include is read from, and how it is judged ───────────────

def test_a_nested_path_is_relative_to_the_fragment_that_holds_it():
    files = {
        "parts/a.lgd": "## A {#a}\n\n{{include: b.lgd}}\n",
        "parts/b.lgd": "### B {#b}\n\n{{include: ./sub/../c.lgd}}\n",
        "parts/c.lgd": "#### C {#c}\n",
    }
    result, loader = _run(_FRONT + _SCOPE + "{{include: parts/a.lgd}}\n", files)
    assert _found(result) == [] and loader.asked == ["parts/a.lgd", "parts/b.lgd", "parts/c.lgd"]
    # A path that climbs out of the fragment's directory into the document's is read; one that is the same
    # file as another spelling is the same file.
    files = {"parts/a.lgd": "{{include: ../top.lgd}}\n", "top.lgd": "Text.\n"}
    result, loader = _run(_FRONT + _SCOPE + "{{include: parts/a.lgd}}\n", files)
    assert _found(result) == [] and loader.asked == ["parts/a.lgd", "top.lgd"]


def test_a_nested_path_that_leads_out_of_the_documents_directory_is_read_only_within_a_root(tmp_path):
    root = tmp_path / "root"
    (root / "doc" / "parts").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "shared" / "x.lgd").write_text("## X {#x}\n\n### Deep {#deep}\n", encoding="utf-8")
    (root / "doc" / "parts" / "a.lgd").write_text(
        "## A {#a}\n\n{{include: ../../shared/x.lgd}}\n\n{{include: ../../../out.lgd}}\n", encoding="utf-8"
    )
    path = root / "doc" / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: parts/a.lgd}}\n", encoding="utf-8")
    document = load(path)
    rooted = validate(document, document_root=root)
    # ../../shared/x.lgd from parts/ is ../shared/x.lgd from the document: within the root, so read; the other leaves it.
    assert _found(rooted) == [("path-outside-root", "parts/a.lgd", 5)]
    assert "not read without a document root" not in _warning(rooted).message
    # Without a root it is valid and not read: no finding, and the Warning says so.
    plain = validate(document)
    assert _found(plain) == []
    assert "2 includes in the fragments they take in" in _warning(plain).message
    assert "2 includes not read (2 lead out of the document's directory, which are not read without a document root)" in _warning(plain).message


def test_a_nested_include_is_judged_like_the_documents_in_the_file_it_is_in():
    part = (
        "## P {#p}\n\n{{include: gone.lgd}}\n\n{{include: notes.txt}}\n\n{{include: /abs.lgd}}\n\n"
        "{{include: https://x/y.lgd}}\n\n{{include: a\\b.lgd}}\n\n{{include:}}\n"
    )
    result, loader = _run(_FRONT + _SCOPE + "{{include: parts/part.lgd}}\n", {"parts/part.lgd": part})
    assert loader.asked == ["parts/part.lgd", "parts/gone.lgd"]
    assert _found(result) == [
        ("include-file-missing", "parts/part.lgd", 3),
        ("include-not-legaldown", "parts/part.lgd", 5),
        ("path-not-relative", "parts/part.lgd", 7),
        ("path-not-relative", "parts/part.lgd", 9),
        ("include-file-missing", "parts/part.lgd", 11),
        ("include-file-missing", "parts/part.lgd", 13),
    ]
    warning = _warning(result)
    # A path already an Error, and a file that is not LegalDown, are not "not read"; a missing file and a backslash are.
    assert "3 includes not read (3 could not be read (include-file-missing))" in warning.message
    assert "6 includes in the fragments they take in" in warning.message


def test_a_nested_fragment_with_frontmatter_or_a_heading_is_reported_and_not_spliced():
    files = {
        "a.lgd": "{{include: b.lgd}}\n\n{{include: c.lgd}}\n",
        "b.lgd": "---\ntitle: No\n---\n\nText.\n",
        "c.lgd": "# One {#one}\n\nText.\n",
    }
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", files)
    assert _found(result) == [("include-has-frontmatter", "b.lgd", 2), ("include-has-h1", "c.lgd", 1)]


def test_a_skip_made_by_a_nested_fragment_is_named_in_its_file_and_order_is_the_combined_documents():
    files = {"a.lgd": "## A {#a}\n\n{{include: b.lgd}}\n", "b.lgd": "#### Deep {#deep}\n"}
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", files)
    assert _found(result) == [("include-heading-skip", "b.lgd", 1)]
    # The nested fragment's headings come after the heading of the section that includes it, before the next one.
    files = {"a.lgd": "## A {#a}\n\n{{include: b.lgd}}\n\n#### Four {#four}\n", "b.lgd": "### Three {#three}\n"}
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", files)
    assert _found(result) == []
    # ... and one in the preamble of a fragment comes before its first heading.
    files = {"a.lgd": "{{include: b.lgd}}\n\n### Three {#three}\n", "b.lgd": "## Two {#two}\n"}
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", files)
    assert _found(result) == []


def test_a_level_a_nested_fragment_fills_is_not_a_heading_skip_of_the_document():
    text = _FRONT + "# One {#one}\n\n{{include: a.lgd}}\n\n### Three {#three}\n"
    result, _ = _run(text, {"a.lgd": "Intro.\n\n{{include: b.lgd}}\n", "b.lgd": "## Deep {#deep}\n"})
    assert _found(result) == []
    # Not when something the fragment includes is not known: the skip stands.
    result, _ = _run(text, {"a.lgd": "Intro.\n\n{{include: gone.lgd}}\n"})
    assert sorted(_found(result)) == [("heading-skip", "main.lgd", 23), ("include-file-missing", "a.lgd", 3)]


def test_a_templates_nested_include_is_template_fragment_invalid_and_not_followed():
    main = _TEMPLATE_FRONT + "# A {#a}\n\n{{include: part.lgd}} {when=x}\n"
    result, loader = _run(main, {"part.lgd": "## P {#p}\n\n{{include: gone.lgd}}\n"})
    assert loader.asked == ["part.lgd"]
    assert _found(result) == [("template-fragment-invalid", "part.lgd", 3)]


def test_assembly_still_refuses_a_nested_include_of_a_plain_document():
    template = Template(
        _FRONT + _SCOPE + "{{include: part.lgd}}\n", resolve={"part.lgd": "## P {#p}\n\n{{include: g.lgd}}\n", "g.lgd": "Text.\n"}.get
    )
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("include-file-missing", "part.lgd", 3)]
    assert "not assembled" in template.problems[0].message


# ── include-anchor-duplicate ─────────────────────────────────────────────────

def test_an_identifier_a_fragment_declares_again_is_reported_at_the_later_declaration():
    # The corpus's case.
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": "Fragment body.\n\n## Scope {#scope}\n\nText.\n"})
    assert _found(result) == [("include-anchor-duplicate", "part.lgd", 3)]
    # The later is the document's when the fragment comes first.
    text = _FRONT + "# One {#one}\n\n{{include: part.lgd}}\n\n## Dup {#dup}\n"
    result, _ = _run(text, {"part.lgd": "## Dup {#dup}\n"})
    assert _found(result) == [("include-anchor-duplicate", "main.lgd", 23)]


def test_an_identifier_two_fragments_declare_is_reported_in_the_later_fragment():
    files = {"a.lgd": "## Dup {#dup}\n", "b.lgd": "Text.\n\n## Dup {#dup}\n"}
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: b.lgd}}\n", files)
    assert _found(result) == [("include-anchor-duplicate", "b.lgd", 3)]
    # ... and through a nested include, and for two declarations in one fragment.
    files = {"a.lgd": "## A {#dup}\n\n{{include: b.lgd}}\n", "b.lgd": "### B {#dup}\n"}
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", files)
    assert _found(result) == [("include-anchor-duplicate", "b.lgd", 1)]
    result, _ = _run(_FRONT + _SCOPE + "{{include: a.lgd}}\n", {"a.lgd": "## A {#dup}\n\n## B {#dup}\n"})
    assert _found(result) == [("include-anchor-duplicate", "a.lgd", 3)]


def test_a_fragment_included_twice_declares_its_identifiers_twice_and_is_reported_once_at_its_heading():
    # The heading of the fragment is where the finding is (not the second include's line): both are in the file.
    text = _FRONT + _SCOPE + "{{include: part.lgd}}\n\n{{include: part.lgd}}\n"
    result, _ = _run(text, {"part.lgd": "Text.\n\n## Part {#part}\n\nText.\n"})
    assert _found(result) == [("include-anchor-duplicate", "part.lgd", 3)]
    # A fragment with no identifier of its own (auto-generated ones are not judged here) draws nothing.
    result, _ = _run(text, {"part.lgd": "Text.\n\n## Part\n\nText.\n"})
    assert _found(result) == []


def test_two_declarations_that_are_never_together_are_no_duplicate():
    main = _TEMPLATE_FRONT + "# A {#a}\n\n{{include: one.lgd}} {when=x}\n\n{{include: two.lgd}} {when=!x}\n"
    files = {"one.lgd": "## Opt {#opt}\n", "two.lgd": "## Opt {#opt}\n"}
    result, _ = _run(main, files)
    assert _found(result) == []
    # ... and when they can be, they are.
    main = _TEMPLATE_FRONT + "# A {#a}\n\n{{include: one.lgd}} {when=x}\n\n{{include: two.lgd}}\n"
    result, _ = _run(main, files)
    assert _found(result) == [("include-anchor-duplicate", "two.lgd", 1)]


def test_the_documents_own_duplicates_are_anchor_duplicates_and_not_reported_twice():
    text = _FRONT + "# One {#dup}\n\n## Two {#dup}\n\n{{include: part.lgd}}\n"
    result, _ = _run(text, {"part.lgd": "Text.\n"})
    assert _found(result) == [("anchor-duplicate", "main.lgd", 21)]


# ── The limits ───────────────────────────────────────────────────────────────

def test_includes_deeper_than_the_depth_cap_are_not_followed_and_the_warning_says_so():
    count = MAX_INCLUDE_DEPTH + 8
    files = {f"p{i}.lgd": f"Text.\n\n{{{{include: p{i + 1}.lgd}}}}\n" for i in range(count)}
    files[f"p{count}.lgd"] = "Text.\n"
    result, loader = _run(_FRONT + _SCOPE + "{{include: p0.lgd}}\n", files)
    assert loader.asked == [f"p{i}.lgd" for i in range(MAX_INCLUDE_DEPTH)]
    assert _found(result) == []
    assert f"following includes of fragments deeper than {MAX_INCLUDE_DEPTH} levels (not checked past that point)" in _warning(result).message
    # Just within the cap: followed to the end, and no cap is named.
    files = {f"p{i}.lgd": f"Text.\n\n{{{{include: p{i + 1}.lgd}}}}\n" for i in range(MAX_INCLUDE_DEPTH - 1)}
    files[f"p{MAX_INCLUDE_DEPTH - 1}.lgd"] = "Text.\n"
    result, loader = _run(_FRONT + _SCOPE + "{{include: p0.lgd}}\n", files)
    assert len(loader.asked) == MAX_INCLUDE_DEPTH and "deeper than" not in _warning(result).message


def test_a_tree_of_more_includes_than_the_cap_is_cut_short_not_expanded():
    # Each fragment includes the next twice: 2**n includes, which are counted as they are expanded.
    levels = 30
    files = {f"p{i}.lgd": f"{{{{include: p{i + 1}.lgd}}}}\n\n{{{{include: p{i + 1}.lgd}}}}\n" for i in range(levels)}
    files[f"p{levels}.lgd"] = "## Leaf {#leaf}\n"
    result, loader = _run(_FRONT + _SCOPE + "{{include: p0.lgd}}\n", files)
    assert len(loader.asked) == levels + 1  # each file once, however many times it is included
    assert f"past {MAX_INCLUDE_EXPANDED:,} includes and headings (not checked past that point)" in _warning(result).message
    assert _found(result) == [("include-anchor-duplicate", "p30.lgd", 1)]


def test_a_fragment_with_more_headings_than_the_cap_is_cut_short_too():
    part = "".join(f"## H{i} {{#h{i}}}\n\n" for i in range(MAX_INCLUDE_EXPANDED + 5))
    result, _ = _run(_FRONT + _SCOPE + "{{include: part.lgd}}\n", {"part.lgd": part})
    assert _found(result) == []
    assert f"past {MAX_INCLUDE_EXPANDED:,} includes and headings" in _warning(result).message


# ── What the Warning of §17.5 says now ───────────────────────────────────────

@pytest.mark.parametrize("files", [{"p.lgd": "## Part {#part}\n\nText.\n"}, {"p.lgd": "Text.\n\n{{include: q.lgd}}\n", "q.lgd": "Text.\n"}])
def test_the_includes_warning_no_longer_says_cycle_and_duplicate_are_not_implemented(files):
    result, _ = _run(_FRONT + _SCOPE + "{{include: p.lgd}}\n", files)
    message = _warning(result).message
    assert "include-cycle" not in message and "include-anchor-duplicate" not in message and "not implemented" not in message
    # The combined document is still never put together (§16.11, #117).
    assert "combined-document checks (§16.11)" in message


# ── One file, however it is written ──────────────────────────────────────────

def test_two_spellings_of_one_file_are_one_file_read_and_judged_once(tmp_path):
    root = tmp_path / "root"
    (root / "dir").mkdir(parents=True)
    (root / "dir" / "a.lgd").write_text("---\ntitle: No\n---\n\nText.\n", encoding="utf-8")
    path = root / "dir" / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: a.lgd}}\n\n{{include: ../dir/a.lgd}}\n", encoding="utf-8")
    loader = _Loader({"a.lgd": "---\ntitle: No\n---\n\nText.\n"})
    result = validate(load(path), resolve=loader, document_root=root)
    # Asked for once, by the path it was first met by, and reported once, in that file.
    assert loader.asked == ["a.lgd"]
    assert _found(result) == [("include-has-frontmatter", "a.lgd", 2)]


def test_a_cycle_through_another_spelling_of_a_file_is_a_cycle(tmp_path):
    root = tmp_path / "root"
    (root / "dir").mkdir(parents=True)
    (root / "dir" / "a.lgd").write_text("Text.\n\n{{include: ../dir/a.lgd}}\n", encoding="utf-8")
    path = root / "dir" / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: a.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path), document_root=root)) == [("include-cycle", "a.lgd", 3)]
    # ... and one back to the document by a path that climbs out and in again.
    (root / "dir" / "a.lgd").write_text("Text.\n\n{{include: ../dir/main.lgd}}\n", encoding="utf-8")
    assert _found(validate(load(path), document_root=root)) == [("include-cycle", "a.lgd", 3)]


def test_a_symbolic_link_is_another_file_for_the_chain_but_the_depth_cap_ends_it(tmp_path):
    (tmp_path / "real.lgd").write_text("Text.\n\n{{include: link.lgd}}\n", encoding="utf-8")
    try:
        (tmp_path / "link.lgd").symlink_to(tmp_path / "real.lgd")
    except OSError:
        pytest.skip("symbolic links are not available")
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _SCOPE + "{{include: real.lgd}}\n", encoding="utf-8")
    # real includes link, which is real: the chain real -> link -> link ... is not told from a link (not resolved)
    # and stops where the same name comes round.
    found = _found(validate(load(path)))
    assert [rule for rule, *_ in found] == ["include-cycle"]


# ── In a template ────────────────────────────────────────────────────────────

def test_a_nested_include_in_a_templates_fragment_leaves_the_rest_of_the_fragment_as_it_is():
    main = _TEMPLATE_FRONT + "# One {#one}\n\n{{include: p.lgd}}\n\n### Three {#three}\n"
    files = {"p.lgd": "## Deep {#deep}\n\n{{include: q.lgd}}\n"}
    result, loader = _run(main, files)
    # Not followed, and an Error of its own; but the fragment still fills the level.
    assert loader.asked == ["p.lgd"]
    assert _found(result) == [("template-fragment-invalid", "p.lgd", 3)]
    message = _warning(result).message
    assert "1 include not read (1 is in a template's fragment, where it is template-fragment-invalid and not followed)" in message
