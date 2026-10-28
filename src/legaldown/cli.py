"""Command-line interface for the LegalDown reference validator.

Specification §16.9 requires structured diagnostic output and recommends both
plain-text and JSON formats for tooling integration; both are provided here,
each diagnostic carrying its stable rule id (§16.1).

Usage::

    legaldown validate contract.lgd
    legaldown validate --format json *.lgd
    legaldown validate --ignore def-unreferenced --warnings-as-errors doc.lgd
    legaldown validate --final signed-contract.lgd
    legaldown validate --fragment clauses/*.lgd
    legaldown validate --fragment --template clauses/*.lgd
    legaldown validate --fragment --no-template clauses/*.lgd
    legaldown validate --fragment --fragment-kind attachment --template schedules/*.lgd
    legaldown assemble template.lgd --answers answers.yaml -o out/
    legaldown assemble template.lgd -i --answers a.yaml --save-answers a.yaml -o out/
    legaldown questions template.lgd --answers answers.yaml --format json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from . import SPEC_VERSION, __version__
from .assembly import AssemblyResult, frontmatter_diagnostic
from .files import within, write_atomically
from .parser import FrontmatterError, load
from .template import AnswersError, Form, Template, diagnostic_dict, load_answers, load_template
from .validator import validate
from .validator.fragments import FragmentKind

# Exit codes: 0 clean, 1 diagnostics found, 2 usage/IO failure.
EXIT_OK = 0
EXIT_DIAGNOSTICS = 1
EXIT_ERROR = 2

_LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


def _validate_path(
    path: Path,
    *,
    final: bool = False,
    fragment: bool = False,
    fragment_kind: FragmentKind | None = None,
    template: bool | None = None,
    document_root: str | None = None,
) -> tuple[list[dict], str | None]:
    """Validate one file; return (diagnostics, read/parse failure message)."""
    try:
        # Frontmatter that cannot be read is the document's fault, which validate
        # reports (frontmatter-invalid-yaml) along with what the body has (#43).
        document = load(path, on_frontmatter_error="keep")
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"cannot read {path}: {exc}"
    except Exception as exc:
        # Not the document's fault: a bug in this validator. Reported as a
        # failure, never as a diagnostic the author would try to fix.
        return [], (
            f"internal error while parsing {path}: {type(exc).__name__}: {exc} "
            f"(please report this)"
        )

    try:
        result = validate(
            document,
            final=final,
            fragment=fragment,
            fragment_kind=fragment_kind,
            template=template,
            document_root=document_root,
        )
    except Exception as exc:
        # As above: validating reads the files the document refers to, too.
        return [], (
            f"internal error while validating {path}: {type(exc).__name__}: {exc} "
            f"(please report this)"
        )
    return [
        {
            # The file the finding is in: the document, or a fragment it includes, which
            # is named by its path from the document's directory (#35).
            "file": str(path.parent / d.file) if d.file else str(path),
            "rule": d.rule,
            "level": d.level,
            "line": d.line,
            "message": d.message,
            # Always present, so the schema does not depend on the mode: false
            # unless --fragment found what the including document may settle.
            "host_dependent": d.host_dependent,
        }
        for d in result.diagnostics
    ], None


def _print_text(diagnostics: list[dict], *, quiet: bool) -> None:
    for d in diagnostics:
        where = f"{d['file']}:{d['line']}" if d.get("line") else d["file"]
        # A finding the including document may settle (--fragment) says so.
        host = " (host-dependent)" if d.get("host_dependent") else ""
        print(f"{where}: {d['level']}: [{d['rule']}] {d['message']}{host}")
    if quiet:
        return
    # The host-dependent findings (--fragment) are counted apart, as the exit
    # status leaves them out, so the summary agrees with it.
    counts = {level: 0 for level in _LEVEL_ORDER}
    host_count = 0
    for d in diagnostics:
        if d.get("host_dependent"):
            host_count += 1
        else:
            counts[d["level"]] = counts.get(d["level"], 0) + 1
    summary = ", ".join(f"{counts.get(lvl, 0)} {lvl}(s)" for lvl in _LEVEL_ORDER)
    if host_count:
        summary += f"; {host_count} host-dependent"
    print(f"\n{summary}" if diagnostics else "No issues found.", file=sys.stderr)


def _same(path: str | os.PathLike[str]) -> str:
    """A file by its absolute path, however it is spelt (``.``, ``..``, a relative path). Links
    are not followed: ``load`` reads a document's files from its own directory, so a link to a
    document is a document of its own, with the includes beside the link."""
    return os.path.abspath(path)


def _judge(d: dict, own_file: str) -> tuple[bool, tuple[Any, ...]]:
    """Whether finding *d* is in the file of the target that produced it (*own_file*, ``_same``),
    and not in a fragment it includes, and what makes it one with another: the same file, line,
    rule and message. The message of a finding in a fragment does not name the fragment by a
    path (the file does), so the same finding reached from two directories reads the same; one
    that carries the including document's context (a heading-skip from level 1, or from level 2)
    is another diagnosis."""
    file = _same(d["file"])
    return file == own_file, (file, d.get("line"), d["rule"], d["message"])


def _run_validate(args: argparse.Namespace) -> int:
    ignored = set(args.ignore or [])
    collected: list[dict] = []
    failures: list[str] = []
    # The findings of each target but those ignored, each with whether it is the target's own
    # and what makes it one with another.
    reports: list[list[tuple[dict, bool, tuple[Any, ...]]]] = []
    reached: set[str] = set()  # the targets validated, by absolute path

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
            # A file named twice, or by a directory and by its own path, is one target: validated
            # and reported once, at its first place.
            here = _same(target)
            if here in reached:
                continue
            reached.add(here)
            diagnostics, failure = _validate_path(
                target,
                final=args.final,
                fragment=args.fragment,
                fragment_kind=args.fragment_kind,
                template=args.host_template,
                document_root=args.document_root,
            )
            if failure:
                failures.append(failure)
                continue
            reports.append([(d, *_judge(d, here)) for d in diagnostics if d["rule"] not in ignored])

    # A fragment several of the documents include is found wrong once, not once for each
    # (#35): across all the targets of the run, a finding in a file other than the one that
    # produced it is printed and counted once per identical (file, line, rule, message). The
    # findings the targets have in themselves are seen first, so a fragment that is also a
    # target is not reported twice whatever the order of the arguments; those are never
    # collapsed: two alike in one document are two findings.
    seen = {identity for found in reports for _d, own, identity in found if own}
    for found in reports:
        for d, own, identity in found:
            if not own:
                if identity in seen:
                    continue
                seen.add(identity)
            collected.append(d)

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
    # What the including document may settle (--fragment) is shown, but is no
    # failure of the fragment: a clean fragment that uses its host's party or
    # term exits 0 (#39).
    own = [d for d in collected if not d.get("host_dependent")]
    if any(d["level"] == "error" for d in own):
        return EXIT_DIAGNOSTICS
    if own and args.strict:
        return EXIT_DIAGNOSTICS
    return EXIT_OK


def _read(path: Path) -> str:
    """*path* as written: no line-break translation, since assembly edits
    the template byte for byte (§15.7.2)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write_stdout(text: str, errors: str = "strict") -> None:
    """*text* to standard output as UTF-8 bytes, whatever the terminal's encoding
    and line-break translation. A character UTF-8 cannot hold (a lone surrogate) is
    an error, unless *errors* says to write it escaped."""
    data = text.encode("utf-8", errors=errors)  # before anything is written
    sys.stdout.flush()
    stream = getattr(sys.stdout, "buffer", None)
    if stream is not None:
        stream.write(data)
        stream.flush()
    else:
        sys.stdout.write(text)


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
        write_atomically(path, text)
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:  # a UnicodeEncodeError is a ValueError; a link loop a RuntimeError
        return f"cannot write the answers to {path}: {exc}"
    return None


