"""Files read under the document root (§2.3, #109): with a root configured, a path that
leads out of the document's directory with ``../`` and stays within the root is read like
any other, for includes, attachment files and ``amends.file`` alike, by ``validate``, by
assembly and by the command line. Nothing outside the root is ever read, not even beside a
document that is itself outside it. Without a root such a path is valid and not read."""
from __future__ import annotations

from pathlib import Path

import pytest

from legaldown import Template, load, load_template, parse, parse_template, validate
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_ERROR, EXIT_OK, main
from legaldown.files import Reach, file_loader, relative_path, relative_path_problem, within

_FRONT = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    label: Provider\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    label: Client\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)
_RULES = {
    "include-file-missing", "include-has-frontmatter", "include-has-h1", "include-heading-skip",
    "path-outside-root", "path-not-relative", "amend-term-undefined", "amend-term-unresolvable",
    "attachment-file-missing",
}
_ORIGINAL = '---\ntitle: O\n---\n\n# S\n\n"Services" {{def: services}} means x.\n'
_EMPTY_ORIGINAL = "---\ntitle: O\n---\n\n# S\n\nText.\n"
_AMENDMENT = "---\ntitle: A\namends:\n  title: Original\n  file: {file}\n---\n\n# S\n\nUses {{{{term: services}}}}.\n"
_ATTACHED = (
    "---\ntitle: A\nattachments:\n  - id: sched\n    title: Schedule\n    file: {file}\n---\n\n"
    "# S\n\nSee {{{{attach: sched}}}}. Uses {{{{term: services}}}}.\n"
)


class _Loader:
    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = files or {}
        self.asked: list[str] = []

    def __call__(self, path: str) -> str | None:
        self.asked.append(path)
        return self.files.get(path)


def _found(result, *rules: str) -> list[tuple[str, str, int | None]]:
    wanted = set(rules) or _RULES
    return [(d.rule, d.file, d.line) for d in result.diagnostics if d.rule in wanted]


@pytest.fixture
def tree(tmp_path):
    """``root/shared/`` and ``root/doc/`` (the documents), with ``outside/`` beside ``root/``."""
    for name in ("root/shared", "root/doc", "outside"):
        (tmp_path / name).mkdir(parents=True)
    return tmp_path


def _write(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


# ── relative_path ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("path", "depth", "relative"),
    [
        ("../a.lgd", None, None), ("../a.lgd", 0, None), ("../a.lgd", 1, "../a.lgd"), ("../a.lgd", 2, "../a.lgd"),
        ("../../a.lgd", 1, None), ("../../a.lgd", 2, "../../a.lgd"),
        ("sub/../../a.lgd", 0, None), ("sub/../../a.lgd", 1, "../a.lgd"), ("sub/../../a.lgd", 2, "../a.lgd"),
        ("../sub/./a.lgd", 1, "../sub/a.lgd"),
        # The rules that stay: a directory, an absolute path, a backslash, a null byte.
        ("..", 2, None), (".", 2, None), ("../", 2, None), ("/a.lgd", 2, None), ("../..\\a.lgd", 2, None),
        ("..\\a.lgd", 2, None), ("../a\0.lgd", 2, None), ("C:/a.lgd", 2, None), ("https://x/a.lgd", 2, None),
        ("a.lgd", 0, "a.lgd"), ("a/../b.lgd", 0, "b.lgd"),
    ],
)
def test_a_path_that_leads_out_is_one_to_read_when_it_stays_within_the_root(path, depth, relative):
    assert relative_path(path, depth=depth) == relative
    assert Reach(None if depth is None else Path("/root"), depth).relative(path) == relative


def test_why_a_path_is_refused_depends_on_the_depth():
    assert relative_path_problem("../a.lgd") == "leads-out"
    assert relative_path_problem("../a.lgd", depth=0) == "leads-out"
    assert relative_path_problem("../a.lgd", depth=1) is None
    assert relative_path_problem("..", depth=1) == "invalid"  # a directory
    assert relative_path_problem("..", depth=0) == "leads-out"


