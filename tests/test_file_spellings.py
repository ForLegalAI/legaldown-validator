"""One file named by two paths (#123): ``ldv-file-spelling-duplicate``.

Since #109 a document under a document root can name one file two ways: from ``root/doc/t.lgd``,
``a.lgd`` and ``../doc/a.lgd`` are the same file. Assembly writes each file it keeps by the path that
names it, so it cannot write one file by two; the second path to a file already named, by an include or
a LegalDown attachment file, is an Error at that path's own line, of the validator and of assembly alike,
whichever path comes first. Which paths are one file is the loader's to say: ``file_loader`` follows
symbolic links, so a linked directory can make two paths that read alike two files, and a link to a file
is that file; a loader that cannot say is taken to read a path from the document's directory as written."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from legaldown import Template, load, load_template, parse, validate
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_OK, main
from legaldown.files import file_loader, loader_identity
from legaldown.grammar import FILE_SPELLING_DUPLICATE_RULE, IMPLEMENTATION_RULE_PREFIX

RULE = "ldv-file-spelling-duplicate"
_SUFFIX = " The template is not assembled."
_TEMPLATE_FRONT = "---\ntitle: T\nquestions:\n  who:\n    type: text\n  x:\n    type: boolean\n"
_PART = "Hello {{placeholder: who}}.\n"
_ANSWERS = {"who": "Ann", "x": True}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _line(text: str, needle: str, nth: int = 1) -> int:
    """The line of the *nth* line of *text* that holds *needle*."""
    found = [number for number, line in enumerate(text.splitlines(), 1) if needle in line]
    return found[nth - 1]


def _template(body: str, extra: str = "") -> str:
    return _TEMPLATE_FRONT + extra + "---\n\n# Scope {#scope}\n\n" + body


def _validated(result) -> list[tuple[int | None, str]]:
    return [(d.line, d.message) for d in result.diagnostics if d.rule == RULE]


def _refused(template: Template) -> list[tuple[int | None, str]]:
    found = [(d.line, d.message) for d in template.problems if d.rule == RULE]
    assert all(message.endswith(_SUFFIX) for _line, message in found)
    return [(line, message.removesuffix(_SUFFIX)) for line, message in found]


def _link(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:  # no links here
        pytest.skip("cannot make a symbolic link")


def test_the_rule_is_this_implementations_own():
    assert FILE_SPELLING_DUPLICATE_RULE == RULE and RULE.startswith(IMPLEMENTATION_RULE_PREFIX)


# ── Two paths to one include ───────────────────────────────────────────────────

@pytest.mark.parametrize(("first", "second"), [("a.lgd", "../doc/a.lgd"), ("../doc/a.lgd", "a.lgd")])
def test_the_second_path_is_an_error_of_validation_and_of_assembly_in_either_order(tmp_path, first, second):
    root = tmp_path / "root"
    _write(root / "doc" / "a.lgd", _PART)
    text = _template(f"{{{{include: {first}}}}}\n\n{{{{include: {second}}}}}\n")
    path = _write(root / "doc" / "t.lgd", text)
    line = _line(text, f"{{{{include: {second}}}}}")
    # The validator: an Error at the second path's line, naming the first.
    validated = _validated(validate(load(path), document_root=root))
    assert [found_line for found_line, _ in validated] == [line]
    assert f"'{second}' names the file already named '{first}'" in validated[0][1]
    assert next(d.level for d in validate(load(path), document_root=root).diagnostics if d.rule == RULE) == "error"
    # Assembly: the same finding, at the same line, and nothing is assembled (no output naming a file it
    # did not write, and no file written twice under two names).
    template = load_template(path, document_root=root)
    assert _refused(template) == validated
    assert [d.rule for d in template.problems] == [RULE]
    result = template.form(_ANSWERS).assemble()
    assert not result.ok and result.output == "" and result.files == {}


@pytest.mark.parametrize("order", ["plain-first", "parent-first"])
def test_the_command_line_refuses_the_template_with_the_finding_in_either_order_and_writes_nothing(
    tmp_path, capsys, order
):
    root = tmp_path / "root"
    _write(root / "doc" / "a.lgd", _PART)
    spellings = ["a.lgd", "../doc/a.lgd"] if order == "plain-first" else ["../doc/a.lgd", "a.lgd"]
    text = _template("".join(f"{{{{include: {each}}}}}\n\n" for each in spellings))
    path = _write(root / "doc" / "t.lgd", text)
    answers = _write(tmp_path / "answers.yaml", "who: Ann\nx: true\n")
    out = tmp_path / "out"
    assert main(["validate", str(path), "--document-root", str(root)]) == EXIT_DIAGNOSTICS
    assert f"[{RULE}]" in capsys.readouterr().out
    code = main(["assemble", str(path), "--document-root", str(root), "--answers", str(answers), "-o", str(out)])
    err = capsys.readouterr().err
    assert code == EXIT_DIAGNOSTICS
    assert f"t.lgd:{_line(text, '{{include: ' + spellings[1] + '}}')}: error: [{RULE}]" in err
    assert "leads out of the output directory" not in err and not out.exists()
    # Named by one path (once: a template includes a fragment in one place, §15.3), it is assembled and written.
    _write(path, _template("{{include: a.lgd}}\n"))
    assert main(["assemble", str(path), "--document-root", str(root), "--answers", str(answers), "-o", str(out)]) == EXIT_OK
    assert (out / "a.lgd").read_text(encoding="utf-8") == "Hello Ann.\n"


def test_a_custom_loader_that_cannot_say_reads_a_path_from_the_documents_directory():
    # No identity: ``a.lgd`` and ``../doc/a.lgd`` from ``doc`` are one path, and one file.
    files = {"a.lgd": _PART, "../doc/a.lgd": _PART}
    text = _template("{{include: a.lgd}}\n\n{{include: ../doc/a.lgd}}\n")
    where = Path("/r/doc/t.lgd")
    document = parse(text, filename="t.lgd")
    validated = _validated(validate(replace(document, path=where), resolve=files.get, document_root="/r"))
    assert [line for line, _ in validated] == [_line(text, "../doc/a.lgd")]
    template = Template(text, resolve=files.get, document_root="/r", path=where)
    assert _refused(template) == validated


def test_a_path_written_another_way_that_normalises_alike_is_the_same_path():
    # ``./a.lgd`` and ``a.lgd`` are one path (``relative_path``): no finding, and the file is written as
    # ``a.lgd``; the include keeps its spelling and the attachment its ``file``, which the include's spelling
    # does not replace. (In a document that is not a template: in one, a file both included and attached is
    # template-fragment-invalid, §15.3, below.)
    files = {"a.lgd": "Hello.\n"}
    text = (
        "---\ntitle: D\nattachments:\n  - id: sch\n    title: Schedule\n    file: a.lgd\n---\n\n"
        "# Scope {#scope}\n\n{{include: ./a.lgd}}\n\nSee {{attach: sch}}.\n"
    )
    assert _validated(validate(parse(text), resolve=files.get)) == []
    template = Template(text, resolve=files.get)
    assert template.problems == ()
    result = template.form({}).assemble()
    assert result.ok and result.files == {"a.lgd": "Hello.\n"}
    assert "{{include: ./a.lgd}}" in result.output and "    file: a.lgd\n" in result.output


# ── An include and an attachment ───────────────────────────────────────────────

@pytest.mark.parametrize("when", ["", "    when: x\n"])
def test_an_attachment_naming_an_included_file_by_another_path_is_the_finding_at_its_file_entry(tmp_path, when):
    # The include and a (conditional) attachment would otherwise be two names for one entry of what is kept.
    root = tmp_path / "root"
    _write(root / "doc" / "a.lgd", "Hello.\n")
    extra = f"attachments:\n  - id: sch\n    title: Schedule\n    file: ../doc/a.lgd\n{when}"
    text = _template("{{include: a.lgd}}\n\nSee {{attach: sch}}.\n", extra)
    path = _write(root / "doc" / "t.lgd", text)
    validated = _validated(validate(load(path), document_root=root))
    assert [line for line, _ in validated] == [_line(text, "file: ../doc/a.lgd")]
    assert "'../doc/a.lgd' names the file already named 'a.lgd'" in validated[0][1]
    template = load_template(path, document_root=root)
    assert _refused(template) == validated
    for x in (True, False):
        result = template.form({"who": "Ann", "x": x}).assemble()
        assert not result.ok and result.files == {}


def test_an_include_naming_an_attached_file_by_another_path_is_the_finding_at_the_include(tmp_path):
    # The includes are met first, then the attachments: the include is first, whatever the lines.
    root = tmp_path / "root"
    _write(root / "doc" / "a.lgd", "Hello.\n")
    extra = "attachments:\n  - id: one\n    title: One\n    file: a.lgd\n  - id: two\n    title: Two\n    file: ../doc/a.lgd\n"
    text = _template("{{include: ../doc/a.lgd}}\n\nSee {{attach: one}} and {{attach: two}}.\n", extra)
    path = _write(root / "doc" / "t.lgd", text)
    validated = _validated(validate(load(path), document_root=root))
    # The first entry names it another way than the include; the second as the include does.
    assert [line for line, _ in validated] == [_line(text, "file: a.lgd")]
    assert "'a.lgd' names the file already named '../doc/a.lgd'" in validated[0][1]
    assert _refused(load_template(path, document_root=root)) == validated


def test_in_a_template_the_same_path_twice_is_template_fragment_invalid_and_not_this_finding():
    # By the text of the paths, the Core check of §15.3; a second path to the file is this finding.
    files = {"a.lgd": _PART}
    extra = "attachments:\n  - id: sch\n    title: Schedule\n    file: a.lgd\n    when: x\n"
    for text in (
        _template("{{include: a.lgd}}\n\nSee {{attach: sch}}.\n", extra),
        _template("{{include: a.lgd}}\n\n{{include: ./a.lgd}}\n"),
    ):
        errors = {d.rule for d in validate(parse(text), resolve=files.get).diagnostics if d.level == "error"}
        assert "template-fragment-invalid" in errors and RULE not in errors
        problems = {d.rule for d in Template(text, resolve=files.get).problems}
        assert "template-fragment-invalid" in problems and RULE not in problems


# ── Nested includes (not a template) ───────────────────────────────────────────

_DOCUMENT = "---\ntitle: D\n---\n\n# Scope {#scope}\n\n"


def test_a_nested_include_naming_a_file_by_another_path_is_the_finding_in_the_fragment():
    # ``p/b.lgd`` includes ``../a.lgd``: from the document's directory that is ``a.lgd``, the path that named
    # the file first, so no finding. ``p/c.lgd`` names it ``../../doc/a.lgd``: ``../doc/a.lgd`` from there.
    files = {
        "a.lgd": "A.\n",
        "p/b.lgd": "B.\n\n{{include: ../a.lgd}}\n",
        "p/c.lgd": "C.\n\n{{include: ../../doc/a.lgd}}\n",
        "../doc/a.lgd": "A.\n",
    }
    text = _DOCUMENT + "{{include: a.lgd}}\n\n{{include: p/b.lgd}}\n\n{{include: p/c.lgd}}\n"
    document = replace(parse(text, filename="t.lgd"), path=Path("/r/doc/t.lgd"))
    result = validate(document, resolve=files.get, document_root="/r")
    found = [(d.file, d.line) for d in result.diagnostics if d.rule == RULE]
    assert found == [("p/c.lgd", 3)]
    template = Template(text, resolve=files.get, document_root="/r", path=Path("/r/doc/t.lgd"))
    assert [(d.rule, d.file, d.line) for d in template.problems] == [(RULE, "p/c.lgd", 3)]


# ── Sameness is the loader's ───────────────────────────────────────────────────

def test_the_file_loader_says_which_file_it_reads(tmp_path):
    root = tmp_path / "root"
    _write(root / "doc" / "a.lgd", "A.\n")
    load_file = file_loader(root / "doc", root=root)
    assert loader_identity(load_file, "a.lgd") == loader_identity(load_file, "../doc/a.lgd")
    assert loader_identity(load_file, "a.lgd") == str((root / "doc" / "a.lgd").resolve())
    assert loader_identity(load_file, "../../x.lgd") is None  # out of the root: no file is read
    assert loader_identity({}.get, "a.lgd") is None  # a loader that cannot say


def test_a_linked_directory_makes_two_paths_that_read_alike_two_files(tmp_path):
    # ``root/doc`` is a link to ``root/elsewhere/doc2``: from it, ``a.lgd`` is ``root/elsewhere/doc2/a.lgd`` and
    # ``../doc/a.lgd`` is ``root/elsewhere/doc/a.lgd`` (the link is followed before ``..``), as the loader reads them.
    # By their text they are one path; to the loader they are two files, each read and judged, and assembled.
    root = tmp_path / "root"
    _write(root / "elsewhere" / "doc2" / "a.lgd", "One {{placeholder: who}}.\n")
    _write(root / "elsewhere" / "doc" / "a.lgd", "Two {{placeholder: who}}.\n\n# Heading {#heading}\n")
    _link(root / "doc", root / "elsewhere" / "doc2", directory=True)
    text = _template("{{include: a.lgd}}\n\n{{include: ../doc/a.lgd}}\n")
    path = _write(root / "doc" / "t.lgd", text)
    result = validate(load(path), document_root=root)
    assert _validated(result) == []
    # The second file is read: its heading is its own finding, in it.
    assert [(d.rule, d.file) for d in result.diagnostics if d.rule == "include-has-h1"] == [
        ("include-has-h1", "../doc/a.lgd")
    ]
    template = load_template(path, document_root=root)
    assert not [d for d in template.problems if d.rule == RULE]
    _write(root / "elsewhere" / "doc" / "a.lgd", "Two {{placeholder: who}}.\n")
    result = load_template(path, document_root=root).form(_ANSWERS).assemble()
    assert result.ok and result.files == {"a.lgd": "One Ann.\n", "../doc/a.lgd": "Two Ann.\n"}


def test_a_link_to_a_file_already_named_is_that_file(tmp_path):
    root = tmp_path / "root"
    _write(root / "doc" / "a.lgd", _PART)
    _link(root / "doc" / "alias.lgd", root / "doc" / "a.lgd")
    text = _template("{{include: a.lgd}}\n\n{{include: alias.lgd}}\n")
    path = _write(root / "doc" / "t.lgd", text)
    validated = _validated(validate(load(path), document_root=root))
    assert [line for line, _ in validated] == [_line(text, "alias.lgd")]
    assert _refused(load_template(path, document_root=root)) == validated
