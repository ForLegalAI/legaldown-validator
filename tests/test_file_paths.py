"""path-not-relative and path-outside-root (§2.3, §16.2, §17.2; #104).

The two Core checks on the paths a document names: ``{{include:}}``, an
image's path, and ``attachments[].file``, ``amends.file``, ``supersedes.file``
and ``translations`` of the frontmatter. They read the text of a path and
never a file; the second runs only when a document root is configured
(``validate(document_root=)``, ``legaldown validate --document-root``).
"""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

import legaldown
from legaldown import Document, load, parse, validate
from legaldown.cli import EXIT_DIAGNOSTICS, EXIT_OK, main
from legaldown.files import (
    has_uri_scheme,
    is_absolute_reference,
    leaves_root,
    names_below,
    relative_path,
)
from legaldown.validator import paths as paths_module

_RULES = {"path-not-relative", "path-outside-root"}
_ROOT_FRONT = "---\ntitle: T\n---\n\n"


def _body(text: str) -> Document:
    """A document whose body is *text*: its first line after the heading is line 7."""
    return parse(f"{_ROOT_FRONT}# A {{#a}}\n\n{text}\n", filename="t.lgd")


def _found(result) -> list[tuple[str, int | None]]:
    return [(d.rule, d.line) for d in result.diagnostics if d.rule in _RULES]


def _messages(result) -> list[str]:
    return [d.message for d in result.diagnostics if d.rule in _RULES]


def _front_source(yaml_text: str, body: str = "Text.") -> str:
    return f"---\ntitle: T\n{yaml_text}---\n\n# A {{#a}}\n\n{body}\n"


def _front(yaml_text: str, body: str = "Text.") -> Document:
    return parse(_front_source(yaml_text, body), filename="t.lgd")


def _line_of(source: str, needle: str) -> int:
    return next(n for n, line in enumerate(source.split("\n"), 1) if needle in line)


# The relative form (§2.3) -------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "https://example.com/part.lgd", "HTTP://x/y", "data:x", "file:///etc/passwd", "mailto:a@b.cz",
        "C:/x.lgd", "C:\\x.lgd", "C:x.lgd", "c:\\x", "/etc/passwd", "//host/share/x.lgd", "\\\\host\\share\\x.lgd",
        "\\abs.lgd", "a+b.c-d:x",
    ],
)
def test_a_path_that_is_not_relative_is_an_error_in_an_include(path):
    result = validate(_body(f"{{{{include: {path}}}}}"))
    assert _found(result) == [("path-not-relative", 7)]
    assert f"'{path}'" in _messages(result)[0] and "(§2.3)" in _messages(result)[0]
    assert all(d.level == "error" for d in result.diagnostics if d.rule in _RULES)


@pytest.mark.parametrize("path", ["./a.lgd", "a.lgd", "sub/a.lgd", "../shared/x.lgd", "../../x.lgd", "a/../../b.lgd"])
def test_a_relative_path_is_valid_without_a_root(path):
    assert _found(validate(_body(f"{{{{include: {path}}}}}"))) == []


def test_a_path_with_a_colon_after_a_slash_is_relative():
    assert _found(validate(_body("{{include: sub/a:b.lgd}}"))) == []
    assert _found(validate(_body("{{include: ./a:b.lgd}}"))) == []


def test_a_file_name_with_a_colon_reads_as_having_a_scheme():
    """RFC 3986: ``a:b`` is the scheme ``a``; the relative path is written ``./a:b``."""
    assert _found(validate(_body("{{include: a:b.lgd}}"))) == [("path-not-relative", 7)]


def test_the_classification_is_the_text_of_the_path_alone():
    assert has_uri_scheme("https://x") and has_uri_scheme("C:x") and not has_uri_scheme("./C:x")
    assert not has_uri_scheme(":x") and not has_uri_scheme("1a:x") and not has_uri_scheme("")
    assert is_absolute_reference("/x") and is_absolute_reference("\\x") and is_absolute_reference("//h/x")
    assert not is_absolute_reference("../x") and not is_absolute_reference("a/b") and not is_absolute_reference("")


def test_an_include_is_found_wherever_it_is_written_and_by_its_own_line():
    body = "First.\n\n- item\n  {{include: /a.lgd}}\n\n> quoted {{include: file:///b.lgd}}\n\n{{include: c.lgd}}\n"
    result = validate(_body(body))
    assert _found(result) == [("path-not-relative", 10), ("path-not-relative", 12)]


def test_an_include_in_code_or_a_comment_is_not_a_reference():
    body = "`{{include: /a.lgd}}`\n\n```\n{{include: /b.lgd}}\n```\n\n<!-- {{include: /c.lgd}} -->\n"
    assert _found(validate(_body(body))) == []


# The frontmatter (§3.2, §3.8, §3.9, §14) ------------------------------------------

_FRONT = (
    "attachments:\n  - id: one\n    title: One\n    file: ok.pdf\n"
    "  - id: two\n    title: Two\n    file: https://x/y.pdf\n"
    "amends:\n  title: Original\n  file: /etc/x.lgd\n"
    "supersedes:\n  title: Old\n  file: file:///x\n"
    "translations:\n  cs: C:/cs.lgd\n  de: de.lgd\n"
)


def test_each_file_of_the_frontmatter_is_checked_at_its_own_key():
    source = _front_source(_FRONT)
    result = validate(parse(source))
    assert _found(result) == [
        ("path-not-relative", _line_of(source, "https://x/y.pdf")),
        ("path-not-relative", _line_of(source, "/etc/x.lgd")),
        ("path-not-relative", _line_of(source, "file:///x")),
        ("path-not-relative", _line_of(source, "C:/cs.lgd")),
    ]
    assert _found(result) == [("path-not-relative", 9), ("path-not-relative", 12), ("path-not-relative", 15), ("path-not-relative", 17)]
    assert [m.split("'")[1] for m in _messages(result)] == ["https://x/y.pdf", "/etc/x.lgd", "file:///x", "C:/cs.lgd"]
    assert ["(attachments[1].file)" in _messages(result)[0], "(amends.file)" in _messages(result)[1]] == [True, True]
    assert "(supersedes.file)" in _messages(result)[2] and "(translations.cs)" in _messages(result)[3]


def test_a_supersedes_that_is_a_string_describes_a_document_and_names_no_file():
    assert _found(validate(_front('supersedes: "Policy: v2"\n'))) == []
    assert _found(validate(_front("supersedes: https://example.com/old\n"))) == []


