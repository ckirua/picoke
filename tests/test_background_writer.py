"""Contract tests for the bounded asynchronous binary writer."""

from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import threading
from collections import Counter

import pytest

from picoke.logging import BackgroundJSONHandler, BackgroundWriter, JSONFormatter


class RecordingStream:
    def __init__(self, *, gate: threading.Event | None = None, result=None):
        self.data = []
        self.calls = []
        self.flushes = 0
        self.gate = gate
        self.result = result
        self.entered = threading.Event()

    def write(self, data):
        self.calls.append(data)
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(3)
        if callable(self.result):
            return self.result(data)
        if self.result is not None:
            return self.result
        self.data.append(bytes(data))
        return len(data)

    def flush(self):
        self.flushes += 1


def join(thread: threading.Thread) -> None:
    thread.join(3)
    assert not thread.is_alive(), f"thread {thread.name!r} did not finish"


def test_exact_bytes_validation_and_constructor_bounds():
    for value in (True, 0, -1, 1.0, "2"):
        with pytest.raises((TypeError, ValueError, OverflowError)):
            BackgroundWriter(RecordingStream(), capacity=value)
    for value in (bytearray(b"x"), memoryview(b"x"), "x", None, type("B", (bytes,), {})(b"x")):
        writer = BackgroundWriter(RecordingStream(), capacity=1)
        try:
            with pytest.raises(TypeError):
                writer.write(value)
        finally:
            writer.close()
    writer = BackgroundWriter(RecordingStream(), capacity=1, max_item_size=1)
    try:
        with pytest.raises(TypeError):
            writer.write(b"", block=1)
        with pytest.raises(ValueError):
            writer.write(b"xx")
        assert writer.write(b"") is True
    finally:
        writer.close()
    with pytest.raises(ValueError):
        BackgroundWriter(RecordingStream(), capacity=0)


def test_nonblocking_full_and_blocked_writer_wakes_when_space_available():
    gate = threading.Event()
    stream = RecordingStream(gate=gate)
    writer = BackgroundWriter(stream, capacity=1)
    first, second, third = b"first", b"second", b"third"
    try:
        assert writer.write(first)
        assert stream.entered.wait(2)
        assert writer.write(second)
        assert writer.write(third, block=False) is False
        outcome = []
        producer = threading.Thread(target=lambda: outcome.append(writer.write(third)))
        producer.start()
        gate.set()
        join(producer)
        assert outcome == [True]
    finally:
        gate.set()
        writer.close()
    assert b"".join(stream.data) == first + second + third


def test_capacity_counts_queued_item_separate_from_in_flight():
    gate = threading.Event()
    stream = RecordingStream(gate=gate)
    writer = BackgroundWriter(stream, capacity=1)
    try:
        assert writer.write(b"a")
        assert stream.entered.wait(2)
        assert writer.write(b"b")
        assert writer.write(b"c", block=False) is False
        gate.set()
        writer.flush()
    finally:
        gate.set()
        writer.close()
    assert b"".join(stream.data) == b"ab"


def test_flush_waits_for_inflight_and_queued_items_and_concurrent_flushes():
    gate = threading.Event()
    stream = RecordingStream(gate=gate)
    writer = BackgroundWriter(stream, capacity=4)
    assert writer.write(b"before")
    assert stream.entered.wait(2)
    flush_results = []
    flushers = [threading.Thread(target=lambda: (writer.flush(), flush_results.append(True))) for _ in range(3)]
    for thread in flushers:
        thread.start()
    # A flush request must not depend on subsequently admitted items.
    assert writer.write(b"after")
    gate.set()
    for thread in flushers:
        join(thread)
    writer.close()
    assert flush_results == [True] * 3
    assert b"".join(stream.data) == b"beforeafter"
    assert stream.flushes >= 1


def test_close_is_idempotent_drains_and_does_not_close_stream():
    stream = io.BytesIO()
    writer = BackgroundWriter(stream)
    assert writer.write(b"payload")
    writer.close()
    writer.close()
    assert stream.getvalue() == b"payload"
    assert not stream.closed
    with pytest.raises(ValueError):
        writer.write(b"later")
    with pytest.raises(ValueError):
        writer.flush()


