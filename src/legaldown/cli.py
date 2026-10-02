"""Command-line interface for the LegalDown reference validator.

Specification §16.9 requires structured diagnostic output and recommends both
plain-text and JSON formats for tooling integration; both are provided here,
each diagnostic carrying its stable rule id (§16.1).

Usage::

    legaldown validate contract.lgd
    legaldown validate --format json *.lgd
    legaldown validate --ignore def-unreferenced --warnings-as-errors doc.lgd
    legaldown validate --final signed-contract.lgd
    legaldown assemble template.lgd --answers answers.yaml -o out/
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from . import SPEC_VERSION, __version__
from .assembly import AssemblyResult, frontmatter_diagnostic
from .files import within
from .parser import FrontmatterError, load
from .template import AnswersError, Form, Template, load_answers, load_template
from .validator import validate

# Exit codes: 0 clean, 1 diagnostics found, 2 usage/IO failure.
EXIT_OK = 0
EXIT_DIAGNOSTICS = 1
EXIT_ERROR = 2

_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


def _validate_path(path: Path, *, final: bool = False) -> tuple[list[dict], str | None]:
    """Validate one file; return (diagnostics, read/parse failure message)."""
    try:
        document = load(path)
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"cannot read {path}: {exc}"
    except FrontmatterError as exc:
        return [
            {
                "file": str(path),
                "rule": "frontmatter-invalid-yaml",
                "level": "error",
                "line": exc.line,
                "message": str(exc),
            }
        ], None
    except Exception as exc:
        # Not the document's fault: a bug in this validator. Reported as a
        # failure, never as a diagnostic the author would try to fix.
        return [], (
            f"internal error while parsing {path}: {type(exc).__name__}: {exc} "
            f"(please report this)"
        )

    try:
        result = validate(document, final=final)
    except Exception as exc:
        # As above: validating reads the files the document refers to, too.
        return [], (
            f"internal error while validating {path}: {type(exc).__name__}: {exc} "
            f"(please report this)"
        )
    return [
        {
            "file": str(path),
            "rule": d.rule,
            "level": d.level,
            "line": d.line,
            "message": d.message,
        }
        for d in result.diagnostics
    ], None


def _print_text(diagnostics: list[dict], *, quiet: bool) -> None:
    for d in diagnostics:
        where = f"{d['file']}:{d['line']}" if d.get("line") else d["file"]
        print(f"{where}: {d['level']}: [{d['rule']}] {d['message']}")
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
            diagnostics, failure = _validate_path(target, final=args.final)
            if failure:
                failures.append(failure)
                continue
            collected.extend(d for d in diagnostics if d["rule"] not in ignored)

    if args.warnings_as_errors:
        for d in collected:
            if d["level"] == "warning":
                d["level"] = "error"

    # By file, then in source order (a diagnostic without a line first), then
    # by level and rule.
    collected.sort(key=lambda d: (d["file"], d.get("line") or 0, _LEVEL_ORDER.get(d["level"], 9), d["rule"]))

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


def _read(path: Path) -> str:
    """*path* as written: no line-break translation, since assembly edits
    the template byte for byte (§15.7.2)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_stdout(text: str) -> None:
    """*text* to standard output as UTF-8 bytes, whatever the terminal's encoding
    and line-break translation."""
    sys.stdout.flush()
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(text.encode("utf-8"))
        stream.flush()
    else:
        sys.stdout.write(text)


