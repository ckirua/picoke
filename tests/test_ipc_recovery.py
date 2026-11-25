"""Deterministic crash/recovery tests for recoverable atomic rings."""
from __future__ import annotations

import multiprocessing
import os
import select
import sys

import pytest

if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
    pytest.skip("recoverable IPC requires Linux x86-64", allow_module_level=True)

from picoke import _atomic_ipc_native
from picoke.ipc import AutoRecoveringAtomicSharedByteRing, RecoverableAtomicSharedByteRing, RecoveryRequiredError, RoleBusyError

_TEST_HOOKS = getattr(_atomic_ipc_native, "TEST_HOOKS", False)
CLASSES = (RecoverableAtomicSharedByteRing, AutoRecoveringAtomicSharedByteRing)

def _failpoint_child(cls_name, name, role, point, connection, payload):
    from picoke.ipc import AutoRecoveringAtomicSharedByteRing, RecoverableAtomicSharedByteRing
    cls = {c.__name__: c for c in (RecoverableAtomicSharedByteRing, AutoRecoveringAtomicSharedByteRing)}[cls_name]
    ring = cls.attach(name)
    if point.endswith("_dirty"):
        ring.set_failpoint(point, connection.fileno())
        ring.claim(role)
    else:
        ring.claim(role)
        ring.set_failpoint(point, connection.fileno())
    ring.try_push(payload) if role == "producer" else ring.try_pop()
    os._exit(3)


def _lock_child(name, ready, stop):
    ring = RecoverableAtomicSharedByteRing.attach(name)
    ring.claim("producer")
    ready.set()
    stop.wait(10)
    ring.release("producer")
    ring.close()


def _stop(process):
    process.join(8)
    if process.is_alive():
        process.kill()
        process.join(2)
        pytest.fail("child did not exit within bound")


def _crash_at(cls, ring, role, point, payload):
    ctx = multiprocessing.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_failpoint_child, args=(cls.__name__, ring.name, role, point, writer, payload))
    try:
        process.start()
        writer.close()
        assert reader.poll(8), f"child did not reach native failpoint {point}"
        assert os.read(reader.fileno(), 128).decode("ascii").strip() == point
        process.kill()
        _stop(process)
        assert process.exitcode == -9
    finally:
        if process.is_alive():
            process.kill()
            process.join(2)
        reader.close()
        writer.close()

def _claim_after_death(cls, ring, role):
    if cls is RecoverableAtomicSharedByteRing:
        with pytest.raises(RecoveryRequiredError):
            ring.claim(role)
        ring.recover(role)
    else:
        ring.claim(role)


def _push_clean(ring, payload):
    ring.claim("producer")
    assert ring.try_push(payload)
    ring.release("producer")


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("capacity", (1, 2))
@pytest.mark.parametrize("payload", (b"", b"x" * 32))
def test_payload_bounds_and_wrapped_sequences(cls, capacity, payload):
    ring = cls.create(capacity, 32)
    try:
        ring.claim("producer")
        ring.claim("consumer")
        for _ in range(5):
            assert ring.try_push(payload)
            assert ring.try_pop() == payload
        assert ring.try_pop() is None
        ring.release("producer")
        ring.release("consumer")
    finally:
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("capacity", (1, 2))
def test_partial_producer_copy_is_discarded(cls, capacity):
    if not _TEST_HOOKS:
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    ring = cls.create(capacity, 32)
    try:
        _crash_at(cls, ring, "producer", "producer_after_partial_payload", b"p" * 32)
        _claim_after_death(cls, ring, "producer")
        ring.claim("consumer")
        assert ring.try_pop() is None
        assert ring.try_push(b"replacement")
        assert ring.try_pop() == b"replacement"
    finally:
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("capacity", (1, 2))
def test_published_producer_item_survives_crash(cls, capacity):
    if not _TEST_HOOKS:
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    ring = cls.create(capacity, 32)
    try:
        if capacity > 1:
            _push_clean(ring, b"first")
        _crash_at(cls, ring, "producer", "after_write_seq", b"committed")
        _claim_after_death(cls, ring, "producer")
        ring.claim("consumer")
        if capacity > 1:
            assert ring.try_pop() == b"first"
        assert ring.try_pop() == b"committed"
        assert ring.try_pop() is None
    finally:
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize("capacity", (1, 2))
def test_consumer_store_boundaries(cls, capacity):
    if not _TEST_HOOKS:
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    for point, expected in (("consumer_after_partial_payload", b"original"), ("before_read_seq", b"original"), ("after_read_seq", None)):
        ring = cls.create(capacity, 32)
        try:
            _push_clean(ring, b"original")
            _crash_at(cls, ring, "consumer", point, b"")
            _claim_after_death(cls, ring, "consumer")
            ring.claim("producer")
            assert ring.try_pop() == expected
            if expected is None:
                assert ring.try_push(b"capacity-free")
                assert ring.try_pop() == b"capacity-free"
        finally:
            ring.close()
            ring.unlink()


