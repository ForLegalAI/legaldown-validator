"""CLI behavior: output formats, rule filtering, and exit codes."""
from __future__ import annotations

import json

import pytest

from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_ERROR, EXIT_OK, main

_VALID = """---
title: Fixture
document_type: contract
sides:
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
---

# Scope {#scope}

{{party: acme}} shall serve {{party: beta}}.
"""

_BROKEN = _VALID + "\nSee Section {{ref: nowhere}}.\n"


@pytest.fixture
def write(tmp_path):
    def _write(name: str, source: str):
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path
    return _write


def test_clean_document_exits_zero(write, capsys):
    path = write("ok.lgd", _VALID)
    assert main(["validate", str(path)]) == EXIT_OK
    assert "No issues found." in capsys.readouterr().err


def test_error_exits_one_and_reports_rule_id(write, capsys):
    path = write("broken.lgd", _BROKEN)
    assert main(["validate", str(path)]) == EXIT_DIAGNOSTICS
    assert "[ref-broken]" in capsys.readouterr().out


def test_json_format_is_machine_readable(write, capsys):
    path = write("broken.lgd", _BROKEN)
    main(["validate", "--format", "json", str(path)])
    payload = json.loads(capsys.readouterr().out)
    assert payload["legaldown_spec"] == "0.2"
    assert any(d["rule"] == "ref-broken" and d["level"] == "error"
               for d in payload["diagnostics"])


def test_ignore_suppresses_a_rule(write, capsys):
    path = write("broken.lgd", _BROKEN)
    code = main(["validate", "--ignore", "ref-broken", str(path)])
    assert code == EXIT_OK
    assert "ref-broken" not in capsys.readouterr().out


def test_warnings_as_errors_promotes_severity(write, capsys):
    # A money directive without currency is a warning by default.
    path = write("warn.lgd", _VALID + "\nPay {{money: 100}}.\n")
    assert main(["validate", str(path)]) == EXIT_OK
    assert main(["validate", "--warnings-as-errors", str(path)]) == EXIT_DIAGNOSTICS


def test_strict_fails_on_warnings_without_promoting(write):
    path = write("warn.lgd", _VALID + "\nPay {{money: 100}}.\n")
    assert main(["validate", "--strict", str(path)]) == EXIT_DIAGNOSTICS


def test_directory_is_walked(write, tmp_path, capsys):
    write("a.lgd", _BROKEN)
    write("b.lgd", _VALID)
    main(["validate", "--format", "json", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)
    assert {d["file"] for d in payload["diagnostics"]}  # at least one file reported


def test_missing_file_exits_two(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "nope.lgd")]) == EXIT_ERROR
    assert "cannot read" in capsys.readouterr().err


