#!/usr/bin/env python3
"""Same-contract structured JSON logging formatter benchmark.

Run with the installed picoke environment, e.g. ``python -I bench/structured_logging.py``.
The reference deliberately uses only the standard library and mirrors the
formatter's frozen schema; timing output is JSON for reproducibility.
"""
from __future__ import annotations

import argparse
import datetime as _datetime
import importlib
import io
import json
import logging
import subprocess
import statistics
import sys
import time
from collections.abc import Mapping
from typing import Any, Callable


def _clean(value: Any, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (Mapping, list)):
        ident = id(value)
        if ident in active:
            raise ValueError("circular reference")
        active.add(ident)
        try:
            if isinstance(value, Mapping):
                result = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("mapping keys must be strings")
                    result[key] = _clean(item, active)
                return result
            return [_clean(item, active) for item in value]
        finally:
            active.remove(ident)
    raise TypeError(f"unsupported field type: {type(value).__name__}")


class ReferenceFormatter(logging.Formatter):
    """Strict-JSON stdlib reference implementation of the frozen contract."""

    def format(self, record: logging.LogRecord) -> str:
        created = _datetime.datetime.fromtimestamp(record.created, _datetime.UTC)
        timestamp = created.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        result: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            result["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            result["stack"] = self.formatStack(record.stack_info)
        if hasattr(record, "fields"):
            if not isinstance(record.fields, Mapping):
                raise TypeError("fields must be a mapping")
            result["fields"] = _clean(record.fields, set())
        return json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":"))



def _record(index: int = 0, *, fields: Any = ..., exc: bool = False, stack: bool = False) -> logging.LogRecord:
    record = logging.LogRecord("fixture.worker", logging.INFO, "fixture.py", 42,
                               "processed %s items", (32 + index,), None)
    record.created = 1_700_000_000.125 + index / 1000
    if fields is not ...:
        record.fields = fields
    if exc:
        try:
            raise ValueError("fixture failure 雪")
        except ValueError:
            record.exc_info = sys.exc_info()
    if stack:
        record.stack_info = "Stack (most recent call last):\n  File \"fixture.py\", line 42, in run"
    return record


def _bytes_formatter(formatter: Any, record: logging.LogRecord) -> bytes:
    return formatter.format(record).encode("utf-8")


_CURRENT_CREATED = 1_700_000_000.125


def _fixed_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = logging.LogRecord(*args, **kwargs)
    record.created = _CURRENT_CREATED
    return record


class _ByteSink(io.TextIOBase):
    def __init__(self) -> None:
        self.buffer = io.BytesIO()
    def write(self, text: str) -> int:
        self.buffer.write(text.encode("utf-8"))
        return len(text)
    def flush(self) -> None:
        pass
    def reset(self) -> None:
        self.buffer.seek(0)
        self.buffer.truncate(0)
    def getvalue(self) -> bytes:
        return self.buffer.getvalue()


