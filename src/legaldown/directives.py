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

from .markdown import FENCE_OPEN_RE, HTML_COMMENT_RE, fence_end

# Named parameters each directive defines (§6, §7, §10, §12, §15.5). ``note`` is
# defined for every field spec (§10.1). Placeholder ``currency`` and ``unit``
# are type-specific: they are defined only when the effective type is
# ``money`` or ``duration`` (§10.7, §13.5 rule 7), which the validator checks.
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
    "placeholder": frozenset({"type", "currency", "unit", "note"}),
    "include": frozenset(),
    "attach": frozenset({"label"}),
    # Its parameters are the answers of the question it names (§15.5), so
    # the document, not this vocabulary, defines them.
    "choose": frozenset(),
}

# Directives whose named parameters the document defines: they are checked
# against it (choose-invalid), never reported as unknown parameters.
_DOCUMENT_DEFINED_PARAMS: frozenset[str] = frozenset({"choose"})

# Parameters defined only for one value of the directive's ``type`` (§10.7):
# a placeholder takes ``currency`` only when its effective type is ``money``,
# and ``unit`` only when it is ``duration``.
PLACEHOLDER_TYPE_PARAMS: dict[str, str] = {"money": "currency", "duration": "unit"}
_TYPE_SPECIFIC_PARAMS: dict[tuple[str, str], str] = {
    ("placeholder", param): placeholder_type
    for placeholder_type, param in PLACEHOLDER_TYPE_PARAMS.items()
}

# Typographic double quotation marks (§11.3). Only the straight ``"`` quotes
# a value, so an unquoted value that begins with one of these usually means
# an editor curled an intended ``"`` (value-curly-quote).
CURLY_DOUBLE_QUOTES = "“”„«»"

# Directive vocabulary defined by §11.1.
KNOWN_DIRECTIVES: frozenset[str] = frozenset(DIRECTIVE_PARAMS)

# §11.4 opener commitment: ``{{`` immediately followed by a name and ``:``.
_OPENER_RE = re.compile(r"\{\{([a-z]+):")
_PARAM_NAME_RE = re.compile(r"[a-z][a-z0-9-]*=")
_WS = " \t"

# §11.4: directives and anchor markers are not recognized inside fenced code
# blocks, HTML comments, or inline code spans. Inline, the construct that
# opens first wins (CommonMark): a directive, a comment, or a code span.
_INLINE_START_RE = re.compile(r"\{\{|<!--|`+")
_BACKTICKS_RE = re.compile(r"`+")


def _blank(text: str, start: int, end: int) -> str:
    """*text* with ``text[start:end]`` blanked, line breaks kept."""
    blanked = "".join("\n" if ch == "\n" else " " for ch in text[start:end])
    return text[:start] + blanked + text[end:]


def _blank_fenced_code(text: str) -> str:
    """*text* with its fenced code blocks blanked. A fence needs lines of its
    own, so single-line text (a paragraph, which the parser joins onto one
    line, or a table cell) has none."""
    if "\n" not in text:
        return text
    lines = text.split("\n")
    index = 0
    while index < len(lines):
        opening = FENCE_OPEN_RE.match(lines[index])
        if opening is None:
            index += 1
            continue
        end = fence_end(lines, index, opening.group("fence"))
        lines[index:end] = [" " * len(line) for line in lines[index:end]]
        index = end
    return "\n".join(lines)


