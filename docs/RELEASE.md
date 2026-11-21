# Working versions and releases

## Branches and versions

- `main` is the only integration and release branch. Start a short-lived branch from current `origin/main` for each independent change: `feat/<topic>`, `fix/<topic>`, `perf/<topic>`, `docs/<topic>`, or `chore/<topic>`. Use a PR targeting `main`; do not mix independent work or use a long-lived `develop` branch.
- A branch pushed as a checkpoint is not a PR or release. Preserve requests to hold a checkpoint without a PR. Before opening a PR, bring the topic up to date with `main` and check its diff is still scoped; do not rewrite a shared branch without agreement.
- After merge, fetch with pruning, delete the finished topic, and update local `main` with a fast-forward. For a squash merge, confirm the merged PR and verify the branch tip matches its recorded head commit before deleting a branch; ancestry alone cannot prove this. Do not delete local worktree branches or tips with extra commits.
- [`pyproject.toml`](../pyproject.toml) is the sole package version source. Working builds may use PEP 440 `X.Y.Z.devN` (for example, `0.1.0.dev0` before the first release). Advance `N` only for a new named development build; never bump for ordinary topic commits. Benchmarks and checkpoints should identify the commit SHA, since several commits can share a working version. Record user-visible changes under **Unreleased** in [`CHANGELOG.md`](../CHANGELOG.md).
- After a stable release, ordinary topics can retain the last release's metadata until a new development cycle needs versioned artifacts. Promote to an explicitly selected `X.Y.Z` or `X.Y.ZrcN` in one release-preparation PR. Do not infer the promotion version by incrementing a `.devN` base.

## Before a release

1. Get explicit user approval to ship, including the exact version, full title, and publication target. Preparing a branch, CI build, or changelog is not approval to tag or publish.
2. From current `main`, prepare `chore/release-<version>`: align package metadata, move the relevant changelog entries to the version heading, and prepare release notes including compatibility and supported platforms. Merge only after review and validation.
3. Verify the exact release commit on clean, synced `main`. Require the [installed-wheel checks](../.github/workflows/check.yml) and build and validate the intended distribution artifacts from that commit. The Linux CPython 3.14 and 3.15 GIL checks do not certify other platforms, bundled libcrypto, or free-threaded builds. Rebuild and test each additional ABI or platform before claiming it. Confirm licensing, distribution metadata and vulnerability reporting before the first release.
4. Once the target is approved and ready, use an annotated `picore-vX.Y.Z` tag on that exact commit and a matching GitHub Release. For a release candidate use `picore-vX.Y.ZrcN`; mark the GitHub release as a prerelease and never make it latest. Do not reuse or force-push tags.
5. Confirm the approved artifacts exist on the selected release target. Install the exact version in a clean supported interpreter and smoke-test one public API; this does not replace the pre-release matrix.

## PyPI Trusted Publishing

Create a [pending publisher in PyPI](https://pypi.org/manage/account/publishing/)
for project `picore`, owner `ckirua`, repository `picore`, workflow
`publish.yml`, and environment `pypi`. This does not reserve the name; the first
successful upload creates the project. Use no API token.

[`publish.yml`](../.github/workflows/publish.yml) runs from `main` by manual
dispatch. Pass an existing approved GitHub release tag (including a
prerelease tag when approved for PyPI). A GitHub-only release or tag push does
not upload anything. First validate without uploading:

```bash
gh workflow run publish.yml --ref main -f release-tag=picore-v0.1.0rc1 -f publish=false
```

Only after the user approves that exact release for production PyPI, dispatch
again with `publish=true`. The run builds one sdist from the annotated release
tag and tests that exact archive on Linux CPython 3.14 and 3.15 before an
OIDC-only job uploads it. Verify the uploaded digest and install the exact
version from PyPI in a clean environment. PyPI files cannot be overwritten.

Only an sdist is published initially; installs require the source-build
dependencies documented in the README. Portable wheels and free-threaded
support are not implied. TestPyPI already has an unrelated `picore` project;
use the validation-only mode rather than targeting that namespace.
