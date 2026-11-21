import base64
import hashlib

import pytest

from picore.crypto.bip137 import (
    bip137_signed_message_hash,
    bip137_sign_message,
    bip137_verify_message,
)
from picore.crypto.secp256k1 import privkey_to_pubkey


def test_bitcoin_core_message_hash_vectors_and_compactsize_boundary():
    # Bitcoin Core's Bitcoin Signed Message framing: magic, CompactSize length,
    # then payload, followed by two SHA-256 rounds.
    assert bip137_signed_message_hash(b"").hex() == (
        "80e795d4a4caadd7047af389d9f7f220562feb6196032e2131e10563352c4bcc"
    )
    assert bip137_signed_message_hash(b"a" * 252).hex() == (
        "b7b164ef991d52735c6bb888642ad7eb6b6939dc984a7fceff4376be041d142f"
    )
    assert bip137_signed_message_hash(b"a" * 253).hex() == (
        "df167ad249ff5837e6acada677118b2ecc6757ab4cdade39caead99ef0220230"
    )
    assert bip137_signed_message_hash(b"") != hashlib.sha256(b"").digest()


@pytest.mark.parametrize(
    ("size", "prefix"),
    ((65535, b"\xfd\xff\xff"), (65536, b"\xfe\x00\x00\x01\x00")),
)
def test_compactsize_two_byte_boundary_against_stdlib_sha256(size, prefix):
    message = b"a" * size
    framed = b"\x18Bitcoin Signed Message:\n" + prefix + message
    expected = hashlib.sha256(hashlib.sha256(framed).digest()).digest()
    assert bip137_signed_message_hash(message) == expected


def test_message_subclass_hashes_the_serialized_payload():
    class BitcoinText(bytes):
        def __len__(self):
            return len(self.decode("utf-8"))

    message = BitcoinText("雪☃".encode("utf-8"))
    assert bip137_signed_message_hash(message) == bip137_signed_message_hash(bytes(message))


def test_sign_verify_uncompressed_header():
    private_key = (1).to_bytes(32, "big")
    public_key = privkey_to_pubkey(private_key)
    message = b"Bitcoin signed message test"
    signature = bip137_sign_message(private_key, message)
    raw = base64.b64decode(signature, validate=True)
    assert len(raw) == 65 and 27 <= raw[0] <= 30
    assert bip137_verify_message(message, signature, public_key)
    assert not bip137_verify_message(message + b"!", signature, public_key)


def test_bitcoin_core_headers_reuse_exact_compact_signature():
    # Same Bitcoin Core (r, s, recovery id), with only the BIP-137 header changed.
    key = bytes.fromhex("d2b8a0116d641fe7d3036f8464628fb595b480414c13a301b3d4038c811c28b0")
    message = b"This is just a test message"
    core = base64.b64decode(
        b"INbVnW4e6PeRmsv2Qgu8NuopvrVjkcxob+sX8OcZG0SALhWybUjzMLPdAsXI46YZGb0KQTRii+wWIQzRpG/U+S0=",
        validate=True,
    )
    pubkey = privkey_to_pubkey(key)
    compressed = bytes((2 | (pubkey[-1] & 1),)) + pubkey[1:33]
    for base in (27, 31, 35, 39):
        compact = bytes((base + (core[0] - 31),)) + core[1:]
        encoded = base64.b64encode(compact)
        expected = pubkey if base == 27 else compressed
        assert bip137_verify_message(message, encoded, expected)
        if base in (31, 35, 39):
            # Verification is against a public key, not a claimed address hash.
            assert bip137_verify_message(message, encoded, compressed)


def test_bitcoin_core_signature_accepts_low_and_high_s_with_matching_parity():
    # Independent Bitcoin Core signmessage fixture and key from the tests above.
    key = bytes.fromhex("d2b8a0116d641fe7d3036f8464628fb595b480414c13a301b3d4038c811c28b0")
    message = b"This is just a test message"
    compact = base64.b64decode(
        b"INbVnW4e6PeRmsv2Qgu8NuopvrVjkcxob+sX8OcZG0SALhWybUjzMLPdAsXI46YZGb0KQTRii+wWIQzRpG/U+S0=",
        validate=True,
    )
    pubkey = privkey_to_pubkey(key)
    compressed = bytes((2 | (pubkey[-1] & 1),)) + pubkey[1:33]
    order = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    s = int.from_bytes(compact[33:], "big")
    high_s = (order - s).to_bytes(32, "big")
    assert s <= order // 2 < int.from_bytes(high_s, "big")

    assert bip137_verify_message(message, base64.b64encode(compact), compressed)
    # Flip recovery parity within the compressed P2PKH header class.
    high_s_header = 31 + ((compact[0] - 31) ^ 1)
    high_s_compact = bytes((high_s_header,)) + compact[1:33] + high_s
    assert bip137_verify_message(message, base64.b64encode(high_s_compact), compressed)
    assert not bip137_verify_message(
        message,
        base64.b64encode(bytes((compact[0],)) + compact[1:33] + high_s),
        compressed,
    )


