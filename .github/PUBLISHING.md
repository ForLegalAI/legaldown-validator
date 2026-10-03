# Publishing (maintainers)

Release mechanics for this repository. Nothing here is needed to *use* the
validator — see the [README](../README.md) for that.

`legaldown-validator` is published to PyPI by the [`publish`](workflows/publish.yml)
workflow using **Trusted Publishing** (OIDC). No API token is stored in this repository — PyPI
verifies the identity of the workflow run itself.

## One-time PyPI setup

This is in place: the project has been on PyPI since 0.1.0. It is recorded here in case the
publisher must be set up again, which takes an owner of the PyPI project.

1. Sign in to <https://pypi.org> → **Your projects** → `legaldown-validator` → **Manage** →
   **Publishing** → *Add a new publisher* (for a project not yet on PyPI: **Your account** →
   **Publishing** → *Add a new pending publisher*).
2. Fill in exactly:

   | Field | Value |
   |---|---|
   | PyPI project name | `legaldown-validator` |
   | Owner | `ForLegalAI` |
   | Repository name | `legaldown-validator` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. Repeat on <https://test.pypi.org> with environment name `testpypi` if you want the dry-run path.

The environment names must match, or PyPI rejects the upload.

## One-time GitHub setup

Create the two environments under **Settings → Environments**:

| Environment | Purpose | Suggested protection |
|---|---|---|
| `pypi` | Real releases | Required reviewers, and restrict to the `main` branch / tags |
| `testpypi` | Dry runs | None needed |

Adding a required reviewer to `pypi` means a human approves each upload — worth it, since a PyPI
version number can never be reused once published.

## Cutting a release

1. Bump `__version__` in [`src/legaldown/__init__.py`](../src/legaldown/__init__.py) — it is the single
   source of truth; `pyproject.toml` reads it via `[tool.hatch.version]`.
2. Commit and run **CI** on it (*Actions → CI → Run workflow*; it runs only on demand, or with the `ci` label on a pull request), then publish a
   GitHub Release with the tag `vX.Y.Z`. Its notes are where this
   project records what changed in a release.

The workflow builds the sdist and wheel, runs `twine check --strict`, **verifies the tag matches
`__version__`** (a mismatch fails the run rather than publishing the wrong version), smoke-tests the
built wheel by installing it and invoking the CLI, and uploads to PyPI.

## Dry run

To rehearse a release, or after changing the workflow, exercise the whole path against TestPyPI:

```
Actions → Publish → Run workflow → target: testpypi
```

Then verify the result installs cleanly:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ legaldown-validator
legaldown --version
```

The extra index is needed because PyYAML is not mirrored on TestPyPI.

## Notes

- **Version numbers are permanent.** PyPI does not allow re-uploading a version, even after
  deletion. Use TestPyPI, or a `.devN`/`.rcN` suffix, to rehearse.
- The distribution is named `legaldown-validator`; the import name is `legaldown`
  (`pip install legaldown-validator` → `import legaldown`).
- The package ships a `py.typed` marker, so type checkers use its annotations directly.
