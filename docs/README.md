# picoke maintainer docs

For the project overview and support policy, start with the root [`README.md`](../README.md). This directory holds planning and contributor material, following picop's separation of public overview and maintainer documentation.

| Document | Purpose |
|---|---|
| [`PLAN.md`](PLAN.md) | Scope, milestones, validation criteria, and open decisions |
| [`CRYPTO_PLAN.md`](CRYPTO_PLAN.md) | Legacy crypto parity, performance comparisons, security gates, and execution plan |
| [`IO_FILES_PLAN.md`](IO_FILES_PLAN.md) | File API contracts, verification, and optimization gates |
| [`BINARY_LOGGING_PLAN.md`](BINARY_LOGGING_PLAN.md) | Append-only binary logging format and validation plan |
| [`IPC_PLAN.md`](IPC_PLAN.md) | Shared-memory queue contracts and verification plan |
| [`NETWORK_PLAN.md`](NETWORK_PLAN.md) | HTTP and WebSocket client planning and decision gates |
| [`RELEASE.md`](RELEASE.md) | Topic branches, working versions, and release checklist |
| [`LEGACY_IDEAS.md`](LEGACY_IDEAS.md) | Legacy utility inventory, candidate priorities, and migration constraints |
| [`../AGENTS.md`](../AGENTS.md) | Agent engineering and compatibility contract |
| [`../.agents/README.md`](../.agents/README.md) | Repository orientation and working rules |

The first package APIs cover Base64, Keccak-256 and Ed25519; see the root README for usage. Do not import picop's module trackers or release claims as picoke's own.
