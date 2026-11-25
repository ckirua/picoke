"""Additional ownership, header validation, and release crash tests."""
from __future__ import annotations

import multiprocessing
import os
import select
import struct
import sys
import time
import uuid

import pytest

if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
    pytest.skip("recoverable IPC requires Linux x86-64", allow_module_level=True)

from picoke import _atomic_ipc_native
from picoke.ipc import (
    AtomicSharedByteRing,
    AutoRecoveringAtomicSharedByteRing,
    RecoverableAtomicSharedByteRing,
    RecoveryRequiredError,
    RoleBusyError,
)

_HOOKS = getattr(_atomic_ipc_native, "TEST_HOOKS", False)


def _join(process):
    process.join(8)
    if process.is_alive():
        process.kill()
        process.join(2)
        pytest.fail("worker did not exit within bound")


def _auto_claim_worker(name, results, stop):
    ring = AutoRecoveringAtomicSharedByteRing.attach(name)
    try:
        try:
            ring.claim("producer")
        except RoleBusyError:
            results.put("busy")
            return
        results.put("claimed")
        stop.wait(10)
    finally:
        ring.close()


def _kill_owner(name, ready):
    ring = AutoRecoveringAtomicSharedByteRing.attach(name)
    ring.claim("producer")
    ready.set()
    time.sleep(10)


def _pause_before_release(name, connection):
    ring = RecoverableAtomicSharedByteRing.attach(name)
    ring.claim("producer")
    ring.set_failpoint("before_unlock", connection.fileno())
    ring.release("producer")
    os._exit(3)


def _create_at_hook(point, suffix, connection):
    import picoke._atomic_ipc as module
    module.secrets.token_hex = lambda _n: suffix
    _atomic_ipc_native.set_next_create_failpoint(point, connection.fileno())
    RecoverableAtomicSharedByteRing.create(1, 8)
    os._exit(3)


def _unpublished_push(cls_name, name, connection):
    cls = RecoverableAtomicSharedByteRing if cls_name == "RecoverableAtomicSharedByteRing" else AutoRecoveringAtomicSharedByteRing
    ring = cls.attach(name)
    ring.claim("producer")
    ring.set_failpoint("before_write_seq", connection.fileno())
    ring.try_push(b"unpublished")
    os._exit(3)


def _atomic_create_at_hook(point, suffix, connection):
    import picoke._atomic_ipc as module
    module.secrets.token_hex = lambda _n: suffix
    _atomic_ipc_native.set_next_create_failpoint(point, connection.fileno())
    AtomicSharedByteRing.create(1, 8)
    os._exit(3)


def _recover_producer(cls, ring):
    if cls is RecoverableAtomicSharedByteRing:
        with pytest.raises(RecoveryRequiredError):
            ring.claim("producer")
        ring.recover("producer")
    else:
        ring.claim("producer")


def _wait_hook(ctx, target, args, expected):
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=target, args=(*args, writer))
    process.start()
    writer.close()
    try:
        assert reader.poll(8), f"child did not reach {expected}"
        assert os.read(reader.fileno(), 128).decode("ascii").strip() == expected
    except BaseException:
        if process.is_alive():
            process.kill()
        process.join(2)
        raise
    finally:
        reader.close()
        writer.close()
    return process


def test_concurrent_auto_replacements_have_one_role_owner():
    ctx = multiprocessing.get_context("spawn")
    ring = AutoRecoveringAtomicSharedByteRing.create(1, 8)
    ready = ctx.Event()
    owner = ctx.Process(target=_kill_owner, args=(ring.name, ready))
    stop = ctx.Event()
    results = ctx.Queue()
    workers = []
    try:
        owner.start()
        assert ready.wait(5)
        owner.kill()
        _join(owner)
        workers = [ctx.Process(target=_auto_claim_worker, args=(ring.name, results, stop)) for _ in range(2)]
        for worker in workers:
            worker.start()
        outcomes = [results.get(timeout=8) for _ in workers]
        assert sorted(outcomes) == ["busy", "claimed"]
        stop.set()
        for worker in workers:
            _join(worker)
    finally:
        stop.set()
        for process in [owner, *workers]:
            if process.is_alive():
                process.kill()
                process.join(2)
        ring.close()
        ring.unlink()


