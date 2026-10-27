"""Frontmatter that cannot be read does not stop the validation of the body (#43)."""
from __future__ import annotations

import json

import pytest
import yaml

from legaldown import (
    Document,
    FrontmatterError,
    document_to_dict,
    load,
    parse,
    parse_template,
    save,
    serialize,
    validate,
)
from legaldown.assembly import frontmatter_diagnostic
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
    for write in (serialize, Document.to_dict, document_to_dict, save):
        with pytest.raises(FrontmatterError) as raised:
            write(document)
        # A new exception each time, the kept one its cause: it is not raised again, so its
        # traceback does not grow and it is not tied to the frames that raised it.
        assert raised.value is not error and raised.value.__cause__ is error
        assert (str(raised.value), raised.value.line) == (str(error), error.line)
    assert error.__traceback__ is None
    assert file.read_bytes() == source.encode("utf-8")  # the text of the block is not lost
    with pytest.raises(FrontmatterError):
        save(document, tmp_path / "copy.lgd")
    assert [p.name for p in tmp_path.iterdir()] == ["doc.lgd"]


def test_the_dict_of_such_a_document_cannot_be_the_way_to_write_it_out():
    # document_to_dict is public: Document.from_dict(document_to_dict(d)) -> serialize
    # would write the empty frontmatter in place of the block.
    document = _kept("---\n@x\n---\n\n# A\n")
    with pytest.raises(FrontmatterError):
        Document.from_dict(document_to_dict(document))


def test_a_document_whose_error_is_cleared_can_be_written_again():
    document = _kept("---\n@x\n---\n\n# A\n")
    document.frontmatter_error = None
    document.metadata.title = "Fixed"
    assert serialize(document).startswith("---\ntitle: Fixed\n")


def test_a_document_keeps_reporting_the_error_until_it_is_cleared():
    # Parsed with "keep", it is validated against empty metadata for as long as the error is set;
    # a caller that repairs the metadata in code clears it.
    document = _kept("---\n@x\n---\n\n# A\n")
    document.metadata.title = "Repaired"
    assert [r for r, *_ in _rows(document)] == ["frontmatter-invalid-yaml"]
    document.frontmatter_error = None
    assert [r for r, *_ in _rows(document)] == ["sides-absent"]  # as for any document with this metadata
    assert serialize(document).startswith("---\ntitle: Repaired\n")


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


# ── What the YAML reader refuses, and what is said of it ───────────

#: A character YAML does not allow fails the reader before it reads anything.
_CONTROL = "---\ntitle: A\x01b\n---\n\n# A\n\nSee {{ref: nope}}.\n"

_UNREADABLE = [
    ("title: [unclosed", 2),
    ('title: T\nsubtitle: "open\nlanguage: en', 3),
    ("title: Fixture\n  bad indent: [unclosed", 3),
    ("title: A\x01b", 2),
    ("title: T\nlanguage: \x00", 3),
    ("!!set {a, b}", 2),
    ("title: T\nquestions:\n  q:\n    default: !!bool maybe", None),
    ("a: " + "[" * 3000, None),
]


@pytest.mark.parametrize("keep", [False, True])
def test_a_character_yaml_does_not_allow_is_a_frontmatter_error_in_both_modes(keep):
    if keep:
        document = _kept(_CONTROL)
        assert [(r, line) for r, _, line in _rows(document)] == [("frontmatter-invalid-yaml", 2), ("ref-broken", 7)]
        assert isinstance(document.frontmatter_error.__cause__, Exception)
    else:
        with pytest.raises(FrontmatterError) as raised:
            parse(_CONTROL)
        assert raised.value.line == 2 and isinstance(raised.value.__cause__, yaml.YAMLError)


def test_the_line_of_a_character_the_reader_refuses_is_its_line_in_the_file():
    assert _kept("---\ntitle: T\nsubtitle: S\nlanguage: \x01\n---\n# A\n").frontmatter_error.line == 4


def test_the_command_line_reports_a_character_yaml_does_not_allow(write, capsys):
    path = write("ctrl.lgd", _CONTROL)
    assert main(["validate", "--format", "json", str(path)]) == EXIT_DIAGNOSTICS
    captured = capsys.readouterr()
    assert captured.err == ""  # not an internal error
    assert [(d["rule"], d["line"]) for d in json.loads(captured.out)["diagnostics"]] == [
        ("frontmatter-invalid-yaml", 2),
        ("ref-broken", 7),
    ]


