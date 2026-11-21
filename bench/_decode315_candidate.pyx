"""Benchmark-only CPython 3.15 Base64 decoder dispatch candidates."""

import binascii
import pybase64

from cpython.object cimport PyObject_IsInstance
from cpython.unicode cimport PyUnicode_GET_LENGTH

cdef object _cached_decode = pybase64.b64decode

from picore.crypto.base64 import b64decode as _picore_b64decode


def baseline(text):
    """Faithful current 3.15 dispatch, including its public fallback."""
    if type(text) is not str:
        if not PyObject_IsInstance(text, str):
            raise TypeError("text must be str")
        return _picore_b64decode(text)
    cdef Py_ssize_t size = PyUnicode_GET_LENGTH(text)
    if size >= 4096:
        try:
            return pybase64.b64decode(text, validate=True)
        except binascii.Error:
            return binascii.a2b_base64(text, strict_mode=True)
    if size >= 64:
        return binascii.a2b_base64(text, strict_mode=True)
    return _picore_b64decode(text)


cdef inline object _candidate(object text, Py_ssize_t threshold, bint cached=False):
    if type(text) is not str:
        if not PyObject_IsInstance(text, str):
            raise TypeError("text must be str")
        return _picore_b64decode(text)
    cdef Py_ssize_t size = PyUnicode_GET_LENGTH(text)
    if size >= threshold:
        try:
            if cached:
                return _cached_decode(text, validate=True)
            return pybase64.b64decode(text, validate=True)
        except binascii.Error:
            return binascii.a2b_base64(text, strict_mode=True)
    if size >= 64:
        return binascii.a2b_base64(text, strict_mode=True)
    return _picore_b64decode(text)


def cutoff_1024(text):
    return _candidate(text, 1024)


def cutoff_2048(text):
    return _candidate(text, 2048)


def cutoff_2732(text):
    return _candidate(text, 2732)


def cutoff_3072(text):
    return _candidate(text, 3072)


def cutoff_3584(text):
    return _candidate(text, 3584)


def cached_2048(text):
    return _candidate(text, 2048, True)
