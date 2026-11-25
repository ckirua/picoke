"""Linux x86-64 atomic SPSC shared-memory byte rings.

``AtomicSharedByteRing`` uses lock-free interprocess sequence counters but has
no crash recovery if a participant dies during an operation. The separate
``RecoverableAtomicSharedByteRing`` and ``AutoRecoveringAtomicSharedByteRing``
variants claim exclusive producer/consumer roles and recover interrupted
operations (explicitly and automatically, respectively). These formats are
distinct from :mod:`picoke.ipc`'s flock-based ring. They require an ordinary
GIL-enabled CPython build on Linux x86-64 with lock-free aligned 64-bit
atomics. Creation requires ``O_TMPFILE`` support on ``/dev/shm`` and a mounted
``/proc``. The creator should unlink after all intended attachers have opened.
"""
from __future__ import annotations

import os
import secrets
import sys

if sys.platform != "linux" or os.uname().machine not in ("x86_64", "amd64"):
    raise ImportError("AtomicSharedByteRing is supported only on Linux x86-64")
if hasattr(sys, "_is_gil_enabled") and not sys._is_gil_enabled():
    raise ImportError("AtomicSharedByteRing requires the GIL")

from . import _atomic_ipc_native as _atomic_ipc

_PREFIX = "/picoke-atomic-"


def _bounds(capacity: int, max_item_size: int) -> None:
    if type(capacity) is not int or capacity <= 0 or capacity > sys.maxsize or capacity & (capacity - 1):
        raise ValueError("capacity must be a positive power of two within Py_ssize_t bounds")
    if type(max_item_size) is not int or max_item_size < 0 or max_item_size > sys.maxsize:
        raise ValueError("max_item_size must be a nonnegative exact int within Py_ssize_t bounds")


def _valid_name(name: str) -> None:
    if (type(name) is not str or not name.startswith(_PREFIX)
            or len(name) != len(_PREFIX) + 32
            or any(c not in "0123456789abcdef" for c in name[len(_PREFIX):])):
        raise ValueError("invalid AtomicSharedByteRing name")


class AtomicSharedByteRing:
    """Copied-byte SPSC FIFO with lock-free interprocess sequence counters.

    ``capacity`` must be a positive power of two. ``max_item_size`` may be
    zero. A handle is bound to its creating process; after ``fork`` it must
    not be used in the child. Calls on one handle are protected by CPython's
    GIL; callers must still obey the single-producer/single-consumer contract.
    """

    __slots__ = ("_name", "_ring", "_owner", "_pid")

    def __init__(self, name: str, ring: _atomic_ipc.Ring, owner: bool):
        self._name = name
        self._ring = ring
        self._owner = owner
        self._pid = os.getpid()

    @classmethod
    def create(cls, capacity: int, max_item_size: int) -> "AtomicSharedByteRing":
        _bounds(capacity, max_item_size)
        name = _PREFIX + secrets.token_hex(16)
        ring = _atomic_ipc.create(name, capacity, max_item_size)
        return cls(name, ring, True)

    @classmethod
    def attach(cls, name: str) -> "AtomicSharedByteRing":
        _valid_name(name)
        ring = _atomic_ipc.attach(name)
        return cls(name, ring, False)

    @property
    def name(self) -> str:
        self._check_process()
        return self._name

    def _check_process(self) -> None:
        if self._pid != os.getpid():
            raise RuntimeError("AtomicSharedByteRing cannot be used after fork")

    def try_push(self, item: bytes) -> bool:
        self._check_process()
        if type(item) is not bytes:
            raise TypeError("item must be exact bytes")
        if len(item) > self._ring.max_item_size:
            raise ValueError("item exceeds max_item_size")
        return self._ring.try_push(item)

    def try_pop(self) -> bytes | None:
        self._check_process()
        return self._ring.try_pop()

    def close(self) -> None:
        self._check_process()
        self._ring.close()

    def unlink(self) -> None:
        self._check_process()
        if not self._owner:
            raise PermissionError("only the creating handle may unlink")
        _atomic_ipc.unlink(self._name)

    def __enter__(self) -> "AtomicSharedByteRing":
        self._check_process()
        self._ring.check_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
class _RecoverableAtomicSharedByteRing:
    """Role-claimed atomic SPSC ring with crash recovery."""

    __slots__ = ("_name", "_ring", "_owner", "_pid")
    _automatic = False

    def __init__(self, name: str, ring, owner: bool):
        self._name = name
        self._ring = ring
        self._owner = owner
        self._pid = os.getpid()

    @classmethod
    def create(cls, capacity: int, max_item_size: int):
        _bounds(capacity, max_item_size)
        name = _PREFIX + secrets.token_hex(16)
        ring = _atomic_ipc.create_recoverable(
            name, capacity, max_item_size, automatic=cls._automatic
        )
        return cls(name, ring, True)

    @classmethod
    def attach(cls, name: str):
        _valid_name(name)
        ring = _atomic_ipc.attach_recoverable(name, automatic=cls._automatic)
        return cls(name, ring, False)

    def _check_process(self):
        if self._pid != os.getpid():
            raise RuntimeError("recoverable ring cannot be used after fork")

    @property
    def name(self):
        self._check_process()
        return self._name

    def claim(self, role: str) -> None:
        self._check_process()
        if role not in ("producer", "consumer"):
            raise ValueError("role must be 'producer' or 'consumer'")
        self._ring.claim(role)

    def release(self, role: str) -> None:
        self._check_process()
        if role not in ("producer", "consumer"):
            raise ValueError("role must be 'producer' or 'consumer'")
        self._ring.release(role)


    def try_push(self, item: bytes) -> bool:
        self._check_process()
        if type(item) is not bytes:
            raise TypeError("item must be exact bytes")
        if len(item) > self._ring.max_item_size:
            raise ValueError("item exceeds max_item_size")
        return self._ring.try_push(item)

    def try_pop(self) -> bytes | None:
        self._check_process()
        return self._ring.try_pop()

    def close(self) -> None:
        self._check_process()
        self._ring.close()

    def unlink(self) -> None:
        self._check_process()
        if not self._owner:
            raise PermissionError("only the creating handle may unlink")
        _atomic_ipc.unlink(self._name)

    def __enter__(self):
        self._check_process()
        self._ring.check_open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

if getattr(_atomic_ipc, "TEST_HOOKS", False):
    def _set_failpoint(self, name: str, fd: int) -> None:
        self._check_process()
        self._ring.set_failpoint(name, fd)

    def _clear_failpoint(self) -> None:
        self._check_process()
        self._ring.clear_failpoint()

    for _cls in (AtomicSharedByteRing, _RecoverableAtomicSharedByteRing):
        _cls.set_failpoint = _set_failpoint
        _cls.clear_failpoint = _clear_failpoint


class RecoverableAtomicSharedByteRing(_RecoverableAtomicSharedByteRing):
    """Atomic SPSC ring requiring explicit recovery after an unclean role exit."""
    def recover(self, role: str) -> None:
        self._check_process()
        if role not in ("producer", "consumer"):
            raise ValueError("role must be 'producer' or 'consumer'")
        self._ring.recover(role)


class AutoRecoveringAtomicSharedByteRing(_RecoverableAtomicSharedByteRing):
    """Atomic SPSC ring which validates and recovers dirty roles on claim."""
    _automatic = True
    pass
