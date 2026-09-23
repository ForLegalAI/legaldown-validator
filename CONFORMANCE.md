# Conformance

`legaldown-validator` implements **Level 1 — Core** of the LegalDown specification 0.2 (§17.2):
parse and validate a single document in memory.

It is verified against the specification's own
[fixtures corpus](https://github.com/ForLegalAI/LegalDown/tree/main/fixtures) — one case per
validation rule, paired with the diagnostic a conforming validator must produce. **73 of the
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
| Template conditions (§15.3, §15.4) — not yet implemented | `question-unused`, `condition-invalid`, `condition-never-true`, `condition-reference-unsafe` |
| Assembly capability (§17.6) — not claimed | `answer-invalid`, `answer-missing`, `answer-unknown` |
| Rendering (§17.3) | `ref-not-enumerated` |
| Lexer-level grammar (§11.2–11.4) | `value-curly-quote`, `raw-html` |
| Other | `frontmatter-absent`, `frontmatter-invalid-yaml` (reported by the CLI, not the validator), `anchor-lossy-slug`, `def-lossy-slug`, `definition-circular`, `definition-used-before-declaration`, `language-code-invalid`, `authoritative-not-declared` |

In practice this means multi-file processing is out of scope: includes, attachment file contents,
and bilingual document sets are not resolved or cross-checked. Single-document authoring, editing,
and CI validation are fully covered for documents that are not templates.

Of the template constructs specification 0.2 adds (§15), questions, `{{choose:}}`, drafting
notes, insertion boundaries, and the final option (§15.9, `validate_document(final=True)` or
`legaldown validate --final`) are validated. Conditions (`when=`) are not recognized yet: a
heading's `{#id when=...}` marker is read as part of the heading text, a paragraph's is literal
text, and alternatives sharing an identifier are reported as duplicates. Validate templates with
conditions with that in mind until the condition rows above are implemented.

One id appears on both sides of that line. `attachment-file-missing` is defined as *the attachment
`file` path exists*, which needs the filesystem and is therefore unimplemented — but the validator
also emits that id when an attachment declares no `file` at all, which is visible in the document
itself. The specification provides no separate id for the absent key, so a diagnostic carrying
`attachment-file-missing` from this implementation always means the key is missing, never that the
path failed to resolve.

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

Cases for the rules above are skipped by name, so the 40 `not implemented` skips reproduce this
table one for one, except `ref-not-enumerated`, which has no fixture. The run reports 48 skips in
total: the remaining eight are the implemented rules named above, skipped as `multi-file case` or
`requires conformance level full`. Cases that need the final option run with it. CI runs this on every push and pull request.

## Declaring conformance in code

The package exports what it targets, so consumers can assert it:

```python
import legaldown

legaldown.SPEC_VERSION       # "0.2"  — specification version targeted
legaldown.CONFORMANCE_LEVEL  # "core" — conformance level claimed
legaldown.__version__        # implementation version
```

The CLI's JSON output repeats the first and last of these on every run, as `legaldown_spec` and
`validator_version`. The conformance level is not part of the JSON payload — read it from the
package.
