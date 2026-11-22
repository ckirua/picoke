# Append-only binary structured logging

## Contract

- Keep `picoke.logging.JSONFormatter` and `BackgroundJSONHandler` unchanged. Add `picoke.binary_logging.BackgroundBinaryHandler` and `iter_binary_records` with optional `picoke[binary-logging]` dependency on MessagePack.
- Each independently framed record is `b"PKL\x01"`, a four-byte unsigned big-endian MessagePack payload length, the payload, and a four-byte unsigned big-endian CRC-32 of the payload. The magic includes the format version. There is no file header, so complete frames can be appended to an existing valid file. One writer per file; no cross-process atomicity is promised. Do not append after a damaged tail without repairing it first.
- The MessagePack map uses the same timestamp, level, logger, message, exception, stack, and optional fields semantics as `JSONFormatter`. Structured field values follow its JSON-compatible validation, including cycle, string-key, and finite-number checks. MessagePack integers must fit its signed-64/unsigned-64 range; out-of-range values raise during formatting. Messages are encoded once and framed once before bounded queue admission.
- `BackgroundBinaryHandler(writer)` is a standard logging handler with caller-owned `BackgroundWriter`. Its `emit` formats and enqueues one complete bytes frame, routes synchronous failures through `handleError`, and its `flush` delegates. `close` does not flush or close the writer. Worker failures are asynchronous and surface through later operations.
- `iter_binary_records(stream, *, max_record_size=1048576, recover_truncated=False)` reads a caller-owned binary stream incrementally. Maximum bounds payload bytes before allocation; short reads must be handled. Clean EOF between frames succeeds. Strict mode raises `BinaryLogError` for a truncated header, payload, or checksum; recovery mode ignores only an incomplete final frame. Bad magic/version, excessive length, bad checksum, malformed MessagePack, non-map output and invalid fields remain errors in either mode. Preserve IO exceptions; never close the stream. Untrusted files are bounded by `max_record_size` and must not trigger arbitrary object deserialization.
- Framed size is payload length plus 12 bytes and must fit the writer's `max_item_size`. `flush` is not `fsync`; neither durability nor zero-copy encoding is promised. GIL CPython 3.14+ only until validated otherwise.

## Work and verification

1. In parallel implement codec/handler/reader, independent contract tests, and benchmark runner. Integration owner maintains dependency metadata, documentation, and correctness review. Agents read the picop skill; no unmeasured substitutions or mid-flight builds/tests.
2. Exercise independent MessagePack decode, golden framing, append across sessions, Unicode and exceptions, all truncated boundaries and strict/recovery modes, malformed/checksum/version/oversize files, short stream reads, validation, handler error and ownership, background drain/failure. Test both 3.14 and 3.15 installed packages.
3. Compare JSON and MessagePack for encoding and complete background delivery of equivalent records, including reader round-trip and exact logical parity; use representative short, nested and Unicode records, one and four producers, output sizes, serial alternating samples, process/environment and raw data. Report variability and avoid performance claims when controls are unstable. Compare equivalent semantics and include setup/queue/drain costs explicitly.
4. Preserve all pre-existing uncommitted changes; no commit, push, PR, version change, or release without separate authorization.


## Verification status

- Installed-wheel suites: 627 tests passed on CPython 3.14.7 and 3.15.0rc2
  with the optional MessagePack dependency installed.
- The end-to-end JSON/binary benchmark verifies logical parity and records
  encoded sizes, but its same-code controls were unstable. See the
  [benchmark report](../bench/README.md#framed-messagepack-versus-json-logging)
  for raw results and limitations.