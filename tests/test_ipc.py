"""Contract tests for the Linux shared-memory byte ring."""

from __future__ import annotations

import multiprocessing
import os
import time

import pytest

from picoke.ipc import SharedByteRing


def _push_in_child(name: str, payloads: list[bytes], result) -> None:
    ring = SharedByteRing.attach(name)
    try:
        for payload in payloads:
            deadline = time.monotonic() + 5
            while not ring.try_push(payload):
                if time.monotonic() >= deadline:
                    result.put("push timed out")
                    return
                time.sleep(0.001)
        result.put("ok")
    finally:
        ring.close()


def _pop_in_child(name: str, count: int, result) -> None:
    ring = SharedByteRing.attach(name)
    try:
        values = []
        deadline = time.monotonic() + 5
        while len(values) < count:
            value = ring.try_pop()
            if value is not None:
                values.append(value)
            elif time.monotonic() >= deadline:
                result.put(("timeout", values))
                return
            else:
                time.sleep(0.001)
        result.put(("ok", values))
    finally:
        ring.close()


def _push_then_exit(name: str) -> None:
    ring = SharedByteRing.attach(name)
    ring.try_push(b"committed")
    os._exit(0)


def _use_inherited_ring(ring, result) -> None:
    try:
        ring.try_push(b"x")
    except RuntimeError as exc:
        result.put(str(exc))
    else:
        result.put("unexpected success")


def _context():
    # Spawn avoids inheriting the creator's mapping and proves attach-by-name.
    return multiprocessing.get_context("spawn")


def _join(process: multiprocessing.Process) -> None:
    process.join(8)
    if process.is_alive():
        process.terminate()
        process.join(2)
        pytest.fail("shared ring child process did not exit")


def test_create_attach_cross_process_fifo_and_empty_payload() -> None:
    ctx = _context()
    ring = SharedByteRing.create(8, 32)
    child = None
    try:
        assert ring.name
        assert ring.capacity == 8
        assert ring.max_item_size == 32
        result = ctx.Queue()
        payloads = [b"first", b"", b"third"]
        child = ctx.Process(target=_push_in_child, args=(ring.name, payloads, result))
        child.start()
        observed = []
        deadline = time.monotonic() + 8
        while len(observed) < len(payloads) and time.monotonic() < deadline:
            item = ring.try_pop()
            if item is not None:
                observed.append(item)
            else:
                time.sleep(0.001)
        _join(child)
        assert result.get(timeout=2) == "ok"
        assert child.exitcode == 0
        assert observed == payloads
        assert ring.try_pop() is None
    finally:
        if child is not None and child.is_alive():
            child.terminate()
            child.join(2)
        ring.close()
        ring.unlink()


def test_independent_process_consumer_receives_fifo() -> None:
    ctx = _context()
    ring = SharedByteRing.create(4, 16)
    process = None
    try:
        payloads = [b"a", b"bb", b"ccc", b"dddd", b"e"]
        result = ctx.Queue()
        process = ctx.Process(target=_pop_in_child, args=(ring.name, len(payloads), result))
        process.start()
        for payload in payloads:
            deadline = time.monotonic() + 5
            while not ring.try_push(payload):
                assert time.monotonic() < deadline, "consumer failed to free ring space"
                time.sleep(0.001)
        _join(process)
        status, values = result.get(timeout=2)
        assert status == "ok"
        assert values == payloads
        assert process.exitcode == 0
    finally:
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()


def test_empty_full_wraparound_and_empty_bytes() -> None:
    ring = SharedByteRing.create(2, 4)
    try:
        assert ring.try_pop() is None
        assert ring.try_push(b"") is True
        assert ring.try_push(b"two") is True
        assert ring.try_push(b"x") is False
        assert ring.try_pop() == b""
        assert ring.try_pop() == b"two"
        assert ring.try_pop() is None
        for payload in (b"a", b"b", b"c", b"d", b"e"):
            assert ring.try_push(payload)
            assert ring.try_pop() == payload
    finally:
        ring.close()
        ring.unlink()


