"""The Warning that §17.5 asks for a check category that was not performed
(``ldv-check-skipped``, #107): one for each category of §17.4 whose construct the
document holds, at the line of the first one, naming exactly what this validation did not
do. A document without such a construct draws none (§17.1)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from legaldown import LegaldownDeprecationWarning, Template, load, parse, validate
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_OK, main
from legaldown.grammar import IMPLEMENTATION_RULE_PREFIX, SKIPPED_RULE
from legaldown.positions import Locator
from legaldown.validator.fragments import IncludeOutcome, UnreadInclude
from legaldown.validator.skipped import _counted, skipped_categories
from legaldown.validator.templates import IncludeDirective

_FRONT = (
    "---\ntitle: Fixture\ndocument_type: contract\nsides:\n"
    "  - name: providers\n    label: Provider\n    parties:\n      - name: acme\n        type: legal_entity\n"
    "        legal_name: Acme Corporation\n"
    "  - name: clients\n    label: Client\n    parties:\n      - name: beta\n        type: legal_entity\n"
    "        legal_name: Beta Industries Inc.\n"
    "---\n\n"
)
_TEMPLATE_FRONT = _FRONT.replace("---\n\n", "questions:\n  x:\n    type: boolean\n---\n\n")
_BODY = "# Scope {#scope}\n\nText.\n\n"


def _with(front_lines: str, body: str = _BODY) -> str:
    """A document whose frontmatter holds *front_lines* besides the fixture's."""
    return _FRONT.replace("---\n\n", front_lines + "---\n\n") + body


def _warnings(text: str, **kwargs):
    """The §17.5 Warnings of *text*, parsed with a name and validated with *kwargs*."""
    return [d for d in validate(parse(text, filename="main.lgd"), **kwargs).diagnostics if d.rule == SKIPPED_RULE]


def _at(text: str, needle: str) -> int:
    return next(number for number, line in enumerate(text.split("\n"), 1) if needle in line)


def _loader(files: dict[str, str]):
    return files.get


# ── No construct, no Warning ─────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    _FRONT + _BODY,
    "# Plain\n\nNo frontmatter.\n",
    _with("translations: {}\n"),
    _with("amends:\n  title: Original\n"),
    _with("supersedes: Old agreement\n"),
    _with("attachments:\n  - id: a\n    title: A\n    file: ''\n", _BODY + "{{attach: a}}\n"),
    _TEMPLATE_FRONT + _BODY + "Text. {when=x}\n",
])
def test_a_document_without_a_construct_beyond_core_draws_none(text):
    assert _warnings(text) == []


@pytest.mark.parametrize("body", [
    "```\n{{include: p.lgd}}\n```\n",
    "<!-- {{include: p.lgd}} -->\n",
    "See {{include: p.lgd}} for more.\n",
    "- {{include: p.lgd}}\n",
    "> [!DRAFTING]\n> {{include: p.lgd}}\n",
    "`{{include: p.lgd}}` and `![a](x.png)`\n",
    "```\n![a](x.png)\n```\n",
])
def test_a_construct_that_is_text_draws_none(body):
    assert _warnings(_FRONT + _BODY + body) == []


# ── Includes (§12, §16.11) ───────────────────────────────────────────────

def test_an_include_read_by_no_loader_draws_one_warning_at_its_line():
    text = _FRONT + _BODY + "{{include: p.lgd}}\n\n{{include: q.lgd}}\n"
    (warning,) = _warnings(text)
    assert (warning.level, warning.line, warning.host_dependent) == ("warning", _at(text, "p.lgd"), False)
    message = warning.message
    assert message.startswith("2 `{{include:}}` paragraphs; 2 includes not read (2 have no loader to read them: no `resolve`, and the document has no path)")
    # Fragments not read: none of the checks that read one was made, the two that follow includes included.
    for rule in ("include-file-missing", "include-has-frontmatter", "include-has-h1", "include-heading-skip",
                 "include-cycle", "include-anchor-duplicate", "combined-document checks (§16.11)"):
        assert rule in message
    assert "template-fragment-invalid" not in message  # not a template


