# cython: annotation_typing=False
"""Benchmark-only pybase64 wrapper-call overhead candidates.

This module compares attribute lookup, cached-callable, and vectorcall paths;
it is not a production implementation.
"""
from cpython.object cimport PyObject_IsInstance
from cpython.ref cimport PyObject
from libc.stddef cimport size_t

cdef extern from "Python.h":
    object PyObject_Vectorcall(
        PyObject *callable,
        PyObject *const *args,
        size_t nargsf,
        PyObject *kwnames
    ) except *

import binascii
import pybase64

cdef object _pybase64 = pybase64
cdef object _cached_decode = pybase64.b64decode
cdef object _validate_kwnames = ("validate",)



cdef inline object _fallback(object text):
    import base64
    return base64.b64decode(text, validate=True)


cdef inline bint _is_valid_text_type(object text):
    return PyObject_IsInstance(text, str)


def b64decode_attr(text: str) -> bytes:
    """Call pybase64's public strict decoder after module-attribute lookup."""
    if type(text) is not str:
        if not _is_valid_text_type(text):
            raise TypeError("text must be str")
        return _fallback(text)
    try:
        return _pybase64.b64decode(text, validate=True)
    except binascii.Error:
        return binascii.a2b_base64(text, strict_mode=True)


def b64decode_cached(text: str) -> bytes:
    """Call a cached pybase64 decoder with strict validation enabled."""
    if type(text) is not str:
        if not _is_valid_text_type(text):
            raise TypeError("text must be str")
        return _fallback(text)
    try:
        return _cached_decode(text, validate=True)
    except binascii.Error:
        return binascii.a2b_base64(text, strict_mode=True)


def b64decode_vector(text: str) -> bytes:
    """Call the cached strict decoder with CPython's vectorcall API."""
    cdef object argument
    cdef object validate = True
    cdef object result
    cdef PyObject *stack[2]

    if type(text) is not str:
        if not _is_valid_text_type(text):
            raise TypeError("text must be str")
        return _fallback(text)

    # stack points to local C storage holding strong Cython references for the
    # whole call. PyObject_Vectorcall borrows these arguments and the kwnames.
    argument = text
    stack[0] = <PyObject *>argument
    stack[1] = <PyObject *>validate
    try:
        result = PyObject_Vectorcall(
            <PyObject *>_cached_decode,
            stack,
            1,
            <PyObject *>_validate_kwnames,
        )
        return result
    except binascii.Error:
        return binascii.a2b_base64(text, strict_mode=True)