def test_a_supersedes_object_without_a_file_names_none():
    assert _found(validate(_front("supersedes:\n  title: Old\n"))) == []


def test_a_missing_file_is_not_this_rules_concern():
    result = validate(_front("attachments:\n  - id: one\n    title: One\n"))
    assert _found(result) == [] and "attachment-file-missing" in result.rules()


def test_a_translations_file_with_a_colon_is_documented_as_a_scheme():
    assert _found(validate(_front('translations:\n  cs: "a:b"\n'))) == [("path-not-relative", 4)]


@pytest.mark.parametrize("yaml_text", ["attachments:\n  - id: a\n    title: A\n    file: ../x.pdf\n", "amends:\n  title: O\n  file: ../o.lgd\n"])
def test_a_relative_file_of_the_frontmatter_is_valid_without_a_root(yaml_text):
    assert _found(validate(_front(yaml_text))) == []


def test_the_frontmatter_is_checked_against_the_root_too():
    document = _front("attachments:\n  - id: a\n    title: A\n    file: ../x.pdf\ntranslations:\n  cs: ok/../cs.lgd\n")
    result = validate(document, document_root="/work/root")
    assert _found(result) == [("path-outside-root", 6)]
    assert "(attachments[0].file)" in _messages(result)[0] and "'/work/root'" in _messages(result)[0]


# Images (§8.7) ------------------------------------------------------------------


def test_the_four_images_of_the_issue_are_four_errors():
    body = "![x](https://example.com/p.png) ![y](/etc/passwd) ![z](//host/x.png) ![w](C:/x.png)"
    result = validate(_body(body))
    assert _found(result) == [("path-not-relative", 7)] * 4
    paths = ["https://example.com/p.png", "/etc/passwd", "//host/x.png", "C:/x.png"]
    assert all(f"File reference '{path}' (image)" in message for path, message in zip(paths, _messages(result), strict=True))


@pytest.mark.parametrize(
    "body",
    [
        "`![a](/e)`",
        "```\n![a](/e)\n```",
        "~~~\n![a](/e)\n~~~",
        "    ![a](/e)",
        "<!-- ![a](/e) -->",
        '{{placeholder: x, note="![a](/e)"}}',
        "[t](https://example.com/x)",
        "<https://example.com/x>",
        '<img src="/x">',
        "\\![a](https://x)",
        "![a][r]\n\n[r]: /etc/passwd",
        "![a] (/e)",
        "![a](/e",
        "![a]()",
        "![a](<>)",
        "![a]({{placeholder: p}})",
        "![a](<{{placeholder: p}}>)",
        "![a](/e \"unclosed)",
        "!\\[a](/e)",
        "![a [b](/e)",
        "![a](/e (unclosed)",
    ],
)
def test_what_is_not_an_image_with_a_path_is_not_checked(body):
    assert _found(validate(_body(body))) == []


def test_raw_html_is_only_raw_html():
    result = validate(_body('<img src="/x">'))
    assert "raw-html" in result.rules() and _found(result) == []


@pytest.mark.parametrize(
    "body",
    [
        "- item ![a](/e)",
        "1. item\n2. ![a](/e)",
        "- item\n  - nested ![a](/e)",
        "> quoted ![a](/e)",
        "> - quoted item ![a](/e)",
        "| h |\n|---|\n| ![a](/e) |",
        '![a](/e "a title")',
        "![a](/e 'a title')",
        "![a](/e (a title))",
        "![a](</e>)",
        "![a](</e with spaces>)",
        "![a](&#47;e)",
        "![a](&#x2F;e)",
        "![a](&sol;e)",
        "![a](\\/e)",
        "![a]( /e )",
        "[![a](/e)](https://example.com)",
        "![a `]` b](/e)",
        "![{{placeholder: p}}](/e)",
        "![a\\]b](/e)",
        "![a](/e\n\"title\")",
        "\\\\![a](/e)",
    ],
)
def test_an_image_in_text_has_its_path_checked(body):
    result = validate(_body(body))
    assert [rule for rule, _line in _found(result)] == ["path-not-relative"]
    assert "/e'" in _messages(result)[0] or "/e with spaces'" in _messages(result)[0]


def test_a_nested_image_gives_both_paths():
    result = validate(_body("![![g](/in)](/out)"))
    assert sorted(m.split("'")[1] for m in _messages(result)) == ["/in", "/out"]


def test_an_image_in_a_heading_is_at_the_heading():
    document = parse(f"{_ROOT_FRONT}# A {{#a}}\n\ntext\n\n## B ![x](https://x/y.png) {{#b}}\n\ntext\n", filename="t.lgd")
    assert _found(validate(document)) == [("path-not-relative", 9)]


def test_an_image_is_at_its_own_line_in_a_paragraph_a_list_a_quote_and_a_table():
    body = (
        "One\n![a](/1) two\nthree ![b](/2)\n\n"
        "- item\n  ![c](/3)\n- ![d](/4)\n\n"
        "> quoted\n> ![e](/5)\n\n"
        "| h | i |\n|---|---|\n| x | ![f](/6) |\n"
    )
    result = validate(_body(body))
    assert _found(result) == [
        ("path-not-relative", 8), ("path-not-relative", 9),
        ("path-not-relative", 12), ("path-not-relative", 13),
        ("path-not-relative", 16),
        ("path-not-relative", 20),
    ]


def test_the_same_image_twice_is_at_each_of_its_lines():
    result = validate(_body("![a](/e)\n\n![a](/e)\n\ntext ![a](/e)"))
    assert _found(result) == [("path-not-relative", 7), ("path-not-relative", 9), ("path-not-relative", 11)]


def test_an_image_in_a_table_cell_with_an_escaped_pipe_is_at_its_line():
    body = "| h | i |\n|---|---|\n| a \\| b | ![x\\|y](/e) |\n| c | ![z](/f) |"
    assert _found(validate(_body(body))) == [("path-not-relative", 9), ("path-not-relative", 10)]


def test_an_escaped_bang_is_a_link_with_a_bang_before_it():
    assert _found(validate(_body("\\![a](https://x/y.png)"))) == []
    # ... and two backslashes escape one another, not the bang.
    assert _found(validate(_body("\\\\![a](https://x/y.png)"))) == [("path-not-relative", 7)]