def test_a_document_outside_the_root_reaches_nothing(tmp_path):
    reach = Reach.of(tmp_path / "root", tmp_path / "elsewhere")
    assert reach.depth is None
    assert reach.check("a.lgd") == (None, "leads-out") and reach.check("../root/a.lgd") == (None, "leads-out")
    assert Reach.of(tmp_path / "root", tmp_path / "root" / "doc").check("../a.lgd") == ("../a.lgd", None)
    assert Reach.of(tmp_path / "root", None).check("../a.lgd") == (None, "leads-out")  # a parsed text is at the root
    assert Reach().check("../a.lgd") == (None, "leads-out") and Reach().check("a.lgd") == ("a.lgd", None)


# ── file_loader ──────────────────────────────────────────────────────────────

def test_a_loader_with_a_root_reads_a_file_that_leads_out_of_its_base_within_the_root(tree):
    _write(tree / "root" / "shared" / "x.lgd", "shared")
    _write(tree / "outside" / "y.lgd", "outside")
    load_file = file_loader(tree / "root" / "doc", root=tree / "root")
    assert load_file("../shared/x.lgd") == "shared"
    assert load_file("sub/../../shared/x.lgd") == "shared"
    # Nothing beyond the root, however it is written, and no absolute path.
    assert load_file("../../outside/y.lgd") is None
    assert load_file(str(tree / "outside" / "y.lgd")) is None
    assert load_file("../..") is None
    # Without the root, as before.
    assert file_loader(tree / "root" / "doc")("../shared/x.lgd") is None


def test_a_loader_with_a_root_reads_nothing_beside_a_base_that_is_outside_it(tree):
    _write(tree / "outside" / "y.lgd", "outside")
    load_file = file_loader(tree / "outside", root=tree / "root")
    assert load_file("y.lgd") is None
    assert within(tree / "outside", "y.lgd", tree / "root") is None
    assert within(tree / "outside", "y.lgd") == (tree / "outside" / "y.lgd").resolve()
    # Not even by a path that leads back into the root.
    _write(tree / "root" / "shared" / "x.lgd", "shared")
    assert load_file("../root/shared/x.lgd") == "shared"  # the target is within the root: that is the test
    assert load_file("../outside/y.lgd") is None


def test_a_symbolic_link_to_a_file_outside_the_root_is_not_read_even_from_within_it(tree):
    _write(tree / "outside" / "y.lgd", "outside")
    try:
        (tree / "root" / "shared" / "link.lgd").symlink_to(tree / "outside" / "y.lgd")
        (tree / "root" / "dirlink").symlink_to(tree / "outside")
    except OSError:
        pytest.skip("symbolic links are not available")
    load_file = file_loader(tree / "root" / "doc", root=tree / "root")
    assert load_file("../shared/link.lgd") is None and load_file("../dirlink/y.lgd") is None
    _write(tree / "root" / "shared" / "inside.lgd", "fine")
    (tree / "root" / "shared" / "alias.lgd").symlink_to(tree / "root" / "shared" / "inside.lgd")
    assert load_file("../shared/alias.lgd") == "fine"


# ── validate ─────────────────────────────────────────────────────────────────

def _document(tree, body: str, name: str = "main.lgd", where: str = "root/doc"):
    return load(_write(tree / where / name, _FRONT + "# Scope {#scope}\n\n" + body))


def test_a_fragment_at_a_parent_directory_within_the_root_is_read_and_checked(tree):
    _write(tree / "root" / "shared" / "part.lgd", "Body.\n\n### Too deep {#too-deep}\n\nText.\n")
    _write(tree / "root" / "shared" / "frame.lgd", "---\ntitle: No\n---\n\n# Heading {#heading}\n\nText.\n")
    document = _document(tree, "{{include: ../shared/part.lgd}}\n\n{{include: ../shared/frame.lgd}}\n\n{{include: ../shared/none.lgd}}\n")
    result = validate(document, document_root=tree / "root")
    # Each at its line in the file it is in; the file is named by its path from the document, with the ../.
    assert sorted(_found(result)) == sorted([
        ("include-has-frontmatter", "../shared/frame.lgd", 2),
        ("include-has-h1", "../shared/frame.lgd", 5),
        ("include-heading-skip", "../shared/part.lgd", 3),
        ("include-file-missing", "main.lgd", 25),
    ])


