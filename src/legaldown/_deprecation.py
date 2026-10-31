"""The warning category of legaldown's deprecations."""
from __future__ import annotations


class LegaldownDeprecationWarning(DeprecationWarning):
    """The category every deprecation warning of legaldown is raised with: an API
    that is removed in a later minor release. No API is deprecated in 0.5.0, so
    nothing raises it now; it stays public so that a ``warnings`` filter naming it
    (``error::legaldown.LegaldownDeprecationWarning``) keeps working, and a
    ``DeprecationWarning`` filter matches it too."""


# Named where it is imported from, ``legaldown.LegaldownDeprecationWarning``
# (a warnings filter written as a qualified name, a pickled warning).
LegaldownDeprecationWarning.__module__ = "legaldown"
