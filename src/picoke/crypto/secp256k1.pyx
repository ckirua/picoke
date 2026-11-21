"""Ethereum-compatible secp256k1 key and recoverable-signature helpers.

Operations are provided by btclib-secp256k1/libsecp256k1; no curve
arithmetic is implemented here.
"""

from cpython.bytes cimport PyBytes_CheckExact, PyBytes_GET_SIZE
from cpython.object cimport PyObject_IsInstance

from btclib_secp256k1 import keys, recovery

from .keccak import keccak256

# secp256k1 group order.
_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


cdef inline Py_ssize_t _bytes_len(object value) except -1:
    if PyBytes_CheckExact(value):
        return PyBytes_GET_SIZE(value)
    return len(value)


def _check_bytes(name: str, value: bytes) -> None:
    if not PyObject_IsInstance(value, bytes):
        raise TypeError(f"{name} must be bytes")


def _private_key(privkey: bytes) -> bytes:
    _check_bytes("privkey", privkey)
    if _bytes_len(privkey) != 32:
        raise ValueError("privkey must be exactly 32 bytes")
    scalar = int.from_bytes(privkey, "big")
    if not 1 <= scalar < _ORDER:
        raise ValueError("privkey scalar is outside the secp256k1 range")
    return privkey


def privkey_to_pubkey(privkey: bytes) -> bytes:
    """Return the 65-byte uncompressed public key for a 32-byte secret scalar."""
    return keys.pubkey_from_prvkey(_private_key(privkey), compressed=False)


def privkey_to_address(privkey: bytes) -> str:
    """Return the lowercase Ethereum address for a 32-byte secret scalar."""
    return "0x" + keccak256(privkey_to_pubkey(privkey)[1:])[-20:].hex()


def sign_recoverable(privkey: bytes, msg_hash: bytes) -> tuple[int, int, int]:
    """Sign a 32-byte message hash; return ``(r, s, 27 + recovery_id)``."""
    key = _private_key(privkey)
    _check_bytes("msg_hash", msg_hash)
    if _bytes_len(msg_hash) != 32:
        raise ValueError("msg_hash must be exactly 32 bytes")
    compact, recid = recovery.sign(msg_hash, key)
    r = int.from_bytes(compact[:32], "big")
    s = int.from_bytes(compact[32:], "big")
    return r, s, 27 + recid


def recover_pubkey(msg_hash: bytes, r: int, s: int, recid: int) -> bytes:
    """Recover an uncompressed public key from a hash, ECDSA scalars and id."""
    _check_bytes("msg_hash", msg_hash)
    if _bytes_len(msg_hash) != 32:
        raise ValueError("msg_hash must be exactly 32 bytes")
    if not PyObject_IsInstance(r, int) or PyObject_IsInstance(r, bool):
        raise TypeError("r must be an int")
    if not PyObject_IsInstance(s, int) or PyObject_IsInstance(s, bool):
        raise TypeError("s must be an int")
    if not PyObject_IsInstance(recid, int) or PyObject_IsInstance(recid, bool):
        raise TypeError("recid must be an int")
    if not 1 <= r < _ORDER or not 1 <= s < _ORDER:
        raise ValueError("signature scalars must be in the secp256k1 range")
    if not 0 <= recid <= 3:
        raise ValueError("recid must be in [0, 3]")
    compact = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return recovery.recover(msg_hash, compact, recid, compressed=False)