def test_malformed_frontmatter_is_reported_not_raised(write, capsys):
    path = write("bad.lgd", "---\ntitle: [unclosed\n---\n\n# Scope {#scope}\n")
    assert main(["validate", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostics"][0]["rule"] == "frontmatter-invalid-yaml"


def test_a_document_without_frontmatter_is_a_warning(write, capsys):
    path = write("bare.lgd", "# Scope {#scope}\n\nBody.\n")
    assert main(["validate", "--format", "json", str(path)]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert [(d["rule"], d["level"]) for d in payload["diagnostics"]] == [("frontmatter-absent", "warning")]


def test_final_rejects_a_remaining_blank(write, capsys):
    path = write("draft.lgd", _VALID + "\nThe fee is {{placeholder: fee, type=money}}.\n")
    assert main(["validate", str(path)]) == EXIT_OK
    capsys.readouterr()
    assert main(["validate", "--final", str(path)]) == EXIT_DIAGNOSTICS
    assert "[placeholder-unfilled]" in capsys.readouterr().out


# ── legaldown assemble (§15.7) ────────────────────────────────────

_TEMPLATE = _VALID.replace(
    "---\n\n# Scope", "questions:\n  x:\n    type: boolean\n---\n\n# Scope"
) + "\nExtra. {when=x}\n\nHi {{placeholder: who}}.\n"


def test_assemble_writes_the_template_to_stdout(write, capsys):
    template = write("t.lgd", _TEMPLATE)
    answers = write("a.yaml", "x: false\nwho: Ann\n")
    assert main(["assemble", str(template), "--answers", str(answers)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Extra." not in out and "Hi Ann." in out and "questions:" not in out


def test_assemble_keeps_crlf_line_breaks(write, tmp_path):
    template = tmp_path / "t.lgd"
    template.write_bytes(_TEMPLATE.replace("\n", "\r\n").encode())
    answers = write("a.yaml", "x: true\nwho: Ann\n")
    assert main(["assemble", str(template), "--answers", str(answers), "-o", str(tmp_path / "out")]) == EXIT_OK
    written = (tmp_path / "out" / "t.lgd").read_bytes()
    assert b"\r\n" in written and b"\n" not in written.replace(b"\r\n", b"")


def test_assemble_writes_kept_files_under_the_output_directory(tmp_path, capsys):
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "a.lgd").write_text("## Part {#part}\n\nFor {{placeholder: who}}.\n", encoding="utf-8")
    template = tmp_path / "t.lgd"
    template.write_text(_TEMPLATE + "\n{{include: parts/a.lgd}}\n", encoding="utf-8")
    answers = tmp_path / "a.yaml"
    answers.write_text("x: false\nwho: Ann\n", encoding="utf-8")
    assert main(["assemble", str(template), "--answers", str(answers)]) == EXIT_ERROR
    assert "-o" in capsys.readouterr().err
    out = tmp_path / "out"
    assert main(["assemble", str(template), "--answers", str(answers), "-o", str(out)]) == EXIT_OK
    assert (out / "parts" / "a.lgd").read_text(encoding="utf-8") == "## Part {#part}\n\nFor Ann.\n"
    assert main(["assemble", str(template), "--answers", str(answers), "-o", str(tmp_path)]) == EXIT_ERROR
    assert "overwrite the template" in capsys.readouterr().err


def test_assemble_reports_a_missing_answer(write, capsys):
    template = write("t.lgd", _TEMPLATE)
    assert main(["assemble", str(template)]) == EXIT_DIAGNOSTICS
    captured = capsys.readouterr()
    assert captured.out == "" and "[answer-missing]" in captured.err


@pytest.mark.parametrize("answers", ["x: [unclosed\n", "when: 2026-13-45\n", "- a\n- b\n"])
def test_assemble_refuses_unreadable_answers(write, capsys, answers):
    template = write("t.lgd", _TEMPLATE)
    path = write("a.yaml", answers)
    assert main(["assemble", str(template), "--answers", str(path)]) == EXIT_ERROR
    assert capsys.readouterr().err.startswith("error: ")


@pytest.mark.parametrize("include", ["../outside.lgd", "/etc/hostname"])
def test_assemble_reads_no_file_outside_the_template_directory(tmp_path, capsys, include):
    (tmp_path / "outside.lgd").write_text("## Outside {#outside}\n\nText.\n", encoding="utf-8")
    folder = tmp_path / "templates"
    folder.mkdir()
    template = folder / "t.lgd"
    template.write_text(_VALID + f"\n{{{{include: {include}}}}}\n", encoding="utf-8")
    assert main(["assemble", str(template)]) == EXIT_DIAGNOSTICS
    assert "[include-file-missing]" in capsys.readouterr().err


def test_assemble_refuses_a_kept_file_named_as_the_template(tmp_path, capsys):
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "main.lgd").write_text(
        _VALID.replace("---\n\n# Scope", "attachments:\n  - id: s\n    title: S\n    file: main.lgd\n---\n\n# Scope")
        + "\nSee {{attach: s}}.\n",
        encoding="utf-8",
    )
    assert main(["assemble", str(folder / "main.lgd"), "-o", str(tmp_path / "out")]) == EXIT_ERROR
    assert "both the template and a file it keeps" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("include", ["a\x00.lgd", "x" * 300 + ".lgd", "loop/x.lgd"])
def test_assemble_treats_an_impossible_path_as_unreadable(tmp_path, capsys, include):
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    template = tmp_path / "t.lgd"
    template.write_text(_VALID + f"\n{{{{include: {include}}}}}\n", encoding="utf-8")
    assert main(["assemble", str(template)]) == EXIT_DIAGNOSTICS
    assert "[include-file-missing]" in capsys.readouterr().err


def test_assemble_refuses_an_output_path_that_is_a_file(write, capsys, tmp_path):
    template = write("t.lgd", _VALID)
    target = write("taken", "")
    assert main(["assemble", str(template), "-o", str(target)]) == EXIT_ERROR
    assert "is not a directory" in capsys.readouterr().err
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "parts").write_text("", encoding="utf-8")
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "a.lgd").write_text("## A {#a}\n\nText.\n", encoding="utf-8")
    template.write_text(_VALID + "\n{{include: parts/a.lgd}}\n", encoding="utf-8")
    assert main(["assemble", str(template), "-o", str(tmp_path / "out")]) == EXIT_ERROR
    assert "the output is incomplete" in capsys.readouterr().err