@pytest.mark.parametrize("cls", CLASSES)
@pytest.mark.parametrize(("role", "point"), (("producer", "producer_dirty"), ("consumer", "consumer_dirty")))
def test_crash_during_dirty_marker_transition(cls, role, point):
    if not _TEST_HOOKS:
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    ring = cls.create(1, 16)
    try:
        _crash_at(cls, ring, role, point, b"unused")
        _claim_after_death(cls, ring, role)
        ring.release(role)
    finally:
        ring.close()
        ring.unlink()


def test_same_process_roles_and_protocol_errors():
    first = RecoverableAtomicSharedByteRing.create(1, 8)
    second = RecoverableAtomicSharedByteRing.attach(first.name)
    try:
        first.claim("producer")
        with pytest.raises(RoleBusyError):
            second.claim("producer")
        with pytest.raises(RuntimeError):
            first.try_pop()
        with pytest.raises(ValueError):
            first.claim("both")
        with pytest.raises(RuntimeError):
            first.claim("producer")
        first.release("producer")
        with pytest.raises(RuntimeError):
            first.release("producer")
        second.claim("producer")
        second.release("producer")
    finally:
        first.close()
        second.close()
        first.unlink()


def test_attached_mapping_survives_unlink_and_close_releases_role():
    creator = RecoverableAtomicSharedByteRing.create(1, 16)
    attached = RecoverableAtomicSharedByteRing.attach(creator.name)
    try:
        creator.claim("producer")
        creator.close()
        attached.claim("producer")
        creator.unlink()
        assert attached.try_push(b"after-unlink")
        attached.claim("consumer")
        assert attached.try_pop() == b"after-unlink"
        attached.release("producer")
        attached.release("consumer")
    finally:
        creator.close()
        attached.close()
        try:
            creator.unlink()
        except OSError:
            pass
def test_live_owner_busy_then_manual_recovery_after_sigkill():
    ctx = multiprocessing.get_context("spawn")
    ring = RecoverableAtomicSharedByteRing.create(1, 8)
    ready, stop = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_lock_child, args=(ring.name, ready, stop))
    try:
        process.start()
        assert ready.wait(5)
        with pytest.raises(RoleBusyError):
            ring.claim("producer")
        process.kill()
        _stop(process)
        with pytest.raises(RecoveryRequiredError):
            ring.claim("producer")
        ring.recover("producer")
        ring.release("producer")
    finally:
        if process.is_alive():
            process.kill()
            process.join(2)
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("cls", CLASSES)
def test_lifecycle_full_empty_types_oversize_and_context(cls):
    ring = cls.create(1, 3)
    name = ring.name
    attached = cls.attach(name)
    try:
        with pytest.raises(PermissionError):
            attached.unlink()
        with pytest.raises(RuntimeError):
            ring.try_push(b"x")
        with pytest.raises(RuntimeError):
            ring.try_pop()
        with ring as entered:
            assert entered is ring
            ring.claim("producer")
            ring.claim("consumer")
            assert ring.try_pop() is None
            assert ring.try_push(b"abc")
            assert ring.try_push(b"x") is False
            assert ring.try_pop() == b"abc"
            assert ring.try_pop() is None
            with pytest.raises(TypeError):
                ring.try_push(bytearray(b"x"))
            with pytest.raises(ValueError):
                ring.try_push(b"four")
            ring.release("producer")
            ring.release("consumer")
        with pytest.raises(ValueError):
            ring.claim("producer")
        ring.unlink()
        with pytest.raises(OSError):
            cls.attach(name)
    finally:
        ring.close()
        attached.close()


def test_manual_recover_can_claim_clean_role_and_marks_it_dirty():
    ring = RecoverableAtomicSharedByteRing.create(1, 8)
    attached = RecoverableAtomicSharedByteRing.attach(ring.name)
    try:
        ring.recover("producer")
        with pytest.raises(RoleBusyError):
            attached.claim("producer")
        ring.release("producer")
        attached.claim("producer")
        attached.release("producer")
    finally:
        ring.close()
        attached.close()
        ring.unlink()


@pytest.mark.parametrize("cls", CLASSES)
def test_close_releases_cleanly_claimed_roles(cls):
    ring = cls.create(1, 8)
    attached = cls.attach(ring.name)
    try:
        ring.claim("producer")
        ring.close()
        attached.claim("producer")
        attached.release("producer")
    finally:
        ring.close()
        attached.close()
        ring.unlink()


def _auto_lock_child(name, ready, stop):
    ring = AutoRecoveringAtomicSharedByteRing.attach(name)
    ring.claim("producer")
    ready.set()
    stop.wait(10)


def test_live_auto_owner_is_busy_and_sigkill_dirty_marker_recovers():
    ctx = multiprocessing.get_context("spawn")
    ring = AutoRecoveringAtomicSharedByteRing.create(1, 8)
    ready, stop = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_auto_lock_child, args=(ring.name, ready, stop))
    try:
        process.start()
        assert ready.wait(5)
        with pytest.raises(RoleBusyError):
            ring.claim("producer")
        process.kill()
        _stop(process)
        ring.claim("producer")
        ring.release("producer")
    finally:
        if process.is_alive():
            process.kill()
            process.join(2)
        ring.close()
        ring.unlink()
