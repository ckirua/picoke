#!/usr/bin/env python3
"""Isolated installed-wheel EIP-712 Agent benchmark and provider attribution.

Run with baseline and candidate interpreter paths. JSON is written to stdout,
so preserve it with ``> result.json``. Paired ratios are candidate/baseline.
"""
from __future__ import annotations

import argparse
import cProfile
import importlib.metadata
import io
import json
import os
import pstats
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from crypto import _cpu

# Frozen fixtures mirror tests/test_crypto_eip712.py; no pytest dependency.
DOMAIN = {"name": "Exchange", "version": "1", "chainId": 42161,
          "verifyingContract": "0x0000000000000000000000000000000000000000"}
AGENT_VECTOR_DOMAIN = {"name": "Agent", "version": "1", "chainId": 42161,
                      "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"}
AGENT_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"},
    ],
    "Agent": [{"name": "source", "type": "string"}, {"name": "connectionId", "type": "bytes32"}],
}
MAIL = {
    "types": {
        "EIP712Domain": AGENT_TYPES["EIP712Domain"],
        "Person": [{"name": "name", "type": "string"}, {"name": "wallet", "type": "address"}],
        "Mail": [{"name": "from", "type": "Person"}, {"name": "to", "type": "Person"},
                 {"name": "contents", "type": "string"}],
    }, "primaryType": "Mail",
    "domain": {"name": "Ether Mail", "version": "1", "chainId": 1,
                "verifyingContract": "0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC"},
    "message": {"from": {"name": "Cow", "wallet": "0xCD2a3d9F938E13CD947Ec05AbC7FE734Df8DD826"},
                "to": {"name": "Bob", "wallet": "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"},
                "contents": "Hello, Bob!"},
}
ARRAY_BASE = {"types": {"EIP712Domain": [], "Item": [{"name": "v", "type": "uint256"}],
                         "Data": [{"name": "items", "type": "Item[]"}]},
              "primaryType": "Data", "domain": {}}


def _workloads() -> dict[str, tuple[dict, str, bytes, str | None]]:
    historical = (DOMAIN, "a", bytes(range(32)))
    vector = (AGENT_VECTOR_DOMAIN, "wss://example.test", bytes(range(32)))
    unicode = (DOMAIN, "π snowman ☃", bytes(32))
    expected = "b4213747a19486f4f2ceb0f7222b9733f612682fd3649afd6a605a21ac36f2da"
    fallback = ({**AGENT_VECTOR_DOMAIN, "chainId": "42161"}, *vector[1:])
    return {
        "agent_historical": (*historical, None),
        "agent_ethers_vector": (*vector, expected),
        "agent_unicode": (*unicode, None),
        "agent_coercion_fallback": (*fallback, expected),
    }


def _direct_operation(domain: dict, source: str, connection_id: bytes) -> Callable[[], bytes]:
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak

    def direct() -> bytes:
        envelope = {
            "types": AGENT_TYPES, "primaryType": "Agent", "domain": domain,
            "message": {"source": source, "connectionId": connection_id},
        }
        signable = encode_typed_data(full_message=envelope)
        return keccak(b"\x19" + signable.version + signable.header + signable.body)

    return direct


def _profile(fn: Callable[[], bytes]) -> str:
    with cProfile.Profile() as profile:
        for _ in range(1000):
            fn()
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(30)
    return stream.getvalue()


def _elapsed_ns(fn: Callable[[], bytes], iterations: int) -> int:
    start = time.perf_counter_ns()
    for _ in range(iterations):
        fn()
    return time.perf_counter_ns() - start


