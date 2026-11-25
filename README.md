# picoke

picoke provides practical Python utilities. Its six Cython crypto extensions cover Base64, Keccak-256, Ed25519, recoverable secp256k1, EIP-712, and Bitcoin signed messages. The extensions use the CPython C API where needed and delegate underlying operations to OpenSSL libcrypto, pybase64, the standard library, PyCryptodome, btclib-secp256k1, and eth-account. [picop](https://github.com/ckirua/picop) is a runtime dependency. No independent security audit is implied.


## Installation

With CPython 3.15 (or 3.14), install from a checkout:

```bash
python3.15 -m pip install .
```

Source builds require C and C++ compilers, the target interpreter's development
headers, and OpenSSL development headers (`openssl/evp.h`) and the libcrypto
library discoverable by the compiler and linker. The isolated build installs
Cython, pybind11, and picop's Cython declarations; it does not install these
system development dependencies. Base64 encoding and Keccak-256 link
libcrypto; dynamically linked builds need a compatible libcrypto shared
library.
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

## Queues

```python
from picoke.queues import ByteRing

ring = ByteRing(capacity=1024, max_item_size=4096)
assert ring.try_push(b"message")
assert ring.try_pop() == b"message"
assert ring.try_pop() is None
```

`ByteRing` is an in-process FIFO for one producer and one consumer, which
may run on separate threads on GIL-enabled CPython. Neither method waits for
space or data.
`try_push` returns `False` when full, without evicting anything. `try_pop`
returns `None` when empty; `b""` is a valid item.

Capacity must be positive and maximum item size nonnegative. Both constructor
arguments must be exact `int` values that fit `Py_ssize_t`, and items must be
exact `bytes`. Oversized items raise `ValueError` even when the ring is full.
The ring allocates its reference slots upfront and retains immutable bytes
without copying them. Popped bytes remain valid after the slot is reused.
See the [queue benchmarks](bench/README.md#bounded-byte-fifo) for tradeoffs.

## Shared-memory IPC

```python
from picoke.ipc import SharedByteRing

ring = SharedByteRing.create(capacity=64, max_item_size=1024)
try:
    peer = SharedByteRing.attach(ring.name)  # May be in another process.
    try:
        assert ring.try_push(b"message")
        assert peer.try_pop() == b"message"
    finally:
        peer.close()
finally:
    ring.close()
    ring.unlink()
```

`SharedByteRing` is a Linux-only, bounded single-producer/single-consumer
queue backed by `/dev/shm`. Payloads are copied into and out of fixed-size
slots. `try_push` returns `False` when full; `try_pop` returns `None` when
empty, and `b""` is a valid item. File locks serialize transitions across
processes; calls can wait for lock acquisition even though they do not wait
for queue space or data. Closing detaches a handle; unlinking removes the
name but leaves existing attachments usable. After `fork`, attach a new handle
by name rather than using the inherited one. A process crash during an update
may leave the ring unusable. See the [IPC plan](docs/IPC_PLAN.md).

An experimental `AtomicSharedByteRing` is available separately on Linux
x86-64 with GIL-enabled CPython. It uses a distinct format and requires a
power-of-two capacity, one producer and one consumer. Payloads are copied;
no process-crash recovery is guaranteed. All participants and `/dev/shm`
must be trusted: truncating a live mapping can cause SIGBUS, and sufficient
shared-memory capacity is required. See the [IPC plan](docs/IPC_PLAN.md)
for validation status and lifecycle constraints.
Creation requires `O_TMPFILE` support on `/dev/shm` and a mounted `/proc`.
If the creator dies after publication, the named ring may require operator
cleanup.

The experimental `RecoverableAtomicSharedByteRing` and
`AutoRecoveringAtomicSharedByteRing` use a separate shared format and require
explicit `claim("producer")` and `claim("consumer")` calls before operating.
Claims are nonblocking and exclusive per role. After a crash, the manual
variant requires `recover(role)`; the automatic variant recovers when a
replacement calls `claim(role)`. Release roles when finished, or close the
handle to release them. A consumer killed after removal may lose an item
before the application processes it: recovery does not provide exactly-once
processing. These variants share the atomic ring's platform and trust
limitations and also require Linux OFD locks. See the
[IPC plan](docs/IPC_PLAN.md) for the recovery protocol.

## File I/O

```python
from contextlib import closing
from picoke.io.files import read_jsonl, write_jsonl

write_jsonl("events.jsonl", [{"event": "started"}, {"event": "finished"}])
with closing(read_jsonl("events.jsonl")) as records:
    print(next(records))
```

`read_json` and `write_json` handle one UTF-8 JSON document. `read_jsonl`
streams one UTF-8 JSON value per line and raises `JSONLDecodeError` with
the physical line number for invalid UTF-8, invalid JSON, or blank lines.
`write_jsonl` overwrites the file with compact, LF-terminated records.

Use `iter_lines(path, strip=True, skip_empty=True, comment_prefix="#")`
for filtered text, or wrap it in `list()` for a snapshot. Close line and
JSONL iterators explicitly when stopping early, as above.

`read_toml` is available without extras; `write_toml` needs `picoke[toml]`.
`read_yaml` and `write_yaml` use PyYAML's safe document APIs and need
`picoke[yaml]`. Install both extras with `python3.15 -m pip install 'picoke[files]'`.

Use `picoke.io.files.zip.read_zip` for bounded reads of selected archive
members. It returns names and bytes without extracting files.

Writes are not atomic. See the [file I/O contracts](docs/IO_FILES_PLAN.md)
for error handling, ZIP limits, and performance results.

## Structured logging

`picoke.logging.JSONFormatter` works with standard-library logging handlers.

```python
import logging

from picoke.logging import JSONFormatter

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logger = logging.getLogger("app")
logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.info("processed %d records", 3, extra={"fields": {"worker": "ingest"}})
```

Each record becomes one compact JSON object with a UTC millisecond `timestamp`,
`level`, `logger`, and `message`. Exception and stack text are included when
available. Put structured data in `extra={"fields": ...}`: mappings with string
keys, lists, strings, booleans, integers, finite floats, and `None` values.
Unsupported values and circular containers raise during formatting; handlers
retain their usual error policy. The handler owns the destination, newline,
and flushing.
See the [logging benchmarks](bench/README.md#structured-json-logging) for
the cost of formatting and handler delivery.

For bounded background delivery to a binary stream:

```python
import logging
from picoke.logging import BackgroundJSONHandler, BackgroundWriter

with open("events.jsonl", "ab") as stream:
    with BackgroundWriter(stream, capacity=256, max_item_size=65536) as writer:
        handler = BackgroundJSONHandler(writer)
        logger = logging.getLogger("background-example")
        logger.addHandler(handler)
        try:
            logger.setLevel(logging.INFO)
            logger.info("started", extra={"fields": {"city": "雪"}})
            handler.flush()
        finally:
            logger.removeHandler(handler)
            handler.close()
```

`BackgroundWriter` defaults to 1,024 queued items and a maximum item size of
1,048,576 bytes. Capacity must be a positive exact `int`; the maximum size
must be a nonnegative exact `int`. Both must fit `Py_ssize_t`. One additional
item can be in flight. `write(data, block=True)` accepts exact `bytes` and
waits for capacity; `block=False` returns `False` when full. Oversized items
raise `ValueError`, even when the queue is full. Accepted items are delivered
in FIFO admission order. Queue slots retain the original bytes without
copying; formatting, encoding, short writes, stream buffering and the
operating system may copy. On a short write the stream must accept a
`memoryview` for the remainder.

`flush()` waits for earlier admissions and flushes the stream. `close()`
stops admission, drains and flushes but never closes the caller-owned stream;
the handler also leaves its writer open when closed. A blocked stream can
make these operations wait indefinitely; flushing does not guarantee
durability. A sink failure terminates delivery and is chained as the cause
of `RuntimeError` on subsequent writer operations or handler flush, potentially
after the logging call. Construct a new writer after a fork. GIL-enabled
CPython is required; free-threaded operation has not been validated.

### Append-only binary logs

Install the optional codec with `python3.15 -m pip install 'picoke[binary-logging]'`.
`BackgroundBinaryHandler` writes versioned, length-prefixed MessagePack
records with CRC-32 checksums through the same background writer:

```python
import logging
from picoke.binary_logging import BackgroundBinaryHandler, iter_binary_records
from picoke.logging import BackgroundWriter

logger = logging.getLogger("binary-example")
logger.setLevel(logging.INFO)
with open("events.pklog", "ab") as stream:
    with BackgroundWriter(stream, max_item_size=1048588) as writer:
        handler = BackgroundBinaryHandler(writer)
        logger.addHandler(handler)
        try:
            logger.info("started", extra={"fields": {"city": "雪"}})
            handler.flush()
        finally:
            logger.removeHandler(handler)
            handler.close()

with open("events.pklog", "rb") as stream:
    for event in iter_binary_records(stream):
        print(event)
```

The framing adds 12 bytes to each MessagePack payload; the writer's maximum
item size must include that overhead. The reader bounds payloads at 1 MiB by
default. By default, incomplete frames raise `BinaryLogError`;
`recover_truncated=True` ignores only an incomplete final frame. Checksum,
version, size and decoding failures still raise. Repair a truncated tail
before appending more frames. The handler and reader do not close their
caller-owned writer or stream.

MessagePack integers are limited to its signed-64/unsigned-64 range. CRC-32
detects accidental corruption; it does not authenticate records. Use one
writer per file; append mode does not coordinate concurrent processes or make
multiple writes atomic. A flush is not `fsync` and does not guarantee crash
durability. Encoding, framing and downstream I/O may copy data.

## Python support

Target CPython **3.14 and newer**, with **3.15 as the default** for development
and primary validation. Python versions below 3.14 are out of scope. The initial
compatibility matrix includes 3.14 and 3.15; newer versions need their own
validation. Free-threaded support is a goal, not a claim until tested.

## Documentation

- [Documentation index](docs/README.md)
- [Project plan](docs/PLAN.md)
- [Agent guidance](AGENTS.md)
