---
name: use-picop
description: >-
  Use public picop helpers wherever they measurably improve Python, Cython,
  or CPython C extension performance without changing behavior. Applies
  to startup, setup, infrequent operations, and repeated workloads.
---

# Use picop (consumer micro-opts)

`picop` provides CPython C-API helpers for Python and Cython on Python ≥ 3.14; it is not a stdlib reimplementation.

**3.0:** use `from picop…` / `from picop… cimport`. The `cypy` Python package and Cython cimport shims have been removed; rebuild downstream Cython extensions.

This is a picore copy of the picop consumer skill. Its catalog is a snapshot;
verify APIs against the picop version actually in use. Upstream-relative links
in this skill target the picop repository at
[`https://github.com/ckirua/picop`](https://github.com/ckirua/picop), not the
picore tree. Upstream `main` may differ from the installed release; confirm
against the matching release or installed package.
For native C extensions, verify a public C interface exists: a Cython `.pxd`
and `cimport` do not by themselves provide a C header API.

## When to apply

Check any call site with a plausible public picop equivalent, regardless of
call frequency. Include startup, setup, and infrequent operations. Adopt the
helper only if the caller's contract and the measurements support it.

## Install (consumer project)

```bash
# Example installation only; choose the project dependency from pyproject.toml.
pip install "picop==3.13.0"
# or from git: pip install "picop @ git+https://github.com/ckirua/picop.git@picop-v3.13.0"
```

Requires **Python ≥ 3.14**.

`picop==3.13.0` is an installation example, not a dependency recommendation.
For a project, follow its `pyproject.toml` dependency policy.

## Sources of truth (read as needed)

| Need | Where |
|------|--------|
| Complete consumer replacement catalog | [`catalog.md`](catalog.md) — source-derived module maps, access modes, contracts, and evidence |
| Product tiers (Core / Protocols / Runtime) | [picop coverage](https://github.com/ckirua/picop/blob/main/COVERAGE.md) |
| Trusted-caller footguns | [picop safety](https://github.com/ckirua/picop/blob/main/docs/SAFETY.md) |
| Compat / post-1.0 policy | [picop release policy](https://github.com/ckirua/picop/blob/main/docs/RELEASE.md) |
| Check / len naming (N3+N4) | [picop naming](https://github.com/ckirua/picop/blob/main/docs/NAMING.md) |
| Removed soft-alias ledger | `picop.compat.COMPAT_MAP` |
| Runnable demos | [picop examples](https://github.com/ckirua/picop/blob/main/examples/README.md) |
| Exact signatures | installed `picop` / `*.pyi` under `src/picop/` |
| Do **not** use for consumers | `docs/PIPELINE.md`, tracker Lifecycle, monkey-patch archive |

Use the matching picop release or installed package as the source of truth;
upstream links target `main` and may differ from the installed version.


## Default import (Python)

```python
from picop.hot import dict_get, dict_len, list_len, list_get, list_append, bytes_len, bytes_contains, bytes_eq, str_len
```

`picop.hot` re-exports only the micro-opt starters (see `picop.hot.__all__`).

### Import styles

1. **`from picop.hot import …`** — default for micro-opts (DX-01)
2. **`from picop.cydict import dict_get`** / **`from picop import dict_get`** — full public surface
3. **`from picop.cydict cimport dict_get`** — Cython hot paths
4. **`from picop… cimport`** — package barrel or direct module paths; no `cypy` cimport shim is available.

Discourage `from picop import *`. Prefer a **PyPI pin** (`pip install "picop==…"`) or a **release tag** over unpinned `git+…`.

Letter/bare soft aliases (`dget`, `llen`, `contains`, `fg8`, …) were **removed in 0.3** — use word-prefix / `str_*` / `ansi_*`. See `picop.compat.COMPAT_MAP` / package `__getattr__` hints. **Core** (`picop.hot` / `picop.__all__`) is frozen at **1.0**; Protocols / Runtime may still evolve under minors.

## Complete consumer map

Use the [complete catalog](catalog.md), not a starter-only cheatsheet, to find
an exact symbol row. Confirm its `Access`, `Decision`, contract restrictions,
and Python/Cython evidence before considering a call-site change. Catalog
coverage does not imply a speedup; `candidate` is not a measured KEEP.

Read [`docs/PERF_PARITY.md`](https://github.com/ckirua/picop/blob/main/docs/PERF_PARITY.md) for measured
Python losses and input-dependent cases. Use
[`docs/OPS_INVENTORY.md`](https://github.com/ckirua/picop/blob/main/docs/OPS_INVENTORY.md) for broader API and
benchmark-label discovery; verify signatures in the installed release.
Repository timings rank candidates, not consumer-specific wins.


### Cython call sites

```cython
from picop.cydict cimport dict_get, dict_len
from picop.cybytes cimport bytes_len, bytes_contains, bytes_eq
```

Or `cimport picop` / package `__init__.pxd` for wider cimport-only plumbing (`cyerr`, `cymem`, `cythread`, `cyatomic`, `cylock`, `cymonitoring`, …) — **not** as pure-Python replacements.

## Footguns

- **Return codes are not booleans.** Mutators like `list_append` / `dict_set` / `set_add` return **`0` on success** and **raise** on error (`except -1`). Never write `if list_append(xs, x):` — success looks false.
- **C-string APIs want `bytes`.** Prefer `*_cstr` names (`map_getitem_cstr`, …). Pass **`bytes`** (ASCII/UTF-8), not Python `str`.
- **Naming (N1/N5):** prefer `dict_*` / `list_*` / `str_*` / `ansi_*` (and siblings). Removed soft aliases such as `dget`, `llen`, bare `contains`, and `fg8` are not supported.
- **Checks (N3):** prefer `{type}_check` / `{type}_check_exact`. `str_is` ≡ `str_check_exact` (exact `str`); prefer `str_check_exact` in check-pair tables.
- **Len vs size (N4):** `*_len` = typed/unchecked; `*_size` = checked/`object`. **Never** treat as identity (`dict_len` ≠ `dict_size`).
- **Spelling (N6):** prefer `dt_timedelta_*`, `method_get_function` / `method_get_self` (checked), and `time_wall`. Unchecked method macros are `method_*_unchecked` (cimport) — not the same as checked `method_get_*`.
- **Overlap:** known type → containers/`picop.hot`; unknown → `picop.protocols`. See `examples/py_overlap_*.py`.
- **Ownership:** pick the right get (see also [`examples/cython/borrow_vs_owned.md`](https://github.com/ckirua/picop/blob/main/examples/cython/borrow_vs_owned.md)):

  | Need | Prefer |
  |------|--------|
  | Dict get; miss/`None` both OK as `None`, lookup errors may be suppressed | `dict_get` |
  | Dict get; propagate lookup errors, miss/`None` both OK as `None` | `dict_get_ref` |
  | Dict get; distinguish a miss from stored `None` | Preserve an explicit membership check or use a distinct sentinel |
  | List get; index known in range | `list_get` (unchecked) |
  | List get; may be OOB | `list_get_checked` / `list_get_ref` |
  | Bytes as C `char*` (Cython) | `picop.cybytes.bas_string` in 3.13.0 (cimport; borrowed — do not outlive `bytes`) |

- **Raw C-API buffers:** `float_unpack2/4/8` reject short byte strings before reading; keep the trusted-caller contracts in [`docs/SAFETY.md`](https://github.com/ckirua/picop/blob/main/docs/SAFETY.md) in view for unchecked APIs and borrowed pointers.
- **Newer Cython helpers:** fixed-width integer conversions and the cimport-only `PyLongWriter` wrappers are discoverable through [`cylong` tracker](https://github.com/ckirua/picop/blob/main/docs/modules/021_cylong.md) and [`examples/cimport_ext/demo.pyx`](https://github.com/ckirua/picop/blob/main/examples/cimport_ext/demo.pyx). Float/type/cell examples are indexed in [`examples/README.md`](https://github.com/ckirua/picop/blob/main/examples/README.md).

## Audit mode (agents)

Before rewriting a call site:

1. Identify what to measure for the actual caller, including input distribution and types.
2. Locate the exact symbol/member row in the [catalog](catalog.md).
3. Honor its access mode, decision and evidence state; do not treat `specialized`,
   `companion` or `legacy` as ordinary Python replacements.
4. Prove equivalence for the caller's admitted inputs, outputs, exceptions,
   mutation, ownership, bounds, lifetime and concurrency behavior.
5. Benchmark original and replacement at the actual call site with repeated
   samples, matching runtime and import/cimport mode, then run consumer tests.
6. Report **KEEP**, **SKIP**, or **UNMEASURED** with the evidence. Do not apply
   an unmeasured rewrite.


## Usually skip (str coerce)

These are **not** in `picop.hot`. Prefer only when a boundary needs coerce — not for typed `str` hot loops:

| Helper | Use when |
|--------|----------|
| `str_or_empty` | Non-str / unwanted → `""`; keep real `str` |
| `str_as_or_empty` | Same family — see `.pyi` one-liner for exact rule |
| `str_none_to_empty` | Only `None` → `""`; other values pass through per API |
| `str_is` / `str_is_not` | Type gates (`str_is` ≡ `str_check_exact`); not conversion |

Hot path on known `str`: use `str_len` / `str_eq` / `str_contains` instead.

## Workflow: retain only measured wins

1. **Choose a representative workload.** Measure the relevant cost (including
   startup or setup if affected). Record picop and CPython versions, GIL mode,
   OS/CPU, and whether the helper is imported in Python or cimported in Cython.
2. **Select from the map.** Check exact types, signatures, Python-vs-Cython
   availability and parity warnings. Keep generic protocol/subclass behavior
   unless the caller's contract explicitly excludes it. No invented helpers.
3. **Prove equivalence before timing.** Compare results, identity/ownership where
   relevant, mutation, return values and exceptions. Cover representative empty,
   hit/miss, size, boundary and invalid-input cases admitted by the caller.
   Preserve evaluation order. Never weaken bounds, lifetime, or concurrency
   guarantees for a faster number; reject a mismatched candidate.
4. **Benchmark both actual paths.** Use the project's benchmark harness or
   `pyperf`; warm up, take at least five independent samples per path, and
   alternate comparison order to reduce drift. Match inputs and isolate mutable
   state between samples. Include any adapter, type-check, or conversion cost.
   Exclude setup only when it is outside the workload being compared. Preserve
   output use so compiled work cannot be optimized away.
5. **Require a measured gain.** Keep repeatable improvements larger than
   run-to-run variation, without material regressions on representative cases
   or the enclosing workload. No blanket minimum percentage applies.
6. **Validate the consumer.** Run nearby behavior tests and the enclosing
   workload benchmark after substitution. Python benchmarks cannot justify a
   Cython substitution or vice versa; a GIL build cannot prove a no-GIL win.
   Limit recommendations to measured runtimes and input shapes.
7. **Report decisions.** For each candidate record location, old expression,
   helper/import, semantic checks, benchmark command/environment, input shapes,
   sample count, means and variation, ratio, and **KEEP / SKIP** with reason.
   If execution is unavailable, mark **UNMEASURED** and do not apply the rewrite.
   No blanket search-and-replace or automatic fallback wrappers.

### Agent invocation

> Use public picop helpers wherever they measurably improve performance
> without changing behavior. Include startup and infrequent operations,
> benchmark Python and Cython separately, and report KEEP/SKIP evidence.
> Leave unmeasured substitutions out.

## Do not

- Invent helpers that don’t exist — check `__all__` / `.pyi` / examples first
- Call **cimport-only** APIs from pure Python (err/mem/thread/atomic/getargs/…)
- Restore builtin **monkey-patches** (`docs/future/MONKEY.md` is archive-only)
- Wrap everything “for completeness” or force losses from cold/dynamic code
- Touch internal PIPELINE/QUEUE unless the user is developing picop itself
- Introduce `cypy` imports: the legacy compatibility package and shims were removed

## More detail

- Complete consumer map and navigation: [catalog.md](catalog.md)
- Compact navigation: [cheatsheet.md](cheatsheet.md)
- Examples index: [`examples/README.md`](https://github.com/ckirua/picop/blob/main/examples/README.md)
