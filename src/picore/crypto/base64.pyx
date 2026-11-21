"""Strict standard Base64 encoding and decoding helpers."""

import base64
import binascii
import pybase64

from cpython.bytes cimport (
    PyBytes_AS_STRING, PyBytes_CheckExact, PyBytes_FromStringAndSize,
    PyBytes_GET_SIZE,
)
from cpython.object cimport PyObject_IsInstance
from cpython.unicode cimport PyUnicode_DecodeASCII, PyUnicode_GET_LENGTH
from libc.limits cimport INT_MAX
from libc.string cimport memcpy, strspn


cdef extern from "Python.h":
    int PY_VERSION_HEX
    bint PyUnicode_IS_ASCII(object text)
    unsigned char *PyUnicode_1BYTE_DATA(object text)
    object PyUnicode_New(Py_ssize_t size, unsigned int maxchar)


cdef extern from "openssl/evp.h":
    int EVP_EncodeBlock(unsigned char *out, const unsigned char *data, int size)
    int EVP_DecodeBlock(unsigned char *out, const unsigned char *data, int size)


cdef object _encode_exact(bytes data, bint ascii_result):
    cdef Py_ssize_t size = PyBytes_GET_SIZE(data)
    cdef Py_ssize_t output_size
    cdef object result
    cdef unsigned char *out
    cdef int written

    if size == 0:
        return "" if ascii_result else b""

    # Host-specific measurements showed stdlib wins for large bytes outputs
    # on CPython 3.15. Avoid assuming this crossover on earlier releases;
    # future runtimes conservatively retain their own optimized stdlib path.
    if (PY_VERSION_HEX >= 0x03100000 or
            (PY_VERSION_HEX >= 0x030F0000 and not ascii_result and size >= 2048)):
        result = base64.b64encode(data)
        return result.decode("ascii") if ascii_result else result
    # The return value as well as the input length must fit an EVP int.
    # Delegation also leaves allocation-overflow behavior to the stdlib for
    # inputs outside this native range, without imposing a new size limit.
    if size > (INT_MAX // 4) * 3:
        result = base64.b64encode(data)
        return result.decode("ascii") if ascii_result else result
    output_size = ((size + 2) // 3) * 4
    if ascii_result:
        result = PyUnicode_New(output_size, 127)
        out = PyUnicode_1BYTE_DATA(result)
    else:
        result = PyBytes_FromStringAndSize(NULL, output_size)
        out = <unsigned char *>PyBytes_AS_STRING(result)
    # Both CPython allocations include the trailing NUL slot EVP writes.
    written = EVP_EncodeBlock(
        out, <const unsigned char *>PyBytes_AS_STRING(data), <int>size
    )
    if written != output_size:
        raise RuntimeError("OpenSSL Base64 encoding failed")
    return result


cdef object _decode_exact(object text):
    cdef Py_ssize_t size
    cdef Py_ssize_t payload_size
    cdef Py_ssize_t output_size
    cdef Py_ssize_t prefix_size
    cdef int padding = 0
    cdef int written
    cdef const unsigned char *source
    cdef unsigned char *out
    cdef unsigned char tail[3]
    cdef object result

    # libbase64's runtime-selected SIMD path combines validation and decoding.
    # Leave unusual or invalid inputs to binascii for its exact error policy.
    # The crossover is workload/CPU dependent; retain the previous path for
    # short strings and unmeasured future CPython releases.
    if (PY_VERSION_HEX < 0x03100000 and
            PyUnicode_GET_LENGTH(text) >= 2048):
        try:
            return pybase64.b64decode(text, validate=True)
        except binascii.Error:
            return binascii.a2b_base64(text, strict_mode=True)

    # Host-specific measurements showed stdlib wins at this size on 3.15;
    # use it conservatively for all future interpreter versions.
    if (PY_VERSION_HEX >= 0x03100000 or
            (PY_VERSION_HEX >= 0x030F0000 and
             PyUnicode_GET_LENGTH(text) >= 64)):
        return binascii.a2b_base64(text, strict_mode=True)
    if not PyUnicode_IS_ASCII(text):
        return binascii.a2b_base64(text, strict_mode=True)
    size = PyUnicode_GET_LENGTH(text)
    if size == 0:
        return b""
    if size > INT_MAX or size % 4:
        return binascii.a2b_base64(text, strict_mode=True)
    source = PyUnicode_1BYTE_DATA(text)
    if source[size - 1] == 61:
        padding = 1
        if source[size - 2] == 61:
            padding = 2
    payload_size = size - padding
    # EVP accepts whitespace and interior padding that strict binascii rejects.
    # Keep only the ordinary alphabet/padding shape native; let this runtime's
    # binascii decide every unusual form and supply its exact error message.
    if strspn(
        <const char *>source,
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    ) != <size_t>payload_size:
        return binascii.a2b_base64(text, strict_mode=True)
    output_size = (size // 4) * 3 - padding
    result = PyBytes_FromStringAndSize(NULL, output_size)
    out = <unsigned char *>PyBytes_AS_STRING(result)
    if padding:
        # EVP always writes three bytes per quartet, including padded bytes.
        # Decode the final quartet separately rather than overrunning the
        # exact-size Python result or discarding real trailing zero bytes.
        prefix_size = size - 4
        if prefix_size:
            written = EVP_DecodeBlock(out, source, <int>prefix_size)
            if written != (prefix_size // 4) * 3:
                return binascii.a2b_base64(text, strict_mode=True)
        written = EVP_DecodeBlock(tail, source + prefix_size, 4)
        if written != 3:
            return binascii.a2b_base64(text, strict_mode=True)
        memcpy(out + (prefix_size // 4) * 3, tail, 3 - padding)
    else:
        written = EVP_DecodeBlock(out, source, <int>size)
        if written != output_size:
            return binascii.a2b_base64(text, strict_mode=True)
    return result


def b64encode(data: bytes) -> bytes:
    """Encode bytes using the standard Base64 alphabet and padding."""
    if PyBytes_CheckExact(data):
        return _encode_exact(data, False)
    if not PyObject_IsInstance(data, bytes):
        raise TypeError("data must be bytes")
    return base64.b64encode(data)


def b64encode_ascii(data: bytes) -> str:
    """Encode bytes to a standard Base64 ASCII string."""
    cdef object encoded
    cdef bytes exact_encoded
    if PyBytes_CheckExact(data):
        return _encode_exact(data, True)
    encoded = b64encode(data)
    if PyBytes_CheckExact(encoded):
        exact_encoded = encoded
        return <str>PyUnicode_DecodeASCII(
            PyBytes_AS_STRING(exact_encoded), PyBytes_GET_SIZE(exact_encoded), NULL
        )
    return encoded.decode("ascii")


def b64decode(text: str) -> bytes:
    """Strictly decode a standard Base64 string."""
    if type(text) is str:
        return _decode_exact(text)
    if not PyObject_IsInstance(text, str):
        raise TypeError("text must be str")
    return base64.b64decode(text, validate=True)


def b64decode_ascii(text: str) -> bytes:
    """Strictly decode a standard Base64 string (legacy API spelling)."""
    return b64decode(text)
