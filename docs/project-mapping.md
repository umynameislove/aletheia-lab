# Project mapping and validation

Project mapping is an explicit, content-addressed configuration applied after
transactional import and file collection. It never infers target, identifier,
metric, run or baseline semantics from path proximity, Git history or log text.

## Required bindings

A configuration binds one exact project, ProjectBundle and file-collection
digest. It declares:

- the dataset item and distinct target and identifier fields;
- each JSON/CSV metric source and its name, value, run and optional step fields;
- declared runs and their configuration item references;
- one baseline run selected before downstream regression evaluation.

Mappings and runs are canonically ordered. Their digest changes when any bound
project, bundle, collection, item, field, run or baseline changes.

## Validation

Validation checks every candidate and emits an auditable accepted/rejected
census. It rejects foreign or stale project references, missing items, wrong
source types, missing fields, artifact mismatch, malformed or non-finite metric
values, duplicate metric identities, undeclared runs, runs without observations
and an unknown baseline.

Any blocker makes the entire result `blocked`; a blocked result releases no
metric observations and cannot be bound into a ProjectBundle. A valid result
can create a new immutable bundle identity with `mapping_configuration_sha256`.
It cannot mint snapshots, evidence references or lineage edges.

## Scientific boundary

Metric observations are measurements, not causes. A configuration or Git/log
association must not be rendered as verified root cause without independent
intervention or external verification. Snapshot and regression logic consume
only valid mappings and remain a separate downstream contract.
