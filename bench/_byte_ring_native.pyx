# cython: language_level=3, boundscheck=False, wraparound=False, initializedcheck=False
"""Benchmark-only fixed-capacity byte ring with native slab storage."""

from libc.stddef cimport size_t
from cpython.mem cimport PyMem_Malloc, PyMem_Free
import sys

cdef class ByteRing:
    cdef unsigned char *data
    cdef Py_ssize_t *lengths
    cdef Py_ssize_t capacity
    cdef Py_ssize_t max_item_size
    cdef Py_ssize_t head
    cdef Py_ssize_t tail
    cdef Py_ssize_t count

    def __cinit__(self, capacity, max_item_size):
        cdef size_t slots
        cdef size_t item_size
        cdef size_t total
        self.data = NULL
        self.lengths = NULL
        self.capacity = 0
        self.max_item_size = 0
        self.head = 0
        self.tail = 0
        self.count = 0
        if type(capacity) is not int or type(max_item_size) is not int:
            raise TypeError("capacity and max_item_size must be exact ints")
        if capacity > sys.maxsize or max_item_size > sys.maxsize:
            raise OverflowError("capacity and max_item_size must fit Py_ssize_t")
        if capacity < 0 or max_item_size < 0:
            raise ValueError("capacity must be positive and max_item_size nonnegative")
        if capacity == 0:
            raise ValueError("capacity must be positive and max_item_size nonnegative")
        slots = <size_t>capacity
        item_size = <size_t>max_item_size
        if slots > (<size_t>-1) // sizeof(Py_ssize_t):
            raise OverflowError("ring metadata allocation size overflow")
        if item_size != 0 and slots > (<size_t>-1) // item_size:
            raise OverflowError("ring storage allocation size overflow")
        if item_size != 0 and slots > <size_t>sys.maxsize // item_size:
            raise OverflowError("ring storage exceeds Py_ssize_t address range")
        total = slots * item_size
        self.lengths = <Py_ssize_t *>PyMem_Malloc(slots * sizeof(Py_ssize_t))
        if self.lengths == NULL:
            raise MemoryError()
        if total:
            self.data = <unsigned char *>PyMem_Malloc(total)
            if self.data == NULL:
                PyMem_Free(self.lengths)
                self.lengths = NULL
                raise MemoryError()
        self.capacity = <Py_ssize_t>capacity
        self.max_item_size = <Py_ssize_t>max_item_size

    def __dealloc__(self):
        if self.data != NULL:
            PyMem_Free(self.data)
        if self.lengths != NULL:
            PyMem_Free(self.lengths)

    def try_push(self, item):
        cdef Py_ssize_t n
        cdef Py_ssize_t offset
        if type(item) is not bytes:
            raise TypeError("item must be exact bytes")
        n = len(item)
        if n > self.max_item_size:
            raise ValueError("item exceeds max_item_size")
        if self.count == self.capacity:
            return False
        offset = self.tail * self.max_item_size
        if n:
            # GIL is held; the source is immutable and alive throughout this copy.
            memcpy(self.data + offset, <const unsigned char *>item, <size_t>n)
        self.lengths[self.tail] = n
        self.tail += 1
        if self.tail == self.capacity:
            self.tail = 0
        self.count += 1
        return True

    def try_pop(self):
        cdef Py_ssize_t n
        cdef Py_ssize_t offset
        cdef object result
        if self.count == 0:
            return None
        n = self.lengths[self.head]
        offset = self.head * self.max_item_size
        if n:
            result = (<bytes>PyBytes_FromStringAndSize(<char *>(self.data + offset), n))
        else:
            result = b""
        self.head += 1
        if self.head == self.capacity:
            self.head = 0
        self.count -= 1
        return result

cdef extern from "string.h":
    void *memcpy(void *, const void *, size_t)

cdef extern from "Python.h":
    object PyBytes_FromStringAndSize(const char *, Py_ssize_t) except *