def _child(args: argparse.Namespace) -> int:
    import picore.crypto.eip712 as eip712
    origin = Path(eip712.__file__).resolve()
    if origin.suffix not in {".so", ".pyd", ".dll", ".dylib"} or not origin.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError(f"eip712 native extension is not installed under sys.prefix: {origin}")
    full = eip712.eip712_hash_full_message
    agent = eip712.eip712_hash_agent_message
    workloads = _workloads()
    operations: dict[str, Callable[[], bytes]] = {}
    preflight: dict[str, Any] = {}
    for name, (domain, source, conn, expected) in workloads.items():
        fn = lambda d=domain, s=source, c=conn: agent(d, s, c)
        got = fn()
        preflight[name] = {"ok": True, "digest": got.hex(), "expected": expected}
        if expected is not None and got.hex() != expected:
            preflight[name]["ok"] = False
        operations[name] = fn
    array_digest = "076a90553e534f54d979119bcbfd7ef5fdf0c10871ca96c5aaad45e4f783618d"
    for label, message, expected in (
        ("full_mail", MAIL, "be609aee343fb3c4b28e1df9e632fca64fcfaede20f02e86244efddf30957bd2"),
        ("full_array_list", {**ARRAY_BASE, "message": {"items": [{"v": 1}, {"v": 2}]}}, array_digest),
        ("full_array_tuple", {**ARRAY_BASE, "message": {"items": ({"v": 1}, {"v": 2})}}, array_digest),
    ):
        operations[label] = lambda m=message: full(m)
        digest = operations[label]().hex()
        preflight[label] = {"ok": digest == expected, "digest": digest, "expected": expected}
    # The specified direct-provider attribution uses same envelope per call,
    # without picore validation; fixed schema is prepared above.
    d, s, c, _ = workloads["agent_historical"]
    direct_label = "direct_provider_historical"
    operations[direct_label] = _direct_operation(d, s, c)
    digest = operations[direct_label]().hex()
    expected = preflight["agent_historical"]["digest"]
    preflight[direct_label] = {"ok": digest == expected, "digest": digest, "expected": expected}
    result: dict[str, Any] = {
        "origin": str(origin),
        "prefix": sys.prefix,
        "python": sys.version,
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("picore", "eth-account", "eth-utils", "eth-abi", "pycryptodome")
        },
        "preflight": preflight,
    }
    if not all(item["ok"] for item in preflight.values()):
        print(json.dumps(result))
        return 2
    if args.worker:
        print(json.dumps(result), flush=True)
        for request in sys.stdin:
            label, iterations = json.loads(request)
            print(_elapsed_ns(operations[label], iterations), flush=True)
        return 0
    if args.profile_only:
        result["profiles_1000_calls"] = {
            label: _profile(operations[label]) for label in ("agent_historical", direct_label)
        }
    elif not args.preflight_only:
        timings = {}
        for label, fn in operations.items():
            for _ in range(args.warmups * args.iterations):
                fn()
            timings[label] = _elapsed_ns(fn, args.iterations) / args.iterations
        result["timings_ns_per_call"] = timings
    print(json.dumps(result))
    return 0


def _summary(values: list[float]) -> dict[str, float]:
    return {"min": min(values), "median": statistics.median(values), "max": max(values)}


def _child_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _child_command(interpreter: str, *args: str) -> list[str]:
    # abspath preserves the venv's python symlink for prefix resolution.
    return [os.path.abspath(interpreter), str(Path(__file__).resolve()), "--_child", *args]


def _run(interpreter: str, args: argparse.Namespace, *mode: str) -> dict[str, Any]:
    proc = subprocess.run(
        _child_command(interpreter, "--iterations", str(args.iterations),
                       "--warmups", str(args.warmups), *mode),
        cwd="/tmp", env=_child_environment(), check=False, capture_output=True, text=True,
    )
    if proc.returncode:
        raise RuntimeError(f"child failed ({interpreter}): {proc.stderr or proc.stdout}")
    return json.loads(proc.stdout)


def _worker_block(worker: subprocess.Popen[str], label: str, calls: int) -> int:
    assert worker.stdin is not None and worker.stdout is not None
    print(json.dumps((label, calls)), file=worker.stdin, flush=True)
    response = worker.stdout.readline()
    if not response:
        raise RuntimeError(f"benchmark worker exited without a sample: {worker.args}")
    return int(response)


