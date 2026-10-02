# Conformance

`legaldown-validator` implements **Level 1 — Core** of the LegalDown specification 0.2 (§17.2):
parse and validate a single document in memory. It also claims the **Assembly** capability
(§17.6): a template and an answers set in, the assembled document out (§15.7).

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — one case per
validation rule, paired with the diagnostic a conforming validator must produce. **84 of the
corpus's 113 rules are implemented, and every one the corpus can exercise at Core level passes**,
at the line each case gives.

Every diagnostic names its file and line (§16.9, [#27](https://github.com/ForLegalAI/legaldown-validator/issues/27)):
the line of the directive, marker, heading or block it is about, or of the frontmatter node — a
mapping key, or a list entry's `-` line, at whatever depth — and for a missing key, the key that
holds it (`amends:` for a missing `amends.title`) or the frontmatter's first key. Two cases are
not asserted at their given line:

- The answer rules (`answer-invalid`, `answer-unknown`) point into the answers file, which
  `assemble` receives already parsed, so its diagnostics name no line.
- `party-name-duplicate` gives line 10, the second side; this implementation reports line 12,
  the duplicate party's own entry, as `side-name-duplicate` and `representative-name-empty`
  point at theirs ([ForLegalAI/LegalDown#42](https://github.com/ForLegalAI/LegalDown/issues/42),
  which also covers `attachment-id-duplicate`, a multi-file case not run here).

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
| Other | `frontmatter-invalid-yaml` (reported by the CLI and assembly, not the validator), `definition-circular`, `definition-used-before-declaration`, `language-code-invalid`, `authoritative-not-declared` |

In practice this means validating a set of files is out of scope: the validator does not resolve
or cross-check includes, attachment file contents, or bilingual document sets. The one thing it
reads from another file is the definitions the amended original (§7.5) and the LegalDown
attachment files (§12.4) declare, from beside a document that has a path (`legaldown.load`; the
CLI) or through `validate(resolve=)`; an unreadable file is as if it were not asked for. Single-document
authoring, editing, and CI validation are fully covered. Assembly is the exception: it reads a
template's include fragments and LegalDown attachment files, and reports the checks on them that
its output depends on (see [Assembly](#assembly-157-176)); the validator still does not.

The template constructs of specification 0.2 (§15) are validated within the document: questions,
conditions and alternatives, reference safety, `{{choose:}}`, drafting notes, insertion
boundaries, and the final option (§15.9, `validate(final=True)` or
`legaldown validate --final`); assembly is described below. Two limits apply:

- A template's include fragments and LegalDown attachment files are not read (Full, §17.4) — the
  attachment files only for the definitions they declare — so
  `question-unused` is not reported for a template that has either: a question may be used there.
- A LegalDown attachment file or include fragment validated on its own is checked as a
  standalone document: conditions, placeholders, and terms that refer to its template's
  questions and definitions are reported as undeclared.

`raw-html` (§8.7) warns about raw HTML other than comments, which renderers leave out: once for
each HTML block that holds more than comments (text after a comment's `-->` on its line
included), in list items and quotes too, and once for each text — a paragraph, a table cell, a
heading — holding an inline tag, processing instruction, declaration or CDATA section, as
cmark-gfm reads them. Comments, autolinks, code spans, escaped `<`, a link's `<…>` destination
and a directive's values are not raw HTML; frontmatter values are not Markdown. Comments and code
spans are found by the directive lexer (§11.4), which reads comments by CommonMark 0.31 and knows
no tags, so a few rare texts are read differently from cmark-gfm: a comment holding `--`, text
after an unclosed `<!--` (where cmark-gfm finds no more inline HTML), and a backtick or `<!--`
inside a CDATA section, declaration or processing instruction.

Code is literal (§11.4): fenced code anywhere, and indented code at the top level of the body,
in list items and in block quotes.

Tables follow GFM as cmark-gfm reads them
([#44](https://github.com/ForLegalAI/legaldown-validator/issues/44)): a paragraph line with a
delimiter row under it — a row of `:?-+:?` cells, as many as the header's, with at least one pipe,
indented at most three columns and no list item — is a table's header; a row's outer pipes are
optional; the body runs to a blank line or a line that starts another block (a quote, heading,
fence, raw HTML, thematic break, list item or indented code), and a line without a pipe is a row
of one cell; cells past the header's width are dropped. A table interrupts a paragraph, but a
setext underline, or a line that is itself a delimiter row, is no header there. A `|` line lazily
continues a list item's or quote's paragraph; it may be the header of a table whose delimiter row
is in the container, but a lazy delimiter row makes none. Where cmark-gfm keeps a lazy line's
indentation, an indented lazy header that starts with a pipe (` | a |`) is read as a header here
and not by cmark-gfm.

Only spaces and tabs are whitespace to the block structure, as in CommonMark
([#47](https://github.com/ForLegalAI/legaldown-validator/issues/47)): a line holding a no-break
space (common in text pasted from a word processor) is not blank, a no-break space before `>`,
`#`, `-` or `|` is not indentation, and one at a paragraph line's end or in a table cell is text.

A list item holds blocks, read as CommonMark reads an item's content (§5.7,
[#64](https://github.com/ForLegalAI/legaldown-validator/issues/64)): its first paragraph, then
later paragraphs, nested lists, code, quotes, tables and raw HTML, in any order — content after
the items nested in it included. A line belongs to an item when it is indented to the item's
content, or lazily continues a paragraph open in it; after a blank line, a line indented into
an item still open goes on in that item, and another item of the list keeps the list one (a
loose list). Code and raw HTML in an item hold no directive, a marker ends only an item's first
paragraph, and a nested item's presence includes the conditions of the items it is nested in
(§15.3). Blocks in an item are never sections, and a directive in an item's paragraph stays in
its text.

A heading in an item or a block quote, ATX (`#` alone too) or setext, is a `heading` block
([#78](https://github.com/ForLegalAI/legaldown-validator/issues/78)): its level and its text,
without the hashes, a closing sequence or the underline. It is not a section (§4.1): not
numbered, without an identifier, and not checked by the heading rules. Its directives are
checked as a paragraph's are, and a marker after it is text, as it is no paragraph for an item's
anchor (§5.7): `- # Title {#x}` is `anchor-misplaced`. As in CommonMark, a heading ends the
paragraph before it, so a line after it is not a lazy continuation line, and a setext underline
never lazily continues a paragraph into a heading. Outside items and quotes, a section's heading
needs text: a line of `#` alone is paragraph text there, where CommonMark reads an empty heading.

What the model does not keep, or reads more simply than CommonMark:

- An ATX heading indented after a list, after a blank line, stays a section heading, where
  CommonMark reads it in the item.
- An ordered item's number is not kept: a list is written numbered from 1. Whether a list is
  loose or tight is not kept either: items are written without blank lines between them.
- A fence left open in an item, under an item nested on its first line and short of that
  item's content, can run on where CommonMark ends it.
- Lists nested more than 64 deep are read that far: deeper, an item's content is one paragraph
  of its text.

A block quote's content is read as blocks too, as an item's is
([#41](https://github.com/ForLegalAI/legaldown-validator/issues/41),
[#56](https://github.com/ForLegalAI/legaldown-validator/issues/56)): paragraphs, lists, code,
nested quotes, tables and raw HTML. Code and raw HTML in a quote hold no directive, and a code
span or comment left open in one of its blocks does not run into the next. Drafting notes are
found in any quote, nested in lists and other quotes too. The model keeps a quote as its text,
one line per source line without its `>`: a tab in a line's leading markers is written as the
spaces it stands for, and a lazy continuation line indented four or more columns, or one that
looks like a table's delimiter row, is kept indented where, read again, it still continues the
paragraph. What the reading does not follow:

- Where a quote ends is found line by line, and two things are read more simply than
  CommonMark reads them: in a list inside the quote, an item's content column is not tracked;
  and an empty item is taken to interrupt a paragraph. Such a quote can end a line early or
  late.
- A quote in 16 or more list items and quotes is read as one text: its directives are checked
  as a paragraph's.

One id appears on both sides of that line. `attachment-file-missing` is defined as *the attachment
`file` path exists*, which needs the filesystem and is therefore unimplemented — but the validator
also emits that id when an attachment declares no `file` at all, which is visible in the document
itself. The specification provides no separate id for the absent key, so a diagnostic carrying
`attachment-file-missing` from the validator always means the key is missing, never that the path
failed to resolve. From assembly, which reads the file, it means the file could not be read.

`frontmatter-absent` is reported for a document that does not open with a closed `---` block,
and for one whose `---` block holds YAML that is a scalar or a list rather than a mapping of
fields: that block is not frontmatter, its `---` lines are thematic breaks, and the whole document
is validated as body. A block that cannot be read at all — YAML that is malformed, nested too
deep, or a mapping of another kind (`!!set`) — is `frontmatter-invalid-yaml`: `parse` (and `load`)
raises `FrontmatterError` for it, which the CLI reports and assembly returns as a diagnostic, for
the template or for a fragment or attachment file it reads. Any other exception while parsing is
a fault of this implementation: the CLI reports it as an internal error (exit status 2), never as
a diagnostic. As §16.6 requires, a document without frontmatter draws that Warning alone: not
`title-missing`, and not `sides-absent`.

`heading-skip` covers the first heading too: in a document with frontmatter, a main document,
the first heading is at level 1 (§4.1). A document without frontmatter may be an include
fragment or an attachment file validated on its own, which has no level-1 heading (§12), so
its first heading is not checked; the skips after it are.

`directive-malformed` is evaluated on the text the parser hands the validator, which keeps a
paragraph's lines (in a list item and a block quote too): a directive broken across two of them
is reported (§11.2, §11.5), as is one left unclosed at the end of its paragraph, list item, or
table cell. So is a `{{def:}}` whose quoted term is on the line before it: §7.2 puts the term on
the same line (`def-no-quoted-span`).

## Checking this yourself

The conformance harness runs against a checkout of the specification repository:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Each case's expected rule is asserted at its expected line, where the case gives one (above).
Cases for the rules above are skipped by name, so the 28 `not implemented` skips reproduce this
table one for one, except `ref-not-enumerated`, which has no fixture. The run reports 92 passed
and 38 skipped: eight of the other skips are the implemented rules named above, skipped as
`multi-file case` or `requires conformance level full`, and two are the `multi-file` assembly
case, which is marked Full (fixtures README, step 4) — `tests/test_assembly.py` assembles it with
a loader instead. Cases that need the final option run with it; cases that need an answers set
are assembled with it, and each assembly case is compared byte for byte with its expected output.
CI runs this on every push and pull request.

## Assembly (§15.7, §17.6)

`Template.form(answers).assemble()` and `legaldown assemble` perform §15.7.2 byte for byte and report
the answer rules of §16.12 (`answer-invalid`, `answer-missing`, `answer-unknown`). The block
structure assembly edits is recorded by the parser's own walk, so assembly and validation read a
template the same way. `Template.questions` lists the questions a template asks; `Form.questions`
those it reaches given an answers set, and `Form.unanswered` those still left open. §15.7.2 gives assembly a template that
validates without Errors, which assembly itself does not check; a `Template` refuses one with Errors
in the validator's template rules (`Template.problems`), and the deprecated `assemble` function does
not.

- A single-file template is assembled at Core, as §17.6 permits ("Core + Assembly").
- A template with include fragments or LegalDown attachment files is assembled when the caller
  passes `resolve` (`load_file` of the deprecated function), which reads them; the CLI reads them relative to the template and refuses a
  path that leads out of its directory. Reading them is a Full capability (§17.4) that this
  implementation provides for assembly without claiming Full, as §17.1 allows. Without
  `resolve`, such a template is refused with `include-file-missing` or
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
  `directive-malformed`, which the validator reports too (see above): it could not be filled.
- A template with `translations` is refused with `translation-file-missing`: its linked templates
  are assembled with it (§15.7.2), which this implementation does not do. The specification has
  no id for a capability an implementation lacks, so the refusal uses the rule it would otherwise
  break.

Assembly edits the template's source and so inherits a few limits of the parser's reading of it:

- An item marker inside a block quote (`> 2. x`) is judged from the line before for the
  line-start check (§15.7.3), not from the list the quote's content holds.
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
