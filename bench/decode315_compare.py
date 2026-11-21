"""Paired, fresh-process comparisons for CPython 3.15 Base64 decode dispatch."""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import statistics
import subprocess
import sys
import sysconfig
import time
import types
from pathlib import Path

VARIANTS = ("baseline", "cutoff_1024", "cutoff_2048", "cutoff_2732", "cutoff_3072", "cutoff_3584", "cached_2048", "picoke")
DEFAULT_SIZES = (767, 768, 769, 1022, 1023, 1024, 1025, 1533, 1534, 1535, 1536, 1537, 1538,
                 2046, 2047, 2048, 2049, 2301, 2302, 2303, 2304, 2305, 2306,
                 2685, 2686, 2687, 2688, 2689, 2690, 3071, 3072, 3073, 4095, 4096, 4097,
                 16384, 65536, 1048576)


def _outcome(fn, arg):
    try:
        value = fn(arg)
    except Exception as exc:
        return {"error_type": f"{type(exc).__module__}.{type(exc).__qualname__}", "message": str(exc)}
    return {"type": f"{type(value).__module__}.{type(value).__qualname__}",
            "value": hashlib.sha256(value).hexdigest() if type(value) is bytes else repr(value)}


def _load(request):
    sys.path.insert(0, request["candidate_root"])
    sys.path.insert(0, request["baseline_root"])
    picoke = importlib.import_module("picoke.crypto.base64")
    extension = importlib.import_module("_decode315_candidate")
    import pybase64
    pybase64_path = Path(pybase64.__file__).resolve()
    funcs = {name: getattr(extension, name) for name in VARIANTS[:-1]}
    funcs["picoke"] = getattr(picoke, request["api"])
    extension_path = Path(extension.__file__).resolve()
    picoke_path = Path(picoke.__file__).resolve()
    comparison_path = None
    if request["comparison_root"]:
        root = Path(request["comparison_root"]).resolve()
        matches = list((root / "picoke" / "crypto").glob("base64*.so"))
        if len(matches) != 1:
            raise RuntimeError(f"Expected one comparison extension under {root}, got {matches}")
        comparison_path = matches[0].resolve()
        package = types.ModuleType("_picoke_benchmark_comparison")
        package.__path__ = []
        sys.modules[package.__name__] = package
        name = package.__name__ + ".base64"
        spec = importlib.util.spec_from_file_location(name, comparison_path)
        comparison = importlib.util.module_from_spec(spec)
        sys.modules[name] = comparison
        spec.loader.exec_module(comparison)
        funcs["installed_candidate"] = getattr(comparison, request["api"])
    if not extension_path.is_relative_to(Path(request["candidate_root"]).resolve()):
        raise RuntimeError(f"candidate extension escaped candidate root: {extension_path}")
    if not picoke_path.is_relative_to(Path(request["baseline_root"]).resolve()):
        raise RuntimeError(f"installed picoke escaped baseline root: {picoke_path}")
    paths = {}
    for label, path in (("extension", extension_path), ("picoke", picoke_path), ("pybase64", pybase64_path)):
        paths[label] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if comparison_path:
        paths["installed_candidate"] = {"path": str(comparison_path),
                                        "sha256": hashlib.sha256(comparison_path.read_bytes()).hexdigest()}
    return funcs, paths

def _probes():
    cases = {}
    for rem in range(3):
        for n in (63, 64, 65, 1022, 1023, 1024, 2047, 2048, 2049):
            if n % 3 == rem:
                cases[f"valid/remainder{rem}/length{n}"] = base64.b64encode(bytes((i * 17 + 9) % 256 for i in range(n))).decode("ascii")
    for label, text in (
        ("empty", ""), ("terminal-NUL", "Zg==\0"), ("embedded-NUL", "Z\0g=="),
        ("bad-first", "!m9v"), ("bad-middle", "Zm!v"), ("bad-last", "Zm9!"),
        ("bad-after-padding", "Zg==!"), ("missing-padding", "Zg"), ("one-padding", "Zg="),
        ("excess-padding", "Zg==="), ("noncanonical-two", "Zh=="), ("noncanonical-one", "Zm9="),
        ("nonascii", "é"), ("surrogate", "\ud800"),
    ):
        cases[f"syntax/{label}"] = text
    cases["binary/zero-tail"] = base64.b64encode(b"a" * 2047 + b"\0\0").decode("ascii")
    for offset in (0, 1024, 2047):
        cases[f"large-invalid/{offset}"] = "A" * offset + "!" + "A" * (2047 - offset)
    cases["large-nonascii"] = "A" * 2048 + "é"
    class Overridden(str):
        def encode(self, *args, **kwargs):
            raise RuntimeError("overridden encode reached")
    class Proxy:
        @property
        def __class__(self): return str
        def encode(self, *args, **kwargs): return b"Zm9v"
    cases["subclass/encode-override"] = Overridden("Zg==")
    cases["subclass/ordinary"] = type("S", (str,), {})("Zm9v")
    cases["proxy/virtual"] = Proxy()
    for value in (None, 3, b"Zg==", bytearray(b"Zg=="), memoryview(b"Zg==")):
        cases[f"type/{type(value).__name__}"] = value
    return cases