def test_an_image_whose_alt_holds_a_directive_is_still_checked():
    result = validate(_body("![{{placeholder: p}}](/etc/x.png)"))
    assert _found(result) == [("path-not-relative", 7)]


def test_an_image_whose_destination_holds_a_directive_is_judged_by_what_precedes_it():
    """Its path is not known until the directive is filled in (assembly's output is validated again, §15.7.4),
    but a start that is not relative is not relative whatever follows."""
    for body in ("![a]({{placeholder: p}})", "![a]({{placeholder: p}}/x.png)", "![a](x/{{placeholder: p}}.png)",
                 "![a](<{{placeholder: p}}https://x>)", "![a](ht{{placeholder: p}}://x)", "![a](C{{placeholder: p}}:x)"):
        assert _found(validate(_body(body))) == [], body
    for body, shown in (
        ("![a](https://{{placeholder: host}}/x.png)", "https://{{placeholder: host}}/x.png"),
        ("![a](/abs/{{placeholder: p}}.png)", "/abs/{{placeholder: p}}.png"),
        ("![a](<https://{{placeholder: p}}/x y.png>)", "https://{{placeholder: p}}/x y.png"),
        ("![a](\\\\host/{{placeholder: p}})", "\\\\host/{{placeholder: p}}"),
        ("![a](C:/{{placeholder: p}})", "C:/{{placeholder: p}}"),
        ("![a](%2Fetc/{{placeholder: p}})", "%2Fetc/{{placeholder: p}}"),
        ("![a](/x{{placeholder: p}}/{{placeholder: q}} \"t\")", "/x{{placeholder: p}}/{{placeholder: q}}"),
    ):
        result = validate(_body(body))
        assert _found(result) == [("path-not-relative", 7)], body
        assert f"'{shown}' (image" in _messages(result)[0], body


def test_a_destination_with_a_directive_is_judged_for_the_root_by_its_whole_names_only(tmp_path):
    """``../../{{placeholder: p}}`` may be filled in as anything, but the names before the last separator are written."""
    root = _root(tmp_path)
    for body in ("![a](../../../{{placeholder: p}}.png)", "![a](../../../x/{{placeholder: p}}.png)",
                 "![a](<../../../{{placeholder: p}}>)", "![a](x/../../{{placeholder: p}})",
                 "![a](%2E%2E/%2E%2E/{{placeholder: p}})", "![a](..%5C..%5C{{placeholder: p}})"):
        result = validate(_body(body), document_root=root)
        assert _found(result) == [("path-outside-root", 7)], body
        assert "{{placeholder: p}}" in _messages(result)[0]
    # The last name is partial, whatever it looks like: ``..`` may be ``...`` or ``..x`` once filled in.
    for body in ("![a](x/..{{placeholder: p}})", "![a](a/../..{{placeholder: p}})",
                 "![a](x/{{placeholder: p}}/../../../y)", "![a]({{placeholder: p}}../../..)", "![a](x/{{placeholder: p}})"):
        assert _found(validate(_body(body), document_root=root)) == [], body
    assert _found(validate(_body("![a](/{{placeholder: p}})"), document_root=root)) == [("path-not-relative", 7)]
    assert _found(validate(_body("![a](../../../{{placeholder: p}}.png)"))) == []  # no root


