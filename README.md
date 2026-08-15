<div align="center">

# legaldown-validator 📐

### Reference parser and validator for [LegalDown](https://github.com/ForLegalAI/LegalDown)

**Parse, validate, and serialize LegalDown documents — from Python or the command line.**

Targets specification **v0.1** · Every diagnostic carries a **stable rule id** · Only dependency: PyYAML

</div>

---

## What this is

[LegalDown](https://github.com/ForLegalAI/LegalDown) is an open plain-text standard for legal
documents. This repository is its **reference implementation**: a parser, a document model, a
serializer, and a validator that checks documents against the specification's §15 rules.

Every diagnostic reports the specification's **stable rule id** (§15.1) alongside its severity and
message. Rule ids survive specification renumbering, so they are the only part of a diagnostic safe
to filter, suppress, or gate a build on.

## Install

```bash
pip install legaldown-validator      # distribution name
python -c "import legaldown"         # import name
```

Requires Python 3.11+. The package ships a `py.typed` marker, so type checkers use its annotations
directly.

## Command line

```bash
legaldown validate contract.lgd                     # validate one document
legaldown validate contracts/                       # walk a directory
legaldown validate --format json contract.lgd       # machine-readable output
legaldown validate --ignore def-unreferenced doc.lgd
legaldown validate --warnings-as-errors doc.lgd     # tighten a CI gate
```

Text output is one diagnostic per line, prefixed with its rule id:

```
contract.lgd: error: [ref-broken] Broken section reference: 'payment-terms'.
contract.lgd: warning: [money-missing-currency] Money directive without currency parameter.
```

JSON output (§15.9) is suitable for tooling:

```json
{
  "legaldown_spec": "0.1",
  "validator_version": "0.1.0",
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

**Exit codes:** `0` clean · `1` diagnostics found (errors, or any diagnostic with `--strict`) ·
`2` a file could not be read.

## Python API

```python
from legaldown import parse_document, validate_document, serialize_document

document = parse_document(open("contract.lgd").read(), filename="contract.lgd")
result = validate_document(document)

for diagnostic in result.diagnostics:
    print(diagnostic.level, diagnostic.rule, diagnostic.message)

if result.is_valid:                      # no Error-level diagnostics
    print(serialize_document(document))
```

`ValidationResult` carries the diagnostics plus the indices building them required — useful for
rendering:

| Attribute | Contents |
|---|---|
| `diagnostics` | `Diagnostic(rule, level, message)` — the authoritative record |
| `errors` / `warnings` / `infos` | Message strings by severity |
| `rules(level=None)` | Set of rule ids present, optionally filtered by severity |
| `sections`, `section_lookup` | Numbered section index; resolves `{{ref:}}` targets |
| `definition_lookup`, `party_lookup`, `side_lookup`, `attachment_lookup` | Resolved display text |
| `inline_dates`, `inline_money`, `inline_durations`, `inline_fields`, `inline_placeholders` | Field-spec values found in the body |

### Migrating pre-0.1 content

Documents written before the 0.1 cutover may use spellings the specification never adopted.
`migrate_legacy_directives()` upgrades them — `{{pct: 5}}` becomes
`{{field: 5%, type=percentage}}`, and `unit=M` becomes `unit=MIN`, leaving code spans and fenced
blocks untouched:

```python
from legaldown import migrate_legacy_directives, parse_document

document = parse_document(migrate_legacy_directives(stored_source))
```

The migration is deliberately **opt-in**: `parse_document` never rewrites its input, so a
deliberately authored `unit=M` still surfaces as `duration-invalid-unit` rather than being silently
"fixed". Call it at your own storage boundary when loading legacy content.

## Conformance

This implementation claims **Level 1 — Core** (§16.2): parse and validate a single document. It
does not claim Rendering (§16.3) or Full (§16.4), so multi-file processing — includes, attachment
file contents, and bilingual sets — is out of scope.

Verified against the specification's own [fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures):
**57 of the 95 corpus rules are implemented**, and every implemented rule passes. Per §16.5, an
implementation must never silently skip a check it cannot perform — the unimplemented rules are
listed here rather than left to be discovered:

| Group | Rules not yet implemented |
|---|---|
| Filesystem-dependent (Full, §16.4) | `amends-file-missing`, `attachment-file-missing`, `attachment-has-frontmatter`, `attachment-has-h1`, `attachment-anchor-duplicate`, `supersedes-file-missing`, `path-not-relative`, `path-outside-root` |
| Includes (Full, §16.4) | `include-file-missing`, `include-not-legaldown`, `include-cycle`, `include-has-frontmatter`, `include-has-h1`, `include-anchor-duplicate`, `include-heading-skip` |
| Bilingual sets (Full, §16.4) | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-implicit-id`, `translation-authoritative-absent` |
| Lexer-level grammar (§11.2–11.4) | `directive-malformed`, `directive-duplicate-param`, `directive-unknown-param`, `brace-stray`, `value-curly-quote`, `raw-html`, `anchor-misplaced` |
| Other | `frontmatter-absent`, `frontmatter-invalid-yaml` (reported by the CLI, not the validator), `anchor-lossy-slug`, `def-lossy-slug`, `definition-circular`, `definition-used-before-declaration`, `language-code-invalid`, `authoritative-not-declared`, `supersedes-title-empty` |

## Development

```bash
git clone https://github.com/ForLegalAI/legaldown-validator
cd legaldown-validator
pip install -e ".[dev]"
pytest
```

### Running the conformance suite

The fixtures corpus lives in the specification repository, so point the harness at a checkout:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Cases for unimplemented rules are skipped and named, so the run doubles as the coverage ledger
above. CI runs this on every push and pull request.

### Releasing

Version history is in [CHANGELOG.md](CHANGELOG.md). Releases are published to PyPI by the
`publish` workflow using Trusted Publishing — see [PUBLISHING.md](PUBLISHING.md) for the one-time
setup and the release checklist. `__version__` in `src/legaldown/__init__.py` is the single source
of truth; the workflow refuses to publish if it disagrees with the release tag.

## License

MIT — see [LICENSE](LICENSE). The LegalDown specification itself is published separately under
CC BY 4.0; it permits implementations to choose their own license.
