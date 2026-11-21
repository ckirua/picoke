# picoke

picoke provides practical Python utilities. Its six Cython crypto extensions cover Base64, Keccak-256, Ed25519, recoverable secp256k1, EIP-712, and Bitcoin signed messages. The extensions use the CPython C API where needed and delegate underlying operations to OpenSSL libcrypto, pybase64, the standard library, PyCryptodome, btclib-secp256k1, and eth-account. [picop](https://github.com/ckirua/picop) is a runtime dependency. No independent security audit is implied.


## Installation

With CPython 3.15 (or 3.14), install from a checkout:

```bash
python3.15 -m pip install .
```

Source builds require a C compiler, the target interpreter's development
headers, and OpenSSL development headers (`openssl/evp.h`) and the libcrypto
library discoverable by the compiler and linker. The isolated build installs
Cython and picop's Cython declarations; it does not install these system
development dependencies. Base64 encoding and Keccak-256 link libcrypto;
dynamically linked builds need a compatible libcrypto shared library.
Large Base64 decodes use the `pybase64` runtime dependency on CPython 3.14
and 3.15; its libbase64 backend selects available SIMD implementations.

Wheels are specific to a CPython ABI and platform and include type stubs.
Cross-platform wheel compatibility and bundled OpenSSL libraries are not
claimed. There is no pure-Python fallback for the crypto extensions.

## Cryptography

```python
from picoke.crypto import (
    b64decode, b64encode, ed25519_public_key, ed25519_sign,
    ed25519_verify, keccak256,
)

assert b64encode(b"foo") == b"Zm9v"
assert b64decode("Zm9v") == b"foo"
digest = keccak256(b"hello")  # Keccak-256, not SHA3-256
seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
public_key = ed25519_public_key(seed)
signature = ed25519_sign(b"message", seed)
assert ed25519_verify(b"message", signature, public_key)
```

Generate production seeds with a cryptographically secure random source; the fixed seed above is only an example. Python `bytes` cannot be guaranteed erased from memory. Signing and key APIs accept bytes; seeds and public keys are 32 bytes, and signatures are 64 bytes. Verification returns `False` for invalid signatures, malformed or noncanonical point encodings, noncanonical scalars and small-order public keys; `TypeError` is raised for non-bytes arguments. Invalid seed lengths raise `ValueError`. Remaining verification follows PyCryptodome's cofactored RFC 8032 behavior; applications must authenticate public keys independently.

`b64encode_ascii` returns a `str`; `b64encode` returns `bytes`. Both decoders, `b64decode` and `b64decode_ascii`, accept ASCII `str` and return `bytes`. Decoding follows `base64.b64decode(..., validate=True)` semantics, raising `ValueError` or `binascii.Error` for non-ASCII text, invalid alphabet or rejected padding. Excess-padding behavior follows the running Python version. Base64 is an encoding, not encryption.

Short Base64 decodes combine validation and decoding in one native pass.
Keccak-256 uses the OpenSSL `KECCAK-256` provider for exact `bytes` when
available, and PyCryptodome otherwise, including for bytes subclasses.

### secp256k1 and Bitcoin messages

```python
from picoke.crypto import (
    bip137_sign_message, bip137_verify_message, privkey_to_address,
    privkey_to_pubkey, recover_pubkey, sign_recoverable,
)

private_key = (1).to_bytes(32, "big")  # Example only; never use as a real key.
public_key = privkey_to_pubkey(private_key)  # 65-byte uncompressed SEC key
address = privkey_to_address(private_key)  # lowercase Ethereum address
message = b"example"
signature = bip137_sign_message(private_key, message)
assert bip137_verify_message(message, signature, public_key)
```

`sign_recoverable(private_key, digest)` accepts a 32-byte digest and returns `(r, s, v)` with `v = 27 + recovery_id`; `recover_pubkey(digest, r, s, recovery_id)` accepts IDs 0–3. Bitcoin signing accepts the `address_type` keyword: `p2pkh-uncompressed` (default), `p2pkh`, `p2sh-p2wpkh`, or `p2wpkh`. Verification checks the recovered public key and header/key compression; it does **not** validate a Bitcoin address or prove that the header matches an address.

Bitcoin messages use the standard framed, double-SHA-256 digest. This deliberately differs from the legacy function of the same name, which hashes raw input once. Legacy Ethereum addresses also differ: legacy hashed the `0x04` SEC prefix; picoke hashes the 64-byte coordinates as Ethereum requires.

### EIP-712

`eip712_hash_full_message(full_message)` hashes an EIP-712 envelope with `types`, `primaryType`, `domain` and `message`; `eip712_hash_agent_message(domain, source, connection_id)` uses the legacy Agent schema but requires exactly 32 bytes for `connection_id`. Array values may be lists or tuples. `EIP712Domain` fields must be unique and in canonical order (`name`, `version`, `chainId`, `verifyingContract`, `salt`, omitting absent fields). Canonical Agent inputs use a fixed-schema Cython encoder; other Agent inputs and generic envelopes use eth-account. Mutually recursive type graphs that eth-account cannot resolve remain unsupported. Check the typed data and authenticate the domain before signing: a digest alone does not establish trust.

`eth-account` loads on the first call that needs the generic encoder, after picoke validates the input. Importing `picoke.crypto` or hashing a canonical Agent message does not load it.

`eth-account` currently constrains PyCryptodome to the 3.x series via `eth-keyfile`; picoke therefore requires PyCryptodome 3.24.1 or newer, below 4. This increases the dependency footprint and differs from the earlier 4.5 baseline; see the [benchmark notes](bench/README.md).

## Rate limiting

```python
from picoke.ratelimit import TokenBucket

# Allow a burst of 20 tokens, then refill continuously at 10 tokens per second.
bucket = TokenBucket(20, refill_amount=10)

async def handle_batch() -> None:
    await bucket.acquire(3)
```

`TokenBucket(capacity, *, refill_amount=1, refill_period_ns=1_000_000_000)` starts
full and uses a monotonic clock. Constructor settings are exact `int` values in
`1..2**63 - 1`; acquisition costs are exact `int` values in `1..capacity`.
Requests are served FIFO, so a large head request can delay smaller ones.
Cancellation while waiting does not consume tokens; use `asyncio.timeout()` to
bound a wait. Construct the bucket anywhere, but use it from one asyncio event
loop only. Importing `picoke.ratelimit` does not load the crypto providers.

The Cython implementation uses picop's native nanosecond clock and fixed-width
arithmetic when scaled capacity fits in 64 bits. Larger settings retain exact
integer arithmetic. It only allocates a lock when an acquisition has to wait.
See the [rate-limit benchmarks](bench/README.md#async-token-bucket) for
workloads and measured tradeoffs.

## Python support

Target CPython **3.14 and newer**, with **3.15 as the default** for development
and primary validation. Python versions below 3.14 are out of scope. The initial
compatibility matrix includes 3.14 and 3.15; newer versions need their own
validation. Free-threaded support is a goal, not a claim until tested.

## Documentation

- [Documentation index](docs/README.md)
- [Project plan](docs/PLAN.md)
- [Agent guidance](AGENTS.md)