def test_a_document_outside_the_root_with_only_destinations_that_hold_directives_is_still_reported_once(tmp_path):
    root = _root(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    path = tmp_path / "elsewhere" / "doc.lgd"
    path.write_text(
        f"{_ROOT_FRONT}# A {{#a}}\n\n![a]({{{{placeholder: p}}}}.png) ![b](x/{{{{placeholder: q}}}}) ![c](./{{{{placeholder: r}}}})\n",
        encoding="utf-8",
    )
    result = validate(load(path), document_root=root)
    assert _found(result) == [("path-outside-root", 1)]
    assert "The document is outside the document root" in _messages(result)[0]
    # A destination that is only a query names no file; the document names none.
    path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n![a](?x=1) ![b](#top)\n", encoding="utf-8")
    assert _found(validate(load(path), document_root=root)) == []


def test_a_nul_in_the_text_is_not_a_directive(tmp_path):
    root = _root(tmp_path)
    result = validate(_body("![a](../../../x\x00.png)"), document_root=root)
    assert _found(result) == [("path-outside-root", 7)] and "x\x00.png" in _messages(result)[0]
    # ... and does not hide what follows it from the judgement, or what precedes it from being a path.
    assert _found(validate(_body("![a](x\x00/../../..)"), document_root=root)) == [("path-outside-root", 7)]
    assert _found(validate(_body("![a](\x00/etc)"))) == []
    assert _found(validate(_body("![a](/\x00)"))) == [("path-not-relative", 7)]
    both = validate(_body("![a](../../../{{placeholder: p}}\x00)"), document_root=root)
    assert _found(both) == [("path-outside-root", 7)]


@pytest.mark.parametrize(
    "destination",
    ["C%3A%5CWindows%5Cx.png", "C%3A/x.png", "c%3a%5cx", "z%3A%2Fx", "C%3Ax.png", "C%3A", "a%3Ab.png", "c%3Ax.png"],
)
def test_a_percent_encoded_drive_is_absolute(destination):
    result = validate(_body(f"![a]({destination})"))
    assert _found(result) == [("path-not-relative", 7)] and "once percent-decoded" in _messages(result)[0]
    assert _found(validate(_body(f"![a]({destination})"), document_root="/r")) == [("path-not-relative", 7)]


@pytest.mark.parametrize("destination", ["1%3A/x", "ab%3Ax", "CD%3Ax", "img/a%3Ab.png"])
def test_a_decoded_colon_that_is_no_drive_is_a_character_of_the_name(destination):
    """Only one letter and a colon at the start is a drive, of either case, as written ``a:b`` is not
    relative either; a colon after more letters, or further in, is a character of the name."""
    assert _found(validate(_body(f"![a]({destination})"))) == []


def test_the_findings_of_the_paths_are_in_document_order():
    document = parse(
        f"{_ROOT_FRONT}# A ![i](/h.png) {{#a}}\n\n![b](/b.png) {{{{include: /c.lgd}}}} ![d](/d.png)\n\n"
        "## B ![e](/e.png) {#b}\n\n![f](/f.png)\n",
        filename="t.lgd",
    )
    result = validate(document)
    assert [(d.line, d.message.split("'")[1]) for d in result.diagnostics if d.rule in _RULES] == [
        (5, "/h.png"), (7, "/b.png"), (7, "/c.lgd"), (7, "/d.png"), (9, "/e.png"), (11, "/f.png"),
    ]


def test_the_example_of_a_document_outside_the_root_is_the_first_reference_in_the_document(tmp_path):
    root = _root(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    path = tmp_path / "elsewhere" / "doc.lgd"
    path.write_text(
        f"{_ROOT_FRONT}# A ![i](h.png) {{#a}}\n\n![b](b.png) {{{{include: c.lgd}}}}\n", encoding="utf-8"
    )
    result = validate(load(path), document_root=root)
    assert _found(result) == [("path-outside-root", 1)] and "such as 'h.png' (image)" in _messages(result)[0]
    # A reference of the frontmatter comes before the body's.
    path.write_text(f"{_ROOT_FRONT[:-5]}translations:\n  cs: cs.lgd\n---\n\n# A ![i](h.png)\n", encoding="utf-8")
    assert "such as 'cs.lgd' (translations.cs)" in _messages(validate(load(path), document_root=root))[0]


def test_a_finding_says_when_the_query_or_the_fragment_was_dropped_to_judge_the_path(tmp_path):
    root = _root(tmp_path)
    message = _messages(validate(_body("![a](../../x.png?p=1)"), document_root=root))[0]
    assert "(image, without its query or fragment)" in message
    message = _messages(validate(_body("![a](%2Fetc/x.png#top)")))[0]
    assert "(image, without its query or fragment, read as '/etc/x.png' once percent-decoded)" in message
    assert "(image) is not relative" in _messages(validate(_body("![a](/etc/x.png)")))[0]


def test_a_directive_in_a_destination_leaves_the_line_of_the_next_image_alone():
    result = validate(_body("![a](https://{{placeholder: p}}/x.png)\n![b](/y.png)"))
    assert _found(result) == [("path-not-relative", 7), ("path-not-relative", 8)]


def test_an_image_in_a_drafting_note_or_in_a_definition_is_checked_all_the_same():
    body = '> **Note**: see ![a](/e).\n\n"Term" {{def: term}} means ![b](/f).\n\nSee {{term: term}} ![c](/g).\n'
    assert _found(validate(_body(body))) == [("path-not-relative", 7), ("path-not-relative", 9), ("path-not-relative", 11)]


@pytest.mark.parametrize("path", ["C:x.png", "c:img/x.png", "C:\\x.png"])
def test_a_drive_relative_path_has_a_scheme(path):
    assert _found(validate(_body(f"![a](<{path}>)"))) == [("path-not-relative", 7)]
    assert _found(validate(_body(f"{{{{include: {path}}}}}"))) == [("path-not-relative", 7)]


def test_a_relative_image_is_valid():
    assert _found(validate(_body("![a](x.png) ![b](img/x.png) ![c](../shared/x.png) ![d](./x.png) ![e](./a:b.png)"))) == []
    assert _found(validate(_body("![e](a:b/x.png)"))) == [("path-not-relative", 7)]  # ``a:`` reads as a scheme


def test_an_image_destination_is_read_as_the_escapes_and_references_it_holds():
    result = validate(_body("![a](&#47;etc)"))
    assert [m.split("'")[1] for m in _messages(result)] == ["/etc"]
    # A backslash before an ampersand makes it literal, and the reference stays text.
    assert _found(validate(_body("![a](\\&#47;etc)"))) == []


def test_an_image_percent_encoded_slash_is_a_path_that_is_not_relative():
    result = validate(_body("![a](%2Fetc/passwd)"))
    assert _found(result) == [("path-not-relative", 7)]
    assert "'%2Fetc/passwd'" in _messages(result)[0] and "read as '/etc/passwd' once percent-decoded" in _messages(result)[0]


def test_a_percent_sign_that_is_no_escape_stays_as_written():
    assert _found(validate(_body("![a](100%/x.png) ![b](%zz/x.png) ![c](%2)"))) == []
    assert _found(validate(_body("![a](100%/x.png)"), document_root="/r")) == []


def test_percent_encoding_is_not_read_in_the_other_references():
    """Only an image destination is a URL: ``%2Fetc`` in an include is a file name."""
    assert _found(validate(_body("{{include: %2Fetc/x.lgd}}"))) == []
    assert _found(validate(_body("{{include: %2E%2E/%2E%2E/x.lgd}}"), document_root="/r")) == []


# The document root (§2.3) -------------------------------------------------------


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    return root


def test_without_a_root_the_containment_check_does_not_run():
    result = validate(_body("{{include: ../../../outside/part.lgd}} ![a](../../../x.png)"))
    assert "path-outside-root" not in result.rules()


def test_a_path_that_escapes_the_root_is_an_error(tmp_path):
    result = validate(_body("{{include: ../../../outside/part.lgd}}"), document_root=_root(tmp_path))
    assert _found(result) == [("path-outside-root", 7)]
    assert "'../../../outside/part.lgd'" in _messages(result)[0] and str(_root(tmp_path)) in _messages(result)[0]
    assert all(d.level == "error" for d in result.diagnostics if d.rule in _RULES)


@pytest.mark.parametrize(
    ("path", "outside"),
    [
        ("a/../b.lgd", False), ("a/b/../../c.lgd", False), ("./a.lgd", False), ("a.lgd", False), ("a//b.lgd", False),
        ("..", True), ("../a.lgd", True), ("a/../../b.lgd", True), ("a/../../../b.lgd", True), ("a\\..\\..\\b.lgd", True),
        ("./../a.lgd", True), ("a/b/../../../c.lgd", True),
    ],
)
def test_a_parsed_document_is_taken_to_be_at_the_root(tmp_path, path, outside):
    result = validate(_body(f"{{{{include: {path}}}}}"), document_root=_root(tmp_path))
    assert _found(result) == ([("path-outside-root", 7)] if outside else [])


def test_the_backslash_separates_names_for_the_containment_too(tmp_path):
    result = validate(_body("{{include: a\\..\\..\\b.lgd}}"), document_root=_root(tmp_path))
    assert _found(result) == [("path-outside-root", 7)]


def test_a_document_in_a_directory_below_the_root_may_go_up_to_it(tmp_path):
    root = _root(tmp_path)
    (root / "sub").mkdir()
    path = root / "sub" / "doc.lgd"
    path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: ../x.lgd}}}}\n\n{{{{include: ../../x.lgd}}}}\n", encoding="utf-8")
    document = load(path)
    assert _found(validate(document, document_root=root)) == [("path-outside-root", 9)]
    # With the document's own directory as the root, one ``..`` leaves it.
    assert _found(validate(document, document_root=root / "sub")) == [("path-outside-root", 7), ("path-outside-root", 9)]
    assert _found(validate(document)) == []


