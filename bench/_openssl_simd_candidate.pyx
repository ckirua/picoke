# cython: annotation_typing=False
"""Benchmark-only strict Base64 decoder using OpenSSL EVP.

Build separately for each target interpreter, linking against the pinned
experimental OpenSSL installation; see ``bench/README.md``. Run the benchmark
in fresh processes with that installation's lib directory in
``LD_LIBRARY_PATH``. This is not a production implementation.
"""

from cpython.object cimport PyObject_IsInstance
from cpython.bytes cimport PyBytes_AS_STRING, PyBytes_FromStringAndSize
from cpython.unicode cimport PyUnicode_GET_LENGTH
from libc.limits cimport INT_MAX
from libc.string cimport memcpy, memchr, strspn

cdef extern from "Python.h":
    bint PyUnicode_IS_ASCII(object text)
    unsigned char *PyUnicode_1BYTE_DATA(object text)

cdef extern from "openssl/evp.h":
    int EVP_DecodeBlock(unsigned char *out, const unsigned char *data, int size)


cdef object _strict_fallback(object text):
    # Keep Python imports out of the extension's import-time path: its custom
    # libcrypto may be loaded before any Python-level module imports are needed.
    import binascii
    return binascii.a2b_base64(text, strict_mode=True)

cdef inline bint _is_base64_char(unsigned char c):
    return (
        (c >= 65 and c <= 90) or (c >= 97 and c <= 122) or
        (c >= 48 and c <= 57) or c == 43 or c == 47
    )


cdef bint _valid_final_quartet(
    const unsigned char *source, Py_ssize_t start, int padding
):
    cdef int i
    for i in range(4 - padding):
        if not _is_base64_char(source[start + i]):
            return False
    for i in range(4 - padding, 4):
        if source[start + i] != 61:
            return False
    return True



cdef object _decode_exact(object text, bint use_memchr=False):
    cdef Py_ssize_t size = PyUnicode_GET_LENGTH(text)
    cdef Py_ssize_t payload_size
    cdef Py_ssize_t output_size
    cdef Py_ssize_t prefix_size
    cdef int padding = 0
    cdef int written
    cdef const unsigned char *source
    cdef unsigned char *out
    cdef unsigned char tail[3]
    cdef object result

    if size == 0:
        return b""
    if not PyUnicode_IS_ASCII(text) or size > INT_MAX or size % 4:
        return _strict_fallback(text)

    source = PyUnicode_1BYTE_DATA(text)
    if source[size - 1] == 61:
        padding = 1
        if source[size - 2] == 61:
            padding = 2
    payload_size = size - padding
    if use_memchr:
        # '=' is permitted only in the final quartet's padding positions.
        # EVP validates the alphabet elsewhere; its return count must equal
        # the exact output implied by the original, untrimmed input length.
        if padding:
            prefix_size = size - 4
            if (memchr(source, 61, <size_t>prefix_size) != NULL or
                    not _valid_final_quartet(source, prefix_size, padding)):
                return _strict_fallback(text)
        elif memchr(source, 61, <size_t>size) != NULL:
            return _strict_fallback(text)
    else:
        # EVP is permissive about whitespace and padding. Only send a pure
        # standard-alphabet prefix down the fast path.
        if strspn(
            <const char *>source,
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        ) != <size_t>payload_size:
            return _strict_fallback(text)

    output_size = (size // 4) * 3 - padding
    result = PyBytes_FromStringAndSize(NULL, output_size)
    out = <unsigned char *>PyBytes_AS_STRING(result)
    if padding:
        # Decode the final quartet to stack storage because EVP always writes
        # three bytes there, while the exact result omits one or two bytes.
        prefix_size = size - 4
        if prefix_size:
            written = EVP_DecodeBlock(out, source, <int>prefix_size)
            if written != (prefix_size // 4) * 3:
                return _strict_fallback(text)
        written = EVP_DecodeBlock(tail, source + prefix_size, 4)
        if written != 3:
            return _strict_fallback(text)
        memcpy(out + (prefix_size // 4) * 3, tail, 3 - padding)
    else:
        written = EVP_DecodeBlock(out, source, <int>size)
        if written != output_size:
            return _strict_fallback(text)
    return result


def b64decode_memchr(text: str) -> bytes:
    """Strict decoder variant using bounded memchr instead of strspn."""
    if type(text) is str:
        return _decode_exact(text, True)
    if not PyObject_IsInstance(text, str):
        raise TypeError("text must be str")
    import base64
    return base64.b64decode(text, validate=True)


def b64decode(text: str) -> bytes:
    """Strictly decode standard Base64, matching picore's public contract."""
    if type(text) is str:
        return _decode_exact(text)
    if not PyObject_IsInstance(text, str):
        raise TypeError("text must be str")
    import base64
    return base64.b64decode(text, validate=True)


def b64decode_ascii(text: str) -> bytes:
    """Strict decoding alias retained for the public picore API."""
    return b64decode(text)
