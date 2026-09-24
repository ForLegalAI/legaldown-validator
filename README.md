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
contract.lgd: error: [ref-broken] Broken section reference: 'payment-terms'.
contract.lgd: warning: [money-missing-currency] Money directive without currency parameter.

1 error(s), 1 warning(s), 0 info(s)
```

One diagnostic per line, each prefixed with its rule id. A clean document reports
`No issues found.` and exits `0`.

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

### Working with the result

Validating a document builds the indices the checks need — section numbers, resolved definitions,
party display text, every inline value found in the body. `ValidationResult` hands all of it back,
so a renderer or a UI can reuse the work instead of re-deriving it:

| Attribute | Contents |
|---|---|
| `diagnostics` | `Diagnostic(rule, level, message)` — the authoritative record |
| `is_valid` | `True` when no Error-level diagnostic was reported |
| `errors` / `warnings` / `infos` | Message strings by severity |
| `rules(level=None)` | Set of rule ids present, optionally filtered by severity |
| `sections`, `section_lookup` | Numbered section index; resolves `{{ref:}}` targets |
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
structures, except the two fields that describe the parsed source rather than the document,
`Metadata.not_line_editable` and `Metadata.frontmatter_absent`. `render_block()` renders a single block when you are driving your own layout.
`iter_directives()` lexes the directives in a piece of text by the §11.2
grammar — parameters in any order, quoted values decoded — and is what the validator itself uses.

One guarantee worth knowing: the parser is **faithful** — it never rewrites your input to make it
valid, so what you authored is exactly what the validator judges. The serializer, by contrast,
normalizes: frontmatter is re-emitted as canonical YAML, paragraphs are written as single
lines, setext (underlined) headings are written as `#` headings, list items are written with
`-` (`+` where `-` would read as a thematic break) or `1.`, `2.`, … whichever marker they
had, and table rows are written
with a pipe at each end and every `|` in a cell escaped as `\|`, so expect a
formatting-normalized file rather than a byte-for-byte copy.

Tables follow GFM: a table needs a delimiter row with one cell per header, rows take the
header's width, and a `|` inside a cell — a code span's included — is written `\|`. A
table block's `headers` and `rows` hold the cell text with those escapes removed, and
`align` holds each column's alignment (`"left"`, `"right"`, `"center"`, or `""`).

Lines indented four or more columns are an indented code block, as in CommonMark, so a
paragraph indented that far is code: its directives and anchors are literal (§11.4). A
paragraph that a model holds but that would, written at the margin, open a heading, a fence,
or an HTML block is written with a backslash before it.

Raw HTML follows CommonMark's HTML blocks: a block of raw HTML, or an HTML comment on
lines of its own, is an `html` block holding its source as written. No heading, directive,
or anchor inside it is recognized (§8.6, §11.4), so a clause commented out with
`<!-- … -->` is not a section. As in CommonMark, a comment left unclosed runs to the end of
the document.

## What gets checked

The full rule set with severities and examples lives in the specification (§16); this is the map:

| Area | Checks include |
|---|---|
| **Structure** | Heading depth and skipped levels, hardcoded section numbers, missing title |
| **Directive syntax** | Malformed directives, repeated parameters, parameters a directive does not define, unquoted values that begin with a curly quote, stray `{{` |
| **Cross-references** | `{{ref:}}` targets that do not exist or point at an attachment |
| **Anchors** | Duplicate identifiers, malformed identifiers, auto-generated collisions and lost letters, markers outside an anchor position |
| **Definitions** | Undefined `{{term:}}`, duplicate ids, auto-generated ids that lost letters, missing quoted span, ambiguous quoting, unreferenced definitions |
| **Parties and sides** | Unknown `{{party:}}` / `{{side:}}`, malformed or duplicate names, invalid party types, minimum party and side counts, empty representatives |
| **Values** | Invalid dates, money without currency or with an unknown one, invalid durations and units, undeclared or reserved custom field types |
| **Placeholders** | Malformed ids, invalid types, one blank with two types, currencies, or units, placeholders in structural and format-checked frontmatter fields |
| **Templates** | Malformed question declarations and defaults, placeholders that contradict their declared question, invalid or contradictory conditions, references whose target a condition can remove, identifiers shared by units that can appear together, `{{choose:}}` that misses or invents an answer, definitions inside drafting notes and mistyped `[!DRAFTING]` markers, blanks in a defined term or against Markdown punctuation, fragments included twice; with `--final`, anything left unfilled |
| **Attachments** | Undeclared `{{attach:}}`, duplicate or colliding ids, empty titles, unreferenced attachments |
| **Amendments** | Terms the amended original does not define, definition overrides, empty amendment titles |
| **Metadata** | No frontmatter at all, invalid document type, invalid dates, missing sides, issuer side requirements, empty `supersedes` title, a declared `legaldown` version newer than 0.2 |

Each check reports at the severity the specification assigns it — Error, Warning, or Info.

## Scope

This implementation claims **Level 1 — Core** (§17.2): everything above applies to a single
document, in memory, with no filesystem access beyond reading the file you point it at. That
covers authoring, editing, and CI validation of individual documents.

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
