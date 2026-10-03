<div align="center">

# legaldown-validator 📐

### The reference implementation of [LegalDown](https://github.com/ForLegalAI/LegalDown)

**Parse, validate, and serialize LegalDown documents — from the command line or from Python.**

Targets specification **v0.2** · Every diagnostic carries a **stable rule id** · One dependency: PyYAML

</div>

---

## What it does

[LegalDown](https://github.com/ForLegalAI/LegalDown) is an open plain-text standard for legal
documents. This package is the toolkit for working with those documents in code:

📥 **Parses** a `.lgd` file into a typed document model — frontmatter, sections, blocks, parties,
definitions, anchors.

✅ **Validates** it against the specification's rule set (§16): broken cross-references, undefined
terms, duplicate identifiers, malformed party metadata, bad dates and money values, and more.

📤 **Serializes** the model back to LegalDown source, so you can edit documents programmatically
and write them out again.

🧩 **Assembles** templates (§15.7): a template and an answers set in, the finished document out,
byte for byte as the specification defines it.

🏷️ **Names every finding.** Each diagnostic carries the specification's stable rule id (§16.1)
alongside its severity and message, so you can suppress one check, escalate another, or gate a
build on exactly the rules you care about. Rule ids survive specification renumbering — they are
the part of a diagnostic that is safe to depend on.

🖨️ **Rendering** a document to HTML or plain text, with its numbering, cross-references, and
defined terms, is the job of [`legaldown-render`](https://github.com/ForLegalAI/legaldown-render),
the reference renderer built on this package (`pip install legaldown-render`).

## Install

```bash
pip install legaldown-validator
```

Python 3.11 or newer. The distribution is named `legaldown-validator`; the import name is
`legaldown`:

```python
import legaldown
```

The package ships a `py.typed` marker, so type checkers use its annotations directly.

## Quick start

Given a LegalDown document:

```markdown
---
title: Services Agreement
document_type: contract
sides:
  - name: providers
    label: Provider
    parties:
      - name: acme
        label: Acme
        type: legal_entity
        legal_name: Acme Corporation
  - name: clients
    label: Client
    parties:
      - name: beta
        label: Beta
        type: legal_entity
        legal_name: Beta Industries Inc.
language: en
---

# Fees {#fees}

{{party: beta}} shall pay {{money: 5000}} under {{ref: payment-terms}}.
```

Validate it:

```bash
legaldown validate contract.lgd
```

```
contract.lgd:14: error: [ref-broken] Broken section reference: 'payment-terms'.
contract.lgd:22: warning: [money-missing-currency] Money directive without currency parameter.

1 error(s), 1 warning(s), 0 info(s)
```

One diagnostic per line, each prefixed with its file and line (§16.9) and its rule id, in source
order. A clean document reports `No issues found.` and exits `0`.

Diagnostics go to **stdout**; the trailing summary and `No issues found.` go to **stderr**, so
`legaldown validate contracts/ > report.txt` captures the findings alone. `--quiet` drops the
summary entirely.

## Command line

```bash
legaldown validate contract.lgd                      # one document
legaldown validate contracts/                        # a whole directory, recursively
legaldown validate --format json contract.lgd        # machine-readable output
legaldown validate --ignore def-unreferenced doc.lgd # mute one rule
legaldown validate --warnings-as-errors doc.lgd      # tighten a CI gate
legaldown validate --final signed-contract.lgd       # nothing left to fill in
```

| Option | Effect |
|---|---|
| `--format {text,json}` | Output format (default `text`) |
| `--ignore RULE_ID` | Suppress a rule by its stable id — repeatable |
| `--final` | Check a document is ready for signature: a remaining placeholder, drafting note, or template construct is an error (§15.9) |
| `--warnings-as-errors` | Report warnings at error severity |
| `--strict` | Exit non-zero on any diagnostic, not just errors |
| `--quiet` | Drop the trailing summary line |

Directories are searched recursively for `*.lgd`, `*.legaldown`, and `*.legal.md`, and
`legaldown --version` reports the validator version.

**Exit codes:** `0` clean · `1` diagnostics found (errors, or any diagnostic under `--strict`) ·
`2` a file could not be read.

**Changed in 0.4.0:** a document that amends another (`amends.file`, §7.5) or declares LegalDown
attachment files (§12.4) is now checked against the definitions those files declare, read from beside
it (never from outside its directory). Before, `legaldown validate` never read them, so a term only the
amended original defines was an Info (`amend-term-unresolvable`) and is now found, and an
`amend-def-override` or `def-duplicate-id` can be reported that was not. A CI check on such documents
can start to fail for that reason: it is the specification's result, and the files must be beside the
document. A file that is not there, or cannot be read, is as if it had not been asked for.

### Assembling a template

```bash
legaldown assemble template.lgd --answers answers.yaml          # the document to stdout
legaldown assemble template.lgd --answers answers.yaml -o out/  # with its fragments and attachment files
```

```bash
legaldown assemble template.lgd -i -o out/                         # asked in the terminal for what is missing
legaldown assemble template.lgd -i --answers a.yaml --save-answers a.yaml -o out/   # ... and remembered
legaldown questions template.lgd --answers answers.yaml           # what is asked, given the answers so far
legaldown questions template.lgd --answers answers.yaml --format json
```

`assemble -i` asks, in the terminal, for the answers `--answers` does not give, as the questions are
reached — a decision opens or closes the questions under it. Each prompt shows the question, what to
type (`yes or no`, `an amount and a currency, like 5000 EUR`) and the default in brackets, and each
answer is checked as it is typed. A decision is asked again until it is answered; an empty line leaves
a value question to its default, or its blank. The prompts go to standard error, so standard output
(or `-o`) is still the assembled template. Without a terminal on standard input, `-i` lists what must
still be answered and exits 1, or assembles if nothing must be. At the end of the input the interview
stops and assembly goes on with what was answered (the questions left are left to their defaults or
blanks, or refuse the assembly if they are decisions); Ctrl-C stops it (exit 2). A template that cannot be assembled asks nothing: its problems are
reported first. `--save-answers FILE` writes the answers used, with those typed, as YAML — also
after an interruption or a failure, so that typing is not lost — and the file is an answers set for
the next run. It is written as a whole or not at all, beside the file and moved into place, so it may
be the answers file that was read; but it is the answers, not the file's text, that are written,
so comments in it are lost (with or without `-i`).

`questions` lists the questions reached, which are unanswered, which stop assembly, and what is wrong
with the answers; `--format json` is `Form.as_dict()` (below), for another program or an agent. It
exits 0, whatever the answers, unless the template has problems or cannot be read; `--check` makes a
form that cannot be assembled exit 1.

The answers set is a YAML mapping of question ids to answers (§15.7.1); the shapes plain YAML gets
wrong are put right for the template's questions (`5000 EUR`, `yes`, a number as an amount). Include fragments and
LegalDown attachment files are read relative to the template, never from outside its directory;
the ones assembly keeps are written under `-o` at their relative paths. Answer problems are
reported as `answer-invalid`, `answer-missing` and `answer-unknown` (§16.12) on stderr, and
nothing is written when there is an Error. A template with Errors in the validator's template rules
(questions, conditions, `{{choose:}}`, placeholders, insertions) is refused, with the rule ids, as
§15.7.2 gives assembly a valid template. Exit codes are as for `validate`.

**Changed in 0.4.0:** `legaldown assemble` refuses a template that has Errors in the validator's
template rules (`question-invalid`, `condition-invalid`, `choose-invalid`, the `placeholder-*` rules,
`insertion-boundary`, `def-term-variable`, `drafting-note-def`, `template-fragment-invalid`, ...),
with exit 1 and the rule ids on stderr, where 0.3 assembled it. §15.7.2 gives assembly a template
that validates; a gate that assembles templates in CI can start to fail on one that never did.
`--save-answers` does not write when there was nothing to save (no answers file read, nothing typed),
so that an answers file you meant to resume from is not replaced by an empty one.

### JSON output

`--format json` emits the structured shape described in §16.9 — ideal for CI annotations, editor
integrations, and dashboards:

```json
{
  "legaldown_spec": "0.2",
  "validator_version": "0.4.0",
  "diagnostics": [
    {
      "file": "contract.lgd",
      "rule": "ref-broken",
      "level": "error",
      "line": 14,
      "message": "Broken section reference: 'payment-terms'."
    }
  ]
}
```

### In CI

```yaml
- run: pip install legaldown-validator
- run: legaldown validate contracts/ --warnings-as-errors
```

The exit code fails the job; the rule ids let you grant exceptions without turning whole checks
off.

## Python API

Three functions cover the common path:

```python
from legaldown import load, serialize, validate

document = load("contract.lgd")
result = validate(document)

for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)

if result.is_valid:                      # no Error-level diagnostics
    print(serialize(document))           # the source text; save(document) writes it to the file
```

`load(path)` takes a `str` or `os.PathLike`, reads the file as UTF-8 and returns the `Document`.
It names the document for you: `document.filename` is the file's name and `document.path` its
absolute path (symbolic links are not followed), which is where the files it refers to are to be
looked for. For text that is not in a file (an editor buffer, an HTTP body, a test), use
`parse(text)`, which `load` is built on; it takes an optional `filename=` to name the document in
its diagnostics.

`load` raises `FileNotFoundError` (or another `OSError`) when the file cannot be read and
`UnicodeDecodeError` when it is not UTF-8. Both it and `parse` raise `FrontmatterError` (a
`yaml.YAMLError` and a `ValueError`) when the frontmatter cannot be read — the
`frontmatter-invalid-yaml` rule, which `validate`, given a parsed document, cannot report.

`validate(document)` checks it and returns a `ValidationResult`; `validate(document, final=True)`
is the signature-ready check (§15.9). A document that amends another (`amends.file`, §7.5) or
declares LegalDown attachment files (§12.4) is checked against the definitions those files
declare. They are read from beside the document — `document.path` is where `load` found it — and
never from outside its directory (§2.3); only the definitions each file declares itself are read,
not those it refers to in turn. A file that is not there, is not UTF-8, or has frontmatter that cannot be read is as
if it had not been asked for: no diagnostic of its own. A document from `parse(text)` has no path,
so there is nothing to read (`resolve=lambda path: None` does the same for a loaded one, to
validate it without reading anything); give `validate` a `resolve=` function, from a relative path to the
file's text (or `None`), to read them from elsewhere — a database, an upload. `file_loader(directory)`
is the same function for files on disk, and the one `parse_template` takes as `resolve=` (`LoadFile`).

> **Deprecated:** `parse_document` is now `parse` (string; same arguments) or `load` (file),
> `validate_document` is now `validate`, `serialize_document` is now `serialize` (text) or `save`
> (file), and the importer callbacks `import_definitions=` and `import_attachment_definitions=` are
> replaced by `resolve=`. They still work and raise a `DeprecationWarning`; they are deprecated
> since 0.4.0 and will be removed in 0.5.0. Every deprecation warning legaldown raises is a
> `legaldown.LegaldownDeprecationWarning` (a `DeprecationWarning` subclass), so one filter catches
> them all: `warnings.simplefilter("error", legaldown.LegaldownDeprecationWarning)` in code, or
> `filterwarnings = ["error::legaldown.LegaldownDeprecationWarning"]` in pytest's configuration
> (Python's own `-W` option and `PYTHONWARNINGS` ignore a category from a package, as they read it
> before the package can be imported).
>
> **Changed in 0.4.0:** `ValidationResult` is a plain value of two parts, `diagnostics` and `index`.
> `errors`, `warnings` and `infos` are read-only lists taken from `diagnostics` (changing them
> changes nothing; change `diagnostics`). What validating resolved, besides what it found, moved
> to `result.index` (below): `result.sections` is now `result.index.sections`, `result.is_template`
> is `result.index.is_template`, and so on, and the five `inline_*` lists are
> `result.index.values.dates`, `.money`, `.durations`, `.fields` and `.placeholders`. The old names
> are gone. So are the result's recording methods (`error`, `warning`, `info`, `at`) and
> `used_terms`, which only the validator used.

The public API is what the `legaldown` and `legaldown.validator` packages export (their
`__all__`), and `legaldown.grammar` and `legaldown.syntax`. `legaldown` is the workflow and the
model: loading, validating, saving and assembling documents, what that returns, and the `Document`
dataclasses. `legaldown.syntax` and `legaldown.grammar` are the tooling API (see
[Tooling API](#tooling-api)): reading source text the way the validator does, and the language's
constants and rules. The names that moved out of `legaldown.__all__` in 0.4.0 — the lexer, the
fragments, the definition readers, `render_block`, the dict factories and the like — still import
from `legaldown`, as the same objects and without a warning, so code written for earlier releases
keeps working; new code imports them from their home (the table at the end of
[Tooling API](#tooling-api)). Changes to the API are listed in the notes of each
[GitHub release](https://github.com/ForLegalAI/legaldown-validator/releases); before 1.0 a
minor release may change it, a patch release does not. Other modules are internal and may
change in any release.

### Working with the result

Validating a document builds the indices the checks need — section numbers, resolved definitions,
party display text, every inline value found in the body. `result.index`, a `DocumentIndex`, hands
all of it back, so a renderer or a UI can reuse the work instead of re-deriving it. The result
itself is what was found, kept nowhere but in `diagnostics`:

| `result.…` | Contents |
|---|---|
| `diagnostics` | `Diagnostic(rule, level, message, line, file)` — the authoritative record |
| `is_valid` | `True` when no Error-level diagnostic was reported |
| `errors` / `warnings` / `infos` | Message strings by severity, taken from `diagnostics` |
| `rules(level=None)` | Set of rule ids present, optionally filtered by severity |
| `index` | A `DocumentIndex`. Values and markers in it are as written: one that is invalid is reported in `diagnostics` too, so check `is_valid` before relying on them: |

| `result.index.…` | Contents |
|---|---|
| `sections`, `section_lookup` | Numbered section index; resolves `{{ref:}}` targets. Numbers count from the shallowest heading level, and a level a heading skips counts as 1 (`#`, `###`, `##` → 1, 1.1.1, 1.2), so no two sections share a number except alternatives and what they contain (§15.8); an entry's `alternative` is true for a section that is an alternative to its preceding sibling — the section just before it at its level under the same parent, with the same identifier, never present together with it (§15.4) — and so shares its number (§15.8) |
| `definition_lookup`, `party_lookup`, `side_lookup`, `attachment_lookup` | Resolved display text |
| `values` | The field-spec values the checks met, as written, as `InlineValues`: `dates`, `money`, `durations`, `fields`, `placeholders` (those of the frontmatter too; one with malformed arguments is not among them) |
| `blanks` | The document's blanks, a template's or not, by placeholder id in order of first occurrence: `Blank(id, type, fixed, in_frontmatter, consistent)` — `type` the type the validator settled on: the effective type of the first occurrence with a valid one (§10.7, §15.2; `text` when none is written), or `None` when no occurrence has a usable type (each is written with an invalid one, or the id is a decision question's); `fixed` the currency of a money blank or the unit of a duration blank that every occurrence fixes (`""` when one fixes none, or the blank is not `consistent`); `consistent` false when its occurrences have different types or fix two currencies or units (placeholder-type-inconsistent); `in_frontmatter` whether one occurrence is in the frontmatter (§3.10) |
| `is_template` | Whether the document is a template (§15.1): it declares `questions`, carries a condition, or holds a `{{choose:}}` |
| `placed_markers` | The markers in body text that apply (§5.7, §15.3), in document order: `PlacedMarker(section, block, fragment, offset, source, identifier, condition, field, item, include_only, line)` — in fragment `fragment` of `block_fragments(block)` (`legaldown.syntax`), at `offset`, which is the block's `field` (`text`, or `suffix` after a lifted `{{ref:}}`/`{{term:}}`); `item` is the list item it marks, counted in pre-order over all the list's items, nested and empty ones included, as `list_fragments` counts them; `identifier` is `""` where it does not apply (an include-only paragraph, §12.2). Identifiers and conditions are as written: check `is_valid` before relying on them |

`is_template(document)` (top-level) gives the template decision without validating (§15.1).
A renderer builds from these decisions rather than re-deriving them, reading the source with
the validator's own helpers ([Tooling API](#tooling-api)).
[`legaldown-render`](https://github.com/ForLegalAI/legaldown-render) is built this way.

### Reading and editing the document model

`load` and `parse` return a `Document` of plain dataclasses — `Metadata`, `Section`, `Block`,
`Side`, `Party`, `Attachment` — that you can inspect, edit, and write back out:

```python
from legaldown import load, save

document = load("contract.lgd")
document.metadata.governing_law = "Czech Republic"

save(document)                           # to document.path, where load found it
```

`save(document, path=None)` mirrors `load`: it writes the document to `path` (a `str` or
`os.PathLike`), or to `document.path` when it has none to be given, and returns the absolute path.
A document from `parse(text)` or built in code has no path, so `save` raises `ValueError` until you
pass one. The write is atomic — the file is either as it was or the new text, never half written —
a symbolic link is written through, and a file keeps its permissions. Missing directories on the way
are created. The text is UTF-8 with LF line endings and no byte-order mark, so a file that was read
with CRLF line endings or a byte-order mark is written back without them. Afterwards `document.path`
is the path written, as after `load`: that is where `validate` looks for the files the document
refers to (and `document.filename`, which diagnostics name, is its name). `serialize(document)` is the same text as a `str`, the inverse of `parse`, for when the
file is not what you want to write (an HTTP response, a database).

Content before the first heading — typically the sentence identifying the parties — is the
document's preamble (§4.4): it is unnumbered, so it lives in `document.preamble` rather than in
`sections`. `document.iter_blocks()` walks every body block, preamble first.

A `Section`'s `identifier` is the explicit `{#id}` written after its heading, or `""` when
it has none. The identifiers the validator generates (§5.3, §5.5) are not written back into the
model: read them from `result.index.sections`.

`document.to_dict()` and `Document.from_dict(data)` round-trip the model through JSON-friendly
structures (missing fields take their defaults), except the fields that describe the parsed source
rather than the document, `Metadata.not_line_editable`, `Metadata.frontmatter_absent`,
`Document.source_map` and `Document.path`. A blank document is `Document()`; `empty_document()`,
still importable from `legaldown`, is not one but a starter contract with two sides and two sections.

Each diagnostic names its `line` (from 1) and its `file` (the document's `filename`), as §16.9
requires: the line of the directive, marker, heading or block it is about, or of the
frontmatter key — for a missing key, the key that holds it, or the frontmatter's first. Lines
come from the `source_map` that `load` and `parse` give a document. A document built from a dict
has none, and one changed after parsing no longer fits its map: their diagnostics have no line
(`None`) rather than a stale one. `FrontmatterError.line` is the line of YAML that cannot be
read. `render_block()` (`legaldown.syntax`) renders a single block when you are driving your own
layout. `iter_directives()` (`legaldown.syntax`) lexes the directives in a piece of text by the §11.2
grammar — parameters in any order, quoted values decoded — and is what the validator itself uses.

`definition_lookup(collect_definitions(document))` (`legaldown.syntax`) gives a document's definitions, id to term,
without validating it: the same map as `result.index.definition_lookup`, except for definitions
imported from an amended original or an attachment file. A term written empty (`"" {{def: x}}`)
reads as its id (`id_term`), and an id that is not a valid identifier is left out.

One guarantee worth knowing: the parser is **faithful** — it never rewrites your input to make it
valid, so what you authored is exactly what the validator judges. The serializer, by contrast,
normalizes: frontmatter is re-emitted as canonical YAML, setext (underlined) headings are
written as `#` headings, list items are written with
`-` (`+` where `-` would read as a thematic break) or `1.`, `2.`, … whichever marker they
had, each nested list at its parent item's content and numbered on its own, without blank
lines between items, and table rows are written
with a pipe at each end and every `|` in a cell escaped as `\|`, so expect a
formatting-normalized file rather than a byte-for-byte copy.

What it normalizes is formatting, never content: what the validator reports of a document is
what it reports of the document written back. So the serializer writes every side, party,
representative and attachment the model holds, one not yet filled in too (a side without
parties, an attachment without a title), where the validator reports what it lacks. A
party's custom fields (§3.4) are the keys of its object that are none of its fields; they are
read into `custom_fields` and written back as keys of their own, a row whose label is still
empty as the key `''`. When keys cannot hold them all (a label written twice, or naming one of
the party's fields), they are written as the party's `custom_fields` list of `label` and
`value` entries, which is read too; a key named `custom_fields` is that list's, never a custom
field. A value that is a list or a mapping is not kept: a custom field holds text.

A list block's `items` are `ListItem`s, each holding the blocks of its content in order: its
first paragraph, then later paragraphs, headings, nested lists (a list block of their own kind),
code, quotes, tables and raw HTML. An empty item holds none. A paragraph in an item is always of
kind `paragraph`: a directive in it stays in its text. A heading in an item or a quote is a
`heading` block, not a section: its `text` without the `#` syntax, and its `level` (1–6; `level`
is 0 on every other block). Written outside an item, where it would be a section's heading, a
`heading` block becomes paragraph text with a backslash before it. `item_text(item)` gives an
item's first paragraph's text, and `render_item(item)` its content as written after its marker
(both in `legaldown.syntax`); a string item in `Document.from_dict` (as models before 0.3 held
them) is read as such content.
A nested item's condition applies within those of the items it is nested in (§15.3).

A quote block keeps its `text`: its content, one line per source line without the `>` marker.
The validator and assembly read that content as blocks, as they read an item's, so code and raw
HTML in a quote hold no directive.

Tables follow GFM: a table needs a delimiter row with one cell per header, rows take the
header's width, and a `|` inside a cell — a code span's included — is written `\|`. A
table block's `headers` and `rows` hold the cell text with those escapes removed, and
`align` holds each column's alignment (`"left"`, `"right"`, `"center"`, or `""`).

A paragraph's `text` (a definition's, and a ref's or term's `prefix` and `suffix`, too) keeps
its lines, joined with `\n` and without their indentation, as CommonMark reads them: a line
ending in `\` or in two spaces is a hard line break, and each other line break a soft one,
which a renderer shows as a space. Link reference definitions stay on their lines. It is written
back as those lines; a line that would, at the margin, end the paragraph (a list item, a
heading, a setext underline, a table's delimiter row, and the like — only a lazy line or a model
built in code holds one) is written four columns in, where it continues the paragraph and is
read back as it was. A blank line in a model's paragraph, which would end it, is left out.

Lines indented four or more columns are an indented code block, as in CommonMark, so a
paragraph indented that far is code: its directives and anchors are literal (§11.4). A
paragraph that a model holds but that would, written at the margin, open a heading, a fence,
or an HTML block is written with a backslash before it.

To write markdown source exactly as typed — an editor's row holding a list, a table or a
quote — put it in a `source` block (`{"kind": "source", "text": "* a\n* b"}`): the serializer
writes its text as it stands, respelling and escaping nothing, and reads it back as the blocks
it holds. Only blank lines at its ends are dropped, and a fence it leaves open is closed unless
it ends the document. It is written next to its neighbours as it stands, so it means what that
text means there: in a list item it is the item's content, and a list in it right after a list
with the same bullet joins that list. The parser never produces a `source` block, and the
validator does not read one: validate the written text.

Raw HTML follows CommonMark's HTML blocks: a block of raw HTML, or an HTML comment on
lines of its own, is an `html` block holding its source as written. No heading, directive,
or anchor inside it is recognized (§8.6, §11.4), so a clause commented out with
`<!-- … -->` is not a section. As in CommonMark, a comment left unclosed runs to the end of
the document.

## Tooling API

Tools built on the validator (renderers, editors, importers) need the language itself: its
vocabulary, its value formats, and how the validator reads source text. Two modules hand that
over. They are part of the supported API and covered by the same versioning as `legaldown`
and `legaldown.validator`, and they are the supported way to reach these names: every
module path below them (`legaldown.markdown`, `legaldown.validator.patterns`, …) is internal
and may be reorganised in any release (some of the names also still import from `legaldown`,
for compatibility). Their names are the validator's own objects, not copies, so
a tool and the validator cannot disagree.

**`legaldown.grammar`** is what needs no document: constants and small rules.

```python
from legaldown.grammar import (
    IDENTIFIER_RE, KNOWN_CURRENCIES, MAX_SECTION_LEVEL, PARTY_TYPES, VALID_PLACEHOLDER_TYPES,
    is_valid_iso_date, slugify_identifier,
)

is_valid_iso_date("2026-02-30")                 # False
slugify_identifier("1st Term")                  # "section-1st-term" (§5.3)
slugify_identifier("日本語", fallback="")        # "": nothing usable, as against real text
```

| Names | What they are |
|---|---|
| `SPEC_VERSION`, `LEGALDOWN_EXTENSIONS` | The specification version implemented; the file extensions of LegalDown source (§2.1) |
| `KNOWN_DIRECTIVES`, `DIRECTIVE_PARAMS`, `PLACEHOLDER_TYPE_PARAMS` | The directive vocabulary and the parameters each defines (§11) |
| `IDENTIFIER_RE`, `RESERVED_VALUE_TYPES`, `VALID_DOC_TYPES`, `PARTY_TYPES`, `KNOWN_CURRENCIES`, `DELIMITER_PAIRS` | Identifier format (§5.3), reserved value-type names, document types, party types, ISO 4217 codes, accepted definition delimiters (§7.2) |
| `VALID_PLACEHOLDER_TYPES`, `DURATION_UNITS`, `VALID_DURATION_UNITS` | Placeholder types and duration units (§10); `DURATION_UNITS` is in the order the specification lists them |
| `LIST_KINDS`, `MAX_SECTION_LEVEL`, `MAX_QUOTE_DEPTH`, `MAX_LIST_DEPTH` | Block kinds that are lists; the deepest heading level (5); how deep the parser reads quotes and lists |
| `VALUE_QUESTION_TYPES`, `DECISION_QUESTION_TYPES`, `QUESTION_TYPES`, `DRAFTING_MARKER`, `FINAL_CHECK_RULES` | Template question types (§15.2); the drafting note's first line (§15.6); the rule ids of the final check (§15.9) |
| `slugify_identifier(text, *, fallback="section")`, `format_section_number` | The §5.3 identifier of a heading or term, and `fallback` where the text yields none (`""` tells that case apart); a dotted section number |
| `is_valid_iso_date`, `is_valid_numeric`, `is_valid_money_amount`, `is_positive_numeric` | Value checks (§3.10, §10) |
| `parse_condition` → `Condition`, `condition_problem`, `exclusive`, `Presence`, `ALWAYS` | Conditions (§15.3, §15.4): parse one, tell why one is invalid, tell whether two units can never appear together |
| `choose_problem(directive, questions)` | Why a `{{choose:}}` is invalid (choose-invalid, §15.5): the first message `validate` records for it, or `None`; `ValueError` for a directive that is not a `{{choose:}}` or is malformed (`validate` reports that as directive-malformed) |

**`legaldown.syntax`** is reading source text the way the validator does.

```python
from legaldown import load
from legaldown.syntax import block_fragments, find_markers, lex

document = load("contract.lgd")
for found in find_markers(document):             # every marker and look-alike in the body
    print(found.source, found.placed(template=False), found.misplaced)
for _section, _index, block in document.iter_blocks():
    for text, _anchor in block_fragments(block):
        print([d.name for d in lex(text).directives])
```

| Names | What they are |
|---|---|
| `lex`, `Lexed`, `Directive`, `iter_directives`, `is_escaped`, `format_value` | The directive lexer (§11.4) and its inverse. A `Directive`'s `positional_span` and `param_spans` are where each value is written, quotes included, so `text[start:end]` is what to replace to change it; `Lexed.literals` are the comments and code spans the lexer skipped, as `(kind, start, end)` |
| `iter_document_directives(document)` → `DirectiveLocation(section, block, fragment, directive)`, `collect_source_directives` | Every directive in a document's body, in order, read as `validate` reads it: `fragment` indexes `block_fragments(block)`, and is `None` for a `{{ref:}}`, `{{term:}}` or `{{def:}}` the parser lifted into a block's fields (a definition's comes before its text); the `{{ref:}}` and `{{term:}}` targets of a document |
| `Marker`, `MARKER_RE`, `parse_marker`, `format_marker`, `is_look_alike` | Anchor and condition markers (§5.7, §15.3) |
| `find_markers(document)`, `FoundMarker`, `is_include_only` | Every marker and look-alike in a document's body, placed or not; `FoundMarker.placed(template)` says which apply (`validate` gives the placed ones as `result.index.placed_markers`) |
| `Fragment`, `ListFragment`, `block_fragments`, `list_fragments`, `text_fragments`, `list_items`, `item_text` | Where a block's text is, and a list's items |
| `collect_definitions(document)` → `DefinitionRef`, `definition_lookup`, `id_term` | The definitions a document declares in its own source (§7), in document order, each with where it is; `definition_lookup(refs)` is their id-to-term map as `validate` builds `result.index.definition_lookup` (but for definitions imported from other files); `id_term(id)` is the term an empty one reads as |
| `find_definition_anchors(text)` → `DefinitionAnchor` | Every `{{def:}}` in a text, with the quoted term it anchors and where that term starts (§7.2) |
| `render_block(block)`, `render_item(item)` | The other way: a block written as LegalDown source, and a list item's content as written after its marker, as `serialize` writes them |
| `Quote`, `block_quotes`, `is_drafting_note(block)` | The block quotes in a block and whether each is a drafting note; whether a quote block is one (§15.6) |
| `quote_blocks(block, depth=0)`, `drafting_note_blocks(block, depth=0)` | The blocks a block quote holds, as the validator reads them, as copies you may change; a drafting note's without its `[!DRAFTING]` marker (§15.6). `depth` is how many list items and quotes the quote is in, the count before entering it (a quote in a list item is at 1); past `MAX_QUOTE_DEPTH` the validator reads a quote as one text, so you get one paragraph of its text as written |
| `code_content(block)` → `CodeContent(info, text, fenced)` | A code block read as CommonMark reads it: the info string, and the code without its fences or indentation |
| `FRONTMATTER_RE`, `LINE_ENDING_RE`, `HTML_COMMENT_RE`, `FENCE_OPEN_RE`, `closes_fence`, `fence_end`, `dedent`, `indent_width`, `strip_text` | The Markdown rules the reading is built on: frontmatter, line endings, comments, fenced code, indentation |

**Where things are in the file.** `document.layout()` gives a `SourceLayout`: the frontmatter's
`(start, end)`, the preamble's `BlockSpan`s, and per section a `SectionSpan` (its `HeadingSpan` and
its blocks' spans), in `document.sections` order. Lines are file lines counted from 1, `end`
exclusive; a list's `BlockSpan` has its `ItemSpan`s, each with the spans of its blocks.
`document.line_of(section, block=None, item=None)` is one line: a section's heading, a top-level
block (`section=None` for the preamble), or a list item's marker, items counted in pre-order as
`PlacedMarker.item` counts them. These are the lines diagnostics name (§16.9). Both are `None`
for a document built in code, or changed since it was parsed. A span covers the lines
`[start, end)`; the blank lines between parts belong to none (but for those between a list's items,
which the item before holds). For text you hold apart from its file — an editor's field —
`body_layout(text)` is the same `SourceLayout` of that text read as a body alone, with lines counted
from 1 at its start and no frontmatter (a `---` first line is a thematic break there).

```python
document = load("contract.lgd")
for number, section in enumerate(document.layout().sections):
    print(document.sections[number].title, section.heading.start, [b.start for b in section.blocks])
```

If you import one of these from a private module, use its public home. The names that left
`legaldown.__all__` in 0.4.0 are listed too, as guidance only: importing them from `legaldown` keeps
working, without a warning.

| From | Use |
|---|---|
| `legaldown`: `lex`, `Lexed`, `Directive`, `iter_directives`, `is_escaped`, `collect_source_directives` | `legaldown.syntax` |
| `legaldown`: `Fragment`, `ListFragment`, `block_fragments`, `list_fragments`, `list_items`, `item_text`, `is_drafting_note` | `legaldown.syntax` |
| `legaldown`: `collect_definitions`, `definition_lookup`, `id_term`, `DefinitionRef`, `find_definition_anchors`, `DefinitionAnchor` | `legaldown.syntax` |
| `legaldown`: `render_block`, `render_item` | `legaldown.syntax` |
| `legaldown`: `DIRECTIVE_PARAMS`, `KNOWN_DIRECTIVES`, `DELIMITER_PAIRS`, `slugify_identifier` | `legaldown.grammar` |
| `legaldown`: `document_from_dict(data)`, `document_to_dict(document)` | `Document.from_dict(data)`, `document.to_dict()` |
| `legaldown`: `empty_document`, `BLOCK_DEFAULTS`, `metadata_from_dict`, `section_from_dict`, `block_from_dict`, `side_from_dict`, `party_from_dict` | No other home: they stay importable from `legaldown`; `Document()` is a blank document, and `Document.from_dict` builds every part |
| `legaldown.directives`: `PLACEHOLDER_TYPE_PARAMS` | `legaldown.grammar` |
| `legaldown.directives`: `format_value` | `legaldown.syntax` |
| `legaldown.definitions`: `text_fragments` | `legaldown.syntax` |
| `legaldown.definitions`: `DELIMITER_PAIRS` | `legaldown.grammar` |
| `legaldown.markdown`: `FENCE_OPEN_RE`, `HTML_COMMENT_RE`, `LINE_ENDING_RE`, `closes_fence`, `dedent`, `fence_end`, `indent_width`, `strip_text` | `legaldown.syntax` |
| `legaldown.markers`: `MARKER_RE`, `Marker`, `format_marker`, `is_look_alike`, `parse_marker` | `legaldown.syntax` |
| `legaldown.models`: `LIST_KINDS` | `legaldown.grammar` |
| `legaldown.parser`: `FRONTMATTER_RE` | `legaldown.syntax` |
| `legaldown.parser`: `MAX_QUOTE_DEPTH` | `legaldown.grammar` |
| `legaldown.validator.patterns`: `LEGALDOWN_EXTENSIONS`, `IDENTIFIER_RE`, `KNOWN_CURRENCIES`, `VALID_DOC_TYPES`, `VALID_DURATION_UNITS`, `VALID_PLACEHOLDER_TYPES` | `legaldown.grammar` |
| `legaldown.validator.templates`: `DECISION_QUESTION_TYPES`, `QUESTION_TYPES` | `legaldown.grammar` |
| `legaldown.validator.templates`: `block_quotes` | `legaldown.syntax` |
| `legaldown.validator.templates`: `check_choose` | `legaldown.grammar.choose_problem` |
| `legaldown.validator.units`: `find_markers`, `is_include_only` | `legaldown.syntax` |
| `legaldown.parser`: `quote_content` | `legaldown.syntax.quote_blocks` (and `drafting_note_blocks`) |
| `legaldown.parser`: `_layout` | `legaldown.syntax.body_layout(text)` for bare body text (lines from 1 at its start); `Document.layout()` for a parsed document (file lines) |
| your own fence stripping (`FENCE_OPEN_RE`, `closes_fence`, `dedent`) | `legaldown.syntax.code_content` |
| `legaldown.cli`: `_read_answers` | `legaldown.load_answers` |

## What gets checked

The full rule set with severities and examples lives in the specification (§16); this is the map:

| Area | Checks include |
|---|---|
| **Structure** | Heading depth and skipped levels, hardcoded section numbers, missing title, raw HTML other than comments |
| **Directive syntax** | Malformed directives, repeated parameters, parameters a directive does not define, unquoted values that begin with a curly quote, stray `{{` |
| **Cross-references** | `{{ref:}}` targets that do not exist or point at an attachment |
| **Anchors** | Duplicate identifiers, malformed identifiers, auto-generated collisions and lost letters, markers outside an anchor position |
| **Definitions** | Undefined `{{term:}}`, duplicate ids, auto-generated ids that lost letters, missing quoted span, ambiguous quoting, unreferenced definitions |
| **Parties and sides** | Unknown `{{party:}}` / `{{side:}}`, malformed or duplicate names, invalid party types, minimum party and side counts, empty representatives |
| **Values** | Invalid dates, money without currency or with an unknown one, invalid durations and units, undeclared or reserved custom field types |
| **Placeholders** | Malformed ids, invalid types, one blank with two types, currencies, or units, placeholders in structural and format-checked frontmatter fields |
| **Templates** | Malformed question declarations and defaults, placeholders that contradict their declared question, invalid or contradictory conditions, references whose target a condition can remove, identifiers shared by units that can appear together, `{{choose:}}` that misses or invents an answer, definitions inside drafting notes and mistyped `[!DRAFTING]` markers, blanks in a defined term or against Markdown punctuation, fragments included twice; with `--final`, anything left unfilled; on assembly, answers that are invalid, missing, or unknown |
| **Attachments** | Undeclared `{{attach:}}`, duplicate or colliding ids, empty titles, unreferenced attachments |
| **Amendments** | Terms the amended original does not define, definition overrides, empty amendment titles |
| **Metadata** | No frontmatter at all, invalid document type, invalid dates, missing sides, issuer side requirements, empty `supersedes` title, a declared `legaldown` version newer than 0.2 |

Each check reports at the severity the specification assigns it — Error, Warning, or Info.

## Assembly

```python
from legaldown import load_template

template = load_template("nda.lgd")     # reads it, and the files it includes, once
template.questions                      # every question, declared and implicit (§15.2)
template.problems                       # why it cannot be assembled, if it cannot

form = template.form({"forum": "courts", "fee": {"amount": "5000", "currency": "EUR"}})
form.questions     # the questions reached so far, in order: ask these
form.unanswered    # ... of them, with no valid answer and no default
form.blocking      # ... of those, the decisions: assembly cannot run without them
form.diagnostics   # what is wrong with the answers given
form.ready         # assembly can run
form.complete      # ... and nothing is left blank

result = form.assemble()
if result.ok:
    contract = result.output        # the assembled template file
    files = result.files            # assembled fragments and attachment files, by relative path
for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)
```

A template is read once; a form is a snapshot of the interview over it, so a front end asks for a
new form after every answer — a decision opens or closes the questions under it. `parse_template(text)`
(or `Template(text)`, which takes the same `resolve=`) is the same for source text; `template.path` is
the file `load_template` read, and `None` for a template from its text. `load_template` raises `FrontmatterError` for frontmatter that cannot
be read, as `parse` does.

**What a form says.** `questions` are those the assembly reaches given the answers so far: a
decision question when a condition or `{{choose:}}` using it lies in a present unit, a value
question when one of its placeholders does. A question with a default is among them (the default
answers it, and a front end can still offer it). `blocking` are the decision questions without an
answer or default, that a condition or `{{choose:}}` depends on: an unanswered value question
leaves its blank, which the specification allows, but an unanswered decision is not assembled. An answer that is not valid counts as no answer, and
does not stand in for the default: it is reported in `diagnostics` (`answer-invalid`), and so is an
answer to a question the template does not have (`answer-unknown`, a warning). `unused` lists the
questions that have an answer but are not reached; it is advice, since one answer that changes can
make many of them unused. `form.problem("fee")` says why one answer is not valid, and so does
`question.problem(answer)`, before anything is assembled. A form never changes after it is made, and
neither do the questions: a `Question` is a fixed value, a copy of the template's declaration, which
every form of the template shares (treat its `default` and `choices` as read-only).

**Asking a person.** A question can read what a person types, so a terminal, a web form or an agent
need not know the shapes `assemble` takes (money is `{amount, currency}`, a boolean is a boolean):

```python
answers, skipped = {}, set()
while True:
    form = template.form(answers)
    q = next((q for q in form.questions if answers.get(q.id) is None and q.id not in skipped), None)
    if q is None:
        break
    try:
        answer = q.from_text(input(f"{q.prompt or q.id} — enter {q.hint}: "))
    except ValueError as exc:          # says what to enter
        print(exc)
    else:
        if answer is not None:
            answers[q.id] = answer
        elif q in form.blocking:       # a decision cannot be left open
            print("An answer is needed.")
        else:                          # the default applies, or the blank stays
            skipped.add(q.id)
```

`question.hint` is what to type — `yes or no`, `a date, YYYY-MM-DD`, `an amount and a currency, like
5000 EUR`, `one of: courts (State courts), arbitration` — and `question.from_text(text)` turns the
text into the answer, or raises `ValueError` saying what to enter; the empty text is `None`, no
answer. What it returns always passes `question.problem`. It is strict and not locale-aware:
`yes`, `no`, `true`, `false`, `y`, `n` for a boolean; a choice by key or label, in any case (a label
two choices share is ambiguous, and a key comes before a label); `5000 EUR` for money and `30 D`
for a duration, with the amount alone where every placeholder fixes the currency or the unit; no
grouping separators, symbols or `5 000,50`.

**Answers from a file.** `load_answers("answers.yaml")` reads the YAML mapping (`AnswersError` if it
is not one; a date such as `2026-13-45` is not YAML). Plain YAML gets some shapes wrong for a
template: `fee: 5000` is a number and not the string money wants, `forum: State courts` names a
choice by its label, a `yes` is not what a form takes. `template.coerce(answers)` puts right what it
can read without guessing — text as `question.from_text` reads it, a money amount given as a number —
and leaves the rest, a float or an unknown id, for the form to report; it never raises. A form does
not coerce by itself, so that its diagnostics stay the specification's; `legaldown assemble` and
`legaldown questions` do.

```python
answers = template.coerce(load_answers("answers.yaml"))
form = template.form(answers)
```

**The form as data.** `form.as_dict()` is the form as JSON-ready data for a web form, a service or an
agent: `ready`, `complete`, the template's `problems`, the `diagnostics` about the answers (each with
the `question` it is about), and `questions` — those reached first, in order, then the others — each with
`id`, `type`, `label`, `prompt`, `reached`, `blocking`, `state` (`answered`, `default`, `invalid`,
`unanswered`), `answer`, `default`, `problem`, `hint` in words and `accepts` as data: the words of a
boolean, the choices, the currency or unit the placeholders fix, the units there are.
`question.to_text(answer)` is what a person would type for an answer (`yes`, `5000 EUR`, `30 D`),
to show a default or fill in an input; `answer_text` and `default_text` hold it in the data.

**What stops a template.** `template.problems` are the reasons a template cannot be assembled
whatever the answers: a file it includes that cannot be read, a placeholder written across lines, a
translation group, and the Errors of the validator's template rules (`question-invalid`,
`condition-invalid`, `choose-invalid`, the `placeholder-*` rules, `insertion-boundary`,
`def-term-variable`, `drafting-note-def`, `template-fragment-invalid`; §15.7.2 gives assembly a
template that validates, and `condition-reference-unsafe` counts too, for a template that includes
no fragment: the validator cannot see which sections a fragment holds), and a placeholder, outside
a drafting note, that would fill in a directive with a repeated parameter (`directive-duplicate-param`)
or a duration unit §10.5 does not define (`duration-invalid-unit`).
`template.validation` is the full `ValidationResult` of the template read alone, as advice: the
validator does not read the fragments a template includes, so it cannot see a section or a
definition that lives in one. `ready` is false while there are problems, and `form.assemble()` then
returns them as its diagnostics.

Assembly edits the template as written — "no other byte of the template changes" — so its
output is identical to any other conforming implementation's. A template with include fragments
or LegalDown attachment files reads them from beside the file (`load_template`) or through
`parse_template(text, resolve=...)`, a function from a relative path to the file's text; without
one such a template is refused rather than assembled partially (§17.6). The files it
reads are checked as the Full level checks them — no frontmatter, no level 1 heading, and in a
template no includes, and no conditions or drafting notes in a fragment — and a template whose
files fail is refused. [CONFORMANCE.md](CONFORMANCE.md#assembly-157-176) lists what assembly
checks and what it does not.

> **Deprecated:** `assemble(text, answers, load_file=)`, `template_questions` and `needed_questions`
> are replaced by `Template` and `Form`. They still work, unchanged (but for `Question`, below, and
> that a `load_file=` loader is never asked for a path that is absolute or leads out of the template's
> directory, §2.3), and raise a `LegaldownDeprecationWarning`; they are deprecated since 0.4.0 and will be removed in 0.5.0. Two things
> differ in the new API: it asks the questions of an included fragment where its `{{include:}}` is
> (the functions ask them after the body), and it refuses a template that has Errors in the
> validator's template rules (the functions assemble it). `legaldown assemble` follows the new API,
> and puts the shapes of its answers file right (`template.coerce`): `5000 EUR` is accepted for a money
> question (and `fee: 5000`, where its placeholders fix the currency), `no` for a boolean, and surrounding spaces of a text answer are
> dropped, which were `answer-invalid` before. A `Question` is now a fixed value.

## Scope

This implementation claims **Level 1 — Core** (§17.2) and the **Assembly** capability (§17.6):
everything above applies to a single document, in memory, with no filesystem access beyond
reading the file you point it at, and the files an assembled template includes when you supply
them. That covers authoring, editing, CI validation, and assembly of individual documents.

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — every rule it
implements passes, bar seven whose fixtures span several files and are covered by unit tests
instead. The specification (§17.5) requires an implementation to be explicit about the
checks it does not perform, so those are listed in
[CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md) rather than left to be discovered.

## Development

```bash
git clone https://github.com/ForLegalAI/legaldown-validator
cd legaldown-validator
pip install -e ".[dev]"
ruff check .
pytest
```

**CI** runs on demand only, as one job on Python 3.12 (the version releases are built with): lint,
the tests with the specification fixtures corpus, and the build with its metadata check. Start it
with *Actions → CI → Run workflow* on the branch you pick (the `conformance` input switches the
corpus off for a quicker run), or by adding the **`ci` label** to a pull request: it then runs again
on each push to that pull request until the label is removed. Nothing else triggers it, so run it
before merging and before a release.

### Conformance suite

The fixtures corpus lives in the specification repository, so point the harness at a checkout:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Cases for rules outside Core are skipped and named, so the run doubles as the coverage ledger in
[CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md),
which accounts for every skip. CI runs it with the rest of the checks, on demand (below).

Bug reports and pull requests are welcome in
[Issues](https://github.com/ForLegalAI/legaldown-validator/issues); questions about the format
itself belong in the specification repository's
[Discussions](https://github.com/ForLegalAI/LegalDown/discussions).

## License

MIT — see [LICENSE](https://github.com/ForLegalAI/legaldown-validator/blob/main/LICENSE). The LegalDown specification itself is published separately under
CC BY 4.0; it permits implementations to choose their own license.
