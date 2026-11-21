# picoke agent guide

Repository operating guide for coding agents and maintainers. Read this guide and the root [`AGENTS.md`](../AGENTS.md) before changing the project.

## Repository map

| Area | Purpose |
|---|---|
| [`README.md`](../README.md) | Product scope, API examples, and Python support |
| [`AGENTS.md`](../AGENTS.md) | Compatibility and engineering rules |
| [`pyproject.toml`](../pyproject.toml) | Setuptools metadata and required dependencies |
| [`src/picoke/`](../src/picoke/) | Typed `src`-layout package; crypto APIs are under `crypto/` |
| [`tests/`](../tests/) | Focused pytest coverage for the crypto APIs |
| [`docs/`](../docs/) | Project plan and legacy feature inventory |
| [`skills/use-picop/SKILL.md`](skills/use-picop/SKILL.md) | picop consumer skill and API catalog |
| [`skills/release-picoke/SKILL.md`](skills/release-picoke/SKILL.md) | Branches, working versions, and release gate |

## Working rules

1. Use Python 3.15 by default; support starts at CPython 3.14. Follow the compatibility rules in [`AGENTS.md`](../AGENTS.md).
2. Before replacing or reviving legacy behavior, inspect its implementation, API, tests, dependencies, and benchmarks. Define equivalent workloads and a relevant baseline before making performance claims; document changed semantics and any build/measurement blocker.
3. For multi-part work, keep [`docs/PLAN.md`](../docs/PLAN.md) current with owned slices, dependencies, test vectors, baselines, and completion criteria. Mark work complete only with evidence; distinguish implemented, planned, and verified support.
4. Update tests and user-facing documentation alongside behavior. Run focused checks for the affected behavior and the supported interpreter versions where available; report unverified environments.
5. Start each independent topic from current `origin/main`; target `main` with one scoped PR. Use the [release skill](skills/release-picoke/SKILL.md) for branch, version, and release work. Do not publish or tag as part of ordinary development.

## picop skill

The [mandatory picop review rule](../AGENTS.md#mandatory-picop-review) applies to Python, Cython, and CPython C extensions. Use picop wherever it measurably improves performance without changing behavior, including startup and infrequent code. Include the [`use-picop` skill](skills/use-picop/SKILL.md) in relevant subagent handoffs and report **KEEP / SKIP / UNMEASURED**. Use `picop`, not the removed `cypy` namespace.

The catalog is a copied upstream snapshot, not a guarantee for the version used by picoke. Links in upstream module maps that point outside the skill refer to the [picop repository](https://github.com/ckirua/picop); consult matching release or installed sources for authoritative contracts.

## Commands

Run from the repository root. Install the checkout and pytest in each supported interpreter environment, then run the focused suite:

```bash
python3.15 -m venv .venv315
.venv315/bin/python -m pip install -e . pytest
.venv315/bin/python -m pytest -q

python3.14 -m venv .venv314
.venv314/bin/python -m pip install -e . pytest
.venv314/bin/python -m pytest -q
```

For a narrower run, pass the relevant `tests/test_crypto_*.py` file(s) to pytest. Documentation-only changes should have working local links and a consistent version policy; do not imply checks passed unless run.

