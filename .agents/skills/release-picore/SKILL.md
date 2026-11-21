---
name: release-picore
description: >-
  Organize picore topic branches, choose working versions, and prepare or ship
  a picore release. Use for git/release workflow, version bumps, tags, GitHub
  Releases, or PyPI publishing. Preparing work is not approval to publish.
---

# Release picore

Follow the [working-version and release policy](../../../docs/RELEASE.md).
This skill is for `ckirua/picore`, not the `picop` dependency. Use picop's
consumer skill separately for code and performance work.

- **Ordinary work:** one short-lived topic branch from current `origin/main`,
  one scoped PR to `main`, and delete the merged branch once its tip is
  verified. A no-PR checkpoint stays a checkpoint. Do not bump the version
  for topic work.
- **Working build:** use the `[project].version` in `pyproject.toml`;
  use PEP 440 `X.Y.Z.devN` for deliberately versioned development builds.
  Keep unreleased changes in `CHANGELOG.md` and identify checkpoints by SHA.
- **Release:** first confirm the exact version, title, and target. Prepare the
  version and notes on a topic branch, validate the exact merged `main`
  commit and artifacts, and tag only with explicit approval. Never use
  picop's release script. For approved PyPI releases, use `publish.yml` from
  `main`: validate the release tag first, then explicitly dispatch the upload.
