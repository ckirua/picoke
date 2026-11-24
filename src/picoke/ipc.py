"""Linux shared-memory single-producer/single-consumer byte ring.

The ring is bounded by item count; each slot reserves ``max_item_size`` bytes.
Payloads are copied into and out of shared memory. Operations use an OS advisory
file lock, so synchronization is process-wide and does not depend on the GIL.
The contract is SPSC; there is no recovery guarantee if a process dies while
holding the lock or modifying the ring.

Call :meth:`close` to detach this handle. The creator should call :meth:`unlink`
when no future process needs to attach. Closing does not unlink, and unlinking
does not invalidate already attached handles. Context manager exit closes only
that handle. The filesystem-backed shared segment and its lock descriptor are
tracked explicitly and are released by ``close``; unlinking removes the name.
"""

from __future__ import annotations

import fcntl
import mmap
import os
import struct
import sys
import threading
import uuid

if sys.platform == "linux":
    from . import _ipc_native

_HEADER = struct.Struct("<8sQQQQQ")
_MAGIC = b"PICORNG1"
_LENGTH = struct.Struct("<Q")
_PREFIX = "picoke-ipc-"


def _require_linux() -> None:
    if sys.platform != "linux":
        raise OSError("SharedByteRing is supported only on Linux")


def _path(name: str) -> str:
    if type(name) is not str or not name.startswith(_PREFIX):
        raise ValueError("invalid SharedByteRing name")
    suffix = name[len(_PREFIX):]
    if not suffix or any(ch not in "0123456789abcdef" for ch in suffix):
        raise ValueError("invalid SharedByteRing name")
    return os.path.join("/dev/shm", name)


class SharedByteRing:
    """A bounded SPSC FIFO backed by a named Linux shared-memory file.

    Use ``create(capacity, max_item_size)`` in the creating process and
    ``attach(name)`` in the consumer (or another process). Only one producer
    and one consumer may operate on a ring concurrently. Advisory ``flock``
    serializes each state transition across processes; the lock is not a
    multi-producer/multi-consumer guarantee.

    ``capacity`` is the number of queued items. ``max_item_size`` is the
    maximum payload length in bytes. Values must be exact integers, capacity
    positive and maximum size nonnegative. Exact ``bytes`` payloads are copied
    into shared memory on successful push and copied back on pop. Empty bytes
    are a valid item; ``None`` alone indicates an empty queue.

    Closing detaches this handle, not the shared object. The creator is
    responsible for ``unlink()`` after other processes have attached or
    finished. Calls after close raise ``ValueError``. An unlinked name can no
    longer be attached, but extant handles remain usable. A process crash while
    mutating the ring is not recoverable and may leave its contents unusable.
    """

    def __init__(self, fd: int, mapping: object, name: str, capacity: int,
                 max_item_size: int, creator: bool) -> None:
        self._fd = fd
        self._pid = os.getpid()
        self._mapping = mapping
        self._name = name
        self._capacity = capacity
        self._max_item_size = max_item_size
        self._creator = creator
        self._closed = False
        self._unlinked = False
        self._guard = threading.Lock()

    @classmethod
    def create(cls, capacity: int, max_item_size: int) -> "SharedByteRing":
        """Create a uniquely named segment and initialize its ring metadata."""
        _require_linux()
        if type(capacity) is not int or type(max_item_size) is not int:
            raise TypeError("capacity and max_item_size must be exact ints")
        if capacity <= 0 or max_item_size < 0:
            raise ValueError("capacity must be positive and max_item_size nonnegative")
        if capacity > (1 << 63) - 1 or max_item_size > (1 << 63) - 1:
            raise OverflowError("capacity and max_item_size must fit in signed 64 bits")
        name = _PREFIX + uuid.uuid4().hex
        path = _path(name)
        size = _HEADER.size + capacity * (_LENGTH.size + max_item_size)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        mapping = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.ftruncate(fd, size)
            mapping = mmap.mmap(fd, size)
            _HEADER.pack_into(mapping, 0, _MAGIC, capacity, max_item_size, 0, 0, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return cls(fd, mapping, name, capacity, max_item_size, True)
        except BaseException:
            if mapping is not None:
                mapping.close()
            os.close(fd)
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def attach(cls, name: str) -> "SharedByteRing":
        """Attach to a segment created by :meth:`create` using its name."""
        _require_linux()
        path = _path(name)
        fd = os.open(path, os.O_RDWR)
        mapping = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            stat = os.fstat(fd)
            if stat.st_size < _HEADER.size:
                raise ValueError("invalid SharedByteRing segment")
            mapping = mmap.mmap(fd, stat.st_size)
            magic, capacity, max_size, head, tail, count = _HEADER.unpack_from(mapping)
            expected = _HEADER.size + capacity * (_LENGTH.size + max_size)
            if (magic != _MAGIC or capacity == 0 or expected != stat.st_size or
                    head >= capacity or tail >= capacity or count > capacity):
                raise ValueError("invalid SharedByteRing segment")
            result = cls(fd, mapping, name, capacity, max_size, False)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return result
        except BaseException:
            if mapping is not None:
                mapping.close()
            os.close(fd)
            raise

    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def max_item_size(self) -> int:
        return self._max_item_size

    def _check_process(self) -> None:
        if os.getpid() != self._pid:
            raise RuntimeError("SharedByteRing cannot be used after fork; attach by name")

    def _ensure_open(self) -> None:
        if self._closed:
            raise ValueError("SharedByteRing is closed")

    def try_push(self, item: bytes) -> bool:
        """Copy exact bytes into the next slot; return False if full."""
        self._check_process()
        if type(item) is not bytes:
            raise TypeError("item must be exact bytes")
        if len(item) > self._max_item_size:
            raise ValueError("item exceeds max_item_size")
        with self._guard:
            self._ensure_open()
            fcntl.flock(self._fd, fcntl.LOCK_EX)
            try:
                return _ipc_native.try_push(
                    self._mapping, item, self._capacity, self._max_item_size
                )
            finally:
                fcntl.flock(self._fd, fcntl.LOCK_UN)

    def try_pop(self) -> bytes | None:
        """Copy and return the oldest item, or None when the queue is empty."""
        self._check_process()
        with self._guard:
            self._ensure_open()
            fcntl.flock(self._fd, fcntl.LOCK_EX)
            try:
                return _ipc_native.try_pop(
                    self._mapping, self._capacity, self._max_item_size
                )
            finally:
                fcntl.flock(self._fd, fcntl.LOCK_UN)

    def close(self) -> None:
        """Detach this handle, releasing its mapping and lock descriptor."""
        self._check_process()
        with self._guard:
            if self._closed:
                return
            self._mapping.close()
            os.close(self._fd)
            self._closed = True

    def unlink(self) -> None:
        """Remove the shared name; existing attached handles remain valid."""
        self._check_process()
        with self._guard:
            if self._unlinked:
                return
            try:
                os.unlink(_path(self._name))
            except FileNotFoundError:
                pass
            self._unlinked = True

    def __enter__(self) -> "SharedByteRing":
        self._check_process()
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


if (sys.platform == "linux" and os.uname().machine in ("x86_64", "amd64")
        and (not hasattr(sys, "_is_gil_enabled") or sys._is_gil_enabled())):
    from ._atomic_ipc import AtomicSharedByteRing
