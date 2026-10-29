"""The reading of a link reference definition that the insertion boundary (§15.7.3, #119) is pinned to, by regular
expressions, apart from ``_InsertionScan``'s index: shared by the oracles of ``test_destination_oracles`` (the end of a
definition at every offset) and ``test_linear_oracles`` (the verdict for every insertion)."""
from __future__ import annotations

import re

INSERTIONS = ("placeholder", "choose")

# `` {0,3}[label]:`` and the blanks after it.
LABEL_RE = re.compile(r" {0,3}\[[^\]]+\]:[ \t]*")
BARE_DESTINATION_RE = re.compile(r"\S*")
# A ``<…>`` destination, which may hold blanks (§6.3), with an escape for a punctuation character.
ANGLE_DESTINATION_RE = re.compile(r"<(?:\\[!-/:-@\[-`{-~]|[^<>\n])*+>")
# A closed title, which the blanks before it set off.
TITLE_RE = re.compile(r"[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\))")
# Where an answer could be the title: the blanks, an insertion, and the blanks and insertions after it. (In the text of
# ``insertion_template``, where an insertion is a NUL and another directive is not.)
TITLE_SLOT_RE = re.compile(r"[ \t]+\0[ \t\0]*")


def insertion_template(text, directives) -> str:
    """*text* with each insertion (``{{placeholder:}}``, ``{{choose:}}``) a run of NULs and each other directive a run
    of SOHs: the template, where only an insertion is a NUL (a NUL in the text is not: it is a STX)."""
    chars = list(text.replace("\0", "\x02"))
    for directive in directives:
        mask = "\0" if directive.name in INSERTIONS and not directive.malformed else "\x01"
        chars[directive.start:directive.end] = mask * (directive.end - directive.start)
    return "".join(chars)


def definition_ends(template, slots, label_end, definitions=True):
    """The readings of the definition whose label ends at *label_end* (``LABEL_RE``'s end), each as ``(angle, end,
    slot)``: its destination is a ``<…>``, where it ends (past a closed title or the title slot), and whether it
    ends in the title slot. *slots* is ``insertion_template(…)``. The bare reading only, and no title slot,
    where *definitions* is False (the text cannot hold a definition: a heading, a table cell)."""
    bare = BARE_DESTINATION_RE.match(template, label_end)
    readings = [(False, bare.end())]
    if definitions and (angle := ANGLE_DESTINATION_RE.match(template, label_end)):
        readings.append((True, angle.end()))
    ends = []
    for is_angle, after in readings:
        if title := TITLE_RE.match(template, after):
            ends.append((is_angle, title.end(), False))
        elif definitions and (slot := TITLE_SLOT_RE.match(slots, after)):
            ends.append((is_angle, slot.end(), True))
        else:
            ends.append((is_angle, after, False))
    return ends
