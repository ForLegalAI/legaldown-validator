"""A document's blocks as plain data, for the tests that compare a shape: shared by the test files (pytest puts
``tests/`` on the import path)."""
from __future__ import annotations

from legaldown.models import Block, list_items
from legaldown.parser import quote_content

UL, OL = "unordered_list", "ordered_list"


def tree(block: Block):
    """A block as plain data, a quote's content included: a paragraph as its
    text, a heading as ``("heading", level, text)``, a list as its kind and its
    items' blocks, any other block as ``(kind,)``."""
    if block.kind.endswith("list"):
        return (block.kind, [[tree(child) for child in item.blocks] for item in list_items(block)])
    if block.kind == "quote":
        return ("quote", [tree(child) for child in quote_content(block.text)[0]])
    if block.kind == "heading":
        return ("heading", block.level, block.text)
    return block.text if block.kind == "paragraph" else (block.kind,)
