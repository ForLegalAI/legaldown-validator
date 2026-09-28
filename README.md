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

### Assembling a template

```bash
legaldown assemble template.lgd --answers answers.yaml          # the document to stdout
legaldown assemble template.lgd --answers answers.yaml -o out/  # with its fragments and attachment files
```

The answers set is a YAML mapping of question ids to answers (§15.7.1). Include fragments and
LegalDown attachment files are read relative to the template, never from outside its directory;
the ones assembly keeps are written under `-o` at their relative paths. Answer problems are
reported as `answer-invalid`, `answer-missing` and `answer-unknown` (§16.12) on stderr, and
nothing is written when there is an Error. Exit codes are as for `validate`.

### JSON output

`--format json` emits the structured shape described in §16.9 — ideal for CI annotations, editor
integrations, and dashboards:

```json
{
  "legaldown_spec": "0.2",
  "validator_version": "0.2.0",
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
from legaldown import parse_document, validate_document, serialize_document

document = parse_document(open("contract.lgd").read(), filename="contract.lgd")
result = validate_document(document)

for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)

if result.is_valid:                      # no Error-level diagnostics
    print(serialize_document(document))
```

`parse_document` raises `FrontmatterError` (a `yaml.YAMLError` and a `ValueError`) when the
frontmatter cannot be read — the `frontmatter-invalid-yaml` rule, which `validate_document`,
given a parsed document, cannot report.

### Working with the result

Validating a document builds the indices the checks need — section numbers, resolved definitions,
party display text, every inline value found in the body. `ValidationResult` hands all of it back,
so a renderer or a UI can reuse the work instead of re-deriving it:

| Attribute | Contents |
|---|---|
| `diagnostics` | `Diagnostic(rule, level, message, line, file)` — the authoritative record |
| `is_valid` | `True` when no Error-level diagnostic was reported |
| `errors` / `warnings` / `infos` | Message strings by severity |
| `rules(level=None)` | Set of rule ids present, optionally filtered by severity |
| `sections`, `section_lookup` | Numbered section index; resolves `{{ref:}}` targets. Numbers count from the shallowest heading level, and a level a heading skips counts as 1 (`#`, `###`, `##` → 1, 1.1.1, 1.2), so no two sections share a number except alternatives and what they contain (§15.8) |
| `definition_lookup`, `party_lookup`, `side_lookup`, `attachment_lookup` | Resolved display text |
| `inline_dates`, `inline_money`, `inline_durations`, `inline_fields`, `inline_placeholders` | Field-spec values found in the body |

### Reading and editing the document model

`parse_document` returns a `Document` of plain dataclasses — `Metadata`, `Section`, `Block`,
`Side`, `Party`, `Attachment` — that you can inspect, edit, and write back out:

```python
from legaldown import parse_document, serialize_document

document = parse_document(source)
document.metadata.governing_law = "Czech Republic"

with open("contract.lgd", "w", encoding="utf-8") as handle:
    handle.write(serialize_document(document))
```

Content before the first heading — typically the sentence identifying the parties — is the
document's preamble (§4.4): it is unnumbered, so it lives in `document.preamble` rather than in
`sections`. `document.iter_blocks()` walks every body block, preamble first.

A `Section`'s `identifier` is the explicit `{#id}` written after its heading, or `""` when
it has none. The identifiers the validator generates (§5.3, §5.5) are not written back into the
model: read them from `ValidationResult.sections`.

`document_to_dict()` / `document_from_dict()` round-trip the model through JSON-friendly
structures, except the fields that describe the parsed source rather than the document,
`Metadata.not_line_editable`, `Metadata.frontmatter_absent` and `Document.source_map`.

