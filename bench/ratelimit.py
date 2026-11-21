"""Paired benchmark for the public token bucket and optional comparison arms.

Run with the target interpreter and installed picore/picop:
    python bench/ratelimit.py [--reference PATH] [--legacy-module DOTTED_MODULE]
    python bench/ratelimit.py --cpu-time  # exclude descheduling in synchronous batches
"""

import argparse
import asyncio
from importlib import import_module
from importlib.machinery import EXTENSION_SUFFIXES
from importlib.metadata import PackageNotFoundError, version
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import platform
import statistics
import sys
import sysconfig
import time


CONSTRUCTION_COUNT = 4096
ACQUIRE_COUNT = 4096
CONTENTION_COUNT = 32
SAMPLES = 9
WARMUPS = 2


class _PreflightError(Exception):
    pass


def _load_reference(path):
    source = Path(path).expanduser().resolve()
    try:
        name = "_picore_ratelimit_reference"
        for suffix in EXTENSION_SUFFIXES:
            if source.name.endswith(suffix):
                name += "." + source.name.removesuffix(suffix)
                break
        spec = spec_from_file_location(name, source)
        if spec is None or spec.loader is None:
            raise ImportError
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.TokenBucket
    except Exception as exc:
        raise RuntimeError(f"reference load failed ({type(exc).__name__})") from None


def _load_legacy(name):
    try:
        return import_module(name).TokenBucket
    except Exception as exc:
        raise RuntimeError(f"legacy import failed ({type(exc).__name__})") from None


def _bucket(factory, workload):
    if workload == "uncontended":
        return factory(4096, refill_amount=1, refill_period_ns=1_000_000_000)
    return factory(1, refill_amount=1, refill_period_ns=1_000_000)


def _legacy_bucket(factory, workload):
    period = 1.0 if workload != "contention" else 0.001
    capacity = 1 if workload == "contention" else 4096
    return factory(capacity, 1, period)


def _make(factory, legacy, workload):
    return _legacy_bucket(factory, workload) if legacy else _bucket(factory, workload)


def _construct(factory, legacy, timer):
    start = timer()
    if legacy:
        for _ in range(CONSTRUCTION_COUNT):
            factory(4096, 1, 1.0)
    else:
        for _ in range(CONSTRUCTION_COUNT):
            factory(4096, refill_amount=1, refill_period_ns=1_000_000_000)
    return timer() - start


async def _acquire_batch(factory, legacy, workload, timer, preflight=False):
    bucket = _make(factory, legacy, workload)
    if workload == "uncontended":
        start = timer()
        for _ in range(ACQUIRE_COUNT):
            await bucket.acquire()
        return timer() - start

    completed = []

    async def worker(index):
        await bucket.acquire()
        completed.append(index)

    start = time.perf_counter_ns()
    tasks = [asyncio.create_task(worker(index)) for index in range(CONTENTION_COUNT)]
    await asyncio.gather(*tasks)
    elapsed = time.perf_counter_ns() - start
    if preflight and completed != list(range(CONTENTION_COUNT)):
        raise _PreflightError("completion order differs from submission order")
    if len(completed) != CONTENTION_COUNT:
        raise _PreflightError("contention did not complete every acquisition")
    return elapsed


async def _run_async_samples(arms, timer):
    # The loop is shared by warmups and every timed observation.
    result = {name: {work: [] for work in ("uncontended", "contention")}
              for name, _, _ in arms}
    for name, factory, legacy in arms:
        for workload in ("uncontended", "contention"):
            try:
                await asyncio.wait_for(
                    _acquire_batch(factory, legacy, workload, timer, preflight=True),
                    timeout=30,
                )
            except Exception as exc:
                reason = str(exc) if type(exc) is _PreflightError else type(exc).__name__
                if not legacy:
                    raise RuntimeError(f"{name} preflight failed for {workload}: {reason}") from None
                result[name][workload] = None
                print(f"preflight excluded {name} from {workload}: {reason}")

    for workload in ("uncontended", "contention"):
        valid = [(name, factory, legacy) for name, factory, legacy in arms
                 if result[name][workload] is not None]
        if not valid:
            continue
        for _ in range(WARMUPS):
            for _, factory, legacy in valid:
                await _acquire_batch(factory, legacy, workload, timer)
        for sample in range(SAMPLES):
            ordered = valid if sample % 2 == 0 else list(reversed(valid))
            for name, factory, legacy in ordered:
                result[name][workload].append(
                    await _acquire_batch(factory, legacy, workload, timer))
    return result


def _report(workload, results, arms):
    print(f"\n{workload} (ns/sample)")
    for name, _, _ in arms:
        values = results.get(name)
        if values is None:
            print(f"  {name}: excluded")
        else:
            print(f"  {name}: median={statistics.median(values):.0f}; "
                  f"samples={','.join(str(value) for value in values)}")
    if len(arms) >= 2:
        baseline = arms[0][0]
        for name, _, _ in arms[1:]:
            left, right = results.get(baseline), results.get(name)
            if not isinstance(left, list) or not isinstance(right, list):
                continue
            ratios = [b / a for a, b in zip(left, right)]
            print(f"  paired {name}/{baseline}: median={statistics.median(ratios):.4f}; "
                  f"ratios={','.join(f'{ratio:.4f}' for ratio in ratios)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", help="source snapshot or ABI-matched extension")
    parser.add_argument("--legacy-module", help="optional module exposing the earlier TokenBucket")
    parser.add_argument("--cpu-time", action="store_true",
                        help="time construction and uncontended in thread CPU time")
    args = parser.parse_args()
    timer = time.thread_time_ns if args.cpu_time else time.perf_counter_ns

    try:
        from picore.ratelimit import TokenBucket
    except Exception as exc:
        parser.error(f"installed picore import failed ({type(exc).__name__})")

    arms = [("installed", TokenBucket, False)]
    if args.reference:
        arms.append(("reference", _load_reference(args.reference), False))
    if args.legacy_module:
        arms.append(("legacy", _load_legacy(args.legacy_module), True))

    try:
        picop_version = version("picop")
    except PackageNotFoundError:
        picop_version = "not-installed"
    gil_enabled = getattr(sys, "_is_gil_enabled", lambda: True)()
    print(f"Python: {platform.python_implementation()} {platform.python_version()} "
          f"({sysconfig.get_config_var('SOABI') or 'unknown ABI'})")
    print(f"Machine: {platform.platform()} / {platform.machine()} / {platform.processor() or 'unknown CPU'}")
    print(f"GIL: {'enabled' if gil_enabled else 'disabled'}; picop: {picop_version}")
    print("Arms: " + ", ".join(name for name, _, _ in arms))
    print(f"Timer: {timer.__name__}; contention always uses perf_counter_ns")

    for _ in range(WARMUPS):
        for _, factory, legacy in arms:
            _construct(factory, legacy, timer)
    construction = {}
    for sample in range(SAMPLES):
        ordered = arms if sample % 2 == 0 else list(reversed(arms))
        for name, factory, legacy in ordered:
            construction.setdefault(name, []).append(_construct(factory, legacy, timer))
    _report("construction x4096", construction, arms)

    async_results = asyncio.run(_run_async_samples(arms, timer))
    for workload in ("uncontended", "contention"):
        _report(workload, {name: values[workload] for name, values in async_results.items()}, arms)


if __name__ == "__main__":
    main()