@dataclass(slots=True)
class Directive:
    """One directive occurrence in a piece of text.

    Values are decoded: quoting and escapes are removed and unquoted values
    trimmed (§11.3). ``positional`` is ``None`` when there is no positional
    value. A repeated parameter keeps its first value in ``params`` and is
    listed in ``duplicates``. ``unquoted`` lists each argument written without
    quotes, as ``(parameter, value)`` with ``""`` for the positional value,
    in source order, repeats included. ``malformed`` describes the §11.2 violation, or
    is empty for a well-formed directive; a malformed directive carries no
    arguments, and runs through the first ``}}`` on its line or up to the next
    directive opener (§11.4 opener commitment), whichever comes first.
    """

    name: str
    positional: str | None
    params: dict[str, str]
    duplicates: tuple[str, ...]
    malformed: str
    start: int
    end: int
    source: str
    unquoted: tuple[tuple[str, str], ...] = ()

    def curly_quoted(self) -> list[tuple[str, str]]:
        """The ``unquoted`` arguments whose value begins with a typographic
        double quotation mark: probably an intended ``"`` (§11.3)."""
        return [(param, value) for param, value in self.unquoted if value[0] in CURLY_DOUBLE_QUOTES]

    def unknown_params(self, *, effective_type: str | None = None) -> list[str]:
        """Named parameters this directive does not define, in source order.

        A type-specific parameter is defined only for its type: the
        *effective_type* when the caller knows it (a placeholder's type can
        come from its declared question, §15.2), else the ``type`` written in
        the directive, defaulting to ``text``. Empty for a directive name
        outside the vocabulary, which is reported as an unknown directive
        instead, and for ``{{choose:}}``, whose parameters are the answers
        to its question (§15.5).
        """
        if self.name not in DIRECTIVE_PARAMS or self.name in _DOCUMENT_DEFINED_PARAMS:
            return []
        directive_type = effective_type or self.params.get("type", "text")
        return [param for param in self.params if not self._defines(param, directive_type)]

    def _defines(self, param: str, directive_type: str) -> bool:
        if param not in DIRECTIVE_PARAMS[self.name]:
            return False
        required_type = _TYPE_SPECIFIC_PARAMS.get((self.name, param))
        return required_type is None or directive_type == required_type


def mask_directives(text: str, directives: list[Directive]) -> str:
    """*text* with each of *directives* made opaque: the same length, filled
    with a character that is neither spacing, a quotation mark, nor
    Markdown punctuation, so a scan of the text around them cannot mistake
    their contents (a quoted value, a space) for the surrounding text's."""
    chars = list(text)
    for directive in directives:
        chars[directive.start:directive.end] = "\x00" * (directive.end - directive.start)
    return "".join(chars)


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
        if _opens_directive(text, pos):
            # §11.4 opener commitment: the next directive begins here, so
            # this one was never closed.
            raise _Malformed("not closed before the next directive")
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
) -> tuple[str | None, dict[str, str], list[str], list[tuple[str, str]], int]:
    """Lex the arguments after a directive opener.

    Returns ``(positional, params, duplicates, unquoted, end)``, where
    *unquoted* is ``Directive.unquoted`` and *end* is the offset just past
    the closing ``}}``.
    """
    positional: str | None = None
    params: dict[str, str] = {}
    duplicates: list[str] = []
    unquoted: list[tuple[str, str]] = []
    pos = _skip_ws(text, pos)
    if text.startswith("}}", pos):
        return positional, params, duplicates, unquoted, pos + 2
    while True:
        pos = _skip_ws(text, pos)
        named = _PARAM_NAME_RE.match(text, pos)
        if named:
            param = named.group(0)[:-1]
            # Quoting is syntax, lost on decoding (§11.3): note it first.
            quoted = text.startswith('"', _skip_ws(text, named.end()))
            value, pos = _lex_named_value(text, named.end())
            if param in params:
                duplicates.append(param)
            else:
                params[param] = value
        else:
            param = ""
            quoted = text.startswith('"', pos)
            value, pos = _lex_value(text, pos)
            if positional is not None:
                raise _Malformed("more than one positional value")
            if params:
                raise _Malformed("positional value after a named parameter")
            positional = value
        if value and not quoted:
            unquoted.append((param, value))
        if text.startswith("}}", pos):
            return positional, params, duplicates, unquoted, pos + 2
        pos += 1  # past the comma


