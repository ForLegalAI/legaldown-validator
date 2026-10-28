# Conformance

`legaldown-validator` implements **Level 1 — Core** of the LegalDown specification 0.2 (§17.2):
parse and validate a single document in memory. It also claims the **Assembly** capability
(§17.6): a template and an answers set in, the assembled document out (§15.7).

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — one case per
validation rule, paired with the diagnostic a conforming validator must produce. **94 of the
corpus's 113 rules are implemented, and every case the harness runs passes**, at the line each
case gives and, for a finding in a file the document includes, in that file.

Every diagnostic names its file and line (§16.9, [#27](https://github.com/ForLegalAI/legaldown-validator/issues/27)):
the file is the document's, or the include fragment it was found in ([#35](https://github.com/ForLegalAI/legaldown-validator/issues/35));
the line of the directive, marker, heading or block it is about, or of the frontmatter node — a
mapping key, or a list entry's `-` line, at whatever depth — and for a missing key, the key that
holds it (`amends:` for a missing `amends.title`) or the frontmatter's first key. Two cases are
not asserted at their given line:

- The answer rules (`answer-invalid`, `answer-unknown`) point into the answers file, which
  assembly receives already parsed (`Template.form(answers)`), so its diagnostics name no line.
- `party-name-duplicate` gives line 10, the second side; this implementation reports line 12,
  the duplicate party's own entry, as `side-name-duplicate` and `representative-name-empty`
  point at theirs ([ForLegalAI/LegalDown#42](https://github.com/ForLegalAI/LegalDown/issues/42),
  which also covers `attachment-id-duplicate`, a multi-file case not run here).

The specification defines 116 rules. The corpus has no fixture for three of them, since a
document alone cannot exercise them. This implementation covers two of the three,
`anchor-autogen-collision` and `legaldown-version-newer`, with unit tests. The third,
`ref-not-enumerated`, depends on the style template and is listed below.

Seven implemented rules — `amend-def-override`, `amend-term-undefined`, `amend-term-unresolvable`,
`attachment-id-collision`, `attachment-id-duplicate`, `attachment-title-empty` and
`attachment-unreferenced` — have fixtures that span several files or are marked for the Full
level, so this single-document harness skips them. They are covered by the unit tests in
`tests/test_spec_alignment.py` and `tests/test_templates.py` instead.

The fixtures of five rules that read an include fragment — `include-file-missing`,
`include-has-frontmatter`, `include-has-h1`, `include-heading-skip` and
`template-fragment-invalid` — are marked Full and span files, and are run all the same: with
`validate(resolve=file_loader(<the case's directory>))`, asserting the rule, the level, the line
**and the file** each gives ([#35](https://github.com/ForLegalAI/legaldown-validator/issues/35),
[Include fragments](#include-fragments-122-1611-1612)). `include-not-legaldown` is Core (§17.2: the
extension check needs only the document) and runs as any other case, file included.

The specification (§17.5) requires an implementation never to silently skip a check it cannot
perform, so the remaining rules are named here, and a validation names them again, in the
document's own diagnostics, when the document holds a construct that calls for one
([Constructs beyond the level](#constructs-beyond-the-level-175)). Most of them belong to
conformance levels this implementation does not claim — Rendering (§17.3) and Full (§17.4) —
because they need to open files other than the document itself.

## Rules not implemented

| Group | Rules |
|---|---|
| Filesystem-dependent (Full, §17.4) | `amends-file-missing`, `attachment-file-missing`, `attachment-has-frontmatter`, `attachment-has-h1`, `attachment-anchor-duplicate`, `supersedes-file-missing`; of these, `attachment-has-frontmatter` and `attachment-has-h1` are reported for an attachment file validated on its own (`validate(fragment=True, fragment_kind="attachment")`, below), and by assembly, but not for an attachment file `validate(resolve=)` reads (see [Include fragments](#include-fragments-122-1611-1612)) |
| Includes (Full, §17.4) | `include-cycle`, `include-anchor-duplicate`. The rest of the group is implemented ([#35](https://github.com/ForLegalAI/legaldown-validator/issues/35)): `include-not-legaldown` at Core; `include-file-missing`, `include-has-frontmatter`, `include-has-h1` and `include-heading-skip` when the validation can read the fragments; and the parts of `template-fragment-invalid` that read them |
| Bilingual sets (Full, §17.4) | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-implicit-id`, `translation-authoritative-absent`, `translation-template-mismatch` |
| Rendering (§17.3) | `ref-not-enumerated` |
| Other | `definition-used-before-declaration`, `language-code-invalid`, `authoritative-not-declared` |

In practice this means validating a set of files is out of scope: the validator does not
cross-check attachment file contents or bilingual document sets, nor the identifiers or
cycles across a document and its fragments. What it reads from another file is the definitions the
amended original (§7.5) and the LegalDown attachment files (§12.4) declare, and, to check them
([Include fragments](#include-fragments-122-1611-1612)), the include fragments (§12.2), from beside a
document that has a path (`legaldown.load`; the CLI) or through `validate(resolve=)`; an
attachment or original that is unreadable is as if it were not asked for, and a fragment that is not
there is `include-file-missing`. Single-document authoring, editing, and CI validation are fully
covered. Assembly reads a template's include fragments and LegalDown attachment files as well, and
reports the checks on them that its output depends on, from the same implementation (see
[Assembly](#assembly-157-176)).

The template constructs of specification 0.2 (§15) are validated within the document: questions,
conditions and alternatives, reference safety, `{{choose:}}`, drafting notes, insertion
boundaries, and the final option (§15.9, `validate(final=True)` or
`legaldown validate --final`); assembly is described below. Two limits apply:

- A template's include fragments and LegalDown attachment files are read for what a template may
  hold in them and for the definitions of the attachment files, not for the questions they use,
  so `question-unused` is not reported for a template that has either: a question may be used there.
- A LegalDown attachment file or include fragment validated on its own (`validate(fragment=True)`,
  `legaldown validate --fragment`) is checked as a body-only fragment (§12.2, §12.4): no frontmatter,
  title, parties or sides are expected, and a first heading at any level is accepted. What it
  refers to in its host's frontmatter or body (a party, side, attachment or question it does not
  declare, a `{{ref:}}` or `{{term:}}` it does not hold) is still reported, with the rule id and
  level it has in a complete document, but with `Diagnostic.host_dependent` set: only the including
  document can settle it. So is a definition it declares and does not reference
  (`def-unreferenced`), which the host may reference. A fragment cannot tell
  whether its host is a template, so it is one exactly when it holds a condition or a `{{choose:}}`,
  unless `validate(template=)` (`legaldown validate --fragment --template` / `--no-template`) says
  so: a blank or a drafting note is legal in any document. A host that is no template declares no
  question, so a condition or a `{{choose:}}` naming one is then the fragment's own
  `condition-invalid` or `choose-invalid`, not `host_dependent`. What §15.3 asks of a template's
  fragment is asked in this mode only when `template=True` says the host is a template (below).
  `legaldown validate --fragment` marks the host-dependent findings in its text output, counts them apart in its
  summary, and its exit status ignores them. Rule ids and levels are unchanged, and without `fragment=True` nothing is
  flagged and a document is checked as a complete one. The fragment's own frontmatter is
  `include-has-frontmatter` and its level-1 heading `include-has-h1`, or, for `fragment_kind="attachment"`
  (`--fragment-kind attachment`), `attachment-has-frontmatter` and `attachment-has-h1` (§16.10);
  the fragment's own `{{include:}}` paths are judged (`path-not-relative`, `include-not-legaldown`) and
  its includes are never read.
  *Kind* (`fragment_kind=`, default `"include"`) says what the file is to its host. With
  `template=True` an include fragment is held to §15.3 as assembly holds it, and each offender is a
  `template-fragment-invalid`: a condition (on a heading, a paragraph or a list item), a drafting note,
  an `{{include:}}` wherever it is, and a heading without an explicit identifier. An attachment file
  is held to the `{{include:}}` alone (§12.4, §16.12: "on `{{include:}}` inside an attachment file");
  its conditions are its own, beneath its attachment's `when`, and a drafting note is legal in it.
  Without `template=True` (the default `None`, or `False`) none of these is asked: a conditional
  fragment is only inferred a template, and its host may be a document.

`definition-circular` (§16.3, [#105](https://github.com/ForLegalAI/legaldown-validator/issues/105))
reads a definition's scope as §7.2 gives it "for purposes such as circular-reference detection": the
paragraph holding its `{{def:}}`. A definition is explained in terms of another when a
`{{term:}}` of the other is in that paragraph, and definitions that are each explained in terms of
the next, round to the first, are circular. A list item's paragraph, each block of a block quote
and a heading in a quote are paragraphs; a table's scope is a row, so a glossary row
(`| "Foo" {{def: foo}} | means {{term: bar}} |`) is one definition's paragraph, and so is its
header row; a block quote nested deeper than the parser reads is one paragraph. A definition in a
list item has its own paragraph as scope, as §7.2 says, so the references in the item's sub-list
(the layout `- "Fee" {{def: fee}} means:` over `(a)` and `(b)` items) are not part of the
definition, and a circle that runs only through them is not reported.

The specification does not say how many diagnostics a circle gets, nor where. One Warning is
given for each set of definitions that lead round to each other (a strongly connected component of
the "explained in terms of" relation, however many circles it holds), at the line of the last of
them in document order, where the circle closes, which is where the fixture has it (line 23);
the message names up to five ids. The Warnings come in that order, so the output does not depend
on the order the search visits the definitions. A circle has at least two distinct definitions.
**A reference in a definition's own paragraph is not a circle:** `"Services" {{def: services}} means
x. See {{term: services}}.` is ordinary wording, and the specification's own fixtures write it in
17 cases (the translation, amendment and single-quote cases), none of which expects a Warning. The
one self-reference reported is a `{{term:}}` to itself in the definition's own quoted term
(`"Self {{term: s}}" {{def: s}}`), which cannot be read any other way.

Only definitions that are registered count (not a duplicate, or an invalid id); a `{{term:}}`
in code or a comment, or an undeclared id, is no reference; a `{{term:}}` in a drafting note
is not either, as assembly removes it (§15.6). A definition imported from the amended original or
an attachment file is a target and never a source (the validator reads only the ids of those
files), so a circle through several files is Full (§17.4), but a definition of the document that
shadows an imported id still is a source. The check is linear in the paragraphs and references,
and takes no recursion, so a ring of any length, or a paragraph holding thousands of definitions,
is as cheap as the rest.

Two approximations, both towards giving a Warning (a Warning is better given than withheld):

- **Paragraph scope.** A paragraph cannot tell which of its definitions a reference explains, so
  two definitions in one paragraph that references both are reported as explaining each other
  (`"A" {{def: a}} and "B" {{def: b}} use {{term: a}} and {{term: b}}.`). A reference to one of them
  only is not (`"A" {{def: a}} means x. "B" {{def: b}} means {{term: a}}.`).
- **Alternatives (§15.4).** In a template, a reference does not lead to a declaration it can never
  appear with (their conditions exclude each other), so alternatives that share an id do not make a
  circle together. Only a reference and the declaration it names are compared, each pair of
  conditions once, so a circle whose members exclude each other further round (`d1` when `q` names
  `d2`, which names `d3` when not `q`, which names `d4`, which names `d1`) is still reported; an
  exact answer needs the answers to every question tried together. The tests are at most the
  references' distinct conditions times the alternatives of the id they name.

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

`path-not-relative` and `path-outside-root` (§2.3, §16.2, §17.2,
[#104](https://github.com/ForLegalAI/legaldown-validator/issues/104)) are Core checks, and syntactic:
they read the text of a path and never a file or the file system, so a symbolic link is not followed,
and whether the file exists is Full (§17.4): for an include, `include-file-missing` when the validation can read files ([Include fragments](#include-fragments-122-1611-1612)). The paths are the ones §2.3 lists: `{{include:}}`, an
image's path, `attachments[].file`, `amends.file`, `supersedes.file` (the object form only: a string is
a description of the document, §3.2) and `translations`. Each is reported at its own line: the directive,
the image, or the key (the entry of a list). An empty path is `attachment-file-missing`'s, not these.

- A path is not relative when it begins with a URI scheme (`[A-Za-z][A-Za-z0-9+.-]*:`: `https://`,
  `file://`, `data:`, `mailto:`, and a drive letter, `C:/x` or `C:x`) or with `/` or `\` (`//host`,
  `\\host`), whitespace around it ignored. `../x` is relative. A relative name whose first segment holds a
  colon (`a:b.lgd`) reads as having the scheme `a`, as RFC 3986 does; write `./a:b.lgd`. A path that is
  not relative is one finding, not also outside the root.
- No document root is configured by default (§2.3): `validate(document_root=)` and
  `legaldown validate --document-root DIR` set it (the fixtures' `requires_config` `document_root`,
  relative to the case's directory). Without one the containment check does not run, and
  `../shared/definitions.lgd` is valid. With one, `normpath(directory of the document / path)` must be at
  or below the root, by names alone, with `/` and `\` both separating names (`a/../b` stays, `a/../../b`
  leaves). The document and the root are made absolute first, so a relative `path` and `.` compare; a document
  from `parse` has no path and is taken to be at the root, so any leading `..` leaves it. A loaded document
  below the root may climb up to it. A document outside the root cannot name a file within it: that is
  reported once, as `path-outside-root` at line 1 saying so, not once for each path, and only when it
  names a relative path at all.
- Reading is stricter than validating: `files.relative_path`, which assembly and the definitions read for
  `amends` and attachments use, still refuses `../` always (a file outside the document's directory is never
  read, §2.3), and now every URI scheme as well (it let `https://example.com/a.lgd` through as
  `https:/example.com/a.lgd`). So `../shared/x.lgd` validates clean, with a root that permits it or without one, but
  such a file is not read ([#109](https://github.com/ForLegalAI/legaldown-validator/issues/109)). Assembly does not report these two rules.
- An image is the inline `![alt](destination)` (§8.7), in a paragraph, list item, quote, table cell, heading
  or definition, and in the alt text of another image (both are checked). The destination is `<…>` or a
  raw one with balanced parentheses, then an optional title; backslash escapes and character references in
  it are decoded (`&#47;etc` is `/etc`). A destination is a URL, so it is read as one: a query or fragment
  (`?…`, `#…`, the first of either) is not part of the path (`x.png?p=/../..` stays in the root), and **it is
  also read percent-decoded**: `%2Fetc` is not relative and `%2E%2E/%2E%2E/x.png` leaves a root, since a tool
  that opens the file decodes the URL first; a `%` not followed by two hex digits stays as written. Only
  a `/` or `\` that decoding yields at the start, or a drive, one letter of either case and a colon
  (`C%3A/x`, `c%3A%5Cx`, `C%3Ax`, `a%3Ab.png`, as `C:x` written is), makes a path absolute: otherwise a
  scheme is the one written, so `https%3A//x` and `ab%3Ax` are relative. No other reference is percent-decoded or loses a query.
  Not an image: one in a code span, fenced or indented code, a comment or a raw HTML block, `\![a](x)`
  (a link after a literal `!`), a link or autolink (valid, §8.7), `<img src>` (that is `raw-html`),
  and a reference-style image whose label no definition has. A reference-style image (`![a][r]`, `![a][]` or
  `![a]` with `[r]: path`, [#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)) names its file
  by the first definition of its label (`collect_link_definitions`): the definition's destination is read as an
  image's is, and checked **once, where it is written** (the line of `[r]: path`, in an item or a quote too),
  however many images name it; a definition no image names is no file reference. As in CommonMark a label that
  follows the text decides (`![a][r]` with only `[a]` defined is no image), `![a][]` and `![a]` take the text as
  the label if it holds no bracket, and no space may come between the text and the label. A definition with a
  directive in it is paragraph text (below), so no image names it. A directive in
  an image's destination (`![a](https://{{placeholder: host}}/x.png)`) leaves its path unknown until it is
  filled in (assembly's output is validated again, §15.7.4), but the text before the first directive may
  decide already: one that begins with a scheme or a `/` or `\` (`![a](/abs/{{placeholder: p}}.png)`) is
  `path-not-relative` whatever is filled in. The rest is not judged for being relative, and for the root only the
  names before the last separator of that text are (`../../../{{placeholder: p}}.png` leaves the root; the
  name before the directive is partial, as `..` may be `..x` once filled in) unless a `?` or `#` ends the
  path before the directive (`..?{{placeholder: p}}` leaves it). A destination of directives alone, or whose
  path is a query or fragment only (`?{{placeholder: p}}`), names no file: `![a]({{placeholder: p}}.png)` does. A directive in the alt text is no matter.
- In a fragment (`validate(fragment=True)`) the include and image paths are its own, whoever includes it:
  they are checked and never `host_dependent`. Its frontmatter is never read, so none of its keys is checked.
  A document whose frontmatter cannot be read is validated by its body alone, as for the other rules.
- A document that validated clean may now carry these Errors, and so `legaldown validate` exit with status 1.

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
never lazily continues a paragraph into a heading. After a blank line, a heading indented to an
item's content is the item's too, as CommonMark has it
([#84](https://github.com/ForLegalAI/legaldown-validator/issues/84)); indented less, or not at
all, it is a section's. Outside items and quotes, a section's heading needs text: a line of `#`
alone is paragraph text there, where CommonMark reads an empty heading.

A link reference definition (CommonMark §4.7,
[#92](https://github.com/ForLegalAI/legaldown-validator/issues/92)) is a `link_definition` block, in an
item or a quote too: its `label` as written (without the spaces around it), its `target` without
`<>` and its `title` without its delimiters, each raw — backslash escapes and character references
are not decoded, so the model keeps what was written. It is read from a closed paragraph, as
CommonMark reads it: the definitions a paragraph *opens with* are blocks of their own, and what is
left of it is the paragraph (or a heading's text). So a definition cannot interrupt a paragraph (`a`
then `[x]: u` is one paragraph), a lazy line is part of the paragraph it continues, a title that is
not closed, or is followed by text on its line, is not read (the definition stands without it if its
destination's line ends there, and the title's line is then text), and a setext underline under
nothing but definitions is no underline: `===` starts a paragraph and `---` is a thematic break. A
label is matched case-folded (`str.casefold`) with each run of spaces, tabs and line breaks one space,
and the first definition of a label wins, wherever it is in the document
(`collect_link_definitions`, which a quote deeper than 16 or an item deeper than 64 is not read for).
A definition is no text to the validator and no unit to assembly: it holds no directive, marker or
anchor, an item's first paragraph (where an anchor may end, and a condition applies, §5.7, §15.3)
is the first block after any definitions it opens with, and `[x]: u` over a lone `{{include:}}`
line leaves the line an include paragraph. Written back, each definition is a paragraph of its own,
blank-separated: the layout is normalized and the model is the same on a second reading. In a list item
a definition and the block after it (another definition, a paragraph, a list, a quote, a fence) are
written directly under one another where that is read back as the same two blocks, so that a tight list
stays tight (a blank line there would make a renderer wrap its items in paragraphs); a title-like
line under a definition is not, as it would become its title. (A list is always written tight, as the model
keeps no looseness (below), so a loose list with a definition in it is written tight after it too.) A reference-style
image (`![a][r]`, `![a][]`, `![a]`) is checked for `path-not-relative` and `path-outside-root` on its
definition's destination, once, at the definition, however many images name it (#104, above).

Where this differs from CommonMark, or is read more simply:

- **A definition with `{{` in it stays paragraph text** (the run of definitions ends before it). CommonMark
  reads it as a definition whose destination or title holds the directive's text as written, but that is not
  final until assembly fills the directive in, and the validator must see the line as written
  (`[x]: {{ref: nope}}` is a `ref` block, `ref-broken`; `[label]: {{placeholder: p}}` is an
  `insertion-boundary` Error). The definitions before it are blocks, it and everything after it one paragraph.
  A `link_definition` that a model built in code holds a directive in (or a label of more than 999 characters)
  is not read back as a definition: it is written as it is, always apart from the blocks around it by blank
  lines, and read back as the text it is.
- **A setext underline under only definitions, in a quote or an item, followed by a line without the
  container's marker:** CommonMark starts a paragraph at `===` that the line continues; here the quote or
  item has closed its paragraph at the underline, so the line is not read into it (`> [x]: u`, `> ===`,
  `text`). Outside containers the paragraph is as CommonMark has it.
- At `MAX_LIST_DEPTH` an item is one paragraph, and `[x]: u` there is text: the serializer writes it with a
  backslash, as it does `# h`. The same holds for a quote deeper than `MAX_QUOTE_DEPTH`.
- A paragraph of a model that reads as a definition (`[x]: u`) is written `\[x]: u`, so it stays a paragraph; one
  that would be a block alone only because definitions were peeled off before it (`<span>`, `2. x`) is written
  directly under them, as the parser read it.
- A label longer than 999 characters, built in code, is written and read back as paragraph text; a blank line in
  a label or a title is a space (it would end the paragraph), a line break in a target is dropped, and an
  unescaped bracket in a label, a `<` or `>` in a destination written in `<>`, or a title delimiter in a title is
  escaped when written.
- A bare destination is one with no space or control character, balanced parentheses (at most 32 deep) and not
  beginning with `<`; one that is not is written in `<>`.
- The differential test (`tests/test_link_definitions_differential.py`) compares the definitions and the blocks
  with pandoc (`commonmark`) and markdown-it-py over generated documents. They disagree with each other in a few
  places: pandoc leaves a title or destination on a lazy continuation line (`1. [a]: /u`, then `  "t"`) out of the
  definition, which markdown-it and cmark's algorithm (the lazy line is paragraph text) keep in, and this package
  follows the latter; markdown-it differs from pandoc after a line holding only a tag, and where a setext
  underline stands under a lone label. The test asks that no definition here is neither pandoc's nor markdown-it's.

What the model does not keep, or reads more simply than CommonMark:

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
deep, or a mapping of another kind (`!!set`), as prose between two `---` lines is — is
`frontmatter-invalid-yaml`. `parse` (and `load`) raise `FrontmatterError` for it by default;
given `on_frontmatter_error="keep"`, which the CLI and the conformance run use, they keep the
error as `Document.frontmatter_error` and read the rest as the body. `validate` then reports it
(an Error, at the line of the problem) and goes on to validate that body (#43): §16.1 says an
Error prevents rendering, not that it stops the validation, and the fixture is not exhaustive.
The block is still the frontmatter, so the document is not `frontmatter-absent`, and its first
heading must still be level 1 (`heading-skip`, §4.1). Its metadata is read as empty, so nothing
is reported for want of what it might have declared (`title-missing`, `sides-absent`,
`sides-minimum`, `parties-minimum`, `issuer-side-required`), and nothing that needs those
declarations is reported either: `party-unknown`, `side-unknown`, `attach-undeclared`,
`ref-targets-attachment`, `amend-term-undefined`, `amend-term-unresolvable`, and
`condition-invalid` and `choose-invalid` where they name a question that is not declared (a
malformed condition, or the wrong form for a declared question, is not). What the body decides
alone is reported, `ref-broken`, `heading-*`, `anchor-*`, `directive-*`, `def-*` and `raw-html`
among them, and `term-undefined` too, although the original an `amends` names might have defined
the term. A block of comments only, such as a heading written as a comment between two `---`
lines, is valid YAML: it is present and empty (§3.1) and not body, so `title-missing` and
`sides-absent` point at it. `serialize`, `save`, `Document.to_dict` and `document_to_dict` raise a new
`FrontmatterError` for such a document, so that nothing written from it loses the block; it
keeps being reported and read as empty until `Document.frontmatter_error` is set to None. The
message is one line, without position or snippet; assembly and `validate` give the same message
and line. Assembly
returns it as a diagnostic, for the template or for a fragment or attachment file it reads, the
latter with that file's normalised path as the diagnostic's `file` and the line in it
(`parse_template` and `load_template` still raise the template's own). A fragment validated on its own (`validate(fragment=True)`,
`legaldown validate --fragment`) never reads its frontmatter: a block it has, readable or not, is
`include-has-frontmatter`, at the line of the problem for one that cannot be read, and
`frontmatter-invalid-yaml` is not reported as well. Any other exception while parsing is a fault of
this implementation: the CLI reports it as an internal error (exit status 2), never as a
diagnostic. As §16.6 requires, a document without frontmatter draws that Warning alone: not
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

## Include fragments (§12.2, §16.11, §16.12)

When the validation can read files (`validate(resolve=)`, or a document with a path, which is read
from its own directory), it reads the fragment of each **include paragraph** (§12.2): a top-level or
preamble paragraph that holds a single `{{include:}}` and at most its marker. An include in a
sentence, a list item, a table cell or a quote is text, is not replaced by a fragment, and is not
read, as assembly reads it; neither is one in a drafting note, which assembly removes unread (§15.6).
Each file is asked for once and parsed once, whoever wants it (the definitions of an attachment
file, the checks below, `include-heading-skip`), and what is reported is in that file's own
`Diagnostic.file` (its normalised path from the document's directory, the key `Template`
reads it by) at the line in it:

| Rule | Reported |
|---|---|
| `include-not-legaldown` (Core) | at the include whose path does not end in `.lgd`, `.legaldown` or `.legal.md`; no resolver needed, and the file is never read |
| `include-file-missing` | at the include (once for each include) of a fragment the loader does not find |
| `include-has-frontmatter` | at the first key of the fragment's frontmatter, or line 1 for an empty block; for a block that cannot be read, at the line of the problem (#43) |
| `include-has-h1` | at each level-1 heading of the fragment |
| `include-heading-skip` | at the heading that skips a level once the fragments are spliced in |
| `template-fragment-invalid` | in a template, at each condition, drafting note, `{{include:}}` or heading without an identifier in a fragment, and at each `{{include:}}` in a LegalDown attachment file |

Decisions the specification leaves open:

- **A path already judged is judged once.** An include path that file reference checks reported
  (`path-not-relative`, an absolute path or one with a URI scheme; `path-outside-root`, given a
  `document_root`) draws that finding alone: it is not also `include-file-missing` or
  `include-not-legaldown`. A path that leads out of the document's directory (`../x.lgd`) is valid as a
  path (§2.3), with a `document_root` or without one, but a `LoadFile` is never asked for it
  (`legaldown.files.relative_path`), as for one that holds a backslash, and assembly refuses a template
  for it as for a file that is not there. **Known limitation**
  ([#109](https://github.com/ForLegalAI/legaldown-validator/issues/109)): this implementation reads
  nothing outside the document's directory, so such an include is skipped, never
  `include-file-missing`, whether a root contains it or there is none (it would break a document whose
  `../shared/x.lgd` exists). The skip is said once, by the Warning of §17.5 below. Given a root it leaves, it is `path-outside-root` alone (#104), as ever. It is not
  `path-not-relative` (an absolute path or a URI scheme, #104's). A backslash, a null byte or an empty
  path is not such a path, and stays `include-file-missing`, at the include, with a message that says it is
  not a readable relative path. (`amends.file` and attachment files outside the directory are still as if
  not asked for.) A document with no path, and no `resolve`, reads nothing: the Warning of §17.5 says so.
- **`resolve=` and the default loader.** A document loaded with `legaldown.load` (and so the CLI)
  now has its fragments read from beside it: a fragment that is missing is an Error on a file that
  was clean. `resolve=lambda path: None` says every file is missing, which `include-file-missing`
  reports; to validate a loaded document without reading its files, validate it without its path
  (`dataclasses.replace(document, path=None)`, or `parse(text)`).
- **Nested includes.** In a template an `{{include:}}` in a fragment or an attachment file is
  `template-fragment-invalid` (§15.3), in a sentence or a list item too. Outside a template §12.2
  allows it; it is **not followed** and nothing is reported of it, which is also why `include-cycle`
  is not implemented. A document that includes itself is not read as its own fragment.
- **The identifiers and the combined document.** `include-heading-skip` is **one pass with every
  include present**: a partial implementation of §16.11, which asks for the hierarchy under every
  combination of answers to the conditions on the includes of a template (§15.3). Each fragment's headings are placed after
  the heading of the section holding its include paragraph (before the first heading, for the
  preamble), and are reported where they are: a fragment's heading in its file, a heading of the
  document that follows a fragment's at its own line; the document's own `heading-skip` is not
  reported a second time. Conversely, a `heading-skip` of the document that a fragment read between
  the headings fills is dropped (`# One`, an include of `## Deep`, `### Three`): §4.1 judges the
  combined document (§12.2). That holds only when every include paragraph in the section before the
  heading was read and present for certain; with one missing, not LegalDown, judged as a path,
  unreadable, conditional (on its paragraph or a section around it), or reported as not a fragment
  (`include-has-frontmatter`, `include-has-h1`: it is not spliced), the plain per-document judgement
  stands. `include-anchor-duplicate` is not implemented. A reference into a
  fragment (`{{ref:}}` to its section) is still `ref-broken`: fragments are not joined to the
  document's identifiers.
- **What of a fragment is not reported by the document that includes it.** The fragment is not
  validated there, only checked for what the specification asks of a fragment. What
  `check_template_body` says of its body (`insertion-boundary`, `drafting-note-def`,
  `def-term-variable`, `drafting-note-unrecognized`) is left to the fragment's own validation
  (`validate(fragment=True)`), because `insertion-boundary`, `drafting-note-def` and `def-term-variable`
  are in the validator's template rules (`TEMPLATE_RULES`): reporting them through the including
  template would newly stop its assembly. `attachment-has-frontmatter` and `attachment-has-h1` are
  not reported for an attachment file that `validate(resolve=)` reads either (assembly refuses a
  template for them, with the file and line); they are reported for the file validated on its own
  with `fragment_kind="attachment"`.
- **One implementation.** The findings come from one module that assembly (`Template.problems`),
  `validate(resolve=)` and `validate(fragment=True)` share, so one file is never judged two ways.
  Assembly reports them as it always did, now one for each offender and with the file and line
  (`Diagnostic.file`, shown by `legaldown assemble` as `part.lgd:3`). `Template.problems` does not
  count the validator's `template-fragment-invalid` of a file assembly read a second time (its
  `d.file` is one of the files it read, or one it could not parse, which is in `problems` by its
  path with the error that stopped it); `Template.validation` keeps both, as advice. The
  other `include-*` ids are not template rules. Assembly and the validator read an include paragraph
  with the same function (`include_paragraph`). Assembly's own refusal of an `{{include:}}` in a
  fragment or attachment file of a document that is not a template (§12.2 allows it; it is not
  assembled) is an `include-file-missing` at the include's line, in the fragment's file, one for
  each include paragraph (an `{{include:}}` in a sentence, a list item or a quote is text, §12.2),
  quoting the directive as written.
- **Several documents, one fragment.** `legaldown validate a.lgd b.lgd`, where both include `part.lgd`,
  reports a finding in `part.lgd` once, in the output and in the counts: findings in a file other than
  the target's, with the same file (the same device and inode when it is there, so a link or a spelling in another case on a
  file system that ignores case is the file; otherwise the same absolute path), line, rule and message as another
  target's, are one, in any order of the arguments. A finding in a fragment does not name the fragment
  in its message (`Diagnostic.file` does), so it reads the same from every directory. A message that
  carries the including document's context is another diagnosis and is reported for each: an
  `include-heading-skip` "from level 1 to 4" in one document and "from level 2 to 4" in the other. A
  target's own findings are never collapsed. A file reached twice as a target (named twice, or by a
  directory and by itself, or by a link beside it) is validated once; a link to a document in another
  directory is a target of its own, as `load` reads the includes from beside the link.
- **An include that names no file.** An include paragraph `{{include:}}` is `include-file-missing`
  ("names no file") at its line when the validation can read files, and assembly refuses the template
  for it at the same line. In a template's fragment any `{{include:}}` is `template-fragment-invalid`,
  as one that names a file; in a plain fragment it is allowed (§12.2) and not followed, and assembly
  refuses it as it does any nested include.
- **Silence.** An attachment file that is not there is not reported (`attachment-file-missing` is
  not implemented); the Warning of §17.5 says that the existence of attachment files is not checked
  ([below](#constructs-beyond-the-level-175)).

## Constructs beyond the level (§17.5)

§17.5: an implementation that meets a construct whose processing lies beyond its claimed level "MUST NOT
ignore it silently": a validator emits a Warning identifying each check category it did not perform,
and does not report the document as passing checks it did not run
([#107](https://github.com/ForLegalAI/legaldown-validator/issues/107)). This implementation does so
with **`ldv-check-skipped`**, a Warning. The id is the implementation's own, which §16.1 allows
for a check of its own, namespaced with a prefix that ends in a hyphen (`ldv-`,
`legaldown.grammar.IMPLEMENTATION_RULE_PREFIX`; `SKIPPED_RULE` is the id), so no id the specification
assigns later can collide with it. It is not in the fixtures corpus (`coverage.json`) and no fixture
expects or forbids it. It is a Warning for every output, so `is_valid` and the exit status are
unchanged; `--strict` and `--warnings-as-errors` count it, and `--ignore ldv-check-skipped` removes it.

- **The trigger is a construct in the document**, never the conformance level: the specification's
  example is "translations declared; bilingual validation not performed", and §17.1 says no construct
  is ever mandatory in a document. A document without the constructs below draws none. A construct
  that is text (an `{{include:}}` in code, a comment, a sentence, a list item or a drafting note; an
  image in a code span) is no construct.
- **One Warning for each category, for each validation**, at the line of the first construct of
  the category (the first include paragraph; the `file` of the first attachment; the `file` of
  `amends`; the `translations` key; the `file` of `supersedes`, or else the first image). A
  fragment validated on its own (`validate(fragment=True)`) draws it too, for its own includes
  and images; it is not `host_dependent`.
- **Strict.** The Warning is made whenever the construct is present, also when the files were
  read, since some of what the category asks is not done in any case. Each message lists exactly
  what was *not* performed in this validation: a check that was made (a fragment that was read) is
  not listed.

| Category (§17.4) | Construct | Not performed |
|---|---|---|
| Includes (§12, §16.11) | an include paragraph | always: `include-cycle` and `include-anchor-duplicate` (not implemented), and the **combined-document checks** (§16.11: a reference or a term into a fragment, `insertion-boundary` and the identifiers a fragment and the document share are judged without the fragments joined to the document; [#117](https://github.com/ForLegalAI/legaldown-validator/issues/117)). For the includes not read: `include-file-missing` (no loader, or a path that leads out of the document's directory, §2.3), `include-has-frontmatter`, `include-has-h1`, `include-heading-skip`, and in a template the part of `template-fragment-invalid` that reads a fragment. In a template with a conditional include: `include-heading-skip` under every combination of answers (it is judged with every include present) |
| Attachments (§12.4, §16.10) | `attachments[].file` | `attachment-file-missing`; for a LegalDown attachment file also `attachment-has-frontmatter`, `attachment-has-h1` and `attachment-anchor-duplicate`, and, with no loader, importing its definitions |
| Amendment (§16.8) | `amends.file` | `amends-file-missing`; when the original was not read, `amend-term-undefined` (a term the amendment does not define is then `amend-term-unresolvable`, Info, as §7.5 says) |
| Bilingual (§14, §16.7) | a non-empty `translations` | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-template-mismatch` |
| Declared paths (§17.4) | `supersedes.file` (the object form), an image path | `supersedes-file-missing`; the existence of image paths (§8.7), which has no rule id |

- **Not counted as unread.** An include whose path is already an Error (`path-not-relative`,
  `path-outside-root`) or is not a LegalDown file (`include-not-legaldown`) is not an include that
  was left unread: the finding is its own. An image path that is already `path-not-relative` or
  `path-outside-root` is not an image to check either. An include that a loader cannot find is
  `include-file-missing`, and the Warning counts its fragment as unread, since its content was not
  checked. An include of the document itself is not read (`include-cycle` is not implemented).
- **Core rules are not named here.** The Core rules this implementation lacks
  (`translation-implicit-id`, `translation-authoritative-absent`, `language-code-invalid`,
  `authoritative-not-declared`, `definition-used-before-declaration`) are gaps in Core, not
  constructs beyond it, and are listed in the table above.
- **Rendering** (§17.3) is a category of its own in §17.5's sense only for a `{{ref:}}` to an item
  or paragraph anchor (`ref-not-enumerated`); it draws no Warning, and is listed above.
- Assembly claims no check, so it adds nothing; `Template.validation` carries the Warning as advice, and
  `Template.problems` does not (it is not a template rule).

## Checking this yourself

The conformance harness runs against a checkout of the specification repository:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Each case's expected rule is asserted at its expected line, where the case gives one (above).
Cases for the rules above are skipped by name, so the 19 `not implemented` skips reproduce this
table one for one, except `ref-not-enumerated`, which has no fixture. Each fixture document is
also written back (`serialize`) and validated again, which must report the same diagnostics. The
run reports 244 passed and 29 skipped (238 and 35 before #35: the five include cases and
`template-fragment-invalid`, six, now run, and the 19 are the 24 less the five include rules, which
the table no longer lists): seven of the other skips are the implemented rules named
above, skipped as `multi-file case` or `requires conformance level full`, two are the `multi-file`
assembly case, which is marked Full (fixtures README, step 4) — `tests/test_assembly.py`
assembles it with a loader instead — and one is the write-back of the `frontmatter-invalid-yaml`
case, whose frontmatter cannot be read. Cases that need the final option run with it; cases that need an answers set
are assembled with it, and each assembly case is compared byte for byte with its expected output.
CI runs this on demand (Actions → CI → Run workflow with the `conformance` input on, or the `ci` label on a pull request).

## Assembly (§15.7, §17.6)

`Template.form(answers).assemble()` and `legaldown assemble` perform §15.7.2 byte for byte and report
the answer rules of §16.12 (`answer-invalid`, `answer-missing`, `answer-unknown`). The block
structure assembly edits is recorded by the parser's own walk, so assembly and validation read a
template the same way. `Template.questions` lists the questions a template asks; `Form.questions`
those it reaches given an answers set, and `Form.unanswered` those still left open. §15.7.2 gives assembly a template that
validates without Errors. Assembly does not require the whole of that, but a `Template` refuses a
template with Errors in the validator's template rules (`Template.problems`); the deprecated
`assemble` function does not.

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
  attachment file may hold conditions of its own (§15.3). These are the validator's own findings
  ([Include fragments](#include-fragments-122-1611-1612)), one for each offender, each with the file
  and the line in it (`Diagnostic.file`, `legaldown assemble` prints `part.lgd:3`). An
  `{{include:}}` in a fragment or attachment file of a document that is not a template is not
  assembled: it is refused with `include-file-missing`. The other Full checks — `include-cycle`, the
  `*-anchor-duplicate` rules, `include-heading-skip` (which the validator reports) and the path rules
  — are not made by assembly.
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
