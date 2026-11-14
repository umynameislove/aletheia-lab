# K-07 scope and interface handoff — 10 November 2026

The working checkout is `aletheia-j01-api`, branch `feat/product-api`, base
`2bbcdc2a7637c840babd8b71910306c64072bc4b` plus concurrent local WIP. A HEAD alone
does not identify the tested content. No stage, commit, push, merge, clone,
branch switch or worktree move is part of this work.

The assignment package at `Desktop/Aletheia-Kien-API/Aletheia-Kien-API` is the
acceptance source. `Hop-dong.json` explicitly labels itself a design proposal.
Older `j01-upload-snapshot-boundary.md` predates the current working preview/
confirm/analysis implementation. Historical one-shot ZIP or HTTPX local checks
are not evidence of a resumable or browser-integrated K-07 release.

| Item | Existing boundary to reuse | Required evidence and receiver |
|---|---|---|
| K-07.01 | `PostgresSources`, existing queue, K05 publication, K06 idempotency | Chunk retry/resume/hash/length/quota/cancel/cleanup/concurrency, S/M/L; Quân wizard |
| K-07.02 | Existing ZIP inspector and P3 parser in bounded worker | Malformed/bomb/traversal/Windows/link/collision/Unicode and fault cleanup; K02/K05 interfaces |
| K-07.03 | Existing source hashes and atomic `complete_snapshot` | Durable creator-bound candidates, source/hash/expiry/current-policy revalidation, safe structured errors; Quân mapping |
| K-07.04 | Existing P3 scientific validator and observation semantics | Four or more runs, explicit target/baseline/unit/class/direction/threshold, decimals/zero/negative/tiny/null; Bao for domain change |
| K-07.05 | Capability choice and upload/local-grant alternatives | Connector false with a tested alternative, or actual grant/revoke/SSRF/credential controls; J01/Quân |

## New preview contract and repository

`hosted_contract/imports.py` is the shared producer contract, not a second
domain validator. It exports `PreviewRequest`, `ConfirmImport`, `PreviewImport`,
`ExcludedMetric`, `MappingIssue` and `MappingFailure`. Synthetic serializer
fixtures and their schemas live in `fixtures/k07/preview-v1-draft`.

Preview root fields are `schema_version=product-api/v1-draft`, `preview_id`,
`workspace_id`, `source_id`, `source_sha256`, `source_state_sha256`,
`mapping_candidates`, `excluded_metrics`, `excluded_metrics_total`, `expires_at`.
Preview content is bounded to 1 MiB. Excluded metric entries contain only
`project_item_id`, `metric_name`, `reason=unsupported_metric_identifier` and
`source_record_index`; at most 1000 are returned. A null total means unknown.
No metric is renamed or stripped of accents. Unsupported names must be explained
at preview; supporting them in the domain remains a separate scientific decision.

`PreviewLeases(sources, ttl_seconds=900)` injects the existing `PostgresSources`.
All operations retain its pool/current authorization, including any worker
rotation adapter injected through `sources._access`.

- `create(credential_digest,workspace_id,source_id,preview_payload)` persists the
  trusted worker projection after checking current source hash and permission.
- `read(...,preview_id)` serves the creator or currently authorized owner/admin.
- `validate(...,preview_id,source_id)` requires the creator, current source grant,
  current source hash and unexpired lease.
- `for_submission(...,preview_id)` resolves creator-bound metadata for exact
  request/receipt replay even after expiry/source change. It still checks current
  session, membership and source permission. This method admits no effect and is
  not the public preview-read route. New operations must call `validate`.
- `cleanup_expired(grace_seconds=3600,limit=100)` is a bounded server maintenance
  operation, not a browser capability. It uses the expiry index, skips locked
  rows and retains every preview referenced by any durable confirm job. It
  never deletes source bytes or snapshots. Retention of referenced leases is
  tied to the existing job/receipt retention policy.

The public request cannot supply reserved mapping fields. The HTTP adapter adds
the server `_preview_id` binding inside the existing internal mapping object;
the worker removes it before invoking P3. Existing source/job request shape is
preserved; no new queue or source repository is introduced.

## Explicit additive migration

