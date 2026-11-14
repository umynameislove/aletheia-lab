# Post-acceptance project conflicts and repeated ZIP confirmation

The `jobs.conflict_v1` feature advertises persistence migration V17. V1–V16
migration bytes and existing terminal history remain unchanged. Drain old workers
before the operator applies V17; startup never runs DDL. A post-acceptance
`WorkspaceConflict` becomes a terminal failed attempt with `failure_kind=conflict`.
`retryable=false`: submitting the old job again cannot repair a stale project CAS.

`GET /v1/workspaces/{workspace_id}/jobs/{job_id}/attempts` retains its existing
fields. Only a latest conflict attempt includes the additional `conflict` object:

```json
{"project_id":"p3-project-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","current_revision":3}
```

The revision is observed at this authorized read, not frozen at the failed write.
It is never permission to overwrite: a new explicit user action must obtain a
current project revision and send its own request key. Both the job and its native
parent source/project are authorized before this metadata is returned. Revoked
sessions/grants cannot use attempt history to discover project information.
No failed output is represented as a successful job output, and frozen Job and
JobReceipt wire fields stay unchanged.

The hosted UI retains the question and the open parent result, explains that the
project changed after acceptance, and offers “Open current project”. It does not
automatically retry the job. A subsequent explicit question reads a fresh CAS and
uses a new request key. Old servers without this feature keep the previous failure
behavior.

Repeated uploads may create distinct source IDs even when ZIP bytes are identical.
For confirmation into an existing project, the publisher reads its current native
snapshot through current authorization. It may reuse the first immutable capture
only when every state field except the new source ID matches exactly, including
workspace, source hash, schema, mapping and mapping-result hashes. The newly
submitted source is authorized again in the final transaction, and publication
still checks the original source/project and current pointer. It does not advance
the project revision or replace a newer snapshot. A changed mapping is a conflict,
not an identical import. Confirm output also rechecks access to the returned
snapshot, including when it belongs to an earlier upload intent.

R4.14 also appends preview migration V2 (`preview_repeat_schema.sql`), preserving
the original preview V1 SQL and ledger row. Its guard keeps the new intent's
creator/source/preview/expiry checks, and permits the returned snapshot's earlier
source ID only when both stored sources and the snapshot bind the same ZIP hash
in the same workspace. The publisher still requires exact mapping/state equality
and current authorization to both sources. Different bytes, expired previews and
foreign workspaces remain rejected. No trigger is disabled.

Drain old API/worker processes before the operator runs core migration, then
preview migration and runtime grants, and starts the new processes. Core schema
verification recognizes the pinned, verified preview V1 or V2 extension without
refreshing historical core facts; new preview readiness/grants require V2.
Do not rewrite an old preview migration checksum or execute startup DDL.

These changes do not close primary50k, staging/IdP, independent restore, or the
remaining image/static-analysis/maintainability release gates.
