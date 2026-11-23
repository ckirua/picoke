"""Cython field validation with bounded native recursion."""

from __future__ import annotations

from libc.math cimport isfinite
from builtins import isinstance as py_isinstance
from collections.abc import Mapping
from cpython.dict cimport PyDict_CheckExact
from cpython.float cimport PyFloat_CheckExact
from cpython.list cimport PyList_CheckExact
from cpython.long cimport PyLong_CheckExact
from cpython.unicode cimport PyUnicode_CheckExact

from ._binary_logging_support import (
    call1, call2, enter1, enter2, fallback_materialize, fallback_numeric, fallback_value,
)


cdef inline bint exact_builtin(object value) noexcept:
    """Only exact built-ins can safely use Cython's optimized isinstance."""
    return (value is None or value is True or value is False or
            PyUnicode_CheckExact(value) or PyLong_CheckExact(value) or
            PyFloat_CheckExact(value) or PyDict_CheckExact(value) or
            PyList_CheckExact(value))


def _materialize_json(value, active):
    """Mirror picoke.logging._json_fields, including traversal and error order."""
    # Switch deep trees back to Python before consuming an unbounded C stack.
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("fields contain a circular reference")
        active.add(identity)
        try:
            result = {}
            for key, item in value.items():
                if type(key) is not str and not py_isinstance(key, str):
                    raise TypeError("field mapping keys must be strings")
                result[key] = (fallback_materialize(item, active)
                               if len(active) >= 64 or not exact_builtin(item)
                               else call2(_materialize_json, item, active))
            return result
        finally:
            active.remove(identity)
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            raise ValueError("fields contain a circular reference")
        active.add(identity)
        try:
            return [(fallback_materialize(item, active)
                     if len(active) >= 64 or not exact_builtin(item)
                     else call2(_materialize_json, item, active)) for item in value]
        finally:
            active.remove(identity)
    raise TypeError(f"unsupported field value type: {type(value).__name__}")



def _validate_value(value):
    """Preserve JSON structural materialization before numeric checks."""
    if not exact_builtin(value):
        return fallback_value(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 64):
            raise OverflowError("integer outside MessagePack 64-bit range")
        return value
    if isinstance(value, float):
        # This fast path only receives exact built-in floats.
        if not isfinite(<double>value):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    if isinstance(value, Mapping):
        # Keep source implementation's complete structural pass first. This
        # preserves custom Mapping.items() side effects and error precedence.
        materialized = enter2(_materialize_json, value, set())
        return enter1(_validate_materialized, materialized)
    if isinstance(value, list):
        return enter1(_validate_materialized, enter2(_materialize_json, value, set()))
    raise TypeError(f"unsupported field value type: {type(value).__name__}")


def _validate_materialized(value, depth=0):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if not -(1 << 63) <= value < (1 << 64):
            raise OverflowError("integer outside MessagePack 64-bit range")
        return value
    if isinstance(value, float):
        if not isfinite(<double>value):
            raise ValueError("non-finite number is not JSON-compatible")
        return value
    if isinstance(value, dict):
        for item in value.values():
            if depth >= 63 or not exact_builtin(item):
                fallback_numeric(item)
            else:
                call2(_validate_materialized, item, depth + 1)
        return value
    if isinstance(value, list):
        for item in value:
            if depth >= 63 or not exact_builtin(item):
                fallback_numeric(item)
            else:
                call2(_validate_materialized, item, depth + 1)
        return value
    raise TypeError(f"unsupported field value type: {type(value).__name__}")




def _exact_string_keys(object value):
    """Fast-path exact dictionaries containing only exact string keys."""
    if not PyDict_CheckExact(value):
        return False
    cdef dict record = value
    for key in record:
        if not PyUnicode_CheckExact(key):
            return False
    return True


def _read_exact(stream, size):
    cdef Py_ssize_t count
    if not size:
        return b""
    chunk = stream.read(size)
    if chunk is None:
        raise BlockingIOError("binary log stream would block")
    if type(chunk) is bytes:
        count = len(<bytes>chunk)
        if count == 0:
            return b""
        if count > size:
            raise ValueError("binary log stream returned more bytes than requested")
        if count == size:
            return chunk
        chunks = [chunk]
        remaining = size - count
    else:
        # Preserve Python type and length semantics for subclasses and proxies.
        if not py_isinstance(chunk, bytes):
            raise TypeError("binary log stream must return bytes")
        if not chunk:
            return b""
        if len(chunk) > size:
            raise ValueError("binary log stream returned more bytes than requested")
        chunks = [chunk]
        remaining = size - len(chunk)
    while remaining:
        chunk = stream.read(remaining)
        if chunk is None:
            raise BlockingIOError("binary log stream would block")
        if type(chunk) is bytes:
            count = len(<bytes>chunk)
            if count == 0:
                break
            if count > remaining:
                raise ValueError("binary log stream returned more bytes than requested")
            chunks.append(chunk)
            remaining -= count
        else:
            if not py_isinstance(chunk, bytes):
                raise TypeError("binary log stream must return bytes")
            if not chunk:
                break
            if len(chunk) > remaining:
                raise ValueError("binary log stream returned more bytes than requested")
            chunks.append(chunk)
            remaining -= len(chunk)
    return b"".join(chunks)