def _preflight(functions):
    failures = []
    observations = {}
    cases = _probes()
    strict = lambda x: binascii.a2b_base64(x, strict_mode=True)
    for label, arg in cases.items():
        expected = _outcome(strict, arg) if type(arg) is str else _outcome(functions["picoke"], arg)
        actuals = {name: _outcome(fn, arg) for name, fn in functions.items()}
        observations[label] = {"strict_binascii": _outcome(strict, arg), "contract_reference": expected, **actuals}
        for name, actual in actuals.items():
            if actual != expected:
                failures.append({"probe": label, "backend": name, "expected": expected, "actual": actual})
    return observations, failures


def _environment(paths):
    versions = {}
    for package in ("picoke", "pybase64", "Cython"):
        try: versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: versions[package] = None
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines() if line.startswith("model name")), cpu)
    return {"executable": sys.executable, "python": sys.version, "implementation": platform.python_implementation(),
            "cache_tag": sys.implementation.cache_tag, "platform": platform.platform(), "machine": platform.machine(),
            "cpu": cpu, "cpu_count": os.cpu_count(),
            "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "config": {k: sysconfig.get_config_var(k) for k in ("CC", "CFLAGS", "Py_GIL_DISABLED")},
            "versions": versions, "paths_and_hashes": paths}


def _worker(request):
    funcs, paths = _load(request)
    probes, failures = _preflight(funcs)
    result = {"probes": probes, "failures": failures, "environment": _environment(paths), "timings": {}}
    if failures:
        return result
    for size in request["sizes"]:
        text = base64.b64encode(bytes((i * 29 + 11) % 256 for i in range(size))).decode("ascii")
        expected = binascii.a2b_base64(text, strict_mode=True)
        for name, fn in funcs.items():
            value = fn(text)
            if type(value) is not bytes or value != expected:
                result["failures"].append({"timed_input": size, "backend": name, "reason": "result mismatch"})
        if result["failures"]:
            return result
        # Calibrate a common iteration count across every backend in this size.
        count = 1
        while True:
            elapsed = []
            for fn in funcs.values():
                start = time.perf_counter_ns()
                for _ in range(count): fn(text)
                elapsed.append(time.perf_counter_ns() - start)
            if max(elapsed) >= request["target_ns"] or count >= request["max_iterations"]:
                break
            count = min(request["max_iterations"], count * 2)
        for fn in funcs.values():
            for _ in range(count): fn(text)
        values = {name: [] for name in funcs}
        names = list(funcs)
        # Short paired blocks within the same worker minimize CPU-frequency drift.
        for block in range(request["blocks"]):
            shift = block % (len(names) - 1) + 1
            order = names[shift:] + names[:shift]
            if block % 2: order.reverse()
            for name in order:
                fn = funcs[name]
                start = time.perf_counter_ns()
                value = None
                for _ in range(count): value = fn(text)
                elapsed = time.perf_counter_ns() - start
                if type(value) is not bytes or len(value) != size:
                    result["failures"].append({"timed_input": size, "backend": name, "reason": "timed result changed"})
                values[name].append(elapsed / count)
        result["timings"][str(size)] = {"iterations": count, "blocks_ns_call": values,
            "mean_ns_call": {name: statistics.mean(v) for name, v in values.items()},
            "paired_ratios_vs_picoke": {name: [a / b for a, b in zip(values[name], values["picoke"])]
                                         for name in names if name != "picoke"}}
    return result


