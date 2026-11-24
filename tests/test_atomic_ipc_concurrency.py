"""Bounded cross-process concurrency tests for the atomic SPSC ring."""

from __future__ import annotations

import multiprocessing
import os
import sys
import time

import pytest

if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
    pytest.skip("atomic IPC requires Linux x86-64", allow_module_level=True)

from picoke.ipc import AtomicSharedByteRing


def _finish(process: multiprocessing.Process) -> None:
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(2)
        pytest.fail("atomic IPC concurrency child did not exit")
    assert process.exitcode == 0


def _stress_producer(name: str, count: int, ready, start, result) -> None:
    ring = AtomicSharedByteRing.attach(name)
    try:
        ready.set()
        if not start.wait(5):
            result.put(("start-timeout", None))
            return
        for sequence in range(count):
            payload = f"{sequence:08d}:".encode() + bytes([sequence % 251]) * (sequence % 97)
            deadline = time.monotonic() + 8
            while not ring.try_push(payload):
                if time.monotonic() >= deadline:
                    result.put(("push-timeout", sequence))
                    return
                time.sleep(0.0005)
        result.put(("ok", count))
    finally:
        ring.close()


def _attached_lifecycle_user(name: str, attached, proceed, result) -> None:
    ring = AtomicSharedByteRing.attach(name)
    try:
        attached.set()
        if not proceed.wait(5):
            result.put(("proceed-timeout", None))
            return
        pushed = ring.try_push(b"still-open")
        popped = ring.try_pop()
        result.put((pushed, popped))
    finally:
        ring.close()


def test_bounded_cross_process_spsc_stress_preserves_fifo_and_payloads() -> None:
    ctx = multiprocessing.get_context("spawn")
    ring = AtomicSharedByteRing.create(64, 128)
    ready, start, result = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=_stress_producer, args=(ring.name, 1200, ready, start, result))
    try:
        process.start()
        assert ready.wait(5), "producer failed to attach"
        start.set()
        received = []
        deadline = time.monotonic() + 12
        while len(received) < 1200 and time.monotonic() < deadline:
            item = ring.try_pop()
            if item is None:
                time.sleep(0.0005)
            else:
                received.append(item)
        _finish(process)
        assert result.get(timeout=2) == ("ok", 1200)
        expected = [f"{i:08d}:".encode() + bytes([i % 251]) * (i % 97) for i in range(1200)]
        assert received == expected
        assert ring.try_pop() is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()
        result.close()
        result.join_thread()


def test_attached_mapping_remains_usable_after_creator_close_and_unlink() -> None:
    ctx = multiprocessing.get_context("spawn")
    ring = AtomicSharedByteRing.create(2, 32)
    attached, proceed, result = ctx.Event(), ctx.Event(), ctx.Queue()
    process = ctx.Process(target=_attached_lifecycle_user, args=(ring.name, attached, proceed, result))
    try:
        process.start()
        assert attached.wait(5), "child failed to attach before unlink"
        ring.close()
        ring.unlink()
        proceed.set()
        _finish(process)
        assert result.get(timeout=2) == (True, b"still-open")
        with pytest.raises((OSError, ValueError, RuntimeError)):
            AtomicSharedByteRing.attach(ring.name)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()
        result.close()
        result.join_thread()