def test_without_a_root_a_fragment_at_a_parent_directory_is_valid_and_not_read(tree):
    _write(tree / "root" / "shared" / "part.lgd", "Body.\n\n### Too deep {#too-deep}\n\nText.\n")
    document = _document(tree, "{{include: ../shared/part.lgd}}\n")
    loader = _Loader()
    for options in ({"resolve": loader}, {}):
        result = validate(document, **options)
        assert _found(result) == []
        assert any(d.rule == "ldv-check-skipped" and "not read without a document root" in d.message
                   for d in result.diagnostics)
    assert loader.asked == []  # a custom resolver is never asked for it


def test_a_custom_resolver_is_asked_for_a_parent_path_with_a_root_and_not_beyond_it(tree):
    loader = _Loader({"../shared/part.lgd": "Body.\n"})
    document = _document(tree, "{{include: ../shared/part.lgd}}\n\n{{include: ../../outside/y.lgd}}\n")
    result = validate(document, resolve=loader, document_root=tree / "root")
    assert loader.asked == ["../shared/part.lgd"]
    assert _found(result) == [("path-outside-root", "main.lgd", 23)]


def test_a_document_outside_the_root_reads_nothing_and_is_reported_once(tree):
    loader = _Loader({name: _ORIGINAL for name in ("orig.lgd", "att.lgd", "frag.lgd", "../root/shared/x.lgd")})
    text = (
        "---\ntitle: A\namends:\n  title: Original\n  file: orig.lgd\n"
        "attachments:\n  - id: sched\n    title: Schedule\n    file: att.lgd\n---\n\n"
        "# S\n\nSee {{attach: sched}}. Uses {{term: services}}.\n\n{{include: frag.lgd}}\n\n{{include: ../root/shared/x.lgd}}\n"
    )
    document = load(_write(tree / "outside" / "amend.lgd", text))
    result = validate(document, resolve=loader, document_root=tree / "root")
    assert loader.asked == []
    # The original was not read, so its term is unresolvable (Info), not undefined.
    assert sorted(rule for rule, _file, _line in _found(result)) == ["amend-term-unresolvable", "path-outside-root"]
    # The same through the default loader: the files beside the document are there, and not read.
    for name in ("orig.lgd", "att.lgd", "frag.lgd"):
        _write(tree / "outside" / name, _EMPTY_ORIGINAL)
    result = validate(document, document_root=tree / "root")
    assert sorted(rule for rule, _file, _line in _found(result)) == ["amend-term-unresolvable", "path-outside-root"]


def test_amends_file_and_an_attachment_file_at_a_parent_directory_give_their_definitions(tree):
    _write(tree / "root" / "shared" / "orig.lgd", _ORIGINAL)
    amended = load(_write(tree / "root" / "doc" / "a.lgd", _AMENDMENT.format(file="../shared/orig.lgd")))
    # Not read without a root: the term is unresolvable (Info), as for any original not read.
    assert "amend-term-unresolvable" in validate(amended).rules()
    result = validate(amended, document_root=tree / "root")
    assert not {"amend-term-undefined", "amend-term-unresolvable"} & set(result.rules())
    # An original read from there is checked: it does not define the term.
    _write(tree / "root" / "shared" / "orig.lgd", _EMPTY_ORIGINAL)
    assert "amend-term-undefined" in validate(amended, document_root=tree / "root").rules("error")
    _write(tree / "root" / "shared" / "att.lgd", '"Services" {{def: services}} means x.\n')
    attached = load(_write(tree / "root" / "doc" / "b.lgd", _ATTACHED.format(file="../shared/att.lgd")))
    assert "term-undefined" in validate(attached).rules("error")
    assert "term-undefined" not in validate(attached, document_root=tree / "root").rules()


