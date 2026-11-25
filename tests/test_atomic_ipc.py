"""Contract tests for the atomic Linux shared-memory byte ring."""

from __future__ import annotations

import multiprocessing
import os
import sys
import time

import pytest

if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
    pytest.skip("atomic IPC requires Linux x86-64", allow_module_level=True)

from picoke.ipc import AtomicSharedByteRing


def _join(process: multiprocessing.Process) -> None:
    process.join(8)
    if process.is_alive():
        process.terminate()
        process.join(2)
        pytest.fail("atomic ring child process did not exit")


def _push_process(name: str, payloads: list[bytes], result) -> None:
    ring = AtomicSharedByteRing.attach(name)
    try:
        for payload in payloads:
            deadline = time.monotonic() + 6
            while not ring.try_push(payload):
                if time.monotonic() >= deadline:
                    result.put(("timeout", None))
                    return
                time.sleep(0.001)
        result.put(("ok", len(payloads)))
    finally:
        ring.close()


def _pop_process(name: str, count: int, result) -> None:
    ring = AtomicSharedByteRing.attach(name)
    try:
        values = []
        deadline = time.monotonic() + 6
        while len(values) < count and time.monotonic() < deadline:
            item = ring.try_pop()
            if item is not None:
                values.append(item)
            else:
                time.sleep(0.001)
        result.put(("ok" if len(values) == count else "timeout", values))
    finally:
        ring.close()


def _use_inherited(ring, result) -> None:
    try:
        ring.try_pop()
    except BaseException as exc:
        result.put((type(exc).__name__, str(exc)))
    else:
        result.put(("success", ""))

def _publish_and_exit(name: str) -> None:
    ring = AtomicSharedByteRing.attach(name)
    if not ring.try_push(b"published"):
        os._exit(2)
    os._exit(0)


def test_published_item_survives_producer_exit() -> None:
    ctx = multiprocessing.get_context("spawn")
    ring = AtomicSharedByteRing.create(2, 16)
    process = ctx.Process(target=_publish_and_exit, args=(ring.name,))
    try:
        process.start()
        _join(process)
        assert process.exitcode == 0
        assert ring.try_pop() == b"published"
        assert ring.try_pop() is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()


def test_empty_full_and_wraparound() -> None:
    ring = AtomicSharedByteRing.create(2, 8)
    try:
        assert ring.try_pop() is None
        assert ring.try_push(b"") is True
        assert ring.try_push(b"12345678") is True
        assert ring.try_push(b"full") is False
        assert ring.try_pop() == b""
        assert ring.try_pop() == b"12345678"
        assert ring.try_pop() is None
        for payload in (b"a", b"bc", b"def", b"ghij", b"k", b""):
            assert ring.try_push(payload) is True
            assert ring.try_pop() == payload
    finally:
        ring.close()
        ring.unlink()


def test_attach_and_cross_process_fifo_integrity() -> None:
    ctx = multiprocessing.get_context("spawn")
    ring = AtomicSharedByteRing.create(8, 64)
    process = None
    try:
        attached = AtomicSharedByteRing.attach(ring.name)
        try:
            payloads = [b""] + [bytes([i]) * (i + 1) for i in range(1, 20)]
            result = ctx.Queue()
            process = ctx.Process(target=_push_process, args=(ring.name, payloads, result))
            process.start()
            received = []
            deadline = time.monotonic() + 8
            while len(received) < len(payloads) and time.monotonic() < deadline:
                item = attached.try_pop()
                if item is None:
                    time.sleep(0.001)
                else:
                    received.append(item)
            _join(process)
            assert result.get(timeout=2) == ("ok", len(payloads))
            assert received == payloads
            assert attached.try_pop() is None
        finally:
            attached.close()
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()


def test_independent_process_consumer_receives_fifo() -> None:
    ctx = multiprocessing.get_context("spawn")
    ring = AtomicSharedByteRing.create(4, 32)
    process = None
    try:
        payloads = [b"one", b"two", b"three", b"four", b"five"]
        result = ctx.Queue()
        process = ctx.Process(target=_pop_process, args=(ring.name, len(payloads), result))
        process.start()
        for payload in payloads:
            deadline = time.monotonic() + 6
            while not ring.try_push(payload):
                assert time.monotonic() < deadline, "consumer failed to free ring space"
                time.sleep(0.001)
        _join(process)
        status, values = result.get(timeout=2)
        assert status == "ok"
        assert values == payloads
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()


def test_lifecycle_and_context_manager() -> None:
    with AtomicSharedByteRing.create(2, 8) as ring:
        name = ring.name
        assert ring.try_push(b"alive")
        assert ring.try_pop() == b"alive"
    with pytest.raises(ValueError):
        ring.__enter__()
    ring.unlink()
    with pytest.raises((OSError, ValueError, RuntimeError)):
        AtomicSharedByteRing.attach(name)


def test_close_and_unlink_are_safe_to_repeat() -> None:
    ring = AtomicSharedByteRing.create(1, 4)
    ring.close()
    ring.close()
    ring.unlink()
    ring.unlink()
    with pytest.raises((RuntimeError, ValueError, OSError)):
        ring.try_push(b"x")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork required")
def test_inherited_handle_is_rejected_after_fork() -> None:
    ctx = multiprocessing.get_context("fork")
    ring = AtomicSharedByteRing.create(2, 8)
    result = ctx.Queue()
    process = ctx.Process(target=_use_inherited, args=(ring, result))
    try:
        process.start()
        _join(process)
        assert process.exitcode == 0
        kind, message = result.get(timeout=2)
        assert kind == "RuntimeError"
        assert "fork" in message.lower()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        result.close()
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("size", [0, 64, 4096])
def test_incomplete_shared_format_is_rejected(size: int) -> None:
    """A valid name with a truncated or invalid header must not attach."""
    import uuid

    name = "/picoke-atomic-" + uuid.uuid4().hex
    path = "/dev/shm" + name
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        os.ftruncate(fd, size)
        with pytest.raises(ValueError):
            AtomicSharedByteRing.attach(name)
    finally:
        os.close(fd)
        os.unlink(path)