def _run(args, roots, sample):
    req = {"candidate_root": roots["candidate"], "baseline_root": roots["baseline"], "sizes": args.sizes,
           "comparison_root": roots["comparison"], "api": args.api,
           "target_ns": int(args.target_ms * 1_000_000), "max_iterations": args.max_iterations,
           "blocks": args.blocks}
    proc = subprocess.run([args.python, "-I", str(Path(__file__).resolve()), "--_worker"],
                         input=json.dumps(req), text=True, capture_output=True, check=False)
    if proc.returncode:
        raise RuntimeError(f"sample {sample} worker failed ({proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
    try: return json.loads(proc.stdout)
    except ValueError as exc: raise RuntimeError(f"worker returned non-JSON: {proc.stdout!r}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--candidate-root", required=True, help="directory containing _decode315_candidate extension")
    parser.add_argument("--baseline-root", required=True, help="installed retained picoke package root")
    parser.add_argument("--comparison-root", help="installed candidate package root to load alongside baseline")
    parser.add_argument("--api", choices=("b64decode", "b64decode_ascii"), default="b64decode")
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES, metavar="DECODED_BYTES")
    parser.add_argument("--target-ms", type=float, default=10)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--blocks", type=int, default=9, help="paired in-process blocks per size")
    parser.add_argument("--max-iterations", type=int, default=1_000_000)
    args = parser.parse_args()
    if args.samples < 9 or args.blocks < 3 or args.max_iterations < 1 or not 0 < args.target_ms < 60000:
        parser.error("require samples >= 9, blocks >= 3, max-iterations >= 1 and 0 < target-ms < 60000")
    if any(n <= 0 for n in args.sizes): parser.error("sizes must be positive")
    roots = {"candidate": str(Path(args.candidate_root).resolve()), "baseline": str(Path(args.baseline_root).resolve()),
             "comparison": str(Path(args.comparison_root).resolve()) if args.comparison_root else None}
    report = {"command": sys.argv, "configuration": vars(args) | {"json_output": str(args.json_output) if args.json_output else None},
              "samples": [], "summary": {}, "blocked": False}
    reference = None
    for index in range(args.samples):
        result = _run(args, roots, index + 1)
        if result["failures"]:
            report["blocked"] = True
            report["samples"].append(result)
            print("PREFLIGHT FAILURE; timings refused: " + json.dumps(result["failures"], ensure_ascii=True), flush=True)
            break
        if reference is None: reference = {"probes": result["probes"], "environment": result["environment"]}
        elif result["probes"] != reference["probes"] or result["environment"] != reference["environment"]:
            raise RuntimeError("worker preflight or environment varied between fresh processes")
        report["samples"].append(result)
        print(f"fresh process {index + 1}/{args.samples} complete", flush=True)
    if not report["blocked"]:
        variants = tuple(report["samples"][0]["timings"][str(args.sizes[0])]["mean_ns_call"])
        for size in args.sizes:
            key = str(size)
            summary = {}
            print(f"\n{size} decoded bytes")
            for name in variants:
                vals = [s["timings"][key]["mean_ns_call"][name] for s in report["samples"]]
                summary[name] = {"mean_ns": statistics.mean(vals), "stdev_ns": statistics.stdev(vals),
                                 "min_ns": min(vals), "max_ns": max(vals), "samples_ns": vals}
                print(f" {name:13} {summary[name]['mean_ns']:.1f} ± {summary[name]['stdev_ns']:.1f} ns")
            ratios = {}
            for name in variants:
                if name == "picoke":
                    continue
                paired = [s["timings"][key]["mean_ns_call"][name] / s["timings"][key]["mean_ns_call"]["picoke"]
                          for s in report["samples"]]
                ratios[name] = {"paired_ratios": paired, "paired_mean": statistics.mean(paired),
                                "paired_stdev": statistics.stdev(paired), "mean_ratio": summary[name]["mean_ns"] / summary["picoke"]["mean_ns"]}
                print(f" {name}/picoke: paired mean {ratios[name]['paired_mean']:.4f} ± {ratios[name]['paired_stdev']:.4f}; "
                      f"ratio of means {ratios[name]['mean_ratio']:.4f}")
            report["summary"][key] = {"backends": summary, "ratios": ratios}
    else:
        print("SKIP: strict binascii parity failure; no timings collected")
    if args.json_output: args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    return int(report["blocked"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--_worker"]:
        print(json.dumps(_worker(json.load(sys.stdin)), sort_keys=True, ensure_ascii=True))
    else:
        raise SystemExit(main())
