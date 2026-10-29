<div align="center">

# legaldown-validator 📐

### The reference implementation of [LegalDown](https://github.com/ForLegalAI/LegalDown)

**Parse, validate, and serialize LegalDown documents, and assemble templates — from the command line or from Python.**

Targets specification **v0.2** · Every diagnostic carries a **stable rule id** · One dependency: PyYAML

</div>

---

## What it does

[LegalDown](https://github.com/ForLegalAI/LegalDown) is an open plain-text standard for legal
documents. This package is the toolkit for working with those documents in code:

📥 **Parses** a `.lgd` file into a typed document model — frontmatter, sections, blocks, parties,
definitions, anchors.

✅ **Validates** it against the specification's rule set (§16): broken cross-references, undefined
terms, circular definitions, duplicate identifiers, malformed party metadata, bad dates and money
values, and more.

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
contract.lgd:24: error: [ref-broken] Broken section reference: 'payment-terms'.
contract.lgd:24: warning: [money-missing-currency] Money directive without currency parameter.

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
legaldown validate --ignore ldv-check-skipped doc.lgd # mute the Warning for a check not performed (§17.5)
legaldown validate --warnings-as-errors doc.lgd      # tighten a CI gate
legaldown validate --final signed-contract.lgd       # nothing left to fill in
legaldown validate --fragment clauses/               # include fragments and attachment files, on their own
legaldown validate --document-root . docs/           # no file reference may lead out of the project, and ../ within it is read
```

| Option | Effect |
|---|---|
| `--format {text,json}` | Output format (default `text`) |
| `--ignore RULE_ID` | Suppress a rule by its stable id — repeatable |
| `--final` | Check a document is ready for signature: a remaining placeholder, drafting note, or template construct is an error (§15.9) |
| `--fragment` | Validate each file as an include fragment or attachment file on its own (§12.2, §12.4), as [`validate(fragment=True)`](#validating-a-fragment) does: what only the including document can settle is reported with `"host_dependent": true` in the JSON output and `(host-dependent)` at the end of the line in text, and does not affect the exit status |
| `--template` / `--no-template` | With `--fragment`: say whether the document that includes the fragments is a template (`validate(fragment=True, template=True / False)`). Without either, a fragment is a template only when it holds a condition or a `{{choose:}}` |
| `--fragment-kind {include,attachment}` | With `--fragment`: what the files are to the document that uses them (`validate(fragment_kind=)`): an `{{include:}}` fragment (the default, §12.2), or a LegalDown attachment file (§12.4), whose frontmatter or level-1 heading is `attachment-has-frontmatter` or `attachment-has-h1` (§16.10). With `--template`, an include fragment is held to what a template's fragment may hold, and an attachment file to holding no `{{include:}}` ([below](#validating-a-fragment)) |
| `--document-root DIR` | The directory every file a document refers to must lie within (§2.3, [`validate(document_root=)`](#file-references-and-the-document-root)): a path that escapes it, as `../` does, is `path-outside-root`. Each document is read from its own directory, which must be within `DIR`. A path that leads out of the document's directory but stays within `DIR` (`../shared/part.lgd`) is read like any other; without `--document-root` it is valid but not read (`ldv-check-skipped` says so), and nothing outside `DIR` is ever read. A path that is absolute or begins with a URI scheme is `path-not-relative` with or without a root |
| `--warnings-as-errors` | Report warnings at error severity |
| `--strict` | Exit non-zero on any diagnostic, not just errors |
| `--quiet` | Drop the trailing summary line |

Directories are searched recursively for `*.lgd`, `*.legaldown`, and `*.legal.md`, and
`legaldown --version` reports the validator version.

A file whose frontmatter cannot be read draws `frontmatter-invalid-yaml` (an Error, at the line of the
problem), and its body is validated all the same; what only the frontmatter could have declared is not
reported.

**Exit codes:** `0` clean · `1` diagnostics found (errors, or any diagnostic under `--strict`) ·
`2` a file could not be read.

**Changed in 0.4.0:** a document that amends another (`amends.file`, §7.5) or declares LegalDown
attachment files (§12.4) is now checked against the definitions those files declare, read from beside
it (never from outside its directory). Before, `legaldown validate` never read them, so a term only the
amended original defines was an Info (`amend-term-unresolvable`) and is now found, and an
`amend-def-override` or `def-duplicate-id` can be reported that was not. A CI check on such documents
can start to fail for that reason: it is the specification's result, and the files must be beside the
document. A file that is not there, or cannot be read, is as if it had not been asked for.

**Changed in 0.5.0 (unreleased):** a document that holds a construct beyond Core (an include paragraph,
an attachment file, `amends.file`, `translations`, `supersedes.file`, an image path) now draws the
Warning `ldv-check-skipped` that §17.5 asks for, for each category of checks not performed ([Scope](#scope));
a CI gate on `--strict` or `--warnings-as-errors` can start to fail for it: `--ignore ldv-check-skipped`.
A document that includes fragments (`{{include:}}` paragraphs, §12.2)
now has them read from beside it, like the files above, and they are checked ([Include
fragments](#include-fragments)): `include-file-missing` at the include of a fragment that is not there,
`include-has-frontmatter`, `include-has-h1` and `include-heading-skip` in the fragment, at its own
line, and, in a template, the parts of `template-fragment-invalid` that read a fragment.
`include-not-legaldown` (an include of a file that is not `.lgd`, `.legaldown` or `.legal.md`) is
reported with no file read at all. A document that was clean can fail for this: a fragment that is
missing, or beside the document under another name, is now an Error, where it was never looked for.
`legaldown validate` names the fragment's file and line (`parts/a.lgd:3`). To validate a loaded
document without reading its fragments, validate it without its path
(`validate(dataclasses.replace(document, path=None))`; `resolve=lambda path: None` says that every file
is missing). `parse(text)` has no path and reads nothing, as before.
Five rules that §17.2 places at Core are now checked ([What gets checked](#what-gets-checked)), and a
CI gate that was passing can fail for them. **A translation without explicit identifiers is now an
Error** (`translation-implicit-id`): a document that links `translations`, and whose `language` differs
from its `authoritative`, one of the languages it links, must give every heading and `{{def:}}` an explicit identifier (§14.2); without
`authoritative`, the same headings and definitions are a Warning (`translation-authoritative-absent`).
**`--strict` now fails a document that keeps its Definitions at the end** (`definition-used-before-declaration`,
an Info): a `{{term:}}` written before the `{{def:}}` it names draws one Info for each definition, at its
first earlier use, and `--strict` counts an Info; to keep that layout, `--ignore
definition-used-before-declaration`. A `language`, `authoritative` or `translations` key that is not a
lowercase ISO 639-1 code (`EN`, `en-US`, `english`) is a Warning (`language-code-invalid`), and so is an
`authoritative` language that is neither the document's `language` nor a `translations` key
(`authoritative-not-declared`), which `--warnings-as-errors` counts. **`Metadata.language` is now `""` for a document that
says none** (it was `"en"`): §3.2 gives `language` no default, so the model holds none, as it holds no `authoritative`;
`empty_document()` says `"en"`, and `serialize` writes `language` only when the model holds one, so writing a document back
does not say a language it never did. Code that read `document.metadata.language` as "en" for such a document should
treat `""` so. Dicts that 0.4's `to_dict` wrote always carry `"language": "en"`, so they now load as declaring English. The text of `ldv-check-skipped` for
`translations` changed: it now names the identifier rules for the linked files, and says whether this
document's own were checked.

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
legaldown questions --document-root . docs/nda.lgd              # a fragment at ../shared/part.lgd is read, within the root
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
LegalDown attachment files are read relative to the template, never from outside its directory
unless `--document-root DIR` (on `assemble` and `questions`) holds them: a path such as `../shared/part.lgd`
that stays within `DIR` is then read, and nothing outside `DIR` is, nor anything a template outside it names.
The ones assembly keeps are written under `-o` at their relative paths — an ordinary document's fragments
that include fragments of their own too (each is read from the directory of the fragment that holds the
include, as `validate` reads it, and written at its own path; the `{{include:}}` lines stay as written). A
circular chain is `include-cycle`, and a chain deeper than 32 fragments, or a walk of more than
10,000 includes and headings, is `ldv-include-limit`: both are Errors, and nothing is assembled. A
template's fragments may not include any (`template-fragment-invalid`), nor may an attachment file;
`include-heading-skip` is the validator's to report, and assembly does not enforce it. `-o` refuses an output
that leads out of its directory, so a file read from `../` is assembled but cannot be written
by the command line (use the library, or keep the fragments below the template). Answer problems are
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
      "line": 24,
      "message": "Broken section reference: 'payment-terms'.",
      "host_dependent": false
    },
    {
      "file": "contract.lgd",
      "rule": "money-missing-currency",
      "level": "warning",
      "line": 24,
      "message": "Money directive without currency parameter.",
      "host_dependent": false
    }
  ]
}
```

`host_dependent` is in every record, so the shape does not depend on the options: it is `false`
unless `--fragment` found what the including document may settle ([below](#validating-a-fragment)).
The text output does not show it. `file` is the file the finding is in: the document, or a fragment it
includes, by its path from the document's (`contracts/parts/a.lgd`) ([below](#include-fragments)).

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
`frontmatter-invalid-yaml` rule — unless you pass `on_frontmatter_error="keep"`: they then read the
rest of the source as the body and keep the error as `document.frontmatter_error`, and `validate`
reports it (an Error, at the line of the problem) together with what is wrong in the body, as
`legaldown validate` does. That is for prose that sits between two `---` lines, say: an author who
meant a thematic break should still be told about the rest of the document. The frontmatter is
then read as empty, so nothing is reported for want of what it might have declared (a title, the
sides, and a party, side, attachment or question the body uses), and the document cannot be
written out: `serialize`, `save`, `to_dict` and `document_to_dict` raise a new `FrontmatterError` (the
message and line of the kept one, which is its `__cause__`), so the text of the block is not lost. Such a
document keeps reporting `frontmatter-invalid-yaml`, and is validated against the empty metadata,
for as long as `document.frontmatter_error` is set, not only when it is parsed: a program that
repairs the metadata in code sets `document.frontmatter_error = None`. The default stays `"raise"`;
`on_frontmatter_error` is keyword-only and is checked before a file is read. The message is one
line, what the YAML reader found wrong; where is the diagnostic's `line`.

`validate(document)` checks it and returns a `ValidationResult`; `validate(document, final=True)`
is the signature-ready check (§15.9), and `validate(document, fragment=True)` checks an include
fragment or attachment file on its own ([below](#validating-a-fragment)). A document that amends another (`amends.file`, §7.5) or
declares LegalDown attachment files (§12.4) is checked against the definitions those files
declare, and one that includes fragments (§12.2) has them checked ([below](#include-fragments)). They
are read from beside the document — `document.path` is where `load` found it — and
never from outside its directory (§2.3); only the definitions each file declares itself are read,
not those it refers to in turn. An original or an attachment file that is not there, is not UTF-8, or has
frontmatter that cannot be read is as if it had not been asked for: no diagnostic of its own (a fragment
that is not there is `include-file-missing`). A document from `parse(text)` has no path,
so there is nothing to read; give `validate` a `resolve=` function, from a relative path to the
file's text (or `None`), to read them from elsewhere — a database, an upload — or to validate a
loaded document without its files, validate it without its path (`dataclasses.replace(document, path=None)`):
`resolve=lambda path: None` answers that no file is there. Such a function is a
`LoadFile`; `file_loader(directory, root=None)` makes one for files on disk, and `parse_template` takes one as
`resolve=` too. Each file is asked for once and parsed once, whichever check wants it. Where those
files may lie is [`document_root`](#file-references-and-the-document-root).

> **Deprecated:** `parse_document` is now `parse` (string; same arguments) or `load` (file),
> `validate_document` is now `validate`, `serialize_document` is now `serialize` (text) or `save`
> (file), and the importer callbacks `import_definitions=` and `import_attachment_definitions=` (and
> their types, `DefinitionsImporter` and `AttachmentDefinitionsImporter`) are replaced by `resolve=`.
> They still work, and calling one raises a `DeprecationWarning`; they are deprecated since 0.4.0
> and will be removed in 0.5.0 (the template functions too: [Assembly](#assembly)). Every deprecation warning legaldown raises is a
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
constants and rules. **Changed in 0.4.0:** the lexer, the fragments, the definition readers, `render_block`, the dict
factories and the like moved out of `legaldown.__all__`, so `from legaldown import *` no longer
brings them. Imported by name they still import from `legaldown`, as the same objects and without
a warning, so code written for earlier releases keeps working; new code imports them from their
home (the table at the end of [Tooling API](#tooling-api)). Changes to the API are listed in the notes of each
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
| `diagnostics` | `Diagnostic(rule, level, message, line, file, host_dependent)` — the authoritative record; `host_dependent` is true only for a finding of `validate(fragment=True)` that the including document may settle ([Validating a fragment](#validating-a-fragment)) |
| `is_valid` | `True` when no Error-level diagnostic was reported |
| `errors` / `warnings` / `infos` | Message strings by severity, taken from `diagnostics` |
| `rules(level=None)` | Set of rule ids present, optionally filtered by severity |
| `index` | A `DocumentIndex`. Values and markers in it are as written: one that is invalid is reported in `diagnostics` too, so check `is_valid` before relying on them: |

| `result.index.…` | Contents |
|---|---|
| `sections`, `section_lookup` | Numbered section index, as `SectionIndexEntry(title, identifier, path, level, number, alternative)`, in document order and by identifier (a paragraph's or item's anchor too, to its section); resolves `{{ref:}}` targets. Numbers count from the shallowest heading level, and a level a heading skips counts as 1 (`#`, `###`, `##` → 1, 1.1.1, 1.2), so no two sections share a number except alternatives and what they contain (§15.8); an entry's `alternative` is true for a section that is an alternative to its preceding sibling — the section just before it at its level under the same parent, with the same identifier, never present together with it (§15.4) — and so shares its number (§15.8) |
| `definition_lookup`, `party_lookup`, `side_lookup`, `attachment_lookup` | Resolved display text |
| `values` | The field-spec values the checks met, as written, as `InlineValues`: `dates`, `money`, `durations`, `fields`, `placeholders` (those of the frontmatter too; one with malformed arguments is not among them) |
| `blanks` | The document's blanks, a template's or not, by placeholder id in order of first occurrence outside a drafting note (an occurrence in a note is no part of a blank, and an id only notes use has none, [#112](https://github.com/ForLegalAI/legaldown-validator/issues/112); `Template.questions` still asks it as a `text` question, and `index.values.placeholders` lists the occurrence as written): `Blank(id, type, fixed, in_frontmatter, consistent)` — `type` the type the validator settled on: the effective type of the first occurrence with a valid one (§10.7, §15.2; `text` when none is written), or `None` when no occurrence has a usable type (each is written with an invalid one, or the id is a decision question's); `fixed` the currency of a money blank or the unit of a duration blank that every occurrence fixes (`""` when one fixes none, or the blank is not `consistent`); `consistent` false when its occurrences have different types or fix two currencies or units (placeholder-type-inconsistent); `in_frontmatter` whether one occurrence is in the frontmatter (§3.10) |
| `is_template` | Whether the document is a template (§15.1): it declares `questions`, carries a condition, or holds a `{{choose:}}` — or, for a fragment, what `validate(fragment=True, template=)` was told ([below](#validating-a-fragment)); `placed_markers` follows the same decision |
| `placed_markers` | The markers in body text that apply (§5.7, §15.3), in document order: `PlacedMarker(section, block, fragment, offset, source, identifier, condition, field, item, include_only, line)` — in fragment `fragment` of `block_fragments(block)` (`legaldown.syntax`), at `offset`, which is the block's `field` (`text`, or `suffix` after a lifted `{{ref:}}`/`{{term:}}`); `item` is the list item it marks, counted in pre-order over all the list's items, nested and empty ones included, as `list_fragments` counts them; `identifier` is `""` where it does not apply (an include-only paragraph, §12.2). Identifiers and conditions are as written: check `is_valid` before relying on them |

`is_template(document)` (top-level) gives the template decision without validating (§15.1).
A renderer builds from these decisions rather than re-deriving them, reading the source with
the validator's own helpers ([Tooling API](#tooling-api)).
[`legaldown-render`](https://github.com/ForLegalAI/legaldown-render) is built this way.

### Validating a fragment

An include fragment (§12.2) or a LegalDown attachment file (§12.4) is body-only: the document that
includes it declares the parties, the sides, the attachments and the questions it uses, and defines
the sections and terms it refers to. Validated as a complete document it draws `frontmatter-absent`
and an Error for every one of those, and a reader cannot tell "the host has not declared it" from
"the fragment is malformed". `fragment=True` tells them apart:

```python
from legaldown import load, validate

result = validate(load("clauses/payment.lgd"), fragment=True)

for d in result.diagnostics:
    print(d.line, d.rule, "(host)" if d.host_dependent else "")
```

```python
validate(document, *, final=False, fragment=False, fragment_kind=None, template=None, resolve=None,
         document_root=None) -> ValidationResult
Diagnostic(rule, level, message, line=None, file="", host_dependent=False)
```

- Nothing is checked of the metadata: a fragment is not asked for a title, parties or sides, and
  no `frontmatter-absent` is reported. Its first heading may be at any level (§4.1 applies to the
  combined document); the skips after it are reported.
- A finding that the including document may settle is reported with the rule id and the level it has
  in a complete document, and has `host_dependent` set: a `{{party:}}`, `{{side:}}` or `{{attach:}}`
  it does not declare, a condition or `{{choose:}}` on a question it does not declare
  (`condition-invalid`, `choose-invalid`), a `{{ref:}}` to an identifier or a `{{term:}}` that the
  fragment itself does not hold (`ref-broken`, `term-undefined`), and a definition it declares and does
  not reference (`def-unreferenced`: the host may). Set those aside while the host is not known; take
  them seriously when the fragment is validated in it.
- Anything else is the fragment's own and is not marked: a malformed directive, a target that is not
  an identifier (`{{ref: Bad Id}}`), a `{{ref:}}` with no target, a malformed condition, a heading that
  skips a level, and so on. A `{{ref:}}` to a section or anchor of the fragment itself, and a
  `{{term:}}` of its own `{{def:}}`, resolve and are not reported.
- A fragment that has frontmatter draws `include-has-frontmatter` (§16.11), at the line of its first
  key (the opening `---` when it has none), and a level-1 heading draws `include-has-h1`, at the
  heading, each an Error. Its metadata is then not read at all, and the lines of everything else are
  the file's. That holds for a block whose YAML cannot be read, too (`load(path,
  on_frontmatter_error="keep")`; the CLI does): it is `include-has-frontmatter`, at the line of the
  problem, and not also `frontmatter-invalid-yaml`.
- What the file is to its host is `fragment_kind=`: `"include"` (the default; an `{{include:}}`
  fragment, §12.2) or `"attachment"` (a LegalDown attachment file, §12.4). An attachment file's
  frontmatter and level-1 heading are `attachment-has-frontmatter` and `attachment-has-h1` (§16.10),
  where an include fragment's are `include-has-frontmatter` and `include-has-h1` (§16.11); the other
  findings are the same. `fragment_kind=` without `fragment=True`, or any other value, raises
  `ValueError`; on the command line it is `--fragment-kind {include,attachment}`.
- A fragment cannot tell whether the document that includes it is a template (§15.1): a blank or a
  drafting note is legal in any document. By default it is a template exactly when it holds a
  condition or a `{{choose:}}`, as any document is, with no `questions` to count. Say what the host is
  with `template=`: `True` checks the fragment as part of a template (a blank whose id YAML reads as a
  boolean or null, `{{placeholder: no}}`, is a `question-invalid`; a preamble paragraph's condition is
  placed, §5.7; and what §15.3 asks of a template's fragment, below). `False` says it is not one, so it declares no
  questions either: a condition or `{{choose:}}` that names one is then a `condition-invalid` or
  `choose-invalid` of the fragment's own, not `host_dependent`, and the fragment is no template.
  `None`, the default, leaves it to the fragment. `index.is_template` and `placed_markers` follow
  that one decision. `template=` without `fragment=True` raises `ValueError`. A blank only in a
  drafting note is never checked for such an id in any document: assembly removes the note first
  (§15.7.2). Nor does an occurrence in a note take part in the blank's type, currency or unit
  ([Blanks in drafting notes](CONFORMANCE.md#blanks-in-drafting-notes-156-1572)).
- With `template=True` (and only then: a conditional fragment is just inferred to be a template), an
  include fragment is held to what §15.3 asks of a template's fragments, as assembly holds it: no
  condition (it goes on the fragment's `{{include:}}` paragraph), no drafting note, no `{{include:}}`,
  and an explicit identifier on every heading, each offender one `template-fragment-invalid` at its
  line. An attachment file (`fragment_kind="attachment"`) may hold conditions and notes of its own,
  and is asked only for no `{{include:}}` (§16.12). Fragment mode reads no file: the includes of the
  fragment are judged by their path (`include-not-legaldown`, `path-not-relative`), not followed (the document that includes the fragment follows them).
- `legaldown validate --fragment` shows a flagged finding with `(host-dependent)` at the end of its
  line, and its exit status ignores them (also under `--strict`): a fragment that only uses what its
  host declares or defines exits `0`, and one with a finding of its own exits `1`. The summary line
  counts them apart (`0 error(s), 0 warning(s), 0 info(s); 3 host-dependent`) to agree with it.
- The paths a fragment names are its own, whoever includes it: its `{{include:}}` and image paths are
  checked (`path-not-relative`, and `path-outside-root` with a `document_root`) and are never
  `host_dependent`. Its frontmatter is never read, so there is no `attachments`, `amends`, `supersedes`
  or `translations` of the fragment's to check.
- `host_dependent` is `False` in every other result: for a complete document there is no host, and a
  broken reference is simply an Error. It takes part in a `Diagnostic`'s equality. Assembly's
  diagnostics never carry it.

### Include fragments

A paragraph that holds only an `{{include:}}` (and at most its marker) is replaced by the fragment
it names (§12.2). When `validate` can read files (a loaded document reads from beside it; or give
`resolve=`), it reads each such fragment once and reports, in the fragment's own file and at the
line in it (`Diagnostic.file` is the path from the document's directory, `parts/a.lgd`, or `../shared/a.lgd`):

- `include-file-missing` at the include of a fragment that is not there, or that cannot be read
  because its path holds a backslash or a null byte, which a loader is never asked for and assembly
  refuses the template for (an absolute path or one with a URI scheme is `path-not-relative` alone, and
  a path that leaves a `document_root` is `path-outside-root` alone; a path that only leads out of the
  document's directory (`../x.lgd`) is valid: with a `document_root` that holds it, it is read like any
  other; without one it is skipped, not read, and the Warning of §17.5 says so);
- `include-cycle` (§16.11) at the include that closes a circular chain, in the file that holds it
  (`parts/b.lgd:3` for a document that includes `a`, which includes `b`, which includes `a`); a document that
  includes itself is one. The includes of a fragment are followed (outside a template, where §12.2 allows
  them) and judged as the document's are, from the fragment's directory and at their line in it; the walk stops
  at a depth of 32 fragments and 10,000 includes and headings, and `ldv-check-skipped` says so (assembly
  of an ordinary document follows them too and, instead of saying so, refuses with the Error
  `ldv-include-limit`: a fragment of more than 10,000 headings is refused by it as well);
- `include-has-frontmatter` and `include-has-h1` (§16.11), `include-has-frontmatter` also for a block
  whose YAML cannot be read;
- `include-heading-skip`: the headings of every fragment are placed where its include paragraph is,
  and one that skips a level (§4.1) is reported at the heading, in its file; a heading of the document
  that follows one of a fragment's is reported at its own line; a `heading-skip` of the document
  that a fragment read between the headings fills (`# One`, an include of `## Deep`, `### Three`) is
  not reported, as §4.1 judges the combined document, unless an include before that heading is not
  known for certain (missing, not LegalDown, unreadable, conditional, itself reported as not a
  fragment, or with something it includes not followed). In a template this is judged under every
  combination of answers to the questions the presence of an include names (§15.3, §16.11), with every unit
  a combination makes absent removed: a skip is reported once, under the first combination that makes it
  (`It is so when forum=arbitration, nda=false.`), and a `heading-skip` of the document is dropped only
  if the fragments fill the level in every combination the heading is there for. At most 12 questions and
  4,096 combinations are tried; past that it is one pass with every include present, and
  `ldv-check-skipped` says so ([CONFORMANCE.md](CONFORMANCE.md#include-fragments-122-1611-1612));
- `include-anchor-duplicate` at the later declaration of an explicit section identifier that the combined
  document declares twice (a fragment's heading in its file, a heading of the document at its own line), unless
  the two can never be there together (alternatives, §15.4); a fragment included twice is reported once, at its
  heading. Auto-generated identifiers are not compared across files;
- in a template, `template-fragment-invalid` for what §15.3 forbids in a fragment (a condition, a
  drafting note, an `{{include:}}`, a heading without an explicit identifier), one finding for each
  offender, and for an `{{include:}}` in a LegalDown attachment file.

`include-not-legaldown` needs no file: an include paragraph whose path does not end in `.lgd`,
`.legaldown` or `.legal.md` is reported at its line (Core), and the path is never read. A path
already reported as `path-not-relative` or `path-outside-root` is that finding alone. A fragment's own
includes are followed by `validate` (not by `validate(fragment=True)`, which reads no file; the
document that includes it does). Assembly makes the same checks of the
same files with the same code, and still refuses an include in a fragment of a document that is not a
template (it does not assemble it), so `Template.problems` and `validate` never judge a file two ways.

### File references and the document root

A document names other files by a path relative to itself (§2.3): `{{include:}}`, an image's path,
and `attachments[].file`, `amends.file`, `supersedes.file` and `translations` in the frontmatter.
`validate` checks the text of each path, at Core level, and reads no file:

```python
validate(document, document_root="/srv/contracts")
```

- `path-not-relative` (Error): the path is absolute (`/etc/x`, `//host/x`) or begins with a URI scheme
  (`https://…`, `file://…`, `data:…`, a drive letter `C:/x`). It is reported with or without a root.
- `path-outside-root` (Error): with a `document_root`, the path leads out of it, as `../../x` does.
  Without one — there is no default root — the check does not run, and `../shared/definitions.lgd` is
  valid. A loaded document is read from the directory of its `path`, which must be within the root
  (otherwise the document is reported once as outside it); a parsed one is taken to be at the root.
  The comparison is of names alone: no file is looked at, and a symbolic link is not followed.
- Whether the file exists is Full (§17.4) and is not checked by this. Reading is a separate matter
  ([#109](https://github.com/ForLegalAI/legaldown-validator/issues/109)): given a `document_root`,
  `../shared/x.lgd` from a document below it is read like any other file, for `{{include:}}`,
  `attachments[].file` and `amends.file` (a `resolve=` is asked for it as `../shared/x.lgd`), by `validate`
  and by assembly (`load_template(path, document_root=)`, `parse_template(text, document_root=, path=)`),
  and nothing outside the root is, whatever the path says, nor anything a document outside the root
  names. Without a root `../shared/x.lgd` is valid and **not read**: no loader is asked for it, an
  `{{include:}}` of it is not an `include-file-missing`, and `ldv-check-skipped` says it was not read.
  `file_loader(directory, root=)` is the loader on disk that keeps to the root.

A reference-style image (`![alt][r]`, `![alt][]` or `![alt]`, with `[r]: path`) is judged by the destination
of the definition it names, once, at the line where the definition is written. [CONFORMANCE.md](CONFORMANCE.md)
gives the details, what counts as an image included.

### Reading and editing the document model

`load` and `parse` return a `Document` of plain dataclasses — `Metadata` (with `Amends` for
`amends` and an object `supersedes`, and `Attachment`), `Section`, `Block` and `ListItem`, `Side`,
`Party` (with `Representative` and `CustomField`) — that you can inspect, edit, and write back out:

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
file is not what you want to write (an HTTP response, a database). Both raise `FrontmatterError`
for a document parsed with `on_frontmatter_error="keep"` whose frontmatter could not be read, and
`save` leaves the file as it is.

Content before the first heading — typically the sentence identifying the parties — is the
document's preamble (§4.4): it is unnumbered, so it lives in `document.preamble` rather than in
`sections`. `document.iter_blocks()` walks every body block, preamble first.

A `Section`'s `identifier` is the explicit `{#id}` written after its heading, or `""` when
it has none. The identifiers the validator generates (§5.3, §5.5) are not written back into the
model: read them from `result.index.sections`.

`document.to_dict()` and `Document.from_dict(data)` round-trip the model through JSON-friendly
structures (missing fields take their defaults), except the fields that describe the parsed source
rather than the document, `Metadata.not_line_editable`, `Metadata.frontmatter_absent`,
`Document.source_map`, `Document.path` and `Document.frontmatter_error`. `to_dict()` raises that
error, when there is one: the empty metadata of such a document would not say what its source says. A blank document is `Document()`; `empty_document()`,
still importable from `legaldown`, is not one but a starter contract with two sides and two sections.

Each diagnostic names its `line` (from 1) and its `file` (the document's `filename`), as §16.9
requires: the line of the directive, marker, heading or block it is about, or of the
frontmatter key — for a missing key, the key that holds it, or the frontmatter's first. Lines
come from the `source_map` that `load` and `parse` give a document. A document built from a dict
has none, and one changed after parsing no longer fits its map: their diagnostics have no line
(`None`) rather than a stale one. `FrontmatterError.line` is the line of YAML that cannot be
read, and the line of the `frontmatter-invalid-yaml` diagnostic (the opening `---` when the reader
gives none). `render_block()` (`legaldown.syntax`) renders a single block when you are driving your own
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
first paragraph, then later paragraphs, headings, link reference definitions (`link_definition`
blocks, below; any before the first paragraph are no text, so that paragraph is still the first),
nested lists (a list block of their own kind), code, quotes, tables and raw HTML. An empty item
holds none. A paragraph in an item is always of kind `paragraph`: a directive in it stays in its
text. A heading in an item or a quote is a `heading` block, not a section: its `text` without
the `#` syntax, and its `level` (1–6; `level` is 0 on every other block). Written outside an
item, where it would be a section's heading, a `heading` block becomes paragraph text with a
backslash before it. `item_text(item)` gives an item's first paragraph's text (link reference
definitions before it are no text), and `render_item(item)` its content as written after its
marker (both in `legaldown.syntax`); a string item in `Document.from_dict` (as models before
0.3 held them) is read as such content.
A nested item's condition applies within those of the items it is nested in (§15.3).

A quote block keeps its `text`: its content, one line per source line without the `>` marker.
The validator and assembly read that content as blocks, as they read an item's, so code and raw
HTML in a quote hold no directive.

A link reference definition (CommonMark §4.7), `[label]: destination "title"`, is a
`link_definition` block, at the top level and in an item or a quote too. Its `label` is as
written (without the spaces around it), its `target` the destination without `<>`, and its
`title` the title without its quotes or parentheses (`""` for none), each raw: backslash
escapes and character references are not decoded, so the model keeps what was written.
`title` is a field of every `Block` (`""` on the other kinds), so `to_dict()` has
`"title": ""` on each block, and a dict without it loads. As in CommonMark, the definitions
a paragraph *opens with* are read, each a block of its own, and what is left is the
paragraph: `a` then `[x]: u` on the next line is one paragraph. A definition that holds a
`{{` directive is paragraph text instead, as its destination or title is not final until
assembly fills the directive in, and the validator sees the line as written. A paragraph that
was only definitions leaves no `paragraph` block, so a document that had `[x]: u` as
a `paragraph` now has a `link_definition` (a **behaviour change**), and `serialize` writes each definition
as a paragraph of its own, separated by blank lines. A model's `paragraph` that would read as a
definition when written is written with a backslash before it, to stay a paragraph.
`collect_link_definitions(document)` (`legaldown.syntax`) gives the document's definitions
by label, the first of a label winning, as CommonMark has it. See
[CONFORMANCE.md](CONFORMANCE.md) for how this differs from CommonMark.

Tables follow GFM: a table needs a delimiter row with one cell per header, rows take the
header's width, and a `|` inside a cell — a code span's included — is written `\|`. A
table block's `headers` and `rows` hold the cell text with those escapes removed, and
`align` holds each column's alignment (`"left"`, `"right"`, `"center"`, or `""`).

A paragraph's `text` (a definition's, and a ref's or term's `prefix` and `suffix`, too) keeps
its lines, joined with `\n` and without their indentation, as CommonMark reads them: a line
ending in `\` or in two spaces is a hard line break, and each other line break a soft one,
which a renderer shows as a space. It is written
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
| `IDENTIFIER_RE`, `RESERVED_VALUE_TYPES`, `VALID_DOC_TYPES`, `PARTY_TYPES`, `KNOWN_CURRENCIES`, `ISO_639_1_CODES`, `DELIMITER_PAIRS` | Identifier format (§5.3), reserved value-type names, document types, party types, ISO 4217 codes, ISO 639-1 codes (§3.2), accepted definition delimiters (§7.2) |
| `VALID_PLACEHOLDER_TYPES`, `DURATION_UNITS`, `VALID_DURATION_UNITS` | Placeholder types and duration units (§10); `DURATION_UNITS` is in the order the specification lists them |
| `LIST_KINDS`, `MAX_SECTION_LEVEL`, `MAX_QUOTE_DEPTH`, `MAX_LIST_DEPTH` | Block kinds that are lists; the deepest heading level (5); how deep the parser reads quotes and lists |
| `VALUE_QUESTION_TYPES`, `DECISION_QUESTION_TYPES`, `QUESTION_TYPES`, `DRAFTING_MARKER`, `FINAL_CHECK_RULES` | Template question types (§15.2); the drafting note's first line (§15.6); the rule ids of the final check (§15.9) |
| `IMPLEMENTATION_RULE_PREFIX`, `SKIPPED_RULE` | The prefix (`"ldv-"`) of the rule ids this implementation defines for checks of its own (§16.1), and the one it reports for a check category it did not perform (`"ldv-check-skipped"`, a Warning, §17.5) |
| `slugify_identifier(value, *, fallback="section")`, `format_section_number` | The §5.3 identifier of a heading or term text, and `fallback` where the text yields none (`""` tells that case apart); a dotted section number |
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
| `collect_link_definitions(document)` → `{label: LinkDefinition(url, title)}`, `normalize_label(label)` | The link reference definitions of a document (`link_definition` blocks, CommonMark §4.7), in an item or a quote too, by label, the first of each label winning: the key is `normalize_label(label)`, the label with each run of whitespace one space and case-folded, so look a label up with `definitions[normalize_label("Foo  Bar")]`. `url` and `title` are raw as the block holds them (backslash escapes and character references not decoded); `title` is `""` for none. Derived on each call; a definition with a directive in it is paragraph text and is none. `locate_link_definitions(document)` → `{label: PlacedLinkDefinition(section, block, ordinal, definition)}` is the same map with the `link_definition` blocks themselves and where each is: the section (`None`: the preamble) and index of its top-level block, and its `ordinal` among that block's definitions, nested ones included, in document order |
| `find_definition_anchors(text)` → `DefinitionAnchor` | Every `{{def:}}` in a text, with the quoted term it anchors and where that term starts (§7.2) |
| `render_block(block)`, `render_item(item)` | The other way: a block written as LegalDown source, and a list item's content as written after its marker, as `serialize` writes them |
| `Quote`, `block_quotes`, `is_drafting_note(block)` | The block quotes in a block and whether each is a drafting note; whether a quote block is one (§15.6) |
| `quote_blocks(block, *, depth=0)`, `drafting_note_blocks(block, *, depth=0)` | The blocks a block quote holds, as the validator reads them, as copies you may change; a drafting note's without its `[!DRAFTING]` marker (§15.6). `depth` is how many list items and quotes the quote is in, the count before entering it (a quote in a list item is at 1); past `MAX_QUOTE_DEPTH` the validator reads a quote as one text, so you get one paragraph of its text as written |
| `code_content(block)` → `CodeContent(info, text, fenced)` | A code block read as CommonMark reads it: the info string, and the code without its fences or indentation |
| `FRONTMATTER_RE`, `LINE_ENDING_RE`, `HTML_COMMENT_RE`, `comment_spans`, `strip_comments`, `FENCE_OPEN_RE`, `closes_fence`, `fence_end`, `dedent`, `indent_width`, `strip_text`, `normalize_label` | The Markdown rules the reading is built on: frontmatter, line endings, comments, fenced code, indentation. To find or remove comments in text you do not trust, use `comment_spans(text)` and `strip_comments(text)`: they read comments as `HTML_COMMENT_RE` does, in linear time, where the regex is quadratic on many unclosed `<!--` |

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
| **Definitions** | Undefined `{{term:}}`, duplicate ids, auto-generated ids that lost letters, missing quoted span, ambiguous quoting, unreferenced definitions, circular definitions (a definition's paragraph names a term whose paragraph leads back to it; one Warning for each such set, §7.2), a `{{term:}}` written before the definition it names (Info, once for each definition) |
| **Parties and sides** | Unknown `{{party:}}` / `{{side:}}`, malformed or duplicate names, invalid party types, minimum party and side counts, empty representatives |
| **Values** | Invalid dates, money without currency or with an unknown one, invalid durations and units, undeclared or reserved custom field types |
| **Placeholders** | Malformed ids, invalid types, one blank with two types, currencies, or units, placeholders in structural and format-checked frontmatter fields |
| **Templates** | Malformed question declarations and defaults, placeholders that contradict their declared question, invalid or contradictory conditions, references whose target a condition can remove, identifiers shared by units that can appear together, `{{choose:}}` that misses or invents an answer, definitions inside drafting notes and mistyped `[!DRAFTING]` markers, blanks in a defined term or against Markdown punctuation, fragments included twice, what a fragment or attachment file of a template may not hold (a condition, a drafting note, an include, a heading without an identifier); with `--final`, anything left unfilled; on assembly, answers that are invalid, missing, or unknown |
| **Includes** | An include of a file that is not LegalDown, a fragment that is missing, with frontmatter or a level-1 heading, or whose headings skip a level where it is spliced in; with a template, a fragment that holds what §15.3 forbids |
| **Attachments** | Undeclared `{{attach:}}`, duplicate or colliding ids, empty titles, unreferenced attachments |
| **Amendments** | Terms the amended original does not define, definition overrides, empty amendment titles |
| **Metadata** | No frontmatter at all, invalid document type, invalid dates, missing sides, issuer side requirements, empty `supersedes` title, a declared `legaldown` version newer than 0.2, a `language`, `authoritative` or `translations` key that is not an ISO 639-1 code, an `authoritative` language the document does not declare, and in a document that links translations the headings and definitions without an explicit identifier (an Error in a translation, a Warning when `authoritative` is absent) |
| **Beyond Core** | `ldv-check-skipped` (Warning, this implementation's own id, §16.1): for each category of checks that a construct in the document calls for and the validation did not perform, as §17.5 requires, once and listing exactly what was not done ([below](#scope)) |

Each check reports at the severity the specification assigns it — Error, Warning, or Info. The one
exception is the Warning of this implementation's own, `ldv-check-skipped` ([Scope](#scope)).

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

`form.assemble()` returns an `AssemblyResult`: `ok` (no Error), `output`, `files` (an emptied
fragment is `""`, written as zero bytes) and `diagnostics`; nothing is assembled while an Error
stands, and `output` and `files` are then empty. `AssemblyError` is raised, rather than a guess
made, if the template's source cannot be mapped onto its parsed structure — never expected for a
template the parser reads, so report it if you meet it.

A template is read once; a form is a snapshot of the interview over it, so a front end asks for a
new form after every answer — a decision opens or closes the questions under it. `parse_template(text)`
(or `Template(text)`, which takes the same `resolve=`) is the same for source text, and both take `document_root=` (the directory every file the template names must lie within: a `../` path that stays within it is read, and its key in `result.files` keeps the `../`) and `path=` (where the template lies, so that its depth below the root is known; without it, at the root, where every `../` leaves); `load_template(path, document_root=)` reads the files from beside `path` and under the root; `template.path` is
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
need not know the shapes an answers set holds (money is `{amount, currency}`, a boolean is a boolean):

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
`id`, `type`, `label`, `prompt`, `declared`, `choices`, `currency`, `unit`, `reached`, `blocking`,
`state` (`answered`, `default`, `invalid`, `unanswered`), `answer`, `default`, `problem`, `hint` in
words and `accepts` as data: the words of a
boolean, the choices, the currency or unit the placeholders fix, the units there are.
`question.to_text(answer)` is what a person would type for an answer (`yes`, `5000 EUR`, `30 D`),
to show a default or fill in an input; `answer_text` and `default_text` hold it in the data. Each problem
and diagnostic has `rule`, `level`, `message`, `line` and `file`: the fragment or attachment file it is
in, relative to the template, or `""` for the template itself (so is the `problems` list of
`legaldown questions --format json`).

**What stops a template.** `template.problems` are the reasons a template cannot be assembled
whatever the answers: a file it includes that cannot be read, a placeholder written across lines, a
translation group, and the Errors of the validator's template rules (`question-invalid`,
`condition-invalid`, `choose-invalid`, the `placeholder-*` rules, `insertion-boundary`,
`def-term-variable`, `drafting-note-def`, `template-fragment-invalid`; §15.7.2 gives assembly a
template that validates, and `condition-reference-unsafe` counts too, for a template that includes
no fragment: the validator cannot see which sections a fragment holds), and a placeholder, outside
a drafting note, that would fill in a directive with a repeated parameter (`directive-duplicate-param`)
or a duration unit §10.5 does not define (`duration-invalid-unit`).
`template.validation` is the full `ValidationResult` of the template, as advice: the validator reads
the fragments a template includes for what they may hold, but does not join them to the template, so it
cannot see a section or a definition that lives in one (a `{{ref:}}` into a fragment is `ref-broken`).
A finding in a fragment names it in `file` and its `line`; `template.problems` has the same findings
of the files assembly read, once. `ready` is false while there are problems, and `form.assemble()` then
returns them as its diagnostics.

Assembly edits the template as written — "no other byte of the template changes" — so its
output is identical to any other conforming implementation's. A template with include fragments
or LegalDown attachment files reads them from beside the file (`load_template`) or through
`parse_template(text, resolve=...)`, a function from a relative path to the file's text; without
one such a template is refused rather than assembled partially (§17.6). The files it
reads are checked as the Full level checks them — no frontmatter, no level 1 heading, and in a
template no includes (outside a template a fragment's includes are followed and assembled, each file at its
own path), and no conditions or drafting notes in a fragment — one diagnostic for each
offender, with its `file` and `line`, and a template whose
files fail is refused. [CONFORMANCE.md](CONFORMANCE.md#assembly-157-176) lists what assembly
checks and what it does not.

> **Deprecated:** `assemble(text, answers, load_file=)`, `template_questions` and `needed_questions`
> are replaced by `Template` and `Form`. They still work, unchanged (but for `Question`, below, and
> that a `load_file=` loader is never asked for a path that is absolute or leads out of the template's
> directory, §2.3, and takes no `document_root`), and raise a `LegaldownDeprecationWarning`; they are deprecated since 0.4.0 and will be removed in 0.5.0. Two things
> differ in the new API: it asks the questions of an included fragment where its `{{include:}}` is
> (the functions ask them after the body), and it refuses a template that has Errors in the
> validator's template rules (the functions assemble it). `legaldown assemble` follows the new API,
> and puts the shapes of its answers file right (`template.coerce`): `5000 EUR` is accepted for a money
> question (and `fee: 5000`, where its placeholders fix the currency), `no` for a boolean, and surrounding spaces of a text answer are
> dropped, which were `answer-invalid` before. A `Question` is now a fixed value.

## Scope

This implementation claims **Level 1 — Core** (§17.2) and the **Assembly** capability (§17.6):
everything above applies to a single document. Beyond the file you point it at, validation reads
the definitions declared by the document it amends and by its LegalDown attachment files
(§7.5, §12.4), and the include fragments (§12.2) to check what they must hold, and assembly the include
fragments and LegalDown attachment files of a template —
each from beside the document, never from outside its directory, or through `resolve=`. That
covers authoring, editing, CI validation, and assembly of individual documents.

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — every rule it
implements passes, bar three (the amendment rules) whose fixtures are marked for the Full level,
and are covered by unit tests instead (the include cases it implements run with a loader, asserting
the file and the line). One case is run against another entry than its own, the translation of
`translation-implicit-id`, as a Core validator reads the document it is given
([CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md)). The specification (§17.5) requires an implementation to be explicit about the
checks it does not perform, so those are listed in
[CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md) rather than left to be discovered.

**Checks that were not performed are said in the output.** A document that holds a construct whose
checks lie beyond Core — an `{{include:}}` paragraph, an attachment file, `amends.file`, `translations`,
`supersedes.file` (the object form), an image path — draws a Warning, `ldv-check-skipped`, for each
such category: one, at the first construct, naming what this validation did not do (§17.5). Which
checks are named depends on the validation: a fragment that was read (a document with a path, or
`validate(resolve=)`) is not listed as unread, and neither are the `include-cycle` and
`include-anchor-duplicate` checks made over what it includes; but the combined-document checks of
§16.11 (a reference or a term into a fragment, [#117](https://github.com/ForLegalAI/legaldown-validator/issues/117))
are never performed, so a document with an include always draws it. The id has the prefix `ldv-` that §16.1 reserves for an implementation's own checks
(`legaldown.grammar.IMPLEMENTATION_RULE_PREFIX`, `SKIPPED_RULE`; assembly has one more, the Error `ldv-include-limit`). It is a Warning: the exit status is
unchanged, and `--strict` and `--warnings-as-errors` count it, so a CI gate that does should
`--ignore ldv-check-skipped` once it has read what it says.

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
which accounts for every skip. CI runs it with the rest of the checks, on demand (above).

Bug reports and pull requests are welcome in
[Issues](https://github.com/ForLegalAI/legaldown-validator/issues); questions about the format
itself belong in the specification repository's
[Discussions](https://github.com/ForLegalAI/LegalDown/discussions).

## License

MIT — see [LICENSE](https://github.com/ForLegalAI/legaldown-validator/blob/main/LICENSE). The LegalDown specification itself is published separately under
CC BY 4.0; it permits implementations to choose their own license.
