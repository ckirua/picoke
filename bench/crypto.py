"""Comparative microbenchmarks for picoke and a user-supplied baseline crypto build.

Run with ``python bench/crypto.py --legacy-root PATH`` after preparing
an isolated baseline package. These measurements are workload-specific.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import importlib.machinery
import importlib.metadata
import itertools
import os
import platform
import random
import statistics
import sys
import ssl
import sysconfig
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", required=True, metavar="PATH",
                        help="parent directory of the prepared baseline package")
    parser.add_argument("--samples", type=int, default=7,
                        help="number of measured batches per operation (default: 7)")
    parser.add_argument("--iterations", type=int, default=100,
                        help="calls per measured batch (default: 100)")
    parser.add_argument("--warmups", type=int, default=2,
                        help="untimed batches per operation (default: 2)")
    parser.add_argument("--sizes", type=int, nargs="+", default=[12, 1024, 1048576],
                        metavar="BYTES", help="Base64 payload sizes (default: 12 1024 1048576)")
    parser.add_argument("--crypto-size", type=int, default=200,
                        help="payload size for Keccak and Ed25519 (default: 200)")
    parser.add_argument("--seed", type=int, default=20261120,
                        help="deterministic workload seed (default: 20261120)")
    parser.add_argument("--order", choices=("alternate", "fixed"), default="alternate",
                        help="alternate implementation order per sample (default)")
    return parser


def _versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("picop", "pycryptodome", "pybase64", "btclib-secp256k1", "eth-account"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "not installed"
    try:
        from Crypto import __version__ as crypto_version
        result["Crypto runtime"] = crypto_version
    except ImportError:
        result["Crypto runtime"] = "unavailable"
    return result

def _cpu() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
            for line in cpuinfo:
                if line.startswith("model name"):
                    return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def _measure(label: str, operations: list[tuple[str, Callable[[], Any]]], *,
             samples: int, iterations: int, warmups: int, order: str) -> None:
    for _, operation in operations:
        for _ in range(warmups):
            for _ in range(iterations):
                operation()
    observations: dict[str, list[float]] = {name: [] for name, _ in operations}
    for sample in range(samples):
        batch = list(operations)
        if order == "alternate" and sample % 2:
            batch.reverse()
        for name, operation in batch:
            start = time.perf_counter_ns()
            for _ in range(iterations):
                operation()
            elapsed = time.perf_counter_ns() - start
            observations[name].append(elapsed / iterations)
    medians = {name: statistics.median(values) for name, values in observations.items()}
    baseline_name = next(iter(medians))
    baseline = medians[baseline_name]
    print(f"\n{label}")
    for name, values in observations.items():
        ordered = sorted(values)
        median = medians[name]
        p25 = ordered[(len(ordered) - 1) // 4]
        p75 = ordered[(3 * (len(ordered) - 1)) // 4]
        print(f"  {name:28} median {median:12.1f} ns/call  "
              f"p25..p75 {p25:.1f}..{p75:.1f}  "
              f"range {min(values):.1f}..{max(values):.1f}  "
              f"ratio-to-{baseline_name} {median / baseline:.3f}x")
        print("    samples ns/call: " + ", ".join(f"{value:.1f}" for value in values))
        print(f"    paired ratios to {baseline_name}: " + ", ".join(
            f"{value / base:.3f}" for value, base in zip(values, observations[baseline_name])))


def _verify(label: str, expected: Any, operations: list[tuple[str, Callable[[], Any]]]) -> None:
    for name, operation in operations:
        actual = operation()
        if actual != expected:
            raise RuntimeError(f"correctness mismatch for {label} ({name})")


def _legacy_package(root: str) -> str:
    packages = {
        path.relative_to(root).parts[0]
        for path in Path(root).glob("*/crypto/encoding/base64.*")
        if path.name.endswith(tuple(importlib.machinery.EXTENSION_SUFFIXES))
    }
    if len(packages) != 1:
        raise ValueError("expected one compiled legacy crypto package under --legacy-root")
    return packages.pop()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.samples < 1 or args.iterations < 1 or args.warmups < 0:
        raise SystemExit("samples and iterations must be positive; warmups cannot be negative")
    if not args.sizes or any(size < 0 for size in args.sizes) or args.crypto_size < 0:
        raise SystemExit("input sizes must be non-negative")
    legacy_root = os.path.abspath(args.legacy_root)
    if not os.path.isdir(legacy_root):
        raise SystemExit(f"legacy root is not a directory: {legacy_root}")
    sys.path.insert(0, legacy_root)

    # Defer both package imports until all CLI validation is complete.
    from picoke.crypto.base64 import b64decode, b64decode_ascii, b64encode, b64encode_ascii
    from picoke.crypto.ed25519 import ed25519_public_key, ed25519_sign, ed25519_verify
    from picoke.crypto.keccak import keccak256
    from Crypto.Hash import keccak as provider_keccak
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa as provider_eddsa
    package = _legacy_package(legacy_root)
    legacy_base64 = importlib.import_module(f"{package}.crypto.encoding.base64")
    legacy_decode = legacy_base64.b64decode
    legacy_decode_ascii = legacy_base64.b64decode_ascii
    legacy_encode = legacy_base64.b64encode
    legacy_encode_ascii = legacy_base64.b64encode_ascii
    legacy_keccak = importlib.import_module(f"{package}.crypto.hashes.keccak").keccak256
    legacy_ed25519 = importlib.import_module(f"{package}.crypto.curves.ed25519")
    legacy_public_key = legacy_ed25519.ed25519_public_key
    legacy_sign = legacy_ed25519.ed25519_sign
    legacy_verify = legacy_ed25519.ed25519_verify

    pending = []
    def defer(label, operations, **kwargs):
        pending.append((label, operations, kwargs))

    print("Comparative microbenchmark (not a reproduction of legacy benchmark cases)")
    print(f"Python: {sys.version.replace(os.linesep, ' ')}")
    print(f"Implementation/platform: {platform.python_implementation()} / {platform.platform()}")
    print(f"Machine/processor: {platform.machine()} / {_cpu()}")
    print(f"Legacy root: {legacy_root}")
    print(f"Counts: samples={args.samples}, iterations/sample={args.iterations}, warmups={args.warmups}, order={args.order}")
    print(f"Python OpenSSL: {ssl.OPENSSL_VERSION}; compiler: {platform.python_compiler()}")
    print(f"Python build CFLAGS: {sysconfig.get_config_var('CFLAGS')}")
    print(f"Libraries: {_versions()}")
    print(f"Legacy Keccak origin: {sys.modules[legacy_keccak.__module__].__file__}")
    print(f"Picoke Keccak origin: {sys.modules[keccak256.__module__].__file__}")

    for size in args.sizes:
        payload = random.Random(args.seed ^ size).randbytes(size)
        if payload.endswith(b"\0"):
            payload = payload[:-1] + b"\x01"
        encoded = base64.b64encode(payload)
        text = encoded.decode("ascii")
        _verify(f"Base64 encode {size}B", encoded, [
            ("legacy bytes", lambda: legacy_encode(payload)),
            ("picoke bytes", lambda: b64encode(payload)),
            ("stdlib bytes", lambda: base64.b64encode(payload)),
        ])
        _verify(f"Base64 encode ASCII {size}B", text, [
            ("legacy ASCII", lambda: legacy_encode_ascii(payload)),
            ("picoke ASCII", lambda: b64encode_ascii(payload)),
            ("stdlib + decode", lambda: base64.b64encode(payload).decode("ascii")),
        ])
        _verify(f"Base64 decode {size}B", payload, [
            ("legacy decode", lambda: legacy_decode(text)),
            ("picoke decode", lambda: b64decode(text)),
            ("stdlib decode", lambda: base64.b64decode(text, validate=True)),
        ])
        _verify(f"Base64 decode ASCII {size}B", payload, [
            ("legacy decode ASCII", lambda: legacy_decode_ascii(text)),
            ("picoke decode ASCII", lambda: b64decode_ascii(text)),
            ("stdlib decode", lambda: base64.b64decode(text, validate=True)),
        ])
        print(f"\nBase64 workload: {size} input bytes, {len(encoded)} encoded bytes; nonzero terminal input")
        for title, ops in (
            (f"encode -> bytes {size}B", [("legacy", lambda payload=payload: legacy_encode(payload)), ("picoke", lambda payload=payload: b64encode(payload)), ("stdlib", lambda payload=payload: base64.b64encode(payload))]),
            (f"encode -> ASCII str {size}B", [("legacy", lambda payload=payload: legacy_encode_ascii(payload)), ("picoke", lambda payload=payload: b64encode_ascii(payload)), ("stdlib+decode", lambda payload=payload: base64.b64encode(payload).decode("ascii"))]),
            (f"decode str -> bytes {size}B", [("legacy", lambda text=text: legacy_decode(text)), ("picoke", lambda text=text: b64decode(text)), ("stdlib", lambda text=text: base64.b64decode(text, validate=True))]),
            (f"decode ASCII -> bytes {size}B", [("legacy", lambda text=text: legacy_decode_ascii(text)), ("picoke", lambda text=text: b64decode_ascii(text)), ("stdlib", lambda text=text: base64.b64decode(text, validate=True))]),
        ):
            defer(title, ops, samples=args.samples, iterations=args.iterations,
                     warmups=args.warmups, order=args.order)
    zero_input = b"\x00"
    zero_text = base64.b64encode(zero_input).decode("ascii")
    legacy_zero = legacy_decode(zero_text)
    print(f"\nTerminal-NUL correctness: input={zero_input!r}, legacy decode={legacy_zero!r}, picoke decode={b64decode(zero_text)!r}; excluded from timing.")

    for size in dict.fromkeys((0, 12, 135, 136, 137, 200, 1024, args.crypto_size)):
        payload = random.Random(args.seed ^ 0x4B454343 ^ size).randbytes(size)
        expected_digest = keccak256(payload)
        direct_keccak = lambda payload=payload: provider_keccak.new(
            digest_bits=256, data=payload).digest()
        operations = [("picoke", lambda payload=payload: keccak256(payload)),
            ("PyCryptodome direct", direct_keccak)]
        legacy_digest = legacy_keccak(payload)
        if legacy_digest == expected_digest:
            operations.insert(0, ("legacy", lambda payload=payload: legacy_keccak(payload)))
        elif size and size % 136 in (0, 135):
            print(f"Keccak {size}B: legacy padding mismatch; legacy excluded "
                  f"(legacy={legacy_digest.hex()}, picoke={expected_digest.hex()})")
        else:
            raise RuntimeError(f"unexpected legacy Keccak mismatch at {size}B")
        _verify(f"Keccak {size}B", expected_digest, operations)
        defer(f"Keccak-256 {size}B", operations, samples=args.samples,
            iterations=args.iterations, warmups=args.warmups, order=args.order)

    seed = bytes(range(32))
    message = random.Random(args.seed ^ 0x45444453).randbytes(args.crypto_size)
    legacy_pub = legacy_public_key(seed)
    picoke_pub = ed25519_public_key(seed)
    provider_key = ECC.construct(curve="Ed25519", seed=seed)
    provider_pub = provider_key.public_key().export_key(format="raw")
    if legacy_pub != picoke_pub or legacy_pub != provider_pub:
        raise RuntimeError("correctness mismatch for Ed25519 public-key derivation")
    legacy_signature = legacy_sign(message, seed)
    picoke_signature = ed25519_sign(message, seed)
    provider_signer = provider_eddsa.new(provider_key, "rfc8032")
    provider_signature = provider_signer.sign(message)
    raw_sign = lambda: provider_eddsa.new(
        ECC.construct(curve="Ed25519", seed=seed), "rfc8032").sign(message)
    if raw_sign() != picoke_signature:
        raise RuntimeError("correctness mismatch for Ed25519 raw-seed provider signing")
    if legacy_signature != picoke_signature or legacy_signature != provider_signature:
        raise RuntimeError("correctness mismatch for Ed25519 signing")
    _verify("Ed25519 verify", True, [
        ("legacy", lambda: legacy_verify(message, legacy_signature, legacy_pub)),
        ("picoke", lambda: ed25519_verify(message, legacy_signature, legacy_pub)),
        ("PyCryptodome", lambda: _provider_verify(message, legacy_signature, provider_pub)),
    ])
    print(f"\nEd25519 workload: seed=bytes(range(32)), message={len(message)} bytes; provider verification has a weaker acceptance policy")
    defer("public-key derivation", [("legacy", lambda: legacy_public_key(seed)),
        ("picoke", lambda: ed25519_public_key(seed)),
        ("PyCryptodome direct", lambda: ECC.construct(curve="Ed25519", seed=seed).public_key().export_key(format="raw"))],
        samples=args.samples, iterations=args.iterations, warmups=args.warmups, order=args.order)
    defer("sign", [("legacy", lambda: legacy_sign(message, seed)),
        ("picoke", lambda: ed25519_sign(message, seed)),
        ("provider raw seed", raw_sign),
        ("provider pre-imported", lambda: provider_eddsa.new(provider_key, "rfc8032").sign(message))],
        samples=args.samples, iterations=args.iterations, warmups=args.warmups, order=args.order)
    defer("verify (valid signature only)", [("legacy", lambda: legacy_verify(message, legacy_signature, legacy_pub)),
        ("picoke", lambda: ed25519_verify(message, legacy_signature, legacy_pub)),
        ("provider weaker policy", lambda: _provider_verify(message, legacy_signature, provider_pub))],
        samples=args.samples, iterations=args.iterations, warmups=args.warmups, order=args.order)
    for label, operations, kwargs in pending:
        _measure(label, operations, **kwargs)
    return 0


def _provider_verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    from Crypto.Signature import eddsa

    verifier = eddsa.new(eddsa.import_public_key(public_key), "rfc8032")
    try:
        verifier.verify(message, signature)
    except (ValueError, TypeError):
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
