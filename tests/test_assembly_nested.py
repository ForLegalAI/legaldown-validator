"""Assembling an ordinary document whose fragments include fragments (§12.2, §15.7.2, #118).

A plain document is a degenerate draft: every step applies and those with nothing to act on change
nothing (§15.7). Each fragment is written at its own path with its ``{{include:}}`` lines as they
were (§15.7.2), those nested in it too; a nested include is read from the directory of the fragment that
holds it, as ``validate`` reads it (#114). A cycle is ``include-cycle``; a chain deeper, or a walk longer,
than the validator follows is ``ldv-include-limit``. A template's nested include stays
``template-fragment-invalid`` (§15.3)."""
from __future__ import annotations

import pytest

from legaldown import Template, assemble, load_template
from legaldown.assembly import _template_identifiers
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_OK, main
from legaldown.grammar import SKIPPED_RULE
from legaldown.validator import fragments
from legaldown.validator.fragments import (
    EMPTY_CHAIN,
    MAX_INCLUDE_DEPTH,
    MAX_INCLUDE_EXPANDED,
    Files,
    closes,
    extend,
    fragment_includes,
)
from legaldown.validator.patterns import INCLUDE_LIMIT_RULE

pytestmark = pytest.mark.filterwarnings(r"ignore:legaldown\.assemble\(\) is deprecated:DeprecationWarning")

_FRONT = "---\ntitle: Fixture\ndocument_type: contract\n---\n\n"
_TEMPLATE_FRONT = "---\ntitle: Fixture\ndocument_type: contract\nquestions:\n  x:\n    type: boolean\n---\n\n"
_MAIN = "# Doc\n\n## Terms\n\n{{include: sub/a.lgd}}\n"
_TEXT = "name"


class _Loader:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.asked: list[str] = []

    def __call__(self, path: str) -> str | None:
        self.asked.append(path)
        return self.files.get(path)


def _assembled(main: str, files: dict[str, str], answers: dict | None = None, *, front: str = _FRONT, **kwargs):
    return Template(front + main, resolve=files.get, **kwargs).form(answers or {}).assemble()


def _found(result, *rules: str):
    return [(d.rule, d.file, d.line) for d in result.diagnostics if not rules or d.rule in rules]


def _refused(result) -> None:
    assert not result.ok
    assert (result.output, result.files) == ("", {})


# ── Nested includes are assembled ─────────────────────────────────────────────

def test_a_fragments_include_is_assembled_and_written_at_its_own_path():
    files = {
        "sub/a.lgd": "### A\n\nFor {{placeholder: name, type=text}}.\n\n{{include: b.lgd}}\n",
        "sub/b.lgd": "#### B\n\nB, for {{placeholder: name, type=text}}.\n",
    }
    result = _assembled(_MAIN, files, {"name": "Ann"})
    assert result.ok and not result.diagnostics
    # The include lines are as they were; the blank is filled in each file that holds one.
    assert result.output == _FRONT + _MAIN
    assert result.files == {
        "sub/a.lgd": "### A\n\nFor Ann.\n\n{{include: b.lgd}}\n",
        "sub/b.lgd": "#### B\n\nB, for Ann.\n",
    }


def test_three_levels_of_nesting_and_every_file_read_is_written():
    files = {
        "sub/a.lgd": "### A\n\n{{include: deeper/b.lgd}}\n",
        "sub/deeper/b.lgd": "#### B\n\n{{include: c.lgd}}\n",
        "sub/deeper/c.lgd": "##### C\n\nDeepest {{placeholder: name, type=text}}.\n",
    }
    loader = _Loader(files)
    result = Template(_FRONT + _MAIN, resolve=loader).form({"name": "x"}).assemble()
    assert result.ok
    # Each file the walk read is in `files`: one not written would be an include of the output with no file.
    assert set(result.files) == set(files) == set(loader.asked)
    assert result.files["sub/deeper/c.lgd"] == "##### C\n\nDeepest x.\n"
    assert result.output == _FRONT + _MAIN


def test_a_nested_include_is_read_from_the_directory_of_the_fragment_that_holds_it():
    files = {
        "sub/a.lgd": "### A\n\n{{include: ../c.lgd}}\n\n{{include: d.lgd}}\n",
        "c.lgd": "### C\n",
        "sub/d.lgd": "### D\n",
    }
    loader = _Loader(files)
    result = Template(_FRONT + _MAIN, resolve=loader).form({}).assemble()
    assert result.ok and set(result.files) == {"sub/a.lgd", "c.lgd", "sub/d.lgd"}
    assert loader.asked == ["sub/a.lgd", "c.lgd", "sub/d.lgd"]


def test_a_fragment_included_from_two_places_is_written_once():
    main = "# Doc\n\n## One\n\n{{include: a.lgd}}\n\n## Two\n\n{{include: b.lgd}}\n"
    files = {
        "a.lgd": "### A\n\n{{include: shared.lgd}}\n",
        "b.lgd": "### B\n\n{{include: shared.lgd}}\n",
        "shared.lgd": "#### S\n\nShared {{placeholder: name, type=text}}.\n",
    }
    result = _assembled(main, files, {"name": "x"})
    assert result.ok and sorted(result.files) == ["a.lgd", "b.lgd", "shared.lgd"]
    assert result.files["shared.lgd"] == "#### S\n\nShared x.\n"


