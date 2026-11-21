"""Ethereum-compatible Keccak-256 hashing."""

from cpython.bytes cimport (
    PyBytes_AS_STRING, PyBytes_Check, PyBytes_CheckExact,
    PyBytes_FromStringAndSize, PyBytes_GET_SIZE,
)
from cpython.object cimport PyObject_IsInstance

from Crypto.Hash import keccak


cdef extern from "openssl/evp.h":
    ctypedef struct EVP_MD:
        pass

cdef extern from "openssl/err.h":
    int ERR_set_mark() noexcept
    int ERR_pop_to_mark() noexcept
    void ERR_clear_error() noexcept

cdef extern from *:
    """
    #include <openssl/evp.h>
    #include <string.h>
    static int picore_digest_keccak256(const EVP_MD *md, const char *data,
                                       size_t size, char *out) {
        unsigned char digest[EVP_MAX_MD_SIZE];
        unsigned int written = 0;
        if (EVP_Digest(data, size, digest, &written, md, NULL) != 1 || written != 32)
            return 0;
        memcpy(out, digest, 32);
        return 1;
    }
    static EVP_MD *picore_fetch_keccak256(void) {
    #if OPENSSL_VERSION_NUMBER >= 0x30000000L && !defined(LIBRESSL_VERSION_NUMBER)
        EVP_MD *md = EVP_MD_fetch(NULL, "KECCAK-256", NULL);
        if (md != NULL && EVP_MD_get_size(md) != 32) {
            EVP_MD_free(md);
            return NULL;
        }
        return md;
    #else
        return NULL;
    #endif
    }
    static void picore_free_keccak256(EVP_MD *md) {
    #if OPENSSL_VERSION_NUMBER >= 0x30000000L && !defined(LIBRESSL_VERSION_NUMBER)
        EVP_MD_free(md);
    #else
        (void)md;
    #endif
    }
    """
    EVP_MD *picore_fetch_keccak256() noexcept
    void picore_free_keccak256(EVP_MD *) noexcept
    int picore_digest_keccak256(const EVP_MD *, const char *, size_t, char *) noexcept


cdef inline void _restore_error_queue(int marked) noexcept:
    if marked:
        ERR_pop_to_mark()
    else:
        ERR_clear_error()


cdef class _DigestMethod:
    cdef EVP_MD *md

    def __cinit__(self):
        cdef int marked = ERR_set_mark()
        self.md = picore_fetch_keccak256()
        _restore_error_queue(marked)

    def __dealloc__(self):
        picore_free_keccak256(self.md)


# Cache only the immutable algorithm handle, never a digest context or input.
cdef _DigestMethod _method = None


def keccak256(data: bytes) -> bytes:
    """Return the Keccak-256 digest of *data*."""
    global _method
    cdef char result[32]
    cdef int marked, ok

    if PyBytes_CheckExact(data):
        if _method is None:
            _method = _DigestMethod()
        if _method.md != NULL:
            marked = ERR_set_mark()
            ok = picore_digest_keccak256(_method.md, PyBytes_AS_STRING(data),
                                        PyBytes_GET_SIZE(data), result)
            _restore_error_queue(marked)
            if ok:
                return PyBytes_FromStringAndSize(result, 32)
    elif not PyBytes_Check(data) and not PyObject_IsInstance(data, bytes):
        raise TypeError("data must be bytes")

    # Preserve subclass behavior and support installations without EVP Keccak.
    return keccak.new(data=data, digest_bits=256).digest()