def test_a_relative_document_path_and_a_relative_root_are_compared_as_absolute(tmp_path, monkeypatch):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "doc.lgd").write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: ../x.lgd}}}}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    document = replace(load("sub/doc.lgd"), path=Path("sub/doc.lgd"))  # as a caller may have set it
    assert _found(validate(document, document_root=".")) == []
    assert _found(validate(document, document_root="sub")) == [("path-outside-root", 7)]
    assert _found(validate(document, document_root=Path("sub/../sub"))) == [("path-outside-root", 7)]
    monkeypatch.chdir(tmp_path / "sub")
    assert _found(validate(document, document_root="..")) == []  # the same document: its path read from here


def test_a_document_outside_the_root_is_reported_once_for_all_its_paths(tmp_path):
    root = _root(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    path = tmp_path / "elsewhere" / "doc.lgd"
    path.write_text(
        f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: a.lgd}}}} ![i](b.png) {{{{include: c.lgd}}}}\n", encoding="utf-8"
    )
    result = validate(load(path), document_root=root)
    assert _found(result) == [("path-outside-root", 1)]
    assert "The document is outside the document root" in _messages(result)[0] and "such as '" in _messages(result)[0]


def test_a_document_outside_the_root_that_names_no_file_is_not_reported(tmp_path):
    root = _root(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    path = tmp_path / "elsewhere" / "doc.lgd"
    path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\nText.\n", encoding="utf-8")
    assert _found(validate(load(path), document_root=root)) == []


def test_a_document_outside_the_root_still_has_its_paths_that_are_not_relative_reported(tmp_path):
    root = _root(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    path = tmp_path / "elsewhere" / "doc.lgd"
    path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: /abs.lgd}}}} {{{{include: rel.lgd}}}}\n", encoding="utf-8")
    assert _found(validate(load(path), document_root=root)) == [("path-not-relative", 7), ("path-outside-root", 1)]


def test_a_path_that_is_not_relative_is_not_also_outside_the_root(tmp_path):
    root = _root(tmp_path)
    for path in ("/etc/passwd", "https://x/../../y", "C:/x/../..", "//host/../x"):
        assert _found(validate(_body(f"{{{{include: {path}}}}} ![a](<{path}>)"), document_root=root)) == [
            ("path-not-relative", 7)
        ] * 2


def test_an_image_percent_encoded_dots_escape_the_root_too(tmp_path):
    root = _root(tmp_path)
    for destination in ("%2E%2E/%2E%2E/secret.png", "%2e%2e/secret.png", "a/%2E%2E/%2E%2E/secret.png", "..%2Fsecret.png", "..%5Csecret.png"):
        result = validate(_body(f"![a]({destination})"), document_root=root)
        assert _found(result) == [("path-outside-root", 7)], destination
    result = validate(_body("![a](%2E%2E/secret.png)"), document_root=root)
    assert "'%2E%2E/secret.png'" in _messages(result)[0] and "read as '../secret.png' once percent-decoded" in _messages(result)[0]
    # ... and an image whose decoded form stays inside does not.
    assert _found(validate(_body("![a](a%2F..%2Fb.png)"), document_root=root)) == []
    # Not for the other references: only an image is a URL.
    assert _found(validate(_body("{{include: %2E%2E/x.lgd}}"), document_root=root)) == []


def test_the_root_may_be_given_as_text_or_as_a_path(tmp_path):
    root = _root(tmp_path)
    document = _body("{{include: ../x.lgd}}")
    assert _found(validate(document, document_root=root)) == _found(validate(document, document_root=str(root)))


def test_the_checks_look_at_no_file_and_follow_no_link(tmp_path, monkeypatch):
    root = _root(tmp_path)
    (tmp_path / "outside").mkdir()
    try:
        (root / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available")

    def refuse(*args, **kwargs):
        raise AssertionError("the file system was asked")

    monkeypatch.setattr(Path, "resolve", refuse)
    monkeypatch.setattr(Path, "exists", refuse)
    monkeypatch.setattr(Path, "is_file", refuse)
    monkeypatch.setattr(Path, "stat", refuse)
    import os

    monkeypatch.setattr(os.path, "realpath", refuse)
    document = _body("{{include: link/x.lgd}} {{include: ../x.lgd}} ![a](link/y.png) ![b](../y.png)")
    assert _found(validate(document, document_root=root)) == [("path-outside-root", 7)] * 2
    doc_path = root / "doc.lgd"
    doc_path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: link/x.lgd}}}}\n", encoding="utf-8")
    assert _found(validate(load(doc_path), document_root=root, resolve=lambda path: None)) == []


def test_a_root_that_does_not_exist_is_a_root_all_the_same(tmp_path):
    assert _found(validate(_body("{{include: ../x.lgd}}"), document_root=tmp_path / "none")) == [("path-outside-root", 7)]


def test_the_helpers_of_the_containment():
    assert not leaves_root("a/../b", 0) and leaves_root("../b", 0) and leaves_root("a/../../b", 0)
    assert not leaves_root("../b", 1) and leaves_root("../../b", 1)
    assert not leaves_root("", 0) and leaves_root("..\\x", 0) and not leaves_root("./a//b", 0)
    assert names_below("/r/s/t", "/r") == 2 and names_below("/r", "/r") == 0
    assert names_below("/", "/r") is None and names_below("/elsewhere", "/r") is None
    assert names_below("/r2", "/r") is None  # not a prefix of the text: of the names


# Fragments (§12.2, §12.4) -------------------------------------------------------


def test_a_fragments_own_paths_are_checked_and_are_never_host_dependent():
    document = parse("## B\n\n{{include: /abs.lgd}} ![a](https://x/y.png)\n", filename="f.lgd")
    result = validate(document, fragment=True)
    assert [(d.rule, d.line, d.host_dependent) for d in result.diagnostics if d.rule in _RULES] == [
        ("path-not-relative", 3, False)
    ] * 2


