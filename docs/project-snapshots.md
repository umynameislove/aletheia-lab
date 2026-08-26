# Project snapshots and refresh identity

P3 snapshots are minted only from an imported bundle that has been bound to an
explicit, valid mapping configuration. The file collection, Git observation,
mapping result and their complete item census must describe the same project
state. A mismatch blocks snapshot construction rather than producing a partial
record.

## Two digest domains

`state_sha256` covers the payload-free project state: source bundle and
manifest, collector versions and outputs, Git state, mapping configuration and
result, baseline selection, item observations and metric observations.
`snapshot_id` is the namespaced form of that digest.

`captured_at` is audit metadata and does not enter state identity. This makes a
refresh of unchanged inputs idempotent instead of creating a false change.
`record_sha256` binds `captured_at` to the state digest so the timestamp cannot
be edited without detection.

## Boundaries

Snapshots contain identifiers, checksums, visibility/redaction states and
bounded metric observations. They never copy imported source text, secret
values or artifact bytes. A snapshot records observations; it does not establish
that a Git, configuration or metric change caused a regression.

Persistence, deletion and typed graph storage remain downstream P3 contracts.

## Refresh comparison and regression candidates

A refresh compares two valid snapshots from the same project. Added, removed
and same-path modified items are reconciled exactly. A rename is never inferred
from matching content: it requires a matching after-snapshot Git-state digest
and an explicit Git rename record. Without that evidence it remains an add plus
a remove.

Metric observations are compared by run, metric and step identity. A changed
metric can mint a `ProjectRegressionEvent`, but the event is permanently marked
`regression_candidate` and `causal_status=unverified`. Direction alone does not
say whether a metric improved or regressed because metric objectives are not
part of the mapping contract.

The project-mode evidence bundle contains only IDs, checksums, roles and
provenance links. Its diagnosis projection is a whitelist and does not expose
raw imported content or metric values. This evidence is suitable for later P3
lineage construction, not for claiming a confirmed hidden cause.
