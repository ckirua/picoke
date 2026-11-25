# Network client plan

This document is a plan, not an implementation or support claim. No HTTP or
WebSocket client API is currently specified here as available. Networking work
must preserve the existing synchronous utilities and remain independent of
`picoke.ratelimit.TokenBucket`, `picoke.queues.ByteRing`, and logging sinks.

## Goals and use cases

Provide small, explicit client utilities for common outbound application work:

- HTTP: request/response calls, bounded response consumption, JSON convenience
  only if its semantics are unambiguous, and explicit streaming for larger
  bodies. Typical uses include service APIs, downloads, and uploads.
- WebSockets: connect to a peer, exchange text/binary messages, and close with
  protocol-aware lifecycle handling. Typical uses include event feeds and
  bidirectional service protocols.
- Correct cancellation, resource ownership, and bounded buffering matter more
  than feature parity with a general-purpose framework. Do not imply server,
  proxy, or inbound-listener support absent a separate design.

## Phase 0 — requirements and provider decision

Before implementation, collect concrete consumer use cases and decide whether
these belong in picoke rather than being direct third-party-library usage.
Inventory Python 3.14+ HTTP and WebSocket providers, licenses, maintenance,
transitive dependencies, TLS/proxy behavior, supported platforms, sync/async
models, and testability. Pin an optional extra to a compatible provider range;
keep imports lazy so installing the base package does not require networking
packages. Do not build a transport stack from sockets/TLS unless provider
constraints make that necessary and a separate security review approves it.

Decision gate: select one provider per protocol, or defer that protocol if no
maintained provider meets compatibility, security, packaging, and lifecycle
requirements. Do not expose backend selection or promise interchangeable
providers without demonstrated semantic parity.

## Phase 1 — public contract and HTTP

Agree on module location and whether the API is async-only. Prefer a narrowly
scoped async client if that matches consumers; do not add sync wrappers that
block an event loop. Decide exact signatures, defaults, URL/redirect policy,
header and body types, timeout model (connect/read/write/overall), and whether
clients are reusable context managers. Specify typed response ownership and
how streamed bodies are consumed and closed before coding.

Required decisions and constraints:

- Cancellation propagates; it must not be translated to success or silently
  retried. Cleanup closes responses and releases connections on cancellation,
  exceptions, and early stream termination.
- Timeouts are finite or explicitly opt-in; distinguish timeout errors from
  cancellation and transport failures. Retries are not implicit, especially
  for non-idempotent requests or partially sent bodies.
- Bound response buffering and decompression; streaming must provide a clear
  consumer-controlled backpressure path. Document whether limits count encoded
  or decoded bytes. Never claim memory bounds that omit provider buffers.
- Preserve status, headers, and body semantics; decide whether non-success
  status raises or remains inspectable. Validate content decoding and malformed
  payload behavior explicitly.
- TLS certificate and hostname verification remain enabled by default. No
  insecure verification shortcut. Avoid logging credentials, cookies, tokens,
  or full sensitive URLs.
- Document redirect limits and credential forwarding rules, URL schemes, proxy
  inheritance, DNS/IP considerations, and SSRF risks. Do not fetch arbitrary
  user-provided URLs without caller-side policy; reject unsupported schemes.
- Avoid silent request-body replay; define ownership for file/iterator bodies
  and whether they must be repeatable.

Correctness vectors include local deterministic servers for success/error
statuses, headers, redirects, chunked and empty bodies, malformed encodings,
large streamed responses, slow consumers, slow/stalled endpoints, connection
refusal, TLS verification failure, cancellation during connect/headers/body,
and cleanup after early exit. Verify exact bytes and status/header behavior,
not merely successful completion.

Decision gate: API and resource lifecycle review plus security review must pass
before stabilizing public names. Defer JSON helpers unless they add clear value
beyond `picoke.io.files` without surprising serialization or decoding policy.

## Phase 2 — WebSockets