def test_multiple_close_callers_join_and_close_write_race():
    gate = threading.Event()
    stream = RecordingStream(gate=gate)
    writer = BackgroundWriter(stream, capacity=2)
    assert writer.write(b"x")
    assert stream.entered.wait(2)
    outcomes = []
    closers = [threading.Thread(target=lambda: (writer.close(), outcomes.append("closed"))) for _ in range(2)]
    for thread in closers:
        thread.start()
    producer_result = []

    def race_write():
        try:
            producer_result.append(writer.write(b"y"))
        except ValueError:
            producer_result.append(False)

    producer = threading.Thread(target=race_write)
    producer.start()
    gate.set()
    for thread in closers + [producer]:
        join(thread)
    assert outcomes == ["closed", "closed"]
    assert producer_result in ([True], [False])
    assert b"".join(stream.data) in (b"x", b"xy")


@pytest.mark.parametrize("count", [1, 4, 8])
def test_concurrent_producers_fifo_per_producer_and_exactly_once(count):
    stream = RecordingStream()
    writer = BackgroundWriter(stream, capacity=7)
    start = threading.Barrier(count + 1)
    errors = []

    def produce(producer):
        try:
            start.wait(3)
            for sequence in range(80):
                payload = f"{producer}:{sequence};".encode()
                writer.write(payload)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=produce, args=(producer,)) for producer in range(count)]
    for thread in threads:
        thread.start()
    start.wait(3)
    for thread in threads:
        join(thread)
    writer.close()
    assert errors == []
    observed = b"".join(stream.data).decode().split(";")[:-1]
    assert len(observed) == count * 80
    assert Counter(observed) == Counter(f"{p}:{n}" for p in range(count) for n in range(80))
    for producer in range(count):
        assert [int(item.split(":")[1]) for item in observed if item.startswith(f"{producer}:")] == list(range(80))


def test_short_writes_use_original_bytes_first_then_buffer_view():
    seen = []
    output = bytearray()

    def short(data):
        seen.append(data)
        n = min(2, len(data))
        output.extend(data[:n])
        return n

    stream = RecordingStream(result=short)
    writer = BackgroundWriter(stream)
    payload = b"abcdef"
    assert writer.write(payload)
    writer.close()
    assert bytes(output) == payload
    assert seen[0] is payload
    assert all(isinstance(item, memoryview) for item in seen[1:])


@pytest.mark.parametrize("result", [0, -1, 100, True, 1.0, None])
def test_invalid_sink_write_result_fails_writer(result):
    stream = RecordingStream(result=lambda _data: result)
    writer = BackgroundWriter(stream)
    assert writer.write(b"x")
    with pytest.raises(RuntimeError, match="background writer failed") as error:
        writer.flush()
    assert isinstance(error.value.__cause__, OSError)
    with pytest.raises(RuntimeError, match="background writer failed") as close_error:
        writer.close()
    assert isinstance(close_error.value.__cause__, OSError)


@pytest.mark.parametrize("method", ["write", "flush"])
def test_sink_exception_is_chained_and_poisons_operations(method):
    class Failing:
        def write(self, data):
            raise KeyboardInterrupt("sink broke")

        def flush(self):
            raise LookupError("flush broke")

    stream = Failing()
    writer = BackgroundWriter(stream)
    if method == "write":
        writer.write(b"x")
        expected = KeyboardInterrupt
    else:
        expected = LookupError
    with pytest.raises(RuntimeError, match="background writer failed") as error:
        writer.flush()
    assert isinstance(error.value.__cause__, expected)
    with pytest.raises(RuntimeError, match="background writer failed"):
        writer.close()


def test_blocked_producer_wakes_with_worker_failure():
    entered = threading.Event()
    release = threading.Event()

    class Failing:
        def write(self, data):
            entered.set()
            assert release.wait(3)
            raise OSError("failed")

        def flush(self):
            pass

    writer = BackgroundWriter(Failing(), capacity=1)
    assert writer.write(b"one")
    assert entered.wait(2)
    assert writer.write(b"two")
    result = []
    producer = threading.Thread(target=lambda: result.append(pytest.raises(RuntimeError, writer.write, b"three")))
    producer.start()
    release.set()
    join(producer)
    assert result
    with pytest.raises(RuntimeError, match="background writer failed"):
        writer.close()