def test_corrupt_slot_length_is_rejected() -> None:
    import struct

    ring = SharedByteRing.create(1, 4)
    try:
        assert ring.try_push(b"data")
        with open(f"/dev/shm/{ring.name}", "r+b", buffering=0) as segment:
            segment.seek(48)
            segment.write(struct.pack("<Q", 5))
        with pytest.raises(RuntimeError, match="corrupt SharedByteRing slot"):
            ring.try_pop()
    finally:
        ring.close()
        ring.unlink()


def test_corrupt_header_is_rejected_before_access() -> None:
    ring = SharedByteRing.create(1, 4)
    try:
        with open(f"/dev/shm/{ring.name}", "r+b", buffering=0) as segment:
            segment.write(b"BADMAGIC")
        with pytest.raises(ValueError, match="invalid SharedByteRing segment"):
            ring.try_push(b"x")
        with pytest.raises(ValueError, match="invalid SharedByteRing segment"):
            ring.try_pop()
    finally:
        ring.close()
        ring.unlink()


class DerivedBytes(bytes):
    pass


@pytest.mark.parametrize("value", [bytearray(b"x"), memoryview(b"x"), "x", None, DerivedBytes(b"x")])
def test_push_requires_exact_bytes(value: object) -> None:
    ring = SharedByteRing.create(1, 4)
    try:
        with pytest.raises(TypeError):
            ring.try_push(value)
    finally:
        ring.close()
        ring.unlink()




def test_oversize_rejected_even_when_full_and_attach_validation() -> None:
    ring = SharedByteRing.create(1, 2)
    attached = None
    try:
        attached = SharedByteRing.attach(ring.name)
        assert attached.capacity == 1
        assert attached.max_item_size == 2
        assert ring.try_push(b"ok")
        with pytest.raises(ValueError):
            ring.try_push(b"toolong")
        assert ring.try_push(b"x") is False
    finally:
        if attached is not None:
            attached.close()
        ring.close()
        ring.unlink()


def test_close_detaches_and_unlink_removes_name() -> None:
    ring = SharedByteRing.create(2, 4)
    name = ring.name
    attached = SharedByteRing.attach(name)
    ring.close()
    attached.close()
    ring.unlink()
    with pytest.raises((FileNotFoundError, OSError, ValueError)):
        SharedByteRing.attach(name)


def test_closed_ring_operations_fail_and_close_is_idempotent() -> None:
    ring = SharedByteRing.create(1, 4)
    ring.close()
    ring.close()
    with pytest.raises((RuntimeError, ValueError)):
        ring.try_push(b"x")
    with pytest.raises((RuntimeError, ValueError)):
        ring.try_pop()
    ring.unlink()


def test_forked_process_must_attach_a_new_handle() -> None:
    ctx = multiprocessing.get_context("fork")
    ring = SharedByteRing.create(1, 4)
    results = ctx.Queue()
    process = ctx.Process(target=_use_inherited_ring, args=(ring, results))
    try:
        process.start()
        _join(process)
        assert process.exitcode == 0
        assert "after fork" in results.get(timeout=2)
        assert ring.try_pop() is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        results.close()
        ring.close()
        ring.unlink()


def test_create_cleans_up_when_initial_lock_fails(monkeypatch) -> None:
    import picoke.ipc as ipc

    resources = []

    def fail_lock(fd, operation):
        resources.append((fd, os.readlink(f"/proc/self/fd/{fd}")))
        raise OSError("lock failed")

    monkeypatch.setattr(ipc.fcntl, "flock", fail_lock)
    with pytest.raises(OSError, match="lock failed"):
        SharedByteRing.create(1, 4)
    fd, path = resources[0]
    with pytest.raises(OSError):
        os.fstat(fd)
    assert not os.path.exists(path)


def test_abrupt_attached_process_exit_does_not_destroy_committed_item() -> None:
    ctx = _context()
    ring = SharedByteRing.create(2, 16)
    process = ctx.Process(target=_push_then_exit, args=(ring.name,))
    try:
        process.start()
        _join(process)
        assert process.exitcode == 0
        assert ring.try_pop() == b"committed"
        assert ring.try_pop() is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()