def test_a_fragments_paths_are_checked_against_the_root(tmp_path):
    result = validate(parse("## B\n\n![a](../../x.png)\n"), fragment=True, document_root=_root(tmp_path))
    assert [(d.rule, d.host_dependent) for d in result.diagnostics if d.rule in _RULES] == [("path-outside-root", False)]


def test_a_fragments_frontmatter_is_never_read_for_paths():
    text = "---\ntitle: T\namends:\n  title: O\n  file: /etc/x.lgd\nattachments:\n  - id: a\n    title: A\n    file: https://x/y\n---\n\n## B\n\nText.\n"
    result = validate(parse(text), fragment=True)
    assert _found(result) == [] and "include-has-frontmatter" in result.rules()
    # ... while it is read in the document it is a part of.
    assert {rule for rule, _ in _found(validate(parse(text)))} == {"path-not-relative"}


def test_a_document_whose_frontmatter_cannot_be_read_is_validated_by_its_body():
    text = "---\ntitle: [unclosed\namends: {file: /etc/x}\n---\n\n# A\n\n![a](/etc/x.png)\n"
    result = validate(parse(text, on_frontmatter_error="keep"))
    assert "frontmatter-invalid-yaml" in result.rules() and [rule for rule, _ in _found(result)] == ["path-not-relative"]


# Reading files: relative_path is the same rule (§2.3) ----------------------------


@pytest.mark.parametrize(
    "path",
    [
        "https://example.com/a.lgd", "HTTPS://example.com/a.lgd", "data:text/plain,x", "file:///x", "file://host/x",
        "mailto:a@b.cz", "urn:x:y", "C:/x", "C:x", "a:b.lgd", "//host/x.lgd", "\\\\host\\x.lgd", "/x",
    ],
)
def test_relative_path_refuses_a_scheme_and_a_url(path):
    assert relative_path(path) is None


@pytest.mark.parametrize(
    ("path", "relative"),
    [("a.lgd", "a.lgd"), ("sub/a.lgd", "sub/a.lgd"), ("./sub//a.lgd", "sub/a.lgd"), ("sub/../a.lgd", "a.lgd"), ("sub/a:b.lgd", "sub/a:b.lgd"),
     ("./a:b.lgd", "a:b.lgd"), ("a.b+c/x.lgd", "a.b+c/x.lgd"), ("1a/x:y", "1a/x:y")],
)
def test_relative_path_still_reads_a_relative_one(path, relative):
    assert relative_path(path) == relative


_AMENDMENT = "---\ntitle: A\namends:\n  title: Original\n  file: {file}\n---\n\n# S\n\nUses {{{{term: services}}}}.\n"
_ORIGINAL = '---\ntitle: O\n---\n\n# S\n\n"Services" {{def: services}} means x.\n'


def test_a_definition_import_reads_a_relative_path_as_before():
    asked: list[str] = []

    def resolve(path: str) -> str | None:
        asked.append(path)
        return _ORIGINAL

    result = validate(parse(_AMENDMENT.format(file="./sub//o.lgd")), resolve=resolve)
    assert asked == ["sub/o.lgd"] and "amend-term-undefined" not in result.rules() and _found(result) == []


@pytest.mark.parametrize("url", ["https://example.com/o.lgd", "data:x", "file:///x", "mailto:a@b.cz", "//host/o.lgd"])
def test_a_definition_import_is_not_handed_a_url(url):
    asked: list[str] = []
    result = validate(parse(_AMENDMENT.format(file=url)), resolve=lambda path: asked.append(path))
    assert asked == [] and "amend-term-unresolvable" in result.rules()
    assert _found(result) == [("path-not-relative", 5)]


def test_an_attachment_file_import_is_not_handed_a_url():
    text = "---\ntitle: A\nattachments:\n  - id: a\n    title: A\n    file: {file}\n---\n\n# S\n\n{{{{attach: a}}}}\n"
    asked: list[str] = []

    def resolve(path: str) -> str | None:
        asked.append(path)
        return '"Services" {{def: services}} means x.\n'

    assert "services" in validate(parse(text.format(file="sub/a.lgd")), resolve=resolve).index.definition_lookup
    assert asked == ["sub/a.lgd"]
    asked.clear()
    result = validate(parse(text.format(file="https://example.com/a.lgd")), resolve=resolve)
    assert asked == [] and _found(result) == [("path-not-relative", 6)]


_TEMPLATE = "---\ntitle: T\n---\n\n# A\n\n{{include: %s}}\n"


def test_assembly_reads_a_relative_include_as_before():
    files = {"parts/a.lgd": "## B {#b}\n\nText.\n"}
    asked: list[str] = []

    def resolve(path: str) -> str | None:
        asked.append(path)
        return files.get(path)

    result = legaldown.parse_template(_TEMPLATE % "./parts//a.lgd", resolve=resolve).form({}).assemble()
    assert asked == ["parts/a.lgd"] and result.ok and result.files == files


@pytest.mark.parametrize("url", ["https://example.com/a.lgd", "data:x", "file:///x", "mailto:a@b.cz"])
def test_assembly_does_not_ask_for_a_url_and_still_refuses_it(url):
    asked: list[str] = []

    def resolve(path: str) -> str | None:
        asked.append(path)
        return "## B\n\nText.\n"

    template = legaldown.parse_template(_TEMPLATE % url, resolve=resolve)
    assert "include-file-missing" in [d.rule for d in template.problems]
    assert asked == []
    # The template's own validation says what is wrong with the path; assembly is not stopped by that alone.
    assert "path-not-relative" in template.validation.rules()


# The command line ---------------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "root" / "sub").mkdir(parents=True)
    doc = tmp_path / "root" / "sub" / "doc.lgd"
    doc.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: ../shared.lgd}}}}\n\n![a](../../x.png)\n", encoding="utf-8")
    return tmp_path


def test_cli_document_root_reports_a_path_that_escapes_it(tree, capsys):
    doc = tree / "root" / "sub" / "doc.lgd"
    assert main(["validate", str(doc), "--document-root", str(tree / "root")]) == EXIT_DIAGNOSTICS
    out = capsys.readouterr().out
    assert out.count("[path-outside-root]") == 1 and f"{doc}:9:" in out
    # No root: no containment check. A loader is never asked for ../shared.lgd, which assembly
    # refuses as unreadable, so it is include-file-missing, and not path-outside-root (#35).
    assert main(["validate", str(doc)]) == EXIT_DIAGNOSTICS
    out = capsys.readouterr().out
    assert "path-outside-root" not in out and out.count("[include-file-missing]") == 1


