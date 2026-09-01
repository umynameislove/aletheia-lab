# ADR 0003: Isolate evaluation attempt-store authorities

- Status: accepted
- Date: 2026-09-02

## Context

The evaluation attempt store is a scientific control boundary. It binds each
request to immutable attempts and outcomes, rejects replay, and exposes a
terminal inventory only after atomic closeout. Keeping contracts, transitions,
publication, reading and integrity verification in one module made accidental
self-verification possible and expanded the blast radius of filesystem changes.

The public schemas, serialized bytes, content hashes and terminal semantics are
already frozen. This decision changes ownership boundaries only; it does not
authorize an evaluation run or change a scientific protocol.

## Decision

`aletheia_lab.evaluation.attempt_store` remains the stable compatibility facade.
The internal `aletheia_lab.evaluation._attempt_store` package owns these separate
authorities:

| Module | Authority |
|---|---|
| `contracts` | frozen schemas, errors and canonical serialization |
| `transitions` | pure lifecycle and replay rules |
| `writer` | immutable object and ledger publication only |
| `reader` | read-only parsing and terminal inventory construction |
| `integrity` | independent membership, hash and link verification |
| `reconciliation` | request/result identity and attempt reconciliation |
| `store` | public lifecycle orchestration over the isolated authorities |

Dependencies flow toward contracts. Reader, integrity verifier and reconciler
must never import writer or invoke publication primitives. Writer does not
verify its own output; successful calls are followed by reconciliation through
the independent read path.

## Compatibility and integrity requirements

- Public import paths and exported symbols remain unchanged.
- Schema versions, canonical JSON, event identities and terminal semantics do
  not change.
- A tracked complete-store fingerprint remains byte-identical across the split.
- Identical replay is idempotent and never counts another attempt.
- Non-identical replay, partial publication, missing objects, broken links,
  tampering and invalid transitions fail closed.
- A crash before terminal publication cannot mint terminal state.
- Internal modules remain below the accepted review-size budget.

These constraints are machine-checked by architecture, characterization,
cross-process reproducibility and mutation tests.

## Consequences

Independent verification can be reviewed without granting it write authority.
Filesystem changes are isolated from lifecycle and identity rules. The package
contains more files, but each file has one auditable responsibility and an
enforced dependency direction. Shared cross-platform durability primitives may
be introduced later without reopening schemas or scientific outcomes.
