# P3 scope amendment and deferred-obligation gates

## Decision

Commit `31f6c58` closes the P3 **core persistence-lineage vertical slice**. It
does not silently redefine every item that earlier planning documents placed in
the wider P3 delivery window as complete.

The verified core is:

```text
authorized import
  -> mapping
  -> immutable before/after snapshots
  -> regression candidate with causal_status="unverified"
  -> diagnosis-visible evidence
  -> content-addressed persistence
  -> non-causal typed lineage
  -> deterministic closeout receipt
```

The closeout reconciles eight exact generation roles, verifies hashes and store
integrity, preserves a monotonic visibility lattice, and cannot express a
`causes` lineage edge. These are engineering and provenance guarantees, not
scientific mechanism admission or real-project causal verification.

## Obligations intentionally not claimed by the core closeout

| Obligation | Status at amendment | Required before | Blocking evidence |
|---|---|---|---|
| Project retention, delete and reference-safe purge receipt | deferred P3b product lifecycle | P6 release candidate and deletion UX acceptance | database, artifacts, caches, reports and indexes are deleted or explicitly reported; shared objects remain reference-safe |
| Prospective temporal holdout seal, custodian and access ledger | deferred prospective-case preparation | any confirmatory P5 prospective-case opening | immutable split/checksum receipt, named custodian, zero pre-freeze holdout access, logged open condition |
| Version-pinned read-only Projmem adapter and source-badged lineage bridge | deferred prospective-case integration | P5 prospective-case analysis or any public Projmem workflow claim | pinned version/schema, reviewed development export, native/manifest/derived provenance separation, unsupported-version failure |
| Sanctioned non-synthetic sample-project audit | deferred ecological/product validation | P5/P6 real-project or end-to-end product claim | import-to-report audit on the named sample/export with no source mutation, hidden-truth claim or secret leakage |

The first item is release-critical but does not block P2R or P4 scientific
instrument preparation. The remaining three block only the corresponding
prospective-case or real-project claims; they do not retroactively invalidate
the controlled benchmark or the P3 core closeout.

## Claim boundary

Allowed now:

> The named release implements and verifies a deterministic, visibility-safe,
> content-addressed P3 core from authorized project import through snapshot,
> regression evidence, non-causal lineage and closeout.

Not allowed until the named gate passes:

- “P3 is complete in every product and prospective-case sense”;
- “project deletion/purge has been verified”;
- “the prospective holdout is sealed”;
- “Projmem integration is implemented”;
- “a real project demonstrates diagnosis effectiveness”; or
- “lineage proximity verifies root cause.”

## Downstream dependency rule

- P2R may begin after this core closeout and the planning/claim synchronization
  audit; it does not depend on delete/purge or Projmem.
- P4 benchmark infrastructure may begin from the verified P3 core, but its
  scientific freeze remains dependent on the registered P2R reconciliation.
- P5 prospective-case work is blocked until the temporal seal and Projmem gates
  pass.
- P6 release is blocked until deletion/purge and the relevant end-to-end sample
  workflow pass.

No downstream result may be used to weaken these gates. A missed gate removes or
downgrades only its corresponding claim; it does not authorize retrospective
backfilling.