@pytest.mark.parametrize(("block", "line"), _UNREADABLE)
def test_the_message_is_one_line_without_a_position_or_a_snippet(block, line):
    document = _kept(f"---\n{block}\n---\n\n# A\n")
    message = validate(document).diagnostics[0].message
    assert message.startswith("The frontmatter cannot be read: ") and message.strip() == message
    assert "\n" not in message and "column" not in message and "<unicode string>" not in message
    if line is not None:
        assert document.frontmatter_error.line == line
    assert "\n" not in str(document.frontmatter_error)


def test_the_message_says_what_is_wrong_and_where_it_was_being_read():
    error = _kept("---\ntitle: [unclosed\n---\n# A\n").frontmatter_error
    assert str(error) == "expected ',' or ']', but got '<stream end>' (while parsing a flow sequence)"
    assert str(_kept(_CONTROL).frontmatter_error) == "unacceptable character #x0001: special characters are not allowed"


@pytest.mark.parametrize(("block", "line"), _UNREADABLE)
def test_validation_and_assembly_say_the_same_of_the_same_block(block, line):
    source = f"---\n{block}\n---\n\n# A\n"
    reported = validate(_kept(source)).diagnostics[0]
    with pytest.raises(FrontmatterError) as raised:
        parse(source)
    assembled = frontmatter_diagnostic(raised.value)
    assert (assembled.rule, assembled.level, assembled.message, assembled.line) == (
        reported.rule, reported.level, reported.message, reported.line,
    )


def test_the_command_line_says_the_same_in_validate_assemble_and_questions(write, capsys):
    path = write("t.lgd", "---\ntitle: Fixture\n  bad indent: [unclosed\n---\n\n# A\n")
    main(["validate", "--format", "json", str(path)])
    validated = json.loads(capsys.readouterr().out)["diagnostics"][0]
    main(["questions", str(path), "--format", "json"])
    asked = json.loads(capsys.readouterr().out)["problems"][0]
    main(["assemble", str(path)])
    assembled = capsys.readouterr().err
    assert (validated["rule"], validated["line"]) == (asked["rule"], asked["line"]) == ("frontmatter-invalid-yaml", 3)
    assert validated["message"] == asked["message"] == "The frontmatter cannot be read: mapping values are not allowed here"
    assert assembled == f"{path}:3: error: [frontmatter-invalid-yaml] {validated['message']}\n"


def test_the_error_kept_has_the_yaml_error_for_its_cause_as_raising_has():
    source = "---\ntitle: [unclosed\n---\n# A\n"
    with pytest.raises(FrontmatterError) as raised:
        parse(source)
    kept = _kept(source).frontmatter_error
    assert isinstance(raised.value.__cause__, yaml.YAMLError)
    assert type(kept.__cause__) is type(raised.value.__cause__)


def test_a_wrong_option_is_refused_before_a_file_is_read(tmp_path):
    with pytest.raises(ValueError, match="on_frontmatter_error"):
        load(tmp_path / "nope.lgd", on_frontmatter_error="Keep")  # type: ignore[arg-type]
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "nope.lgd", on_frontmatter_error="keep")


# ── What a kept error holds, and where it is ───────────────────────

@pytest.mark.parametrize("block", ["@x", "!!set {a, b}", "[unclosed", "title: A\x01b", "questions:\n  q:\n    default: !!bool maybe"])
def test_a_kept_error_holds_no_frames(block):
    error = _kept(f"---\n{block}\n---\n# A\n").frontmatter_error
    assert error.__traceback__ is None
    seen = []
    cause = error.__cause__ or error.__context__
    while cause is not None and cause not in seen:
        assert cause.__traceback__ is None
        seen.append(cause)
        cause = cause.__cause__ or cause.__context__
    # The YAML error is the cause, where there is one (the !!set is this parser's own).
    assert (error.__cause__ is None) == (block == "!!set {a, b}")


def test_a_value_its_tag_cannot_hold_says_so_and_where():
    error = _kept("---\ntitle: T\nquestions:\n  q:\n    default: !!bool maybe\n---\n# A\n").frontmatter_error
    assert (str(error), error.line) == ("a value its tag cannot hold: 'maybe'", 5)
    assert isinstance(error.__cause__, KeyError)
    error = _kept("---\ntitle: T\nquestions:\n  q:\n    default: " + "9" * 5000 + "\n---\n# A\n").frontmatter_error
    assert error.line == 5 and str(error).startswith("a value its tag cannot hold: ")
    assert "\n" not in str(error)