def _same_file(first: Path, second: Path) -> bool:
    """Whether the two paths are one file: the same path, or a link, symbolic or hard, to it."""
    try:
        if first.resolve() == second.resolve():
            return True
        return os.path.samefile(first, second)
    except (OSError, RuntimeError):  # one of them is not there, or a symbolic link loops
        return False


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
            if not state["answers"] and not args.answers:
                # Nothing was read and nothing typed: an existing file is not replaced by an empty one.
                print(f"no answers to save; {args.save_answers} was not written", file=sys.stderr)
            else:
                problem = _save_answers(Path(args.save_answers), state["answers"])
                if problem:
                    print(f"error: {problem}", file=sys.stderr)
                    code = EXIT_ERROR if code == EXIT_OK else code
    return code


def _assemble(args: argparse.Namespace, state: dict[str, Any]) -> int:
    template_path = Path(args.template)
    if args.save_answers and _same_file(Path(args.save_answers), template_path):
        print("error: --save-answers names the template itself; it would be overwritten", file=sys.stderr)
        return EXIT_ERROR
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
                    if not _interview(template, answers):
                        _say("End of the input: the questions left are left to their defaults, or their blanks.")
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
        print(f"{_location(diagnostic_dict(d), str(template_path))}{d.level}: [{d.rule}] {d.message}", file=sys.stderr)
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
        try:
            _write_stdout(result.output)
        except UnicodeEncodeError as exc:
            print(f"error: the assembled template holds text that cannot be written as UTF-8 ({exc.reason}); "
                  f"it was not written", file=sys.stderr)
            return EXIT_ERROR
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
        except (OSError, UnicodeEncodeError) as exc:
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
        # No host_dependent: assembly output is never a fragment-mode result (#39).
        return None, None, [diagnostic_dict(diagnostic)], None
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, [], f"cannot read {path}: {exc}"
    except Exception as exc:
        return None, None, [], f"internal error while reading {path}: {type(exc).__name__}: {exc} (please report this)"