Only begin after provider/API selection and HTTP lifecycle principles are
settled. Specify connection context management, `send`/`receive`, message types,
close behavior, ping/pong policy, and whether reconnect is caller-managed. Do
not silently reconnect or replay messages. Decide maximum message size and
whether limits apply before and after decompression.

Backpressure must be explicit: bound any internal receive queue, define what
happens when full (pause reads, block receiver, or fail), and do not create an
unbounded task-per-message pattern. A single reader/writer ownership rule or
other concurrency semantics must be documented. Cancellation during handshake,
receive, send, and close must release the connection; close is idempotent where
provider semantics permit, and graceful close must have a bounded timeout with
an explicit abort path.

Security requirements: TLS verification by default; validate allowed schemes;
consider origin policy only where relevant to the client contract; cap frame and
message sizes; avoid leaking credentials through redirects/subprotocol errors;
make compression opt-in or bound decompressed size. Document that application
message authentication and authorization remain the caller's responsibility.

Correctness vectors include text, binary, empty and oversized frames, fragmented
messages, ping/pong, peer close, abnormal disconnect, handshake rejection,
slow peers, bounded-queue saturation, concurrent send/receive policy, and
cancellation/cleanup at each lifecycle boundary.

Decision gate: implement WebSockets only if a concrete use case justifies the
additional provider and protocol surface. HTTP completion does not imply
WebSocket support.

## Phase 3 — integration and dependencies

Expose optional extras (for example, separate HTTP and WebSocket extras if
provider dependencies differ) only after dependency decisions. Missing extras
should fail at the call boundary with a clear installation instruction; base
package import must continue to work. Keep provider-specific types out of the
stable public API unless deliberately accepted as a dependency contract. Add
typing, examples, and docs alongside each public API. Update package metadata
and source distributions only when implementation is approved.

Audit all plausible picop helper candidates at actual Python call sites against
the installed public release and catalog. HTTP orchestration is likely dominated
by I/O, but this is not evidence either way. Record KEEP/SKIP/UNMEASURED and do
not add a helper without equivalent behavior and measured end-to-end benefit.
No native implementation is presumed.

## Baselines and benchmark design

Compare equivalent behavior against direct use of the chosen provider (the
primary baseline), and where useful a standard-library implementation for a
narrow case. Measure startup/import and dependency cost separately from warmed
requests. For HTTP, include small JSON API calls, small and large downloads,
streamed consumption, uploads, concurrent requests, and slow server/client
cases. For WebSockets, include connect/close, small text and binary messages,
streaming message rates, concurrent connections, slow peers, and queue pressure.

Use local controlled servers to avoid external-network noise; verify byte/status
parity before timing. Record Python/provider versions, OS/CPU, TLS mode, payload
sizes, concurrency, warm/cold connections, timeouts, and memory. Measure latency
(distributions, not only means), throughput, peak/traced memory, connection
reuse, cancellation/cleanup time, and rejection/error counts. Alternate paired
runs, use fresh processes for setup-sensitive cases, preserve raw samples, and
report unstable fixtures as inconclusive. Never benchmark unbounded buffering
against bounded behavior as if equivalent.

Retain an abstraction only when it provides a useful API or reproducible benefit
without material correctness, latency, memory, or dependency regressions. Do not
claim network speedups from synthetic-only measurements or remote-host samples
without controls.

## Execution order and acceptance

1. Requirements/provider and dependency review; publish decisions and security
   threat model before API implementation.
2. HTTP contract, implementation, deterministic tests, and installed-wheel
   validation on CPython 3.14 and 3.15.
3. HTTP benchmark against direct-provider baseline; keep only measured
   substitutions, otherwise retain straightforward provider calls.
4. Separate WebSocket go/no-go decision; if approved, contract, implementation,
   adversarial lifecycle tests, and provider baseline benchmark.
5. Review docs, optional extras, package imports, and security limitations.

Each phase is accepted only when its observable semantics, error/cancellation
behavior, resource cleanup, boundedness, and dependency installation are tested
on both supported interpreter versions. Free-threaded and non-Linux support
must not be claimed until separately validated. An unimplemented phase remains
explicitly planned; do not mark networking complete based on this document.