def test_an_include_read_by_a_loader_still_names_what_is_never_performed():
    text = _FRONT + _BODY + "{{include: p.lgd}}\n"
    (warning,) = _warnings(text, resolve=_loader({"p.lgd": "## Part {#part}\n\nText.\n"}))
    assert warning.line == _at(text, "p.lgd")
    message = warning.message
    assert "not read" not in message
    # Implemented (#114): neither is named when the fragments were read; the combined document is still never built.
    assert "include-cycle" not in message and "include-anchor-duplicate" not in message
    assert "not implemented" not in message
    assert "combined-document checks (§16.11)" in message
    # Done, not listed: the fragment was read, so these were made.
    assert "include-file-missing" not in message and "include-has-frontmatter" not in message


def test_a_template_names_the_part_of_the_fragment_check_that_reads_a_file():
    text = _TEMPLATE_FRONT + _BODY + "{{include: p.lgd}}\n"
    (warning,) = _warnings(text)
    assert "the parts of template-fragment-invalid that read a fragment" in warning.message
    (read,) = _warnings(text, resolve=_loader({"p.lgd": "## Part {#part}\n\nText.\n"}))
    assert "template-fragment-invalid" not in read.message


def test_a_conditional_include_is_judged_under_every_combination_and_the_warning_names_no_such_limit():
    text = _TEMPLATE_FRONT + _BODY + "{{include: p.lgd}} {when=x}\n"
    (warning,) = _warnings(text, resolve=_loader({"p.lgd": "## Part {#part}\n\nText.\n"}))
    # Tried (#108), so nothing is left to say of it: only past the cap of the combinations is it named.
    assert "every combination" not in warning.message and "judged with every include present" not in warning.message


def test_the_includes_warning_of_the_end_state_names_only_the_combined_document_when_everything_was_read():
    # Everything the includes call for is done (#109, #114, #108) but the combined document (§16.11, #117).
    text = _TEMPLATE_FRONT + _BODY + "{{include: p.lgd}} {when=x}\n"
    (warning,) = _warnings(text, resolve=_loader({"p.lgd": "## Part {#part}\n\nText.\n"}))
    assert warning.message == (
        "1 `{{include:}}` paragraph. Not performed: the combined-document checks (§16.11), such as a reference "
        "or a term into a fragment, or an auto-generated identifier the fragments and the document share "
        "(include processing is Full, §17.4; §17.5)."
    )
    # ... and for fragments not read, the rules that read or join them, in this order.
    (unread,) = _warnings(text)
    assert (
        "include-file-missing, include-has-frontmatter, include-has-h1, include-heading-skip, include-cycle, "
        "include-anchor-duplicate and the parts of template-fragment-invalid that read a fragment for the "
        "fragments not read; the combined-document checks"
    ) in unread.message


def test_each_reason_an_include_is_not_read_is_counted_apart():
    text = _FRONT + _BODY + "{{include: gone.lgd}}\n\n{{include: ../out.lgd}}\n\n{{include: absent.lgd}}\n"
    result = validate(parse(text, filename="main.lgd"), resolve=_loader({}))
    (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
    assert "2 could not be read (include-file-missing)" in warning.message
    assert "1 leads out of the document's directory, which is not read" in warning.message
    # Asked for, and a file that is not there is an Error of its own: that check was made.
    assert [d.rule for d in result.diagnostics if d.rule == "include-file-missing"] == ["include-file-missing"] * 2
    assert "include-file-missing, include-has-frontmatter" in warning.message  # for the one that leads out


def test_an_include_of_the_document_itself_is_include_cycle_and_not_unread(tmp_path):
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _BODY + "{{include: main.lgd}}\n", encoding="utf-8")
    result = validate(load(path))
    assert [d.rule for d in result.diagnostics if d.rule == "include-cycle"] == ["include-cycle"]
    (warning,) = [d for d in result.diagnostics if d.rule == SKIPPED_RULE]
    # An Error of its own, like a path judged: not an include left unread.
    assert "not read" not in warning.message and "include-cycle" not in warning.message