def test_forked_child_cannot_use_handle_and_parent_keeps_role_lock():
    ring = RecoverableAtomicSharedByteRing.create(1, 8)
    attached = RecoverableAtomicSharedByteRing.attach(ring.name)
    try:
        ring.claim("producer")
        pid = os.fork()
        if pid == 0:
            try:
                ring.try_push(b"child")
            except RuntimeError:
                os._exit(0)
            os._exit(2)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        with pytest.raises(RoleBusyError):
            attached.claim("producer")
        ring.release("producer")
        attached.claim("producer")
        attached.release("producer")
    finally:
        ring.close()
        attached.close()
        ring.unlink()


def test_fork_child_closes_inherited_role_lock_descriptor():
    ring = RecoverableAtomicSharedByteRing.create(1, 8)
    attached = RecoverableAtomicSharedByteRing.attach(ring.name)
    read_fd, write_fd = os.pipe()
    ready_read_fd, ready_write_fd = os.pipe()
    pid = None
    try:
        ring.claim("producer")
        pid = os.fork()
        if pid == 0:
            os.close(write_fd)
            os.close(ready_read_fd)
            os.write(ready_write_fd, b"r")
            os.close(ready_write_fd)
            os.read(read_fd, 1)
            os._exit(0)
        os.close(read_fd)
        read_fd = -1
        os.close(ready_write_fd)
        ready_write_fd = -1
        readable, _, _ = select.select([ready_read_fd], [], [], 5)
        assert readable, "fork child did not finish at-fork cleanup"
        assert os.read(ready_read_fd, 1) == b"r"
        os.close(ready_read_fd)
        ready_read_fd = -1
        ring.release("producer")
        attached.claim("producer")
        attached.release("producer")
        os.write(write_fd, b"x")
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        for fd in (read_fd, write_fd, ready_read_fd, ready_write_fd):
            if fd >= 0:
                os.close(fd)
        if pid is not None:
            try:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            except ProcessLookupError:
                pass
        ring.close()
        attached.close()
        ring.unlink()


def test_exec_child_does_not_release_parent_role_lock():
    ring = RecoverableAtomicSharedByteRing.create(1, 8)
    attached = RecoverableAtomicSharedByteRing.attach(ring.name)
    try:
        ring.claim("producer")
        pid = os.fork()
        if pid == 0:
            os.execl("/bin/true", "true")
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        with pytest.raises(RoleBusyError):
            attached.claim("producer")
        ring.release("producer")
        attached.claim("producer")
        attached.release("producer")
    finally:
        ring.close()
        attached.close()
        ring.unlink()


def test_recoverable_attach_rejects_atomic_header_and_corrupt_magic():
    atomic = AtomicSharedByteRing.create(1, 8)
    try:
        with pytest.raises((ValueError, OSError, RuntimeError)):
            RecoverableAtomicSharedByteRing.attach(atomic.name)
    finally:
        atomic.close()
        atomic.unlink()

    ring = RecoverableAtomicSharedByteRing.create(1, 8)
    try:
        fd = os.open("/dev/shm" + ring.name, os.O_RDWR)
        try:
            assert os.pwrite(fd, b"BROKEN!!", 40) == 8
        finally:
            os.close(fd)
        with pytest.raises(ValueError):
            RecoverableAtomicSharedByteRing.attach(ring.name)
    finally:
        ring.close()
        ring.unlink()


