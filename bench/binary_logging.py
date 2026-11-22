#!/usr/bin/env python3
"""Compare end-to-end JSON and framed MessagePack logging delivery.

Run with ``python bench/binary_logging.py --samples 9`` from the repository
root. Each timing observation runs in a fresh process; paired samples alternate
the actual BackgroundJSONHandler and BackgroundBinaryHandler paths. The report
includes encoded sizes (including newline or binary framing), exact decoded
record parity, raw samples, repeated-code controls, and runtime environment.
MessagePack is optional (install the project's msgpack extra/package).
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from typing import Any


SHAPES = {
    "short": {"city": "Paris", "count": 3},
    "nested": {"request": {"items": [1, {"ok": True, "score": 2.5}], "tags": ["a", "b"]}},
    "unicode": {"city": "雪", "greeting": "こんにちは 🌍", "nested": {"ключ": "значение"}},
}


def _event(shape: str, index: int, logger: str) -> dict[str, Any]:
    return {
        "timestamp": "2024-01-02T03:04:05.006Z",
        "level": "INFO",
        "logger": logger,
        "message": f"processed {index} records",
        "fields": SHAPES[shape],
    }


class _Sink:
    def __init__(self) -> None:
        self.data = bytearray()
        self.records: list[bytes] = []

    def write(self, payload: bytes | memoryview) -> int:
        value = bytes(payload)
        self.data.extend(value)
        self.records.append(value)
        return len(payload)

    def flush(self) -> None:
        pass



def _measure(codec: str, shape: str, producers: int, count: int, capacity: int) -> dict[str, Any]:
    from picoke.binary_logging import BackgroundBinaryHandler, _encode_record, iter_binary_records
    from picoke.logging import BackgroundJSONHandler, BackgroundWriter, JSONFormatter

    sink = _Sink()
    expected: list[dict[str, Any] | None] = [None] * (producers * count)
    started = time.perf_counter_ns()
    writer = BackgroundWriter(sink, capacity=capacity, max_item_size=1_048_576)

    def produce(producer: int) -> None:
        handler = (BackgroundJSONHandler(writer) if codec == "json"
                   else BackgroundBinaryHandler(writer))
        logger = logging.Logger(f"binary_logging.worker.{producer}", logging.INFO)
        for index in range(count):
            global_index = producer * count + index
            event = _event(shape, global_index, logger.name)
            record = logging.LogRecord(logger.name, logging.INFO, "bench", 1,
                                       event["message"], (), None)
            record.created = 1_704_164_645.006
            record.fields = event["fields"]
            expected[global_index] = event
            handler.handle(record)

    threads = [threading.Thread(target=produce, args=(i,)) for i in range(producers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    producer_done_ns = time.perf_counter_ns()
    writer.flush()
    writer.close()
    finished_ns = time.perf_counter_ns()

    if len(sink.records) != len(expected):
        raise AssertionError(f"record count mismatch: {len(sink.records)} != {len(expected)}")
    decode_started = time.perf_counter_ns()
    if codec == "json":
        if any(not payload.endswith(b"\n") for payload in sink.records):
            raise AssertionError("JSON handler output is missing its newline")
        actual: list[Any] = [json.loads(payload) for payload in sink.records]
    else:
        actual = list(iter_binary_records(io.BytesIO(bytes(sink.data))))
    decode_ns = time.perf_counter_ns() - decode_started
    actual_sorted = sorted(actual, key=lambda event: event["message"])
    expected_sorted = sorted(expected, key=lambda event: event["message"])
    if actual_sorted != expected_sorted:
        raise AssertionError("decoded events differ from the logical input records")
    encoding_record = logging.LogRecord("binary_logging.encoding", logging.INFO, "bench", 1,
                                        "processed 0 records", (), None)
    encoding_record.created = 1_704_164_645.006
    encoding_record.fields = SHAPES[shape]
    formatter = JSONFormatter() if codec == "json" else logging.Formatter()
    encode_start = time.perf_counter_ns()
    if codec == "json":
        encoded = [(formatter.format(encoding_record) + "\n").encode("utf-8")
                   for _ in range(producers * count)]
    else:
        encoded = [_encode_record(encoding_record, formatter)
                   for _ in range(producers * count)]
    encode_ns = time.perf_counter_ns() - encode_start
    if len(encoded) != len(expected):
        raise AssertionError("encoding-only count mismatch")
    return {
        "codec": codec,
        "shape": shape,
        "producers": producers,
        "records": len(expected),
        "producer_wall_ns": producer_done_ns - started,
        "delivery_drain_close_wall_ns": finished_ns - started,
        "decode_wall_ns": decode_ns,
        "encoding_only_wall_ns": encode_ns,
        "encoded_bytes_total_including_framing": len(sink.data),
        "encoded_bytes_per_record_including_framing": [len(value) for value in sink.records],
        "decoded_record_parity": True,
    }


def _child(cfg: dict[str, Any]) -> int:
    result = _measure(**cfg)
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    return 0


def _fresh(cfg: dict[str, Any], affinity: str | None) -> dict[str, Any]:
    script = (
        "import os,runpy,sys; os.sched_setaffinity(0,{int(sys.argv[1])}); "
        "p=sys.argv[2]; sys.argv=[p,'--_child',sys.argv[3]]; runpy.run_path(p,run_name='__main__')"
    )
    if affinity and hasattr(os, "sched_setaffinity"):
        command = [sys.executable, "-c", script, affinity, __file__, json.dumps(cfg)]
    else:
        command = [sys.executable, __file__, "--_child", json.dumps(cfg)]
    output = subprocess.check_output(command, text=True)
    return json.loads(output.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "--_child":
        return _child(json.loads(raw[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=9, help="alternating paired fresh-process samples")
    parser.add_argument("--items", type=int, default=1000, help="records per producer per sample")
    parser.add_argument("--capacity", type=int, default=256)
    parser.add_argument("--affinity", help="pin fresh children to one CPU, where supported")
    parser.add_argument("--shapes", nargs="+", choices=tuple(SHAPES), default=list(SHAPES))
    parser.add_argument("--producers", nargs="+", type=int, choices=(1, 4), default=[1, 4])
    args = parser.parse_args(raw)
    if args.samples < 1 or args.items < 1:
        parser.error("--samples and --items must be positive")
    try:
        import msgpack
    except ImportError as exc:
        parser.error(f"MessagePack benchmark requires msgpack: {exc}")

    result: dict[str, Any] = {
        "runtime": sys.version,
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "gil_enabled": getattr(sys, "_is_gil_enabled", lambda: True)(),
        "msgpack_version": getattr(msgpack, "__version__", "unknown"),
        "items_per_producer": args.items,
        "capacity": args.capacity,
        "fresh_processes_per_sample": True,
        "samples": args.samples,
        "affinity_cpu": args.affinity,
        "same_code_control": {},
        "fixtures": {},
    }
    for shape in args.shapes:
        for producers in args.producers:
            key = f"{shape}-p{producers}"
            rounds: list[dict[str, Any]] = []
            controls: dict[str, dict[str, list[Any]]] = {
                "json": {"raw_delivery_drain_close_wall_ns": [], "paired_ratios": []},
                "msgpack": {"raw_delivery_drain_close_wall_ns": [], "paired_ratios": []},
            }
            for pair in range(args.samples):
                order = ("json", "msgpack") if pair % 2 == 0 else ("msgpack", "json")
                paired: dict[str, Any] = {}
                for codec in order:
                    cfg = {"codec": codec, "shape": shape, "producers": producers,
                           "count": args.items, "capacity": args.capacity}
                    paired[codec] = _fresh(cfg, args.affinity)
                rounds.append(paired)
                # Same-code A/A observations compare an independently launched
                # repeat with that codec's paired observation for this round.
                for codec in order:
                    cfg = {"codec": codec, "shape": shape, "producers": producers,
                           "count": args.items, "capacity": args.capacity}
                    control_ns = _fresh(cfg, args.affinity)["delivery_drain_close_wall_ns"]
                    controls[codec]["raw_delivery_drain_close_wall_ns"].append(control_ns)
                    controls[codec]["paired_ratios"].append(
                        control_ns / paired[codec]["delivery_drain_close_wall_ns"])
            size_samples = {
                codec: [round_item[codec]["encoded_bytes_per_record_including_framing"]
                        for round_item in rounds]
                for codec in ("json", "msgpack")
            }
            ratios = [round_item["msgpack"]["delivery_drain_close_wall_ns"] /
                      round_item["json"]["delivery_drain_close_wall_ns"] for round_item in rounds]
            result["fixtures"][key] = {
                "alternating_order": ["json/msgpack" if i % 2 == 0 else "msgpack/json" for i in range(args.samples)],
                "raw_samples": rounds,
                "paired_delivery_ratio_msgpack_over_json": ratios,
                "median_paired_delivery_ratio_msgpack_over_json": statistics.median(ratios),
                "encoded_size": {
                    "raw_bytes_per_record_by_pair": size_samples,
                    "median_bytes_per_record": {
                        codec: statistics.median(value for group in groups for value in group)
                        for codec, groups in size_samples.items()
                    },
                    "median_total_ratio_msgpack_over_json": statistics.median(
                        [round_item["msgpack"]["encoded_bytes_total_including_framing"] /
                         round_item["json"]["encoded_bytes_total_including_framing"] for round_item in rounds]),
                },
            }
            result["same_code_control"][key] = {
                codec: {**values,
                        "median_ns": statistics.median(values["raw_delivery_drain_close_wall_ns"]),
                        "median_paired_ratio": statistics.median(values["paired_ratios"]),
                        "within_10_percent": sum(0.9 <= r <= 1.1 for r in values["paired_ratios"]),
                        "stable": (0.95 <= statistics.median(values["paired_ratios"]) <= 1.05
                                   and sum(0.9 <= r <= 1.1 for r in values["paired_ratios"])
                                   >= (7 * args.samples + 8) // 9)}
                for codec, values in controls.items()
            }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
