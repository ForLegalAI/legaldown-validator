# Conformance

`legaldown-validator` implements **Level 1 — Core** of the LegalDown specification 0.2 (§17.2):
parse and validate a single document in memory. It also claims the **Assembly** capability
(§17.6): a template and an answers set in, the assembled document out (§15.7).

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — one case per
validation rule, paired with the diagnostic a conforming validator must produce. **101 of the
corpus's 113 rules are implemented, and every case the harness runs passes**, at the line each
case gives and, for a finding in a file the document includes, in that file.

Every diagnostic names its file and line (§16.9, [#27](https://github.com/ForLegalAI/legaldown-validator/issues/27)):
the file is the document's, or the include fragment it was found in ([#35](https://github.com/ForLegalAI/legaldown-validator/issues/35));
the line of the directive, marker, heading or block it is about, or of the frontmatter node — a
mapping key, or a list entry's `-` line, at whatever depth — and for a missing key, the key that
holds it (`amends:` for a missing `amends.title`) or the frontmatter's first key. Three cases are
not asserted at their given line (for the last two the harness asserts the line this implementation
reports instead, `_DISPUTED_LINES`, so a regression is still caught):

- The answer rules (`answer-invalid`, `answer-unknown`) point into the answers file, which
  assembly receives already parsed (`Template.form(answers)`), so its diagnostics name no line.
- `party-name-duplicate` gives line 10, the second side; this implementation reports line 12,
  the duplicate party's own entry, as `side-name-duplicate` and `representative-name-empty`
  point at theirs ([ForLegalAI/LegalDown#42](https://github.com/ForLegalAI/LegalDown/issues/42)).
- `attachment-id-duplicate` gives line 8, the second attachment's `title`; this implementation
  reports line 7, the entry's `- id:` where the duplicate id is written, as `attachment-id-collision`
  gives its entry's (line 4; the same issue).

One case is not run against its entry, for a reason of the specification's own (§17.2):
`translation-implicit-id` names `doc-en.lgd`, the primary (`language: en`, `authoritative: en`), as
its entry, and expects its finding in `doc-fr.lgd`. The row is Core, and Core is "not required to
open any file other than the document itself" (§17.2); it asks every heading and `{{def:}}` to carry
an explicit identifier "when the document itself is a translation". A Core validator reports the
row only when the document it validates is itself a translation, so given the primary it correctly
reports nothing (and the Warning of §17.5 for the linked file it did not open). The harness
therefore validates `doc-fr.lgd` for this case (`_DISPUTED_ENTRY`), through the same assertions, file
and line included: the entry should be `doc-fr.lgd`, which the primary does not need. Nothing is
changed in the specification repository. The other bilingual case that is Core,
`translation-authoritative-absent`, names its entry, `doc-en.lgd`, and runs as it is.

The specification defines 116 rules. The corpus has no fixture for three of them, since a
document alone cannot exercise them. This implementation covers two of the three,
`anchor-autogen-collision` and `legaldown-version-newer`, with unit tests. The third,
`ref-not-enumerated`, depends on the style template and is listed below.

Three implemented rules — `amend-def-override`, `amend-term-undefined` and
`amend-term-unresolvable` — have fixtures that are marked for the Full level and name the
original they amend, so this single-document harness skips them. They are covered by the unit
tests in `tests/test_spec_alignment.py` and `tests/test_templates.py` instead. A case with several
files is run all the same when every diagnostic it expects is in its entry file, as the other files
are only there (`attachment-id-collision`, `attachment-id-duplicate`, `attachment-title-empty`,
`attachment-unreferenced`, `translation-authoritative-absent`); it is skipped as a `multi-file case`
only when an expected diagnostic names another file.

The fixtures of seven rules that read an include fragment — `include-anchor-duplicate`,
`include-cycle`, `include-file-missing`, `include-has-frontmatter`, `include-has-h1`,
`include-heading-skip` and `template-fragment-invalid` — are marked Full and span files, and are run all the same: with
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
| Bilingual sets (Full, §17.4) | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-template-mismatch`; and `translation-implicit-id` and `translation-authoritative-absent` for the linked files (for the document itself they are Core, and implemented, below) |
| Rendering (§17.3) | `ref-not-enumerated` |

In practice this means validating a set of files is out of scope: the validator does not
cross-check attachment file contents or bilingual document sets, nor, beyond the section identifiers and the include chain (below), the combined
document of a document and its fragments ([#117](https://github.com/ForLegalAI/legaldown-validator/issues/117)). What it reads from another file is the definitions the
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
  exact answer needs the answers to every question tried together. A reference is tested only
  against the alternatives that name a question it names (an alternative that can never appear
  is not reached at all), and **at most 4,000,000 pairs are tested in a document**
  (`legaldown.validator.circular.MAX_EXCLUSION_TESTS`; the cap is this implementation's, the
  specification has none). Past it a reference reaches every alternative of the id it names, as in
  a document that is no template. That can only join circles, so no circular definition goes
  unreported, but circles may merge into fewer Warnings, at other lines. The cap needs thousands of
  alternatives of one id under one question, named from as many places under it.

`definition-used-before-declaration` (§16.3, Info, [#116](https://github.com/ForLegalAI/legaldown-validator/issues/116))
is named by the specification and given no more: §7.2 lets a `{{def:}}` "appear anywhere", and a
`{{term:}}` targets "the `{{def:}}` anchor" (§7.3). A use is *before* a declaration when it comes first in
the document's own text, in the order it is written: the preamble, then the sections; within a block, its
text in order (a table by rows, a list by items, the paragraph around a `{{def:}}` by offset). **One Info is
given for each definition**, at its first earlier use, with the number of earlier uses and the line of the
declaration in the message (`Definition 'services' is used 3 times before its declaration at line 25
(§7.2).`), not one for each use: a document that keeps its Definitions article last (a common layout) would
otherwise draw one for every use of every term. The fixture has it at the use, line 21. Not counted: a
`{{term:}}` in a drafting note (assembly removes it), in code or in a comment; one in a heading (§4.2 has no
directive there); a use in the definition's own quoted term (`"Self {{term: s}}" {{def: s}}`), which is
`definition-circular`'s; an id the document does not declare (`term-undefined`); an id the amended original
declares, which is in force before the document (§7.5). A `{{def:}}` in a drafting note is no declaration either ([below](#definitions-in-drafting-notes-156-125)). A use is judged against the first declaration of its id that can appear with it (in a
template, one that can appear and is not excluded from the use's presence, §15.4): a use between two alternatives
is judged against the one it resolves to, a declaration that can never appear is no declaration, and a use that can
never appear is no use. The pairs of presences tested are capped, within a budget of their own (`MAX_EXCLUSION_TESTS`, see Limits). Past it a use whose first compatible declaration cannot be told apart is left unjudged, so an Info can be missed but is never false. The files the document includes and
attaches are not expanded, so a definition that only they declare is no declaration here (as
`term-undefined` reads it). The auto-linking of §7.4 is not implemented, so a term written without
`{{term:}}` is no use. `legaldown validate --strict` counts the Info, so a document that keeps its
definitions at the end fails it; `--ignore definition-used-before-declaration` removes it.

`language-code-invalid` and `authoritative-not-declared` (§16.6, Warnings, #116) check the language
fields. The specification asks for "valid ISO 639-1 codes" and gives no list; this implementation embeds the 184
codes of the registry (`legaldown.grammar.ISO_639_1_CODES`, beside `KNOWN_CURRENCIES`), in lowercase as the
registry has them: `EN`, `en-US` and `eng` draw the Warning once, at the line of the value, which says
what to write (a `translations` key is also the path's lookup key, and `authoritative` is compared with the
keys exactly). The check covers `language`, a non-empty `authoritative`, and each `translations` key (found by the
key as stripped, at the line where it is written, padded; a `translations` entry whose key or value is null, empty or blank once stripped is ignored when the frontmatter is read, and of keys that are the same once stripped the last stays, as `ldv-frontmatter-key-duplicate` below reports); a value holding a `{{placeholder:}}` is
`placeholder-in-structural-field`'s alone. `authoritative` must equal the document's `language` or a
`translations` key. §3.2 gives `language` no default, so the model holds none (`Metadata.language` is `""` for a
document that says none, an empty `language:` included, and `empty_document()` says `"en"`), and
`authoritative: en` and `authoritative: fr` without a `language` are treated alike: the message says the language is not
declared. `serialize` writes `language` only when the model holds one, so writing a document back does not say a
language it never did, and the diagnostics are the same with or without a source map, after `to_dict`/`from_dict`, and after
writing it back. **A value that is no code is
no language**, so the checks that compare languages judge only the codes that passed (the rule of §16.6, that a value that fails one
format check is exempt from the checks that assume it): `language: english` with `authoritative: en` draws
`language-code-invalid` alone, and so does a `translations` key that might have been the authoritative.

**`ldv-frontmatter-key-duplicate`** (#126, #131) reports a key of a frontmatter mapping that a later key of the
same mapping replaces, at its own line, so that it does not go silently. The id is this implementation's own (§16.1,
`ldv-`; `legaldown.grammar.FRONTMATTER_KEY_DUPLICATE_RULE`): the specification has no rule for it. The YAML reader
keeps the last of the keys a mapping holds as one (`title:` twice, a party's `name:` twice) and the model's dict does
the same, so the earlier key and its value are dropped. Every mapping of the frontmatter is checked: the root, the
mappings in a sequence (`sides[0].parties[1]`), the fields of a question (`questions.my-q.default`) and the mappings
a `<<` merges in. The finding names the mapping, `frontmatter` for the root and otherwise its path in backticks:
names as `parties[0]` and `questions.my-q.default`, a key that is no plain name between brackets and escaped as
JSON (`["weird key"]`), a typed key of a question's `default` as Python writes it between braces (`{1}`, so that it
is not taken for a position), a sequence entry by its position in the YAML sequence (`[1]`), and a merged mapping as
`x.<<` (`x.<<[0]` in a list of sources).

`translations` and `field_types` are judged apart, with their own wording, as the model reads their keys as text,
stripped, and keeps the last of the keys it reads as one (in the order the model holds them: `fr: a`, `' fr': b`,
`fr: c` keeps the entry at the second line, not the last written). Each entry that does not stay is reported at its
own line, and the finding names the line of the one that does (for a key written again, the entry that repeats it,
in the same key as read); when the model keeps none, because the last entry written has an empty or blank key or a
null, empty or blank value, the finding says which. (In the other mappings, a key written again names the line of
the key that repeats it, and a key read as the same names the line of the one the model holds.) The keys are shown as they are written in the source (quotes and escapes
included, between backticks), so that `1` and `"1"` differ; an aliased key (`&k fr`, then `*k`) is one node for the
YAML reader, which keeps no position for the alias, so it is at its anchor's line, and the finding does not name that
line again. Three cases:

- **The same YAML key written twice** (`fr` and `fr`, or `fr` and `"fr"`): invalid YAML, which a strict loader
  fails as `frontmatter-invalid-yaml`, and this implementation's YAML reader, like PyYAML, reads as the last.
  An Error. In the mappings other than those two it is judged by the type YAML gives the keys that the model holds
  as one: both text, both a boolean (`yes` and `true` in a `default`), both null (`~` and `null`), both an integer
  (`1` and `01`) are the same key, however written.
- **Keys that YAML keeps apart but the model reads as the same text** (`1` and `'1'`: the model reads every
  plain scalar as written), "once read as text". In a question's `default` the YAML types stay (§15.7.1), so `1`
  and `'1'` are two keys there, and `1`, `1.0` and `true`, which Python holds as one though YAML types them
  differently, "once read as a YAML value". A plain `=` is a YAML type of its own that the reader turns into text, so
  `=` and `'='` are "once read as text".
- **Keys that strip alike** (`fr` and `' fr'`), "once stripped": the reader strips the keys of `translations` and
  `field_types` ([#116](https://github.com/ForLegalAI/legaldown-validator/issues/116)).

The last two are a **Warning**, in every mapping: the specification does not forbid them (it judges a padded language
code a Warning, `language-code-invalid`, and says nothing of a padded `field_types` key, which no other rule sees
as the reader strips it), and a rule of this implementation's own does not fail a document that the specification
accepts. The first is an Error. A `translations` entry that is blank once stripped is skipped before it can replace
a sibling (#116), so it draws nothing; a blank `field_types` description is a declaration, and does. Keys that YAML
resolves equal but that are written differently (`~` and `null`, `1` and `01`, `true` and `True`) are two text
keys the model keeps both of, and draw nothing outside a `default`.

How the rule reads the YAML:

- **A key merged into a mapping (`<<`) is not an entry written there.** A key written over a merged one replaces
  it, as the YAML specification says, and the same key from two merged mappings is a matter of precedence (the
  first source wins): neither is a repeat. A merged mapping is judged as a mapping of its own, so a key repeated
  in a source is reported at the source (`x.<<`), and so is one in a source of `translations` or `field_types`
  (`translations.<<`), whose own entries stay with the check above. Only the entries of a source that reach the
  mapping are judged: one that the mapping's own key, or an earlier source, takes the place of is never read, and
  neither is its value. A `<<` written twice in one mapping is not reported: the reader merges both, and the model
  drops nothing.
- **A dropped entry is ignored whole:** a repeat inside the value of a key that a later one replaces is not
  reported, as the model never holds it.
- **A mapping shared through an alias is reported once**, under the first path that reaches it, at the line where it
  is written, whereas the check of `translations` and `field_types` judges it under each map that holds it. A key
  node shared by two mappings (`{&k a: 1, *k : 2}`) is reported in each, at the anchor's line. An aliased key is
  at its anchor's line even when the anchor is written in another mapping: the YAML reader gives an alias no position
  of its own.
- **`translations` and `field_types` written twice at the root** are a repeat of the root mapping: the one dropped
  is reported as `frontmatter`, and the one read, by the check above.
- **The findings describe the source text.** A document whose metadata was changed after it was parsed draws none
  of them (the parser keeps a digest of the metadata it built, which the validator compares), as one built from a dict
  has no source map.
- **Frontmatter that cannot be read** is `frontmatter-invalid-yaml` alone. A key that cannot be held in a dict (a
  mapping or a sequence as a key) fails the document before this check.

The keys of these maps are found at the line where they are written, padded or not, by the checks that report on
one (`language-code-invalid`, `path-not-relative`, `placeholder-in-structural-field`, and the checks of a `field_types`
key, which judge it stripped), and the path of the entry that stays is the one judged.
A document built from a dict has no source map, so it draws no finding; neither does a document whose body
was changed after it was parsed, nor an entry for a key that the model no longer holds (or holds again after it
did not): the findings are judged against the model. A fragment (`fragment=True`) has no metadata to check.

`translation-implicit-id` (§16.7, Error) and `translation-authoritative-absent` (§16.7, Warning) are the
single-file translation rows that §17.2 makes Core: the identifiers of the document itself. **A file is a
translation** when `translations` is non-empty and `language` and `authoritative` are both declared,
both valid codes, and different, and `authoritative` is one of the `translations` keys, a language of the
group: §16.7 is about linked files, so a document that links nothing is
no translation (`authoritative-not-declared` is the finding for `language: fr` with `authoritative: en`
and no `translations`), a code that is no code is not compared, and a typo in `authoritative`
(`de` for `en`) is `authoritative-not-declared` alone, not an Error on every heading of what may be the primary.
`authoritative-not-declared` judges only valid codes, so it is silent when a `translations` key is no code (the key may be
what was meant), and then the absence of `authoritative` from the keys is not held against the document: `language: de`,
`authoritative: cs`, `translations: {Fr: …}` draws `language-code-invalid` for the key and, for each heading
and `{{def:}}` without an identifier, `translation-implicit-id`; with every key valid it draws
`authoritative-not-declared` alone. For each heading and each `{{def:}}`
without an explicit identifier a translation draws an Error (identifiers originate in the primary and are mirrored
explicitly, §14.2), and a document that declares `translations` without `authoritative` a Warning (the primary
cannot be told from its translations, so the linked files are checked symmetrically): **one for each
node**, at its own line, like `anchor-lossy-slug` and `def-lossy-slug`; the message of the Warning says that one line,
`authoritative:`, ends all of them. The two are exclusive. An identifier that is written but invalid is `anchor-format`,
not implicit; a duplicate generated identifier draws the Error at each declaration besides `def-duplicate-id`; a heading in a list
item or a quote is a block, not a section; a `{{def:}}` in a drafting note is not counted ([below](#definitions-in-drafting-notes-156-125)). The identifiers of the *linked* files are Full (§17.4) and are
named in the Warning of §17.5 as not read ([below](#constructs-beyond-the-level-175)).

`raw-html` (§8.7) warns about raw HTML other than comments, which renderers leave out: once for
each HTML block that holds more than comments (text after a comment's `-->` on its line
included), in list items and quotes too, and once for each text — a paragraph, a table cell, a
heading — holding an inline tag, processing instruction, declaration or CDATA section, as
CommonMark 0.31.2 §6.6 reads them (a declaration is `<!`, an ASCII letter, anything but `>`, and `>`).
Comments, autolinks, code spans, escaped `<`, a link's `<…>` destination
and a directive's values are not raw HTML; frontmatter values are not Markdown. Comments and code
spans are found by the directive lexer (§11.4), which reads comments by CommonMark 0.31 and knows
no tags, so a few rare texts are read differently from cmark-gfm: a comment holding `--`, text
after an unclosed `<!--` (where cmark-gfm finds no more inline HTML), and a backtick or `<!--`
inside a CDATA section, declaration or processing instruction. Code spans and comments are blanked first and
no HTML runs across them, so a declaration, processing instruction or CDATA section whose closer is inside one
is not found, where CommonMark, pandoc and markdown-it let the HTML that starts first win (`A <!a `>` b.` and
`A <!a <!-- x --> b.` hold a declaration ending in the code span and in the comment): a missed `raw-html` Warning.

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
  `../shared/definitions.lgd` is valid (though not read, below). With one, `normpath(directory of the document / path)` must be at
  or below the root, by names alone, with `/` and `\` both separating names (`a/../b` stays, `a/../../b`
  leaves). The document and the root are made absolute first, so a relative `path` and `.` compare; a document
  from `parse` has no path and is taken to be at the root, so any leading `..` leaves it. A loaded document
  below the root may climb up to it. A document outside the root cannot name a file within it: that is
  reported once, as `path-outside-root` at line 1 saying so, not once for each path, and only when it
  names a relative path at all.
- **Reading under the root** ([#109](https://github.com/ForLegalAI/legaldown-validator/issues/109)). §2.3
  says a document "must never be able to read files outside the tree it belongs to", and that without a
  root the containment check does not run; it says nothing of reading. So this implementation reads a
  file a document names only when a root is configured and the file is within it:
  - **With a root**, a path whose leading `../` run stays within it (`../shared/x.lgd` from a document
    one directory below the root) is read, like any other: for an include, an attachment file (its
    definitions) and `amends.file` alike, by `validate`, by assembly (`load_template(path,
    document_root=)`, `parse_template(text, document_root=, path=)`, `legaldown assemble --document-root`)
    and by the CLI. A custom `resolve=` is asked for it too, as `../shared/x.lgd` (a change for loaders that
    never saw a `../`). Containment is tested against the root only: nothing outside it is read, however
    the path is written, and nothing a document outside the root names (`path-outside-root` reports the
    document once; its includes, attachment files and `amends.file`, even beside it, are not read). The
    default loader (`file_loader(directory, root=)`) enforces the same on the file itself: it follows symbolic
    links and refuses a file whose real path is outside the root. `files.relative_path(path, depth=)`
    takes how many names the document is below the root (`names_below`). The names of the rules for what
    stays are unchanged: a backslash, a null byte, an absolute path, a URI scheme, `..` or `.` alone are
    refused as ever. A document under a symbolic link to elsewhere is below the root by its names (the
    check is lexical) and reads nothing outside the root, so a fragment it names is `include-file-missing`.
  - **Without a root**, `../x` is valid and **not read**: no finding, a custom `resolve=` is not asked, and
    the Warning of §17.5 says so (a document one does not control may not read what it names). The
    deprecated `assemble()` is unchanged: it asks for no `../`. `-o` still refuses an output that leads
    out of its directory, so a kept file read from `../` is assembled in memory (`AssemblyResult.files` keeps
    its `../shared/x.lgd`) but the CLI cannot write it.
  - `Diagnostic.file` of a finding in such a file is its path from the document's directory, `../shared/x.lgd`;
    the CLI prints it without the detour.
  Assembly does not report `path-not-relative` or `path-outside-root`.
- An image is the inline `![alt](destination)` (§8.7), in a paragraph, list item, quote, table cell, heading
  or definition, and in the alt text of another image (both are checked). The destination is `<…>` or a
  raw one with balanced parentheses, then an optional title (read as a definition's is, but permissively, below); backslash escapes and character references in
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
  `text`; also in a list in a quote, a quote in a quote or in an item: `tests/test_link_definitions.py` pins the
  seven shapes). Outside containers the paragraph is as CommonMark has it.
- At `MAX_LIST_DEPTH` an item is one paragraph, and `[x]: u` there is text: the serializer writes it with a
  backslash, as it does `# h`. The same holds for a quote deeper than `MAX_QUOTE_DEPTH`.
- A paragraph of a model that reads as a definition (`[x]: u`) is written `\[x]: u`, so it stays a paragraph; one
  that would be a block alone only because definitions were peeled off before it (`<span>`, `2. x`) is written
  directly under them, as the parser read it.
- A label longer than 999 characters, built in code, is written and read back as paragraph text; a blank line in
  a label or a title is a space (it would end the paragraph), a line break in a target is dropped, and an
  unescaped bracket in a label, a `<` or `>` in a destination written in `<>`, or a title delimiter in a title is
  escaped when written.
- A bare destination is one with no space, control character or DEL, balanced parentheses (at most 32 deep) and not
  beginning with `<`; one that is not is written in `<>`, and so is one that holds a NUL (text to the reader, below, but written
  in `<>` as ever).
- **One scanner for the destination and the title of a link** ([#113](https://github.com/ForLegalAI/legaldown-validator/issues/113)):
  the definitions parser, the image paths (`path-not-relative`, `path-outside-root`) and the `raw-html` check
  (a link's `<…>` destination is no raw HTML) read them with the same functions (`scan_destination`,
  `scan_title`, `scan_inline_link`), by CommonMark 0.31.2 §4.7 and §6.3 and as markdown-it reads them
  (`tests/test_destination_oracles.py` compares an inline link with it over generated text). The image paths read
  them **permissively**, below:
  - `<…>` holds no `<`, `>` or line break but escaped; a bare destination ends at a space, a control character
    (U+0000–U+001F, but for NUL: below) or DEL (U+007F, §2.1 counts it a control character), or at a `)` that
    closes no `(` of it, and holds at most 32 deep.
  - **A NUL is text** (§2.3 makes it U+FFFD): it no longer ends a bare destination in a definition
    (`[a]: u\0v` is a definition). pandoc and markdown-it read it so. It is also how a directive is
    masked in a destination of an image.
  - **A title is told apart by the space before it**, and the parts of an inline link are separated by spaces,
    tabs and up to one line break (§6.3): `[a](<b>"t")` is no link (to `raw-html`: the `<b>` is a tag), `[a](<b>\n"t")` is one, and
    two line breaks end the paragraph, so `[a](\n\n<b>)` is none. A line break may now precede the `<…>` of a link
    or the `)` that ends it (`raw-html` no longer reads `[a](\n<b>)` as a tag), a title may hold `\"` or `\)`,
    and a title directly after `<…>` makes the `<b>` a tag, as it is the text of no link.
  - **The path checks read permissively and take the union of the readings** (`scan_inline_link(permissive=True)`,
    for `path-not-relative` and `path-outside-root`): a rule that keeps a document from naming a file outside its
    root must cover everything *any* supported renderer reads as an image, as the insertion boundary
    over-approximates (§15.7.3). So an image is also `![b](/etc/passwd\x7f)` (a DEL or other control character in a
    bare destination, which then ends only at a space, a tab or an LF) and `![a](</etc/passwd>"t")` (a title glued to
    `<…>`), which pandoc renders (below) and the specification and markdown-it do not. The link says whether the
    specification reads it too (`InlineLink.strict`, from the same pass): if it does not, the offset is also a
    reference-style image to the specification (`![r](x\x7f)` is `![r]` and text), and the definition `![r]` names
    is judged too.
    Two gaps remain, each a missed `path-not-relative` or `path-outside-root`, both
    [#121](https://github.com/ForLegalAI/legaldown-validator/issues/121): a definition only pandoc reads
    (`[r]: /etc/passwd\x7f`, a control character or DEL in its bare destination, which is paragraph text to the model,
    the specification and markdown-it) is not judged when `![a][r]` names it; and markdown-it reads a backslash before
    *any* character as an escape pair, so `![a](/etc/passwd\ "t)` (a space after the backslash) is an image to it
    alone, and is not judged either.
  - **pandoc (`commonmark`) deviates in two places, accepted**: it reads `[a](<b>"t")` as a link (a title glued
    to the `>`), and it keeps a control character or DEL in a bare destination (`[a](b\x7fc)`, `[a](b\x01c)`), where
    the specification (§6.3 "does not include ASCII control characters") and markdown-it read no link. This package
    follows the specification, but for the path checks. (`test_pandoc_reads_a_link_where_the_specification_and_markdown_it_read_none`
    holds them to what pandoc does.) markdown-it reads a backslash
    before *any* character as a pair, not only before punctuation, and breaks a paragraph at a `>` line; these
    are its own.
  - The `insertion-boundary` rule for a link reference definition (§15.7.3) is not this reading: it flags an
    insertion where a label, a destination or a title *could* be once it is filled in, over a destination of any
    non-blank characters or a `<…>` (which may hold blanks) and a title with no escapes, and an unterminated
    `](x {{p}}` still counts. It also flags an insertion (`{{placeholder:}}`, `{{choose:}}`, no other directive)
    in the title's place, after the destination and its blanks (`[a]: u {{placeholder: p}}`,
    `[a]: <x y> {{placeholder: p}}`). The longer of the readings of the definition wins, so no verdict that the
    bare reading drew is lost (`_InsertionScan._reference_end`, checked against regular expressions at every
    offset), in linear time. An ATX heading and a table cell hold no definition: there the rule reads a
    definition as it did before #119 (a bare destination and a closed title), so `# [a]: {{placeholder: p}}`
    and the same in a table cell are still flagged, and `# [a]: u {{placeholder: p}}` is not: verdicts
    that existed before #119, kept as they were. (A setext heading's text can hold one: CommonMark takes the
    definitions out of a paragraph before it reads the underline.) The rule is an over-approximation, per line
    as §15.7.3 is: a line that continues a paragraph, which is no definition (`Text`, then
    `[a]: u {{placeholder: p}}`), is read like any other and is flagged. The one place it is not per line is
    a setext heading's text that spans more than one line: the parser joins its lines with spaces, so an
    insertion anywhere in it may go unseen (#129: `[a]: u {{placeholder: p}}`, `Text`, `===`; `Text`,
    `[a]: u {{placeholder: p}}`, `===`; `- [a]: u {{placeholder: p}}`, `  Text`, `  ===`;
    `[a]: u`, `[b]: <x {{placeholder: p}} y>`, `more`, `===`). It still does not flag an insertion
    after a closed title (`[a]: u "t" {{choose: q}}`, a `{{choose:}}` phrase that is empty) or before one
    (`[a]: u {{choose: q}} "t"`), one that an answer could turn into a title with the quote after it
    (`[a]: u {{placeholder: p}}"`), one in a destination on the next line, one in a setext heading's text of
    more than one line (above), or one on a line with text after the definition (`[Note]: {{placeholder: p}} shall
    pay.`), the last being pinned
    as it was ([#129](https://github.com/ForLegalAI/legaldown-validator/issues/129)).
- The differential test (`tests/test_link_definitions_differential.py`) compares the definitions and the blocks
  with pandoc (`commonmark`) and markdown-it-py over generated documents. They disagree with each other in a few
  places: pandoc leaves a title or destination on a lazy continuation line (`1. [a]: /u`, then `  "t"`) out of the
  definition, which markdown-it and cmark's algorithm (the lazy line is paragraph text) keep in, and this package
  follows the latter; markdown-it differs from pandoc after a line holding only a tag, and where a setext
  underline stands under a lone label. The test asks that no definition here is neither pandoc's nor markdown-it's.
- **Where the shapes differ from pandoc or markdown-it** ([#111](https://github.com/ForLegalAI/legaldown-validator/issues/111)):
  over 1.5 million generated documents the block structure here differs from pandoc's in three classes, each
  deliberate or the reference's own deviation, none a link definition (a fourth, a nested quote ended early by a
  line that looks like a table's delimiter row, is a fault of this package: see the quotes, below):
  - *An empty ATX heading* (`#` alone, outside items and quotes) is paragraph text here, by design (above): a
    section's heading needs text. pandoc and markdown-it read an empty heading.
  - *A declaration with a lowercase letter* (`<!c`) is an HTML block, kind 4 of CommonMark 0.31.2 §4.6 (`<!`
    and an ASCII letter; 0.30 dropped the restriction to capitals, commonmark-spec #620). pandoc and
    markdown-it still read `<!c` as a paragraph. In text (§6.6, as above) markdown-it reads `<!c x>`, `<!Cx>`
    and `<!D>` as inline HTML, as this package does; pandoc reads `<!c x>` and `<!doctype x>` so but `<!Cx>`,
    `<!D>` and `<!c>`, with no whitespace after the letters, as text, as the rule was before 0.30.
  - *A degenerate end*: `<!-->`, `<!--->` and `<?>` end the HTML block on the line they start (the end condition
    is tested from the `<`, so it overlaps the start), where the next line is a paragraph. markdown-it reads the
    same; pandoc runs the block on to the next end marker.

What the model does not keep, or reads more simply than CommonMark:

- An ordered item's number is not kept: a list is written numbered from 1. Whether a list is
  loose or tight is not kept either: items are written without blank lines between them.
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
  late. A third, found by the differential of [#111](https://github.com/ForLegalAI/legaldown-validator/issues/111)
  (about one document in 200,000, and older than it): a nested quote whose paragraph has a line with a `|`,
  a lazy line, then a line that looks like a table's delimiter row (`>>a||`, `>     x`, `>>-|-`) ends there,
  where CommonMark (and pandoc, markdown-it) go on with the paragraph and take an unmarked line after it.
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

## Blanks in drafting notes (§15.6, §15.7.2)

A `{{placeholder:}}` or `{{choose:}}` in a drafting note is "recognized and validated" (§15.6, so that
a `{{ref:}}` in a note cannot go stale unnoticed), and the note is deleted before any blank is
filled (§15.7.2 step 2); a question "asked only inside a drafting note needs no answer" (step 1)
([#112](https://github.com/ForLegalAI/legaldown-validator/issues/112)). So an occurrence in a note
is checked by itself, and is no part of the blank:

| Check | An occurrence in a note |
|---|---|
| `placeholder-id-malformed`, `placeholder-type-invalid`, `placeholder-unknown-currency`, `duration-invalid-unit`, a directive's arguments | reported, at the occurrence |
| `placeholder-question-mismatch` (a written type that is not the declared one; the id of a decision question) | reported: it is the occurrence against the declaration (§15.2) |
| `placeholder-type-inconsistent` (types, currencies, units, §10.7) | not made against the blank: the note's type, currency and unit are not the blank's. Two notes do not conflict either |
| `question-invalid` for a default against the currency or unit the placeholders fix | the note's currency or unit fixes nothing |
| `question-unused` | a question only notes use (by a placeholder or a `{{choose:}}`) is unused, a Warning that says only drafting notes use it: it is no blank of the document and needs no answer, though assembly still lists it among the template's questions (below) |
| `placeholder-unfilled`, `template-construct-present` (`--final`) | both, as ever: a placeholder in a note remains in the document (§15.9), and so does the note |
| `index.blanks` | an id only notes use is not a blank; for one that is also in the body, type, `fixed` and `consistent` are the body's, and the order is that of the first occurrence outside a note |

Assembly reads a blank the same way (`Template.questions`, the answers' types and the currency
they must carry): the type and the currency or unit come from the occurrences outside notes
only. An id that only notes use stays a question that assembly knows, a `text` one (nothing
gives it a type), so an answer to it is not `answer-unknown` and no one is asked for it, which
is the one place `Template.questions` lists an id that `index.blanks` does not. `Template.questions`
lists first the declared questions that have a valid type, in the order of the declarations; then the
placeholder ids not among those, in the order of their first occurrence outside a note, and last the ids only
notes use, in the order the notes first use them. A declared question without a valid type
(`question-invalid`) is not placed with the declared ones: a placeholder that uses it places it, as a `text`
question, among the blanks; one that no placeholder uses is not listed. Before this a note
written before the body's placeholder could give a text blank the type `money`, and the answer
`five hundred` was `answer-invalid`.

## Definitions in drafting notes (§15.6, #125)

A `{{def:}}` "MUST NOT appear inside a drafting note, because assembly would remove the definition" (§15.6),
and assembly deletes every note, its whole extent, before anything else is resolved (§15.7.2 step 2). So a
definition in a note declares nothing: it is not registered (`collect_definitions` leaves it out, and so do
`index.definition_lookup`, `definition_lookup` and the definitions read from an amended original or an
attachment file), and `drafting-note-def` is the one finding about it
([#125](https://github.com/ForLegalAI/legaldown-validator/issues/125)). The directive is still "recognized and
validated" (§15.6), as to its form; what stops is everything about its identifier:

| Check | A `{{def:}}` in a note |
|---|---|
| `drafting-note-def` | reported, at the occurrence |
| `directive-malformed`, `def-no-quoted-span`, `def-emphasis`, `def-single-quote-ambiguous` | reported, at the occurrence: they are about the directive's form |
| `anchor-format`, `def-lossy-slug` | not made: the identifier is not registered, so a note's invalid or lossy identifier is not judged. It is when the definition is moved out of the note |
| `def-duplicate-id`, `def-autogen-collision` | not made, in either order: a note's definition neither is a duplicate of a definition outside notes nor makes it one, and a definition outside notes keeps the identifier |
| `def-unreferenced` | not made on a note's definition, and it no longer hides the Warning of the definition outside notes that has its identifier |
| `amend-def-override`, `amend-term-undefined` | a note's definition overrides nothing in the amended original, and one in the original's own note is not imported |
| `translation-implicit-id`, `translation-authoritative-absent` | a note's definition without an identifier is not a missing one |
| `condition-reference-unsafe`, `definition-circular`, `definition-used-before-declaration` | a note's definition is no declaration, in a conditional alternative or not |
| `term-undefined` | a `{{term:}}` that only a note's definition declares is undefined, at the use |

A `{{term:}}`, in a note or outside one, resolves against the definitions outside notes only, so a note that
both defines and uses its term draws `term-undefined` beside `drafting-note-def`: §15.4 exempts a reference
in a note from resolving "in every assembled document", but it is still checked against the template as
written, and a note's `{{def:}}` is not part of it. A `{{term:}}` in a note still counts as a use for
`def-unreferenced`, as the validator counts one in a conditional section that may be removed (a Warning, §16.4);
it does not for `definition-circular` and `definition-used-before-declaration` (above). A `{{ref:}}` to the
identifier of a definition is `ref-broken` either way: a definition is not an anchor (§5.6).

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
| `include-file-missing` | at the include (once for each include) of a fragment the loader does not find; for an include in a fragment, at its line in the fragment |
| `include-cycle` | at the include that closes a circular chain, in the file that holds it (`part-b.lgd:3` for main to a to b to a), once for each such include; a document that includes itself, or a fragment that includes the document, is one |
| `include-has-frontmatter` | at the first key of the fragment's frontmatter, or line 1 for an empty block; for a block that cannot be read, at the line of the problem (#43) |
| `include-has-h1` | at each level-1 heading of the fragment |
| `include-heading-skip` | at the heading that skips a level once the fragments are spliced in |
| `include-anchor-duplicate` | at the later declaration of an explicit section identifier that the combined document declares twice: a fragment's heading in its file, a heading of the document at its own line |
| `template-fragment-invalid` | in a template, at each condition, drafting note, `{{include:}}` or heading without an identifier in a fragment, and at each `{{include:}}` in a LegalDown attachment file |

Decisions the specification leaves open:

- **A path already judged is judged once.** An include path that file reference checks reported
  (`path-not-relative`, an absolute path or one with a URI scheme; `path-outside-root`, given a
  `document_root`) draws that finding alone: it is not also `include-file-missing` or
  `include-not-legaldown`. A path that leads out of the document's directory (`../x.lgd`) is valid as a
  path (§2.3). Given a `document_root` that holds it, it is read like any other include (`../shared/x.lgd`,
  [#109](https://github.com/ForLegalAI/legaldown-validator/issues/109)); with none, a `LoadFile` is never asked for
  it (`legaldown.files.relative_path`), as for one that holds a backslash, so it is skipped, never
  `include-file-missing` (it would break a document whose `../shared/x.lgd` exists), and assembly refuses a
  template for it as for a file that is not there. The skip is said once, by the Warning of §17.5 below. Given
  a root it leaves, it is `path-outside-root` alone (#104), as ever. It is not
  `path-not-relative` (an absolute path or a URI scheme, #104's). A backslash, a null byte or an empty
  path is not such a path, and stays `include-file-missing`, at the include, with a message that says it is
  not a readable relative path. (`amends.file` and attachment files are read or skipped by the same rule.)
  A document with no path, and no `resolve`, reads nothing: the Warning of §17.5 says so.
- **`resolve=` and the default loader.** A document loaded with `legaldown.load` (and so the CLI)
  now has its fragments read from beside it: a fragment that is missing is an Error on a file that
  was clean. `resolve=lambda path: None` says every file is missing, which `include-file-missing`
  reports; to validate a loaded document without reading its files, validate it without its path
  (`dataclasses.replace(document, path=None)`, or `parse(text)`).
- **Nested includes** ([#114](https://github.com/ForLegalAI/legaldown-validator/issues/114)). In a
  template an `{{include:}}` in a fragment or an attachment file is `template-fragment-invalid` (§15.3), in a
  sentence or a list item too, and is not followed. Outside a template §12.2 allows it, and
  `validate` **follows** it: each include paragraph of a fragment is judged as the document's are
  (`include-not-legaldown`, `path-not-relative`, `path-outside-root`, `include-file-missing`), from the
  directory of the fragment that holds it (`parts/a.lgd` including `b.lgd` reads `parts/b.lgd`; `../b.lgd`
  from it is the document's directory, and is read, while one that leads out of the document's
  directory is read only under a `document_root`, #109), at its line in the fragment and with the fragment as
  `Diagnostic.file`. A file reached by several includes is read, and its own findings made, once; the
  findings on an include of a fragment are made once for each include. The fragment's shape (frontmatter, level 1
  heading) is reported as for any fragment, and it is not followed or spliced when it fails. A chain that
  comes back to a file in it, or to the document itself, is `include-cycle` (§12.2, §16.11) at the include
  that closes it, and is followed no further; a file included from two places (a diamond) is no cycle. A
  document that includes itself is a cycle too, in a template as well. Files are the same file when their
  paths joined to the document's directory and normalized are (`./a.lgd`, `sub/../a.lgd` and, from a document
  in `dir`, `../dir/a.lgd`): read once, judged once, and named in a finding by the path it was first met by (whichever spelling
  comes first in the order the validator reads them, so an attachment's `../dir/a.lgd` may name a file that an
  include spells `a.lgd`).
  Symbolic links are **not** followed for this (a link is another file, and so is another case of a name on
  a file system that ignores case), and a chain through one ends at the depth cap; the one place a link
  is followed is whether a file is the document itself. In a template an include in a fragment is not followed,
  and the Warning of §17.5 says so; it does not leave the rest of the fragment unknown (what the fragment
  fills it still fills).
  **Caps.** A document that is not the author's can chain includes into a tree of any size, so the walk stops at a
  **depth of 32 fragments** and at **10,000 expanded entries** (every include followed and every heading
  of a fragment counts one, so a fragment that includes two others that each include two others is cut
  short long before it is expanded). Past either nothing more is followed, what lies after the include is
  not known (a `heading-skip` that a fragment would have filled stands), and the Warning of §17.5 says which cap was
  reached and that the rest was not checked. The caps are this implementation's own (the specification has none).
  Assembly of a document that is not a template follows the same includes, with the same caps
  ([Assembly](#assembly-157-176), [#118](https://github.com/ForLegalAI/legaldown-validator/issues/118)).
  A fragment validated on its own (`validate(fragment=True)`) reads no file, so its includes are judged by their
  path only (`include-not-legaldown`, `path-not-relative`); the document that includes it follows them.
- **The identifiers and the combined document.** `include-heading-skip` is judged on the combined document
  **under every combination of answers** ([#108](https://github.com/ForLegalAI/legaldown-validator/issues/108);
  §15.3, §15.4, §16.11). The questions are those that the presence of a read, spliceable include paragraph
  names (its own condition and those of the sections around it, `Units.presence`), as §15.3 says ("the
  questions named in the presence conditions of `{{include:}}` paragraphs"), in the order they are first used
  in the document (within one presence, as the frontmatter declares them). A question that only a section, a
  paragraph or an item names is not varied, and its conditions hold, as do those on a question that has no
  answer to try: a conditional section takes its subsections with it, so taking it out cannot break §4.1 by
  itself (§15.3, "Heading hierarchy"). For each combination (true before false, a choice's values as
  declared, the last question changing fastest) every unit it makes absent is removed (an include, and a
  section with its subsections, since a presence is cumulative), the fragments of the includes that are left
  are spliced in, and §4.1 is judged.
  Each fragment's headings are placed after
  the heading of the section holding its include paragraph (before the first heading, for the
  preamble), those of the fragments it includes after the heading of its own section that includes them, and
  are reported where they are: a fragment's heading in its file, a heading of the
  document that follows a fragment's at its own line; the document's own `heading-skip` is not
  reported a second time. A heading is reported **once**, however many combinations skip at it, under
  the first (the message ends `It is so when forum=arbitration, nda=false.`, naming every question tried;
  a template with no conditional include has one combination and the message is as it was). Conversely, a
  `heading-skip` of the document that the fragments read between the headings fill in every
  combination in which the heading is there is dropped (`# One`, an include of `## Deep`, `### Three`): §4.1 judges the
  combined document (§12.2). A document heading that is filled in some combinations and not in others
  (`# One`, a conditional include of `## Deep`, `### Three`) keeps its `heading-skip`, and no
  `include-heading-skip` is added for it. That holds only when every include paragraph in the section before the
  heading was read, and what those include was followed; with one missing, not
  LegalDown, judged as a path, unreadable, or reported as not a fragment
  (`include-has-frontmatter`, `include-has-h1`: it is not spliced), or with
  something it includes not followed, the plain per-document judgement stands.
  **Cap:** at most **12 questions and 4,096 combinations**, whichever is reached first (a template's
  questions are few; the cap is this implementation's). Headings that are there together (consecutive ones
  with one presence) are judged once for their order inside, and once for each combination only for their
  first, so the work is the combinations times the changes of presence, not times the headings. Past it the pass is made once, with every
  include present, as before #108, and an include that is conditional fills nothing; the Warning of §17.5 says
  so ("include-heading-skip under every combination of answers ... judged with every include present
  instead"), and there is no partial try. Combinations that leave the same units present are judged once.
  `include-anchor-duplicate` compares the **explicit** section identifiers (the specification's
  "section identifiers", §12.2) of the document and of every fragment in the order of the combined document
  and reports the later declaration: two that can never be there together (alternatives, with exclusive
  presences, §15.4; a fragment is there when its include is) are none, and two in the document itself are
  `anchor-duplicate`'s. A fragment included twice declares its identifiers twice and is reported **once**,
  at its heading in its own file (the heading is addressable there), not at the second include. An
  auto-generated identifier is not compared across the files (`anchor-autogen-collision` is not made
  across a fragment and the document), and neither are the identifiers of items and paragraphs. A reference into a
  fragment (`{{ref:}}` to its section) is still `ref-broken`: fragments are not joined to the document's identifiers
  ([#117](https://github.com/ForLegalAI/legaldown-validator/issues/117), the combined document).
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
  with the same function (`include_paragraph`). Assembly's own refusal of an `{{include:}}` in an
  attachment file of a document that is not a template (§12.2 allows it; it is not assembled) is an
  `include-file-missing` at the include's line, in the attachment's file, one for each include
  paragraph (an `{{include:}}` in a sentence, a list item or a quote is text, §12.2), quoting the
  directive as written. In an include fragment it is followed ([Assembly](#assembly-157-176)).
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
  as one that names a file; in a plain fragment it is allowed (§12.2) and is `include-file-missing` there
  too, at its line in the fragment, and assembly refuses it for the same reason, at the same line.
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
Assembly has an id of its own too, **`ldv-include-limit`**, an Error ([Assembly](#assembly-157-176)), and
validation a second one, **`ldv-frontmatter-key-duplicate`**, an Error or a Warning, which is a metadata check
(any frontmatter mapping, #126, #131) and not a skipped one.

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
| Includes (§12, §16.11) | an include paragraph | always: the **combined-document checks** (§16.11: a reference or a term into a fragment, `insertion-boundary` and the identifiers a fragment and the document share are judged without the fragments joined to the document; [#117](https://github.com/ForLegalAI/legaldown-validator/issues/117)). For the includes not read: `include-file-missing` (no loader, or a path that leads out of the document's directory with no `document_root` to read it under, §2.3), `include-has-frontmatter`, `include-has-h1`, `include-heading-skip`, `include-cycle`, `include-anchor-duplicate`, and in a template the part of `template-fragment-invalid` that reads a fragment. Past a cap of the walk (32 fragments deep, 10,000 includes and headings), the rest of the include tree. Past the cap of the combinations (12 questions, 4,096 combinations), `include-heading-skip` under every combination of answers (it is then judged once, with every include present) |
| Attachments (§12.4, §16.10) | `attachments[].file` | `attachment-file-missing`; for a LegalDown attachment file also `attachment-has-frontmatter`, `attachment-has-h1` and `attachment-anchor-duplicate`, and importing its definitions for each LegalDown attachment file whose definitions were not imported, with the reason (see below) |
| Amendment (§16.8) | `amends.file` | `amends-file-missing`; when the original was not imported (for any reason, see below), `amend-term-undefined` (a term the amendment does not define is then `amend-term-unresolvable`, Info, as §7.5 says) |
| Bilingual (§14, §16.7) | a non-empty `translations` | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-template-mismatch`; and `translation-implicit-id` and `translation-authoritative-absent` for the linked files, whose identifiers are not read (the document's own are checked, Core, §17.2) |
| Declared paths (§17.4) | `supersedes.file` (the object form), an image path | `supersedes-file-missing`; the existence of image paths (§8.7), which has no rule id |

- **Not counted as unread.** An include whose path is already an Error (`path-not-relative`,
  `path-outside-root`) or is not a LegalDown file (`include-not-legaldown`) is not an include that
  was left unread: the finding is its own. An image path that is already `path-not-relative` or
  `path-outside-root` is not an image to check either. An include that a loader cannot find is
  `include-file-missing`, and the Warning counts its fragment as unread, since its content was not
  checked. An include of the document itself is `include-cycle`, an Error of its own, and not an include left unread.
  An include in a fragment that is not followed for a reason that is no finding of its own (a path that leads out
  of the document's directory with no root) is counted, as one of "the includes in the fragments they take in".
- **When "importing the definitions" is named, for an attachment file.** Of a LegalDown attachment file
  whose definitions were not imported, for every reason but its own path (its definitions are the
  document's already): no loader to read it (no `resolve`, and the document has no path); a path that leads
  out of the document's directory (valid, §2.3, and not asked for without a `document_root`, as for an include, #109); a path that is
  a finding of its own (`path-not-relative`, or `path-outside-root` given a root), whose definitions are not
  imported, so that a `def-duplicate-id` against them (§16.10) is not made; or a file that could not be read
  (the loader has none, frontmatter that cannot be read, or a backslash or a null byte in the path, which no
  finding reports). The message counts each reason (`3 LegalDown attachment files (1 leads out of the
  document's directory, which is not read without a document root; 2 could not be read)`). With the deprecated `import_attachment_definitions`, any path the
  importer was asked about and gave nothing for is one that could not be read, whatever the path is. A path is
  judged as it is written once stripped, as `parse` reads it (it strips `attachments[].file` and
  `amends.file`) and the references are judged.
- **When `amend-term-undefined` is named, for the original.** Whenever the definitions of the original an
  `amends.file` names were not imported, for any reason, so for every one above and also for its own path
  and for a file that is not LegalDown. A term then drops quietly to the Info `amend-term-unresolvable`,
  so the Error check is not made, and §17.5 asks that it be said. Attachment files and the original differ
  in two ways: the document's own path is not counted for an attachment file, and a file that is not
  LegalDown is never counted for an attachment (it is not imported at all) while the original that is not
  LegalDown is.
- **Core rules are named only for what was not read.** `translation-implicit-id` and
  `translation-authoritative-absent` are Core for the document itself and are checked in it, so the
  Warning does not list them as skipped; it names them "for the linked files", which a Core validator
  does not open, and says that this document's own identifiers were checked only when the rules ran (the document is
  a translation, or declares no `authoritative`); otherwise it says they were not, that the document is not a
  translation (§17.2), and why (§17.5: the document is not reported as passing a check that was not run). The other Core rules of #116 (`language-code-invalid`,
  `authoritative-not-declared`, `definition-used-before-declaration`) read nothing else and are never named.
- **Rendering** (§17.3) is a category of its own in §17.5's sense only for a `{{ref:}}` to an item
  or paragraph anchor (`ref-not-enumerated`); it draws no Warning, and is listed above.
- Assembly claims no check, so it adds nothing; `Template.validation` carries the Warning as advice, and
  `Template.problems` does not (it is not a template rule).

## Cost on large or hostile input (#94, #110)

Parsing and validating take time linear in the size of the document, with the exceptions below.
Each is a document built to be slow, none occurs in a document written by a person, and none
changes a result: the identifiers and the findings do not depend on the size of the input.
(`tests/test_scaling.py` counts the work of each shape that was made linear, and sets a generous
time budget.)

- **Headings of one title under pairs of conditions** are quadratic: `p_k ∧ q` and `p_k ∧ !q` for
  every k, where each heading's identifier is the lowest suffix (`same`, `same-2`, …, §5.5) no
  heading that can appear with it has taken. Each new heading is compared with the suffixes taken
  before it, by counts, without testing a condition: **O(n²) in the number n of such headings,
  about 0.7 s for 1,000, 3 s for 2,000 and 14 s for 4,000**, and each needs a question to
  be declared. The identifiers are exact; there is no cap on this work, as a cap would make an
  identifier depend on the size of the document.
- **Lists nested to the depth cap** read the lines of an item again for each of the up to
  `MAX_LIST_DEPTH` (64) lists that enclose it: linear in the size of the document, with a larger
  constant than a flat list of short items has (1,000 nested items, 1 MB, take about 3 s, where
  100,000 flat items, 1.8 MB, take about 4 s). Deeper than the cap an item is one paragraph.
- **Circular definitions** test at most `MAX_EXCLUSION_TESTS` (4,000,000) pairs of conditions,
  about 5 s, past which alternatives are not told apart (see Alternatives above).
- **`definition-used-before-declaration`** has its own budget of `MAX_EXCLUSION_TESTS` (4,000,000) pair
  tests, so its worst-case time adds to `definition-circular`'s. Past it a use whose declaration cannot be
  told apart is left unjudged (fewer Infos), the opposite lean to `definition-circular`, which joins
  alternatives (more Warnings): an Info is better missed than given falsely.
- The walk of includes and the combinations of answers are capped as documented
  ([Include fragments](#include-fragments-122-1611-1612)).

## Checking this yourself

The conformance harness runs against a checkout of the specification repository:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Each case's expected rule is asserted at its expected line, where the case gives one (above).
Cases for the rules above are skipped by name, so the 12 `not implemented` skips reproduce this
table one for one, except `ref-not-enumerated`, which has no fixture. Each fixture document is
also written back (`serialize`) and validated again, which must report the same diagnostics. The
run reports 255 passed and 18 skipped (246 and 27 before #116, whose five rules now run, and with them
the four cases of several files that expect nothing in another: `attachment-id-collision`, `-duplicate`, `-title-empty` and
`-unreferenced`; 238 and 35 before #35): twelve are the `not implemented` skips, three are the
implemented amendment rules, skipped as `requires conformance level full`, two are the `multi-file`
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
  path that leads out of its directory, unless `--document-root` holds it (#109). Reading them is a Full capability (§17.4) that this
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
  `{{include:}}` in an attachment file of a document that is not a template is not assembled: it is
  refused with `include-file-missing`. One in an include fragment of such a document is followed, below.
  The other Full checks — `include-anchor-duplicate` and `include-heading-skip` (which the validator
  reports), `attachment-anchor-duplicate` and the path rules — are not made by assembly. (A document
  that fails `include-heading-skip` is assembled; the validator is where to find it.)
- **Nested includes of a document that is not a template**
  ([#118](https://github.com/ForLegalAI/legaldown-validator/issues/118)). §15.7 gives a draft, and so
  an ordinary document, the same procedure, with every step that has nothing to act on changing
  nothing; §12.2 allows a fragment to include fragments. Assembly therefore **follows** the include
  paragraphs of a fragment, as `validate` does, and **writes each file it reads at its own path**
  (§15.7.2: "each fragment ... is written to an output file at the same relative path"): the
  `{{include:}}` lines are not replaced by the text, so the output tree is a document that `validate`
  reads the way it read the template's. Step 7 splices every fragment, nested ones too, to tell which
  identifier each heading gets. §12.2 says only that an include path is relative "to the including
  document"; this implementation reads a nested one **from the directory of the fragment that holds it**,
  the way `validate` does (above), so that the output validates as the input did (§15.7.4). The
  specification and its fixtures do not decide this. A nested path that leads out of the document's
  directory is read only under a `document_root` that holds it (#109), and its key in `files` keeps the
  `../`, as a flat one does ([#123](https://github.com/ForLegalAI/legaldown-validator/issues/123) is not
  fixed: a file reached by two spellings is read and written under both).
  - **A cycle** is `include-cycle`, an Error, at the include that closes it, in the file that holds it,
    with the validator's message (a `Template`'s `problems` hold it once, not once for the assembly and
    once for the validation). It is the validator's own test, so that a chain through
    another spelling of a file, or back to the template (when the template has a `path`; without one
    nothing says which file it is, and it is read as a fragment and refused for its frontmatter), is
    caught the same way. A document that is not a template and includes *itself* is now `include-cycle`, at its
    line, where assembly said `include-has-frontmatter` of it. A **template** that includes itself is refused as
    it was (the file is read as a fragment, and has frontmatter and a heading of level 1, and its include is
    `template-fragment-invalid`): there is no `include-cycle` of assembly's in a template, which has no nested
    include to follow, though the validator names the cycle.
  - **The caps are Errors.** Assembly shares the validator's walk limits (32 fragments deep, 10,000 includes
    and headings) and spends them in the walk's order: an include costs one before it is judged, a fragment's
    headings cost one each in section order, and the document's own includes cost none; so
    both stop at the same place (a test lowers the caps and compares them). Where the validator warns that it
    did not check past a cap (`ldv-check-skipped`), assembly would be leaving a part of the document out, which §17.6
    forbids; it refuses with **`ldv-include-limit`**, an Error ("assembly stopped at its limit on ..."), at the
    include or heading where the cap was reached, and assembles nothing. The id is the implementation's own
    (§16.1, `ldv-`; `legaldown.grammar.INCLUDE_LIMIT_RULE`).
    **Changed:** a fragment of more than 10,000 headings, which was assembled when it included nothing, is now
    refused for the same reason (so is one of 32 or more levels of includes, which was refused anyway).
  - An include is judged in the validator's order: it names a file, **is a LegalDown file**, is a path that can be
    read from the directory of the file that holds it, closes no cycle, is not too deep, is there. An
    include of a file that is not LegalDown (`a.txt`) is `include-not-legaldown`, an Error at its line, nested
    or the template's own ([#128](https://github.com/ForLegalAI/legaldown-validator/issues/128); assembly
    used to read it if the loader had it), and the loader is not asked for it. (The validator judges the
    path of the template's own include, if it is absolute or leaves the root, as `path-not-relative` or
    `path-outside-root` alone; assembly refuses it as `include-file-missing`, as before.) **A deliberate
    difference:** a valid path that only leads out of the document's directory, where no `document_root`
    holds it, is unread for the validator (the Warning of §17.5) and `include-file-missing` for assembly,
    as for a flat include, since no output can be made without the file.
  - Everything else is as for a flat include: a file that is not there or cannot be read is
    `include-file-missing` (with its file and line, now); `include-has-frontmatter`, `include-has-h1`
    and unreadable frontmatter are reported for a nested file, which is then not followed; an include that is not an
    include paragraph (in a sentence, a list item, a quote, a drafting note) is text and is not read.
    A file that is an attachment file as well as an include fragment is read once, as a fragment, and is still
    checked as an attachment file: an include in it is refused. **Not changed:** a template's nested include
    and any in an attachment file stay refused.
  - A fragment included from two places is asked its questions at the last include of it in the combined
    document, however the files were read (`Form.questions`).
- A `{{placeholder:}}` or `{{choose:}}` written across two lines is refused with
  `directive-malformed`, which the validator reports too (see above): it could not be filled.
- A template with `translations` is refused with `translation-file-missing`: its linked templates
  are assembled with it (§15.7.2), which this implementation does not do. The specification has
  no id for a capability an implementation lacks, so the refusal uses the rule it would otherwise
  break.

Assembly edits the template's source and so inherits a few limits of the parser's reading of it:

- The line-start check (§15.7.3) reads the output with the parser, line by line, against the
  template line read the same way, so a marker is judged in the container that holds it (`> 2. x`,
  a lazy line, a line after a deleted one). It cannot give a backslash a position where the
  template's own markers, left bare by an empty phrase, begin the construct (`text` over
  `- {{choose:}}` with an empty phrase: the bare `-` underlines `text`; `- - - {{choose:}}`: a
  thematic break; a bare `> 2. ` that holds no paragraph for the lazy lines after it to continue, so
  that the next template `> ` opens a quote of its own). The line after one that an empty phrase
  leaves as bare markers (`> `) is not checked: it is not blank, so step 5 does not delete it
  (§15.7.2), and `> text` / `> ` / `> <x>` keeps `<x>`, which begins an HTML block there. The
  specification gives no position for these; the output keeps the template's markers as written
  (open question #120). Inserted text in the one top-level block of a long list item or
  quote, each answer beginning a block (`- `, `# `), is read against that block's earlier lines:
  quadratic in the size of the block.
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
