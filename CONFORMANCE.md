# Conformance

`legaldown-validator` implements **Level 1 — Core** of the LegalDown specification (§16.2): parse
and validate a single document in memory.

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — one case per
validation rule, paired with the diagnostic a conforming validator must produce. **57 of the
corpus's 95 rules are implemented, and every one the corpus can exercise at Core level passes.**

Seven implemented rules — `amend-def-override`, `amend-term-undefined`, `amend-term-unresolvable`,
`attachment-id-collision`, `attachment-id-duplicate`, `attachment-title-empty`, and
`attachment-unreferenced` — have fixtures that span several files or are marked for the Full level,
so this single-document harness skips them. They are covered by the unit tests in
`tests/test_spec_alignment.py` instead.

The specification (§16.5) requires an implementation never to silently skip a check it cannot
perform, so the remaining rules are named here. Most of them belong to conformance levels this
implementation does not claim — Rendering (§16.3) and Full (§16.4) — because they need to open
files other than the document itself.

## Rules not implemented

| Group | Rules |
|---|---|
| Filesystem-dependent (Full, §16.4) | `amends-file-missing`, `attachment-file-missing`, `attachment-has-frontmatter`, `attachment-has-h1`, `attachment-anchor-duplicate`, `supersedes-file-missing`, `path-not-relative`, `path-outside-root` |
| Includes (Full, §16.4) | `include-file-missing`, `include-not-legaldown`, `include-cycle`, `include-has-frontmatter`, `include-has-h1`, `include-anchor-duplicate`, `include-heading-skip` |
| Bilingual sets (Full, §16.4) | `translation-file-missing`, `translation-hierarchy-mismatch`, `translation-anchor-mismatch`, `translation-def-mismatch`, `translation-language-set-mismatch`, `translation-implicit-id`, `translation-authoritative-absent` |
| Lexer-level grammar (§11.2–11.4) | `directive-malformed`, `directive-duplicate-param`, `directive-unknown-param`, `brace-stray`, `value-curly-quote`, `raw-html`, `anchor-misplaced` |
| Other | `frontmatter-absent`, `frontmatter-invalid-yaml` (reported by the CLI, not the validator), `anchor-lossy-slug`, `def-lossy-slug`, `definition-circular`, `definition-used-before-declaration`, `language-code-invalid`, `authoritative-not-declared`, `supersedes-title-empty` |

In practice this means multi-file processing is out of scope: includes, attachment file contents,
and bilingual document sets are not resolved or cross-checked. Single-document authoring, editing,
and CI validation are fully covered.

One id appears on both sides of that line. `attachment-file-missing` is defined as *the attachment
`file` path exists*, which needs the filesystem and is therefore unimplemented — but the validator
also emits that id when an attachment declares no `file` at all, which is visible in the document
itself. The specification provides no separate id for the absent key, so a diagnostic carrying
`attachment-file-missing` from this implementation always means the key is missing, never that the
path failed to resolve.

## Checking this yourself

The conformance harness runs against a checkout of the specification repository:

```bash
git clone https://github.com/ForLegalAI/LegalDown ../LegalDown
LEGALDOWN_FIXTURES_DIR=../LegalDown/fixtures pytest tests/conformance -q
```

Cases for the rules above are skipped by name, so the 38 `not implemented` skips reproduce this
table one for one. The run reports 45 skips in total: the remaining seven are the implemented
rules named above, skipped as `multi-file case` or `requires conformance level full`. CI runs this
on every push and pull request.

## Declaring conformance in code

The package exports what it targets, so consumers can assert it:

```python
import legaldown

legaldown.SPEC_VERSION       # "0.1"  — specification version targeted
legaldown.CONFORMANCE_LEVEL  # "core" — conformance level claimed
legaldown.__version__        # implementation version
```

The CLI's JSON output repeats the first and last of these on every run, as `legaldown_spec` and
`validator_version`. The conformance level is not part of the JSON payload — read it from the
package.
