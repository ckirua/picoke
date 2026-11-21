# picore project plan

## Intent and status

picore aims to provide practical CPython utilities built on picop. Crypto now covers Base64, Keccak-256, Ed25519, secp256k1, EIP-712 and Bitcoin signed messages; see the [crypto plan](CRYPTO_PLAN.md) for compatibility differences and remaining verification. The [legacy utility inventory](LEGACY_IDEAS.md) identifies further candidates. Picop is the low-level base, not a catalog to copy.

## Compatibility target

- CPython 3.14+; 3.15 is the default development and primary validation interpreter. Older Python versions are out of scope.
- Initially test both 3.14 and 3.15. `>=3.14` in package metadata expresses the minimum, not proof that an untested future interpreter works.
- Aim for regular and free-threaded builds, but claim each only after testing it. Native builds must be separate for each interpreter and ABI; a free-threaded import must not silently enable the GIL if support is claimed.

## Milestones

### 1. Cryptography: legacy parity and performance

- Implementations cover the legacy public capability families; full interoperability, performance characterization and independent security review remain open.
- Follow the [crypto execution plan](CRYPTO_PLAN.md), including actual legacy comparisons and documented intentional behavior differences.
- Set up CI and decide licensing and distribution metadata before publishing.

### 2. Protocol hardening

- Expand independent interoperability vectors and adversarial coverage for recoverable secp256k1, EIP-712 and Bitcoin signed messages; obtain independent security review.
- Evaluate the eth-account dependency footprint and PyCryptodome version constraint against correctness, portability and measured performance.

### 3. Expand and measure

- Select additional features by concrete need; define their behavior, dependencies and resource lifetime before implementation.
- Measure representative workloads before claiming a performance benefit, and consult picop's public API and safety constraints when a measured use justifies it.
- Document public APIs and limitations; test installed artifacts rather than only the source tree.

### 4. Harden and prepare a release

- Validate each claimed platform and ABI, including free-threaded correctness and concurrency with the GIL disabled if claimed. Investigate import-time GIL state and use separate builds.
- Follow the [working-version and release policy](RELEASE.md). Finish distribution and vulnerability reporting setup before the first release; document actual support evidence.
- Done when the documented release checklist passes and the release is explicitly approved.

## Open decisions

- Specifications and provider choices for later protocol work and other legacy candidates.
- Supported platforms and dependency maintenance policy.
- Project license, publishing target, and publishing configuration.
- Which free-threaded configurations can be supported and verified.

Update this plan as decisions are made; mark work complete only with evidence. Picop's existing trackers, benchmark numbers, and release status do not establish picore's.