def test_amends_file_beside_a_document_outside_the_root_is_not_read(tree):
    # outside/amend.lgd amends outside/orig.lgd: the root is not the document's tree.
    _write(tree / "outside" / "orig.lgd", _ORIGINAL)
    document = load(_write(tree / "outside" / "amend.lgd", _AMENDMENT.format(file="orig.lgd")))
    result = validate(document, document_root=tree / "root")
    assert sorted(rule for rule, _file, _line in _found(result)) == ["amend-term-unresolvable", "path-outside-root"]
    loader = _Loader({"orig.lgd": _ORIGINAL})
    validate(document, document_root=tree / "root", resolve=loader)
    assert loader.asked == []


def test_a_document_that_includes_itself_by_a_parent_path_adds_nothing(tree):
    path = _write(tree / "root" / "doc" / "main.lgd", _FRONT + "# Scope {#scope}\n\n{{include: ../doc/main.lgd}}\n")
    result = validate(load(path), document_root=tree / "root")
    assert _found(result) == []


def test_a_fragment_validated_on_its_own_still_reads_nothing(tree):
    document = parse("## A {#a}\n\n{{include: ../shared/part.lgd}}\n", filename="f.lgd")
    loader = _Loader({"../shared/part.lgd": "Body.\n"})
    validate(document, fragment=True, resolve=loader, document_root=tree / "root")
    assert loader.asked == []


# ── assembly ─────────────────────────────────────────────────────────────────

_TEMPLATE = (
    "---\ntitle: T\nquestions:\n  who:\n    type: text\n---\n\n# Scope {#scope}\n\n{{include: ../shared/part.lgd}}\n"
)
_PART = "Hello {{placeholder: who}}.\n"


def test_assembly_reads_a_fragment_at_a_parent_directory_with_a_root(tree):
    _write(tree / "root" / "shared" / "part.lgd", _PART)
    path = _write(tree / "root" / "doc" / "t.lgd", _TEMPLATE)
    template = load_template(path, document_root=tree / "root")
    assert template.problems == ()
    result = template.form({"who": "Ann"}).assemble()
    assert result.ok and list(result.files) == ["../shared/part.lgd"]
    assert result.files["../shared/part.lgd"] == "Hello Ann.\n"
    # Without a root it is refused, as any file that cannot be read.
    assert [d.rule for d in load_template(path).problems] == ["include-file-missing"]
    # And a template outside the root reads nothing.
    outside = _write(tree / "outside" / "t.lgd", _TEMPLATE)
    assert [d.rule for d in load_template(outside, document_root=tree / "root").problems][:1] == ["include-file-missing"]


def test_a_template_made_from_text_is_at_the_root_unless_it_is_given_a_path(tree):
    loader = _Loader({"../shared/part.lgd": _PART})
    assert [d.rule for d in parse_template(_TEMPLATE, resolve=loader, document_root=tree / "root").problems] == [
        "include-file-missing"
    ]
    assert loader.asked == []
    template = parse_template(
        _TEMPLATE, resolve=loader, document_root=tree / "root", path=tree / "root" / "doc" / "t.lgd"
    )
    assert template.problems == () and loader.asked == ["../shared/part.lgd"]
    assert template.path == tree / "root" / "doc" / "t.lgd"
    assert Template(_TEMPLATE, resolve=loader).problems[0].rule == "include-file-missing"


def test_a_templates_validation_judges_its_paths_by_the_root_too(tree):
    _write(tree / "root" / "shared" / "part.lgd", _PART)
    path = _write(tree / "root" / "doc" / "t.lgd", _TEMPLATE)
    assert _found(load_template(path, document_root=tree / "root").validation) == []
    assert _found(load_template(path, document_root=tree / "root" / "doc").validation)[0][0] == "path-outside-root"


# ── the command line ─────────────────────────────────────────────────────────

