"""The LegalDown specification version this implementation targets."""
from __future__ import annotations

import re

SPEC_VERSION = "0.2"

_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def parse_version(text: str) -> tuple[int, ...] | None:
    """A ``legaldown`` version (§3.2) as comparable numbers, trailing zeros
    dropped so that ``0.2`` and ``0.2.0`` compare equal; None when it is
    not a dotted number."""
    if not _VERSION_RE.fullmatch(text):
        return None
    numbers = [int(part) for part in text.split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)