def test_binary_and_nul_message_signing():
    private_key = (1).to_bytes(32, "big")
    public_key = privkey_to_pubkey(private_key)
    for message in (b"\0", b"before\0after", bytes(range(256))):
        signature = bip137_sign_message(private_key, message)
        assert bip137_verify_message(message, signature, public_key)
        assert not bip137_verify_message(message + b"\0", signature, public_key)

def test_bitcoin_core_signmessagewithprivkey_vector():
    # Bitcoin Core test/functional/rpc_signmessagewithprivkey.py (compressed WIF).
    key = bytes.fromhex("d2b8a0116d641fe7d3036f8464628fb595b480414c13a301b3d4038c811c28b0")
    message = b"This is just a test message"
    expected = b"INbVnW4e6PeRmsv2Qgu8NuopvrVjkcxob+sX8OcZG0SALhWybUjzMLPdAsXI46YZGb0KQTRii+wWIQzRpG/U+S0="
    assert bip137_sign_message(key, message, address_type="p2pkh") == expected
    public_key = privkey_to_pubkey(key)
    compressed = bytes((2 | (public_key[-1] & 1),)) + public_key[1:33]
    assert bip137_verify_message(message, expected, compressed)


@pytest.mark.parametrize(
    ("address_type", "base"),
    [("p2pkh-uncompressed", 27), ("p2pkh", 31), ("p2sh-p2wpkh", 35), ("p2wpkh", 39)],
)
def test_address_type_headers_and_key_representation(address_type, base):
    private_key = (1).to_bytes(32, "big")
    public_key = privkey_to_pubkey(private_key)
    compressed = bytes((2 | (public_key[-1] & 1),)) + public_key[1:33]
    signature = bip137_sign_message(private_key, b"hello", address_type=address_type)
    assert base <= base64.b64decode(signature)[0] <= base + 3
    expected_key = public_key if base == 27 else compressed
    assert bip137_verify_message(b"hello", signature, expected_key)
    assert not bip137_verify_message(b"hello", signature, compressed if base == 27 else public_key)


def test_verification_rejects_malformed_signatures_and_keys():
    key = privkey_to_pubkey((1).to_bytes(32, "big"))
    assert not bip137_verify_message(b"x", b"%%%", key)
    assert not bip137_verify_message(b"x", base64.b64encode(b"short"), key)
    for header in (26, 43):
        assert not bip137_verify_message(b"x", base64.b64encode(bytes((header,)) + b"\0" * 64), key)
    assert not bip137_verify_message(b"x", "☃", key)
    valid = bip137_sign_message((1).to_bytes(32, "big"), b"x")
    for scalar_position in (1, 33):
        for invalid_scalar in (0, 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141):
            compact_parts = [bytearray(32), bytearray(32)]
            compact_parts[(scalar_position - 1) // 32][:] = invalid_scalar.to_bytes(32, "big")
            compact_parts[1 - (scalar_position - 1) // 32][:] = (1).to_bytes(32, "big")
            compact = bytes((31,)) + bytes(compact_parts[0]) + bytes(compact_parts[1])
            assert not bip137_verify_message(b"x", base64.b64encode(compact), key)
    assert not bip137_verify_message(b"x", valid, b"\x05" + key[1:])
    assert not bip137_verify_message(b"x", valid, b"\x02" + key[1:33])
    tampered = bytearray(base64.b64decode(valid))
    tampered[-1] ^= 1
    assert not bip137_verify_message(b"x", base64.b64encode(tampered), key)
    assert not bip137_verify_message(b"changed", valid, key)


@pytest.mark.parametrize(
    "args",
    [
        (bytearray(b"x"),),
    ],
)
def test_hash_rejects_non_bytes(args):
    with pytest.raises(TypeError):
        bip137_signed_message_hash(*args)

def test_verification_accepts_text_signature_and_checks_key_length_first():
    key = privkey_to_pubkey((1).to_bytes(32, "big"))
    signature = bip137_sign_message((1).to_bytes(32, "big"), b"x").decode("ascii")
    assert bip137_verify_message(b"x", signature, key)

    class RaisingAsciiStr(str):
        def encode(self, *args, **kwargs):
            raise RuntimeError("signature encoding reached")

    assert not bip137_verify_message(b"x", RaisingAsciiStr("not base64"), b"short")
    with pytest.raises(RuntimeError, match="signature encoding reached"):
        bip137_verify_message(b"x", RaisingAsciiStr("not base64"), key)
