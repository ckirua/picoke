"""Benchmark Python overhead in strict pybase64 decode wrappers.

Runs each implementation in isolated fresh processes; pin CPU externally when
needed. Candidate root contains ``_pybase64_wrapper_candidate``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

BACKENDS = ("attr", "cached", "vector", "picoke", "pybase64")
DEFAULT_SIZES = (8, 3072, 3073, 3074, 65536, 1048576)


def _worker(request):
    import base64
    import importlib.metadata
    import pybase64
    import picoke.crypto.base64 as picoke_base64

    import sys
    sys.path.insert(0, request["candidate_root"])
    import _pybase64_wrapper_candidate as candidate
    functions = {
        "attr": candidate.b64decode_attr,
        "cached": candidate.b64decode_cached,
        "vector": candidate.b64decode_vector,
        "picoke": picoke_base64.b64decode,
        "pybase64": lambda text: pybase64.b64decode(text, validate=True),
    }
    def outcome(function, value):
        try:
            result = function(value)
            return {"result_type": type(result).__module__ + "." + type(result).__qualname__, "result": result.hex() if type(result) is bytes else repr(result)}
        except Exception as exc:
            return {"exception": type(exc).__module__ + "." + type(exc).__qualname__, "message": str(exc)}

    probes = {}
    payloads = (b"", b"\0", b"\0\0", b"abc", b"abc\0\0", bytes(range(256)),
                b"x" * 3071 + b"\0", b"x" * 3072 + b"\0\0",
                b"x" * 3073 + b"\0\0\0")
    texts = {f"binary/{index}/lenmod3-{len(data) % 3}": base64.b64encode(data).decode("ascii")
             for index, data in enumerate(payloads)}
    texts.update({"syntax/" + repr(text): text for text in ("é", "Zg=", "Zg===", "=Zg=", "Zm9v====", "Zg==x", "A", "====")})
    texts.update({"long/" + repr(text[-8:]): text for text in
                  ("A" * 4096 + "é", "A" * 4096 + "\ud800", "A" * 4095 + "!",
                   "A" * 4092 + "AA==")})
    class Overridden(str):
        def encode(self, *args, **kwargs):
            raise RuntimeError("overridden encode invoked")
    texts["subclass/overridden-encode"] = Overridden("Zg==")

    class StringProxy:
        @property
        def __class__(self):
            return str

        def encode(self, *args, **kwargs):
            return b"Zm9v"

    texts["virtual-str"] = StringProxy()
    texts["wrong-type"] = b"Zm9v"
    for label, text in texts.items():
        probes[label] = {name: outcome(function, text) for name, function in functions.items()}
    parity = {label: all(values[name] == values["picoke"] for name in ("attr", "cached", "vector"))
              for label, values in probes.items()}
    result = {
        "probes": probes, "parity": parity,
        "environment": {"executable": sys.executable, "python": sys.version,
                        "platform": platform.platform(), "machine": platform.machine(),
                        "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
                        "pybase64_version": importlib.metadata.version("pybase64"),
                        "picoke_module": picoke_base64.__file__, "candidate_module": candidate.__file__,
                        "picoke_sha256": hashlib.sha256(Path(picoke_base64.__file__).read_bytes()).hexdigest(),
                        "candidate_sha256": hashlib.sha256(Path(candidate.__file__).read_bytes()).hexdigest(),
                        "pybase64_simd": pybase64._pybase64._get_simd_name(pybase64._pybase64._get_simd_path())},
    }
    if request["mode"] == "time":
        timings = {}
        for size in request["sizes"]:
            payload = (bytes(range(251)) * ((size + 250) // 251))[:size]
            text = base64.b64encode(payload).decode("ascii")
            name = request["backend"]
            function = functions[name]
            expected = functions["picoke"](text)
            if type(expected) is not bytes or function(text) != expected:
                raise RuntimeError(f"Non-equivalent output: {name}, {size}")
            key = f"{size}/{name}"
            count = 1
            cap = min(1000000, max(1, 100000000 // max(size, 1)))
            while True:
                start = time.perf_counter_ns()
                for _ in range(count):
                    function(text)
                elapsed = time.perf_counter_ns() - start
                if elapsed >= request["target_ns"] or count >= cap:
                    break
                count = min(cap, count * 2)
            for _ in range(request["warmups"]):
                for _ in range(count):
                    function(text)
            start = time.perf_counter_ns()
            for _ in range(count):
                value = function(text)
            timings[key] = {"ns_per_call": (time.perf_counter_ns() - start) / count,
                            "iterations": count, "result_len": len(value)}
        result["timings"] = timings
    return result


def _run(args, mode, backend=None):
    request = {"mode": mode, "backend": backend, "candidate_root": args.candidate_root,
               "sizes": args.sizes, "warmups": args.warmups,
               "target_ns": int(args.target_ms * 1_000_000)}
    completed = subprocess.run([args.python, "-I", str(Path(__file__).resolve()), "--_worker"],
                               input=json.dumps(request), text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"worker failed ({completed.returncode}):\n{completed.stderr}\n{completed.stdout}")
    return json.loads(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--candidate-root", required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES, metavar="BYTES")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--target-ms", type=float, default=20)
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()
    if args.samples < 7 or any(size < 0 for size in args.sizes) or not 0 < args.target_ms < 60000 or args.warmups < 1:
        parser.error("require samples >= 7, nonnegative sizes, 0 < target-ms < 60000, and warmups >= 1")
    args.candidate_root = str(Path(args.candidate_root).resolve())
    preflight = _run(args, "check")
    differences = [{"probe": label, "outcomes": outcomes} for label, outcomes in preflight["probes"].items() if not preflight["parity"][label]]
    report = {"command": sys.argv, "configuration": vars(args) | {"json_output": str(args.json_output) if args.json_output else None},
              "environment": preflight["environment"], "probes": preflight["probes"],
              "differences": differences, "rounds": [], "summary": {}}
    blocked = bool(differences)
    print("ENVIRONMENT " + json.dumps(report["environment"], sort_keys=True))
    if blocked:
        print("SKIP: correctness mismatch; no timings collected")
    else:
        for index in range(args.samples):
            order = BACKENDS if index % 2 == 0 else tuple(reversed(BACKENDS))
            round_data = {}
            for backend in order:
                result = _run(args, "time", backend)
                if result["probes"] != preflight["probes"]:
                    raise RuntimeError("behavior changed between preflight and timing")
                round_data[backend] = result["timings"]
            report["rounds"].append({"order": order, "results": round_data})
            print(f"Round {index + 1}/{args.samples}: {' -> '.join(order)}", flush=True)
        for size in args.sizes:
            for backend in BACKENDS:
                key = f"{size}/{backend}"
                values = [row["results"][backend][key]["ns_per_call"] for row in report["rounds"]]
                report["summary"][key] = {"mean_ns": statistics.mean(values), "stdev_ns": statistics.stdev(values), "samples_ns": values}
            ratios = {}
            for backend in BACKENDS[1:]:
                paired = [row["results"]["attr"][f"{size}/attr"]["ns_per_call"] / row["results"][backend][f"{size}/{backend}"]["ns_per_call"] for row in report["rounds"]]
                ratios[backend] = {"paired_mean": statistics.mean(paired), "paired_stdev": statistics.stdev(paired), "paired_ratios": paired}
            report["summary"][f"{size}/ratios"] = ratios
            print(f"{size} bytes: " + ", ".join(f"{b}={report['summary'][f'{size}/{b}']['mean_ns']:.1f}ns" for b in BACKENDS))
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if blocked else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--_worker"]:
        print(json.dumps(_worker(json.load(sys.stdin)), sort_keys=True))
    else:
        raise SystemExit(main())