def test_an_include_that_is_already_a_finding_is_not_counted_as_unread():
    # A path judged (path-not-relative) and a file that is not LegalDown (include-not-legaldown) are
    # Errors; nothing is skipped about them. The Warning is still there: the combined document is never built.
    text = _FRONT + _BODY + "{{include: /abs.lgd}}\n\n{{include: n.txt}}\n"
    result = validate(parse(text, filename="main.lgd"), resolve=_loader({}))
    assert {d.rule for d in result.diagnostics} >= {"path-not-relative", "include-not-legaldown"}
    (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
    assert "not read" not in warning.message and warning.message.startswith("2 `{{include:}}` paragraphs. ")
    assert "combined-document checks (§16.11)" in warning.message


def test_a_fragment_validated_on_its_own_reads_no_file_for_its_includes():
    text = "## Part {#part}\n\n{{include: q.lgd}}\n"
    result = validate(parse(text), fragment=True)
    (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
    assert warning.line == 3 and not warning.host_dependent
    assert "1 include not read (1 is in a fragment validated on its own, which reads no file)" in warning.message


# ── The other categories of §17.4 ───────────────────────────────────────

_ATTACHED = "attachments:\n  - id: a\n    title: A\n    file: a.lgd\n  - id: b\n    title: B\n    file: b.pdf\n"


def test_attachment_files_draw_one_warning_at_the_first():
    text = _with(_ATTACHED, _BODY + "{{attach: a}} {{attach: b}}\n")
    (warning,) = _warnings(text)
    assert warning.line == _at(text, "file: a.lgd")
    assert warning.message.startswith("2 attachment files declared (1 LegalDown). Not performed: ")
    for rule in ("attachment-file-missing", "attachment-has-frontmatter", "attachment-has-h1",
                 "attachment-anchor-duplicate", "importing the definitions of 1 LegalDown attachment file (1 has no loader to read it: no `resolve`, and the document has no path)"):
        assert rule in warning.message
    (read,) = _warnings(text, resolve=_loader({"a.lgd": "Text.\n"}))
    assert "importing" not in read.message and "attachment-anchor-duplicate" in read.message
    # A loader that has not the file did not import its definitions either.
    (missing,) = _warnings(text, resolve=lambda _path: None)
    assert "importing the definitions of 1 LegalDown attachment file (1 could not be read)" in missing.message


def test_an_attachment_that_is_not_legaldown_is_only_asked_for_its_existence():
    text = _with("attachments:\n  - id: b\n    title: B\n    file: b.pdf\n", _BODY + "{{attach: b}}\n")
    (warning,) = _warnings(text)
    assert warning.message.startswith("1 attachment file declared (0 LegalDown). Not performed: attachment-file-missing ")
    assert "attachment-has-h1" not in warning.message


def _attached(file: str) -> str:
    return _with(f"attachments:\n  - id: a\n    title: A\n    file: '{file}'\n", _BODY + "{{attach: a}}\n")


def _amended(file: str) -> str:
    return _with(f"amends:\n  title: Original\n  file: '{file}'\n")


def test_a_path_that_leads_out_of_the_directory_is_an_import_that_did_not_happen_for_both():
    # Valid (§2.3), and never asked for without a document root (#109): the same as an include of it.
    asked: list[str] = []

    def loader(path: str) -> str | None:
        asked.append(path)
        return None

    (warning,) = _warnings(_attached("../shared/defs.lgd"), resolve=loader)
    assert (
        "importing the definitions of 1 LegalDown attachment file "
        "(1 leads out of the document's directory, which is not read without a document root)"
    ) in warning.message
    (amends,) = _warnings(_amended("../shared/orig.lgd"), resolve=loader)
    assert "amend-term-undefined" in amends.message and asked == []


def test_each_reason_is_counted_and_worded_by_its_number():
    front = (
        "attachments:\n"
        + "".join(f"  - id: a{i}\n    title: A\n    file: '{file}'\n" for i, file in enumerate(
            ["../one.lgd", "gone.lgd", "gone2.lgd", "x\\\\y.lgd"]
        ))
    )
    (warning,) = _warnings(_with(front), resolve=lambda _path: None)
    assert (
        "importing the definitions of 4 LegalDown attachment files "
        "(1 leads out of the document's directory, which is not read without a document root; 3 could not be read)"
    ) in warning.message
    (none,) = _warnings(_with(front), resolve=None)
    # With no loader at all, that is the reason for every one that is asked about (a path that leads out too).
    assert "importing the definitions of 4 LegalDown attachment files (4 have no loader to read them: no `resolve`, and the document has no path)" in none.message


def test_a_backslash_or_null_byte_path_is_unreadable_and_said_so():
    # No finding says anything of it, and no loader is asked: the Warning does.
    def refuse(_path: str) -> str | None:
        pytest.fail("asked")

    for file in ("a\\\\b.lgd", "a\0b.lgd"):
        document = parse(_attached("x.lgd"))
        document.metadata.attachments[0].file = file  # (a NUL cannot be written in YAML)
        (warning,) = [d for d in validate(document, resolve=refuse).diagnostics if d.rule == SKIPPED_RULE]
        assert "importing the definitions of 1 LegalDown attachment file (1 could not be read)" in warning.message
        document = parse(_amended("x.lgd"))
        document.metadata.amends.file = file
        (amends,) = [d for d in validate(document, resolve=refuse).diagnostics if d.rule == SKIPPED_RULE]
        assert "amend-term-undefined" in amends.message


def test_a_path_that_is_a_finding_is_an_import_that_did_not_happen_for_an_attachment(tmp_path):
    # Its definitions are not imported either (a def-duplicate-id against them is not made): every
    # reason but its own path is said.
    absolute = validate(parse(_attached("/abs.lgd")), resolve=lambda _path: pytest.fail("asked"))
    outside = validate(parse(_attached("../out.lgd")), resolve=lambda _path: pytest.fail("asked"), document_root=tmp_path)
    for result, found in ((absolute, "path-not-relative"), (outside, "path-outside-root")):
        assert found in result.rules()
        (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
        assert "(1 has a path refused by path-not-relative or path-outside-root)" in warning.message, found


def test_a_path_that_is_a_finding_is_still_an_original_not_imported(tmp_path):
    # A failed import of the original drops a term to the Info amend-term-unresolvable: the Error
    # check is not made for any reason, and the Warning says so.
    absolute = validate(parse(_amended("/abs.lgd")), resolve=lambda _path: pytest.fail("asked"))
    outside = validate(parse(_amended("../out.lgd")), resolve=lambda _path: pytest.fail("asked"), document_root=tmp_path)
    for result, found in ((absolute, "path-not-relative"), (outside, "path-outside-root")):
        assert found in result.rules()
        (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
        assert "amend-term-undefined" in warning.message, found


def test_the_document_itself_is_no_import_that_did_not_happen_for_an_attachment_but_is_for_the_original(tmp_path):
    # An attachment that is the document adds nothing to it. An original that is the document is not
    # imported either, so its terms are the Info and the Error check is not made.
    path = tmp_path / "main.lgd"
    path.write_text(_attached("main.lgd"), encoding="utf-8")
    (warning,) = [d for d in validate(load(path)).diagnostics if d.rule == SKIPPED_RULE]
    assert "importing" not in warning.message
    path.write_text(_amended("main.lgd") + "{{term: foo}}\n", encoding="utf-8")
    result = validate(load(path))
    (warning,) = [d for d in result.diagnostics if d.rule == SKIPPED_RULE]
    assert "amend-term-undefined" in warning.message and "amend-term-unresolvable" in result.rules()


def test_a_deprecated_importer_that_gave_nothing_is_an_import_that_did_not_happen_whatever_the_path():
    for file in ("../x.lgd", "/abs.lgd", "ok.lgd"):
        with pytest.warns(LegaldownDeprecationWarning):
            result = validate(parse(_attached(file)), import_attachment_definitions=lambda _path: None)
        (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
        assert "importing the definitions of 1 LegalDown attachment file (1 could not be read)" in warning.message
        with pytest.warns(LegaldownDeprecationWarning):
            result = validate(parse(_attached(file)), import_attachment_definitions=lambda _path: {})
        (warning,) = (d for d in result.diagnostics if d.rule == SKIPPED_RULE)
        assert "importing" not in warning.message


def test_an_amendment_draws_one_warning_at_its_file():
    text = _with("amends:\n  title: Original\n  file: orig.lgd\n")
    (warning,) = _warnings(text)
    assert warning.line == _at(text, "file: orig.lgd")
    assert "amends-file-missing" in warning.message and "amend-term-undefined" in warning.message
    # The original was read: its terms were resolved against it, so only the existence is not.
    (read,) = _warnings(text, resolve=_loader({"orig.lgd": "# O {#o}\n\nText.\n"}))
    assert "amends-file-missing" in read.message and "amend-term-undefined" not in read.message


def test_translations_draw_one_warning_that_names_the_bilingual_checks():
    text = _with("language: en\nauthoritative: en\ntranslations:\n  cs: x.cs.lgd\n  de: x.de.lgd\n")
    (warning,) = _warnings(text)
    assert warning.line == _at(text, "translations:")
    assert warning.message.startswith("translations declared (2 linked documents); bilingual validation not performed: ")
    for rule in ("translation-file-missing", "translation-hierarchy-mismatch", "translation-anchor-mismatch",
                 "translation-def-mismatch", "translation-language-set-mismatch", "translation-template-mismatch"):
        assert rule in warning.message
    # The identifier rules are Core (§17.2) for the document itself, and checked: only the
    # linked files' identifiers were not read, and the message says so, and that this document's were.
    assert warning.message.endswith(
        "; translation-implicit-id and translation-authoritative-absent for the linked files, whose "
        "identifiers were not read (this document's own were checked) "
        "(§14, §16.7; bilingual validation is Full, §17.4; §17.5)."
    )
    assert "translation-file-missing, translation-hierarchy-mismatch" in warning.message


def test_a_superseded_file_and_an_image_path_are_declared_paths_not_checked_for_existence():
    text = _with("supersedes:\n  title: Old\n  file: old.lgd\n", _BODY + "![logo](logo.png)\n")
    (warning,) = _warnings(text)
    assert warning.line == _at(text, "file: old.lgd")
    assert warning.message.startswith("supersedes.file and an image path declared. Not performed: supersedes-file-missing")
    assert "existence of image paths (§8.7), which has no rule id" in warning.message
    # An object `supersedes` alone, and a string one (a description, §3.2, not a path).
    (alone,) = _warnings(_with("supersedes:\n  title: Old\n  file: old.lgd\n"))
    assert "image" not in alone.message
    assert _warnings(_with("supersedes: Old\n")) == []


def test_the_first_image_gives_the_warning_its_line():
    text = _FRONT + _BODY + "![a](one.png) and ![b](two.png)\n\n![c](three.png)\n"
    (warning,) = _warnings(text)
    assert warning.line == _at(text, "one.png")
    assert warning.message.startswith("an image path declared. Not performed: the existence of image paths")


@pytest.mark.parametrize("path", ["#top", "?q=1", "{{placeholder: logo}}", "#a?b"])
def test_an_image_that_names_no_file_is_no_image_to_check(path):
    text = _FRONT + _BODY + f"![x]({path})\n"
    assert _warnings(text) == []
    # ... and a later one that names a file is the one the Warning stands at.
    later = text + "\n![y](ok.png)\n"
    (warning,) = _warnings(later)
    assert warning.line == _at(later, "ok.png")


def test_an_image_in_a_drafting_note_is_no_image_to_check():
    note = "> [!DRAFTING]\n> ![a](note.png)\n\n"
    assert _warnings(_FRONT + _BODY + note) == []
    text = _FRONT + _BODY + note + "![b](body.png)\n"
    assert _warnings(text)[0].line == _at(text, "body.png")
    # It is judged all the same (§15.6).
    assert "path-not-relative" in validate(parse(_FRONT + _BODY + "> [!DRAFTING]\n> ![a](/abs.png)\n")).rules()
    # By a link reference definition too.
    by_label = _FRONT + _BODY + "> [!DRAFTING]\n> ![a][r]\n\n[r]: pic.png\n"
    assert _warnings(by_label) == []


def test_an_image_by_a_link_reference_definition_is_one_too():
    text = _FRONT + _BODY + "![a][r]\n\n[r]: pic.png\n"
    (warning,) = _warnings(text)
    assert warning.line == _at(text, "[r]: pic.png")


def test_an_image_path_that_is_a_finding_is_no_image_to_check():
    text = _FRONT + _BODY + "![a](/abs.png)\n"
    result = validate(parse(text))
    assert "path-not-relative" in result.rules() and SKIPPED_RULE not in result.rules()
    # A later one that is fine is the one the Warning stands at.
    later = _FRONT + _BODY + "![a](/abs.png)\n\n![b](ok.png)\n"
    (warning,) = [d for d in validate(parse(later)).diagnostics if d.rule == SKIPPED_RULE]
    assert warning.line == _at(later, "ok.png")


def test_every_category_at_once_is_five_warnings_in_the_order_of_the_table():
    front = (
        "language: en\nauthoritative: en\ntranslations:\n  cs: x.cs.lgd\n" + _ATTACHED
        + "amends:\n  title: O\n  file: o.lgd\nsupersedes:\n  title: S\n  file: s.lgd\n"
    )
    text = _with(front, _BODY + "{{include: p.lgd}}\n\n![a](a.png)\n\n{{attach: a}} {{attach: b}}\n")
    warnings = _warnings(text)
    starts = ["1 `{{include:}}` paragraph", "2 attachment files", "amends.file", "translations declared",
              "supersedes.file and an image path"]
    assert [w.message.startswith(start) for w, start in zip(warnings, starts, strict=True)] == [True] * 5
    assert len({w.line for w in warnings}) == 5


# ── What the Warning is ───────────────────────────────────────────────────

def test_the_rule_is_an_implementation_rule_of_level_warning():
    assert SKIPPED_RULE == "ldv-check-skipped" and SKIPPED_RULE.startswith(IMPLEMENTATION_RULE_PREFIX)
    text = _FRONT + _BODY + "{{include: p.lgd}}\n"
    result = validate(parse(text, filename="main.lgd"))
    assert result.is_valid and [d.level for d in result.diagnostics if d.rule == SKIPPED_RULE] == ["warning"]
    fixtures = os.environ.get("LEGALDOWN_FIXTURES_DIR")
    if fixtures and (Path(fixtures) / "coverage.json").exists():
        assert SKIPPED_RULE not in (Path(fixtures) / "coverage.json").read_text(encoding="utf-8")


def test_the_fragment_wording_has_a_plural():
    text = "## Part {#part}\n\n{{include: q.lgd}}\n\n{{include: r.lgd}}\n"
    (warning,) = [d for d in validate(parse(text), fragment=True).diagnostics if d.rule == SKIPPED_RULE]
    assert "2 includes not read (2 are in a fragment validated on its own, which reads no file)" in warning.message


def test_a_parsed_document_with_no_loader_is_told_what_to_do_about_it():
    # A document with no path has nothing to read beside; a loaded one has a loader, so never this reason.
    (parsed,) = _warnings(_FRONT + _BODY + "{{include: p.lgd}}\n")
    assert "1 has no loader to read it: no `resolve`, and the document has no path" in parsed.message


def test_parse_strips_the_paths_the_references_are_judged_by():
    # `_definitions_in` relies on it: it does not strip a path again.
    document = parse(
        _with("attachments:\n  - id: a\n    title: A\n    file: ' a.lgd '\namends:\n  title: O\n  file: ' o.lgd '\n")
    )
    assert document.metadata.attachments[0].file == "a.lgd" and document.metadata.amends.file == "o.lgd"


def test_a_count_with_no_wording_is_refused():
    with pytest.raises(ValueError, match="would not add up"):
        _counted("include", ["no-resolver", "own"], {"no-resolver": ("a", "b")})


def test_the_command_exits_zero_unless_strict_and_can_ignore_the_rule(tmp_path, capsys):
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _BODY + "![a](a.png)\n", encoding="utf-8")
    assert main(["validate", str(path)]) == EXIT_OK
    assert f"{path}:{_at(path.read_text(), 'a.png')}: warning: [ldv-check-skipped]" in capsys.readouterr().out
    assert main(["validate", "--strict", str(path)]) == EXIT_DIAGNOSTICS
    capsys.readouterr()
    assert main(["validate", "--ignore", SKIPPED_RULE, "--strict", str(path)]) == EXIT_OK
    assert SKIPPED_RULE not in capsys.readouterr().out
    assert main(["validate", "--format", "json", str(path)]) == EXIT_OK
    records = json.loads(capsys.readouterr().out)["diagnostics"]
    assert [r["rule"] for r in records] == [SKIPPED_RULE] and records[0]["level"] == "warning"


def test_the_command_reads_the_fragments_beside_the_document_and_still_says_what_is_not_done(tmp_path, capsys):
    (tmp_path / "p.lgd").write_text("## Part {#part}\n\nText.\n", encoding="utf-8")
    path = tmp_path / "main.lgd"
    path.write_text(_FRONT + _BODY + "{{include: p.lgd}}\n", encoding="utf-8")
    assert main(["validate", str(path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[ldv-check-skipped]" in out and "combined-document checks (§16.11)" in out and "not read" not in out


def test_assembly_is_unchanged_by_it(tmp_path):
    text = _TEMPLATE_FRONT + _BODY + "{{include: p.lgd}}\n\nBeta. {when=x}\n"
    files = {"p.lgd": "## Part {#part}\n\nText.\n"}
    template = Template(text, resolve=files.get)
    assert not template.problems
    assert SKIPPED_RULE in {d.rule for d in template.validation.diagnostics}
    assembled = template.form({"x": True}).assemble()
    assert assembled.ok and not assembled.diagnostics and "ldv-" not in assembled.output


# ── The record check_includes hands over ───────────────────────────────────

def test_the_caps_a_check_reached_are_named_by_the_includes_warning():
    document = parse(_FRONT + _BODY + "{{include: p.lgd}}\n", filename="main.lgd")
    include = IncludeDirective("p.lgd", "p.lgd", "{{include: p.lgd}}", 3, 0, 0, 0, 0, False, True)
    outcome = IncludeOutcome(unread=[UnreadInclude(include, "no-resolver")], caps=["the include tree exceeds 10,000 includes (not checked past that point)"])
    (category,) = skipped_categories(
        document, Locator(document), includes=[include], outcome=outcome, image=None, template=False,
        fragment=False, attachments_unread=(), amends_unread=True,
    )
    assert category.key == "includes" and category.line == 3
    assert "the include tree exceeds 10,000 includes (not checked past that point)" in category.message
