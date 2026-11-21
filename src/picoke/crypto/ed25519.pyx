"""Ed25519 public-key operations backed by PyCryptodome."""

from cpython.bytes cimport PyBytes_Check, PyBytes_CheckExact, PyBytes_GET_SIZE
from cpython.object cimport PyObject_IsInstance

from Crypto.Signature import eddsa


cdef inline Py_ssize_t _bytes_len(object value) except -1:
    if PyBytes_CheckExact(value):
        return PyBytes_GET_SIZE(value)
    return len(value)

cdef inline bint _zero_bytes(const unsigned char* value, Py_ssize_t start, Py_ssize_t end) noexcept:
    cdef Py_ssize_t i
    for i in range(start, end):
        if value[i] != 0:
            return False
    return True


cdef inline bint _le_lt(const unsigned char* value, const unsigned char* limit, bint point) noexcept:
    cdef Py_ssize_t i
    cdef unsigned char byte
    for i in range(31, -1, -1):
        byte = value[i]
        if point and i == 31:
            byte &= 0x7f
        if byte < limit[i]:
            return True
        if byte > limit[i]:
            return False
    return False


cdef inline bint _canonical_point(const unsigned char* value) noexcept:
    cdef unsigned char p[32]
    cdef unsigned char pm1[32]
    cdef Py_ssize_t i
    for i in range(32):
        p[i] = 0xff
    p[0] = 0xed
    p[31] = 0x7f
    if not _le_lt(value, p, True):
        return False
    for i in range(32):
        pm1[i] = p[i]
    pm1[0] -= 1
    if value[31] & 0x80:
        if value[0] == 1 and _zero_bytes(value, 1, 31) and value[31] == 0x80:
            return False
        if value[0] == pm1[0]:
            for i in range(1, 31):
                if value[i] != pm1[i]:
                    break
            else:
                if (value[31] & 0x7f) == pm1[31]:
                    return False
    return True


cdef inline bint _scalar_lt(const unsigned char* value) noexcept:
    cdef const unsigned char* limit = <const unsigned char*>(
        b"\xed\xd3\xf5\x5c\x1a\x63\x12\x58"
        b"\xd6\x9c\xf7\xa2\xde\xf9\xde\x14"
        b"\x00\x00\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x00\x00\x10")
    return _le_lt(value, limit, False)



def ed25519_public_key(seed: bytes) -> bytes:
    """Derive the raw Ed25519 public key corresponding to a 32-byte seed."""
    if not PyObject_IsInstance(seed, bytes):
        raise TypeError("seed must be bytes")
    if _bytes_len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")

    key = eddsa.import_private_key(seed)
    return key.public_key().export_key(format="raw")


def ed25519_sign(message: bytes, seed: bytes) -> bytes:
    """Sign a message using a 32-byte Ed25519 private seed."""
    if not PyObject_IsInstance(message, bytes):
        raise TypeError("message must be bytes")
    if not PyObject_IsInstance(seed, bytes):
        raise TypeError("seed must be bytes")
    if _bytes_len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")

    key = eddsa.import_private_key(seed)
    return eddsa.new(key, "rfc8032").sign(message)


def ed25519_verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """Return whether a pure Ed25519 signature is valid for the message."""
    if not PyObject_IsInstance(message, bytes):
        raise TypeError("message must be bytes")
    if not PyObject_IsInstance(signature, bytes):
        raise TypeError("signature must be bytes")
    if not PyObject_IsInstance(public_key, bytes):
        raise TypeError("public_key must be bytes")
    if _bytes_len(signature) != 64 or _bytes_len(public_key) != 32:
        return False

    cdef const unsigned char* sigdata
    cdef const unsigned char* keydata
    cdef object normalized_signature = signature
    cdef object normalized_key = public_key
    if not PyBytes_Check(signature):
        normalized_signature = bytes(signature)
    if not PyBytes_Check(public_key):
        normalized_key = bytes(public_key)
    # Subclasses can override __len__; never read beyond their actual buffers.
    if PyBytes_GET_SIZE(normalized_signature) != 64 or PyBytes_GET_SIZE(normalized_key) != 32:
        return False
    sigdata = <const unsigned char*>normalized_signature
    keydata = <const unsigned char*>normalized_key
    if not _scalar_lt(sigdata + 32) or not _canonical_point(sigdata) or not _canonical_point(keydata):
        return False
    try:
        key = eddsa.import_public_key(public_key)
        # RFC 8032's cofactored equation admits forgeries for small-order keys.
        if (key.pointQ * 8).is_point_at_infinity():
            return False
        eddsa.new(key, "rfc8032").verify(message, signature)
    except ValueError:
        return False
    return True
