# Changelog

## Unreleased

## 0.1.0rc1

- Added Python APIs for Base64, Keccak-256, Ed25519, recoverable secp256k1, EIP-712, and Bitcoin signed messages.
- Native extensions target CPython 3.14 and newer. Python 3.15 is the development default; free-threaded support is not yet claimed.
- Short Base64 decoding validates and decodes in one native pass while preserving strict errors.
- Keccak-256 uses OpenSSL when available for exact `bytes`, accelerating canonical EIP-712 Agent hashing as well.
- Added `picore.ratelimit.TokenBucket`, a native asyncio limiter with weighted FIFO requests and exact nanosecond refill.
