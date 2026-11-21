"""Bitcoin signed-message hashing and BIP-137 recoverable signatures.

Unlike the historical implementation, the hash here follows Bitcoin's
framed, double-SHA-256 message-digest convention.
"""

import base64
import binascii
import hashlib
from typing import Literal

from libc.stdint cimport uint64_t
from cpython.bytes cimport PyBytes_Check, PyBytes_FromStringAndSize, PyBytes_GET_SIZE
from cpython.object cimport PyObject_IsInstance

from .secp256k1 import recover_pubkey, sign_recoverable

_MESSAGE_MAGIC = b"\x18Bitcoin Signed Message:\n"
_SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141




cdef bytes _compact_size(Py_ssize_t size):
    cdef unsigned char encoded[9]
    cdef Py_ssize_t width, i
    cdef uint64_t value = <uint64_t>size
    if size < 0xFD:
        encoded[0] = <unsigned char>size
        width = 1
    elif size <= 0xFFFF:
        encoded[0] = 0xFD
        width = 3
    elif size <= 0xFFFFFFFF:
        encoded[0] = 0xFE
        width = 5
    else:
        encoded[0] = 0xFF
        width = 9
    if width > 1:
        for i in range(1, width):
            encoded[i] = <unsigned char>(value & 0xFF)
            value >>= 8
    return PyBytes_FromStringAndSize(<const char *>encoded, width)


def bip137_signed_message_hash(message: bytes) -> bytes:
    """Return Bitcoin's double-SHA-256 hash of the framed message.

    This deliberately differs from the legacy raw, single-SHA-256 hash.
    """
    if not PyObject_IsInstance(message, bytes):
        raise TypeError("message must be bytes")
    if PyBytes_Check(message):
        # Prefix and payload must describe the same underlying bytes, not a
        # subclass's virtual length or reflected addition.
        framed = bytes.__add__(_MESSAGE_MAGIC + _compact_size(PyBytes_GET_SIZE(message)), message)
    else:
        framed = _MESSAGE_MAGIC + _compact_size(len(message)) + message
    return hashlib.sha256(hashlib.sha256(framed).digest()).digest()


def bip137_sign_message(
    privkey: bytes,
    message: bytes,
    *,
    address_type: Literal["p2pkh-uncompressed", "p2pkh", "p2sh-p2wpkh", "p2wpkh"] = "p2pkh-uncompressed",
) -> bytes:
    """Sign a Bitcoin message using the BIP-137 header for the address type.

    The default is an uncompressed P2PKH key.
    """
    if not PyObject_IsInstance(privkey, bytes):
        raise TypeError("privkey must be bytes")
    if not PyObject_IsInstance(message, bytes):
        raise TypeError("message must be bytes")
    headers = {"p2pkh-uncompressed": 27, "p2pkh": 31, "p2sh-p2wpkh": 35, "p2wpkh": 39}
    if address_type not in headers:
        raise ValueError("unsupported BIP-137 address type")
    digest = bip137_signed_message_hash(message)
    r, s, v = sign_recoverable(privkey, digest)
    recid = v - 27
    if not 0 <= recid <= 3:
        raise ValueError("secp256k1 provider returned invalid recovery id")
    compact = bytes((headers[address_type] + recid,)) + r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.b64encode(compact)


def bip137_verify_message(
    message: bytes, signature_b64: bytes | str, pubkey: bytes
) -> bool:
    """Verify compact BIP-137 signatures against a secp256k1 public key.

    The supplied key must be a 65-byte uncompressed or 33-byte compressed
    SEC public key. BIP-137 header address-type bits are validated; since this
    API accepts a public key rather than an address, verification compares the
    recovered point and does not assert an address/hash type.
    """
    if not PyObject_IsInstance(message, bytes):
        raise TypeError("message must be bytes")
    if not PyObject_IsInstance(signature_b64, (bytes, str)):
        raise TypeError("signature_b64 must be bytes or str")
    if not PyObject_IsInstance(pubkey, bytes):
        raise TypeError("pubkey must be bytes")
    if len(pubkey) not in (33, 65):
        return False
    if (len(pubkey) == 33 and pubkey[0] not in (2, 3)) or (
        len(pubkey) == 65 and pubkey[0] != 4
    ):
        return False
    # A 65-byte compact signature needs 88 Base64 characters. Keep subclass
    # dispatch on the generic decoding path.
    if (type(signature_b64) is bytes or type(signature_b64) is str) and len(signature_b64) > 88:
        return False
    try:
        raw = base64.b64decode(signature_b64, validate=True)
    except (ValueError, binascii.Error, UnicodeError):
        return False
    if len(raw) != 65:
        return False
    header = raw[0]
    if not 27 <= header <= 42:
        return False
    adjusted = header - 27
    compressed = adjusted >= 4
    # BIP-137 reserves four ids per address type; all map to recovery ids 0..3.
    recid = adjusted & 3
    if compressed != (len(pubkey) == 33):
        return False
    r = int.from_bytes(raw[1:33], "big")
    s = int.from_bytes(raw[33:], "big")
    if r == 0 or s == 0 or r >= _SECP256K1_ORDER or s >= _SECP256K1_ORDER:
        return False
    try:
        recovered = recover_pubkey(bip137_signed_message_hash(message), r, s, recid)
    except ValueError:
        return False
    return recovered == pubkey if len(pubkey) == 65 else _compress(recovered) == pubkey


def _compress(pubkey: bytes) -> bytes:
    if len(pubkey) != 65 or pubkey[0] != 4:
        return b""
    return bytes((2 | (pubkey[64] & 1),)) + pubkey[1:33]
