# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions track the implementation, not the specification: the LegalDown specification version this
release targets is exported as `legaldown.SPEC_VERSION`.

---

## [Unreleased]

## [0.1.0] — 2026-08-15

First release. Targets LegalDown specification **v0.1** at conformance
**Level 1 — Core** (§16.2): parse and validate a single document.

### Added

- **Parser and document model** — YAML frontmatter plus a heading/block document tree, with
  `{#identifier}` anchors on headings, list items, and paragraphs.
- **Serializer** — `Document` back to LegalDown source.
- **Validator** — the §15 rule set at Core level. Every diagnostic carries the specification's
  **stable rule id** (§15.1) and severity, exposed as `Diagnostic(rule, level, message)` on
  `ValidationResult`, alongside the section, definition, party, side, and attachment indices that
  building them produced.
- **Definitions (§7)** — quoted term followed by `{{def:}}`, across the eight accepted quotation
  mark pairs, with auto-derived identifiers.
- **Command line** — `legaldown validate` with plain-text and JSON output (§15.9), `--ignore` by
  rule id, `--warnings-as-errors`, `--strict`, recursive directory walking, and exit codes suitable
  for gating a build.
- **`migrate_legacy_directives()`** — upgrades pre-0.1 spellings (`{{pct: X}}` → `{{field: X%,
  type=percentage}}`, `unit=M` → `unit=MIN`) without touching code spans or fenced blocks. Opt-in by
  design: the parser never rewrites its input, so a deliberately authored `unit=M` still reports
  `duration-invalid-unit`.
- **`SPEC_VERSION` and `CONFORMANCE_LEVEL`** exported so consumers can assert what they validate
  against.
- **Conformance harness** — runs the validator against the specification's own fixtures corpus.
  57 of the corpus's 95 rules are implemented and pass; the remainder are listed in the README per
  §16.5, which requires an implementation never to silently skip a check it cannot perform.

### Notes

Extracted from the PactTrack application, where this code was vendored, into the standalone
reference implementation the specification points to.

[Unreleased]: https://github.com/ForLegalAI/legaldown-validator/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ForLegalAI/legaldown-validator/releases/tag/v0.1.0
