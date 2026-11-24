# Shared byte ring implementation and validation plan

## Decisions

- `SharedByteRing` uses a uniquely named file in Linux's shared-memory filesystem (`/dev/shm`) and a shared `mmap`; no Python resource tracker participates, so an attaching process cannot accidentally unlink storage when it exits.
- One fixed-size slot is reserved for every queue position. Capacity counts items; `max_item_size` bounds each payload. The shared header contains dimensions, head, tail, and count. Each occupied slot stores an explicit length, preserving empty bytes as an item distinct from an empty queue.
- Every transition that reads or changes shared ring state is protected by exclusive `flock` on the opened segment. This is an OS-level interprocess lock, not a GIL-based flag. A per-handle thread lock serializes operations that share a descriptor; distinct handles also have distinct open file descriptions and conflict through `flock`. The contract remains one producer and one consumer; no crash recovery is promised.
- The API copies payload bytes into shared memory on push and returns a copied `bytes` on pop. It retains no borrowed buffer views. `close()` detaches a handle and is idempotent; `unlink()` removes the discoverable name without invalidating already-open mappings. The creator is responsible for unlinking after clients are done. Calls on a closed handle fail with `ValueError`; attaching a missing name raises the underlying `FileNotFoundError`.
- Header and slot bounds are validated when attaching, and slot lengths are checked before reading. Creation is exclusive to avoid collisions; a UUID name avoids name reuse during open/unlink races. Storage and descriptor cleanup are explicit.
- Inherited handles cannot be used after `fork`; a child must attach a fresh handle by name. This avoids sharing a `flock` open-file description and potentially inherited thread lock.
- Slot operations use a checked C++/pybind11 helper while the Python handle retains process checks, its thread guard, and `flock`. The helper validates the mapped header and bounds before accessing slots; it does not change the shared format or provide zero-copy access.

## Phased validation

1. **API and local invariants:** validate exact integer and bytes types, dimensions, zero-length payloads, oversize rejection, full/empty behavior, FIFO order, wraparound, repeated close/unlink, post-close operations, and invalid names/headers.
2. **Process sharing:** use independent processes, attaching by name, to verify payload visibility, ordering, producer/consumer progress, and that an attaching process exiting does not remove the creator's name. Verify attach/open races against unlink and that existing mappings remain usable.
3. **Concurrency and lifecycle:** exercise the documented SPSC contract with bounded joins; check one producer/consumer plus multiple threads on a single handle are serialized, close/unlink resource handling, and no deadlock. Crash recovery and multi-producer/multi-consumer behavior are explicitly out of scope.
4. **Platform and packaging:** verify Linux-only failure behavior and package inclusion on supported Python 3.14 and 3.15 builds. Run the targeted IPC suite and then the integrated suite in fresh interpreter environments. No platform, ABI, or performance claim is made until those checks complete.

## picop review

- **SKIP** — `try_push` / `try_pop` operate on mmap slices, struct fields, OS locks, and bytes copies; the catalog's in-memory container/bytes helpers do not replace these operations without changing ownership or synchronization.
- **UNMEASURED** — public helper substitutions for exact-type checks and small metadata operations. These calls are not currently replaced; no performance result is claimed.

## Separate atomic SPSC investigation

Do not mix atomic operations with the `PICORNG1` layout or replace its
`flock` protocol. An experimental copied-bytes SPSC ring used a separate
format, 64-bit acquire/release sequence counters and power-of-two capacity.
On Linux x86-64 with GIL-enabled Python it passed an exploratory FIFO/full/
empty/sequence-wrap check and cross-process benchmark traffic. The prototype
is **not production-ready**: its validation, initialization race, lifecycle
and process-death behavior have not passed the acceptance criteria below.

Before a production atomic API can be considered:

1. Specify the supported OS, architecture, compiler and ABI; require genuinely
   lock-free process-shared atomics and validate alignment and format.
2. Publish initialization safely; validate all size arithmetic and metadata
   before slot access, and reject incompatible or incomplete segments.
3. Specify producer/consumer ownership, same-handle threading, fork, close and
   unlink behavior, plus process-death semantics. Do not promise crash
   recovery without an explicit recovery protocol.
4. Test process races, corrupted mappings, capacity and sequence wraparound,
   slot integrity, process termination at publication boundaries and the
   full documented lifecycle. Keep the original format interoperable only
   with the original implementation.
5. Compare end-to-end performance with paired same-machine controls on each
   supported interpreter. The exploratory results are in the benchmark README.

### Zero-copy lifetime contract

A separate zero-copy API would need a native buffer exporter that keeps each
slot leased while any derived buffer remains exported. Explicit release alone
cannot invalidate retained memoryviews safely. Define mapping close, unlink,
process death and lease reclamation before implementation; a live process
must never observe a slot reused behind an exported view. The existing copying
API remains unchanged, and no crash-safe zero-copy implementation is claimed.

The separate `AtomicSharedByteRing` implementation is under validation; it
does not replace `SharedByteRing`. Its backing objects are accessible to the
same user: participating processes and the shared-memory filesystem must be
trusted not to mutate metadata or truncate the file. Truncation of a live
mapping can cause SIGBUS. `/dev/shm` must have sufficient backing capacity;
successful sizing alone does not reserve physical storage. Only Linux x86-64
GIL-enabled CPython is targeted; free-threaded operation is not supported.
Creation uses an unnamed `O_TMPFILE` object and publishes it via `/proc/self/fd`;
the filesystem must support this and `/proc` must be mounted. An abrupt
creator exit before publication leaves no named temporary object, but a ring
already published may require operator cleanup. Ownership checks are API
conventions, not a security boundary against processes with the same OS
credentials.

Validation on Linux x86-64, GIL-enabled CPython 3.14.7 and 3.15.0rc2:
the IPC suite covers cross-process FIFO, lifecycle, malformed segments and
process exit around public operations. A separate `linkat` fault-injection
run stopped the creator immediately before and immediately after publication,
then killed it: before publication no name remained; after publication an
attacher could use the fully initialized ring. This does not test interruption
inside native slot copying or promise recovery when a participant dies during
an operation.