def test_cli_document_root_accepts_a_relative_root_and_a_relative_document(tree, capsys, monkeypatch):
    monkeypatch.chdir(tree / "root")
    assert main(["validate", "sub/doc.lgd", "--document-root", "."]) == EXIT_DIAGNOSTICS
    assert capsys.readouterr().out.count("[path-outside-root]") == 1
    assert main(["validate", "sub/doc.lgd", "--document-root", "sub"]) == EXIT_DIAGNOSTICS
    assert capsys.readouterr().out.count("[path-outside-root]") == 2
    # ../shared.lgd is within this root, so valid (§2.3); this implementation reads nothing outside
    # the document's directory, and says nothing of it (CONFORMANCE.md, a known limitation).
    assert main(["validate", "sub/doc.lgd", "--document-root", ".."]) == EXIT_OK


def test_cli_reports_a_path_that_is_not_relative_with_or_without_a_root(tree, capsys):
    doc = tree / "root" / "bad.lgd"
    doc.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: https://example.com/part.lgd}}}}\n", encoding="utf-8")
    for extra in ([], ["--document-root", str(tree / "root")]):
        assert main(["validate", str(doc), "--format", "json", *extra]) == EXIT_DIAGNOSTICS
        out = capsys.readouterr().out
        assert '"rule": "path-not-relative"' in out and '"host_dependent": false' in out


def test_cli_fragment_mode_takes_the_root_too(tree, capsys):
    fragment = tree / "root" / "frag.lgd"
    fragment.write_text("## B\n\n![a](../x.png)\n", encoding="utf-8")
    assert main(["validate", "--fragment", str(fragment), "--document-root", str(tree / "root")]) == EXIT_DIAGNOSTICS
    assert "[path-outside-root]" in capsys.readouterr().out


# Hostile text (linear time) -----------------------------------------------------


def _seconds(call, *args) -> float:
    start = time.perf_counter()
    call(*args)
    return time.perf_counter() - start


class _Counted:
    def __init__(self, monkeypatch, name):
        self.calls = 0
        wrapped = getattr(paths_module, name)

        def counted(*args, **kwargs):
            self.calls += 1
            return wrapped(*args, **kwargs)

        monkeypatch.setattr(paths_module, name, counted)


def test_the_brackets_of_a_text_are_paired_once_and_each_image_is_parsed_once(monkeypatch):
    from legaldown.directives import lex

    pairs, parses = _Counted(monkeypatch, "_closing_brackets"), _Counted(monkeypatch, "_destination")
    text = "![a](/x) " * 5000
    assert len(paths_module.images(text, lex(text))) == 5000
    assert pairs.calls == 1 and parses.calls == 5000


_HOSTILE = {
    "unclosed destinations": "![a](",
    "nested destinations": "![a](![a](",
    "unclosed titles": '![a](/x "',
    "unclosed single-quoted titles": "![a](/x '",
    "unclosed paren titles": "![a](/x (",
    "unclosed angle destinations": "![a](<",
    "unclosed brackets": "![a",
    "bare openers": "![",
    "opening brackets": "[",
    "closing brackets": "]",
    "backslashes": "\\![",
    "images": "![a](/x.png) ",
    "images with titles": '![a](/x.png "t") ',
    "deep parentheses": "![a](((((((((((((((((((((((((((((((((((((",
}