def test_an_include_that_is_not_an_include_paragraph_is_text_and_not_read():
    part = (
        "### A\n\nSee {{include: g1.lgd}} inline.\n\n- {{include: g2.lgd}}\n\n> {{include: g3.lgd}}\n\n"
        "> [!DRAFTING]\n> {{include: g4.lgd}}\n"
    )
    loader = _Loader({"sub/a.lgd": part})
    result = Template(_FRONT + _MAIN, resolve=loader).form({}).assemble()
    assert result.ok and loader.asked == ["sub/a.lgd"]
    assert set(result.files) == {"sub/a.lgd"}
    # The drafting note is removed with its include, unread (§15.6); the rest is text.
    assert "g4.lgd" not in result.files["sub/a.lgd"] and "g3.lgd" in result.files["sub/a.lgd"]


def test_the_include_paragraphs_assembly_reads_are_the_ones_the_validator_reads():
    part = (
        "{{include: p.lgd}}\n\n### A\n\nSee {{include: g1.lgd}} inline.\n\n{{include: q.lgd}}\n\n- {{include: g2.lgd}}\n\n"
        "### B\n\n> {{include: g3.lgd}}\n\n{{include: r.lgd}}\n"
    )
    template = Template(_FRONT + _MAIN, resolve={"sub/a.lgd": part}.get)
    source = template._t.subs["sub/a.lgd"]
    from legaldown import parse
    expected = fragment_includes(fragments.FragmentFile("sub/a.lgd", parse(part, on_frontmatter_error="keep")))
    assert [(i.path, i.section) for i in source.includes] == [(i.path, i.section) for i in expected]
    assert [i.section for i in source.includes] == [None, 0, 1]


