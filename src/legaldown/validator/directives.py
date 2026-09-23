"""Directive lexer for the §11.2 grammar.

A directive is recognized in two steps: first its generic shape
``{{name: argument, argument, ...}}`` is lexed, then the named parameters are
checked against the set defined for that directive. Recognizing the shape
before the vocabulary means a parameter a directive does not define, a
parameter given out of the conventional order, or a quoted value (§11.3)
never stops the directive's own checks from running.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..models import Block
from ..serializer import render_block
from .patterns import DIRECTIVE_NAME_RE

_PARAM_NAME_RE = re.compile(r"[a-z][a-z0-9-]*=")
_WS = " \t"

# Named parameters each directive defines (§6, §7, §10, §12). ``note`` is
# defined for every field spec (§10.1). Placeholder ``currency`` is
# type-specific: it is defined only when the effective type is ``money``
# (§10.7, §13.5 rule 7), which the validator checks separately.
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


@dataclass(frozen=True)
class Directive:
    """One directive occurrence, lexed per §11.2 and §11.3.

    Values are decoded: quoting and escapes are removed and unquoted values
    are trimmed. ``positional`` is ``None`` when the directive has no
    positional value. A repeated parameter keeps its first value in
    ``params`` and is listed in ``duplicates``. ``malformed`` is empty for a
    well-formed directive, otherwise a description of the grammar violation;
    ``end`` then points just past the opener.
    """

    name: str
    positional: str | None
    params: dict[str, str] = field(default_factory=dict)
    duplicates: tuple[str, ...] = ()
    malformed: str = ""
    start: int = 0
    end: int = 0
    source: str = ""

    def unknown_params(self) -> list[str]:
        """Named parameters not defined for this directive, in source order."""
        allowed = DIRECTIVE_PARAMS.get(self.name)
        if allowed is None:
            return []
        return [name for name in self.params if name not in allowed]


class _Malformed(Exception):
    pass


def _lex_quoted(text: str, pos: int) -> tuple[str, int]:
    """Lex a quoted value whose opening ``"`` is at *pos*; return (value, end)."""
    out: list[str] = []
    i = pos + 1
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            break
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in '"\\':
            out.append(text[i + 1])
            i += 2
            continue
        if ch == '"':
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise _Malformed("unterminated quoted value")


def _lex_value(text: str, pos: int) -> tuple[str, int]:
    """Lex a quoted or unquoted value starting at *pos*; return (value, end).

    The returned end sits on the ``,`` or ``}}`` that terminates the value.
    """
    while pos < len(text) and text[pos] in _WS:
        pos += 1
    if pos < len(text) and text[pos] == '"':
        value, pos = _lex_quoted(text, pos)
        while pos < len(text) and text[pos] in _WS:
            pos += 1
        if not (text.startswith(",", pos) or text.startswith("}}", pos)):
            raise _Malformed("unexpected text after a quoted value")
        return value, pos
    start = pos
    while pos < len(text):
        if text[pos] == "," or text.startswith("}}", pos):
            return text[start:pos].strip(_WS), pos
        if text[pos] == "\n":
            break
        pos += 1
    raise _Malformed("directive is not closed with '}}' on the same line")


def _lex_arguments(text: str, pos: int, name: str, start: int) -> Directive:
    positional: str | None = None
    params: dict[str, str] = {}
    duplicates: list[str] = []
    seen_named = False
    first = True
    while True:
        while pos < len(text) and text[pos] in _WS:
            pos += 1
        if first and text.startswith("}}", pos):
            end = pos + 2
            break
        first = False
        named = _PARAM_NAME_RE.match(text, pos)
        if named:
            param = named.group(0)[:-1]
            value, pos = _lex_value(text, named.end())
            if param in params:
                duplicates.append(param)
            else:
                params[param] = value
            seen_named = True
        else:
            value, pos = _lex_value(text, pos)
            if positional is not None:
                raise _Malformed("more than one positional value")
            if seen_named:
                raise _Malformed("positional value after a named parameter")
            positional = value
        if text.startswith("}}", pos):
            end = pos + 2
            break
        pos += 1  # the separating comma
    return Directive(
        name=name,
        positional=positional,
        params=params,
        duplicates=tuple(duplicates),
        start=start,
        end=end,
        source=text[start:end],
    )


def iter_directives(text: str) -> Iterator[Directive]:
    """Yield every directive in *text*, well-formed or malformed, in order.

    Callers are expected to have blanked uninterpreted regions (code spans,
    code blocks, comments; §11.4) beforehand.
    """
    pos = 0
    while True:
        opener = DIRECTIVE_NAME_RE.search(text, pos)
        if not opener:
            return
        name = opener.group(1)
        try:
            directive = _lex_arguments(text, opener.end(), name, opener.start())
        except _Malformed as exc:
            line_end = text.find("\n", opener.start())
            directive = Directive(
                name=name,
                positional=None,
                malformed=str(exc),
                start=opener.start(),
                end=opener.end(),
                source=text[opener.start():line_end if line_end >= 0 else len(text)],
            )
        yield directive
        pos = directive.end


def directive_fragments(block: Block) -> list[str]:
    """The text of *block* in which directives are recognized.

    The parser lifts one ``{{ref:}}``, ``{{term:}}``, or leading ``{{def:}}``
    out of the paragraph text into block fields; those blocks are rendered
    back to source so the lifted directive is lexed like any other.
    """
    if block.kind == "definition" or (
        block.kind in ("ref", "term") and block.target.strip()
    ):
        return [render_block(block)]
    fragments = [f for f in (block.text, block.prefix, block.suffix) if f]
    fragments.extend(item for item in block.items if item)
    fragments.extend(cell for row in block.rows for cell in row if cell)
    return fragments


def parse_def_id(raw: str) -> str:
    """Return the identifier of a ``{{def:}}`` from its raw argument text.

    ``raw`` is everything between ``def:`` and ``}}``. Parameters, which
    ``{{def:}}`` does not define, are not part of the id; the directive scan
    reports them separately. Falls back to the stripped raw text when the
    arguments are malformed, so the identifier checks still see it.
    """
    try:
        directive = _lex_arguments(raw + "}}", 0, "def", 0)
    except _Malformed:
        return raw.strip()
    return directive.positional or ""
