"""The LegalDown specification version this implementation targets."""
from __future__ import annotations

SPEC_VERSION = "0.2"


def parse_version(text: str) -> tuple[int, ...] | None:
    """A ``legaldown`` version (§3.2) as comparable numbers, trailing zeros
    dropped so that ``0.2`` and ``0.2.0`` compare equal; None when it is
    not a dotted number."""
    parts = text.split(".")
    if not all(part.isdecimal() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)
