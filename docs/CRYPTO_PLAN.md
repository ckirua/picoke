# Cryptography: legacy parity and performance plan

## Goal and status

The six capabilities use Cython extensions. Base64 uses pybase64/libbase64 for large exact strings on CPython 3.14 and 3.15, OpenSSL EVP for eligible smaller inputs, and strict stdlib paths otherwise. Dispatch thresholds are host/workload-specific; see [measurements and raw results](../bench/README.md). CPython 3.16+ conservatively uses stdlib. Compilation alone establishes neither parity, a speedup nor an independent security audit.

## Inventory and ownership

| Workstream | Legacy public APIs | Current status / dependency |
|---|---|---|
| Base64 | `b64encode`, `b64encode_ascii`, `b64decode`, `b64decode_ascii` | Cython wrapper over pybase64/libbase64 and OpenSSL EVP with strict stdlib fallback; exact bytes/str contract and strict errors; legacy trailing-NUL decode defect is not preserved. |
| Keccak | `keccak256` | Present using PyCryptodome; compare with legacy Cython and published Ethereum vectors, not SHA3. |
| Ed25519 | `ed25519_public_key`, `ed25519_sign`, `ed25519_verify` | PyCryptodome with canonical point and scalar checks and small-order public-key rejection; otherwise the provider's cofactored verification policy. |
| secp256k1 | `privkey_to_pubkey`, `privkey_to_address`, `sign_recoverable`, `recover_pubkey` | Implemented using btclib-secp256k1/libsecp256k1. Legacy address derivation hashes the 65-byte SEC key including its prefix; picore hashes only coordinates, matching Ethereum. |
| EIP-712 | `eip712_hash_full_message`, `eip712_hash_agent_message` | Fixed-schema encoder for canonical Agent inputs; generic and fallback inputs use eth-account with structural validation, canonical domain field ordering and non-mutating tuple-array normalization. Mutually recursive type graphs that the provider cannot resolve remain unsupported. |
| Bitcoin messages | `bip137_signed_message_hash`, `bip137_sign_message`, `bip137_verify_message` | Implemented with Bitcoin message framing and double SHA-256, unlike legacy single SHA-256 of raw input. Verification compares public keys, not addresses; signing supports all BIP-137 header classes. |