def test_payload_identity_empty_item_and_release_after_drain():
    stream = RecordingStream()
    writer = BackgroundWriter(stream)
    payload = bytes(bytearray(b"unique payload"))
    reference = payload
    assert writer.write(payload)
    assert writer.write(b"")
    writer.close()
    assert stream.calls[0] is reference
    assert stream.data == [payload]


def test_handler_emits_exact_utf8_json_line_flush_and_preserves_stream_ownership():
    stream = io.BytesIO()
    writer = BackgroundWriter(stream)
    handler = BackgroundJSONHandler(writer)
    logger = logging.getLogger(f"background-test-{id(handler)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info("event", extra={"fields": {"city": "雪"}})
        handler.flush()
        lines = stream.getvalue().splitlines(keepends=True)
        assert len(lines) == 1 and lines[0].endswith(b"\n")
        event = json.loads(lines[0])
        assert event["message"] == "event"
        assert event["fields"] == {"city": "雪"}
        assert lines[0].decode("utf-8").encode("utf-8") == lines[0]
    finally:
        logger.removeHandler(handler)
        handler.close()
        writer.close()
    assert not stream.closed


def test_handler_close_does_not_flush_or_close_writer():
    stream = RecordingStream()
    writer = BackgroundWriter(stream)
    handler = BackgroundJSONHandler(writer)
    handler.close()
    assert stream.flushes == 0
    assert writer.write(b"still open")
    writer.close()
    assert stream.flushes >= 1
    assert b"".join(stream.data) == b"still open"


def test_handler_formats_once_supports_custom_formatter_and_routes_emit_error():
    class CountingFormatter(logging.Formatter):
        calls = 0

        def format(self, record):
            self.calls += 1
            return "custom 雪"

    stream = io.BytesIO()
    writer = BackgroundWriter(stream)
    handler = BackgroundJSONHandler(writer)
    formatter = CountingFormatter()
    handler.setFormatter(formatter)
    handler.emit(logging.LogRecord("x", logging.INFO, "", 1, "msg", (), None))
    handler.flush()
    assert formatter.calls == 1
    assert stream.getvalue() == "custom 雪\n".encode()
    handler.close()
    writer.close()

    class BadWriter:
        def write(self, data, *, block=True):
            raise OSError("broken")

    bad_handler = BackgroundJSONHandler(BadWriter())
    record = logging.LogRecord("x", logging.INFO, "", 1, "msg", (), None)
    errors = []
    bad_handler.handleError = lambda rec: errors.append(rec)
    bad_handler.emit(record)
    assert errors == [record]
    bad_handler.close()


def test_default_handler_formatter_is_json_formatter():
    writer = BackgroundWriter(io.BytesIO())
    handler = BackgroundJSONHandler(writer)
    try:
        assert isinstance(handler.formatter, JSONFormatter)
    finally:
        writer.close()
        handler.close()


def test_worker_self_calls_are_rejected_before_deadlock():
    responses = []

    class Reentrant:
        def write(self, data):
            for operation in (lambda: writer.write(b"nested"), writer.flush, writer.close):
                try:
                    operation()
                except RuntimeError as error:
                    responses.append(str(error))
            return len(data)

        def flush(self):
            pass

    writer = BackgroundWriter(Reentrant())
    assert writer.write(b"outer")
    writer.close()
    assert responses == ["background writer cannot be called from its worker"] * 3


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
def test_forked_writer_rejected_in_child_before_inherited_lock(tmp_path):
    # A subprocess deadline bounds a regression that tries to acquire an inherited lock.
    code = '''import io, os
from picoke.logging import BackgroundWriter
w = BackgroundWriter(io.BytesIO())
pid = os.fork()
if pid == 0:
    for operation in (lambda: w.write(b"x"), lambda: w.write(bytearray(b"x")), w.flush, w.close):
        try:
            operation()
        except RuntimeError as e:
            if str(e) != "background writer cannot be used after fork":
                os._exit(2)
        except BaseException:
            os._exit(3)
        else:
            os._exit(4)
    os._exit(0)
_, status = os.waitpid(pid, 0)
w.close()
os._exit(os.waitstatus_to_exitcode(status))
'''
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=5)
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


def test_context_manager_flushes_and_closes_writer():
    stream = io.BytesIO()
    with BackgroundWriter(stream) as writer:
        writer.write(b"context")
    assert stream.getvalue() == b"context"
    assert not stream.closed
