"""Frontmatter that cannot be read does not stop the validation of the body (#43)."""
from __future__ import annotations

import json

import pytest

from legaldown import Document, FrontmatterError, load, parse, save, serialize, validate
from legaldown.cli import EXIT_DIAGNOSTICS, main

#: Prose between two --- lines, as written by someone who meant a thematic break.
PROSE = ["*Important* notice", "`code`", "@x", "> q", "| a |", "[l](x)", "%x"]

#: A body whose one error, ref-broken, is on its fifth line.
_BODY = "# Scope\n\nText.\n\nSee {{ref: nope}}.\n"


def _kept(source: str) -> Document:
    return parse(source, on_frontmatter_error="keep")


def _rows(document: Document, **options):
    return [(d.rule, d.level, d.line) for d in validate(document, **options).diagnostics]


@pytest.fixture
def write(tmp_path):
    def _write(name: str, source: str):
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path
    return _write


# ── The opt-in ─────────────────────────────────────────────────────

@pytest.mark.parametrize("block", PROSE)
def test_prose_between_two_rules_is_unreadable_by_default_and_kept_on_request(block):
    source = f"---\n{block}\n---\n\n{_BODY}"
    with pytest.raises(FrontmatterError) as raised:
        parse(source)
    document = _kept(source)
    assert document.frontmatter_error is not None
    assert document.frontmatter_error.line == raised.value.line
    assert str(document.frontmatter_error) == str(raised.value)
    # The block is the frontmatter, not body: it is there and holds nothing.
    assert document.metadata.frontmatter_absent is False
    assert document.preamble == []
    assert [s.title for s in document.sections] == ["Scope"]


def test_a_document_that_can_be_read_has_no_error_whatever_the_option():
    source = "---\ntitle: T\n---\n\n# A\n"
    assert parse(source).frontmatter_error is None
    assert _kept(source).frontmatter_error is None
    assert _kept(source) == parse(source)
    assert _kept("# No frontmatter\n").frontmatter_error is None


def test_the_error_is_not_part_of_equality():
    # Like the path: it describes the source, not what the document says.
    kept = _kept("---\n[x\n---\n# A\n")
    other = _kept("---\n[x\n---\n# A\n")
    kept.frontmatter_error = None
    assert kept == other


@pytest.mark.parametrize("value", ["", "Keep", "skip", None])
def test_any_other_value_of_the_option_is_refused(value):
    with pytest.raises(ValueError, match="on_frontmatter_error"):
        parse("# A\n", on_frontmatter_error=value)  # type: ignore[arg-type]


def test_the_default_of_parse_and_load_is_to_raise(tmp_path):
    source = "---\ntitle: [unclosed\n---\n\n# A\n"
    with pytest.raises(FrontmatterError):
        parse(source)
    file = tmp_path / "bad.lgd"
    file.write_text(source, encoding="utf-8")
    with pytest.raises(FrontmatterError):
        load(file)
    with pytest.raises(FrontmatterError):
        load(file, on_frontmatter_error="raise")
    assert load(file, on_frontmatter_error="keep").frontmatter_error is not None


def test_load_keeps_the_error_and_names_the_file(tmp_path):
    file = tmp_path / "bad.lgd"
    file.write_text("---\n@x\n---\n\n" + _BODY, encoding="utf-8")
    document = load(file, on_frontmatter_error="keep")
    assert (document.filename, document.path) == ("bad.lgd", file.absolute())
    assert [r for r, *_ in _rows(document)] == ["frontmatter-invalid-yaml", "ref-broken"]


def test_a_block_that_is_not_closed_is_not_frontmatter_whatever_the_option():
    document = _kept("---\n*x*\n\n# A\n")
    assert document.frontmatter_error is None and document.metadata.frontmatter_absent


# ── The diagnostics ────────────────────────────────────────────────

@pytest.mark.parametrize("block", PROSE)
def test_prose_between_two_rules_reports_the_block_and_the_body(block):
    document = _kept(f"---\n{block}\n---\n\n{_BODY}")
    rows = _rows(document)
    assert rows == [("frontmatter-invalid-yaml", "error", document.frontmatter_error.line), ("ref-broken", "error", 9)]


def test_the_line_of_the_error_is_that_of_the_yaml_problem():
    document = _kept("---\ntitle: Fixture\n  bad indent: [unclosed\n---\n\n# Scope\n\n{{ref: nope}}\n")
    assert _rows(document) == [("frontmatter-invalid-yaml", "error", 3), ("ref-broken", "error", 8)]
    # A quoted scalar left open is where the quote opens.
    document = _kept('---\ntitle: T\nsubtitle: "open\nlanguage: en\n---\n\n# A\n\n{{ref: nope}}\n')
    assert _rows(document) == [("frontmatter-invalid-yaml", "error", 3), ("ref-broken", "error", 9)]


