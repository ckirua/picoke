"""Independent key/address vectors and input-contract tests for secp256k1."""

import pytest

from picore.crypto.secp256k1 import (
    _ORDER,
    privkey_to_address,
    privkey_to_pubkey,
    recover_pubkey,
    sign_recoverable,
)


def test_private_key_one_public_key_and_ethereum_address() -> None:
    secret = (1).to_bytes(32, "big")
    assert privkey_to_pubkey(secret).hex() == (
        "0479be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"
        "483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8"
    )
    assert privkey_to_address(secret) == "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"


def test_recover_key_from_recoverable_signature() -> None:
    secret = (1).to_bytes(32, "big")
    digest = bytes.fromhex(
        "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
    )
    r, s, v = sign_recoverable(secret, digest)
    assert 1 <= r < _ORDER
    assert 1 <= s <= _ORDER // 2
    assert 27 <= v <= 30
    assert recover_pubkey(digest, r, s, v - 27) == privkey_to_pubkey(secret)

    assert recover_pubkey(digest, r, s, (v - 27) ^ 1) != privkey_to_pubkey(secret)


def test_recover_rejects_bool_and_preserves_argument_validation_order() -> None:
    digest = bytes(32)
    with pytest.raises(TypeError, match="msg_hash must be bytes"):
        recover_pubkey(bytearray(32), True, 1, 0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="r must be an int"):
        recover_pubkey(digest, True, 1, 0)
    with pytest.raises(TypeError, match="s must be an int"):
        recover_pubkey(digest, 1, False, 0)
    with pytest.raises(TypeError, match="recid must be an int"):
        recover_pubkey(digest, 1, 1, True)


def test_recover_accepts_arbitrary_precision_scalar_comparison() -> None:
    with pytest.raises(ValueError, match="signature scalars"):
        recover_pubkey(bytes(32), 1 << 256, 1, 0)



def test_bytes_type_check_propagates_dynamic_class_exception() -> None:
    class ClassLookupError:
        @property
        def __class__(self):
            raise RuntimeError("class lookup failed")

    with pytest.raises(RuntimeError, match="class lookup failed"):
        privkey_to_pubkey(ClassLookupError())  # type: ignore[arg-type]




def test_private_key_bytes_subclass_length_override_is_preserved() -> None:
    class ShortBytes(bytes):
        def __len__(self) -> int:
            return 31

    with pytest.raises(ValueError, match="exactly 32 bytes"):
        privkey_to_pubkey(ShortBytes(bytes(32)))
def test_wrong_input_types() -> None:
    with pytest.raises(TypeError):
        privkey_to_pubkey(bytearray(32))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sign_recoverable((1).to_bytes(32, "big"), bytearray(32))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        recover_pubkey(bytearray(32), 1, 1, 0)  # type: ignore[arg-type]
@pytest.mark.parametrize(
    "key", [b"", bytes(31), bytes(33), bytes(32), _ORDER.to_bytes(32, "big")]
)
def test_invalid_private_key(key: bytes) -> None:
    with pytest.raises(ValueError):
        privkey_to_pubkey(key)


@pytest.mark.parametrize("digest", [b"", bytes(31), bytes(33)])
def test_invalid_message_hash(digest: bytes) -> None:
    with pytest.raises(ValueError):
        sign_recoverable((1).to_bytes(32, "big"), digest)
    with pytest.raises(ValueError):
        recover_pubkey(digest, 1, 1, 0)


@pytest.mark.parametrize("r,s", [(0, 1), (1, 0), (_ORDER, 1), (1, _ORDER)])
def test_invalid_signature_scalars(r: int, s: int) -> None:
    with pytest.raises(ValueError):
        recover_pubkey(bytes(32), r, s, 0)


@pytest.mark.parametrize("recid", [-1, 4])
def test_invalid_recovery_id(recid: int) -> None:
    with pytest.raises(ValueError):
        recover_pubkey(bytes(32), 1, 1, recid)


def test_bitcoin_core_signature_recovers_independent_ethers_public_key() -> None:
    # Bitcoin Core signmessage fixture; SEC point independently derived with
    # ethers@6.15.0 SigningKey.computePublicKey.
    digest = bytes.fromhex(
        "0fcc896d51b842ea407bb1ec0eeb6d0ba46d1109037821a8f6c2737767ab1a1c"
    )
    compact = bytes.fromhex(
        "d6d59d6e1ee8f7919acbf6420bbc36ea29beb56391cc686feb17f0e7191b44"
        "802e15b26d48f330b3dd02c5c8e3a61919bd0a4134628bec16210cd1a46fd4f92d"
    )
    r = int.from_bytes(compact[:32], "big")
    s = int.from_bytes(compact[32:], "big")
    expected = bytes.fromhex(
        "04c150061989643d77162902b725409087959f15914649d4f06b6cc3f8c87bb2"
        "386abca96335972d53de4dbf431e7e1825f6377bea700948a802a46383193a95bb"
    )
    assert (r, s, 27 + 1) == (
        97172374564642588321231251157371128293032801461003038031484395053071004812416,
        20844726280357043039907947191067372231522893384906128172766374941593013909805,
        28,
    )
    assert recover_pubkey(digest, r, s, 1 ^ 1) != expected
    # ECDSA's (r, n-s) form is the same signature with recovery parity flipped.
    # The fixture and expected SEC point come from Bitcoin Core and ethers above.
    assert recover_pubkey(digest, r, _ORDER - s, 1 ^ 1) == expected


@pytest.mark.parametrize("secret", [(1).to_bytes(32, "big"), (_ORDER - 1).to_bytes(32, "big")])
def test_scalar_boundary_signing_is_deterministic_and_low_s(secret: bytes) -> None:
    digest = bytes.fromhex("42" * 32)
    first = sign_recoverable(secret, digest)
    assert first == sign_recoverable(secret, digest)
    r, s, v = first
    assert 1 <= r < _ORDER
    assert 1 <= s <= _ORDER // 2
    assert 27 <= v <= 30
    pubkey = privkey_to_pubkey(secret)
    assert recover_pubkey(digest, r, s, v - 27) == pubkey
    assert recover_pubkey(digest, r, _ORDER - s, (v - 27) ^ 1) == pubkey


@pytest.mark.parametrize("secret", [bytes(32), _ORDER.to_bytes(32, "big")])
def test_private_key_scalar_boundaries_rejected(secret: bytes) -> None:
    with pytest.raises(ValueError, match="scalar"):
        privkey_to_pubkey(secret)