The [BIP-137 specification](https://github.com/bitcoin/bips/blob/master/bip-0137.mediawiki) describes header ranges 27–42 and its verification example uses a formatted Bitcoin message and double SHA-256. The legacy raw single-SHA-256 behavior is not interchangeable; migration and interoperability must be specified explicitly.

Legacy uses OpenSSL EVP for Base64, hand-coded Cython Keccak and Python-big-integer curve arithmetic. The latter is not established constant-time; performance and security must not be inferred from Cython. Legacy EIP-712 performs permissive coercions, truncation and padding; characterize those differences against normative vectors. The legacy code is BSD-3-Clause: retain required notice and attribution if code is copied or adapted. Decide picore's license separately.

Picop's public helpers were assessed in matched Cython A/Bs, but none produced a stable improvement exceeding the consumer skill's measurement gate. The tested candidate substitutions were removed; production uses direct `PyBytes_CheckExact`/`PyBytes_GET_SIZE`, `PyBytes_FromStringAndSize`, `found.add(current)`, and `pending.append(_base_type(field["type"]))` arms instead. Picop was consulted and remains a runtime dependency; no performance gain is implied. See [benchmark findings and reproducible commands](../bench/README.md).

The [EIP-712 Agent benchmarks](../bench/README.md#fixed-schema-agent-encoder)
found insufficient headroom in the generic wrapper but retained a fixed-schema
Cython encoder for canonical Agent inputs. Coercions and other inputs keep the
eth-account path. Independent ethers vectors and differential tests cover
digest parity, validation order, and subclass dispatch.

## Protocol hardening

- Agent differential tests cover UTF-8 byte lengths around Keccak's rate,
  embedded NUL, Unicode normalization differences, and mutation of a reused
  domain dict. The fallback checks actual `bytes` storage width as well as the
  existing length check; oversized or short identifiers are not ABI-padded
  by the Agent API. Generic EIP-712 keeps its provider behavior.
- Recoverable secp256k1 signing produces low-S on the tested provider.
  Recovery and BIP-137 verification also accept the equivalent high-S form
  with matching recovery parity. BIP-137 checks the expected public key, not
  a Bitcoin address or its family. The hash frames the actual serialized
  bytes of subclasses. Oversized exact `bytes` or `str` signatures are rejected
  before Base64 decoding; subclass decoding keeps its existing dispatch.
- The internal boundary review found no native bounds or lifetime issue in
  the fixed Agent encoder. An independent security audit and free-threaded
  validation remain open.
- The rebuilt wheels passed 416 tests each on CPython 3.14.7 and 3.15.0rc2,
  outside the checkout with native extension origins verified.

### EIP-712 dependency and startup cost

`eth-account` 0.14.0 depends on `eth-keyfile` 0.10.0 in the tested wheels;
`eth-keyfile` requires PyCryptodome `>=3.6.6,<4`, and picore also directly
requires `>=3.24.1,<4` for its crypto primitives. The generic EIP-712 API and
noncanonical Agent inputs need the encoder; canonical Agent hashing does not.
The hardened checkpoint eagerly loaded `eth_account.messages` from every
`picore.crypto` import, including standalone Keccak.

Median import-only milliseconds in nine fresh processes per target, measured
with `perf_counter_ns` around each import from `/tmp` without `PYTHONPATH`.
These are host measurements, not additive stage costs:

| CPython | `picore` | `picore.crypto` | `picore.crypto.keccak` | `picore.crypto.eip712` | `eth_account.messages` |
|---|---:|---:|---:|---:|---:|
| 3.14.7 | 0.24 | 1454 | 1493 | 1503 | 1472 |
| 3.15.0rc2 | 0.39 | 1394 | 1323 | 1251 | 1309 |

The [recheck](../bench/README.md#full-crypto-recheck) moved the provider import
behind generic-envelope validation. Cold `picore.crypto` import dropped about
90% on both measured runtimes without a warm regression above the gate. This
defers provider startup to the first generic or fallback call; provider import
errors also surface there. Installation dependencies are unchanged.

## Execution and gates

1. **Source and contract inventory.** Inspect pinned implementations, public stubs, tests and dependencies. Record matching signatures, output formats, exceptional behavior and deliberate differences; use authoritative protocol specifications and independent vectors for cryptographic operations. Status: initial source inventory above; full per-input parity remains to be established.
2. **Reproducible baseline.** Build the actual pinned legacy Base64, Keccak and Ed25519 Cython sources in a separate temporary environment for each interpreter, linked against their required dependencies. Verify correctness before timing; report build or compatibility blockers without substituting a different implementation. The legacy tree contains no crypto benchmarks: all new benchmarks must be described as new comparisons, not historical measurements.
   Initial all-EVP and final hybrid measurements on CPython 3.14 and 3.15rc2 are recorded in [`bench/README.md`](../bench/README.md), including raw evidence, actual means/spreads, workload-specific gains, dispatch thresholds and temporary-baseline reproducibility limitations.
3. **Compare and select.** Compare equivalent public-call workloads, include stdlib/provider baselines, conversion/import costs, independent samples, warmup and alternating order; report interpreter, compiler/build, CPU/OS, library versions, workloads, latency/throughput and variation. Legacy Base64's terminal-NUL defect must be tested and excluded from equal-output timing. Exact strings of at least 2048 encoded characters on CPython 3.14 and 3.15 use pybase64's validating decoder, falling back to strict binascii on rejection. Other paths retain the earlier EVP/stdlib dispatch, with future versions using stdlib conservatively. The cutoff is a host-specific measurement, not an API guarantee; see the benchmark report for variation.
   Final Base64 hybrid paired comparisons and initial all-EVP snapshots are linked from [`bench/README.md`](../bench/README.md). Other compiled-versus-Python comparisons have broad sample spreads and no blanket speed conclusion. Picop helper A/Bs found no stable win meeting the measurement gate, so tested substitutions were removed.
4. **Compile the six slices in parallel.** Each owns one `.pyx` module and its protocol tests. Keep inter-module Python interfaces stable: secp256k1 calls Keccak, and Bitcoin signing and recovery call secp256k1. Keep providers responsible for crypto and test observable behavior at Python call boundaries, including argument-validation order and subclass behavior. The integration owner maintains the six `.pyi` stubs, package exports, PEP 517 build configuration and documentation. Keep only one implementation per module.
5. **Verify installed artifacts.** For each interpreter, build a source distribution in isolation, build a wheel from that source distribution, and install the wheel in a clean environment. Inspect the source archive for the six `.pyx` files, and the wheel for the six native extensions, stubs and `py.typed`. Run all crypto tests outside the checkout, including the extension-origin and export test. Cython and setuptools are build dependencies; picop is a runtime dependency. Source builds require a C compiler and interpreter headers. Wheels are ABI- and platform-specific, and free-threaded builds are a separate unverified target.
6. **Measure and review.** Compare the compiled wrappers to the prior Python/provider baseline on matching workloads, and compare to pinned legacy builds where the contracts agree. Record interpreter, provider versions, sample variation, unmeasured cases and any blocked matrix entry. Keep only repeatable picop wins under the [consumer skill's measurement gate](../.agents/skills/use-picop/SKILL.md); do not infer speed from compilation. Keep the Python secret-erasure limitations and the intentional legacy differences documented. Internal tests or automated reviews do not replace independent security review of signing and protocol code.

## Definition of done

All legacy capability families have a documented decision and working, tested implementation or an explicitly approved change in scope; interoperable test vectors and edge cases pass; equivalent legacy comparisons are reproducible and measured or have a specific documented blocker; performance decisions are supported by results; docs and API contracts match the code. A subset of three operations, successful unit tests alone, or an unmeasured provider substitution does not complete the crypto project.
