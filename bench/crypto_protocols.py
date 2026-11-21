"""Compare secp256k1 and Agent EIP-712 operations with a supplied baseline."""

import argparse
import hashlib
import os
import importlib.metadata
import platform
import sys

from crypto import _cpu, _legacy_package, _measure

def _bitcoin_hash(message: bytes) -> bytes:
    size = len(message)
    if size < 253:
        length = bytes([size])
    elif size <= 0xffff:
        length = b"\xfd" + size.to_bytes(2, "little")
    elif size <= 0xffffffff:
        length = b"\xfe" + size.to_bytes(4, "little")
    else:
        length = b"\xff" + size.to_bytes(8, "little")
    framed = b"\x18Bitcoin Signed Message:\n" + length + message
    return hashlib.sha256(hashlib.sha256(framed).digest()).digest()



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()
    if args.samples < 1 or args.iterations < 1 or args.warmups < 0:
        parser.error("samples and iterations must be positive and warmups non-negative")
    sys.path.insert(0, args.legacy_root)
    package = _legacy_package(args.legacy_root)
    old = importlib.import_module(f"{package}.crypto.curves.secp256k1")
    old_eip = importlib.import_module(f"{package}.crypto.signing.eip712")
    from picoke.crypto import secp256k1 as new
    from picoke.crypto.eip712 import eip712_hash_agent_message
    from picoke.crypto.bip137 import bip137_signed_message_hash

    secret = (1).to_bytes(32, "big")
    digest = bytes.fromhex("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08")
    pub = new.privkey_to_pubkey(secret)
    assert old.privkey_to_pubkey(secret) == pub
    old_address = old.privkey_to_address(secret)
    new_address = new.privkey_to_address(secret)
    if old_address != new_address:
        print(f"Address mismatch: legacy={old_address}, picoke={new_address}; excluded from timing")
    old_signature = old.sign_recoverable(secret, digest)
    new_signature = new.sign_recoverable(secret, digest)
    for signature in (old_signature, new_signature):
        r, s, v = signature
        for implementation in (old, new):
            if implementation.recover_pubkey(digest, r, s, v - 27) != pub:
                raise RuntimeError("cross-implementation secp256k1 recovery mismatch")
    r, s, v = new_signature
    message = b"Bitcoin signed message\x00binary"
    if bip137_signed_message_hash(message) != _bitcoin_hash(message):
        raise RuntimeError("Bitcoin framed hash mismatch")
    domain = {"name": "Exchange", "version": "1", "chainId": 42161,
              "verifyingContract": "0x0000000000000000000000000000000000000000"}
    source, connection_id = "a", bytes(range(32))
    expected = old_eip.eip712_hash_agent_message(domain, source, connection_id)
    assert eip712_hash_agent_message(domain, source, connection_id) == expected
    print(f"Python: {sys.version}; platform: {platform.platform()}; CPU: {_cpu()}")
    print(f"btclib-secp256k1: {importlib.metadata.version('btclib-secp256k1')}; eth-account: {importlib.metadata.version('eth-account')}")
    print(f"Legacy origin: {old.__file__}; picoke origin: {new.__file__}")
    print(f"Legacy root: {os.path.abspath(args.legacy_root)}; samples={args.samples}, iterations={args.iterations}, warmups={args.warmups}")
    print("Correctness checked before timing; legacy Bitcoin raw hash differs and is excluded.")
    if old_signature != new_signature:
        print("Signatures differ despite successful cross-recovery; signing excluded from timing.")
    cases = [
        ("secp256k1 public key", [("legacy", lambda: old.privkey_to_pubkey(secret)), ("picoke", lambda: new.privkey_to_pubkey(secret))]),
        ("secp256k1 recover", [("legacy", lambda: old.recover_pubkey(digest, r, s, v - 27)), ("picoke", lambda: new.recover_pubkey(digest, r, s, v - 27))]),
        ("EIP-712 Agent", [("legacy", lambda: old_eip.eip712_hash_agent_message(domain, source, connection_id)), ("picoke", lambda: eip712_hash_agent_message(domain, source, connection_id))]),
        ("Bitcoin framed double-SHA-256", [("stdlib reference", lambda: _bitcoin_hash(message)), ("picoke", lambda: bip137_signed_message_hash(message))]),
    ]
    if old_signature == new_signature:
        cases.append(("secp256k1 sign", [("legacy", lambda: old.sign_recoverable(secret, digest)), ("picoke", lambda: new.sign_recoverable(secret, digest))]))
    if old_address == new_address:
        cases.append(("Ethereum address", [("legacy", lambda: old.privkey_to_address(secret)), ("picoke", lambda: new.privkey_to_address(secret))]))
    for label, operations in cases:
        _measure(label, operations, samples=args.samples, iterations=args.iterations,
                 warmups=args.warmups, order="alternate")


if __name__ == "__main__":
    main()
