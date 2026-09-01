# ADR 0002: Separate diagnosis development execution boundaries

- Status: accepted
- Date: 2026-09-02

## Context

The synthetic diagnosis development pilot must construct a frozen nine-variant
matrix, execute it deterministically, publish immutable artifacts and validate
the resulting store independently. Keeping those responsibilities in one
module made changes difficult to review and allowed the executor, verifier and
publication lifecycle to become accidental dependencies of one another.

That coupling is an experimental risk. A change intended for orchestration
could silently affect independent validation or artifact publication, even
though the public API and scientific boundary should remain unchanged.

## Decision

The stable public module `aletheia_lab.diagnosis.development` is a facade. Its
documented symbols and import path remain unchanged. Implementation is owned by
the internal `aletheia_lab.diagnosis._development` package with these
responsibilities:

| Module | Authority |
|---|---|
| `contracts` | immutable schemas, identifiers and validation invariants |
| `policy` | pure context, evidence, tool-ledger and response-shape derivations |
| `planning` | synthetic cases, plans and bound requests |
| `executor` | deterministic development-only executor boundary |
| `resources` | conservative, deterministic resource accounting |
| `store` | append-only content-addressed publication and verification |
| `validation` | independent request and response reconciliation |
| `runner` | orchestration of the complete matrix and terminal closeout |

Dependencies flow from orchestration toward lower-level authorities. Contracts
do not import any other internal component. Validation imports only contracts
and the pure policy kernel, never planning, executor or runner. Store
publication does not import planning, execution or validation. The offline
audit imports the independent contracts, resource, store and validation
authorities directly rather than traversing the facade or runner.

An architecture regression test parses imports and blocks violations of this
dependency graph. It also freezes the public export set and places a review
budget on internal module size. Changing an accepted boundary requires an
explicit architectural amendment rather than incidental coupling.

## Compatibility and integrity requirements

This split is behavior-preserving:

- canonical request, response, record, manifest, terminal and audit bytes must
  remain unchanged for the tracked plan;
- run identifiers and content-addressed object membership must remain unchanged;
- the facade must export the same public symbols as before the split;
- the existing failure receipts, fail-closed behavior and development-only
  restrictions remain authoritative;
- no development output becomes scientific evidence or consumes a registered
  attempt.

## Consequences

- Execution changes can be reviewed without granting them authority over the
  independent verifier.
- Store lifecycle changes remain isolated from experimental planning.
- Smaller modules reduce cross-platform regression scope and make mutation
  tests more diagnostic.
- Internal modules are not compatibility surfaces; external callers continue
  to use `aletheia_lab.diagnosis.development`.
- More files are present, but their ownership and permitted dependencies are
  machine-checked rather than conventional.