def is_escaped(text: str, pos: int) -> bool:
    """True if the brace at *pos* is backslash-escaped (§11.4): preceded by an
    odd number of backslashes, as ``\\\\`` is itself a literal backslash."""
    backslashes = 0
    while pos - backslashes > 0 and text[pos - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _opens_directive(text: str, pos: int) -> bool:
    """True if an unescaped directive opener (§11.4) starts at *pos*."""
    return bool(_OPENER_RE.match(text, pos)) and not is_escaped(text, pos)


def _malformed_end(text: str, start: int, body: int) -> int:
    """End of a malformed directive opened at *start*, whose arguments begin
    at *body*: through the first ``}}`` on its line, but never past the next
    directive opener, which begins a directive of its own (§11.4)."""
    line_end = text.find("\n", start)
    if line_end < 0:
        line_end = len(text)
    close = text.find("}}", body, line_end)
    end = close + 2 if close >= 0 else line_end
    return next((pos for pos in range(body, end) if _opens_directive(text, pos)), end)


@dataclass(slots=True)
class Lexed:
    """What the lexer found in a piece of text.

    ``view`` has the same offsets as the text, with fenced code, comments,
    and code spans blanked. Callers that look around directives (the defined
    term before a ``{{def:}}``, anchor markers) use it so they agree with the
    lexer about what is literal.
    """

    directives: list[Directive]
    stray_braces: list[int]  # offsets of each ``{{`` that opens no directive
    view: str


def lex(text: str) -> Lexed:
    """Lex *text* for directives, outside literal regions (§11.4).

    Fenced code blocks are found first, by line, as block structure precedes
    inline structure. The rest is read once, left to right, taking whichever
    of a directive, a comment, or a code span opens first. A directive is
    lexed from the source as written and consumes its own text, so a quoted
    value may hold backticks or ``<!--`` without opening anything. Any other
    unescaped ``{{`` is literal text that is usually a typo (a stray brace).
    """
    view = _blank_fenced_code(text or "")
    directives: list[Directive] = []
    stray_braces: list[int] = []
    pos = 0
    while token := _INLINE_START_RE.search(view, pos):
        start = token.start()
        if is_escaped(view, start):
            pos = start + 1
            continue
        if token.group(0) == "<!--":
            comment = HTML_COMMENT_RE.match(view, start)
            if comment is None:
                pos = start + 4  # an unclosed comment is literal text
            else:
                view = _blank(view, start, comment.end())
                pos = comment.end()
            continue
        if token.group(0).startswith("`"):
            ticks = len(token.group(0))
            close = next(
                (m for m in _BACKTICKS_RE.finditer(view, token.end()) if len(m.group(0)) == ticks),
                None,
            )
            if close is None:
                pos = token.end()  # an unmatched backtick run is literal text
            else:
                view = _blank(view, start, close.end())
                pos = close.end()
            continue
        opener = _OPENER_RE.match(view, start)
        if opener is None:
            stray_braces.append(start)
            pos = start + 1
            continue
        directive = _lex_directive(text, start, opener)
        directives.append(directive)
        pos = directive.end
    return Lexed(directives, stray_braces, view)


def _lex_directive(text: str, start: int, opener: re.Match[str]) -> Directive:
    """Lex the directive whose opener (``{{name:``) is *opener*."""
    try:
        positional, params, duplicates, unquoted, end = _lex_arguments(text, opener.end())
    except _Malformed as exc:
        end = _malformed_end(text, start, opener.end())
        return Directive(
            name=opener.group(1),
            positional=None,
            params={},
            duplicates=(),
            malformed=str(exc),
            start=start,
            end=end,
            source=text[start:end].rstrip(_WS),
        )
    return Directive(
        name=opener.group(1),
        positional=positional,
        params=params,
        duplicates=tuple(duplicates),
        malformed="",
        start=start,
        end=end,
        source=text[start:end],
        unquoted=tuple(unquoted),
    )


def iter_directives(text: str) -> Iterator[Directive]:
    """Yield every directive in *text* in order, well-formed or malformed.

    Openers inside code spans, code blocks, and comments are literal (§11.4)
    and skipped. Once a directive opens, its arguments are lexed from the
    source as written, so a quoted value may contain backticks.
    """
    return iter(lex(text).directives)


def format_value(value: str, *, positional: bool = False) -> str:
    """Spell *value* as directive source, quoting it only where §11.3 must.

    The inverse of the lexer: ``iter_directives`` decodes the result back to
    *value*. Raises ``ValueError`` for a line break, which no directive value
    can hold.
    """
    if "\n" in value or "\r" in value:
        raise ValueError(f"A directive value cannot contain a line break (§11.3): {value!r}")
    needs_quotes = (
        (positional and not value)
        or "{{" in value
        or "," in value
        or "}" in value
        or value.startswith('"')
        # Unquoted, it would read as an auto-curled quote (value-curly-quote).
        or value[:1] in CURLY_DOUBLE_QUOTES
        or value != value.strip(_WS)
        or (positional and _PARAM_NAME_RE.match(value) is not None)
    )
    if not needs_quotes:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
