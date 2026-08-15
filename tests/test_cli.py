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
    assert payload["legaldown_spec"] == "0.1"
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
