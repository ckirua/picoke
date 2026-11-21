"""Guarded standard-library deque baseline for ByteRing benchmarks.

Exactly one producer appends and exactly one consumer pops. Between an
admitting capacity check and append, only the consumer can change the size,
and it can only shrink it. Between an admitting nonempty check and pop, only
the producer can change the size, and it can only grow it. Neither check needs
a lock for this SPSC contract on the tested GIL-enabled CPython builds.

Bytes objects are retained directly by deque; no payload copy is made. A
successful pop transfers that same immutable object reference to the caller.
"""

from collections import deque
import sys


class ByteRing:
    """Fixed-capacity SPSC byte queue implemented with a guarded deque."""

    __slots__ = ("_capacity", "_max_item_size", "_items")

    def __init__(self, capacity: int, max_item_size: int) -> None:
        if type(capacity) is not int:
            raise TypeError("capacity must be an exact int")
        if type(max_item_size) is not int:
            raise TypeError("max_item_size must be an exact int")
        if capacity > sys.maxsize or max_item_size > sys.maxsize:
            raise OverflowError("value does not fit Py_ssize_t")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if max_item_size < 0:
            raise ValueError("max_item_size must be non-negative")

        self._capacity = capacity
        self._max_item_size = max_item_size
        self._items: deque[bytes] = deque()

    def try_push(self, item: bytes) -> bool:
        if type(item) is not bytes:
            raise TypeError("item must be exact bytes")
        if len(item) > self._max_item_size:
            raise ValueError("item exceeds max_item_size")
        if len(self._items) == self._capacity:
            return False
        self._items.append(item)
        return True

    def try_pop(self) -> bytes | None:
        if not self._items:
            return None
        return self._items.popleft()
