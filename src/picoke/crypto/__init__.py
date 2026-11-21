"""Encoding, hashing, and signing utilities."""

from .base64 import b64decode, b64decode_ascii, b64encode, b64encode_ascii
from .bip137 import bip137_signed_message_hash, bip137_sign_message, bip137_verify_message
from .eip712 import eip712_hash_agent_message, eip712_hash_full_message
from .ed25519 import ed25519_public_key, ed25519_sign, ed25519_verify
from .keccak import keccak256
from .secp256k1 import (
    privkey_to_address,
    privkey_to_pubkey,
    recover_pubkey,
    sign_recoverable,
)

__all__ = [
    "b64encode",
    "b64encode_ascii",
    "b64decode",
    "b64decode_ascii",
    "keccak256",
    "ed25519_public_key",
    "ed25519_sign",
    "ed25519_verify",
    "privkey_to_address",
    "privkey_to_pubkey",
    "recover_pubkey",
    "sign_recoverable",
    "eip712_hash_agent_message",
    "eip712_hash_full_message",
    "bip137_signed_message_hash",
    "bip137_sign_message",
    "bip137_verify_message",
]
