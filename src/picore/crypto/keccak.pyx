"""Ethereum-compatible Keccak-256 hashing."""

from cpython.bytes cimport PyBytes_Check
from cpython.object cimport PyObject_IsInstance

from Crypto.Hash import keccak


def keccak256(data: bytes) -> bytes:
    """Return the Keccak-256 digest of *data*."""
    if not PyBytes_Check(data) and not PyObject_IsInstance(data, bytes):
        raise TypeError("data must be bytes")
    return keccak.new(data=data, digest_bits=256).digest()
