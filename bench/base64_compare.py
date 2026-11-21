"""Compare installed compiled Base64 implementations in isolated processes.

Example, with user-supplied compiled package roots::

    python bench/base64_compare.py --python .venv/bin/python \
        --baseline-root /path/to/previous/picore \
        --legacy-root /path/to/baseline/package

The candidate is imported from --python's environment, or --candidate-root.
Both decode APIs accept str and return bytes; ``ascii`` is an API spelling,
not a bytes-input decoder. No installation into the driving interpreter is needed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.machinery
import json
import os
import platform
import statistics
import subprocess
import sys
import sysconfig
import time
from pathlib import Path


FUNCTIONS = ("b64encode", "b64encode_ascii", "b64decode", "b64decode_ascii")
SIZES = (12, 1024, 1048576)
BACKENDS = ("candidate", "previous", "legacy", "stdlib")


def _stdlib_functions():
    import base64

    def encode(data):
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        return base64.b64encode(data)

    def encode_ascii(data):
        return encode(data).decode("ascii")

    def decode(text):
        if not isinstance(text, str):
            raise TypeError("text must be str")
        return base64.b64decode(text, validate=True)

    return dict(zip(FUNCTIONS, (encode, encode_ascii, decode, decode)))


def _load(backend, root):
    if root:
        sys.path.insert(0, root)
    if backend == "stdlib":
        import base64
        import binascii
        origins = {
            module.__name__: {
                "path": (getattr(module, "__file__", None)
                         or getattr(getattr(module, "__spec__", None), "origin", None)
                         or "unknown (no module file or spec origin)"),
                "source": "Python standard library",
                "backend": "stdlib",
            }
            for module in (base64, binascii)
        }
        return _stdlib_functions(), origins
    if backend == "legacy":
        # The isolated worker does not add the script directory to sys.path.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from crypto import _legacy_package
        finally:
            sys.path.pop(0)
        name = f"{_legacy_package(root)}.crypto.encoding.base64"
    else:
        name = "picore.crypto.base64"
    module = importlib.import_module(name)
    origin = Path(module.__file__).resolve()
    if not any(str(origin).endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES):
        raise RuntimeError(f"{backend} must be compiled, loaded {origin}")
    if root and not origin.is_relative_to(Path(root).resolve()):
        raise RuntimeError(f"{backend} escaped requested package root: {origin}")
    return {name: getattr(module, name) for name in FUNCTIONS}, {
        module.__name__: {"path": str(origin), "sha256": hashlib.sha256(origin.read_bytes()).hexdigest(),
                          "source": "compiled extension", "backend": backend}
    }


def _size_label(size):
    if size % 1048576 == 0:
        return f"{size // 1048576}MiB"
    if size % 1024 == 0:
        return f"{size // 1024}KiB"
    return f"{size}-byte"


def _cases(sizes):
    import base64
    cases = {}
    for size in sizes:
        label = _size_label(size)
        # Binary, including internal NULs, but never a terminal NUL: the real
        # legacy decoder strips those and cannot be timed as an equivalent API.
        payload = (bytes(range(256)) * ((size + 255) // 256))[:size - 1] + b"!"
        text = base64.b64encode(payload).decode("ascii")
        for name in FUNCTIONS:
            cases[f"{name}/{label}"] = (name, payload if "encode" in name else text)
    return cases


def _outcome(function, argument):
    try:
        value = function(argument)
    except Exception as exc:
        return {"error": f"{type(exc).__module__}.{type(exc).__qualname__}", "message": str(exc)}
    if type(value) in (bytes, str):
        raw = value.encode("utf-8") if type(value) is str else value
        return {"type": type(value).__name__, "length": len(value), "sha256": hashlib.sha256(raw).hexdigest()}
    return {"type": type(value).__name__, "repr": repr(value)}


def _checks(functions, cases, backend):
    import base64
    reference = _stdlib_functions()
    timed = {}
    for key, (name, argument) in cases.items():
        # Exact equality and exact result type, not just cross-process digests.
        expected = reference[name](argument)
        try:
            actual = functions[name](argument)
            timed[key] = type(actual) is type(expected) and actual == expected
        except Exception:
            timed[key] = False
    probes = {}
    for label, data in (
        ("empty", b""), ("all-bytes", bytes(range(256))),
        ("embedded-NUL", b"a\x00b"), ("terminal-NUL", b"a\x00"),
        ("only-NUL", b"\x00"), ("terminal-NULs", b"abc\x00\x00\x00"),
    ):
        text = base64.b64encode(data).decode("ascii")
        for name in FUNCTIONS:
            argument = data if "encode" in name else text
            probes[f"binary/{label}/{name}"] = _outcome(functions[name], argument)
    # These are behavior probes, never benchmarked or equated with successful
    # strict decoding. Padding oddities include some stdlib-accepted strings.
    for text in ("!", "Zg", "Zg=", "Zg===", "Zg==x", "=Zg==", "Zm9v=", "Zm9v====",
                 "Zh==", "Zm9=", "Z g==", "Zg==\n", "Zg==\x00", "é", "😀", "A", "===="):
        for name in FUNCTIONS[2:]:
            probes[f"syntax/{text!r}/{name}"] = _outcome(functions[name], text)
    for value in (None, 3, b"Zg==", bytearray(b"Zg==")):
        for name in FUNCTIONS[2:]:
            probes[f"type/{value!r}/{name}"] = _outcome(functions[name], value)
    for value in (None, 3, "abc", bytearray(b"abc")):
        for name in FUNCTIONS[:2]:
            # Legacy typed Cython accepts None, then invokes unchecked bytes
            # helpers. Do not risk a native crash in this timing harness.
            probes[f"type/{value!r}/{name}"] = (
                {"not_run": "legacy None encode may reach unchecked native bytes helpers"}
                if backend == "legacy" and value is None else _outcome(functions[name], value)
            )
    return {"timed_equivalent": timed, "probes": probes}


def _environment(origins):
    import ssl
    import importlib.metadata
    versions = {}
    for package in ("picop", "Cython", "pybase64"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                    if line.startswith("model name")), cpu)
    maps = Path("/proc/self/maps")
    libraries = sorted({line.split()[-1] for line in maps.read_text().splitlines()
                        if "libcrypto" in line or "libssl" in line}) if maps.exists() else []
    simd = sys.modules.get("pybase64._pybase64")
    simd_provider = ({"origin": simd.__file__,
                      "active_path": simd._get_simd_name(simd._get_simd_path())}
                     if simd is not None else None)
    return {
        "executable": sys.executable, "python": sys.version, "implementation": platform.python_implementation(),
        "cache_tag": sys.implementation.cache_tag, "platform": platform.platform(), "machine": platform.machine(),
        "cpu": cpu, "cpu_count": os.cpu_count(), "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "gil_enabled": sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else None,
        "config": {key: sysconfig.get_config_var(key) for key in ("CC", "CFLAGS", "CONFIG_ARGS", "Py_GIL_DISABLED")},
        "ssl_module_openssl_version": ssl.OPENSSL_VERSION,
        "mapped_crypto_library_paths": libraries,
        "pybase64_simd": simd_provider,
        "evp_provider_version": "not resolved; mapped library paths reported separately",
        "package_metadata_versions": versions, "origins": origins,
        "environment": {key: value for key, value in os.environ.items()
                        if key.startswith(("PYTHON", "OMP_")) or key in ("LD_LIBRARY_PATH", "LD_PRELOAD")},
        "clock": vars(time.get_clock_info("perf_counter")),
    }


def _worker(request):
    functions, origins = _load(request["backend"], request["root"])
    cases = _cases(request["sizes"])
    sizes_by_label = {_size_label(size): size for size in request["sizes"]}
    checks = _checks(functions, cases, request["backend"])
    result = {"checks": checks, "environment": _environment(origins)}
    timings = {}
    if request["mode"] == "time":
        for key in request["eligible"]:
            if not checks["timed_equivalent"][key]:
                raise RuntimeError(f"equivalence changed before timing: {key}")
            name, argument = cases[key]
            function = functions[name]
            size = sizes_by_label[key.rsplit("/", 1)[1]]
            cap = min(request["iterations"], 64 if size >= 1048576 else 1000000)
            count = 1
            # Adaptive calibration doubles up to a fixed iteration cap. Setup,
            # imports, equality checks and process startup are outside timers.
            while True:
                start = time.perf_counter_ns()
                for _ in range(count):
                    function(argument)
                elapsed = time.perf_counter_ns() - start
                if elapsed >= request["target_ns"] or count >= cap:
                    break
                count = min(cap, count * 2)
            for _ in range(request["warmups"]):
                for _ in range(count):
                    function(argument)
            start = time.perf_counter_ns()
            for _ in range(count):
                value = function(argument)
            elapsed = time.perf_counter_ns() - start
            if type(value) is not type(_stdlib_functions()[name](argument)):
                raise RuntimeError("result type changed during timing")
            timings[key] = {"ns_per_call": elapsed / count, "iterations": count, "elapsed_ns": elapsed}
    result["timings"] = timings
    return result


def _run(args, backend, root, mode, eligible=()):
    request = {"backend": backend, "root": root, "mode": mode, "eligible": list(eligible),
               "iterations": args.iterations, "target_ns": int(args.target_ms * 1000000),
               "warmups": args.warmups, "sizes": args.sizes}
    completed = subprocess.run(
        [args.python, "-I", str(Path(__file__).resolve()), "--_worker"],
        input=json.dumps(request), capture_output=True, text=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"{backend} worker failed ({completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    try:
        return json.loads(completed.stdout)
    except ValueError as exc:
        raise RuntimeError(f"{backend} returned non-JSON output: {completed.stdout!r}") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--python", default=sys.executable, help="same interpreter for all four implementations")
    parser.add_argument("--candidate-root", help="optional directory containing compiled candidate picore/")
    parser.add_argument("--baseline-root", required=True, help="directory containing previous compiled picore/")
    parser.add_argument("--legacy-root", required=True, help="directory containing the compiled baseline package")
    parser.add_argument("--samples", type=int, default=7, help="fresh-process paired rounds, minimum 7")
    parser.add_argument("--sizes", type=int, nargs="+", default=SIZES, metavar="BYTES",
                        help="positive payload byte lengths (default: 12 1024 1048576)")
    parser.add_argument("--iterations", type=int, default=1000000, help="per-case iteration cap (payloads >=1MiB additionally capped at 64)")
    parser.add_argument("--target-ms", type=float, default=25, help="adaptive calibration target, not a guaranteed duration")
    parser.add_argument("--warmups", type=int, default=1, help="untimed batches after calibration")
    parser.add_argument("--json-output", type=Path, help="save complete environment, probes and raw samples")
    args = parser.parse_args()
    if args.samples < 7 or args.iterations < 1 or not 0 < args.target_ms < 60000 or args.warmups < 1:
        parser.error("require samples >= 7, iterations >= 1, 0 < target-ms < 60000, warmups >= 1")
    if any(size <= 0 for size in args.sizes):
        parser.error("sizes must be positive integer byte lengths")
    args.sizes = list(dict.fromkeys(args.sizes))
    roots = {"candidate": args.candidate_root, "previous": args.baseline_root, "legacy": args.legacy_root, "stdlib": None}
    roots = {key: str(Path(value).resolve()) if value else None for key, value in roots.items()}
    preflight = {backend: _run(args, backend, roots[backend], "check") for backend in BACKENDS}
    report = {"command": sys.argv, "configuration": vars(args) | {"json_output": str(args.json_output) if args.json_output else None},
              "preflight": preflight, "rounds": [], "differences": [], "summary": {}}
    reference = preflight["stdlib"]["checks"]["probes"]
    blocked = False
    for backend in BACKENDS:
        print(f"ENVIRONMENT {backend}: {json.dumps(preflight[backend]['environment'], sort_keys=True)}")
        for key, actual in preflight[backend]["checks"]["probes"].items():
            expected = reference[key]
            if actual != expected:
                difference = {"backend": backend, "probe": key, "actual": actual, "strict_stdlib": expected}
                report["differences"].append(difference)
                print("BEHAVIOR DIFFERENCE " + json.dumps(difference, sort_keys=True))
                if backend in ("candidate", "previous"):
                    blocked = True
    # Candidate must preserve previous behavior as well as the strict reference.
    for backend in ("candidate", "previous"):
        if not all(preflight[backend]["checks"]["timed_equivalent"].values()):
            blocked = True
    cases = _cases(args.sizes)
    eligible = [key for key in cases
                if all(preflight[backend]["checks"]["timed_equivalent"][key] for backend in BACKENDS)]
    for key in cases:
        if key not in eligible:
            print(f"EXCLUDED non-equivalent timed case: {key}")
    print("METHOD: fresh process per backend per round; forward/reverse alternating paired order; "
          "adaptive batches; all correctness probes precede timing. Both decoders take str, return bytes.")
    print("LIMITATIONS: Python-call microbenchmark, GC default, no automatic CPU pinning or frequency control; "
          "caps may yield short batches; process samples are not statistical independence guarantees. "
          "stdlib includes public type guards and ASCII conversion. Legacy behavior differences are not parity. "
          "Terminal-NUL data is correctness-only. No overall KEEP follows from this benchmark alone.")
    if blocked:
        print("SKIP: candidate/previous failed strict behavior parity; no timings collected.")
    else:
        for index in range(args.samples):
            order = BACKENDS if index % 2 == 0 else tuple(reversed(BACKENDS))
            results = {}
            for backend in order:
                result = _run(args, backend, roots[backend], "time", eligible)
                if result["checks"] != preflight[backend]["checks"] or result["environment"] != preflight[backend]["environment"]:
                    raise RuntimeError(f"{backend} behavior/environment changed between samples")
                results[backend] = result["timings"]
            report["rounds"].append({"order": order, "results": results})
            print(f"Round {index + 1}/{args.samples}: {' -> '.join(order)}", flush=True)
        for key in eligible:
            summary = {}
            print(f"\n{key}")
            for backend in BACKENDS:
                values = [row["results"][backend][key]["ns_per_call"] for row in report["rounds"]]
                summary[backend] = {"mean_ns": statistics.mean(values), "stdev_ns": statistics.stdev(values),
                                    "min_ns": min(values), "max_ns": max(values), "samples_ns": values}
                stats = summary[backend]
                print(f"  {backend:9} mean={stats['mean_ns']:.1f} ns stdev={stats['stdev_ns']:.1f} "
                      f"range=[{min(values):.1f}, {max(values):.1f}]")
            ratios = {}
            for backend in BACKENDS[1:]:
                paired = [row["results"]["candidate"][key]["ns_per_call"] / row["results"][backend][key]["ns_per_call"]
                          for row in report["rounds"]]
                ratio = summary["candidate"]["mean_ns"] / summary[backend]["mean_ns"]
                ratios[backend] = {"ratio_of_means": ratio, "paired_mean": statistics.mean(paired),
                                   "paired_stdev": statistics.stdev(paired), "paired_ratios": paired}
                print(f"  candidate/{backend}: means={ratio:.4f}; paired mean={statistics.mean(paired):.4f} "
                      f"stdev={statistics.stdev(paired):.4f} range=[{min(paired):.4f}, {max(paired):.4f}]")
            prior = ratios["previous"]
            if prior["ratio_of_means"] <= .95 and max(prior["paired_ratios"]) <= .95:
                decision = "KEEP evidence for this case only (all paired rounds >=5% faster)"
            elif prior["ratio_of_means"] > 1.05 and min(prior["paired_ratios"]) >= 1.05:
                decision = "SKIP / repeatable regression versus previous (all paired rounds >=5% slower)"
            elif prior["ratio_of_means"] > 1.05:
                decision = "INCONCLUSIVE / mean suggests regression but paired rounds are inconsistent"
            elif min(prior["paired_ratios"]) >= 1.05:
                decision = "INCONCLUSIVE / paired rounds suggest regression but mean does not"
            else:
                decision = "SKIP / no repeatable >=5% win established"
            print("  " + decision)
            report["summary"][key] = {"backends": summary, "ratios": ratios, "decision": decision}
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if blocked or len(eligible) != len(cases) else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--_worker"]:
        print(json.dumps(_worker(json.load(sys.stdin)), sort_keys=True))
    else:
        raise SystemExit(main())
