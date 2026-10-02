"""CLI behavior: output formats, rule filtering, and exit codes."""
from __future__ import annotations

import io
import json
import sys

import pytest
import yaml

from legaldown import load
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


def test_a_file_that_is_not_utf8_is_unreadable_not_a_crash(tmp_path, capsys):
    path = tmp_path / "latin.lgd"
    path.write_bytes(b"---\ntitle: caf\xe9\n---\n")
    assert main(["validate", str(path)]) == EXIT_ERROR
    assert "cannot read" in capsys.readouterr().err


def test_malformed_frontmatter_is_reported_not_raised(write, capsys):
    path = write("bad.lgd", "---\ntitle: [unclosed\n---\n\n# Scope {#scope}\n")
    assert main(["validate", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    payload = json.loads(capsys.readouterr().out)
    assert payload["diagnostics"][0]["rule"] == "frontmatter-invalid-yaml"


@pytest.mark.parametrize("frontmatter", [
    "title: [unclosed",
    "*Important* notice",  # prose between two --- lines
    "!!set {a, b}",
    "title: !!binary xx",
    "a: " + "[" * 3000,  # nested past what the YAML reader goes
    "title: T\nquestions:\n  q:\n    default: " + "9" * 5000,  # an integer too long to convert
    "title: T\nquestions:\n  q:\n    default: !!bool maybe",  # no boolean
])
def test_frontmatter_that_cannot_be_read_is_a_diagnostic(write, capsys, frontmatter):
    path = write("bad.lgd", f"---\n{frontmatter}\n---\n\n# Scope {{#scope}}\n")
    assert main(["validate", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    payload = json.loads(capsys.readouterr().out)
    assert [d["rule"] for d in payload["diagnostics"]] == ["frontmatter-invalid-yaml"]


def test_a_parser_fault_is_an_internal_error_not_a_diagnostic(write, capsys, monkeypatch):
    # A bug in the parser is not the author's YAML (#42): it is reported as a
    # failure to report, and the other files are still validated.
    import legaldown.cli

    def broken(path):
        if path.name == "a.lgd":
            raise AttributeError("'set' object has no attribute 'get'")
        return load(path)

    monkeypatch.setattr(legaldown.cli, "load", broken)
    first, second = write("a.lgd", _VALID), write("b.lgd", _BROKEN)
    assert main(["validate", "--format", "json", str(first), str(second)]) == EXIT_ERROR
    captured = capsys.readouterr()
    rules = [d["rule"] for d in json.loads(captured.out)["diagnostics"]]
    assert "frontmatter-invalid-yaml" not in rules
    assert "ref-broken" in rules  # b.lgd
    assert "internal error while parsing" in captured.err and "AttributeError" in captured.err


def test_assembling_a_template_with_frontmatter_that_cannot_be_read(write, capsys):
    path = write("t.lgd", "---\ntitle: [unclosed\n---\n\n# A\n")
    assert main(["assemble", str(path)]) == EXIT_DIAGNOSTICS
    assert "[frontmatter-invalid-yaml]" in capsys.readouterr().err


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


def test_assemble_refuses_a_template_that_keeps_itself(tmp_path, capsys):
    # Its own attachment file: its frontmatter and level 1 heading stop it
    # before the output could be written over by the kept file.
    folder = tmp_path / "t"
    folder.mkdir()
    (folder / "main.lgd").write_text(
        _VALID.replace("---\n\n# Scope", "attachments:\n  - id: s\n    title: S\n    file: main.lgd\n---\n\n# Scope")
        + "\nSee {{attach: s}}.\n",
        encoding="utf-8",
    )
    assert main(["assemble", str(folder / "main.lgd"), "-o", str(tmp_path / "out")]) == EXIT_DIAGNOSTICS
    assert "[attachment-has-frontmatter]" in capsys.readouterr().err
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


def test_validate_reads_the_amended_original_beside_the_document(tmp_path, capsys):
    amendment = (
        "---\ntitle: A\nsides:\n  - name: s\n    parties:\n      - name: p\n"
        "        type: legal_entity\n        legal_name: P\n"
        "amends:\n  title: Original\n  file: original.lgd\n---\n\n# S {#s}\n\nUses {{term: services}}.\n"
    )
    (tmp_path / "amendment.lgd").write_text(amendment, encoding="utf-8")
    assert main(["validate", "--format", "json", str(tmp_path / "amendment.lgd")]) == EXIT_DIAGNOSTICS
    rules = {d["rule"] for d in json.loads(capsys.readouterr().out)["diagnostics"]}
    assert "amend-term-unresolvable" in rules  # no original beside it: as before
    (tmp_path / "original.lgd").write_text(
        '---\ntitle: O\n---\n\n# S {#s}\n\n"Services" {{def: services}} means x.\n', encoding="utf-8"
    )
    assert main(["validate", "--format", "json", str(tmp_path / "amendment.lgd")]) == EXIT_DIAGNOSTICS
    diagnostics = json.loads(capsys.readouterr().out)["diagnostics"]
    assert diagnostics  # the document's own findings are still reported
    assert not {"amend-term-unresolvable", "amend-term-undefined"} & {d["rule"] for d in diagnostics}


def test_a_fault_while_validating_is_an_internal_error_and_the_others_are_still_validated(write, capsys, monkeypatch):
    import legaldown.cli

    real = legaldown.cli.validate

    def broken(document, **options):
        if document.filename == "a.lgd":
            raise AttributeError("boom")
        return real(document, **options)

    monkeypatch.setattr(legaldown.cli, "validate", broken)
    first, second = write("a.lgd", _VALID), write("b.lgd", _BROKEN)
    assert main(["validate", "--format", "json", str(first), str(second)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert "ref-broken" in [d["rule"] for d in json.loads(captured.out)["diagnostics"]]  # b.lgd
    assert "internal error while validating" in captured.err and "AttributeError" in captured.err


def test_assemble_refuses_a_template_with_an_error_in_the_template_rules(write, capsys):
    template = write("t.lgd", _TEMPLATE.replace("Hi {{placeholder: who}}.", 'Hi {{choose: x, true="only one phrase"}}.'))
    answers = write("a.yaml", "x: true\n")
    assert main(["assemble", str(template), "--answers", str(answers)]) == EXIT_DIAGNOSTICS
    captured = capsys.readouterr()
    assert captured.out == "" and "[choose-invalid]" in captured.err


def test_a_fault_while_assembling_is_an_internal_error(write, capsys, monkeypatch):
    import legaldown.template

    def broken(self, answers=None):
        raise AttributeError("boom")

    monkeypatch.setattr(legaldown.template.Template, "form", broken)
    template = write("t.lgd", _TEMPLATE)
    assert main(["assemble", str(template)]) == EXIT_ERROR
    assert "internal error while assembling" in capsys.readouterr().err


def test_a_fault_while_reading_the_template_is_an_internal_error(write, capsys, monkeypatch):
    import legaldown.cli

    def broken(path):
        raise legaldown.AssemblyError("boom")

    monkeypatch.setattr(legaldown.cli, "load_template", broken)
    assert main(["assemble", str(write("t.lgd", _TEMPLATE))]) == EXIT_ERROR
    assert "internal error while reading" in capsys.readouterr().err


# ── legaldown questions ──────────────────────────────────────────

_ASKING = _TEMPLATE.replace("questions:\n  x:\n    type: boolean\n", "questions:\n  x:\n    type: boolean\n    prompt: Keep the extras?\n")


def _questions(args, capsys):
    code = main(["questions", *args])
    return code, capsys.readouterr()


def test_questions_lists_what_is_asked_as_json(write, capsys):
    template = write("t.lgd", _ASKING)
    code, captured = _questions([str(template), "--format", "json"], capsys)
    assert code == EXIT_OK
    data = json.loads(captured.out)
    assert (data["ready"], data["complete"], data["problems"]) == (False, False, [])
    assert [q["id"] for q in data["questions"] if q["reached"]] == ["x", "who"]
    by_id = {q["id"]: q for q in data["questions"]}
    assert by_id["x"]["blocking"] and by_id["x"]["state"] == "unanswered" and by_id["x"]["label"] == "Keep the extras?"
    assert by_id["who"]["hint"] == "text" and by_id["x"]["accepts"]["kind"] == "boolean"
    assert [d["rule"] for d in data["diagnostics"]] == ["answer-missing"]
    assert data["diagnostics"][0]["question"] == "x"


def test_questions_reads_the_answers_and_puts_their_shapes_right(write, capsys):
    template = write("t.lgd", _ASKING)
    answers = write("a.yaml", "x: no\nwho: Ann\n")
    code, captured = _questions([str(template), "--answers", str(answers), "--format", "json"], capsys)
    data = json.loads(captured.out)
    assert code == EXIT_OK and data["ready"] and data["complete"]
    assert {q["id"]: q["answer"] for q in data["questions"] if q["reached"]} == {"x": False, "who": "Ann"}


def test_questions_as_text(write, capsys):
    template = write("t.lgd", _ASKING)
    code, captured = _questions([str(template)], capsys)
    assert code == EXIT_OK and captured.err == ""
    lines = captured.out.splitlines()
    assert lines[0].startswith("! x (boolean) Keep the extras?") and "enter yes or no" in lines[0]
    assert lines[1].startswith("? who (text)") and "enter text" in lines[1]
    assert "[answer-missing]" in captured.out and lines[-1] == "not ready"
    answers = write("a.yaml", "x: true\nwho: Ann\n")
    code, captured = _questions([str(template), "--answers", str(answers)], capsys)
    assert captured.out.splitlines()[-1] == "complete" and "answered: Ann" in captured.out and "answered: yes" in captured.out


def test_questions_check_makes_not_ready_an_exit_status(write, capsys):
    template = write("t.lgd", _ASKING)
    assert _questions([str(template), "--check"], capsys)[0] == EXIT_DIAGNOSTICS
    answers = write("a.yaml", "x: true\n")
    assert _questions([str(template), "--answers", str(answers), "--check"], capsys)[0] == EXIT_OK  # ready, with blanks


def test_questions_reports_a_template_with_problems(write, capsys):
    template = write("t.lgd", _TEMPLATE.replace("Hi {{placeholder: who}}.", 'Hi {{choose: x, true="only one phrase"}}.'))
    code, captured = _questions([str(template), "--format", "json"], capsys)
    data = json.loads(captured.out)
    assert code == EXIT_DIAGNOSTICS and [p["rule"] for p in data["problems"]] == ["choose-invalid"]
    code, captured = _questions([str(template)], capsys)
    assert code == EXIT_DIAGNOSTICS and "problem: [choose-invalid]" in captured.out


def test_questions_reports_unreadable_frontmatter_as_a_diagnostic(write, capsys):
    template = write("t.lgd", "---\ntitle: [unclosed\n---\n")
    code, captured = _questions([str(template), "--format", "json"], capsys)
    data = json.loads(captured.out)
    assert code == EXIT_DIAGNOSTICS and data["problems"][0]["rule"] == "frontmatter-invalid-yaml" and data["questions"] == []
    code, captured = _questions([str(template)], capsys)
    assert code == EXIT_DIAGNOSTICS and "[frontmatter-invalid-yaml]" in captured.err


@pytest.mark.parametrize("answers", ["x: [unclosed\n", "- a\n- b\n"])
def test_questions_with_an_unreadable_answers_file_or_template_is_an_error(write, capsys, tmp_path, answers):
    template = write("t.lgd", _ASKING)
    assert _questions([str(template), "--answers", str(write("a.yaml", answers))], capsys)[0] == EXIT_ERROR
    assert _questions([str(tmp_path / "nope.lgd")], capsys)[0] == EXIT_ERROR
    assert _questions([str(template), "--answers", str(tmp_path / "nope.yaml")], capsys)[0] == EXIT_ERROR


def test_assemble_puts_the_shapes_of_the_answers_file_right(write, capsys):
    template = write("t.lgd", _ASKING.replace("Hi {{placeholder: who}}.", "Fee {{placeholder: fee, type=money}}."))
    answers = write("a.yaml", "x: no\nfee: 5000 eur\n")  # not the shape of a money answer
    assert main(["assemble", str(template), "--answers", str(answers)]) == EXIT_OK
    assert "{{money: 5000, currency=EUR}}" in capsys.readouterr().out
    integers = write("b.yaml", "x: no\nfee:\n  amount: 5000\n  currency: EUR\n")  # a number as the amount
    assert main(["assemble", str(template), "--answers", str(integers)]) == EXIT_OK
    assert "{{money: 5000, currency=EUR}}" in capsys.readouterr().out


def test_questions_writes_utf8_whatever_the_terminal_encoding(tmp_path):
    import os
    import subprocess
    import sys

    template = tmp_path / "t.lgd"
    template.write_text(_TEMPLATE.replace("prompt: x", "x").replace("    type: boolean\n", "    type: boolean\n    prompt: Jméno — ano?\n", 1), encoding="utf-8")
    src = os.path.join(os.path.dirname(__file__), "..", "src")
    env = {**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONPATH": src}
    for fmt in ("text", "json"):
        done = subprocess.run(
            [sys.executable, "-m", "legaldown.cli", "questions", str(template), "--format", fmt],
            capture_output=True, env=env, check=False,
        )
        assert done.returncode == 0, done.stderr
        assert "Jméno — ano?".encode() in done.stdout


def test_questions_json_is_strict_json_for_not_a_number_answers(write, capsys):
    template = write("t.lgd", _ASKING)
    answers = write("a.yaml", "x: true\nwho: .nan\n")
    code, captured = _questions([str(template), "--answers", str(answers), "--format", "json"], capsys)
    assert code == EXIT_OK
    json.loads(captured.out, parse_constant=lambda name: pytest.fail(f"{name} is not JSON"))


def test_questions_with_answers_nested_too_deep_is_an_error(write, capsys):
    template = write("t.lgd", _ASKING)
    answers = write("a.yaml", "x: " + "[" * 3000 + "]" * 3000 + "\n")
    code, captured = _questions([str(template), "--answers", str(answers)], capsys)
    assert code == EXIT_ERROR and "cannot read the answers" in captured.err


def test_a_fault_while_listing_the_questions_is_an_internal_error(write, capsys, monkeypatch):
    import legaldown.template

    def broken(self):
        raise AttributeError("boom")

    monkeypatch.setattr(legaldown.template.Form, "as_dict", broken)
    template = write("t.lgd", _ASKING)
    assert _questions([str(template), "--format", "json"], capsys)[0] == EXIT_ERROR


# ── legaldown assemble -i, --save-answers ─────────────────────────

_INTERVIEW = _TEMPLATE.replace(
    "    type: boolean\n", "    type: boolean\n    prompt: Keep the extras?\n", 1
).replace("Hi {{placeholder: who}}.", "Hi {{placeholder: who}}. Fee {{placeholder: fee, type=money}}.")


class _Terminal(io.StringIO):
    """Standard input that is a terminal, typing *lines*."""

    def isatty(self):
        return True


def _type(monkeypatch, *lines: str) -> None:
    monkeypatch.setattr(sys, "stdin", _Terminal("".join(f"{line}\n" for line in lines)))


def test_an_interview_asks_for_the_answers_and_writes_the_document_to_stdout(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch, "no", "Ann", "250 eur")
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "Hi Ann. Fee {{money: 250, currency=EUR}}." in captured.out and "Extra." not in captured.out
    assert "Keep the extras? (yes or no)" in captured.err and "who (text)" in captured.err  # prompts: stderr
    assert "Keep the extras?" not in captured.out


def test_a_decision_is_asked_again_until_it_is_answered(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch, "", "maybe", "yes", "Ann", "", )
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "An answer is needed." in err and "Enter yes or no." in err
    assert err.count("Keep the extras?") == 3


def test_a_value_question_may_be_left_to_its_blank(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch, "yes", "", "", "")
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "{{placeholder: who}}" in out and "Extra." in out


def test_an_interview_shows_the_default_and_the_questions_the_answers_file_gave_are_not_asked(write, capsys, monkeypatch):
    template = write(
        "t.lgd",
        _INTERVIEW.replace("    prompt: Keep the extras?\n", "    prompt: Keep the extras?\n  fee:\n    type: money\n    default:\n      amount: '100'\n      currency: EUR\n", 1),
    )
    answers = write("a.yaml", "x: no\n")
    _type(monkeypatch, "Ann", "")
    assert main(["assemble", str(template), "--answers", str(answers), "-i"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "[100 EUR]" in err and "Keep the extras?" not in err


def test_an_answer_the_file_gave_that_is_not_valid_is_asked_again(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    answers = write("a.yaml", "x: maybe\nwho: Ann\n")
    _type(monkeypatch, "no", "")
    assert main(["assemble", str(template), "--answers", str(answers), "-i"]) == EXIT_OK
    assert "is not valid" in capsys.readouterr().err


def test_without_a_terminal_it_lists_what_must_be_answered(write, capsys):
    template = write("t.lgd", _INTERVIEW)
    assert main(["assemble", str(template), "-i"]) == EXIT_DIAGNOSTICS  # pytest's stdin is no terminal
    captured = capsys.readouterr()
    assert captured.out == "" and "x: Keep the extras? (enter yes or no)" in captured.err and "-i" in captured.err
    answers = write("a.yaml", "x: no\n")
    assert main(["assemble", str(template), "--answers", str(answers), "-i"]) == EXIT_OK  # nothing blocks: batch
    assert "{{placeholder: who}}" in capsys.readouterr().out


def test_without_i_a_missing_decision_says_how_to_be_asked(write, capsys):
    template = write("t.lgd", _INTERVIEW)
    assert main(["assemble", str(template)]) == EXIT_DIAGNOSTICS
    err = capsys.readouterr().err
    assert "[answer-missing]" in err and "-i" in err


def test_the_end_of_the_input_ends_the_interview(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch)  # nothing typed
    assert main(["assemble", str(template), "-i"]) == EXIT_DIAGNOSTICS
    captured = capsys.readouterr()
    assert captured.out == "" and "[answer-missing]" in captured.err


def test_an_interruption_saves_the_answers_and_assembles_nothing(write, capsys, monkeypatch, tmp_path):
    template = write("t.lgd", _INTERVIEW)

    class Typist(_Terminal):
        def __init__(self):
            super().__init__("no\n")

        def readline(self, *args):
            line = super().readline(*args)
            if not line:
                raise KeyboardInterrupt
            return line

    monkeypatch.setattr(sys, "stdin", Typist())
    saved = tmp_path / "saved.yaml"
    assert main(["assemble", str(template), "-i", "--save-answers", str(saved)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == "" and "interrupted" in captured.err
    assert yaml.safe_load(saved.read_text(encoding="utf-8")) == {"x": False}


def test_saved_answers_give_the_same_document_when_read_back(write, capsys, monkeypatch, tmp_path):
    template = write("t.lgd", _INTERVIEW)
    saved = tmp_path / "out" / "saved.yaml"
    _type(monkeypatch, "no", "Ann Čech", "250 eur")
    assert main(["assemble", str(template), "-i", "--save-answers", str(saved)]) == EXIT_OK
    first = capsys.readouterr().out
    text = saved.read_text(encoding="utf-8")
    assert "Ann Čech" in text and "'250'" in text  # not escaped; an amount stays a quoted string
    assert main(["assemble", str(template), "--answers", str(saved)]) == EXIT_OK
    assert capsys.readouterr().out == first


def test_answers_are_saved_even_when_nothing_could_be_assembled(write, capsys, tmp_path):
    template = write("t.lgd", _INTERVIEW)
    answers = write("a.yaml", "who: Ann\n")
    saved = tmp_path / "saved.yaml"
    assert main(["assemble", str(template), "--answers", str(answers), "--save-answers", str(saved)]) == EXIT_DIAGNOSTICS
    assert yaml.safe_load(saved.read_text(encoding="utf-8")) == {"who": "Ann"}
    broken = write("b.lgd", "---\ntitle: [unclosed\n---\n")
    other = tmp_path / "other.yaml"
    assert main(["assemble", str(broken), "--answers", str(answers), "--save-answers", str(other)]) == EXIT_DIAGNOSTICS
    assert yaml.safe_load(other.read_text(encoding="utf-8")) == {"who": "Ann"}


def test_answers_are_not_saved_when_the_answers_file_cannot_be_read(write, capsys, tmp_path):
    template = write("t.lgd", _INTERVIEW)
    saved = tmp_path / "saved.yaml"
    assert main(["assemble", str(template), "--answers", str(tmp_path / "nope.yaml"), "--save-answers", str(saved)]) == EXIT_ERROR
    assert not saved.exists()


def test_a_failure_to_save_the_answers_is_an_error(write, capsys, tmp_path):
    template = write("t.lgd", _INTERVIEW)
    answers = write("a.yaml", "x: no\nwho: Ann\n")
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory", encoding="utf-8")
    code = main(["assemble", str(template), "--answers", str(answers), "--save-answers", str(blocked / "a.yaml")])
    assert code == EXIT_ERROR and "cannot write the answers" in capsys.readouterr().err


def test_a_template_that_cannot_be_assembled_asks_nothing(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW.replace("Hi {{placeholder: who}}.", 'Hi {{choose: x, true="only one phrase"}}.'))
    _type(monkeypatch, "no", "Ann")
    assert main(["assemble", str(template), "-i"]) == EXIT_DIAGNOSTICS
    err = capsys.readouterr().err
    assert "[choose-invalid]" in err and "Keep the extras?" not in err
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # no terminal
    assert main(["assemble", str(template), "-i"]) == EXIT_DIAGNOSTICS
    err = capsys.readouterr().err
    assert "[choose-invalid]" in err and "must be answered" not in err


def test_the_empty_line_drops_an_answer_that_is_not_valid(write, capsys, monkeypatch, tmp_path):
    template = write("t.lgd", _INTERVIEW)
    answers = write("a.yaml", "x: no\nwho: '  '\nfee: 5000\n")
    saved = tmp_path / "saved.yaml"
    _type(monkeypatch, "", "")
    code = main(["assemble", str(template), "--answers", str(answers), "-i", "--save-answers", str(saved)])
    assert code == EXIT_OK  # the blanks stay; nothing invalid is left
    assert "who" not in yaml.safe_load(saved.read_text(encoding="utf-8"))


def test_text_that_is_not_utf8_is_not_an_answer(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch, "no", "\udcff\udcfe bad", "Ann", "")
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "Hi Ann." in captured.out and "UTF-8" in captured.err


def test_the_answers_are_saved_beside_and_moved_into_place(write, tmp_path):
    from legaldown.cli import _save_answers

    saved = tmp_path / "a.yaml"
    saved.write_text("# my notes\nwho: Old\n", encoding="utf-8")
    assert _save_answers(saved, {"who": "New"}) is None
    assert yaml.safe_load(saved.read_text(encoding="utf-8")) == {"who": "New"}
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]  # no temporary file left
    assert "cannot write" in _save_answers(saved, {"who": object()})
    assert yaml.safe_load(saved.read_text(encoding="utf-8")) == {"who": "New"}  # as it was
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]
    assert _save_answers(saved, {"who": "\udcff"}) is None  # YAML writes it escaped, and it reads back
    assert yaml.safe_load(saved.read_text(encoding="utf-8")) == {"who": "\udcff"}


def test_the_default_that_is_accepted_is_echoed(write, capsys, monkeypatch):
    template = write(
        "t.lgd",
        _INTERVIEW.replace("    prompt: Keep the extras?\n", "    prompt: Keep the extras?\n  fee:\n    type: money\n    default:\n      amount: '100'\n      currency: EUR\n", 1),
    )
    _type(monkeypatch, "no", "Ann", "")
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    assert "the default, 100 EUR" in capsys.readouterr().err


def test_keys_typed_as_escape_sequences_are_not_an_answer(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch, "no", "abc\x1b[D\x1b[DX", "Ann", "")  # the arrow keys of a terminal without line editing
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "Hi Ann." in captured.out and "\x1b" not in captured.out and "control character" in captured.err


# ── --save-answers keeps what it replaces ─────────────────────────


def test_saving_the_answers_keeps_the_permissions_of_the_file_it_replaces(write, tmp_path):
    import os
    import stat

    from legaldown.cli import _save_answers

    saved = tmp_path / "a.yaml"
    saved.write_text("who: Old\n", encoding="utf-8")
    os.chmod(saved, 0o640)
    assert _save_answers(saved, {"who": "New"}) is None
    assert stat.S_IMODE(saved.stat().st_mode) == 0o640
    fresh = tmp_path / "new" / "b.yaml"
    assert _save_answers(fresh, {"who": "New"}) is None
    mask = os.umask(0)
    os.umask(mask)
    assert stat.S_IMODE(fresh.stat().st_mode) == 0o666 & ~mask  # not the 0600 of a temporary file


def test_saving_the_answers_writes_through_a_symbolic_link(tmp_path):
    from legaldown.cli import _save_answers

    real = tmp_path / "real.yaml"
    real.write_text("who: Old\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symbolic links are not available")
    assert _save_answers(link, {"who": "New"}) is None
    assert link.is_symlink() and yaml.safe_load(real.read_text(encoding="utf-8")) == {"who": "New"}


def test_the_answers_are_never_saved_over_the_template(write, capsys):
    template = write("t.lgd", _INTERVIEW)
    before = template.read_text(encoding="utf-8")
    assert main(["assemble", str(template), "--save-answers", str(template)]) == EXIT_ERROR
    assert "names the template itself" in capsys.readouterr().err
    assert template.read_text(encoding="utf-8") == before


def test_an_answer_that_cannot_be_written_as_utf8_is_an_error_not_a_traceback(write, capsys):
    template = write("t.lgd", _INTERVIEW)
    answers = write("a.yaml", 'x: no\nwho: "\\uDCFF"\n')
    assert main(["assemble", str(template), "--answers", str(answers)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == "" and "cannot be written as UTF-8" in captured.err
    code = main(["questions", str(template), "--answers", str(answers), "--format", "json"])
    assert code == EXIT_OK and "\\udcff" in capsys.readouterr().out  # described, escaped


def test_the_end_of_the_input_is_said(write, capsys, monkeypatch):
    template = write("t.lgd", _INTERVIEW)
    _type(monkeypatch, "no")  # who is not answered: the input ends
    assert main(["assemble", str(template), "-i"]) == EXIT_OK
    assert "End of the input" in capsys.readouterr().err


def test_nothing_read_and_nothing_typed_does_not_replace_the_file_to_save_to(write, capsys, monkeypatch, tmp_path):
    keep = write("keep.yaml", "# mine\nwho: Ann\nx: false\n")
    before = keep.read_text(encoding="utf-8")
    # a template that cannot be read (a typo in its name)
    assert main(["assemble", str(tmp_path / "typo.lgd"), "--save-answers", str(keep)]) == EXIT_ERROR
    # an interview with no terminal and nothing typed
    template = write("t.lgd", _INTERVIEW)
    assert main(["assemble", str(template), "-i", "--save-answers", str(keep)]) == EXIT_DIAGNOSTICS
    assert "was not written" in capsys.readouterr().err
    # an interview interrupted before the first answer
    class Interrupted(_Terminal):
        def readline(self, *args):
            raise KeyboardInterrupt

    monkeypatch.setattr(sys, "stdin", Interrupted())
    assert main(["assemble", str(template), "-i", "--save-answers", str(keep)]) == EXIT_ERROR
    assert keep.read_text(encoding="utf-8") == before


def test_an_answers_file_that_was_read_is_saved_even_when_it_is_empty_of_answers(write, tmp_path):
    answers = write("a.yaml", "")
    template = write("t.lgd", _INTERVIEW)
    saved = tmp_path / "saved.yaml"
    assert main(["assemble", str(template), "--answers", str(answers), "--save-answers", str(saved)]) == EXIT_DIAGNOSTICS
    assert yaml.safe_load(saved.read_text(encoding="utf-8")) == {}


def test_the_template_is_not_overwritten_through_a_hard_link_either(write, tmp_path):
    import os

    template = write("t.lgd", _INTERVIEW)
    link = tmp_path / "hard.lgd"
    try:
        os.link(template, link)
    except OSError:
        pytest.skip("hard links are not available")
    before = template.read_text(encoding="utf-8")
    assert main(["assemble", str(template), "--save-answers", str(link)]) == EXIT_ERROR
    assert template.read_text(encoding="utf-8") == before


def test_a_symbolic_link_loop_as_the_file_to_save_to_is_an_error_not_a_traceback(write, capsys, tmp_path):
    import os

    template = write("t.lgd", _INTERVIEW)
    answers = write("a.yaml", "x: no\nwho: Ann\n")
    loop = tmp_path / "loop.yaml"
    try:
        os.symlink(loop, loop)
    except OSError:
        pytest.skip("symbolic links are not available")
    code = main(["assemble", str(template), "--answers", str(answers), "--save-answers", str(loop)])
    err = capsys.readouterr().err
    if code == EXIT_ERROR:  # Python before 3.13 cannot resolve a link loop: said, not a traceback
        assert "cannot write the answers" in err
    else:  # Python 3.13 resolves it to itself, and the answers replace the loop
        assert code == EXIT_OK and not loop.is_symlink()
        assert yaml.safe_load(loop.read_text(encoding="utf-8")) == {"x": False, "who": "Ann"}