def test_an_error_without_a_line_of_its_own_is_the_opening_line():
    document = _kept("---\n*x*\n---\n# A\n")
    document.frontmatter_error.line = None
    assert _rows(document)[0] == ("frontmatter-invalid-yaml", "error", 1)


def test_the_message_says_why():
    result = validate(_kept("---\ntitle: [unclosed\n---\n\n# A\n"))
    first = result.diagnostics[0]
    assert first.rule == "frontmatter-invalid-yaml"
    assert first.message.startswith("The frontmatter cannot be read: ")
    assert "expected" in first.message  # what the YAML reader says


def test_the_error_is_the_first_diagnostic_and_only_one():
    document = _kept("---\n@x\n---\n\n# A\n\n{{ref: nope}} {{ref: nada}}\n\n#### Deep\n")
    rules = [r for r, *_ in _rows(document)]
    assert rules[0] == "frontmatter-invalid-yaml" and rules.count("frontmatter-invalid-yaml") == 1
    assert rules.count("ref-broken") == 2


def test_body_diagnostics_have_the_file_line_numbers_whatever_the_block_length():
    short = _kept("---\n@x\n---\n# A\n\n{{ref: nope}}\n")
    long = _kept("---\ntitle: T\ntags:\n  - a\n  - b\n  - [c\n---\n# A\n\n{{ref: nope}}\n")
    assert _rows(short)[1] == ("ref-broken", "error", 6)
    assert _rows(long)[1] == ("ref-broken", "error", 10)


def test_the_first_heading_is_still_required_at_level_one():
    document = _kept("---\n@x\n---\n\n## Scope\n")
    assert [r for r, *_ in _rows(document)] == ["frontmatter-invalid-yaml", "heading-skip"]


def test_the_block_is_not_absent_frontmatter_and_not_a_missing_title():
    rules = [r for r, *_ in _rows(_kept("---\n@x\n---\n\n# A\n"))]
    assert rules == ["frontmatter-invalid-yaml"]
    assert {"frontmatter-absent", "title-missing", "sides-absent", "parties-minimum"}.isdisjoint(rules)


def test_nothing_is_marked_host_dependent():
    document = _kept("---\n@x\n---\n\n# A\n\n{{party: x}} {{ref: nope}}\n")
    assert not any(d.host_dependent for d in validate(document).diagnostics)


def test_the_final_check_still_applies_to_the_body():
    document = _kept("---\n@x\n---\n\n# A\n\nPay {{placeholder: fee, type=money}}.\n")
    assert [r for r, *_ in _rows(document, final=True)] == ["frontmatter-invalid-yaml", "placeholder-unfilled"]
    assert [r for r, *_ in _rows(document)] == ["frontmatter-invalid-yaml"]


# ── What the frontmatter would have declared is not reported ───────

#: Each uses what only the frontmatter declares: a party, a side, an attachment, a question.
_DECLARED = [
    "{{party: x}}",
    "{{side: x}}",
    "{{attach: x}}",
    '{{choose: q, a="x"}}',
    "## Clause {#c when=q:a}\n\nText.",
    "## Clause {#c when=q}\n\nText.",
    "{{placeholder: q}}",
]


@pytest.mark.parametrize("body", _DECLARED)
def test_what_the_frontmatter_declares_is_not_reported_as_undeclared(body):
    document = _kept(f"---\n*x*\n---\n\n# Scope\n\n{body}\n")
    assert [r for r, *_ in _rows(document)] == ["frontmatter-invalid-yaml"]


def test_every_such_use_at_once_yields_only_the_error():
    body = "\n\n".join(_DECLARED[:4])
    document = _kept(f"---\n*x*\n---\n\n# Scope\n\n{body}\n\n## Sub {{#sub when=q:a}}\n\nText.\n")
    assert _rows(document) == [("frontmatter-invalid-yaml", "error", 2)]


def test_the_same_body_in_a_readable_document_with_no_declarations_is_reported():
    # The control: the suppression is the unreadable frontmatter's, not the body's.
    body = "\n\n".join(_DECLARED[:4])
    rules = [r for r, *_ in _rows(parse(f"---\ntitle: T\n---\n\n# Scope\n\n{body}\n"))]
    assert {"party-unknown", "side-unknown", "attach-undeclared", "choose-invalid"} <= set(rules)


def test_what_the_body_alone_decides_is_still_reported():
    body = (
        "# Scope {#scope}\n\n{{ref: nope}} {{term: nope}}\n\n"
        "## Scope {#scope}\n\n{{directive-unknown: x}}\n\n<div>html</div>\n\n#### Deep\n"
    )
    rules = {r for r, *_ in _rows(_kept(f"---\n@x\n---\n\n{body}"))}
    assert {"ref-broken", "term-undefined", "anchor-duplicate", "heading-skip"} <= rules
    assert "frontmatter-invalid-yaml" in rules