def test_the_command_line_validates_assembles_and_asks_with_a_document_root(tree, capsys):
    _write(tree / "root" / "shared" / "part.lgd", _PART)
    template = _write(tree / "root" / "doc" / "t.lgd", _TEMPLATE)
    answers = _write(tree / "answers.yaml", "who: Ann\n")
    root = str(tree / "root")
    assert main(["validate", str(template), "--document-root", root]) == EXIT_OK
    assert "[include-file-missing]" not in capsys.readouterr().out
    # Without it, the include is skipped, and says so in the Warning.
    assert main(["validate", str(template)]) == EXIT_OK
    assert "not read without a document root" in capsys.readouterr().out
    assert main(["questions", str(template), "--document-root", root, "--format", "json", "--answers", str(answers)]) == EXIT_OK
    assert '"id": "who"' in capsys.readouterr().out
    assert main(["questions", str(template)]) == EXIT_DIAGNOSTICS
    capsys.readouterr()
    assert main(["assemble", str(template), "--document-root", root, "--answers", str(answers)]) == EXIT_ERROR
    err = capsys.readouterr().err
    # The kept file leads out of the template's directory, so it is not written to a directory (-o is needed).
    assert "assembly also writes 1 other file(s)" in err
    assert main(["assemble", str(template), "--answers", str(answers)]) == EXIT_DIAGNOSTICS
    assert "[include-file-missing]" in capsys.readouterr().err


def test_the_output_option_still_refuses_a_file_that_leads_out_of_the_output_directory(tree, capsys):
    _write(tree / "root" / "shared" / "part.lgd", _PART)
    template = _write(tree / "root" / "doc" / "t.lgd", _TEMPLATE)
    answers = _write(tree / "answers.yaml", "who: Ann\n")
    out = tree / "out"
    code = main(["assemble", str(template), "--document-root", str(tree / "root"), "--answers", str(answers),
                 "-o", str(out)])
    assert code == EXIT_ERROR
    assert "../shared/part.lgd leads out of the output directory; nothing was written" in capsys.readouterr().err
    assert not out.exists()


def test_a_finding_in_a_parent_fragment_is_located_without_the_detour(tree, capsys):
    _write(tree / "root" / "shared" / "part.lgd", "Body.\n\n### Too deep {#too-deep}\n\nText.\n")
    document = _document(tree, "{{include: ../shared/part.lgd}}\n")
    assert main(["validate", str(document.path), "--document-root", str(tree / "root")]) == EXIT_DIAGNOSTICS
    out = capsys.readouterr().out
    assert f"{tree / 'root' / 'shared' / 'part.lgd'}:3: error: [include-heading-skip]" in out
    assert ".." not in out.split("[include-heading-skip]")[0].splitlines()[-1]


# ── A template that is given no loader reads nothing ────────────────────────

def test_a_template_with_a_path_and_no_loader_reads_nothing_in_validation_either(tree):
    # The fragment is on disk, beside the template, and has frontmatter: it would be reported if it were read.
    _write(tree / "root" / "doc" / "part.lgd", "---\ntitle: No\n---\n\nText.\n")
    text = "---\ntitle: T\n---\n\n# Scope {#scope}\n\n{{include: part.lgd}}\n\n{{include: ../shared/x.lgd}}\n"
    path = tree / "root" / "doc" / "t.lgd"
    for options in ({}, {"document_root": tree / "root"}):
        template = parse_template(text, path=path, **options)
        rules = {d.rule for d in template.validation.diagnostics}
        assert not rules & {"include-has-frontmatter", "include-file-missing"}, rules
        assert "ldv-check-skipped" in rules
        # Assembly reads nothing either, and says so: the two agree on what was read.
        assert {d.rule for d in template.problems} == {"include-file-missing"}
    # The root and the path are still what judges its paths.
    rooted = parse_template(text, path=path, document_root=tree / "root" / "doc")
    assert "path-outside-root" in {d.rule for d in rooted.validation.diagnostics}


def test_a_relative_root_is_made_absolute_when_it_is_given(tree, monkeypatch):
    _write(tree / "root" / "shared" / "x.lgd", "shared")
    monkeypatch.chdir(tree)
    loader = file_loader("root/doc", root="root")
    reach = Reach.of("root", "root/doc")
    monkeypatch.chdir(tree / "outside")
    assert loader("../shared/x.lgd") == "shared"
    assert reach.root == tree / "root" and reach.depth == 1