def test_corrupt_queued_length_failure_keeps_consumer_dirty():
    creator = RecoverableAtomicSharedByteRing.create(1, 8)
    consumer = RecoverableAtomicSharedByteRing.attach(creator.name)
    replacement = None
    try:
        creator.claim("producer")
        assert creator.try_push(b"valid")
        creator.release("producer")
        consumer.claim("consumer")
        fd = os.open("/dev/shm" + creator.name, os.O_RDWR)
        try:
            # RecoverHeader occupies 192 bytes; the first slot starts with its length.
            assert os.pwrite(fd, struct.pack("<Q", 9), 192) == 8
        finally:
            os.close(fd)
        with pytest.raises(RuntimeError, match="corrupt recoverable slot"):
            consumer.try_pop()
        consumer.close()
        replacement = RecoverableAtomicSharedByteRing.attach(creator.name)
        with pytest.raises(RecoveryRequiredError):
            replacement.claim("consumer")
    finally:
        creator.close()
        consumer.close()
        if replacement is not None:
            replacement.close()
        creator.unlink()


@pytest.mark.parametrize("cls", (RecoverableAtomicSharedByteRing, AutoRecoveringAtomicSharedByteRing))
def test_before_write_sequence_store_leaves_item_unpublished(cls):
    if not _HOOKS:
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    ctx = multiprocessing.get_context("spawn")
    ring = cls.create(1, 16)
    try:
        reader, writer = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_unpublished_push, args=(cls.__name__, ring.name, writer))
        process.start()
        writer.close()
        assert reader.poll(8)
        assert os.read(reader.fileno(), 128).decode().strip() == "before_write_seq"
        process.kill()
        _join(process)
        reader.close()
        _recover_producer(cls, ring)
        ring.claim("consumer")
        assert ring.try_pop() is None
        assert ring.try_push(b"new")
        assert ring.try_pop() == b"new"
    finally:
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("point", ("before_linkat", "after_linkat"))
def test_create_publication_failpoints(point):
    if not _HOOKS or not hasattr(_atomic_ipc_native, "set_next_create_failpoint"):
        pytest.skip("requires create failpoint support in PICOKE_IPC_TEST_HOOKS=1 build")
    ctx = multiprocessing.get_context("spawn")
    suffix = uuid.uuid4().hex
    name = "/picoke-atomic-" + suffix
    process = _wait_hook(ctx, _create_at_hook, (point, suffix), point)
    process.kill()
    _join(process)
    if point == "before_linkat":
        with pytest.raises(OSError):
            RecoverableAtomicSharedByteRing.attach(name)
    else:
        ring = RecoverableAtomicSharedByteRing.attach(name)
        try:
            ring.claim("producer")
            ring.release("producer")
        finally:
            ring.close()
            _atomic_ipc_native.unlink(name)


@pytest.mark.parametrize("cls", (RecoverableAtomicSharedByteRing, AutoRecoveringAtomicSharedByteRing))
def test_clean_release_marker_precedes_unlock(cls):
    if not _HOOKS:
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    ring = cls.create(1, 8)
    try:
        ctx = multiprocessing.get_context("spawn")
        process = _wait_hook(ctx, _pause_before_release, (ring.name,), "before_unlock")
        process.kill()
        _join(process)
        ring.claim("producer")
        ring.release("producer")
    finally:
        ring.close()
        ring.unlink()


@pytest.mark.parametrize("point", ("before_linkat", "after_linkat"))
def test_existing_atomic_ring_native_create_failpoints(point):
    if not _HOOKS or not hasattr(_atomic_ipc_native, "set_next_create_failpoint"):
        pytest.skip("requires PICOKE_IPC_TEST_HOOKS=1")
    ctx = multiprocessing.get_context("spawn")
    suffix = uuid.uuid4().hex
    name = "/picoke-atomic-" + suffix
    process = _wait_hook(ctx, _atomic_create_at_hook, (point, suffix), point)
    process.kill()
    _join(process)
    if point == "before_linkat":
        with pytest.raises(OSError):
            AtomicSharedByteRing.attach(name)
    else:
        ring = AtomicSharedByteRing.attach(name)
        try:
            assert ring.try_push(b"atomic")
            assert ring.try_pop() == b"atomic"
        finally:
            ring.close()
            _atomic_ipc_native.unlink(name)


