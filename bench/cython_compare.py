"""Compare compiled picoke with its frozen pure-Python package in isolated processes."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
import hashlib
from pathlib import Path

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable, help="Python executable used for both processes")
    parser.add_argument("--baseline-root", required=True, help="directory containing the frozen pure-Python picore/")
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--iterations", type=int, default=10000,
                        help="maximum iterations per operation and sample (default: 10000)")
    parser.add_argument("--warmups", type=int, default=2)
    return parser


def _jsonable(value):
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _versions() -> dict[str, str]:
    result = {"python": sys.version.replace("\n", " "), "platform": platform.platform()}
    for package in ("picop", "pycryptodome", "btclib-secp256k1", "eth-account"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "not installed"
    try:
        from Crypto import __version__ as crypto_version
        result["Crypto runtime"] = crypto_version
    except ImportError:
        result["Crypto runtime"] = "unavailable"
    return result


def _worker(root: str, samples: int, iterations: int, warmups: int) -> None:
    sys.path.insert(0, os.path.abspath(root))
    import base64
    package = "picore" if (Path(root) / "picore" / "__init__.py").is_file() else "picoke"
    crypto = importlib.import_module(f"{package}.crypto")

    secret = (1).to_bytes(32, "big")
    digest = bytes.fromhex("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08")
    message = b"picore deterministic benchmark\x00message"
    seed = bytes(range(32))
    domain = {"name": "Exchange", "version": "1", "chainId": 42161,
              "verifyingContract": "0x0000000000000000000000000000000000000000"}
    agent_source, connection_id = "a", bytes(range(32))
    full = {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                             {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "Mail": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "contents", "type": "string"}],
        }, "primaryType": "Mail", "domain": domain,
        "message": {"from": "0x0000000000000000000000000000000000000001",
                    "to": "0x0000000000000000000000000000000000000002", "contents": "Hello"},
    }
    pub = crypto.privkey_to_pubkey(secret)
    r, s, v = crypto.sign_recoverable(secret, digest)
    ed_pub = crypto.ed25519_public_key(seed)
    ed_sig = crypto.ed25519_sign(message, seed)
    bip_sig = crypto.bip137_sign_message(secret, message)
    cases = {}
    for size_name, payload in (
        ("small", b"picore\x00"),
        ("1KiB", bytes(range(256)) * 4),
        ("1MiB", bytes(range(256)) * 4096),
    ):
        encoded = base64.b64encode(payload).decode("ascii")
        cases[f"Base64 bytes encode {size_name}"] = lambda p=payload: crypto.b64encode(p)
        cases[f"Base64 str encode {size_name}"] = lambda p=payload: crypto.b64encode_ascii(p)
        cases[f"Base64 str decode {size_name}"] = lambda e=encoded: crypto.b64decode(e)
        cases[f"Base64 bytes decode {size_name}"] = lambda e=encoded: crypto.b64decode_ascii(e)
    for size_name, payload in (
        ("empty", b""),
        ("12-byte", b"picore\x00data!"),
        ("200-byte", message.ljust(200, b"\x00")),
        ("1KiB", bytes(range(256)) * 4),
    ):
        cases[f"Keccak-256 {size_name}"] = lambda p=payload: crypto.keccak256(p)
    cases.update({
        "Keccak-256": lambda: crypto.keccak256(message),
        "Ed25519 public key": lambda: crypto.ed25519_public_key(seed),
        "Ed25519 sign": lambda: crypto.ed25519_sign(message, seed),
        "Ed25519 verify": lambda: crypto.ed25519_verify(message, ed_sig, ed_pub),
        "secp256k1 public key": lambda: crypto.privkey_to_pubkey(secret),
        "Ethereum address": lambda: crypto.privkey_to_address(secret),
        "secp256k1 sign": lambda: crypto.sign_recoverable(secret, digest),
        "secp256k1 recover": lambda: crypto.recover_pubkey(digest, r, s, v - 27),
        "EIP-712 full message": lambda: crypto.eip712_hash_full_message(full),
        "EIP-712 Agent": lambda: crypto.eip712_hash_agent_message(domain, agent_source, connection_id),
        "BIP137 message hash": lambda: crypto.bip137_signed_message_hash(message),
        "BIP137 sign": lambda: crypto.bip137_sign_message(secret, message),
        "BIP137 verify": lambda: crypto.bip137_verify_message(message, bip_sig, pub),
    })
    outputs = {name: _jsonable(operation()) for name, operation in cases.items()}
    if outputs["Ed25519 verify"] is not True or outputs["BIP137 verify"] is not True:
        raise RuntimeError("signature verification failed")
    timings = {}
    iterations_used = {}
    for name, operation in cases.items():
        if "1MiB" in name:
            cap = min(iterations, 10)
        elif any(term in name for term in ("Ed25519", "secp256k1", "Ethereum address", "EIP-712", "BIP137")):
            cap = min(iterations, 100)
        else:
            cap = iterations
        start = time.perf_counter_ns()
        operation()
        one_call_ns = max(1, time.perf_counter_ns() - start)
        count = min(cap, max(1, (10_000_000 + one_call_ns - 1) // one_call_ns))
        iterations_used[name] = count
        for _ in range(warmups):
            for _ in range(count):
                operation()
        values = []
        for _ in range(samples):
            start = time.perf_counter_ns()
            for _ in range(count):
                operation()
            values.append((time.perf_counter_ns() - start) / count)
        timings[name] = values
    origins = {name: module.__file__ for name, module in sys.modules.items()
               if (name == package or name.startswith(f"{package}.")) and getattr(module, "__file__", None)}
    print(json.dumps({"outputs": outputs, "timings": timings, "iterations_used": iterations_used,
                      "origins": origins, "versions": _versions()}, sort_keys=True))


def _run(executable: str, root: str, samples: int, iterations: int, warmups: int) -> dict:
    script = str(Path(__file__).resolve())
    completed = subprocess.run(
        [executable, script, "--_worker", root, str(samples), str(iterations), str(warmups)],
        cwd=os.path.dirname(script), text=True, capture_output=True, check=False,
    )
    if completed.returncode:
        raise RuntimeError(f"benchmark worker failed ({root}):\n{completed.stderr.strip()}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid worker output ({root}): {completed.stdout!r}") from exc


def main(argv: list[str] | None = None) -> int:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--_worker":
        _, _, root, samples, iterations, warmups = sys.argv
        _worker(root, int(samples), int(iterations), int(warmups))
        return 0
    parser = _parser()
    args = parser.parse_args(argv)
    if args.samples < 1 or args.iterations < 1 or args.warmups < 0:
        parser.error("samples and iterations must be positive; warmups cannot be negative")
    current = str(Path(__file__).resolve().parents[1])
    baseline = os.path.abspath(args.baseline_root)
    if not os.path.isdir(os.path.join(baseline, "picore")):
        parser.error("--baseline-root must contain the frozen picore/ package directory")
    if os.path.samefile(current, baseline):
        parser.error("current and baseline package roots must be different")
    preflight = {
        "compiled": _run(args.python, current, 0, args.iterations, 0),
        "baseline": _run(args.python, baseline, 0, args.iterations, 0),
    }
    if preflight["compiled"]["outputs"] != preflight["baseline"]["outputs"]:
        raise RuntimeError("compiled and baseline outputs differ; refusing to time either implementation")
    results = {"compiled": [], "baseline": []}
    for sample in range(args.samples):
        order = (("compiled", current), ("baseline", baseline))
        if sample % 2:
            order = tuple(reversed(order))
        pair = {}
        for name, root in order:
            pair[name] = _run(args.python, root, 1, args.iterations, args.warmups)
        if any(pair[name]["outputs"] != preflight[name]["outputs"] for name in results):
            raise RuntimeError("deterministic outputs changed between preflight and timed worker")
        for name in results:
            results[name].append(pair[name])
    print(f"Python executable: {args.python}")
    print(f"Compiled root: {current}\nBaseline root: {baseline}")
    print(f"Paired samples={args.samples}, iteration cap={args.iterations}, warmups={args.warmups}; "
          "adaptive batches target 10 ms, with 1MiB capped at 10 and crypto operations at 100; outputs match.")
    for impl in ("compiled", "baseline"):
        print(f"\n{impl} versions: {json.dumps(results[impl][0]['versions'], sort_keys=True)}")
        print(f"{impl} module origins: {json.dumps(results[impl][0]['origins'], sort_keys=True)}")
    print("\nTimes are per-call means across iterations; spread is min..max of paired sample means.")
    names = results["compiled"][0]["timings"]
    for case in names:
        compiled = [entry["timings"][case][0] for entry in results["compiled"]]
        baseline_values = [entry["timings"][case][0] for entry in results["baseline"]]
        cmean, bmean = statistics.mean(compiled), statistics.mean(baseline_values)
        counts_c = [entry["iterations_used"][case] for entry in results["compiled"]]
        counts_b = [entry["iterations_used"][case] for entry in results["baseline"]]
        print(f"{case} (calls/sample compiled={min(counts_c)}..{max(counts_c)}, "
              f"baseline={min(counts_b)}..{max(counts_b)}): "
              f"compiled mean {cmean:.1f} ns (spread {min(compiled):.1f}..{max(compiled):.1f}); "
              f"baseline mean {bmean:.1f} ns (spread {min(baseline_values):.1f}..{max(baseline_values):.1f}); "
              f"compiled/baseline {cmean / bmean:.3f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