def _interleaved_samples(
    arms: tuple[tuple[str, str], ...], labels: list[str], args: argparse.Namespace
) -> Iterator[dict[str, dict[str, float]]]:
    with ExitStack() as stack:
        workers = {}
        for side, interpreter in arms:
            worker = stack.enter_context(subprocess.Popen(
                _child_command(interpreter, "--worker"), cwd="/tmp", env=_child_environment(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            ))
            assert worker.stdin is not None and worker.stdout is not None
            stack.callback(worker.stdin.close)
            ready = worker.stdout.readline()
            if not ready or not all(item["ok"] for item in json.loads(ready)["preflight"].values()):
                raise RuntimeError(f"benchmark worker failed preflight: {interpreter}")
            workers[side] = worker
        sides = tuple(workers)
        for label in labels:
            for side in sides:
                _worker_block(workers[side], label, args.iterations * args.warmups)

        width = args.paired_block_size
        for sample in range(args.samples):
            observed: dict[str, dict[str, float]] = {side: {} for side in sides}
            for label in labels:
                elapsed = dict.fromkeys(sides, 0)
                for block, offset in enumerate(range(0, args.iterations, width)):
                    calls = min(width, args.iterations - offset)
                    order = sides if (sample + block) % 2 == 0 else reversed(sides)
                    for side in order:
                        elapsed[side] += _worker_block(workers[side], label, calls)
                for side in sides:
                    observed[side][label] = elapsed[side] / args.iterations
            yield observed


def _isolated_samples(
    arms: tuple[tuple[str, str], ...], args: argparse.Namespace
) -> Iterator[dict[str, dict[str, float]]]:
    for index in range(args.samples):
        order = arms if index % 2 == 0 else reversed(arms)
        yield {side: _run(interpreter, args)["timings_ns_per_call"] for side, interpreter in order}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-python")
    parser.add_argument("--candidate-python")
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmups", type=int, default=2, help="number of untimed batches")
    parser.add_argument(
        "--paired-block-size", type=int, metavar="CALLS",
        help="reuse warmed workers and alternate shorter blocks within each sample",
    )
    parser.add_argument("--profile", action="store_true", help="profile baseline separately after timing")
    parser.add_argument("--_child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--preflight-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--profile-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.samples <= 0 or args.iterations <= 0 or args.warmups < 0:
        parser.error("samples and iterations must be positive; warmups must be nonnegative")
    if args.paired_block_size is not None and args.paired_block_size <= 0:
        parser.error("--paired-block-size must be positive")
    if args._child:
        return _child(args)
    if not args.baseline_python or not args.candidate_python:
        parser.error("--baseline-python and --candidate-python are required")
    # Check both installations before timing any sample.
    baseline = _run(args.baseline_python, args, "--preflight-only")
    candidate = _run(args.candidate_python, args, "--preflight-only")
    if baseline["python"] != candidate["python"]:
        raise RuntimeError("baseline and candidate Python versions differ")
    for name in baseline["versions"].keys() - {"picore"}:
        if baseline["versions"][name] != candidate["versions"][name]:
            raise RuntimeError(f"baseline and candidate {name} versions differ")
    for label in baseline["preflight"]:
        if baseline["preflight"][label]["digest"] != candidate["preflight"][label]["digest"]:
            raise RuntimeError(f"preflight digest mismatch ({label})")
    labels = list(baseline["preflight"])
    samples = {"baseline": {key: [] for key in labels}, "candidate": {key: [] for key in labels}}
    ratios = {key: [] for key in labels}
    arms = (("baseline", args.baseline_python), ("candidate", args.candidate_python))
    measurements = (
        _interleaved_samples(arms, labels, args) if args.paired_block_size is not None
        else _isolated_samples(arms, args)
    )
    for observed in measurements:
        for label in labels:
            for side in ("baseline", "candidate"):
                samples[side][label].append(observed[side][label])
            ratios[label].append(observed["candidate"][label] / observed["baseline"][label])

    summaries = {
        side: {label: _summary(values) for label, values in side_data.items()}
        for side, side_data in samples.items()
    }
    public_median = summaries["baseline"]["agent_historical"]["median"]
    direct_median = summaries["baseline"]["direct_provider_historical"]["median"]
    metadata = ("origin", "prefix", "python", "versions")
    report = {
        "schema": "picore.eip712-agent-benchmark.v1",
        "cpu": _cpu(),
        "settings": {"samples": args.samples, "iterations": args.iterations, "warmups": args.warmups},
        "paired_block_size": args.paired_block_size,
        "cpu_affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "baseline": {key: baseline[key] for key in metadata},
        "candidate": {key: candidate[key] for key in metadata},
        "preflight": baseline["preflight"],
        "raw_ns_per_call": samples,
        "summaries_ns_per_call": summaries,
        "paired_candidate_over_baseline": {
            label: {"raw": values, **_summary(values)} for label, values in ratios.items()
        },
        "direct_provider_attribution": {
            "baseline_median_ns": public_median,
            "direct_median_ns": direct_median,
            "estimated_removable_fraction": 1 - direct_median / public_median,
        },
        "profile": (
            _run(args.baseline_python, args, "--profile-only")["profiles_1000_calls"]
            if args.profile else None
        ),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