def test_term_undefined_is_still_reported():
    # Decision: a definition may come from the original an `amends` names, which the
    # unreadable block might have said, but hiding every undefined term would mask real errors.
    assert [r for r, *_ in _rows(_kept("---\n@x\n---\n\n# A\n\n{{term: nope}}\n"))] == [
        "frontmatter-invalid-yaml", "term-undefined",
    ]


# ── The tools that write ───────────────────────────────────────────

def test_serialize_to_dict_and_save_refuse_a_document_whose_frontmatter_was_not_read(tmp_path):
    source = "---\n*Important* notice\n---\n\n# A\n\nText.\n"
    file = tmp_path / "doc.lgd"
    file.write_bytes(source.encode("utf-8"))
    document = load(file, on_frontmatter_error="keep")
    error = document.frontmatter_error
    with pytest.raises(FrontmatterError) as raised:
        serialize(document)
    assert raised.value is error
    with pytest.raises(FrontmatterError) as raised:
        document.to_dict()
    assert raised.value is error
    with pytest.raises(FrontmatterError) as raised:
        save(document)
    assert raised.value is error
    assert file.read_bytes() == source.encode("utf-8")  # the text of the block is not lost
    with pytest.raises(FrontmatterError):
        save(document, tmp_path / "copy.lgd")
    assert [p.name for p in tmp_path.iterdir()] == ["doc.lgd"]


def test_a_document_whose_error_is_cleared_can_be_written_again():
    document = _kept("---\n@x\n---\n\n# A\n")
    document.frontmatter_error = None
    document.metadata.title = "Fixed"
    assert serialize(document).startswith("---\ntitle: Fixed\n")


def test_a_document_built_from_a_dict_has_no_error():
    assert Document.from_dict({"sections": [{"title": "A", "level": 1}]}).frontmatter_error is None


# ── The command line ───────────────────────────────────────────────

@pytest.mark.parametrize("block", PROSE)
def test_the_command_line_reports_the_block_and_the_body(write, capsys, block):
    path = write("bad.lgd", f"---\n{block}\n---\n\n{_BODY}")
    assert main(["validate", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    diagnostics = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["rule"], d["level"], d["line"], d["file"], d["host_dependent"]) for d in diagnostics] == [
        ("frontmatter-invalid-yaml", "error", 2, str(path), False),
        ("ref-broken", "error", 9, str(path), False),
    ]


def test_the_command_line_text_output_names_both(write, capsys):
    path = write("bad.lgd", f"---\n@x\n---\n\n{_BODY}")
    assert main(["validate", str(path)]) == EXIT_DIAGNOSTICS
    out = capsys.readouterr().out
    assert "[frontmatter-invalid-yaml]" in out and "[ref-broken]" in out


def test_the_command_line_does_not_cascade_from_the_unreadable_block(write, capsys):
    body = "\n\n".join(_DECLARED[:4])
    path = write("bad.lgd", f"---\n@x\n---\n\n# Scope\n\n{body}\n")
    assert main(["validate", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    assert [d["rule"] for d in json.loads(capsys.readouterr().out)["diagnostics"]] == ["frontmatter-invalid-yaml"]


def test_a_fragment_with_frontmatter_that_cannot_be_read_is_include_has_frontmatter(write, capsys):
    # The fragment must not have the block, readable or not: that is the finding, at the
    # problem's line. The body is checked as a fragment's, so what the host may declare
    # is flagged, and frontmatter-invalid-yaml is not added: the block is not read at all.
    path = write("frag.lgd", "---\n@x\n---\n\n## Clause\n\n{{party: x}} {{ref: nope}}\n")
    assert main(["validate", "--fragment", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    diagnostics = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [(d["rule"], d["line"], d["host_dependent"]) for d in diagnostics] == [
        ("include-has-frontmatter", 2, False),
        ("party-unknown", 7, True),
        ("ref-broken", 7, True),
    ]


def test_a_fragment_may_start_at_any_level_when_its_block_cannot_be_read():
    document = _kept("---\n@x\n---\n\n## Clause\n\n### Sub\n")
    assert [r for r, *_ in _rows(document, fragment=True)] == ["include-has-frontmatter"]


def test_a_fragment_block_on_its_first_key_line_is_reported_there_when_it_can_be_read():
    # Unchanged by #43: a readable block is named at its first key.
    document = parse("---\ntitle: T\n---\n\n## Clause\n")
    assert _rows(document, fragment=True) == [("include-has-frontmatter", "error", 2)]