def _location(d: dict, template: str) -> str:
    """Where a problem or diagnostic of *template* is, as ``assemble`` shows it:
    the template, or the fragment or attachment file beside it, and the line."""
    name = str(Path(template).parent / d["file"]) if d["file"] else template
    return f"{name}:{d['line']}: " if d["line"] else f"{name}: "


def _format_form(form: Form, template: str) -> str:
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
        lines.append(f"problem: {_location(d, template)}[{d['rule']}] {d['message']}")
    for d in data["diagnostics"]:
        lines.append(f"{d['level']}: {_location(d, template)}[{d['rule']}] {d['message']}")
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
                                      "diagnostics": [], "questions": []}, indent=2, allow_nan=False) + "\n",
                          errors="backslashreplace")
        else:
            for d in problems:
                print(f"{_location(d, args.template)}{d['level']}: [{d['rule']}] {d['message']}", file=sys.stderr)
        return EXIT_DIAGNOSTICS
    try:
        form = template.form(template.coerce(answers))
        if args.format == "json":
            text = json.dumps(form.as_dict(), indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        else:
            text = _format_form(form, args.template)
        ready, problems_found = form.ready, bool(template.problems)
    except Exception as exc:
        # Not the template's fault: a bug in this validator, as in ``validate``.
        print(f"error: internal error while listing the questions of {args.template}: "
              f"{type(exc).__name__}: {exc} (please report this)", file=sys.stderr)
        return EXIT_ERROR
    _write_stdout(text, errors="backslashreplace")
    if problems_found or (args.check and not ready):
        return EXIT_DIAGNOSTICS
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="legaldown",
        description=(
            "LegalDown reference validator "
            f"(specification {SPEC_VERSION}, Core conformance level, Assembly capability)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"legaldown {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser(
        "validate",
        help="Validate LegalDown documents.",
        description=(
            "Validate LegalDown documents. Diagnostics go to standard output, the summary to "
            "standard error. Exit status 0 when clean, 1 when diagnostics are found (errors, "
            "or any with --strict), 2 when a file cannot be read."
        ),
    )
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
        "--fragment",
        action="store_true",
        help=(
            "Validate each file as an include fragment or attachment file on its own (§12.2, §12.4; "
            "--fragment-kind says which): no frontmatter is expected, and what only the including document can settle (a party, "
            "side, attachment or question it declares, a section or term it defines) is reported "
            "with \"host_dependent\": true in JSON and \"(host-dependent)\" in text, and does not "
            "affect the exit status."
        ),
    )
    validate.add_argument(
        "--fragment-kind",
        choices=("include", "attachment"),
        default=None,
        help=(
            "With --fragment: what the files are to the document that uses them, include (the "
            "default; an {{include:}} fragment, §12.2) or attachment (a LegalDown attachment file, "
            "§12.4). An attachment file's frontmatter or level 1 heading is attachment-has-frontmatter "
            "or attachment-has-h1 (§16.10); with --template, an include fragment is held to what a "
            "template's fragment may hold (no condition, drafting note or include, and an explicit "
            "identifier on every heading, §15.3), and an attachment file only to holding no include."
        ),
    )
    host = validate.add_mutually_exclusive_group()
    # dest is not `template`: assemble and questions have that argument.
    host.add_argument(
        "--template",
        action="store_const",
        const=True,
        default=None,
        dest="host_template",
        help=(
            "With --fragment: the document that includes the fragments is a template (§15.1), so "
            "they are checked as parts of one. Without --template or --no-template, a fragment is "
            "a template only when it holds a condition or a {{choose:}}."
        ),
    )
    host.add_argument(
        "--no-template",
        action="store_const",
        const=False,
        dest="host_template",
        help=(
            "With --fragment: the including document is not a template, so it declares no "
            "question for a condition or {{choose:}} to name."
        ),
    )
    validate.add_argument(
        "--document-root",
        metavar="DIR",
        help=(
            "The directory every file a document refers to must lie within (§2.3): a path that "
            "escapes it, as ../ does, is path-outside-root. Each document is read from its own "
            "directory, which must be within DIR; paths are only read as text, no file is looked "
            "at. Without it, ../shared/definitions.lgd is valid. A path that is absolute or begins "
            "with a URI scheme is path-not-relative with or without it."
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
            "LegalDown attachment files are read relative to the template. A template with "
            "Errors in the validator's template rules is refused. Exit status as for validate."
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
            "it lists what must still be answered and exits 1, or assembles if nothing must be."
        ),
    )
    assemble_cmd.add_argument(
        "--save-answers",
        metavar="FILE",
        help="Write the answers used, with those typed, to FILE (YAML), also after an interruption or a failure.",
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
    if getattr(args, "host_template", None) is not None and not args.fragment:
        parser.error("--template and --no-template are only valid with --fragment")
    if getattr(args, "fragment_kind", None) is not None and not args.fragment:
        parser.error("--fragment-kind is only valid with --fragment")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
