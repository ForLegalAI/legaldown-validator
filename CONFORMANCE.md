# Conformance

`legaldown-validator` implements **Level 1 — Core** of the LegalDown specification 0.2 (§17.2):
parse and validate a single document in memory. It also claims the **Assembly** capability
(§17.6): a template and an answers set in, the assembled document out (§15.7).

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — one case per
validation rule, paired with the diagnostic a conforming validator must produce. **84 of the
corpus's 113 rules are implemented, and every one the corpus can exercise at Core level passes.**

The specification defines 116 rules. The corpus has no fixture for three of them, since a
document alone cannot exercise them. This implementation covers two of the three,
`anchor-autogen-collision` and `legaldown-version-newer`, with unit tests. The third,
`ref-not-enumerated`, depends on the style template and is listed below.

Eight implemented rules — `amend-def-override`, `amend-term-undefined`, `amend-term-unresolvable`,
`attachment-id-collision`, `attachment-id-duplicate`, `attachment-title-empty`,
`attachment-unreferenced`, and `template-fragment-invalid` — have fixtures that span several files
or are marked for the Full level, so this single-document harness skips them. They are covered by
the unit tests in `tests/test_spec_alignment.py` and `tests/test_templates.py` instead. Of
`template-fragment-invalid`, the parts that need only the template are implemented (a fragment
included twice, a LegalDown attachment file declared twice or also included, an include inside a
drafting note); the parts that read a fragment's content are Full (§17.4).

The specification (§17.5) requires an implementation never to silently skip a check it cannot
perform, so the remaining rules are named here. Most of them belong to conformance levels this
implementation does not claim — Rendering (§17.3) and Full (§17.4) — because they need to open
files other than the document itself.

## Rules not implemented

