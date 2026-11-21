# cython: boundscheck=False, wraparound=False
"""Bounded single-producer, single-consumer queues."""

from cpython.bytes cimport PyBytes_CheckExact, PyBytes_GET_SIZE
from cpython.long cimport PyLong_AsSsize_t, PyLong_CheckExact

cdef class ByteRing:
    """Fixed-capacity FIFO for immutable bytes on GIL-enabled CPython."""

    # The GIL serializes each transition. Head and tail always index the
    # private list and count is in [0, capacity]; slot access cannot go OOB.
    cdef list slots
    cdef Py_ssize_t capacity
    cdef Py_ssize_t max_item_size
    cdef Py_ssize_t head
    cdef Py_ssize_t tail
    cdef Py_ssize_t count

    def __cinit__(self, capacity, max_item_size):
        if not PyLong_CheckExact(capacity) or not PyLong_CheckExact(max_item_size):
            raise TypeError("capacity and max_item_size must be exact ints")
        if capacity <= 0 or max_item_size < 0:
            raise ValueError("capacity must be positive and max_item_size nonnegative")
        self.capacity = PyLong_AsSsize_t(capacity)
        self.max_item_size = PyLong_AsSsize_t(max_item_size)
        self.slots = [None] * self.capacity

    def try_push(self, item):
        """Return False without enqueuing if the ring is full."""
        if not PyBytes_CheckExact(item):
            raise TypeError("item must be exact bytes")
        if PyBytes_GET_SIZE(item) > self.max_item_size:
            raise ValueError("item exceeds max_item_size")
        if self.count == self.capacity:
            return False
        self.slots[self.tail] = item
        self.tail += 1
        if self.tail == self.capacity:
            self.tail = 0
        self.count += 1
        return True

    def try_pop(self):
        """Return the oldest bytes item, or None if the ring is empty."""
        cdef object item
        if self.count == 0:
            return None
        item = self.slots[self.head]
        self.slots[self.head] = None
        self.head += 1
        if self.head == self.capacity:
            self.head = 0
        self.count -= 1
        return item
