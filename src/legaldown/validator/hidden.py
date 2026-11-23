"""Constructs that take content out of validation without a word (#12): this implementation's own rules
(§16.1, the prefix ``ldv-``), as the specification gives none for them.

**``ldv-fence-unclosed``** (Warning). A fenced code block that is never closed runs to the end of the file
(CommonMark), so that every heading, directive and paragraph after it is code: nothing in it is a section,
and no rule reads it (§11.4). The construct is valid, and the specification has no rule for it, so it is a
Warning, at the fence's opening line, saying how much it holds. A fence that its list item or quote ends is
not reported: the container closes it, and what follows the container is read as usual. One in a container
that itself runs to the end of the file is, as nothing but the end of the file closes it.

**``ldv-heading-directive``** (Error). Heading text must not contain a directive (§4.2), and a directive is
recognized in body text only (§11.4), so one written in a section's heading is literal text that no rule
checks: a ``{{def:}}`` declares nothing, and the term it was to define is ``term-undefined`` where it is used;
assembly leaves a ``{{placeholder:}}`` there unfilled. It is
an Error, as §4.2 is a MUST NOT and the directive is lost whatever it was meant to do; no rule of the
specification's covers it (``heading-hardcoded-number`` is the numbering of §4.2, and a heuristic). The
usual cause is a setext heading: a paragraph directly above a ``---`` or ``===`` line is a heading, and the
message names that line. A heading in a list item or a quote is a heading too, and is reported the same way (this implementation
reads its text as body text, so its directives are checked as well). A ``{{choose:}}`` in a section's heading is
left to ``choose-invalid``, the specification's rule for it (§15.5).

Both judge each file by itself: the document, or a fragment or attachment file on its own; in the combined
document (§16.11, ``combined.Combined``), each file at its place, as every file keeps its own structure
there (a fence a fragment leaves open does not run into the document). So the two passes agree, and the
combined pass, which reads the fragments too, says it once (``combined.merge_diagnostics``).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from ..directives import Lexed
from ..markdown import FENCE_OPEN_RE, closes_fence, code_content, fence_end
from ..models import LIST_KINDS, Block, Document
from ..positions import Locator
from .patterns import FENCE_UNCLOSED_RULE, HEADING_DIRECTIVE_RULE
from .result import Line, _Recorder

if TYPE_CHECKING:
    from .combined import Combined
    from .fragments import FragmentKind

#: What a file is called in a message, by what it is to the document (None: the document itself).
_NAMES: dict[FragmentKind | None, str] = {None: "document", "include": "fragment", "attachment": "attachment file"}

# An ATX heading's line, and a setext underline, in a code block's content: for the count of what it holds.
_ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
_SETEXT_RE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")


def _files(
    document: Document, where: Locator, combined: Combined | None, fragment: FragmentKind | None
) -> Iterator[tuple[Document, Locator, Callable[[int | None], Line], str]]:
    """Each file to judge: its document, its locator, how a line of it is given to a diagnostic, and what
    it is called in a message. *fragment*: what the document validated on its own is to its host, if it is
    a fragment (``validate(fragment=True)``)."""
    if combined is None:
        yield document, where, lambda line: line, _NAMES[fragment]
        return
    for place in combined.instances:
        name = _NAMES[None if not place.path else "attachment" if place.attachment is not None else "include"]
        yield place.document, place.where, place.spot, name


def _last_block(document: Document) -> tuple[int | None, int, Block] | None:
    """The top-level block the file ends with, as ``(section, index, block)``: None when it ends with a
    heading, or has no body."""
    if document.sections:
        blocks = document.sections[-1].blocks
        return (len(document.sections) - 1, len(blocks) - 1, blocks[-1]) if blocks else None
    return (None, len(document.preamble) - 1, document.preamble[-1]) if document.preamble else None


def _innermost_last(block: Block) -> Block | None:
    """The block that *block* ends with, in its last list item or its quote, as deep as they go: the one
    the end of the file closes when *block* ends the file, and the last that ``nested_blocks`` gives of it.
    None when a container that ends it holds no block (an empty item or quote)."""
    from ..definitions import list_items  # the parser builds on that module
    from ..parser import MAX_QUOTE_DEPTH, quote_content

    depth = 0
    while True:
        if block.kind in LIST_KINDS:
            items = list_items(block)
            if not items or not items[-1].blocks:
                return None
            block = items[-1].blocks[-1]
        elif block.kind == "quote" and depth < MAX_QUOTE_DEPTH:
            children, _spans = quote_content(block.text, depth + 1)
            if not children:
                return None
            block = children[-1]
        else:
            return block
        depth += 1


def _open_fence(block: Block) -> str | None:
    """The fence of *block* if it is a fenced code block that no line of it closes, else None. (A block built
    in code may hold text after its closing fence, which is no code, ``definitions.block_fragments``.)"""
    if block.kind != "code" or not block.text:
        return None
    lines = block.text.split("\n")
    opening = FENCE_OPEN_RE.match(lines[0])
    if opening is None:
        return None
    fence = opening.group("fence")
    end = fence_end(lines, 0, fence)
    return None if end - 1 > 0 and closes_fence(lines[end - 1], fence) else fence


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _headings_in(lines: list[str]) -> int:
    """How many of *lines*, read as LegalDown, would be headings: ATX lines, and the last line of a paragraph
    over a setext underline (a ``---`` after a blank line is a thematic break)."""
    return sum(
        1 for number, line in enumerate(lines)
        if _ATX_RE.match(line) or (
            number + 1 < len(lines) and _SETEXT_RE.match(lines[number + 1])
            and line.strip() and not _SETEXT_RE.match(line)
        )
    )


def _fence_message(block: Block, fence: str, name: str, lex_fragment: Callable[[str], Lexed]) -> str:
    lines = code_content(block).text.splitlines()
    if not lines:
        held = "Nothing follows it"
    else:
        headings = _headings_in(lines)
        directives = len(lex_fragment("\n".join(lines)).directives)
        among = [_count(number, noun) for number, noun in ((headings, "heading"), (directives, "directive")) if number]
        held = (
            f"It holds the rest of the {name}, {_count(len(lines), 'line')}"
            f"{' with ' + _join(among) if among else ''}, as code: no section, directive or definition in it "
            f"is read or checked (§11.4)"
        )
    spliced = (
        " (and, where a tool splices the fragment's text into the document, §12.2, on through what follows "
        "its include)"
        if name == "fragment" else ""
    )
    return (
        f"The code block opened by '{fence}' at this line is never closed, so it runs to the end of the "
        f"{name}{spliced} (CommonMark). {held}. Close it with a line of '{fence}'."
    )


def check_unclosed_fences(
    document: Document,
    where: Locator,
    result: _Recorder,
    lex_fragment: Callable[[str], Lexed],
    *,
    combined: Combined | None = None,
    fragment: FragmentKind | None = None,
) -> None:
    """``ldv-fence-unclosed``: a fenced code block of a file that the end of the file closes, at its
    opening line."""
    for own, locator, spot, name in _files(document, where, combined, fragment):
        last = _last_block(own)
        if last is None:
            continue
        section, index, top = last
        block = _innermost_last(top)
        fence = _open_fence(block) if block is not None else None
        if block is None or fence is None:
            continue
        # The block the file ends with is the last of its top-level block's, in pre-order. (No line when the
        # layout no longer fits the model: the container's would not be the fence's.)
        spans = locator.nested_spans(section, index)
        line = spans[-1][0] if spans else None
        result.warning(FENCE_UNCLOSED_RULE, _fence_message(block, fence, name, lex_fragment), line=spot(line))


def _shown(directives: list[str]) -> str:
    quoted = [f"'{source}'" for source in directives[:2]]
    if len(directives) > 2:
        quoted.append(f"{len(directives) - 2} more")
    return _join(quoted)


def _heading_message(text: str, found: list[str], underline: tuple[int, str] | None) -> str:
    """The message of ``ldv-heading-directive`` for the heading *text* holding the directives *found*, with
    its setext *underline* (its line and what is written there), if it has one."""
    lost = (
        "it is not recognized there (§11.4), so it is shown as written and what it was to do is not done"
        if len(found) == 1 else
        "they are not recognized there (§11.4), so they are shown as written and what they were to do is not done"
    )
    setext = ""
    if underline is not None:
        line, written = underline
        keep = (
            "put a blank line before it to keep the paragraph, and the line a thematic break"
            if written.startswith("-") else "remove it, or put a blank line before it, to keep the paragraph"
        )
        setext = (
            f" The '{written}' line under it at line {line} is what makes this paragraph a heading (a setext "
            f"heading, CommonMark): {keep}."
        )
    return (
        f"The heading '{text}' holds {_shown(found)}, but heading text must not contain a directive (§4.2): "
        f"{lost}.{setext}"
    )


def _underline(locator: Locator, line: int | None) -> tuple[int, str] | None:
    """A setext underline at *line* of the file, and what is written there: without the indentation and
    the ``>`` of the containers it is in."""
    body = locator.body()
    if line is None or body is None:
        return None
    lines, first = body
    written = lines[line - first] if 0 <= line - first < len(lines) else ""
    return line, re.sub(r"^[ \t>]*", "", written).strip() or "---"


def _nested_headings(top: Block) -> Iterator[tuple[int, Block]]:
    """The ``heading`` blocks in the list items and quotes of the top-level block *top*, each with its
    place among the blocks ``nested_blocks`` gives."""
    from ..definitions import nested_blocks  # the parser builds on that module

    if top.kind not in LIST_KINDS and top.kind != "quote":
        return
    for number, block in enumerate(nested_blocks(top)):
        if block.kind == "heading":
            yield number, block


def check_heading_directives(
    document: Document,
    where: Locator,
    result: _Recorder,
    lex_fragment: Callable[[str], Lexed],
    *,
    combined: Combined | None = None,
    fragment: FragmentKind | None = None,
) -> None:
    """``ldv-heading-directive``: a heading whose text holds a directive (§4.2), once for each heading, at
    its first line."""
    for own, locator, spot, _name in _files(document, where, combined, fragment):
        for index, section in enumerate(own.sections):
            # (A ``{{choose:}}`` in a heading is ``choose-invalid``'s, §15.5.)
            found = [d.source for d in lex_fragment(section.title).directives if d.name != "choose"]
            if found:
                underline = _underline(locator, locator.underline(index))
                result.error(
                    HEADING_DIRECTIVE_RULE, _heading_message(section.title, found, underline),
                    line=spot(locator.heading(index)),
                )
        for section_index, block_index, top in own.iter_indexed_blocks():
            spans: list[tuple[int, int]] | None = None
            for number, block in _nested_headings(top):
                found = [d.source for d in lex_fragment(block.text).directives]
                if not found:
                    continue
                if spans is None:
                    spans = locator.nested_spans(section_index, block_index) or []
                start, end = spans[number] if number < len(spans) else (None, None)
                underline = _underline(locator, end - 1) if start is not None and end - start > 1 else None
                result.error(
                    HEADING_DIRECTIVE_RULE, _heading_message(block.text, found, underline), line=spot(start)
                )
