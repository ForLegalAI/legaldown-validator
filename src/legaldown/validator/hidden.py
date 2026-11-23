"""Constructs that take content out of validation without a word (#12): this implementation's own rules
(§16.1, the prefix ``ldv-``), as the specification gives none for them.

**``ldv-fence-unclosed``** (Warning). A fenced code block that is never closed runs to the end of the file
(CommonMark), so that every heading, directive and paragraph after it is code: nothing in it is a section,
and no rule reads it (§11.4). The construct is valid, and the specification has no rule for it, so it is a
Warning, at the fence's opening line, saying how much it holds. A fence that its list item or quote ends is
not reported: the container closes it, and what follows the container is read as usual. One in a container
that itself runs to the end of the file is, as nothing but the end of the file closes it.

**``ldv-heading-directive``** (Error). Heading text must not contain a directive (§4.2), and a directive is
recognized in body text only (§11.4), so one written in a heading is literal text that no rule checks: a
``{{def:}}`` declares nothing, and the term it was to define is ``term-undefined`` where it is used. It is
an Error, as §4.2 is a MUST NOT and the directive is lost whatever it was meant to do; no rule of the
specification's covers it (``heading-hardcoded-number`` is the numbering of §4.2, and a heuristic). The
usual cause is a setext heading: a paragraph directly above a ``---`` line is a heading, and the message
says so. A heading in a list item or a quote is a heading too, and is reported the same way (this implementation
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
from ..markdown import FENCE_OPEN_RE, closes_fence, code_content
from ..models import LIST_KINDS, Block, Document
from ..positions import Locator
from .patterns import FENCE_UNCLOSED_RULE, HEADING_DIRECTIVE_RULE
from .result import Line, _Recorder

if TYPE_CHECKING:
    from .combined import Combined
    from .fragments import FragmentKind

#: What a file is called in a message, by what it is to the document (None: the document itself).
_NAMES: dict[FragmentKind | None, str] = {None: "document", "include": "fragment", "attachment": "attachment file"}

# An ATX heading's line in a code block's content, for the count of what the block holds.
_ATX_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")


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


def _unclosed(block: Block) -> bool:
    """True if *block* is a fenced code block without its closing fence."""
    if block.kind != "code" or not block.text:
        return False
    lines = block.text.split("\n")
    opening = FENCE_OPEN_RE.match(lines[0])
    return opening is not None and not (len(lines) > 1 and closes_fence(lines[-1], opening.group("fence")))


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _fence_message(block: Block, name: str, lex_fragment: Callable[[str], Lexed]) -> str:
    lines = code_content(block).text.splitlines()
    fence = FENCE_OPEN_RE.match(block.text).group("fence")  # type: ignore[union-attr]  # _unclosed
    if not lines:
        held = "Nothing follows it"
    else:
        headings = sum(1 for line in lines if _ATX_RE.match(line))
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
        if block is None or not _unclosed(block):
            continue
        lines = locator.nested_lines(section, index)
        # The block the file ends with is the last of its top-level block's, in pre-order.
        line = lines[-1] if lines else locator.block(section, index)
        result.warning(FENCE_UNCLOSED_RULE, _fence_message(block, name, lex_fragment), line=spot(line))


def _shown(directives: list[str]) -> str:
    quoted = [f"'{source}'" for source in directives[:2]]
    if len(directives) > 2:
        quoted.append(f"{len(directives) - 2} more")
    return _join(quoted)


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
            if not found:
                continue
            underline = locator.underline(index)
            setext = ""
            if underline is not None:
                body = locator.body()
                written = body[0][underline - body[1]].strip() if body is not None else "---"
                setext = (
                    f" The '{written}' line under it at line {underline} is what makes this paragraph a "
                    f"heading (a setext heading, CommonMark): "
                    + (
                        "put a blank line before it to keep the paragraph, and the line a thematic break."
                        if written.startswith("-") else "remove it, or put a blank line before it, to keep the paragraph."
                    )
                )
            read = (
                "it is not read as one (§11.4), so nothing checks it and it is shown as written"
                if len(found) == 1 else
                "they are not read as directives (§11.4), so nothing checks them and they are shown as written"
            )
            result.error(
                HEADING_DIRECTIVE_RULE,
                f"The heading '{section.title}' holds {_shown(found)}, but heading text must not contain a "
                f"directive (§4.2): {read}.{setext}",
                line=spot(locator.heading(index)),
            )
        for section_index, block_index, top in own.iter_indexed_blocks():
            lines: list[int] | None = None
            for number, block in _nested_headings(top):
                found = [d.source for d in lex_fragment(block.text).directives]
                if not found:
                    continue
                if lines is None:
                    lines = locator.nested_lines(section_index, block_index) or []
                result.error(
                    HEADING_DIRECTIVE_RULE,
                    f"The heading '{block.text}' holds {_shown(found)}, but heading text must not contain "
                    f"a directive (§4.2).",
                    line=spot(lines[number] if number < len(lines) else None),
                )
