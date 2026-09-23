"""LegalDown directive syntax (spec section 11).

A directive is recognized in two steps: its generic shape
``{{name: argument, argument, ...}}`` is lexed by the §11.2 grammar, and only
then are its named parameters compared with the ones the directive defines.
Because the shape is recognized independently of the vocabulary, a parameter
the directive does not define, parameters in an unconventional order, or a
quoted value (§11.3) never hide the directive from the checks that apply to
it.

This module is the single source of truth for directive syntax. It is reused
by the parser, serializer, definition collection, and validator.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

# Named parameters each directive defines (§6, §7, §10, §12). ``note`` is
# defined for every field spec (§10.1). Placeholder ``currency`` is
# type-specific: it is defined only when the effective type is ``money``
# (§10.7, §13.5 rule 7), which the validator checks.
DIRECTIVE_PARAMS: dict[str, frozenset[str]] = {
    "ref": frozenset(),
    "def": frozenset(),
    "term": frozenset({"label"}),
    "date": frozenset({"note"}),
    "money": frozenset({"currency", "note"}),
    "duration": frozenset({"unit", "note"}),
    "party": frozenset({"label", "note"}),
    "side": frozenset({"label", "note"}),
    "field": frozenset({"type", "note"}),
    "placeholder": frozenset({"type", "currency", "note"}),
    "include": frozenset(),
    "attach": frozenset({"label"}),
}

# Parameters defined only for one value of the directive's ``type`` (§10.7):
# a placeholder takes ``currency`` only when its effective type is ``money``.
_TYPE_SPECIFIC_PARAMS: dict[tuple[str, str], str] = {("placeholder", "currency"): "money"}

# Directive vocabulary defined by §11.1.
KNOWN_DIRECTIVES: frozenset[str] = frozenset(DIRECTIVE_PARAMS)

# §11.4 opener commitment: ``{{`` immediately followed by a name and ``:``.
_OPENER_RE = re.compile(r"\{\{([a-z]+):")
_PARAM_NAME_RE = re.compile(r"[a-z][a-z0-9-]*=")
_WS = " \t"

# §11.4: directives and anchor markers are not recognized inside inline code
# spans, fenced code blocks, or HTML comments.
_CODE_SPAN_RE = re.compile(r"``.*?``|`[^`\n]*`", re.DOTALL)
_FENCED_CODE_RE = re.compile(r"^(?P<fence>```|~~~).*?^(?P=fence)", re.DOTALL | re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_uninterpreted(text: str) -> str:
    """Blank out code spans, code blocks, and comments, preserving offsets.

    Directive-like text in those regions is literal (§11.4); blanking it keeps
    it out of every scan without shifting the position of anything else.
    """
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))

    text = _FENCED_CODE_RE.sub(_blank, text or "")
    text = _HTML_COMMENT_RE.sub(_blank, text)
    return _CODE_SPAN_RE.sub(_blank, text)


@dataclass(slots=True)
class Directive:
    """One directive occurrence in a piece of text.

    Values are decoded: quoting and escapes are removed and unquoted values
    trimmed (§11.3). ``positional`` is ``None`` when there is no positional
    value. A repeated parameter keeps its first value in ``params`` and is
    listed in ``duplicates``. ``malformed`` describes the §11.2 violation, or
    is empty for a well-formed directive; a malformed directive carries no
    arguments, and ``source`` runs through the first ``}}`` on its line, or to
    the end of the line.
    """

    name: str
    positional: str | None
    params: dict[str, str]
    duplicates: tuple[str, ...]
    malformed: str
    start: int
    end: int
    source: str

    def unknown_params(self) -> list[str]:
        """Named parameters this directive does not define, in source order.

        Empty for a directive name outside the vocabulary, which is reported
        as an unknown directive instead.
        """
        if self.name not in DIRECTIVE_PARAMS:
            return []
        return [param for param in self.params if not self._defines(param)]

    def _defines(self, param: str) -> bool:
        if param not in DIRECTIVE_PARAMS[self.name]:
            return False
        required_type = _TYPE_SPECIFIC_PARAMS.get((self.name, param))
        return required_type is None or self.params.get("type", "text") == required_type


class _Malformed(Exception):
    pass


_UNCLOSED = "not closed with '}}' on the same line"


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in _WS:
        pos += 1
    return pos


def _lex_value(text: str, pos: int) -> tuple[str, int]:
    """Lex one value at *pos*; return it decoded, with the offset of the
    ``,`` or ``}}`` that terminates it."""
    pos = _skip_ws(text, pos)
    if text.startswith('"', pos):
        chars: list[str] = []
        pos += 1
        while True:
            if pos >= len(text) or text[pos] == "\n":
                raise _Malformed("unterminated quoted value")
            if text[pos] == "\\" and text[pos + 1:pos + 2] in ('"', "\\"):
                chars.append(text[pos + 1])
                pos += 2
            elif text[pos] == '"':
                break
            else:
                chars.append(text[pos])
                pos += 1
        pos = _skip_ws(text, pos + 1)
        if not text.startswith((",", "}}"), pos):
            if pos >= len(text) or text[pos] == "\n":
                raise _Malformed(_UNCLOSED)
            raise _Malformed("text after a quoted value")
        return "".join(chars), pos
    start = pos
    while not text.startswith((",", "}}"), pos):
        if pos >= len(text) or text[pos] == "\n":
            raise _Malformed(_UNCLOSED)
        pos += 1
    value = text[start:pos].strip(_WS)
    if not value:
        # An argument must have content; ``label=`` names its parameter, so
        # only a bare empty argument (a stray comma) gets here.
        raise _Malformed("empty argument")
    return value, pos


def _lex_named_value(text: str, pos: int) -> tuple[str, int]:
    """Lex a named parameter's value, which may be empty (``label=``)."""
    end = _skip_ws(text, pos)
    if text.startswith((",", "}}"), end):
        return "", end
    return _lex_value(text, pos)