`preview_migration.migrate(conn,schema)` (alias `migrate_preview_schema`) requires
a caller-owned non-autocommit transaction and a strictly validated existing
application schema. It returns `adopted` or `unchanged`; the caller commits.
It does not run at startup. Install after the existing K05 persistence and K04
workspace/source-scope migrations. `verify(conn)` checks version/checksum plus
installed columns/defaults/constraints/indexes/trigger/function signature.
`PreviewLeases.is_ready()` fails closed when these are missing or changed.

The migration adds `k07_import_previews`, `k07_import_failures`, an expiry index,
a bounded-reference lookup index on the existing jobs and a confirm publication
trigger. It does not modify historical K05/K04 migration checksums.

On a confirm transition to `succeeded`, the trigger requires the preview creator
to match the job creator, and workspace/source/hash/expiry to match current
source metadata and the just-inserted upload snapshot. The trigger runs inside
the existing `PostgresJobs.complete_snapshot` transaction. An expired/missing
preview aborts both snapshot insertion and the successful terminal update.
Existing job authorization and worker lease fencing remain authoritative.

**Compatibility:** after explicitly installing this migration, legacy confirm
jobs lacking `_preview_id` cannot publish. Drain or resolve such work before
adoption. Old succeeded rows are retained. This is not a claim of a live rollout
or permission to migrate an in-use schema.

`k07_import_failures` is a bounded safe-diagnostic projection with job/workspace
FK scope. Its writer must require the live existing job lease; its reader must
check current job policy. It is not another result store or job state machine.

## Joint acceptance remains distinct

| Joint row | Kiên producer contribution | Quân / common evidence still required |
|---|---|---|
| J-03.01 | Source→preview→mapping→confirm→persist, hashes/IDs and field errors | S/M/L wizard/progress/resume; UI payload equals persisted source/snapshot |
| J-03.02 | Correct snapshot input and source/citation identity for analysis | Reader/claim/citation consumer; K08/K11 full pipeline |
| J-03.03 | Retry safety and immutable confirmed input | Hosted refresh/no-op/history, reload/back/new tab; K08/K06 |
| J-03.04 | Bounded data and actionable invalid/unknown/denied/expired errors | Real mobile/keyboard/consumer recovery and paging/graph |
| J-04.01 | Durable receipt and same-key exact-request recovery | Lost ACK/reconnect/reload through browser to terminal |
| J-04.02 | Existing lease/fence/cancel truth | Worker death/stale publish/cancel-success race at UI |
| J-04.03 | Scoped receipts and source/preview/job identities | Delayed success/error must not change a new account/project/workspace context |
| J-04.04 | Current permission/tombstone/fence publication checks | API/UI/DB/object witness, no resurrection or unauthorized output |

J01 owns shared wire/schema decisions; K04 owns source/job policy; K05 owns
canonical storage; K06 owns idempotency/audit; K02 owns worker/process runtime.
This work does not overwrite those shared writers. Upload snapshots continue
to say Git provenance not supplied; no uploaded `.git` or fabricated history
is used to satisfy local canonical provenance.

S/M/L remain 1/20/200 MiB and 1k/10k/100k metric rows. Physical bytes, rows and
claims are independent dimensions. Functional tests do not close the 50k gate.
All joint rows remain open until producer and consumer run the same release.

## Evidence of this preview slice

Dedicated unit and real-PostgreSQL checks use only freshly generated
`k07_preview_<uuid>` schemas, then remove exactly their own schema. Tests cover
wire safety/bounds, source/hash/expiry, cross-tenant/missing IDs, current source
grant/session revoke, stale receipt metadata, atomic publication rollback,
valid publication, cleanup/grace/reference retention and migration drift.

Logs and per-run results are retained under workspace
`k07-verification-20261110/preview`. Early collection failures from missing
`itsdangerous` were fixed using an existing dependency directory; an intervening
test assertion involving an artificially changed expiry was corrected and the
suite rerun. Failed attempts are not counted as passes and overlapping runs are
not added. This slice is real PostgreSQL plus in-process P3, not browser, TLS,
real IdP, subprocess, staging or joint acceptance.
