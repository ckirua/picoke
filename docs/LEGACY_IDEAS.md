# Legacy utility ideas

## Source and purpose

An earlier private Python utility tree is a source of practical use cases for picore, not a specification or codebase to port wholesale. Its module inventory and maturity labels do not establish picore support. Apart from the first crypto milestone described below, the items are candidates, not implemented features or commitments to preserve old names.

## Candidate inventory

| Area | Useful ideas from the earlier utility tree | Picore design questions |
|---|---|---|
| Rate limiting | Async token and leaky buckets, fixed and sliding windows; configurable capacity and time periods. | Monotonic time, precise refill/window semantics, invalid or impossible costs, fairness, cancellation, concurrency and clock control in tests. Compare with a straightforward asyncio implementation. |
| In-process queues | Bounded SPSC byte and fixed-record rings with nonblocking push/pop and statistics. | Define SPSC ownership, full/empty behavior, payload bounds, data copying, memory ordering, and buffer lifetime. Compare with `queue` and suitable native baselines; verify free-threaded correctness separately. |
| Files and streams | JSONL reader/writer and cached line readers with filtering, encoding, and stdio/mmap strategies. | Define streaming versus caching, blank and malformed records, newline/encoding rules, resource limits, file-change behavior and errors. Compare with ordinary file iteration and the same JSON backend; do not assume mmap or native code wins. |
| Time and runtime | Nanosecond clocks, UTC datetime and platform-specific clocks; loop selection and signal-driven idle. | Distinguish wall and monotonic clocks, units, precision and platform availability. Compare with `time` and picop; signal handling affects process-wide state. |
| Logging | Native logger, configurable sinks and stdlib logging compatibility, including JSON-lines formatting. | Specify filtering, formatting, ordering, flush/shutdown, errors, sink ownership, thread safety and backpressure. Benchmark against stdlib logging for enabled and disabled calls. |
| IPC and messaging | Linux shared-memory SPSC ring with nonblocking publish/consume, wait/wake and explicit unlink; async and sync ZeroMQ interfaces. | SHM is cross-process and Linux-only, not the in-process queue. Define lifecycle, initialization races, crash recovery, slot/view lifetime, counter wrap and ABI. ZeroMQ adds libzmq, socket/context ownership and delivery/backpressure semantics. |
| Codecs and archives | JSON decoding/validation, MessagePack, YAML, TOML subset and ZIP helpers. | Specify interoperability and malformed input, resource bounds, archive path safety and supported format subset. Prefer maintained libraries unless workload evidence justifies ownership of parsers or native bindings. |
| Data | Arrow CSV/Parquet and table assembly; normalized dataset manifests; database helpers. | Validate schema, nullability, ownership, error behavior and compatibility with upstream PyArrow. Evaluate manifest normalization independently of native performance and only for a concrete data workflow. |
| Network clients | Async HTTP client/pool and WebSocket queue and callback paths. | Account for aiohttp/picows dependencies, connection lifecycle, cancellation, backpressure, buffering and reconnect semantics; prefer upstream clients unless a specific gap is demonstrated. |
| Cryptography | Base64, Keccak, curves and protocol-specific signing. | High security and interoperability burden. Prefer maintained, reviewed providers; require test vectors, malformed-input tests, secret-handling and independent security review before any signing API. |

## Proposed sequence

1. **Cryptography:** Base64, Keccak, Ed25519, secp256k1, EIP-712 and Bitcoin signed-message APIs are implemented. Follow the [crypto plan](CRYPTO_PLAN.md) for measured legacy comparisons, intentional differences and remaining review. Consult the [picop skill](../.agents/skills/use-picop/SKILL.md) when a measured use of its primitives is appropriate.
2. **Protocol hardening:** expand interoperability vectors and obtain independent security review. The legacy Bitcoin raw single-SHA-256 and SEC-prefix Ethereum-address behavior intentionally differ from picore.
3. **Expand by demonstrated need:** consider rate limiting, in-process queues, files, time, logging, data and codecs as independent features. Isolate heavy or platform-specific integrations; do not inherit an earlier project's mandatory dependency set by default.
4. **Specialized work:** defer shared-memory IPC, transports and archive extraction until concrete workloads justify lifecycle, platform, security and dependency maintenance.

## Acceptance before implementation

For the chosen feature record its input/output and error contract, lifetime and concurrency rules, baseline, representative workload, dependency/ABI requirements and target platforms. Test edge cases and installed artifacts on CPython 3.14 and 3.15. Treat 3.14t/3.15t as separate builds and do not claim free-threaded support until verified with the GIL disabled. Record benchmark environment and results; legacy status or catalog coverage is not evidence of a picore speedup.

The historical source code was BSD-3-Clause. Ideas are inspiration; before copying or adapting code, review attribution, license and notice requirements, including those of each native or vendored dependency. Decide picore's own license independently.
