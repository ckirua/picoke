from typing import Literal

def bip137_signed_message_hash(message: bytes) -> bytes: ...
def bip137_sign_message(
    privkey: bytes,
    message: bytes,
    *,
    address_type: Literal["p2pkh-uncompressed", "p2pkh", "p2sh-p2wpkh", "p2wpkh"] = "p2pkh-uncompressed",
) -> bytes: ...
def bip137_verify_message(message: bytes, signature_b64: bytes | str, pubkey: bytes) -> bool: ...
