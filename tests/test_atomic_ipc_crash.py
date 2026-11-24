"""Deterministic process-death checks at observable atomic-ring boundaries.

These exercise boundaries exposed between public calls; they cannot kill a
process inside the native memcpy/atomic-store instruction window.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import time
import uuid

import pytest

if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
    pytest.skip("atomic IPC requires Linux x86-64", allow_module_level=True)

from picoke.ipc import AtomicSharedByteRing


def _join(process: multiprocessing.Process) -> None:
    process.join(8)
    if process.is_alive():
        process.terminate()
        process.join(2)
        pytest.fail("atomic ring crash-test child did not exit")


def _die_before_native_create(ready, suffix: str) -> None:
    """Pause after choosing a name but before native creation/publication."""
    import picoke._atomic_ipc as module

    module.secrets.token_hex = lambda _n: suffix

    def paused_create(*_args):
        ready.set()
        while True:
            time.sleep(1)

    module._atomic_ipc.create = paused_create
    module.AtomicSharedByteRing.create(2, 16)


def _pause_before_push(name: str, ready) -> None:
    AtomicSharedByteRing.attach(name)
    ready.set()
    while True:
        time.sleep(1)


def _push_then_die(name: str) -> None:
    ring = AtomicSharedByteRing.attach(name)
    if not ring.try_push(b"committed"):
        os._exit(2)
    os._exit(0)


def _pop_then_die(name: str, consumed) -> None:
    ring = AtomicSharedByteRing.attach(name)
    if ring.try_pop() != b"before-pop":
        os._exit(2)
    consumed.set()
    os._exit(0)


def test_process_death_before_creation_publication_leaves_no_attachable_name() -> None:
    ctx = multiprocessing.get_context("spawn")
    suffix = uuid.uuid4().hex
    name = "/picoke-atomic-" + suffix
    ready = ctx.Event()
    process = ctx.Process(target=_die_before_native_create, args=(ready, suffix))
    process.start()
    try:
        assert ready.wait(5), "creator did not reach pre-publication boundary"
        process.terminate()
        _join(process)
        with pytest.raises(OSError):
            AtomicSharedByteRing.attach(name)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)


def test_process_death_before_producer_publication_leaves_ring_unchanged() -> None:
    ring = AtomicSharedByteRing.create(1, 16)
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    process = ctx.Process(target=_pause_before_push, args=(ring.name, ready))
    try:
        process.start()
        assert ready.wait(5), "producer did not reach pre-publication boundary"
        process.terminate()
        _join(process)
        assert ring.try_pop() is None
        assert ring.try_push(b"after-death")
        assert ring.try_pop() == b"after-death"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()


def test_process_death_after_producer_publication_preserves_item() -> None:
    ring = AtomicSharedByteRing.create(2, 16)
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=_push_then_die, args=(ring.name,))
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


def test_process_death_after_consumer_publication_releases_capacity() -> None:
    ring = AtomicSharedByteRing.create(1, 16)
    assert ring.try_push(b"before-pop")
    ctx = multiprocessing.get_context("spawn")
    consumed = ctx.Event()
    process = ctx.Process(target=_pop_then_die, args=(ring.name, consumed))
    try:
        process.start()
        _join(process)
        assert process.exitcode == 0
        assert consumed.is_set()
        assert ring.try_push(b"after-pop")
        assert ring.try_pop() == b"after-pop"
        assert ring.try_pop() is None
    finally:
        if process.is_alive():
            process.terminate()
            process.join(2)
        ring.close()
        ring.unlink()