def test_the_deprecated_function_assembles_a_nested_include_too():
    files = {"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "#### B\n\nText.\n"}
    result = assemble(_FRONT + _MAIN, {}, load_file=files.get)
    assert result.ok and result.files == files
    result = assemble(_FRONT + _MAIN, {}, load_file={**files, "sub/b.lgd": "#### B\n\n{{include: a.lgd}}\n"}.get)
    assert _found(result) == [("include-cycle", "sub/b.lgd", 3)]


def test_the_questions_of_a_nested_fragment_are_asked_where_they_are_reached():
    main = "# Doc\n\nFirst {{placeholder: first, type=text}}.\n\n{{include: a.lgd}}\n\nLast {{placeholder: last, type=text}}.\n"
    files = {"a.lgd": "## A\n\n{{include: b.lgd}}\n\nA {{placeholder: middle, type=text}}.\n", "b.lgd": "### B\n\nB {{placeholder: deep, type=text}}.\n"}
    form = Template(_FRONT + main, resolve=files.get).form({})
    assert [question.id for question in form.questions] == ["first", "deep", "middle", "last"]


def test_a_fragment_included_directly_and_through_another_is_asked_at_its_last_include():
    # c is included by a, and by the document after "Mid": the last include in the combined document reads it.
    main = "# Doc\n\n{{include: a.lgd}}\n\nMid {{placeholder: pmid, type=text}}.\n\n{{include: c.lgd}}\n"
    c = "## C\n\nC {{placeholder: pc, type=text}}.\n"
    nested = {"a.lgd": "## A\n\nA {{placeholder: pa, type=text}}.\n\n{{include: c.lgd}}\n", "c.lgd": c}
    flat = {"a.lgd": "## A\n\nA {{placeholder: pa, type=text}}.\n", "c.lgd": c}
    asked = [question.id for question in Template(_FRONT + main, resolve=nested.get).form({}).questions]
    assert asked == [question.id for question in Template(_FRONT + main, resolve=flat.get).form({}).questions]
    assert asked == ["pa", "pmid", "pc"]
    # ... the same in the order the files are read (not inline), and the file is written once.
    result = _assembled(main, nested, {"pa": "1", "pmid": "2", "pc": "3"})
    assert result.ok and result.files["c.lgd"] == "## C\n\nC 3.\n"


def test_a_tree_the_walk_cut_short_is_not_walked_again_to_decide_what_is_kept():
    levels = 40  # 2**40 paths
    files = {f"p{i}.lgd": f"{{{{include: p{i + 1}.lgd}}}}\n\n{{{{include: p{i + 1}.lgd}}}}\n" for i in range(levels)}
    files[f"p{levels}.lgd"] = "## Leaf\n"
    template = Template(_FRONT + "# Doc\n\n{{include: p0.lgd}}\n", resolve=files.get)
    template.form({})  # the walk that was cut short left targets, and this must end
    assert template._t.last["p1.lgd"] is template._t.subs["p0.lgd"].includes[0]  # (the walk stopped before the second)


_ATTACHED = _FRONT.replace("---\n\n", "attachments:\n  - id: d\n    title: D\n    file: d.lgd\n---\n\n")


def _survives(front: str, main: str, files: dict[str, str], tmp_path) -> Template:
    """Everything a caller does with a template that may have problems must not raise."""
    template = Template(front + main, resolve=files.get, path=tmp_path / "main.lgd")
    assert template.problems is not None and template.validation is not None
    form = template.form({})
    form.as_dict()
    form.assemble()
    _template_identifiers(template._t)
    return template


def test_a_cycle_through_an_attachment_file_that_is_an_include_too_does_not_end_in_an_error(tmp_path):
    # The cycles are cleared from what the walk of the includes left; the attachment file's own includes are
    # not walked, and a file they take in must still be decided.
    files = {
        "a.lgd": "## A\n\n{{include: b.lgd}}\n\n{{include: d.lgd}}\n\n{{include: a.lgd}}\n",
        "b.lgd": "## B\n\n{{include: d.lgd}}\n",
        "c.lgd": "## C\n",
        "d.lgd": "## D\n\n{{include: c.lgd}}\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n",
    }
    template = _survives(_ATTACHED, "# Doc\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n", files, tmp_path)
    assert "include-cycle" in {d.rule for d in template.problems}
    assert not template.form({}).ready


@pytest.mark.parametrize("seed", range(60))
def test_no_graph_of_includes_and_attachments_makes_a_template_raise(tmp_path, seed):
    import random

    rng = random.Random(seed)
    names = ["a.lgd", "b.lgd", "c.lgd", "d.lgd", "e.lgd"]
    files = {}
    for name in names:
        taken = rng.sample(names, rng.randint(0, 3))
        files[name] = f"## {name[0].upper()}\n\n" + "".join(f"{{{{include: {other}}}}}\n\n" for other in taken)
    main = "# Doc\n\n" + "".join(f"{{{{include: {other}}}}}\n\n" for other in rng.sample(names, rng.randint(1, 3)))
    attached = rng.sample(names, rng.randint(0, 2))
    front = _FRONT.replace("---\n\n", "".join(
        ["attachments:\n"] + [f"  - id: {n[0]}\n    title: T\n    file: {n}\n" for n in attached] + ["---\n\n"]
    )) if attached else _FRONT
    _survives(front, main, files, tmp_path)


def test_each_fragment_is_parsed_once_when_the_template_has_a_path(monkeypatch, tmp_path):
    import legaldown.assembly
    import legaldown.parser

    seen: list[str] = []
    real = legaldown.parser.parse

    def counting(text, *args, **kwargs):
        seen.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(legaldown.parser, "parse", counting)
    monkeypatch.setattr(legaldown.assembly, "parse", counting)
    files = {"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "#### B\n\nText.\n"}
    Template(_FRONT + _MAIN, resolve=files.get, path=tmp_path / "main.lgd")
    assert all(seen.count(text) == 1 for text in files.values())


def test_nested_lines_keep_their_line_endings_and_a_nested_byte_order_mark_goes_as_a_flat_one_does():
    nested = "#### B\r\n\r\nCRLF {{placeholder: name, type=text}}.\r\n"
    files = {"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "\ufeff" + nested}
    result = _assembled(_MAIN, files, {"name": "x"})
    flat = _assembled(_MAIN, {"sub/a.lgd": "\ufeff" + nested})
    assert result.ok and result.files["sub/b.lgd"] == "#### B\r\n\r\nCRLF x.\r\n"
    assert result.files["sub/b.lgd"].startswith(flat.files["sub/a.lgd"][:1])


# ── The identifiers of a heading (step 7) ─────────────────────────────────────

def test_the_identifiers_of_headings_in_nested_fragments_are_worked_out_in_the_combined_document():
    # Step 7 splices every fragment at its include, those nested in it too, to tell which identifier each heading
    # gets. Nothing in an ordinary document changes one (no unit is removed, and a heading holds no blank), so
    # nothing is written in: but the headings of the files must match the combined document's, or assembly fails.
    main = "# Doc\n\n## Terms\n\n{{include: a.lgd}}\n\n## Terms\n\nLast.\n"
    files = {
        "a.lgd": "### Terms\n\n{{include: sub/b.lgd}}\n\n### Terms\n",
        "sub/b.lgd": "#### Terms\n\n{{include: c.lgd}}\n",
        "sub/c.lgd": "##### Terms\n\nText.\n",
    }
    result = _assembled(main, files)
    assert result.ok and result.files == files and result.output == _FRONT + main


# ── What is wrong with a nested include ───────────────────────────────────────

@pytest.mark.parametrize(("files", "expected"), [
    # a missing file, at the include that names it
    ({"sub/a.lgd": "### A\n\n{{include: gone.lgd}}\n"}, [("include-file-missing", "sub/a.lgd", 3)]),
    # one that names no file
    ({"sub/a.lgd": "### A\n\n{{include:}}\n"}, [("include-file-missing", "sub/a.lgd", 3)]),
    # a path that cannot be read: absolute, a backslash, out of the directory without a root
    ({"sub/a.lgd": "### A\n\n{{include: /etc/x.lgd}}\n"}, [("include-file-missing", "sub/a.lgd", 3)]),
    ({"sub/a.lgd": "### A\n\n{{include: x\\y.lgd}}\n"}, [("include-file-missing", "sub/a.lgd", 3)]),
    ({"sub/a.lgd": "### A\n\n{{include: ../../x.lgd}}\n", "../x.lgd": "### X\n"}, [("include-file-missing", "sub/a.lgd", 3)]),
    # a file that is not a fragment
    ({"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "---\ntitle: T\n---\n\n#### B\n"}, [("include-has-frontmatter", "sub/b.lgd", 2)]),
    ({"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "# B\n"}, [("include-has-h1", "sub/b.lgd", 1)]),
    # frontmatter that cannot be read is a diagnostic, not an exception
    ({"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "---\ntitle: [\n---\n\n#### B\n"}, [("frontmatter-invalid-yaml", "sub/b.lgd", 2)]),
])
def test_what_is_wrong_with_a_nested_include_is_reported_in_its_file_at_its_line_and_nothing_is_assembled(files, expected):
    result = _assembled(_MAIN, files)
    assert _found(result) == expected
    _refused(result)


def test_an_include_that_names_no_file_says_so_as_a_documents_does():
    result = _assembled(_MAIN, {"sub/a.lgd": "### A\n\n{{include:}}\n"})
    (diagnostic,) = result.diagnostics
    assert "names no file" in diagnostic.message and "'{{include:}}'" in diagnostic.message


def test_a_nested_fragment_is_judged_as_a_file_once_however_often_it_is_included():
    main = "# Doc\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n"
    files = {
        "a.lgd": "## A\n\n{{include: bad.lgd}}\n", "b.lgd": "## B\n\n{{include: bad.lgd}}\n", "bad.lgd": "# Bad\n",
    }
    assert _found(_assembled(main, files)) == [("include-has-h1", "bad.lgd", 1)]
    # ... but a path that is not there is reported at each include of it.
    files["bad.lgd"] = "## Bad\n"
    del files["bad.lgd"]
    assert _found(_assembled(main, files)) == [
        ("include-file-missing", "a.lgd", 3), ("include-file-missing", "b.lgd", 3),
    ]


def test_an_include_below_a_fragment_with_frontmatter_is_not_followed():
    loader = _Loader({"sub/a.lgd": "---\ntitle: T\n---\n\n### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "### B\n"})
    result = Template(_FRONT + _MAIN, resolve=loader).form({}).assemble()
    assert _found(result) == [("include-has-frontmatter", "sub/a.lgd", 2)]
    assert loader.asked == ["sub/a.lgd"]


def test_assembly_does_not_judge_the_heading_levels_of_the_combined_document():
    # include-heading-skip is the validator's (§16.11); assembly is given a document that has no Errors (§15.7.2).
    main = "# Doc\n\n{{include: a.lgd}}\n"
    files = {"a.lgd": "##### Five\n\nText.\n"}
    assert _assembled(main, files).ok
    template = Template(_FRONT + main, resolve=files.get)
    assert "include-heading-skip" in {d.rule for d in template.validation.diagnostics}
    assert template.problems == ()


# ── What is not a LegalDown file ──────────────────────────────────────────────

def test_a_nested_include_of_a_file_that_is_not_legaldown_is_refused_and_not_asked_for():
    loader = _Loader({"sub/a.lgd": "### A\n\n{{include: b.txt}}\n", "sub/b.txt": "Text.\n"})
    result = Template(_FRONT + _MAIN, resolve=loader).form({}).assemble()
    assert _found(result) == [("include-not-legaldown", "sub/a.lgd", 3)]
    assert "'b.txt' is not a LegalDown file" in result.diagnostics[0].message
    assert loader.asked == ["sub/a.lgd"]
    _refused(result)


@pytest.mark.parametrize("front", [_FRONT, _TEMPLATE_FRONT])
def test_an_include_of_the_documents_own_of_a_file_that_is_not_legaldown_is_refused_and_not_asked_for(front):
    loader = _Loader({"x.txt": "Text.\n"})
    result = Template(front + "# Doc\n\n{{include: x.txt}}\n", resolve=loader).form({"x": True}).assemble()
    assert [(d.rule, d.file) for d in result.diagnostics] == [("include-not-legaldown", "")]
    assert loader.asked == []
    # ... with no loader too: the extension is judged before anything is read.
    result = Template(front + "# Doc\n\n{{include: x.txt}}\n").form({"x": True}).assemble()
    assert [d.rule for d in result.diagnostics] == ["include-not-legaldown"]


def test_an_absolute_path_of_the_document_is_a_path_the_validator_judges_alone_and_assembly_cannot_read():
    result = _assembled("# Doc\n\n{{include: /etc/x.txt}}\n", {})
    assert [d.rule for d in result.diagnostics] == ["include-file-missing"]


def test_a_nested_path_that_only_leads_out_of_the_directory_is_an_error_where_the_validator_leaves_it_unread():
    # (A deliberate difference: the validator warns and goes on; output cannot be made without the file.)
    files = {"sub/a.lgd": "### A\n\n{{include: ../../x.lgd}}\n"}
    template = Template(_FRONT + _MAIN, resolve=files.get)
    assert [d.rule for d in template.problems] == ["include-file-missing"]
    assert "include-file-missing" not in {d.rule for d in template.validation.diagnostics}


def test_the_chain_helpers_are_the_validators(tmp_path):
    template = Template(_FRONT + _MAIN, resolve=lambda path: None, path=tmp_path / "main.lgd")
    files = Files(lambda path: None, template._t.document, None)
    chain = extend(files, extend(files, EMPTY_CHAIN, "sub/a.lgd"), "sub/b.lgd")
    assert chain.paths == ("sub/a.lgd", "sub/b.lgd")
    assert closes(files, chain, "sub/a.lgd") and not closes(files, chain, "sub/c.lgd") and not closes(files, EMPTY_CHAIN, "sub/a.lgd")


# ── Cycles ────────────────────────────────────────────────────────────────────

def test_a_chain_that_comes_back_is_a_cycle_at_the_include_that_closes_it():
    files = {"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "#### B\n\n{{include: a.lgd}}\n"}
    result = _assembled(_MAIN, files)
    assert _found(result) == [("include-cycle", "sub/b.lgd", 3)]
    _refused(result)
    assert "the document -> sub/a.lgd -> sub/b.lgd -> sub/a.lgd" in result.diagnostics[0].message


def test_a_fragment_that_includes_itself_is_a_cycle():
    result = _assembled(_MAIN, {"sub/a.lgd": "### A\n\n{{include: a.lgd}}\n"})
    assert _found(result) == [("include-cycle", "sub/a.lgd", 3)]
    _refused(result)


def test_a_cycle_is_one_diagnostic_however_often_it_is_reached():
    main = "# Doc\n\n{{include: a.lgd}}\n\n{{include: a.lgd}}\n"
    files = {"a.lgd": "## A\n\n{{include: b.lgd}}\n", "b.lgd": "### B\n\n{{include: a.lgd}}\n"}
    assert _found(_assembled(main, files)) == [("include-cycle", "b.lgd", 3)]


def test_a_cycle_reached_from_two_places_leaves_no_loop_in_what_assembly_reads(tmp_path):
    # b is included by the document and by a, and a by the document and by b: the cycle is refused in the
    # chain it is met in, and a target left by another chain must not lead round.
    main = "# Doc\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n"
    files = {"a.lgd": "## A\n\n{{include: b.lgd}}\n", "b.lgd": "## B\n\n{{include: a.lgd}}\n"}
    template = Template(_FRONT + main, resolve=files.get, path=tmp_path / "main.lgd")
    assert {d.rule for d in template.problems} == {"include-cycle"}
    # (Once, as the validator says it: at the include that closed it where it was first met.)
    assert [(d.file, d.line) for d in template.problems] == [("b.lgd", 3)]
    # The identifiers of the template are worked out over the combined document, which must end.
    assert _template_identifiers(template._t)
    assert template.form({}).ready is False
    assert _found(template.form({}).assemble()) == _found(template.form({}).assemble())
    _refused(template.form({}).assemble())


def test_a_fragment_that_includes_the_document_is_a_cycle_when_the_template_has_a_path(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.lgd").write_text("### A\n\n{{include: ../main.lgd}}\n", encoding="utf-8")
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _MAIN, encoding="utf-8")
    template = load_template(path)
    assert _found(template.form({}).assemble()) == [("include-cycle", "sub/a.lgd", 3)]
    assert "sub/a.lgd -> the document" in template.problems[0].message
    # One Error, not one of assembly and one of the validation.
    assert [d.rule for d in template.problems].count("include-cycle") == 1


def test_a_fragment_that_includes_a_document_that_is_not_known_to_be_the_template_reads_it_as_a_fragment():
    # Without a path nothing says which file the template is: it is read, and is not a fragment (it has frontmatter
    # and a level 1 heading).
    files = {"sub/a.lgd": "### A\n\n{{include: ../main.lgd}}\n", "main.lgd": _FRONT + _MAIN}
    assert _found(_assembled(_MAIN, files)) == [
        ("include-has-frontmatter", "main.lgd", 2), ("include-has-h1", "main.lgd", 6),
    ]


def test_a_document_that_includes_itself_is_a_cycle(tmp_path):
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + "# Doc\n\n{{include: main.lgd}}\n", encoding="utf-8")
    result = load_template(path).form({}).assemble()
    assert _found(result) == [("include-cycle", "", 8)]
    _refused(result)
    # (A flat include of a file with frontmatter was `include-has-frontmatter`; it is the cycle now.)


def test_a_template_that_includes_itself_is_refused_as_it_was(tmp_path):
    path = tmp_path / "t.lgd"
    path.write_text(_TEMPLATE_FRONT + "# Doc\n\n{{include: t.lgd}}\n", encoding="utf-8")
    result = load_template(path).form({"x": True}).assemble()
    assert not result.ok and "include-cycle" not in {d.rule for d in result.diagnostics}


def test_a_cycle_through_another_spelling_of_a_file_is_a_cycle(tmp_path):
    root = tmp_path / "root"
    (root / "dir" / "sub").mkdir(parents=True)
    (root / "dir" / "sub" / "a.lgd").write_text("### A\n\n{{include: ../../dir/sub/b.lgd}}\n", encoding="utf-8")
    (root / "dir" / "sub" / "b.lgd").write_text("#### B\n\n{{include: a.lgd}}\n", encoding="utf-8")
    path = root / "dir" / "main.lgd"
    path.write_text(_FRONT + _MAIN, encoding="utf-8")
    template = load_template(path, document_root=root)
    result = template.form({}).assemble()
    assert [rule for rule, *_ in _found(result)] == ["include-cycle"]
    _refused(result)


_GRAPHS = {
    "a-b-a": {"a.lgd": "## A\n\n{{include: b.lgd}}\n", "b.lgd": "### B\n\n{{include: a.lgd}}\n"},
    "self": {"a.lgd": "## A\n\n{{include: a.lgd}}\n", "b.lgd": "## B\n"},
    "three": {
        "a.lgd": "## A\n\n{{include: b.lgd}}\n", "b.lgd": "### B\n\n{{include: c.lgd}}\n",
        "c.lgd": "#### C\n\n{{include: a.lgd}}\n",
    },
    "diamond": {
        "a.lgd": "## A\n\n{{include: b.lgd}}\n\n{{include: c.lgd}}\n", "b.lgd": "### B\n\n{{include: d.lgd}}\n",
        "c.lgd": "### C\n\n{{include: d.lgd}}\n", "d.lgd": "#### D\n",
    },
    "diamond-cycle": {
        "a.lgd": "## A\n\n{{include: b.lgd}}\n\n{{include: c.lgd}}\n", "b.lgd": "### B\n\n{{include: d.lgd}}\n",
        "c.lgd": "### C\n\n{{include: d.lgd}}\n", "d.lgd": "#### D\n\n{{include: a.lgd}}\n",
    },
    "to-the-document": {"a.lgd": "## A\n\n{{include: main.lgd}}\n", "main.lgd": _FRONT + "# Doc\n\n{{include: a.lgd}}\n"},
    "in-a-section": {"a.lgd": "## A\n\n{{include: b.lgd}}\n\n## A2\n\n{{include: b.lgd}}\n", "b.lgd": "### B\n\n{{include: a.lgd}}\n"},
    "missing": {"a.lgd": "## A\n\n{{include: gone.lgd}}\n\n{{include: b.lgd}}\n", "b.lgd": "### B\n\n{{include: b.lgd}}\n"},
    # the order of the checks of an include: a file that is not LegalDown is not looked for, nested or not
    "txt-nested": {"a.lgd": "## A\n\n{{include: b.txt}}\n\n{{include: ../gone.txt}}\n\n{{include: c.lgd}}\n", "b.txt": "x\n", "c.lgd": "### C\n"},
    "txt-top-level": {
        "<main>": "# Doc\n\n{{include: b.txt}}\n\n{{include: ../x.txt}}\n\n{{include: a.lgd}}\n",
        "a.lgd": "## A\n", "b.txt": "x\n",
    },
    "two-entries": {
        "<main>": "# Doc\n\n{{include: a.lgd}}\n\n{{include: b.lgd}}\n",
        "a.lgd": "## A\n\n{{include: b.lgd}}\n", "b.lgd": "## B\n\n{{include: a.lgd}}\n",
    },
    "txt-and-cycle": {"a.lgd": "## A\n\n{{include: a.txt}}\n\n{{include: a.lgd}}\n", "a.txt": "x\n"},
}


@pytest.mark.parametrize("graph", sorted(_GRAPHS))
def test_assembly_finds_what_the_validator_finds_of_the_includes_it_judges_before_reading(tmp_path, graph):
    """Drift guard: the same ``include-cycle``, ``include-not-legaldown`` and ``include-file-missing``, in the
    same file at the same line, whatever the order of the checks of an include."""
    files = dict(_GRAPHS[graph])
    main = files.pop("<main>", "# Doc\n\n{{include: a.lgd}}\n")
    path = tmp_path / "main.lgd"
    template = Template(_FRONT + main, resolve=files.get, path=path)
    rules = {"include-cycle", "include-not-legaldown", "include-file-missing"}
    validated = sorted((d.rule, d.file, d.line) for d in template.validation.diagnostics if d.rule in rules)
    assembled = sorted((d.rule, d.file, d.line) for d in template.problems if d.rule in rules)
    assert assembled == validated and assembled == sorted(set(assembled))
    assert bool(assembled) == (graph != "diamond")


# ── The limits ────────────────────────────────────────────────────────────────

def _chain(count: int) -> dict[str, str]:
    """``p0.lgd`` includes ``p1.lgd``, and so on to ``p{count-1}.lgd``, which includes nothing."""
    files = {f"p{i}.lgd": f"Text.\n\n{{{{include: p{i + 1}.lgd}}}}\n" for i in range(count - 1)}
    files[f"p{count - 1}.lgd"] = "Text.\n"
    return files


def test_a_chain_of_the_depth_cap_is_assembled_and_one_more_is_refused():
    main = "# Doc\n\n{{include: p0.lgd}}\n"
    result = _assembled(main, _chain(MAX_INCLUDE_DEPTH))
    assert result.ok and len(result.files) == MAX_INCLUDE_DEPTH
    result = _assembled(main, _chain(MAX_INCLUDE_DEPTH + 1))
    assert _found(result) == [(INCLUDE_LIMIT_RULE, f"p{MAX_INCLUDE_DEPTH - 1}.lgd", 3)]
    _refused(result)
    assert f"deeper than {MAX_INCLUDE_DEPTH} levels" in result.diagnostics[0].message
    # It says assembly stopped, as an Error does; "not checked" is the Warning of the validator's.
    assert result.diagnostics[0].message.startswith("Assembly stopped at its limit") and "not checked" not in result.diagnostics[0].message


def test_a_tree_of_more_includes_than_the_cap_is_refused_not_expanded():
    levels = 30
    files = {f"p{i}.lgd": f"{{{{include: p{i + 1}.lgd}}}}\n\n{{{{include: p{i + 1}.lgd}}}}\n" for i in range(levels)}
    files[f"p{levels}.lgd"] = "## Leaf\n"
    loader = _Loader(files)
    result = Template(_FRONT + "# Doc\n\n{{include: p0.lgd}}\n", resolve=loader).form({}).assemble()
    assert [rule for rule, *_ in _found(result)] == [INCLUDE_LIMIT_RULE]
    _refused(result)
    assert f"past {MAX_INCLUDE_EXPANDED:,} includes and headings" in result.diagnostics[0].message
    assert len(set(loader.asked)) == levels + 1


def test_a_fragment_of_more_headings_than_the_cap_is_refused_though_it_includes_nothing():
    # (It was assembled before nested includes were: the validator cannot vouch for it past the cap, §17.5.)
    def fragment(count: int) -> str:
        return "".join(f"## H{i}\n\nText.\n\n" for i in range(count))

    main = "# Doc\n\n{{include: part.lgd}}\n"
    assert _assembled(main, {"part.lgd": fragment(MAX_INCLUDE_EXPANDED)}).ok
    result = _assembled(main, {"part.lgd": fragment(MAX_INCLUDE_EXPANDED + 1)})
    assert _found(result) == [(INCLUDE_LIMIT_RULE, "part.lgd", 4 * MAX_INCLUDE_EXPANDED + 1)]
    _refused(result)


def test_the_documents_own_includes_cost_nothing():
    main = "# Doc\n\n" + "".join(f"{{{{include: p{i}.lgd}}}}\n\n" for i in range(5))
    files = {f"p{i}.lgd": "## P\n" for i in range(5)}
    assert _assembled(main, files).ok


def _shapes() -> dict[str, tuple[str, dict[str, str]]]:
    main = "# Doc\n\n{{include: f.lgd}}\n"
    shapes: dict[str, tuple[str, dict[str, str]]] = {}
    for n in (2, 3, 4, 5, 6):
        shapes[f"chain-{n}"] = ("# Doc\n\n{{include: p0.lgd}}\n", _chain(n))
    for levels in (1, 2, 3):
        tree = {f"t{i}.lgd": f"{{{{include: t{i + 1}.lgd}}}}\n\n{{{{include: t{i + 1}.lgd}}}}\n" for i in range(levels)}
        tree[f"t{levels}.lgd"] = "## Leaf\n"
        shapes[f"tree-{levels}"] = ("# Doc\n\n{{include: t0.lgd}}\n", tree)
    for headings in (1, 3, 6, 9):
        shapes[f"headings-{headings}"] = (main, {"f.lgd": "".join(f"## H{i}\n\n" for i in range(headings))})
    # The cap is reached at a heading, or at an include before it or after it: the order of the walk counts.
    shapes["preamble"] = (main, {"f.lgd": "{{include: b.lgd}}\n\n## A1\n\n## A2\n\n## A3\n", "b.lgd": "### B1\n"})
    shapes["section"] = (main, {"f.lgd": "## A1\n\n{{include: b.lgd}}\n\n## A2\n\n## A3\n", "b.lgd": "### B1\n"})
    shapes["last"] = (main, {"f.lgd": "## A1\n\n## A2\n\n## A3\n\n{{include: b.lgd}}\n", "b.lgd": "### B1\n"})
    shapes["missing"] = (main, {"f.lgd": "## A1\n\n{{include: gone.lgd}}\n\n## A2\n\n## A3\n"})
    shapes["diamond"] = (main, {
        "f.lgd": "## A1\n\n{{include: b.lgd}}\n\n## A2\n\n{{include: b.lgd}}\n", "b.lgd": "### B1\n\n{{include: c.lgd}}\n",
        "c.lgd": "#### C1\n",
    })
    return shapes


@pytest.mark.parametrize("expanded", range(1, 10))
@pytest.mark.parametrize("depth", range(1, 6))
@pytest.mark.parametrize("shape", sorted(_shapes()))
def test_assembly_stops_where_the_validator_stops_under_lowered_caps(monkeypatch, shape, depth, expanded):
    """Drift guard: assembly refuses with ``ldv-include-limit`` for exactly the caps the validator says it
    reached (``ldv-check-skipped``), whatever the size of the caps: the order of the budget is the walk's."""
    monkeypatch.setattr(fragments, "MAX_INCLUDE_DEPTH", depth)
    monkeypatch.setattr(fragments, "MAX_INCLUDE_EXPANDED", expanded)
    main, files = _shapes()[shape]
    template = Template(_FRONT + main, resolve=files.get)
    skipped = " ".join(d.message for d in template.validation.diagnostics if d.rule == SKIPPED_RULE)
    reached = {("depth", "deeper than"), ("count", "includes and headings")}
    validated = {name for name, phrase in reached if phrase in skipped}
    assembled = {
        name for name, phrase in reached for d in template.problems if d.rule == INCLUDE_LIMIT_RULE and phrase in d.message
    }
    assert assembled == validated
    result = template.form({}).assemble()
    assert result.ok == (not [d for d in template.problems if d.level == "error"])
    if validated:
        assert not result.ok and (result.output, result.files) == ("", {})


# ── A document root ───────────────────────────────────────────────────────────

def _on_disk(tmp_path):
    root = tmp_path / "root"
    (root / "doc" / "sub").mkdir(parents=True)
    (root / "shared").mkdir()
    (tmp_path / "outside.lgd").write_text("### Out\n", encoding="utf-8")
    (root / "shared" / "x.lgd").write_text("#### Shared\n\nFor {{placeholder: name, type=text}}.\n", encoding="utf-8")
    (root / "doc" / "main.lgd").write_text(_FRONT + _MAIN, encoding="utf-8")
    return root, root / "doc" / "main.lgd"


def test_a_nested_path_that_leaves_the_directory_is_read_within_a_root_and_refused_without_one(tmp_path):
    root, path = _on_disk(tmp_path)
    (root / "doc" / "sub" / "a.lgd").write_text("### A\n\n{{include: ../../shared/x.lgd}}\n", encoding="utf-8")
    result = load_template(path, document_root=root).form({"name": "Ann"}).assemble()
    assert result.ok
    # The key keeps the `../`, as a flat one does, and the include line is as written.
    assert sorted(result.files) == ["../shared/x.lgd", "sub/a.lgd"]
    assert result.files["../shared/x.lgd"] == "#### Shared\n\nFor Ann.\n"
    assert result.files["sub/a.lgd"].endswith("{{include: ../../shared/x.lgd}}\n")
    without = load_template(path).form({"name": "Ann"}).assemble()
    assert _found(without) == [("include-file-missing", "sub/a.lgd", 3)]
    _refused(without)


def test_a_nested_path_that_leaves_the_root_is_refused(tmp_path):
    root, path = _on_disk(tmp_path)
    (root / "doc" / "sub" / "a.lgd").write_text("### A\n\n{{include: ../../../outside.lgd}}\n", encoding="utf-8")
    result = load_template(path, document_root=root).form({}).assemble()
    assert _found(result) == [("include-file-missing", "sub/a.lgd", 3)]
    _refused(result)


# ── What stays refused ────────────────────────────────────────────────────────

def test_a_templates_nested_include_is_template_fragment_invalid_as_it_was():
    main = "# Doc {#doc}\n\n{{include: sub/a.lgd}}\n"
    files = {"sub/a.lgd": "### A {#a}\n\n{{include: b.lgd}}\n", "sub/b.lgd": "#### B {#b}\n"}
    result = _assembled(main, files, {"x": True}, front=_TEMPLATE_FRONT)
    assert {rule for rule, *_ in _found(result)} == {"template-fragment-invalid"}
    _refused(result)


@pytest.mark.parametrize("text", ["{{include: g.lgd}}", "> [!DRAFTING]\n> {{include: g.lgd}}"])
def test_an_include_in_an_attachment_file_of_an_ordinary_document_is_still_refused(text):
    front = _FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n---\n\n")
    files = {"s.lgd": f"## B\n\n{text}\n\n", "g.lgd": "### G\n"}
    result = _assembled("# A\n\nSee {{attach: s}}.\n", files, front=front)
    if text.startswith(">"):
        assert result.ok  # a drafting note is removed with the include in it, unread (§15.6)
        return
    assert _found(result) == [("include-file-missing", "s.lgd", 3)]
    assert "attached file" in result.diagnostics[0].message
    _refused(result)


def test_an_attachment_file_that_is_also_an_include_fragment_is_still_an_attachment_file():
    front = _FRONT.replace("---\n\n", "attachments:\n  - id: s\n    title: S\n    file: s.lgd\n---\n\n")
    files = {"s.lgd": "## B\n\n{{include: g.lgd}}\n", "g.lgd": "### G\n"}
    # As a fragment alone its include is followed ...
    assert _assembled("# A\n\n{{include: s.lgd}}\n", files).ok
    # ... but it is attached too, and an attachment file holds no include for assembly.
    result = _assembled("# A\n\nSee {{attach: s}}.\n\n{{include: s.lgd}}\n", files, front=front)
    assert _found(result) == [("include-file-missing", "s.lgd", 3)]
    assert "attached file" in result.diagnostics[0].message
    _refused(result)
    # Attached twice, or by two items, it is said once.
    twice = front.replace("file: s.lgd\n", "file: s.lgd\n  - id: t\n    title: T\n    file: s.lgd\n")
    assert len(_assembled("# A\n\n{{include: s.lgd}}\n", files, front=twice).diagnostics) == 1


def test_template_problems_hold_the_cycle_once(tmp_path):
    files = {"sub/a.lgd": "### A\n\n{{include: b.lgd}}\n", "sub/b.lgd": "#### B\n\n{{include: a.lgd}}\n"}
    template = Template(_FRONT + _MAIN, resolve=files.get, path=tmp_path / "main.lgd")
    assert [(d.rule, d.file, d.line) for d in template.problems] == [("include-cycle", "sub/b.lgd", 3)]
    assert not template.form({}).ready


# ── The command ───────────────────────────────────────────────────────────────

def _project(tmp_path, b: str = "#### B\n\nText.\n"):
    (tmp_path / "sub").mkdir()
    (tmp_path / "doc.lgd").write_text(_FRONT + _MAIN, encoding="utf-8")
    (tmp_path / "sub" / "a.lgd").write_text("### A\n\n{{include: b.lgd}}\n", encoding="utf-8")
    (tmp_path / "sub" / "b.lgd").write_text(b, encoding="utf-8")
    return tmp_path / "doc.lgd"


def test_the_command_writes_every_nested_fragment_at_its_path(tmp_path):
    path = _project(tmp_path)
    assert main(["assemble", str(path), "-o", str(tmp_path / "out")]) == EXIT_OK
    out = tmp_path / "out"
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.lgd")) == ["doc.lgd", "sub/a.lgd", "sub/b.lgd"]
    assert (out / "sub" / "b.lgd").read_text(encoding="utf-8") == "#### B\n\nText.\n"
    assert (out / "doc.lgd").read_text(encoding="utf-8") == _FRONT + _MAIN


def test_the_command_refuses_to_print_a_document_that_has_nested_files(tmp_path, capsys):
    path = _project(tmp_path)
    assert main(["assemble", str(path)]) != EXIT_OK
    assert "other file(s)" in capsys.readouterr().err


def test_the_command_reports_a_cycle_and_writes_nothing(tmp_path, capsys):
    path = _project(tmp_path, b="#### B\n\n{{include: a.lgd}}\n")
    assert main(["assemble", str(path), "-o", str(tmp_path / "out")]) == EXIT_DIAGNOSTICS
    assert f"{tmp_path / 'sub' / 'b.lgd'}:3: error: [include-cycle]" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_the_command_reports_a_limit_and_writes_nothing(tmp_path, capsys):
    path = _project(tmp_path)
    for name, text in _chain(MAX_INCLUDE_DEPTH + 1).items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    path.write_text(_FRONT + "# Doc\n\n{{include: p0.lgd}}\n", encoding="utf-8")
    assert main(["assemble", str(path), "-o", str(tmp_path / "out")]) == EXIT_DIAGNOSTICS
    assert f"[{INCLUDE_LIMIT_RULE}]" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()