| Group | Rules |
|---|---|
| Filesystem-dependent (Full, §17.4) | `amends-file-missing`, `attachment-file-missing`, `attachment-has-frontmatter`, `attachment-has-h1`, `attachment-anchor-duplicate`, `supersedes-file-missing`, `path-not-relative`, `path-outside-root` |
| Includes (Full, §17.4) | `include-file-missing`, `include-not-legaldown`, `include-cycle`, `include-has-frontmatter`, `include-has-h1`, `include-anchor-duplicate`, `include-heading-skip` |
| Bilingual sets (Full, §17.4) | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-implicit-id`, `translation-authoritative-absent`, `translation-template-mismatch` |
| Rendering (§17.3) | `ref-not-enumerated` |
| Lexer-level grammar (§11.2–11.4) | `raw-html` |
| Other | `frontmatter-invalid-yaml` (reported by the CLI, not the validator), `definition-circular`, `definition-used-before-declaration`, `language-code-invalid`, `authoritative-not-declared` |

In practice this means validating a set of files is out of scope: the validator does not resolve
or cross-check includes, attachment file contents, or bilingual document sets. Single-document
authoring, editing, and CI validation are fully covered. Assembly is the exception: it reads a
template's include fragments and LegalDown attachment files, and reports the checks on them that
its output depends on (see [Assembly](#assembly-157-176)); the validator still does not.

The template constructs of specification 0.2 (§15) are validated within the document: questions,
conditions and alternatives, reference safety, `{{choose:}}`, drafting notes, insertion
boundaries, and the final option (§15.9, `validate_document(final=True)` or
`legaldown validate --final`); assembly is described below. Three limits apply:

- A template's include fragments and LegalDown attachment files are not read (Full, §17.4), so
  `question-unused` is not reported for a template that has either: a question may be used there.
- The parser flattens nested lists ([#16](https://github.com/ForLegalAI/legaldown-validator/issues/16)),
  so a nested list item's presence does not include the conditions of the items it is nested in.
- A LegalDown attachment file or include fragment validated on its own is checked as a
  standalone document: conditions, placeholders, and terms that refer to its template's
  questions and definitions are reported as undeclared.

Code is literal (§11.4): fenced code anywhere, and indented code at the top level of the body.
Indented code inside a block quote or a list item is not recognized, so directives in it are
checked. After a blank line, a line indented to the last list item's content continues that
item, as CommonMark reads it (§5.7,
[#54](https://github.com/ForLegalAI/legaldown-validator/issues/54)): a later paragraph or a fence
there is part of the item, so the fence is code, a marker at the end of a later paragraph is
misplaced, and the item's condition removes it too. Raw HTML in a list item, there or on its first
line, is read as the item's text, so directives in it are checked
([#59](https://github.com/ForLegalAI/legaldown-validator/issues/59)). Four limits follow from the
flat list model ([#16](https://github.com/ForLegalAI/legaldown-validator/issues/16)):

- An ATX heading indented after a list stays a section heading, where CommonMark reads it in the
  item.
- A setext heading in an item's later content is read as more of its text.
- A line indented less than the last item's content but into an ancestor item stays a paragraph
  after the list; written back, one that would open a block gets a backslash.
- A blank line between two items still makes two lists.

A fence that opens a list item's or a quote's text on its only line (`- ~~~ …`) is code. One
behind nested markers (`- > ~~~ …`, `> - ~~~ …`), or one running over several lines of a nested
quote or item, is not recognized: directives in it are checked where CommonMark reads code.

Drafting notes are found in quote blocks, in list items, and nested in other quotes. Two nestings
are not followed: a quote inside a list inside a quote (`> - > [!DRAFTING]`), and a lazy
continuation line of a nested quote. Either needs a full CommonMark container parser.

One id appears on both sides of that line. `attachment-file-missing` is defined as *the attachment
`file` path exists*, which needs the filesystem and is therefore unimplemented — but the validator
also emits that id when an attachment declares no `file` at all, which is visible in the document
itself. The specification provides no separate id for the absent key, so a diagnostic carrying
`attachment-file-missing` from the validator always means the key is missing, never that the path
failed to resolve. From assembly, which reads the file, it means the file could not be read.

`frontmatter-absent` is reported for a document that does not open with a closed `---` block,
and for one whose `---` block holds YAML that is a scalar or a list rather than a mapping of
fields: that block is not frontmatter, its `---` lines are thematic breaks, and the whole document
is validated as body. A block that is not valid YAML at all is `frontmatter-invalid-yaml`, which
the CLI reports; `parse_document` raises for it. As §16.6 requires, a document without
frontmatter draws that Warning alone: not `title-missing`, and not `sides-absent`.

`heading-skip` covers the first heading too: in a document with frontmatter, a main document,
the first heading is at level 1 (§4.1). A document without frontmatter may be an include
fragment or an attachment file validated on its own, which has no level-1 heading (§12), so
its first heading is not checked; the skips after it are.

`directive-malformed` is evaluated on the text the parser hands the validator. The parser joins
the lines of a paragraph (and a list item's continuation lines) with a space, so a directive
broken across two of those lines reaches the validator on one line and is not reported. A
directive left unclosed at the end of its paragraph, list item, or table cell is reported.

## Checking this yourself

The conformance harness runs against a checkout of the specification repository:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Cases for the rules above are skipped by name, so the 29 `not implemented` skips reproduce this
table one for one, except `ref-not-enumerated`, which has no fixture. The run reports 91 passed
and 39 skipped: eight of the other skips are the implemented rules named above, skipped as
`multi-file case` or `requires conformance level full`, and two are the `multi-file` assembly
case, which is marked Full (fixtures README, step 4) — `tests/test_assembly.py` assembles it with
a loader instead. Cases that need the final option run with it; cases that need an answers set
are assembled with it, and each assembly case is compared byte for byte with its expected output.
CI runs this on every push and pull request.

## Assembly (§15.7, §17.6)

`assemble(template, answers)` and `legaldown assemble` perform §15.7.2 byte for byte and report
the answer rules of §16.12 (`answer-invalid`, `answer-missing`, `answer-unknown`). The block
structure assembly edits is recorded by the parser's own walk, so assembly and validation read a
template the same way. `template_questions` and `needed_questions` list the questions a template
asks, all of them or those an answers set still leaves open.

- A single-file template is assembled at Core, as §17.6 permits ("Core + Assembly").
- A template with include fragments or LegalDown attachment files is assembled when the caller
  passes `load_file`, which reads them; the CLI reads them relative to the template and refuses a
  path that leads out of its directory. Reading them is a Full capability (§17.4) that this
  implementation provides for assembly without claiming Full, as §17.1 allows. Without
  `load_file`, such a template is refused with `include-file-missing` or
  `attachment-file-missing`, never assembled partially (§17.6).
- A file assembly reads is checked as the Full level checks it (§16.10–§16.12), and the template
  is refused if it fails: frontmatter (`include-has-frontmatter`, `attachment-has-frontmatter`), a
  level 1 heading (`include-has-h1`, `attachment-has-h1`), and, in a template,
  `template-fragment-invalid` for an `{{include:}}` in a fragment or attachment file, or a
  condition, a drafting note, or a heading without an explicit identifier in a fragment. An
  attachment file may hold conditions of its own (§15.3). An `{{include:}}` in a fragment or
  attachment file of a document that is not a template is not assembled: it is refused with
  `include-file-missing`. The other Full checks — `include-cycle`, the `*-anchor-duplicate` and
  `include-heading-skip` rules, and the path rules — are not made.
- A `{{placeholder:}}` or `{{choose:}}` written across two lines is refused with
  `directive-malformed`: the validator reads it on one line (see above), but it could not be
  filled.
- A template with `translations` is refused with `translation-file-missing`: its linked templates
  are assembled with it (§15.7.2), which this implementation does not do. The specification has
  no id for a capability an implementation lacks, so the refusal uses the rule it would otherwise
  break.

Assembly edits the template's source and so inherits a few limits of the parser's reading of it:

- An item marker inside a block quote (`> 2. x`) is judged from the line before for the
  line-start check (§15.7.3); the parser does not read lists inside quotes.
- Removing a drafting note or unit can join what it separated: indented code after a removed note
  that followed a list becomes a paragraph of the list's last item, and two code blocks separated
  only by a removed note merge. §15.7.2 step 2 removes the lines; the template author keeps such
  blocks apart with text that stays.
- A drafting note that starts on a list item's marker line (`- > [!DRAFTING]`) is removed with that
  line, the marker included.
- A file is written back with one line ending: its first LF or CRLF, or CR in a file without LF.
  A file that mixes them comes out with that one throughout.
- A frontmatter placeholder in a double-quoted scalar is found in the line as written, so one
  whose `note` uses the scalar's own escapes (`\"`) is not recognized, and is left unfilled.

All six cases of the corpus's `fixtures/assembly` assemble byte for byte, the multi-file case
included, and each output validates without Errors (§15.7.4).

## Declaring conformance in code

The package exports what it targets, so consumers can assert it:

```python
import legaldown

legaldown.SPEC_VERSION       # "0.2"  — specification version targeted
legaldown.CONFORMANCE_LEVEL  # "core" — conformance level claimed
legaldown.CAPABILITIES       # frozenset({"assembly"}) — capabilities claimed (§17.6)
legaldown.__version__        # implementation version
```

The CLI's JSON output repeats the first and last of these on every run, as `legaldown_spec` and
`validator_version`. The conformance level is not part of the JSON payload — read it from the
package.
