"""Compare experimental OpenSSL SIMD Base64 wrappers with picoke and binascii.

Example:
  python bench/openssl_simd_compare.py --python /path/to/python \
    --candidate-root /tmp/candidate --openssl-libdir /tmp/openssl/install/lib \
    --samples 7 --json-output /tmp/openssl-simd.json

Each backend/sample runs in a fresh process; only candidate workers receive the
custom OpenSSL loader path. Timings cover the complete public decode wrappers.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import ctypes
import hashlib
import importlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
OPENSSL_CAPS_SCALAR = ":~0x20:~0x0:~0x0:~0x0"

BACKENDS = ("openssl", "openssl_memchr", "openssl_scalar",
            "openssl_memchr_scalar", "picoke", "binascii")
DEFAULT_SIZES = (3072, 65536, 1048576, 8388608)


def _outcome(fn, value):
    try:
        result = fn(value)
        return {"type": type(result).__name__, "value": result.hex() if isinstance(result, bytes) else repr(result)}
    except Exception as exc:
        return {"type": type(exc).__name__, "message": str(exc)}


def _stdlib_decode(value):
    if type(value) is str:
        return binascii.a2b_base64(value, strict_mode=True)
    if not isinstance(value, str):
        raise TypeError("text must be str")
    return base64.b64decode(value, validate=True)


def _checks(fn):
    probes = {}
    for n in range(24):
        raw = bytes((i * 73 + n * 19) & 255 for i in range(n)) + (b"\0" if n else b"")
        probes[f"valid/{n}"] = _outcome(fn, base64.b64encode(raw).decode("ascii"))
    probes["embedded-terminal-nul"] = _outcome(fn, base64.b64encode(b"A\0B\0").decode())
    malformed = ("!AAA", "AA!A", "AAA!", "AA A", "AA\nA", "A", "AA", "AAA", "====",
                 "A===", "AA==x", "AAAA=", "AAAA====", "éAAA")
    for i, text in enumerate(malformed):
        probes[f"invalid/{i}/{text!r}"] = _outcome(fn, text)
    for position in (0, 2048, 4095):
        for bad in ("!", " ", "=", "\0"):
            text = "A" * position + bad + "A" * (4095 - position)
            probes[f"large-invalid/{position}/{bad!r}"] = _outcome(fn, text)
    for text in ("A" * 4096 + "é", "A" * 4096 + "\ud800", "A" * 4092 + "AA=="):
        probes[f"large-edge/{text[-4:]!r}"] = _outcome(fn, text)
    probes["wrong-bytes"] = _outcome(fn, b"Zm9v")
    probes["wrong-none"] = _outcome(fn, None)

    class StrSubclass(str):
        def encode(self, *args, **kwargs):
            raise AssertionError("overridden encode called")

    probes["str-subclass-encode-override"] = _outcome(fn, StrSubclass("AA=="))
    class StringProxy:
        @property
        def __class__(self):
            return str

        def encode(self, *args, **kwargs):
            return b"Zm9v"

    probes["virtual-str"] = _outcome(fn, StringProxy())
    return probes


def _origin_report(candidate, libdir):
    import importlib.machinery
    mod = importlib.import_module(candidate)
    path = Path(mod.__file__).resolve()
    if not any(str(path).endswith(s) for s in importlib.machinery.EXTENSION_SUFFIXES):
        raise RuntimeError(f"candidate must be compiled extension, got {path}")
    mapped = sorted({Path(line.split()[-1]).resolve() for line in Path("/proc/self/maps").read_text().splitlines()
                     if "libcrypto" in line and line.split()[-1].startswith("/")})
    expected = Path(libdir).resolve()
    matching = [p for p in mapped if p.parent == expected and p.name.startswith("libcrypto.so.4")]
    if not matching:
        raise RuntimeError(f"candidate did not load libcrypto.so.4 from exact --openssl-libdir {expected}: {mapped}")
    libs = []
    for p in matching:
        try:
            lib = ctypes.CDLL(str(p))
            lib.OpenSSL_version.argtypes = [ctypes.c_int]
            lib.OpenSSL_version.restype = ctypes.c_char_p
            version = lib.OpenSSL_version(0).decode("ascii", "replace")
        except (OSError, AttributeError):
            version = None
        libs.append({"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "version": version})
    return {"extension": str(path), "extension_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "libcrypto": libs, "mapped_libcrypto": [str(p) for p in mapped]}


def _load(req):
    if req["backend"].startswith("openssl"):
        if req.get("candidate_root"):
            sys.path.insert(0, req["candidate_root"])
        module = importlib.import_module("_openssl_simd_candidate")
        method = "b64decode_memchr" if "memchr" in req["backend"] else "b64decode"
        return getattr(module, method), _origin_report("_openssl_simd_candidate", req["openssl_libdir"])
    if req["backend"] == "pybase64_wrapper":
        sys.path.insert(0, req["wrapper_root"])
        module = importlib.import_module("_pybase64_wrapper_candidate")
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(Path(req["wrapper_root"]).resolve()):
            raise RuntimeError(f"Provider wrapper escaped requested root: {path}")
        return module.b64decode_attr, {"module": str(path),
                                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if req["backend"] == "picoke":
        module = importlib.import_module("picoke.crypto.base64")
        path = Path(module.__file__).resolve()
        return module.b64decode, {"module": str(path),
                                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return _stdlib_decode, {"module": getattr(binascii, "__file__", binascii.__spec__.origin)}


def _worker(req):
    fn, origin = _load(req)
    checks = _checks(fn)
    reference = _checks(_stdlib_decode)
    differences = {key: {"actual": value, "expected": reference.get(key)}
                   for key, value in checks.items() if value != reference.get(key)}
    if req["backend"] == "picoke":
        fn(base64.b64encode(b"x" * 4096).decode("ascii"))
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines()
                    if line.startswith("model name")), cpu)
    env = {"python": sys.version, "executable": sys.executable, "platform": platform.platform(),
           "machine": platform.machine(), "cpu": cpu,
           "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
           "openssl_ia32cap": os.environ.get("OPENSSL_ia32cap"),
           "origin": origin}
    try:
        import importlib.metadata
        env["pybase64_version"] = importlib.metadata.version("pybase64")
    except importlib.metadata.PackageNotFoundError:
        env["pybase64_version"] = None
    simd = sys.modules.get("pybase64._pybase64")
    env["pybase64_simd"] = ({"origin": simd.__file__, "active_path": simd._get_simd_name(simd._get_simd_path())}
                            if simd else None)
    timings = {}
    for size in req.get("sizes", []):
        raw = (bytes(range(256)) * ((size + 255) // 256))[:size - 1] + b"!"
        text = base64.b64encode(raw).decode("ascii")
        expected = _stdlib_decode(text)
        actual = fn(text)
        if type(actual) is not bytes or actual != expected:
            raise RuntimeError(f"{req['backend']} output differs for {size} bytes")
        if req.get("mode") != "time":
            continue
        count = 1
        cap = max(1, min(100000, 64_000_000 // max(1, size)))
        while True:
            start = time.perf_counter_ns()
            for _ in range(count):
                fn(text)
            elapsed = time.perf_counter_ns() - start
            if elapsed >= req["target_ns"] or count >= cap:
                break
            count = min(cap, count * 2)
        for _ in range(2):
            for _ in range(count):
                fn(text)
        start = time.perf_counter_ns()
        for _ in range(count):
            value = fn(text)
        elapsed = time.perf_counter_ns() - start
        if type(value) is not bytes:
            raise RuntimeError("decoder return type changed")
        timings[str(size)] = {"ns_per_call": elapsed / count, "iterations": count, "elapsed_ns": elapsed}
    return {"checks": checks, "differences": differences, "environment": env, "timings": timings}


def _run(args, backend, mode):
    request = {"backend": backend, "mode": mode, "candidate_root": args.candidate_root,
               "openssl_libdir": args.openssl_libdir, "wrapper_root": args.wrapper_root,
               "sizes": args.sizes,
               "target_ns": int(args.target_ms * 1_000_000)}
    env = os.environ.copy()
    env.pop("OPENSSL_ia32cap", None)
    env.pop("LD_PRELOAD", None)
    if backend.startswith("openssl"):
        env["LD_LIBRARY_PATH"] = str(Path(args.openssl_libdir).resolve())
        if backend.endswith("scalar"):
            env["OPENSSL_ia32cap"] = OPENSSL_CAPS_SCALAR
    else:
        env.pop("LD_LIBRARY_PATH", None)
        env.pop("OPENSSL_ia32cap", None)
    proc = subprocess.run([args.python, "-I", str(Path(__file__).resolve()), "--_worker"],
                          input=json.dumps(request), text=True, capture_output=True, env=env)
    if proc.returncode:
        raise RuntimeError(f"{backend} worker failed: {proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--candidate-root", required=True, help="directory containing _openssl_simd_candidate extension")
    parser.add_argument("--openssl-libdir", required=True, help="directory containing candidate's libcrypto")
    parser.add_argument("--wrapper-root", help="directory containing the compiled pybase64 wrapper candidate")
    parser.add_argument("--backends", nargs="+", help="subset of backends, including picoke and binascii")
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--sizes", type=int, nargs="+", default=DEFAULT_SIZES, metavar="DECODED_BYTES")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--target-ms", type=float, default=20)
    args = parser.parse_args()
    if args.samples < 7 or any(n <= 0 for n in args.sizes) or not 0 < args.target_ms < 60000:
        parser.error("require samples >= 7, positive sizes, and 0 < target-ms < 60000")
    args.candidate_root = str(Path(args.candidate_root).resolve())
    args.openssl_libdir = str(Path(args.openssl_libdir).resolve())
    if args.wrapper_root:
        args.wrapper_root = str(Path(args.wrapper_root).resolve())
    allowed = BACKENDS + (("pybase64_wrapper",) if args.wrapper_root else ())
    backends = tuple(args.backends) if args.backends else allowed
    if (len(set(backends)) != len(backends) or not {"picoke", "binascii"} <= set(backends)
            or not set(backends) <= set(allowed)):
        parser.error("backends must be unique supported names and include picoke and binascii")
    preflight = {b: _run(args, b, "check") for b in backends}
    reference = preflight["binascii"]["checks"]
    report = {"configuration": {"python": args.python, "candidate_root": args.candidate_root,
                                 "openssl_libdir": args.openssl_libdir, "wrapper_root": args.wrapper_root,
                                 "backends": backends, "samples": args.samples,
                                 "sizes": args.sizes, "target_ms": args.target_ms},
              "preflight": preflight, "rounds": [], "summary": {}}
    mismatch = False
    for backend in backends:
        for key, expected in reference.items():
            actual = preflight[backend]["checks"].get(key)
            if actual != expected:
                mismatch = True
                print(f"PREFLIGHT DIFFERENCE {backend} {key}: actual={actual!r} expected={expected!r}")
    print("Method: complete public wrapper, fresh worker per backend/sample, alternating paired order; ")
    print("preflight binary payloads and strict malformed-input outcomes against binascii.")
    if mismatch:
        raise RuntimeError("Strict behavior differs; refusing to benchmark.")
    for sample in range(args.samples):
        order = backends if sample % 2 == 0 else tuple(reversed(backends))
        results = {}
        for backend in order:
            result = _run(args, backend, "time")
            if result["checks"] != preflight[backend]["checks"]:
                raise RuntimeError(f"{backend} behavior changed after preflight")
            results[backend] = result["timings"]
        report["rounds"].append({"order": order, "results": results})
        print(f"Round {sample + 1}/{args.samples}: {' -> '.join(order)}", flush=True)
    for size in args.sizes:
        key = str(size)
        per_backend = {}
        for backend in backends:
            values = [r["results"][backend][key]["ns_per_call"] for r in report["rounds"]]
            per_backend[backend] = {"mean_ns": statistics.mean(values), "stdev_ns": statistics.stdev(values),
                                    "samples_ns": values}
        ratios = {b: per_backend[b]["mean_ns"] / per_backend["picoke"]["mean_ns"]
                  for b in backends if b != "picoke"}
        paired_ratios = {
            b: [row["results"][b][key]["ns_per_call"] /
                row["results"]["picoke"][key]["ns_per_call"] for row in report["rounds"]]
            for b in backends if b != "picoke"
        }
        report["summary"][key] = {"backends": per_backend, "openssl_over": ratios,
                                  "paired_ratios": paired_ratios}
        print(f"{size} decoded bytes: " + ", ".join(
            f"{b} {per_backend[b]['mean_ns']:.1f} ± {per_backend[b]['stdev_ns']:.1f} ns"
            for b in backends))
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 1 if mismatch else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--_worker"]:
        print(json.dumps(_worker(json.load(sys.stdin)), sort_keys=True))
    else:
        raise SystemExit(main())