@pytest.mark.parametrize("shape", _HOSTILE)
def test_hostile_text_is_read_in_linear_time(shape):
    from legaldown.directives import lex

    unit = _HOSTILE[shape]
    text = unit * (200_000 // len(unit))
    assert _seconds(lambda: paths_module.images(text, lex(text))) < 3.0  # tens of milliseconds now; a quadratic scan took minutes


@pytest.mark.parametrize("shape", ["![a](", "![a](/x \"", "![a](/x (", "![a](![a](", "![a", "![a](/x.png) "])
def test_a_document_of_hostile_paragraphs_is_validated_in_a_time_budget(shape):
    document = _body((shape * (50_000 // len(shape)) + "\n\n") * 4)
    assert _seconds(validate, document) < 10.0


def test_ten_thousand_images_in_one_paragraph_are_each_found_at_their_line():
    document = _body("![a](/x.png) " * 10_000)
    result = validate(document)
    assert _found(result) == [("path-not-relative", 7)] * 10_000


# A destination is a URL (§8.7): what is encoded, what is a query ------------------


@pytest.mark.parametrize("destination", ["img/a%3Ab.png", "https%3A//x/y.png", "%68ttps://x"])
def test_a_percent_encoded_colon_is_a_character_of_the_name_not_a_scheme(destination, tmp_path):
    """``https%3A//x`` names no scheme: a scheme is the one written. (One letter and a colon at the start
    is a drive once decoded, ``test_a_percent_encoded_drive_is_absolute``.)"""
    assert _found(validate(_body(f"![a]({destination})"))) == [], destination
    assert _found(validate(_body(f"![a]({destination})"), document_root=_root(tmp_path))) == [], destination


def test_the_decoded_form_is_still_absolute_with_a_slash_and_still_escapes():
    for destination in ("%2Fetc", "%2fetc/passwd", "%5Cwindows", "%2F%2Fhost/x.png"):
        assert _found(validate(_body(f"![a]({destination})"))) == [("path-not-relative", 7)], destination
    assert _found(validate(_body("![a](%2E%2E/x.png)"), document_root="/r")) == [("path-outside-root", 7)]
    # ... while a scheme written plainly is a scheme, with or without encoding elsewhere in it.
    assert _found(validate(_body("![a](https://x/a%20b.png)"))) == [("path-not-relative", 7)]


@pytest.mark.parametrize(
    "destination",
    ["x.png?p=/../../..", "x.png#/../..", "x.png?p=../..#/../..", "sub/x.png?next=../../../y", "x.png?u=https://z/q", "x.png?p=/etc",
     "x.png#C:/y", "<x.png?p=../../..>", "x.png?p=%2F..%2F..%2F.."],
)
def test_a_query_or_a_fragment_is_no_part_of_the_path_of_an_image(destination, tmp_path):
    assert _found(validate(_body(f"![a]({destination})"), document_root=_root(tmp_path))) == [], destination
    assert _found(validate(_body(f"![a]({destination})"))) == [], destination


def test_what_precedes_a_query_still_counts():
    root = "/r"
    assert _found(validate(_body("![a](../../x.png?p=1)"), document_root=root)) == [("path-outside-root", 7)]
    assert _found(validate(_body("![a](..%2F..%2Fx.png#top)"), document_root=root)) == [("path-outside-root", 7)]
    assert _found(validate(_body("![a](/x.png?p=1)"))) == [("path-not-relative", 7)]
    assert _found(validate(_body("![a](https://x/y.png?p=1#f)"))) == [("path-not-relative", 7)]
    assert _found(validate(_body("![a](?p=/etc)"))) == []  # nothing before the query


def test_an_encoded_query_mark_is_a_character_of_the_name(tmp_path):
    """``%3F`` is not a ``?``: ``x%3F/../../../y`` leaves the root."""
    assert _found(validate(_body("![a](x%3F/../../../y.png)"), document_root=_root(tmp_path))) == [("path-outside-root", 7)]


def test_a_query_is_part_of_the_path_of_an_include():
    """Only an image destination is a URL: a ``?`` in an include is a character of the file name."""
    assert _found(validate(_body("{{include: https://x/y?z}}"))) == [("path-not-relative", 7)]
    assert _found(validate(_body("{{include: a?/../../../b.lgd}}"), document_root="/r")) == [("path-outside-root", 7)]


# The document's own directory is the base ------------------------------------------


def test_a_document_in_a_subdirectory_with_the_parent_as_root_may_climb_one_level(tmp_path, monkeypatch):
    """The base is the document's own directory, not the root: the harness of the fixtures sets ``path`` for a
    ``document_root`` case for this, and a root like ``..`` is relative to the case's directory."""
    (tmp_path / "sub").mkdir()
    path = tmp_path / "sub" / "doc.lgd"
    path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{{{{include: ../x.lgd}}}} ![i](../../y.png)\n", encoding="utf-8")
    document = load(path)
    assert _found(validate(document, document_root=tmp_path)) == [("path-outside-root", 7)]  # only ``../../y.png``
    monkeypatch.chdir(path.parent)
    assert _found(validate(document, document_root="..")) == [("path-outside-root", 7)]
    assert _found(validate(document, document_root=".")) == [("path-outside-root", 7), ("path-outside-root", 7)]
    # A document that was parsed is at the root, so ``../x.lgd`` leaves it.
    assert _found(validate(parse(path.read_text(encoding="utf-8")), document_root="..")) == [("path-outside-root", 7)] * 2


# Where the known text ends, and what a path of directives names ---------------------


def test_a_query_or_a_fragment_before_the_directive_ends_the_path_so_its_last_name_is_whole(tmp_path):
    root = _root(tmp_path)
    for body in ("![a](..?{{placeholder: p}})", "![a](x/../..#{{placeholder: p}})", "![a](../..?x={{placeholder: p}})"):
        result = validate(_body(body), document_root=root)
        assert _found(result) == [("path-outside-root", 7)], body
        assert "without its query or fragment" in _messages(result)[0]
    # ... while a name before the directive, with nothing to end it, may be anything once filled in.
    assert _found(validate(_body("![a](x/..{{placeholder: p}})"), document_root=root)) == []
    assert _found(validate(_body("![a](x?{{placeholder: p}})"), document_root=root)) == []


def test_a_destination_of_directives_names_no_file_but_one_with_more_than_a_query_does(tmp_path):
    root = _root(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    path = tmp_path / "elsewhere" / "doc.lgd"

    def reported(destinations: str) -> list[tuple[str, int | None]]:
        path.write_text(f"{_ROOT_FRONT}# A {{#a}}\n\n{destinations}\n", encoding="utf-8")
        return _found(validate(load(path), document_root=root))

    for none in ("![a]({{placeholder: p}})", "![a]({{placeholder: p}}{{placeholder: q}})", "![a](?{{placeholder: p}})",
                 "![a](#{{placeholder: p}})", "![a]({{placeholder: p}}?x=1)", "![a]({{placeholder: p}}#top)",
                 "![a](<  {{placeholder: p}}  >)"):
        assert reported(none) == [], none
    for some in ("![a]({{placeholder: p}}.png)", "![a]({{placeholder: p}}/x)", "![a](x{{placeholder: p}})",
                 "![a]({{placeholder: p}}x{{placeholder: q}})", "![a]({{placeholder: p}}{{placeholder: q}}.png)"):
        assert reported(some) == [("path-outside-root", 1)], some
    assert reported("![a]({{placeholder: p}}) ![b]({{placeholder: p}}.png) ![c](?x)") == [("path-outside-root", 1)]


def test_a_finding_on_a_literal_start_shows_the_names_that_were_judged(tmp_path):
    root = _root(tmp_path)
    message = _messages(validate(_body("![a](%2E%2E/a/b{{placeholder: p}})"), document_root=root))[0]
    assert "read as '../a/' once percent-decoded" in message and "'%2E%2E/a/b{{placeholder: p}}'" in message
    message = _messages(validate(_body("![a](%2E%2E/a/b?{{placeholder: p}})"), document_root=root))[0]
    assert "without its query or fragment, read as '../a/b' once percent-decoded" in message


@pytest.mark.parametrize("query", ["?x", "\\?x", "&#63;x", "&quest;x", "&num;x", "\\#x"])
def test_a_directive_then_a_query_names_no_file_however_the_query_is_written(query, tmp_path):
    """``{{placeholder: p}}?x`` names no file, and neither does the same query written with an
    escape or a character reference: the rest is read as the literal start is."""
    document = replace(parse(f"{_ROOT_FRONT}# A {{#a}}\n\n![a]({{{{placeholder: p}}}}{query})\n"), path=tmp_path / "elsewhere" / "d.lgd")
    assert _found(validate(document, document_root=tmp_path / "root")) == []


def test_many_images_with_directives_are_read_in_linear_work():
    """Each image reads only the directives inside its destination, never a copy of the rest (#94)."""
    from dataclasses import replace as replace_lexed

    from legaldown.directives import lex

    class Counted(list):
        copied = 0

        def __getitem__(self, index):
            if isinstance(index, slice):
                Counted.copied += len(range(*index.indices(len(self))))
            return super().__getitem__(index)

    text = "![a]({{placeholder: p}}.png) " * 3000
    lexed = lex(text)
    found = paths_module.images(text, replace_lexed(lexed, directives=Counted(lexed.directives)))
    assert len(found) == 3000 and Counted.copied <= 3000
