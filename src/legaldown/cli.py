"""Command-line interface for the LegalDown reference validator.

Specification §15.9 requires structured diagnostic output and recommends both
plain-text and JSON formats for tooling integration; both are provided here,
each diagnostic carrying its stable rule id (§15.1).

Usage::

    legaldown validate contract.lgd
    legaldown validate --format json *.lgd
    legaldown validate --ignore def-unreferenced --warnings-as-errors doc.lgd
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import SPEC_VERSION, __version__
from .parser import parse_document
from .validator import validate_document

# Exit codes: 0 clean, 1 diagnostics found, 2 usage/IO failure.
EXIT_OK = 0
EXIT_DIAGNOSTICS = 1
EXIT_ERROR = 2

_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


def _validate_path(path: Path) -> tuple[list[dict], str | None]:
    """Validate one file; return (diagnostics, read/parse failure message)."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"cannot read {path}: {exc}"

    try:
        document = parse_document(source, filename=path.name)
    except Exception as exc:  # malformed YAML frontmatter, etc.
        return [
            {
                "file": str(path),
                "rule": "frontmatter-invalid-yaml",
                "level": "error",
                "message": str(exc),
            }
        ], None

    result = validate_document(document)
    return [
        {
            "file": str(path),
            "rule": d.rule,
            "level": d.level,
            "message": d.message,
        }
        for d in result.diagnostics
    ], None


def _print_text(diagnostics: list[dict], *, quiet: bool) -> None:
    for d in diagnostics:
        print(f"{d['file']}: {d['level']}: [{d['rule']}] {d['message']}")
    if quiet:
        return
    counts = {level: 0 for level in _LEVEL_ORDER}
    for d in diagnostics:
        counts[d["level"]] = counts.get(d["level"], 0) + 1
    summary = ", ".join(f"{counts.get(lvl, 0)} {lvl}(s)" for lvl in _LEVEL_ORDER)
    print(f"\n{summary}" if diagnostics else "No issues found.", file=sys.stderr)


def _run_validate(args: argparse.Namespace) -> int:
    ignored = set(args.ignore or [])
    collected: list[dict] = []
    failures: list[str] = []

    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_dir():
            targets = sorted(
                p
                for pattern in ("*.lgd", "*.legaldown", "*.legal.md")
                for p in path.rglob(pattern)
            )
        else:
            targets = [path]
        for target in targets:
            diagnostics, failure = _validate_path(target)
            if failure:
                failures.append(failure)
                continue
            collected.extend(d for d in diagnostics if d["rule"] not in ignored)

    if args.warnings_as_errors:
        for d in collected:
            if d["level"] == "warning":
                d["level"] = "error"

    collected.sort(key=lambda d: (d["file"], _LEVEL_ORDER.get(d["level"], 9), d["rule"]))

    if args.format == "json":
        print(
            json.dumps(
                {
                    "legaldown_spec": SPEC_VERSION,
                    "validator_version": __version__,
                    "diagnostics": collected,
                },
                indent=2,
            )
        )
    else:
        _print_text(collected, quiet=args.quiet)

    for failure in failures:
        print(f"error: {failure}", file=sys.stderr)
    if failures:
        return EXIT_ERROR
    has_error = any(d["level"] == "error" for d in collected)
    if has_error:
        return EXIT_DIAGNOSTICS
    if collected and args.strict:
        return EXIT_DIAGNOSTICS
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legaldown",
        description=(
            "LegalDown reference validator "
            f"(specification {SPEC_VERSION}, Core conformance level)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"legaldown {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate LegalDown documents.")
    validate.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to validate (directories are searched recursively).",
    )
    validate.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    validate.add_argument(
        "--ignore",
        action="append",
        metavar="RULE_ID",
        help="Suppress a rule by its stable id (repeatable), e.g. --ignore def-unreferenced.",
    )
    validate.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="Report warnings at error severity.",
    )
    validate.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any diagnostic is reported, not just errors.",
    )
    validate.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the trailing summary line.",
    )
    validate.set_defaults(func=_run_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
