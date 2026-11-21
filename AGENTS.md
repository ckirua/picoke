# Agent guidance (picore)

Read [`.agents/README.md`](.agents/README.md) for repository orientation and [`docs/PLAN.md`](docs/PLAN.md) for current milestones. Distinguish implemented features from plans and verified results.

## Compatibility

- Target CPython 3.14 and newer; use Python 3.15 by default. Do not add support for older Python versions.
- Package metadata must retain `requires-python = ">=3.14"`; validate separately on 3.14 and 3.15. Validate newer interpreters before claiming compatibility.
- Treat free-threaded builds as a separate ABI and support goal. Do not claim support until build, import, correctness, and concurrency are tested with the GIL disabled.

## Branches and versions

- Follow the [release-picore skill](.agents/skills/release-picore/SKILL.md) for branch organization, working versions, and release changes. Use one topic branch per independent change; ordinary checkpoints do not bump package versions or authorize a PR, tag, or publication.

## Mandatory picop review

- Before implementing or reviewing Python, Cython, or CPython C extension code, read the [`use-picop` skill](.agents/skills/use-picop/SKILL.md). This applies to subagents too; include it in relevant task handoffs.
- Use public picop helpers wherever they measurably improve performance without changing behavior. Call frequency is not an eligibility rule: startup, setup, and infrequent operations count too. Verify the helper against the catalog and installed release, using Python `import`, Cython `cimport`, or a documented public C API as appropriate.
- Record **KEEP**, **SKIP**, or **UNMEASURED** for plausible candidates in the task or review summary. Name the call site, helper, and evidence or reason. Do not force imports, repeat rejected trials without new evidence, or retain unmeasured performance substitutions.

## Engineering

- Define observable semantics, ownership, error handling, and thread safety before exposing native code. Avoid unchecked access and borrowed-reference lifetime assumptions; consult picop's safety documentation when using its helpers.
- Benchmark against an appropriate Python or standard-library baseline on representative workloads. Preserve correctness first; retain performance claims only with reproducible evidence.
- When replacing or reviving a legacy feature, inspect its actual implementation, APIs, tests, dependencies and benchmarks first. Preserve useful behavior unless a deliberate, documented contract decision changes it. Measure equivalent workloads against the legacy implementation when it can be built; report blockers and avoid claiming parity or speed without evidence.
- Keep private source names, repository URLs, and revisions out of shared docs, benchmark artifacts, and pull requests. Preserve required license and attribution notices.
- For multi-part features, maintain a concrete plan with owned work, dependencies, test vectors, performance baselines and explicit completion criteria. Partial implementation is not completion. Parallelize independent slices with clear file ownership and a single integration owner; resolve cross-slice contracts before integration.
- Keep public API, typing, examples, tests, documentation, and release notes aligned as they are introduced. Rebuild and test native artifacts per interpreter ABI; do not reuse binaries across versions or GIL modes.
- Run the narrowest relevant checks for each change. Record unverified platforms or interpreter modes rather than implying they passed.
