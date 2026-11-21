"""Compare the checkpoint crypto APIs with provider attribution arms."""

import base64
import binascii
import hashlib
import json
import statistics
import time

import pybase64
from Crypto.Hash import keccak
from btclib_secp256k1 import keys, recovery
from picore import crypto
from picop.hot import bytes_len


def rejected(fn, value):
    try:
        fn(value)
    except ValueError:
        return True
    return False


def strict(value):
    return binascii.a2b_base64(value, strict_mode=True)


def simd(value):
    return pybase64.b64decode(value, validate=True)


def measure(label, funcs, calls=1000):
    expected = funcs[0][1]()
    assert all(func() == expected for _, func in funcs), label
    samples = {name: [] for name, _ in funcs}
    for _, func in funcs:
        for _ in range(calls * 2):
            func()
    for sample in range(9):
        totals = dict.fromkeys(samples, 0)
        for block in range(10):
            for name, func in (funcs if (sample + block) % 2 == 0 else reversed(funcs)):
                start = time.perf_counter_ns()
                for _ in range(calls // 10):
                    func()
                totals[name] += time.perf_counter_ns() - start
        for name in samples:
            samples[name].append(totals[name] / calls)
    baseline = next(iter(samples.values()))
    print(json.dumps({"workload": label, "calls": calls, "ns": samples,
                      "median_ns": {k: statistics.median(v) for k, v in samples.items()},
                      "paired_median": {k: statistics.median(x / y for x, y in zip(v, baseline))
                                        for k, v in samples.items()}}))


class TextSubclass(str):
    pass


for size in (1533, 1536, 1539, 4096):
    encoded = base64.b64encode(b"x" * size).decode("ascii")
    for value in (encoded, TextSubclass(encoded)):
        measure(f"b64-{type(value).__name__}-{len(value)}", [
            ("picore", lambda: crypto.b64decode(value)), ("bin", lambda: strict(value)),
            ("simd", lambda: simd(value))])
    invalid = encoded[:-1] + "!"
    measure(f"b64-rejection-{len(invalid)}", [
        ("picore", lambda: rejected(crypto.b64decode, invalid)),
        ("bin", lambda: rejected(strict, invalid))])
for size in (0, 12, 32, 66, 96, 135, 136, 137, 160, 200, 272, 1024, 65536):
    value = b"x" * size
    measure(f"keccak-{size}", [("picore", lambda: crypto.keccak256(value)),
                               ("provider-bits", lambda: keccak.new(data=value, digest_bits=256).digest()),
                               ("provider-bytes", lambda: keccak.new(data=value, digest_bytes=32).digest())])
key = (1).to_bytes(32, "big")
digest = hashlib.sha256(b"picore checkpoint").digest()
r, s, v = crypto.sign_recoverable(key, digest)
compact = r.to_bytes(32, "big") + s.to_bytes(32, "big")
measure("secp-pubkey", [("picore", lambda: crypto.privkey_to_pubkey(key)),
                         ("provider", lambda: keys.pubkey_from_prvkey(key, compressed=False))], 300)


def provider_sign():
    compact, recid = recovery.sign(digest, key)
    return int.from_bytes(compact[:32], "big"), int.from_bytes(compact[32:], "big"), recid + 27


measure("secp-sign-checked", [("picore", lambda: crypto.sign_recoverable(key, digest)),
                               ("provider+output", provider_sign)], 300)
measure("secp-recover", [("picore", lambda: crypto.recover_pubkey(digest, r, s, v - 27)),
                          ("provider", lambda: recovery.recover(digest, compact, v - 27, compressed=False))], 300)
for size in (252, 253, 1024, 65536):
    message = b"x" * size
    magic = b"\x18Bitcoin Signed Message:\n"
    prefix = bytes((size,)) if size < 253 else (b"\xfd" + size.to_bytes(2, "little")
              if size < 65536 else b"\xfe" + size.to_bytes(4, "little"))
    def direct():
        return hashlib.sha256(hashlib.sha256(magic + prefix + message).digest()).digest()
    measure(f"bip137-hash-{size}", [("picore", lambda: crypto.bip137_signed_message_hash(message)),
                                   ("stdlib-precomputed-prefix", direct)])
measure("picop-python-exact-bytes-len", [("builtin", lambda: len(digest)),
                                        ("picop.hot", lambda: bytes_len(digest))], 20000)