def _write_atomically(path: Path, text: str) -> None:
    """*text* to *path*: all of it, or the file as it was — written beside it and
    moved into place, since the file may be the answers a person typed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name)
        raise


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _read_answers(path: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """The answers set (§15.7.1): a YAML mapping, or empty without a file."""
    if path is None:
        return {}, None
    try:
        return load_answers(path), None
    except OSError as exc:
        return None, f"cannot read {path}: {exc}"
    except AnswersError as exc:
        return None, str(exc)


def _ask(prompt: str) -> str | None:
    """A line from standard input, after *prompt* on standard error (standard output
    is the assembled template); ``None`` at the end of the input. A line that is not
    text in the terminal's encoding is asked for again. It reads the line as it is:
    no editing or history, which Python gives only where standard output is the
    terminal."""
    while True:
        print(prompt, end="", file=sys.stderr, flush=True)
        try:
            line = sys.stdin.readline()
        except UnicodeDecodeError:
            print("That is not text in this terminal's encoding; type it again.", file=sys.stderr, flush=True)
            continue
        return line.rstrip("\r\n") if line else None


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _interview(template: Template, answers: dict, ask=_ask, say=_say) -> bool:
    """Ask for the answers that *answers* does not give, as the form grows, until
    the form has nothing left to ask; *answers* gets what is typed. A decision
    cannot be left open, so it is asked again until it is answered; the empty
    line leaves a value question to its default, or its blank. Returns False at
    the end of the input."""
    skipped: set[str] = set()
    while True:
        form = template.form(answers)
        question = next(
            (q for q in form.questions
             if (answers.get(q.id) is None or form.problem(q.id)) and q.id not in skipped),
            None,
        )
        if question is None:
            return True
        given = answers.get(question.id)
        if given is not None:
            say(f"The answer given to '{question.id}', {question.to_text(given)!r}, is not valid: "
                f"{form.problem(question.id)}")
        default = f" [{question.to_text(question.default)}]" if question.default is not None else ""
        line = ask(f"{question.prompt or question.id} ({question.hint}){default}: ")
        if line is None:
            return False
        try:
            answer = question.from_text(line)
        except ValueError as exc:
            say(str(exc))
            continue
        if answer is not None:
            answers[question.id] = answer
        elif question in form.blocking:
            say("An answer is needed.")
        else:
            answers.pop(question.id, None)  # an answer that is not valid does not stay: the default, or the blank
            if question.default is not None:
                say(f"  {question.id}: the default, {question.to_text(question.default)}")
            skipped.add(question.id)


def _missing_report(template: Template, answers: dict) -> str | None:
    """What must still be answered before assembly, as text; None when nothing."""
    form = template.form(answers)
    if not form.blocking:
        return None
    lines = [f"error: {len(form.blocking)} question(s) must be answered before assembly:"]
    lines += [f"  {q.id}: {q.prompt or q.id} (enter {q.hint})" for q in form.blocking]
    lines.append("run in a terminal with -i to be asked, or give the answers with --answers")
    return "\n".join(lines)


def _save_answers(path: Path, answers: dict) -> str | None:
    """*answers* to the YAML file at *path*; a failure as text. Money amounts stay
    quoted strings, dates the dates they are."""
    try:
        text = yaml.safe_dump(answers, sort_keys=False, allow_unicode=True, default_flow_style=False)
        text.encode("utf-8")
        _write_atomically(path, text)
    except (OSError, ValueError, yaml.YAMLError) as exc:  # UnicodeEncodeError is a ValueError
        return f"cannot write the answers to {path}: {exc}"
    return None


def _run_assemble(args: argparse.Namespace) -> int:
    state: dict[str, Any] = {"answers": None}
    code = EXIT_ERROR
    try:
        code = _assemble(args, state)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    finally:
        # Also after an interruption or a failure: what was typed is not lost.
        if args.save_answers and state["answers"] is not None:
            problem = _save_answers(Path(args.save_answers), state["answers"])
            if problem:
                print(f"error: {problem}", file=sys.stderr)
                code = EXIT_ERROR if code == EXIT_OK else code
    return code


def _assemble(args: argparse.Namespace, state: dict[str, Any]) -> int:
    template_path = Path(args.template)
    answers, failure = _read_answers(Path(args.answers) if args.answers else None)
    state["answers"] = answers
    result = None
    if failure is None:
        try:
            template = load_template(template_path)
        except FrontmatterError as exc:
            result = AssemblyResult(diagnostics=[frontmatter_diagnostic(exc)])
        except (OSError, UnicodeDecodeError) as exc:
            failure = f"cannot read {template_path}: {exc}"
        except Exception as exc:
            # Not the template's fault: a bug in this validator, as in ``validate``.
            failure = f"internal error while reading {template_path}: {type(exc).__name__}: {exc} (please report this)"
    if failure is not None:
        print(f"error: {failure}", file=sys.stderr)
        return EXIT_ERROR

    if result is None:
        try:
            answers = state["answers"] = template.coerce(answers)
            if args.interactive and not template.problems:  # a template that cannot be assembled asks nothing
                if sys.stdin.isatty():
                    _interview(template, answers)
                else:
                    report = _missing_report(template, answers)
                    if report:
                        print(report, file=sys.stderr)
                        return EXIT_DIAGNOSTICS
            result = template.form(answers).assemble()
        except Exception as exc:
            # Not the template's fault: a bug in this validator, as in ``validate``.
            print(f"error: internal error while assembling {template_path}: {type(exc).__name__}: {exc} "
                  f"(please report this)", file=sys.stderr)
            return EXIT_ERROR
    for d in result.diagnostics:
        where = f"{template_path}:{d.line}" if d.line else f"{template_path}"
        print(f"{where}: {d.level}: [{d.rule}] {d.message}", file=sys.stderr)
    if not result.ok:
        if not args.interactive and any(d.rule == "answer-missing" for d in result.diagnostics):
            print("run with -i in a terminal to be asked for them, or give them with --answers", file=sys.stderr)
        return EXIT_DIAGNOSTICS

    if args.output is None:
        if result.files:
            print(
                f"error: assembly also writes {len(result.files)} other file(s); "
                f"give an output directory with -o",
                file=sys.stderr,
            )
            return EXIT_ERROR
        # Bytes, so that no platform translates the template's line breaks.
        _write_stdout(result.output)
        return EXIT_OK

    out = Path(args.output)
    problem = None
    if out.exists() and not out.is_dir():
        problem = f"{out} is not a directory"
    elif template_path.name in result.files:
        problem = f"{template_path.name} is both the template and a file it keeps"
    outputs = {template_path.name: result.output, **result.files}
    targets = {relative: within(out, relative) for relative in outputs}
    for relative, target in targets.items():
        if problem is None and target is None:
            problem = f"{relative} leads out of the output directory"
        elif problem is None and target == template_path.resolve():
            problem = f"{relative} would overwrite the template"
    if problem is not None:
        print(f"error: {problem}; nothing was written", file=sys.stderr)
        return EXIT_ERROR
    for relative, text in outputs.items():
        try:
            _write(targets[relative], text)  # an emptied file is written as zero bytes
        except OSError as exc:
            print(f"error: cannot write {relative}: {exc}; the output is incomplete", file=sys.stderr)
            return EXIT_ERROR
    return EXIT_OK


def _load_for_questions(args: argparse.Namespace) -> tuple[Template | None, dict | None, list[dict], str | None]:
    """The template and the answers for ``questions``: ``(template, answers, problems, failure)``."""
    answers, failure = _read_answers(Path(args.answers) if args.answers else None)
    if failure is not None:
        return None, None, [], failure
    path = Path(args.template)
    try:
        return load_template(path), answers, [], None
    except FrontmatterError as exc:
        diagnostic = frontmatter_diagnostic(exc)
        return None, None, [{"rule": diagnostic.rule, "level": diagnostic.level,
                             "message": diagnostic.message, "line": diagnostic.line}], None
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, [], f"cannot read {path}: {exc}"
    except Exception as exc:
        return None, None, [], f"internal error while reading {path}: {type(exc).__name__}: {exc} (please report this)"


def _format_form(form: Form) -> str:
    """The form as text: the questions reached, then what is wrong, then whether it is ready."""
    data = form.as_dict()
    lines = []
    for question in data["questions"]:
        if not question["reached"]:
            continue
        mark = "!" if question["blocking"] else "?" if question["state"] in ("unanswered", "invalid") else " "
        if question["state"] == "answered":
            detail = f"answered: {question['answer_text']}"
        elif question["state"] == "default":
            detail = f"default: {question['default_text']}"
        elif question["state"] == "invalid":
            detail = f"invalid: {question['problem']}"
        else:
            detail = f"enter {question['hint']}"
        lines.append(f"{mark} {question['id']} ({question['type']}) {question['label']} — {detail}")
    others = sum(1 for question in data["questions"] if not question["reached"])
    if others:
        lines.append(f"  ({others} more, not asked yet or not at all with these answers)")
    for d in data["problems"]:
        lines.append(f"problem: [{d['rule']}] {d['message']}")
    for d in data["diagnostics"]:
        lines.append(f"{d['level']}: [{d['rule']}] {d['message']}")
    lines.append("complete" if data["complete"] else "ready, with blanks left" if data["ready"] else "not ready")
    return "\n".join(lines) + "\n"


def _run_questions(args: argparse.Namespace) -> int:
    template, answers, problems, failure = _load_for_questions(args)
    if failure is not None:
        print(f"error: {failure}", file=sys.stderr)
        return EXIT_ERROR
    if template is None:  # frontmatter that cannot be read
        if args.format == "json":
            _write_stdout(json.dumps({"ready": False, "complete": False, "problems": problems,
                                      "diagnostics": [], "questions": []}, indent=2, allow_nan=False) + "\n")
        else:
            for d in problems:
                print(f"{args.template}: {d['level']}: [{d['rule']}] {d['message']}", file=sys.stderr)
        return EXIT_DIAGNOSTICS
    try:
        form = template.form(template.coerce(answers))
        if args.format == "json":
            text = json.dumps(form.as_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        else:
            text = _format_form(form)
        ready, problems_found = form.ready, bool(template.problems)
    except Exception as exc:
        # Not the template's fault: a bug in this validator, as in ``validate``.
        print(f"error: internal error while listing the questions of {args.template}: "
              f"{type(exc).__name__}: {exc} (please report this)", file=sys.stderr)
        return EXIT_ERROR
    _write_stdout(text)
    if problems_found or (args.check and not ready):
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
        "--final",
        action="store_true",
        help=(
            "Check that documents are ready for signature: a remaining placeholder, "
            "drafting note, or template construct is an error (§15.9)."
        ),
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

    assemble_cmd = sub.add_parser(
        "assemble",
        help="Assemble a template with an answers set (§15.7).",
        description=(
            "Assemble a template with an answers set (§15.7). Include fragments and "
            "LegalDown attachment files are read relative to the template."
        ),
    )
    assemble_cmd.add_argument("template", help="The template file.")
    assemble_cmd.add_argument(
        "--answers", metavar="FILE", help="YAML mapping of question ids to answers (§15.7.1)."
    )
    assemble_cmd.add_argument(
        "-o",
        "--output",
        metavar="DIR",
        help=(
            "Write the assembled template, and the fragments and attachment files it "
            "keeps, under DIR at their relative paths. Without it the template is "
            "written to standard output."
        ),
    )
    assemble_cmd.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help=(
            "Ask, in the terminal, for the answers --answers does not give, as the "
            "questions are reached (prompts go to standard error). Without a terminal "
            "it lists what must still be answered and exits 1."
        ),
    )
    assemble_cmd.add_argument(
        "--save-answers",
        metavar="FILE",
        help="Write the answers used, with those typed, to FILE (YAML), also after an interruption.",
    )
    assemble_cmd.set_defaults(func=_run_assemble)

    questions_cmd = sub.add_parser(
        "questions",
        help="List what a template asks, given the answers so far (§15.7).",
        description=(
            "List the questions a template asks given the answers so far: those reached, "
            "which are unanswered, which stop assembly, and what is wrong with the answers. "
            "Exit status 0 unless the template has problems or cannot be read, or, with "
            "--check, is not ready to assemble."
        ),
    )
    questions_cmd.add_argument("template", help="The template file.")
    questions_cmd.add_argument(
        "--answers", metavar="FILE", help="YAML mapping of question ids to answers (§15.7.1)."
    )
    questions_cmd.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format (default: text)."
    )
    questions_cmd.add_argument(
        "--check", action="store_true", help="Exit non-zero when assembly cannot run with these answers."
    )
    questions_cmd.set_defaults(func=_run_questions)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
