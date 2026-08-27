# P3 core ingestion, snapshot, persistence and lineage closeout

The P3 core vertical slice closes only when an exact persisted generation can be
reconciled from mapped project imports through a non-causal lineage graph. A
merge, passing unit test or valid SQLite file by itself is not sufficient.

This receipt does not claim that every product-release or prospective-case
obligation originally scheduled near P3 is complete. Retention/delete/purge,
the prospective temporal seal/access ledger, the version-pinned Projmem bridge,
and a sanctioned non-synthetic sample audit remain separately gated work. Their
scope, dependencies and stop conditions are recorded in
[`p3-scope-amendment.md`](p3-scope-amendment.md).

## Closeout unit

One closeout receipt binds exactly eight generation roles (two roles may point to
the same immutable record when an unchanged bundle is intentionally reused):

1. mapped project bundle before the observed change;
2. mapped project bundle after the observed change;
3. before snapshot;
4. after snapshot;
5. snapshot comparison;
6. regression candidate;
7. diagnosis-visible evidence bundle; and
8. typed lineage graph.

The closeout builder reloads every record through the content-addressed store,
recomputes object and canonical hashes, checks the current migration history and
requires all models to belong to the same project. Snapshot-to-bundle,
comparison-to-snapshot, event-to-comparison, evidence-to-event and
lineage-to-generation relationships are reconciled explicitly.

The result is a `ProjectCloseoutReceipt`. It contains IDs, schema versions,
checksums, record census and projection counts. It contains no source path,
artifact payload, metric value, configuration value or secret.

## Scientific boundary

P3 establishes reproducible observation and provenance. It does not establish a
hidden cause. A closeable regression event must retain
`causal_status="unverified"`, and the lineage vocabulary deliberately has no
`causes` edge. Mechanism admission remains a registered P2/P2R responsibility;
diagnosis evaluation remains a frozen P4/P5 responsibility.

## Visibility and dashboard contract

The receipt binds deterministic `public`, `diagnosis` and `evaluator` graph/table
projections. Node and edge counts must be monotonic across that visibility lattice,
and every projected edge must retain both endpoints. Dashboard code may consume
these projections but must not reconstruct hidden nodes from evaluator-only data.

## Operational verification

Given an existing store and the eight selected IDs, run:

```bash
PYTHONPATH=src python scripts/p3_project_closeout.py STORE_ROOT \
  --project-id PROJECT_ID \
  --before-bundle-id BEFORE_BUNDLE_ID \
  --after-bundle-id AFTER_BUNDLE_ID \
  --before-snapshot-id BEFORE_SNAPSHOT_ID \
  --after-snapshot-id AFTER_SNAPSHOT_ID \
  --comparison-id COMPARISON_ID \
  --event-id EVENT_ID \
  --evidence-bundle-id EVIDENCE_BUNDLE_ID \
  --lineage-graph-id LINEAGE_GRAPH_ID
```

The command never reads or mutates the source project. Opening an older supported
store may apply its predefined, hash-bound metadata migration before verification.
A valid receipt has `status="p3_closeout_pass"`; any missing, stale, replayed,
tampered, cross-project or visibility-inconsistent member fails closed.

The complete blocking matrix is in
[`p3-closeout-acceptance.csv`](p3-closeout-acceptance.csv). Repository release
acceptance additionally requires the project profile, Python 3.11/3.12 full test
matrix, Windows project job, coverage threshold, Ruff, strict mypy, hygiene,
Bandit and dependency audit to pass.

Passing this closeout is therefore evidence for the P3 core persistence-lineage
generation only. It is not a deletion receipt, a prospective-holdout seal, a
Projmem compatibility report, a real-project effectiveness result, or a product
release authorization.