def _handler_arm(formatter: Any, record: logging.LogRecord) -> tuple[Callable[[], None], _ByteSink]:
    sink = _ByteSink()
    handler = logging.StreamHandler(sink)
    handler.setFormatter(formatter)
    logger = logging.Logger("fixture.worker", logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    extra = {"fields": record.fields} if hasattr(record, "fields") else None

    def emit() -> None:
        global _CURRENT_CREATED
        _CURRENT_CREATED = record.created
        sink.reset()
        logger.info(record.msg, *record.args, extra=extra,
                    exc_info=record.exc_info, stack_info=bool(record.stack_info))
    return emit, sink


def _invalid_cases(formatter: Any) -> None:
    cases = [([1], TypeError), ({1: "x"}, TypeError), ({"nested": {1: "x"}}, TypeError),
             ({"nested": (1, 2)}, TypeError), (object(), TypeError),
             ({"value": float("nan")}, ValueError), ({"value": float("inf")}, ValueError),
             (None, TypeError)]
    for value, expected in cases:
        try:
            formatter.format(_record(fields=value))
        except expected:
            continue
        raise AssertionError(f"invalid fields {value!r} did not raise {expected.__name__}")
    circular: list[Any] = []
    circular.append(circular)
    try:
        formatter.format(_record(fields={"nested": circular}))
    except ValueError:
        pass
    else:
        raise AssertionError("circular fields did not raise ValueError")
    shared = {"ok": [1, True, None]}
    formatter.format(_record(fields={"left": shared, "right": shared}))


def _median(values: list[float]) -> float:
    return statistics.median(values)


def _run_pair(left: Callable[[], Any], right: Callable[[], Any], calls: int,
              samples: int, blocks: int) -> dict[str, Any]:
    for _ in range(20):
        left(); right()
    ltimes: list[float] = []
    rtimes: list[float] = []
    ratios: list[float] = []
    for sample in range(samples):
        ls = rs = 0.0
        for block in range(blocks):
            order = ((0, left), (1, right)) if (sample + block) % 2 == 0 else ((1, right), (0, left))
            elapsed = [0.0, 0.0]
            for arm, fn in order:
                start = time.thread_time_ns()
                for _ in range(calls):
                    fn()
                elapsed[arm] = (time.thread_time_ns() - start) / calls
            ls += elapsed[0]
            rs += elapsed[1]
        ltimes.append(ls / blocks)
        rtimes.append(rs / blocks)
        ratios.append(rtimes[-1] / ltimes[-1])
    return {"left_ns_per_call": ltimes, "right_ns_per_call": rtimes,
            "right_over_left_ratios": ratios,
            "median_left_ns": _median(ltimes), "median_right_ns": _median(rtimes),
            "median_ratio": _median(ratios)}


def _load_candidate(module_name: str, class_name: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, class_name)()


def _cold_child(module_name: str, formatter_name: str) -> int:
    record = _record(fields={"context": {"nested": [1, "雪", True]}})
    start = time.perf_counter_ns()
    output = _load_candidate(module_name, formatter_name).format(record)
    elapsed = time.perf_counter_ns() - start
    assert output == ReferenceFormatter().format(record)
    print(elapsed)
    return 0


def _cold_comparison(module: str, formatter: str, samples: int) -> dict[str, Any]:
    elapsed = []
    for _ in range(samples):
        completed = subprocess.run(
            [sys.executable, "-I", "-B", __file__, "--_cold-child", module, formatter],
            check=True, capture_output=True, text=True,
        )
        elapsed.append(int(completed.stdout))
    return {"scope": "fresh process with stdlib benchmark dependencies loaded; "
                     "picoke import, construction, first format",
            "ns": elapsed, "median_ns": _median(elapsed)}


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--_cold-child":
        return _cold_child(*arguments[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", default="picoke.logging")
    parser.add_argument("--formatter", default="JSONFormatter")
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--blocks", type=int, default=8)
    parser.add_argument("--calls", type=int, default=100)
    args = parser.parse_args(arguments)

    reference = ReferenceFormatter()
    candidate = _load_candidate(args.module, args.formatter)
    records = [_record(), _record(1, fields={}),
               _record(2, fields={"nested": {"values": [3, "雪 café", None]}}),
               _record(3, exc=True), _record(4, stack=True)]
    original_factory = logging.getLogRecordFactory()
    logging.setLogRecordFactory(_fixed_factory)
    parity = []
    handler_arms = []
    try:
        for record in records:
            expected = _bytes_formatter(reference, record)
            actual = _bytes_formatter(candidate, record)
            if actual != expected:
                raise AssertionError(f"formatter byte mismatch: expected={expected!r}, actual={actual!r}")
            ref_emit, ref_sink = _handler_arm(reference, record)
            candidate_emit, candidate_sink = _handler_arm(candidate, record)
            for emit in (ref_emit, candidate_emit):
                emit()
            expected_sink = ref_sink.getvalue()
            actual_sink = candidate_sink.getvalue()
            if actual_sink != expected_sink:
                raise AssertionError("enabled handler byte mismatch")
            parity.append({"formatter_bytes": actual.decode("ascii"),
                           "handler_bytes": actual_sink.decode("ascii")})
            handler_arms.append((ref_emit, candidate_emit))
        _invalid_cases(reference)
        _invalid_cases(candidate)

        cold = _cold_comparison(args.module, args.formatter, args.samples)
        result: dict[str, Any] = {
            "runtime": sys.version, "module": args.module, "formatter": args.formatter,
            "fixture_count": len(records), "parity": parity, "cold_setup": cold,
        }
        fixtures = {}
        for i, record in enumerate(records):
            def ref_format(r: logging.LogRecord = record) -> str:
                return reference.format(r)
            def candidate_format(r: logging.LogRecord = record) -> str:
                return candidate.format(r)
            ref_emit, candidate_emit = handler_arms[i]
            fixtures[f"format-{i}"] = {
                "same_code_calibration": _run_pair(ref_format, ref_format, args.calls, args.samples, args.blocks),
                "formatter": _run_pair(ref_format, candidate_format, args.calls, args.samples, args.blocks),
                "same_code_handler_calibration": _run_pair(ref_emit, ref_emit, args.calls, args.samples, args.blocks),
                "enabled_handler": _run_pair(ref_emit, candidate_emit, args.calls, args.samples, args.blocks),
            }

        def disabled_arm(formatter: Any) -> Callable[[], None]:
            sink = _ByteSink()
            handler = logging.StreamHandler(sink)
            handler.setFormatter(formatter)
            logger = logging.Logger("structured_logging.disabled", logging.WARNING)
            logger.propagate = False
            logger.addHandler(handler)
            return lambda: logger.info("disabled %s", 32)

        disabled_reference = disabled_arm(reference)
        disabled_candidate = disabled_arm(candidate)
        result["disabled"] = {
            "same_code_calibration": _run_pair(disabled_reference, disabled_reference,
                                               args.calls, args.samples, args.blocks),
            "reference_vs_candidate": _run_pair(disabled_reference, disabled_candidate,
                                                args.calls, args.samples, args.blocks),
        }
        result["fixtures"] = fixtures
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    finally:
        logging.setLogRecordFactory(original_factory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


