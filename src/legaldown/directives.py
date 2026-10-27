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
from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass, field

from .markdown import FENCE_OPEN_RE, comment_end, fence_end

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


_NOT_LINE_BREAK_RE = re.compile(r"[^\n]")


def _blank_spans(text: str, spans: list[tuple[str, int, int]]) -> str:
    """*text* with each of the ordered, disjoint *spans* (``Lexed.literals``)
    blanked, line breaks kept: one pass, however many there are."""
    kept: list[str] = []
    pos = 0
    for _kind, start, end in spans:
        kept.append(text[pos:start])
        kept.append(_NOT_LINE_BREAK_RE.sub(" ", text[start:end]))
        pos = end
    kept.append(text[pos:])
    return "".join(kept)


def blank_fenced_code(text: str) -> str:
    """*text* — text holding blocks, such as a code block's or a block
    quote's — with its fenced code blocks blanked, line breaks kept. Not a
    paragraph's: a fence cannot interrupt one, so a line of it that opens
    like one (read without its indentation, or lazy) is text."""
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

    ``positional_span`` and ``param_spans`` say where the values are: the
    offsets ``(start, end)`` in the lexed text of each value as written, quotes
    included, so ``text[start:end]`` is what to replace to change the value (a
    parameter written without a value, ``label=``, has an empty span where its
    value would go). ``param_spans`` has the first occurrence of a repeated
    parameter, as ``params`` has its value. A malformed directive has none.
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
    positional_span: tuple[int, int] | None = None
    param_spans: dict[str, tuple[int, int]] = field(default_factory=dict)

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


def _lex_value(text: str, pos: int) -> tuple[str, tuple[int, int], int]:
    """Lex one value at *pos*; return it decoded, where it is written (quotes
    included), and the offset of the ``,`` or ``}}`` that terminates it."""
    pos = _skip_ws(text, pos)
    if text.startswith('"', pos):
        chars: list[str] = []
        opening = pos
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
        closing = pos + 1
        pos = _skip_ws(text, closing)
        if not text.startswith((",", "}}"), pos):
            if pos >= len(text) or text[pos] == "\n":
                raise _Malformed(_UNCLOSED)
            raise _Malformed("text after a quoted value")
        return "".join(chars), (opening, closing), pos
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
    return value, (start, start + len(value)), pos


def _lex_named_value(text: str, pos: int) -> tuple[str, tuple[int, int], int]:
    """Lex a named parameter's value, which may be empty (``label=``)."""
    end = _skip_ws(text, pos)
    if text.startswith((",", "}}"), end):
        return "", (end, end), end
    return _lex_value(text, pos)


def _lex_arguments(
    text: str, pos: int
) -> tuple[
    str | None, tuple[int, int] | None, dict[str, str], dict[str, tuple[int, int]],
    list[str], list[tuple[str, str]], int,
]:
    """Lex the arguments after a directive opener.

    Returns ``(positional, positional_span, params, param_spans, duplicates,
    unquoted, end)``, where *unquoted* is ``Directive.unquoted`` and *end* is
    the offset just past the closing ``}}``.
    """
    positional: str | None = None
    positional_span: tuple[int, int] | None = None
    params: dict[str, str] = {}
    param_spans: dict[str, tuple[int, int]] = {}
    duplicates: list[str] = []
    unquoted: list[tuple[str, str]] = []
    pos = _skip_ws(text, pos)
    if text.startswith("}}", pos):
        return positional, positional_span, params, param_spans, duplicates, unquoted, pos + 2
    while True:
        pos = _skip_ws(text, pos)
        named = _PARAM_NAME_RE.match(text, pos)
        if named:
            param = named.group(0)[:-1]
            # Quoting is syntax, lost on decoding (§11.3): note it first.
            quoted = text.startswith('"', _skip_ws(text, named.end()))
            value, span, pos = _lex_named_value(text, named.end())
            if param in params:
                duplicates.append(param)
            else:
                params[param] = value
                param_spans[param] = span
        else:
            param = ""
            quoted = text.startswith('"', pos)
            value, span, pos = _lex_value(text, pos)
            if positional is not None:
                raise _Malformed("more than one positional value")
            if params:
                raise _Malformed("positional value after a named parameter")
            positional = value
            positional_span = span
        if value and not quoted:
            unquoted.append((param, value))
        if text.startswith("}}", pos):
            return positional, positional_span, params, param_spans, duplicates, unquoted, pos + 2
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


class _Landmarks:
    """Where the line ends and the ``}}`` are in a text, found once. A text
    of many malformed directives on one line would otherwise search to the end
    of the line for each (#94)."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._line_ends: list[int] | None = None  # found at the first malformed directive
        self._closers: list[int] = []

    def line_end(self, start: int) -> int:
        """The offset of the first line break from *start* on, or the text's end."""
        if self._line_ends is None:
            self._line_ends = _find_all(self._text, "\n")
            self._closers = _find_all(self._text, "}}")
        at = bisect_left(self._line_ends, start)
        return self._line_ends[at] if at < len(self._line_ends) else len(self._text)

    def close(self, body: int, line_end: int) -> int:
        """``text.find("}}", body, line_end)``; after ``line_end``."""
        at = bisect_left(self._closers, body)
        return self._closers[at] if at < len(self._closers) and self._closers[at] + 2 <= line_end else -1