def _lex_arguments(
    text: str, pos: int
) -> tuple[str | None, dict[str, str], list[str], int]:
    """Lex the arguments after a directive opener.

    Returns ``(positional, params, duplicates, end)``, where *end* is the
    offset just past the closing ``}}``.
    """
    positional: str | None = None
    params: dict[str, str] = {}
    duplicates: list[str] = []
    pos = _skip_ws(text, pos)
    if text.startswith("}}", pos):
        return positional, params, duplicates, pos + 2
    while True:
        pos = _skip_ws(text, pos)
        named = _PARAM_NAME_RE.match(text, pos)
        if named:
            param = named.group(0)[:-1]
            value, pos = _lex_named_value(text, named.end())
            if param in params:
                duplicates.append(param)
            else:
                params[param] = value
        else:
            value, pos = _lex_value(text, pos)
            if positional is not None:
                raise _Malformed("more than one positional value")
            if params:
                raise _Malformed("positional value after a named parameter")
            positional = value
        if text.startswith("}}", pos):
            return positional, params, duplicates, pos + 2
        pos += 1  # past the comma


def _is_escaped(text: str, pos: int) -> bool:
    """True if the brace at *pos* is backslash-escaped (§11.4): preceded by an
    odd number of backslashes, as ``\\\\`` is itself a literal backslash."""
    backslashes = 0
    while pos - backslashes > 0 and text[pos - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _malformed_source(text: str, start: int) -> str:
    """The span to quote for a malformed directive: through the first ``}}``
    on its line, else to the end of the line."""
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    close = text.find("}}", start, line_end)
    return text[start:close + 2 if close >= 0 else line_end]


def iter_directives(text: str) -> Iterator[Directive]:
    """Yield every directive in *text* in order, well-formed or malformed.

    Openers inside code spans, code blocks, and comments are literal (§11.4)
    and skipped. Once a directive opens, its arguments are lexed from the
    source as written, so a quoted value may contain backticks.
    """
    scan = strip_uninterpreted(text)  # same offsets as text
    pos = 0
    while opener := _OPENER_RE.search(scan, pos):
        start = opener.start()
        if _is_escaped(text, start):
            pos = start + 1
            continue
        try:
            positional, params, duplicates, end = _lex_arguments(text, opener.end())
        except _Malformed as exc:
            source = _malformed_source(text, start)
            end = start + len(source)
            yield Directive(
                name=opener.group(1),
                positional=None,
                params={},
                duplicates=(),
                malformed=str(exc),
                start=start,
                end=end,
                source=source,
            )
        else:
            yield Directive(
                name=opener.group(1),
                positional=positional,
                params=params,
                duplicates=tuple(duplicates),
                malformed="",
                start=start,
                end=end,
                source=text[start:end],
            )
        if scan[start:end] != text[start:end]:
            # A backtick or comment marker inside the directive was taken
            # for literal-region syntax; recompute the regions after it.
            scan = scan[:end] + strip_uninterpreted(text[end:])
        pos = end


def format_value(value: str, *, positional: bool = False) -> str:
    """Spell *value* as directive source, quoting it only where §11.3 must.

    The inverse of the lexer: ``iter_directives`` decodes the result back to
    *value*.
    """
    needs_quotes = (
        (positional and not value)
        or "," in value
        or "}" in value
        or value.startswith('"')
        or value != value.strip(_WS)
        or (positional and _PARAM_NAME_RE.match(value) is not None)
    )
    if not needs_quotes:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
