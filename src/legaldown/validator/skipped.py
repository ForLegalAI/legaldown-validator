"""The Warning for a check category not performed (§17.5, ``ldv-check-skipped``).

§17.5: an implementation that meets a construct whose processing lies beyond its
claimed level must say so, with a Warning identifying each check category it did not
perform, and must not report a document as passing checks it did not run. The trigger
is a construct in the document (§17.1: no construct is ever mandatory), so a document
without one draws nothing. The categories are the bullets of §17.4 that this
implementation does not fully perform; each is one Warning for one validation, at the line of
its first construct, and lists exactly what was not performed in it: a check that was
made (a fragment that was read) is not listed.

The rule id is this implementation's own (§16.1: a prefix ending in a hyphen), and so is
the level of detail of the message, which the specification leaves to implementations.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial

from ..models import Amends, Document
from ..positions import Locator
from .fragments import IncludeOutcome, UnreadInclude
from .paths import FileReference
from .patterns import LEGALDOWN_EXTENSIONS
from .result import Line
from .templates import IncludeDirective


@dataclass(frozen=True, slots=True)
class SkippedCategory:
    """A check category not performed: its *key*, the Warning's *message*, and the *line*
    of the first construct that makes it apply."""
    key: str
    message: str
    line: Line


def _join(items: Sequence[str]) -> str:
    """``a, b and c``."""
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _items(parts: Sequence[str]) -> str:
    """The parts of a longer list, apart."""
    return "; ".join(parts)


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _unread(unread: list[UnreadInclude], fragment: bool) -> list[str]:
    """How many includes were not read, and why, for the message."""
    reasons = {
        "no-resolver": (
            "the includes inside a fragment are not followed" if fragment
            else "no loader to read it (no `resolve`, and the document has no path)"
        ),
        "leads-out": "its path leads out of the document's directory, which is not read",
        "unreadable": "it could not be read (include-file-missing)",
        "own": "it names the document itself",
    }
    found = []
    for reason, because in reasons.items():
        number = sum(1 for each in unread if each.reason == reason)
        if number:
            found.append(f"{_count(number, 'include')} not read: {because}")
    return found


def _includes(
    paragraphs: list[IncludeDirective], outcome: IncludeOutcome, template: bool, fragment: bool
) -> SkippedCategory | None:
    if not paragraphs:
        return None
    unread = outcome.unread
    rules = []
    if any(each.reason in ("no-resolver", "leads-out") for each in unread):
        rules.append("include-file-missing")
    if unread:
        rules += ["include-has-frontmatter", "include-has-h1", "include-heading-skip"]
        if template:
            rules.append("the parts of template-fragment-invalid that read a fragment")
    met = [_count(len(paragraphs), "`{{include:}}` paragraph"), *_unread(unread, fragment)]
    parts = []
    if rules:
        parts.append(f"{_join(rules)} for the fragments not read")
    if outcome.conditional:
        parts.append(
            "include-heading-skip under every combination of answers to the conditions on the includes, "
            "judged with every include present"
        )
    parts.append("include-cycle and include-anchor-duplicate, which are not implemented")
    # The combined document is never put together (§12.2, §16.11): what refers across a
    # fragment and the document is judged as if the fragment were not there.
    parts.append(
        "the combined-document checks (§16.11), such as a reference or a term into a fragment, "
        "or an identifier the fragments and the document share"
    )
    parts += [f"{cap} (not checked past that point)" for cap in outcome.caps]
    return SkippedCategory(
        "includes",
        f"{_items(met)}. Not performed: {_items(parts)} "
        f"(include processing is Full, §17.4; §17.5).",
        paragraphs[0].line,
    )


#: Why the definitions of a LegalDown attachment file were not imported, each as a message says it of
#: one file and of several: every reason but the document's own (``own``), whose definitions are the
#: document's already. This is the one place the reasons are listed (``_Imported.not_imported``).
NOT_IMPORTED = {
    "no-resolver": ("has no loader to read it", "have no loader to read them"),
    "leads-out": ("leads out of the document's directory, which is not read", "lead out of the document's directory, which is not read"),
    "unreadable": ("could not be read", "could not be read"),
    "rejected": ("has a path refused by path-not-relative or path-outside-root", "have paths refused by path-not-relative or path-outside-root"),
}


def _not_imported(reasons: Sequence[str]) -> str:
    """The attachment files with no definitions imported, with a count for each reason:
    ``3 LegalDown attachment files (1 leads out of the document's directory, which is not read;
    2 could not be read)``."""
    why = [
        f"{number} {NOT_IMPORTED[reason][number != 1]}"
        for reason in NOT_IMPORTED
        if (number := reasons.count(reason))
    ]
    return f"{_count(len(reasons), 'LegalDown attachment file')} ({'; '.join(why)})"


def _attachments(document: Document, where: Locator, unread: Sequence[str]) -> SkippedCategory | None:
    files = [(index, att.file.strip()) for index, att in enumerate(document.metadata.attachments) if att.file.strip()]
    if not files:
        return None
    legaldown = sum(1 for _index, file in files if file.endswith(LEGALDOWN_EXTENSIONS))
    rules = ["attachment-file-missing"]
    if legaldown:
        rules += ["attachment-has-frontmatter", "attachment-has-h1", "attachment-anchor-duplicate"]
    parts = [_join(rules)]
    if unread:
        parts.append(f"importing the definitions of {_not_imported(unread)}")
    return SkippedCategory(
        "attachments",
        f"{_count(len(files), 'attachment file')} declared ({legaldown} LegalDown). Not performed: "
        f"{_items(parts)} (attachment processing is Full, §17.4; §17.5).",
        partial(where.key, "attachments", files[0][0], "file"),
    )


def _amendment(document: Document, where: Locator, unread: bool) -> SkippedCategory | None:
    amends = document.metadata.amends
    if not amends or not amends.file.strip():
        return None
    parts = ["amends-file-missing"]
    if unread:
        parts.append(
            "amend-term-undefined, since the definitions of the original were not imported "
            "(a term not defined in the amendment is amend-term-unresolvable, Info)"
        )
    return SkippedCategory(
        "amendment",
        f"amends.file declared. Not performed: {_items(parts)} (amendment processing is Full, §17.4; §17.5).",
        partial(where.key, "amends", "file"),
    )


def _bilingual(document: Document, where: Locator) -> SkippedCategory | None:
    translations = document.metadata.translations
    if not translations:
        return None
    rules = [
        "translation-file-missing", "translation-hierarchy-mismatch", "translation-anchor-mismatch",
        "translation-def-mismatch", "translation-language-set-mismatch", "translation-template-mismatch",
    ]
    return SkippedCategory(
        "bilingual",
        f"translations declared ({_count(len(translations), 'linked document')}); bilingual validation not "
        f"performed: {_join(rules)} (§14, §16.7; bilingual validation is Full, §17.4; §17.5).",
        partial(where.key, "translations"),
    )


def _paths(document: Document, where: Locator, image: FileReference | None) -> SkippedCategory | None:
    superseded = isinstance(document.metadata.supersedes, Amends) and document.metadata.supersedes.file.strip()
    if not superseded and image is None:
        return None
    met, parts = [], []
    if superseded:
        met.append("supersedes.file")
        parts.append("supersedes-file-missing")
    if image is not None:
        met.append("an image path")
        parts.append("the existence of image paths (§8.7), which has no rule id")
    return SkippedCategory(
        "paths",
        f"{_join(met)} declared. Not performed: {_items(parts)} "
        f"(existence checks of declared paths are Full, §17.4; §17.5).",
        partial(where.key, "supersedes", "file") if superseded else image.line,
    )


def skipped_categories(
    document: Document,
    where: Locator,
    *,
    includes: list[IncludeDirective],
    outcome: IncludeOutcome,
    image: FileReference | None,
    template: bool,
    fragment: bool,
    attachments_unread: Sequence[str],
    amends_unread: bool,
) -> list[SkippedCategory]:
    """The check categories this validation did not perform for the constructs *document*
    holds, in the order of §17.4: at most one for each (§17.5).

    *includes* are the document's include paragraphs and *outcome* what ``check_includes``
    did with them; *image* is the first image path to hold no finding of its own
    (``FileReferences.first_image``), or None; *fragment*: the document is validated on its own, as a fragment;
    *attachments_unread*: why each of its LegalDown attachment files had no definitions
    imported (``_Imported.missing``: not for its own, nor for a path that is a finding);
    *amends_unread*: the same of the original it amends."""
    found = [
        _includes([each for each in includes if each.paragraph], outcome, template, fragment),
        _attachments(document, where, attachments_unread),
        _amendment(document, where, amends_unread),
        _bilingual(document, where),
        _paths(document, where, image),
    ]
    return [category for category in found if category is not None]
