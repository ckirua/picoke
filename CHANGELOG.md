# Changelog

## Unreleased

- Added `picoke.ipc.SharedByteRing`, a bounded Linux shared-memory SPSC byte queue with explicit close and unlink.
- Accelerated shared-memory slot operations with a checked pybind11 extension while retaining process locks and copied-byte ownership.
- Added experimental `picoke.ipc.AtomicSharedByteRing`, a copied-byte SPSC ring for Linux x86-64 GIL-enabled CPython with a distinct atomic shared-memory format.
- Added experimental recoverable atomic SPSC rings with explicit role ownership and manual or automatic recovery after process termination.

## 0.1.0rc3

- Added `picoke.queues.ByteRing`, a bounded single-producer, single-consumer FIFO for immutable bytes on GIL-enabled CPython.
- Added JSON, streaming JSON Lines, filtered text lines, TOML, and YAML file helpers under `picoke.io.files`. TOML writing and YAML use optional extras.
- Added bounded ZIP member reads under `picoke.io.files.zip`, without filesystem extraction.
- JSON Lines writes reuse one encoder per nonempty call; empty inputs still truncate without creating an encoder.
- Added `picoke.logging.JSONFormatter` for compact structured events with standard-library logging handlers.
- Added a bounded background binary writer and structured JSON logging handler with explicit flush, shutdown, and error propagation.
- Added optional MessagePack-framed append-only structured logging with checksums and a bounded streaming reader.
- Reduced copying in MessagePack log framing and optimized complete-frame stream reads without changing the file format.
- Moved binary-log field materialization and validation into a Cython extension while preserving the framed format and field validation semantics.
- Accelerated finite-float validation in binary logging without changing the wire format or accepted numeric values.
- Accelerated fragmented binary-log reads and validation of wide record maps while preserving stream and key-type behavior.

## 0.1.0rc2

- Renamed the distribution and import package from `picore` to `picoke`.

## 0.1.0rc1

- Added Python APIs for Base64, Keccak-256, Ed25519, recoverable secp256k1, EIP-712, and Bitcoin signed messages.
- Native extensions target CPython 3.14 and newer. Python 3.15 is the development default; free-threaded support is not yet claimed.
- Short Base64 decoding validates and decodes in one native pass while preserving strict errors.
- Keccak-256 uses OpenSSL when available for exact `bytes`, accelerating canonical EIP-712 Agent hashing as well.
- Added `picore.ratelimit.TokenBucket`, a native asyncio limiter with weighted FIFO requests and exact nanosecond refill.