Each diagnostic names its `line` (from 1) and its `file` (the document's `filename`), as §16.9
requires: the line of the directive, marker, heading or block it is about, or of the
frontmatter key — for a missing key, the key that holds it, or the frontmatter's first. Lines
come from the `source_map` that `parse_document` gives a document. A document built from a dict
has none, and one changed after parsing no longer fits its map: their diagnostics have no line
(`None`) rather than a stale one. `FrontmatterError.line` is the line of YAML that cannot be
read. `render_block()` renders a single block when you are driving your own layout.
`iter_directives()` lexes the directives in a piece of text by the §11.2
grammar — parameters in any order, quoted values decoded — and is what the validator itself uses.

`definition_lookup(collect_definitions(document))` gives a document's definitions, id to term,
without validating it: the same map as `ValidationResult.definition_lookup`, except for definitions
imported from an amended original or an attachment file. A term written empty (`"" {{def: x}}`)
reads as its id (`id_term`), and an id that is not a valid identifier is left out.

One guarantee worth knowing: the parser is **faithful** — it never rewrites your input to make it
valid, so what you authored is exactly what the validator judges. The serializer, by contrast,
normalizes: frontmatter is re-emitted as canonical YAML, paragraphs are written as single
lines, setext (underlined) headings are written as `#` headings, list items are written with
`-` (`+` where `-` would read as a thematic break) or `1.`, `2.`, … whichever marker they
had, each nested list at its parent item's content and numbered on its own, without blank
lines between items, and table rows are written
with a pipe at each end and every `|` in a cell escaped as `\|`, so expect a
formatting-normalized file rather than a byte-for-byte copy.

A list block's `items` are `ListItem`s, each holding the blocks of its content in order: its
first paragraph, then later paragraphs, headings, nested lists (a list block of their own kind),
code, quotes, tables and raw HTML. An empty item holds none. A paragraph in an item is always of
kind `paragraph`: a directive in it stays in its text. A heading in an item or a quote is a
`heading` block, not a section: its `text` without the `#` syntax, and its `level` (1–6; `level`
is 0 on every other block). Written outside an item, where it would be a section's heading, a
`heading` block becomes paragraph text with a backslash before it. `item_text(item)` gives an
item's first paragraph's text, and `render_item(item)` its content as written after its marker;
a string item in `document_from_dict` (as models before 0.3 held them) is read as such content.
A nested item's condition applies within those of the items it is nested in (§15.3).

A quote block keeps its `text`: its content, one line per source line without the `>` marker.
The validator and assembly read that content as blocks, as they read an item's, so code and raw
HTML in a quote hold no directive.

Tables follow GFM: a table needs a delimiter row with one cell per header, rows take the
header's width, and a `|` inside a cell — a code span's included — is written `\|`. A
table block's `headers` and `rows` hold the cell text with those escapes removed, and
`align` holds each column's alignment (`"left"`, `"right"`, `"center"`, or `""`).

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
from legaldown import assemble, needed_questions, template_questions

result = assemble(template_source, {"forum": "courts", "fee": {"amount": "5000", "currency": "EUR"}})
if result.ok:
    contract = result.output        # the assembled template file
    files = result.files            # assembled fragments and attachment files, by relative path
for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)

template_questions(template_source)            # every question, declared and implicit (§15.2)
needed_questions(template_source, answers)     # what to ask next, given the answers so far
```

Assembly edits the template as written — "no other byte of the template changes" — so its
output is identical to any other conforming implementation's. A template with include fragments
or LegalDown attachment files needs `load_file=`, a function from a relative path to the file's
text; without it such a template is refused rather than assembled partially (§17.6). The files it
reads are checked as the Full level checks them — no frontmatter, no level 1 heading, and in a
template no includes, and no conditions or drafting notes in a fragment — and a template whose
files fail is refused. [CONFORMANCE.md](CONFORMANCE.md#assembly-157-176) lists what assembly
checks and what it does not.

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
pytest
```

### Conformance suite

The fixtures corpus lives in the specification repository, so point the harness at a checkout:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Cases for rules outside Core are skipped and named, so the run doubles as the coverage ledger in
[CONFORMANCE.md](https://github.com/ForLegalAI/legaldown-validator/blob/main/CONFORMANCE.md),
which accounts for every skip. CI runs it on every push and pull request.

Bug reports and pull requests are welcome in
[Issues](https://github.com/ForLegalAI/legaldown-validator/issues); questions about the format
itself belong in the specification repository's
[Discussions](https://github.com/ForLegalAI/LegalDown/discussions).

## License

MIT — see [LICENSE](https://github.com/ForLegalAI/legaldown-validator/blob/main/LICENSE). The LegalDown specification itself is published separately under
CC BY 4.0; it permits implementations to choose their own license.