def test_a_value_its_tag_cannot_hold_is_reported_at_its_line_by_validate():
    document = _kept("---\ntitle: T\nquestions:\n  q:\n    default: !!bool maybe\n---\n# A\n")
    assert _rows(document)[0] == ("frontmatter-invalid-yaml", "error", 5)


@pytest.mark.parametrize("ending", ["\x85", "\u2028", "\u2029"])
def test_the_line_counts_line_feeds_only_as_the_format_does(ending):
    # YAML ends a line at NEL, LS and PS too, which the file does not: the line is the file's.
    cases = [
        (f"title: \"A{ending}B\"\nbad: [unclosed", 3),  # a parser error, found after the break
        (f"title: \"A{ending}B\"\nlanguage: \x01", 3),  # a character the reader refuses
        (f"title: \"A{ending}B\"\nquestions:\n  q:\n    default: !!bool maybe", 5),  # a value its tag cannot hold
        (f"title: \"A{ending}B\"\nsubtitle: 'open\nlanguage: en", 3),  # a quote, where it opens
    ]
    for block, line in cases:
        assert _kept(f"---\n{block}\n---\n\n# A\n\n{{{{ref: nope}}}}\n").frontmatter_error.line == line, block


def test_the_line_of_a_set_is_counted_the_same_way():
    assert _kept("---\n# a\n# b\n!!set {a, b}\n---\n").frontmatter_error.line == 4


# ── Writing, and the guard ─────────────────────────────────────────

def test_save_without_a_path_refuses_the_document_before_it_asks_for_one():
    document = _kept("---\n@x\n---\n# A\n")
    assert document.path is None
    with pytest.raises(FrontmatterError):
        save(document)


def test_the_guard_raises_a_frontmatter_error_whatever_was_stored():
    document = _kept("---\n@x\n---\n# A\n")
    document.frontmatter_error = ValueError("by hand")  # type: ignore[assignment]
    for write in (serialize, Document.to_dict, save):
        with pytest.raises(FrontmatterError, match="by hand") as raised:
            write(document)
        assert raised.value.line is None and isinstance(raised.value.__cause__, ValueError)


# ── Assembly, in the files it reads ────────────────────────────────

_ASSEMBLED = "---\ntitle: T\n---\n\n# A\n\n{{include: f.lgd}}\n"


def test_assembly_says_of_a_fragment_what_validate_says_with_the_fragments_file_and_line():
    fragment = "---\ntitle: Fixture\n  bad indent: [unclosed\n---\n\nText.\n"
    reported = validate(_kept(fragment)).diagnostics[0]
    (assembled,) = parse_template(_ASSEMBLED, resolve=lambda path: fragment).problems
    assert (assembled.rule, assembled.level, assembled.message, assembled.line) == (
        reported.rule, reported.level, reported.message, reported.line,
    )
    assert assembled.file == "f.lgd"  # the normalised path: the message does not carry it


def test_assembly_names_a_fragment_by_its_normalised_path():
    template = _ASSEMBLED.replace("f.lgd", "./sub/../f.lgd")
    (assembled,) = parse_template(template, resolve=lambda path: "---\n@x\n---\n\nText.\n").problems
    assert assembled.file == "f.lgd"


def test_the_command_line_names_the_fragment_file_and_its_line(tmp_path, capsys):
    (tmp_path / "t.lgd").write_text(_ASSEMBLED, encoding="utf-8")
    (tmp_path / "f.lgd").write_text("---\ntitle: T\n  bad: [x\n---\n\nText.\n", encoding="utf-8")
    assert main(["assemble", str(tmp_path / "t.lgd")]) == EXIT_DIAGNOSTICS
    shown = capsys.readouterr().err
    message = "The frontmatter cannot be read: mapping values are not allowed here"
    assert f"{tmp_path / 'f.lgd'}:3: error: [frontmatter-invalid-yaml] {message}" in shown
    # The template's own is on the template.
    (tmp_path / "t.lgd").write_text("---\n@x\n---\n\n# A\n", encoding="utf-8")
    assert main(["assemble", str(tmp_path / "t.lgd")]) == EXIT_DIAGNOSTICS
    assert f"{tmp_path / 't.lgd'}:2: error: [frontmatter-invalid-yaml]" in capsys.readouterr().err