def _find_all(text: str, needle: str) -> list[int]:
    """The offsets of *needle* in *text*, overlapping ones too."""
    found: list[int] = []
    at = text.find(needle)
    while at >= 0:
        found.append(at)
        at = text.find(needle, at + 1)
    return found


def _malformed_end(text: str, start: int, body: int, landmarks: _Landmarks) -> int:
    """End of a malformed directive opened at *start*, whose arguments begin
    at *body*: through the first ``}}`` on its line, but never past the next
    directive opener, which begins a directive of its own (§11.4)."""
    line_end = landmarks.line_end(start)
    close = landmarks.close(body, line_end)
    end = close + 2 if close >= 0 else line_end
    return next((pos for pos in range(body, end) if _opens_directive(text, pos)), end)


@dataclass(slots=True)
class Lexed:
    """What the lexer found in a piece of text.

    ``view`` has the same offsets as the text, with comments and code spans
    blanked. Callers that look around directives (the defined
    term before a ``{{def:}}``, anchor markers) use it so they agree with the
    lexer about what is literal. ``literals`` lists those regions, in order, as
    ``(kind, start, end)``: *kind* is ``"comment"`` or ``"code"`` (a code
    span), and ``view[start:end]`` is what was blanked of ``text[start:end]``.
    """

    directives: list[Directive]
    stray_braces: list[int]  # offsets of each ``{{`` that opens no directive
    view: str
    literals: list[tuple[str, int, int]] = field(default_factory=list)


def lex(text: str) -> Lexed:
    """Lex *text* for directives, outside literal regions (§11.4).

    *text* is inline text, such as a paragraph's: fenced code is not looked
    for. Text that can hold it (a code block's, a block quote's) has it
    blanked first, as block structure precedes inline structure
    (``block_fragments`` gives such text so). It is
    read once, left to right, taking whichever
    of a directive, a comment, or a code span opens first. A directive is
    lexed from the source as written and consumes its own text, so a quoted
    value may hold backticks or ``<!--`` without opening anything. Any other
    unescaped ``{{`` is literal text that is usually a typo (a stray brace).
    """
    source = text or ""
    directives: list[Directive] = []
    stray_braces: list[int] = []
    literals: list[tuple[str, int, int]] = []
    pos = 0
    # Everything is sought in the source as written, and the blanked view is
    # built once at the end (#94): blanking a span changes only text before
    # ``pos``, which no search looks at, and a backslash run that ends at
    # ``pos`` is cut short by the span's last character (a ``>`` or a
    # backtick), so ``is_escaped`` reads the same.
    last_close = source.rfind("-->")  # a comment opening after it cannot close
    backtick_runs: dict[int, list[int]] | None = None  # run length -> starts, at the first run
    landmarks = _Landmarks(source)
    while token := _INLINE_START_RE.search(source, pos):
        start = token.start()
        if is_escaped(source, start):
            pos = start + 1
            continue
        if token.group(0) == "<!--":
            end = comment_end(source, start, last_close)
            if end is None:
                pos = start + 4  # an unclosed comment is literal text
            else:
                literals.append(("comment", start, end))
                pos = end
            continue
        if token.group(0).startswith("`"):
            ticks = len(token.group(0))
            if backtick_runs is None:
                backtick_runs = {}
                for run in _BACKTICKS_RE.finditer(source):
                    backtick_runs.setdefault(len(run.group(0)), []).append(run.start())
            # The first run of as many backticks as the opening one, after it.
            # (The opening one may be the end of a longer run that an escape,
            # a comment, or a directive cut short: its length may be no one's.)
            starts = backtick_runs.get(ticks, [])
            at = bisect_left(starts, token.end())
            if at == len(starts):
                pos = token.end()  # an unmatched backtick run is literal text
            else:
                end = starts[at] + ticks
                literals.append(("code", start, end))
                pos = end
            continue
        opener = _OPENER_RE.match(source, start)
        if opener is None:
            stray_braces.append(start)
            pos = start + 1
            continue
        directive = _lex_directive(text, start, opener, landmarks)
        directives.append(directive)
        pos = directive.end
    return Lexed(directives, stray_braces, _blank_spans(source, literals), literals)


def _lex_directive(text: str, start: int, opener: re.Match[str], landmarks: _Landmarks) -> Directive:
    """Lex the directive whose opener (``{{name:``) is *opener*. *landmarks*
    are the text's."""
    try:
        positional, positional_span, params, param_spans, duplicates, unquoted, end = _lex_arguments(
            text, opener.end()
        )
    except _Malformed as exc:
        end = _malformed_end(text, start, opener.end(), landmarks)
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
        positional_span=positional_span,
        param_spans=param_spans,
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
        or (value[:1] != "" and value[0] in CURLY_DOUBLE_QUOTES)
        or value != value.strip(_WS)
        or (positional and _PARAM_NAME_RE.match(value) is not None)
    )
    if not needs_quotes:
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
