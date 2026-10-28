"""Lines after items nested on a line (``* - a``): what each is in, as CommonMark has it
(#94 review).

Each document was found by differential fuzzing against the tree before #94, where
it is read as cmark (``pandoc -f commonmark``) does, and checked with
markdown-it where that agrees: a line is in the innermost of the nested items whose
content it reaches (a marker there starts an item, a lone tag or an item that cannot
interrupt is the paragraph's text), short of them a marker starts an item of the item
they are nested in, which ends them, and an empty item or a bare one that ends the line
is read as the item it is.
"""
from __future__ import annotations

import pytest

from legaldown import parse
from legaldown.parser import quote_blocks

P, C = ("p",), ("c",)


def _shape(blocks: list) -> list:
    out: list = []
    for block in blocks:
        if block.kind in ("unordered_list", "ordered_list"):
            out.append((block.kind[:2], [_shape(item.blocks) for item in block.items]))
        elif block.kind == "quote":
            out.append(("q", _shape(quote_blocks(block))))
        else:
            out.append({"paragraph": P, "code": C}.get(block.kind, (block.kind,)))
    return out


DOCUMENTS = [
    ('*   2. - b\n        1. ---\nc\n', [("un", [[("or", [[("un", [[P]]), ("or", [[("rule",)]])]])]]), P]),
    ('*   2. - b\n        1. ---\n|\n', [("un", [[("or", [[("un", [[P]]), ("or", [[("rule",)]])]])]]), P]),
    ('* - >\n  2.\n    b\n  <g>\n=\n', [("un", [[("un", [[("q", [])]]), ("or", [[]]), P]])]),
    ('* - >a\n      1.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - >a\n      2.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - a\n    2.\n  b\n=\n', [("un", [[("un", [[P]])]])]),
    ('* - a\n  2.\n    b\n  <g>\n=\n', [("un", [[("un", [[P]]), ("or", [[]]), P]])]),
    ('* - a\n  2.\n    b\n  <y>\n=\n', [("un", [[("un", [[P]]), ("or", [[]]), P]])]),
    ('* -\n    2.\n      b\n=\n', [("un", [[("un", [[("or", [[]]), P]])]])]),
    ('+ 0.\tz\n  2)\t-\t -  \t> <n>\nc\n', [("un", [[("or", [[P]]), ("or", [[("un", [[("un", [[C]])]])]])]]), P]),
    ('- * a\n    2.\n  x\n=\n', [("un", [[("un", [[P]])]])]),
    ('- - a\n      <g>\nb\n', [("un", [[("un", [[P]])]])]),
    ('- -\n  1.\n    a\n  =\nb\n', [("un", [[("un", [[]]), ("or", [[]]), ("heading",)]]), P]),
    ('- -\t>v\n      1.\n**\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('- 0. `\n      <g>\na\n', [("un", [[("or", [[P]])]])]),
    ('- 1.     -\n       a\n=\nb\n', [("un", [[("or", [[C, P]])]])]),
    ('- 1.     -\n     a\n     1.\nb\n', [("un", [[("or", [[C, P]])]])]),
    ('- 1.\n    -\n     a\n  =\nb\n', [("un", [[("or", [[]]), ("un", [[]]), ("heading",)]]), P]),
    ('- 10.  `\n      1.\n      e\na\n', [("un", [[("or", [[P]])]])]),
    ('>* a\n    * **\n>   2) 1)     e\nc\n', [("q", [("un", [[P]])])]),
    ('>* a\n    * **\n>   2) 1)     e\nq\n', [("q", [("un", [[P]])])]),
    ('* - >a\n**\n    2.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - >a\n->\n    2.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - >a\n=\n    2.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - >a\nc\n    <y>\n=\n', [("un", [[("un", [[("q", [P]), ("html",)]])]]), P]),
    ('* - >a\nq\n    2.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - >a\nx\n    2.\n=\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* - >a\n|\n    2.\nb\n', [("un", [[("un", [[("q", [P]), ("or", [[]])]])]]), P]),
    ('* 2. b\n  >a\n      1.\nc\n', [("un", [[("or", [[P]]), ("q", [P])]])]),
    ('- - -\tz\n    <y>\n\tx\na\n', [("un", [[("un", [[("un", [[P]]), ("html",)]])]]), P]),
    ('- 0. ```\n  0. e\n  <g>\na\n', [("un", [[("or", [[C], [P]]), ("html",)]]), P]),
    ('-\n  1.   e\n      <y>\ne\n', [("un", [[("or", [[P]])]])]),
    ('- 0. ```\n    2. >\n      e\na\n', [("un", [[("or", [[C], [("q", [])]]), C]]), P]),
    ('>-\t1.\n>    .\n    ~~~\n*-\n', [("q", [("un", [[("or", [[]]), P]])])]),
    ('- *\n\n    ```\n  ->\ne\n', [("un", [[("un", [[]]), C]]), P]),
    ('* - >\n      2.\n\n    b\n=\n', [("un", [[("un", [[("q", []), ("or", [[]]), P]])]])]),
    ('-\n  1. e\n  <v>\n  y\n      ```\n**\n', [("un", [[("or", [[P]]), ("html",)]]), P]),
    ('>>q\n>-\n>   x\n>     1.\t#\n**\n', [("q", [("q", [P]), ("un", [[P, ("or", [[("heading",)]])]])]), P]),
]


@pytest.mark.parametrize(("source", "shape"), DOCUMENTS, ids=[repr(source) for source, _ in DOCUMENTS])
def test_a_line_after_items_nested_on_a_line_is_in_what_it_reaches(source: str, shape: list) -> None:
    assert _shape(parse(source).preamble) == shape
