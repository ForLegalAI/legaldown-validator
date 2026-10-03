"""The warning category of legaldown's deprecations."""
from __future__ import annotations


class LegaldownDeprecationWarning(DeprecationWarning):
    """A deprecated legaldown API was used: it is removed in the next minor
    release. Every deprecation warning legaldown raises is of this category,
    so one filter covers them all; a ``DeprecationWarning`` filter still
    matches them too."""


# Named where it is imported from, ``legaldown.LegaldownDeprecationWarning``
# (a warnings filter written as a qualified name, a pickled warning).
LegaldownDeprecationWarning.__module__ = "legaldown"
