"""Conformance harness: run the validator over the LegalDown fixtures corpus.

The LegalDown specification repository ships a validation fixtures corpus
(``fixtures/`` — one directory per §16 rule id, each case paired with the
diagnostics a conforming validator must produce). This test drives our
validator over that corpus and asserts:

- **valid/** cases produce no Error-level diagnostics, and
- **invalid/** cases for rules this implementation covers produce the
  expected rule id at the expected severity — the answer rules (§16.12)
  through assembly, with the case's answers set, and
- **assembly/** cases assemble byte for byte to their expected output
  (§15.7, the Assembly capability, §17.6).

Rules the implementation does not yet cover are skipped and reported, so
this file doubles as the coverage ledger against the spec.

The corpus lives in a separate repository, so the harness activates only
when ``LEGALDOWN_FIXTURES_DIR`` points at its ``fixtures/`` directory:

    LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from legaldown import CAPABILITIES, assemble
from legaldown.parser import parse_document
from legaldown.validator import validate_document

FIXTURES_DIR = os.environ.get("LEGALDOWN_FIXTURES_DIR", "")

pytestmark = [
    pytest.mark.conformance,
    pytest.mark.skipif(
        not FIXTURES_DIR or not Path(FIXTURES_DIR).is_dir(),
        reason="LEGALDOWN_FIXTURES_DIR not set (LegalDown specification fixtures corpus)",
    ),
]

# §16 rules this implementation evaluates. Cases for other rules are skipped
# (single-file, Core-level scope: no filesystem, include, bilingual, or
# line-level lexer checks yet).
IMPLEMENTED_RULES = {
    "anchor-autogen-collision", "anchor-duplicate", "anchor-format", "anchor-lossy-slug",
    "anchor-misplaced", "value-curly-quote", "frontmatter-absent",
    "answer-invalid", "answer-missing", "answer-unknown",
    "amends-title-empty", "amend-def-override", "amend-term-undefined",
    "amend-term-unresolvable",
    "attach-undeclared", "attachment-id-collision", "attachment-id-duplicate",
    "attachment-title-empty", "attachment-unreferenced", "brace-stray", "choose-invalid",
    "condition-invalid", "condition-never-true", "condition-reference-unsafe",
    "date-invalid", "date-of-birth-invalid",
    "def-autogen-collision", "def-duplicate-id", "def-emphasis", "def-lossy-slug",
    "def-term-variable",
    "def-no-quoted-span", "def-single-quote-ambiguous", "def-unreferenced",
    "directive-duplicate-param", "directive-malformed", "directive-unknown",
    "directive-unknown-param", "document-type-invalid", "drafting-note-def",
    "drafting-note-unrecognized",
    "duration-invalid-unit", "duration-invalid-value",
    "field-type-key-format", "field-type-key-reserved", "field-type-missing",
    "field-type-undeclared",
    "heading-depth", "heading-hardcoded-number", "heading-skip", "insertion-boundary",
    "issuer-side-required", "legaldown-version-newer", "metadata-date-invalid",
    "money-invalid-amount", "money-missing-currency", "money-unknown-currency",
    "note-invalid", "parties-minimum",
    "party-name-duplicate", "party-name-malformed", "party-type-invalid",
    "party-unknown",
    "placeholder-id-malformed", "placeholder-in-structural-field",
    "placeholder-question-mismatch", "placeholder-type-inconsistent",
    "placeholder-type-invalid", "placeholder-unfilled", "placeholder-unknown-currency",
    "question-invalid", "question-unused",
    "ref-broken", "ref-targets-attachment", "representative-name-empty",
    "side-name-duplicate", "side-name-malformed", "side-party-name-format",
    "side-unknown", "sides-absent", "sides-minimum", "supersedes-title-empty",
    "template-construct-present", "template-fragment-invalid", "term-undefined", "title-missing",
}


def _load_expectation(case_dir_or_file: Path) -> tuple[Path, dict]:
    """Return (entry .lgd path, expectation dict) for a fixture case."""
    if case_dir_or_file.is_file():
        # Built from the stem so names containing dots (e.g. "a.b.lgd") map to
        # "a.b.expected.json" — matching how _iter_invalid_cases finds them.
        expected_path = case_dir_or_file.with_name(
            case_dir_or_file.stem + ".expected.json"
        )
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        return case_dir_or_file, expected
    expected_path = case_dir_or_file / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    entry = case_dir_or_file / expected.get("entry", "main.lgd")
    return entry, expected


def _iter_invalid_cases():
    root = Path(FIXTURES_DIR) / "invalid"
    if not root.is_dir():
        return
    for rule_dir in sorted(root.iterdir()):
        if not rule_dir.is_dir():
            continue
        single_files = sorted(rule_dir.glob("*.lgd"))
        if (rule_dir / "expected.json").exists():
            yield pytest.param(rule_dir, id=f"{rule_dir.name}/{rule_dir.name}")
        for lgd in single_files:
            if lgd.with_name(lgd.stem + ".expected.json").exists():
                yield pytest.param(lgd, id=f"{rule_dir.name}/{lgd.stem}")


def _iter_valid_cases():
    root = Path(FIXTURES_DIR) / "valid"
    if not root.is_dir():
        return
    for lgd in sorted(root.glob("*.lgd")):
        yield pytest.param(lgd, id=lgd.stem)


# Runner configuration this harness can supply (fixtures README): the final
# option (§15.9), and an answers set for the Assembly capability (§17.6).
# Cases needing anything else are skipped.
_SUPPORTED_CONFIG = {"final", "answers"}


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _loader(case: Path):
    """``load_file`` for a case: files relative to its template."""

    def load(relative: str) -> str | None:
        target = case / relative
        return _read(target) if target.is_file() else None

    return load


def _assemble_case(template: Path, answers: Path):
    loaded = yaml.safe_load(_read(answers)) or {}
    return assemble(_read(template), loaded, load_file=_loader(template.parent))


def _validate_file(path: Path, config: dict):
    """Validate *path*; with an answers set, assemble it instead, which
    reports the answer rules (§16.12)."""
    if "answers" in config:
        return _assemble_case(path, path.parent / config["answers"])
    document = parse_document(path.read_text(encoding="utf-8"), filename=path.name)
    return validate_document(document, final=bool(config.get("final")))


@pytest.mark.parametrize("case", list(_iter_valid_cases()))
def test_valid_fixture_produces_no_errors(case: Path):
    expected_path = case.with_name(case.stem + ".expected.json")
    expected = (
        json.loads(expected_path.read_text(encoding="utf-8"))
        if expected_path.exists()
        else {}
    )
    if expected.get("requires_level", "core") != "core":
        pytest.skip(f"requires conformance level {expected['requires_level']}")
    if expected.get("requires_capability") not in (None, *CAPABILITIES):
        pytest.skip(f"requires the {expected['requires_capability']} capability")
    config = expected.get("requires_config") or {}
    if not set(config) <= _SUPPORTED_CONFIG:
        pytest.skip("requires runner configuration")
    result = _validate_file(case, config)
    assert not [d for d in result.diagnostics if d.level == "error"], (
        f"valid fixture produced errors: {[d for d in result.diagnostics if d.level == 'error']}"
    )


@pytest.mark.parametrize("case", list(_iter_invalid_cases()))
def test_invalid_fixture_reports_expected_rule(case: Path):
    entry, expected = _load_expectation(case)
    rule_id = (case if case.is_dir() else case.parent).name
    if rule_id not in IMPLEMENTED_RULES:
        pytest.skip(f"rule {rule_id} not implemented")
    if expected.get("requires_level", "core") != "core":
        pytest.skip(f"requires conformance level {expected['requires_level']}")
    if expected.get("requires_capability") not in (None, *CAPABILITIES):
        pytest.skip(f"requires the {expected['requires_capability']} capability")
    config = expected.get("requires_config") or {}
    if not set(config) <= _SUPPORTED_CONFIG:
        pytest.skip("requires runner configuration")
    if case.is_dir() and len(list(case.glob("*.lgd"))) > 1:
        pytest.skip("multi-file case (single-document harness)")

    result = _validate_file(entry, config)
    produced = {(d.rule, d.level) for d in result.diagnostics}
    for diag in expected.get("diagnostics", []):
        want = (diag["rule"], diag["level"])
        if diag["rule"] not in IMPLEMENTED_RULES:
            continue
        assert want in produced, (
            f"expected {want} not produced; got {sorted(produced)}"
        )


def _iter_assembly_templates():
    root = Path(FIXTURES_DIR) / "assembly"
    if not root.is_dir():
        return
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        yield pytest.param(case, id=case.name)


def _skip_above_core(case: Path) -> None:
    """Skip a case whose ``case.json`` asks for a level above Core (fixtures
    README, step 4)."""
    if (case / "case.json").exists():
        level = json.loads((case / "case.json").read_text(encoding="utf-8")).get("requires_level", "core")
        if level != "core":
            pytest.skip(f"requires conformance level {level}")


@pytest.mark.parametrize("case", list(_iter_assembly_templates()))
def test_assembly_template_validates_without_errors(case: Path):
    """Each assembly case's template MUST produce no Errors (fixtures README,
    step 3)."""
    _skip_above_core(case)
    result = _validate_file(case / "template.lgd", {})
    assert not result.errors, (
        f"template produced errors: {[d for d in result.diagnostics if d.level == 'error']}"
    )


def _expected_tree(case: Path) -> dict[str, str]:
    """Every file the case expects assembly to write, by relative path; the
    template's output is ``template.lgd`` (fixtures README)."""
    if (case / "expected.lgd").is_file():
        return {"template.lgd": _read(case / "expected.lgd")}
    tree = case / "expected"
    return {path.relative_to(tree).as_posix(): _read(path) for path in sorted(tree.rglob("*")) if path.is_file()}


@pytest.mark.parametrize("case", list(_iter_assembly_templates()))
def test_assembly_case_assembles_byte_for_byte(case: Path):
    """§15.7.2: the same template and answers set give byte-identical output.
    (The Full multi-file case is skipped here, and assembled by
    tests/test_assembly.py.)"""
    _skip_above_core(case)
    result = _assemble_case(case / "template.lgd", case / "answers.yaml")
    assert result.ok, result.diagnostics
    assert {"template.lgd": result.output, **result.files} == _expected_tree(case)
    # The assembly guarantee (§15.7.4): the output has no Errors either.
    assert not validate_document(parse_document(result.output)).errors
