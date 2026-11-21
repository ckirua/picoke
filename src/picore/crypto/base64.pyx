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


cdef extern from "Python.h":
    int PY_VERSION_HEX
    bint PyUnicode_IS_ASCII(object text)
    unsigned char *PyUnicode_1BYTE_DATA(object text)
    object PyUnicode_New(Py_ssize_t size, unsigned int maxchar)


cdef extern from "openssl/evp.h":
    int EVP_EncodeBlock(unsigned char *out, const unsigned char *data, int size)


cdef extern from *:
    """
    /* Compact ASCII only, with a nonempty multiple of four bytes. */
    static int picore_decode_quartets(const unsigned char *src, Py_ssize_t n,
                                     int pad, unsigned char *out) {
        static const unsigned char sextet[128] = {
            255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
            255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
            255,255,255,255,255,255,255,255,255,255,255, 62,255,255,255, 63,
             52, 53, 54, 55, 56, 57, 58, 59, 60, 61,255,255,255,255,255,255,
            255,  0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14,
             15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,255,255,255,255,255,
            255, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40,
             41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51,255,255,255,255,255
        };
        const unsigned char *end = src + n - (pad ? 4 : 0);
        for (; src < end; src += 4, out += 3) {
            unsigned a = sextet[src[0]], b = sextet[src[1]];
            unsigned c = sextet[src[2]], d = sextet[src[3]];
            if ((a | b | c | d) & 128) return 0;
            out[0] = (unsigned char)((a << 2) | (b >> 4));
            out[1] = (unsigned char)((b << 4) | (c >> 2));
            out[2] = (unsigned char)((c << 6) | d);
        }
        if (pad) {
            unsigned a = sextet[src[0]], b = sextet[src[1]];
            unsigned c = pad == 1 ? sextet[src[2]] : 0;
            if ((a | b | c) & 128) return 0;
            out[0] = (unsigned char)((a << 2) | (b >> 4));
            if (pad == 1) out[1] = (unsigned char)((b << 4) | (c >> 2));
        }
        return 1;
    }
    """
    bint picore_decode_quartets(const unsigned char *, Py_ssize_t, int, unsigned char *) noexcept


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
    cdef Py_ssize_t output_size
    cdef int padding = 0
    cdef const unsigned char *source
    cdef unsigned char *out
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
    output_size = (size // 4) * 3 - padding
    result = PyBytes_FromStringAndSize(NULL, output_size)
    out = <unsigned char *>PyBytes_AS_STRING(result)
    # No intermediate pad bytes or second validation pass. Delegate unusual
    # input to binascii so its version-specific strict error policy stays intact.
    if not picore_decode_quartets(source, size, padding, out):
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
