# Hosted integration additions: immutable results, jobs, paged reads and reports

This is an integration contract for the current local implementation, not final
release or joint acceptance. The final source identity, combined check totals,
large-input result and distribution archive are still being completed. Historical
fixtures and old receipts retain their original scope. No IdP, paid provider,
remote CI, deployment or 50k-user claim follows from local verification.

Let `B = /v1/workspaces/{workspace_id}`. Keep current session/CSRF, workspace and
object authorization checks. Capabilities are current server facts, not static
frontend switches. Existing refresh/follow-up/export/delete bindings remain;
these additions do not require rebuilding those journeys from scratch.

## Current Quán replacement-content receiving check

The current 133-file web snapshot has identity
`5255bed607a9fcb0d7def29e94293e787242db5db57cecd30495b58d23603414`.
In a new owned copy, its unchanged 306 consumers pass with the normal command
and serial execution; 28 focused bindings pass. A standalone actual-client/port
check uses two valid 400-byte ZIPs with identical file metadata but different
SHA-256: completed and partial uploads keep different content separate, while
the same bytes in a renamed File reuse the original intent. The prior metadata
identity bug is closed for this snapshot. The first parallel polling deadline
failure remains in the receipt, alongside isolated and subsequent passing runs.

The latest receipt is
`Bang-chung/20261111-k08-k12/quan-final-producer-receiving/result.json` in the
canonical package; the earlier receiving receipt is retained separately. The
latest run explicitly points `J01_FIXTURES` at fresh producer output. All 53 K04
files match the maintained `producer-20261112-k08-k12` package byte for byte;
a supplemental actual-client check verifies its JSON/Markdown descriptor and
download bytes. No reference checkout test/build/cache was run or written.
These checks do not qualify the newly listed job/page/PDF UI bindings. Quán's
capabilities reader accepts PDF, but its current export creation/download
binding still accepts JSON/Markdown only.

## Capability-selected additions

| Server signal | Consumer behavior |
|---|---|
| `features["jobs.execution_v1"]` | Understand `cancel_requested` and `expired`, bounded attempt history, current retry/cancel decisions. |
| `features["catalog.paged_read"]` | Use result headers and bounded claim/evidence/graph routes. |
| `features["analysis.metric_observations_v2"]` | Describe the new local metric-observation analysis accurately. |
| `features["comparisons.upload_v1"]` | Read the persisted upload-generation comparison records. |
| `supported_export_formats` contains `pdf` | Offer PDF alongside the listed formats; do not infer it from local/demo support. |

Permissions still come from `allowed_actions` and current server checks. Missing
flags do not mean existing immutable data may be guessed, silently replaced by
demo data, or relabelled as a successful new analysis.

## Job progress, attempts, cancellation and retry

The existing submit/status/output/cancel/retry routes remain. Add
`GET B/jobs/{job_id}/attempts`, returning `JobAttemptPage`: workspace/job IDs,
`retry_of_job_id` (nullable) and at most three `items`, without a cursor.
Each attempt includes `epoch`, nullable start/heartbeat/lease/finish timestamps,
`outcome` and nullable `failure_kind`. The producer models are
`hosted_contract/job_attempts.py` and `hosted_contract/job_page.py`.

Treat `queued`, `running`, and `cancel_requested` as pending. A cancel request
is not proof the child stopped: wait for the actual terminal state. Terminal
states are `succeeded`, `failed`, `cancelled`, and `expired`; read output only
for success. Missing legacy timestamps/progress/retry metadata remain unknown,
not zero. Use the returned `retryable` decision and current capability/authority;
do not retry every terminal failure or create another job to hide a failed read.
Attempt outcomes also distinguish `lease_lost` and `retrying`.

Keep the original intent key/body/CAS after mutation `outcome_unknown`, and the
accepted job ID after a poll/output read failure. A trusted terminal refusal may
settle the intent. Changing question/source/selection is a new intent. A manual
retry returns its own receipt and retains retry lineage; it does not rewrite the
old terminal job. A 409 state conflict is a refusal, not a successful retry.

## Bounded readers and search

| Route | Output |
|---|---|
| `GET B/results/{result}/header` | `ResultHeader`: pinned project/result/snapshot, view schema, runtime/result summary, conversation ID and exact turn/claim/evidence/node/edge counts. |
| `GET B/results/{result}/claims?limit=50&cursor=...&search=...` | `ClaimPage` with pinned scope and page envelope. |
| `GET B/results/{result}/evidence?limit=50&cursor=...&search=...` | `EvidencePage` with the same envelope and pinned scope. |
| `GET B/results/{result}/evidence/{evidence}?claim_id=...` | One `EvidenceView`, including its citation/counterevidence relationship to the requested claim. |
| `GET B/results/{result}/graph?limit=50&cursor=...` | `GraphView` with nodes/edges, truthful `truncated`, and opaque continuation. |

The request limit is 1-100, default 50. Search is bounded text (at most 128
characters), not a SQL expression or fuzzy semantic diagnosis. Result item pages
are read from verified persisted projections. The existing complete result route
remains, but do not use a full-result fetch as the normal implementation of every
page or invent empty pages when projection availability fails.

Use `next_cursor === null` to determine exhaustion. `total_kind="unknown"`
means the page does not assert a total; the header's exact counts remain separate.
For graph pages, `truncated` describes that page's subset and can remain true on
the final page. Accumulate/deduplicate IDs as needed; edge pages carry their
endpoint context. Do not label a partial graph complete from the first response.

Cursors are opaque and bound to actor/session/workspace, current authority,
query/filter/page size, projection and catalog revision. Keep their query fixed
while continuing. On `400 cursor_invalid` or `410 cursor_expired`, discard the
page continuation, tell the user the page needs refreshing and restart page one
for that query. Never reuse a cursor across accounts/workspaces or mix old and
new revision pages. This recovery does not authorize replaying a mutation under
a fresh key. A permission loss still wipes private UI state.

## Pinned observations and generation comparisons

The public analysis request still selects `backend="local_evidence"`. It does
not accept a provider/API key/endpoint or an internal `analysis_model` override.
The server pins an accepted job to its analysis version. Historical
`upload-snapshot-observation/v1` results remain unchanged; the new version is
`upload-snapshot-metric-observation/v2`. Both are local, `external_call=false`,
causal status `unverified`; neither is an LLM diagnosis or causal proof.

The new analysis explicitly groups every validated metric row by
`(run, metric, class, unit, unit_origin)`. Claims/evidence report complete counts,
min/max and mapped step endpoints. Raw operands remain in the pinned immutable
snapshot, with source/record/operand hashes. This is an explicitly labelled
aggregate, not a row sample. The limit is 64 series; exceeding it is a typed
failure with no dropped rows or fabricated success. Unknown class/unit remains
explicit; no unit conversion or automatic target/unit inference is performed.

Changed uploaded generations also persist an immutable
`hosted-upload-generation-comparison/v1` canonical record. Discover it through
`B/projects/{project}/records?kind=snapshot_comparison`, then read its record
address. The existing `RefreshResult` remains unchanged: it has project,
previous/current snapshot, disposition and revision, not a new comparison-ID
field. No-op refresh keeps the snapshot and creates no new comparison.

Comparison series retain paired/unchanged/increased/decreased/added/removed
counts, nullable adverse count, exact decimal-string delta ranges and operand
hashes. All row counts reconcile across the two pinned snapshots. Missing policy
is `assessment="not_assessed"`, with direction/threshold/adverse null; null is
not zero or a judgement of safety. The aggregate is limited to 64 series and
keeps `causal_status="unverified"`. A unit/schema/policy mismatch is rejected,
not disguised as removed/added observations. Old result/history/export stays
bound to its original generation after another refresh.

## PDF and download compatibility

Export still uses `POST B/results/{result}/exports` with only
`{"format":"json"}`, `markdown`, or capability-advertised `pdf`; it returns
`201 ExportDescriptor`, not a queued export job. Add `pdf` to the format/handle
reader only when supported. PDF handles use `ex_pdf_{result}_{nonce}`, MIME
`application/pdf`, and a generated ASCII `.pdf` filename. All formats preserve
the descriptor's project/result/workspace, byte size, SHA-256 and original expiry.

Keep key/receipt while an export outcome or download is uncertain; do not start
another export merely because the byte read failed. Authenticated GET/HEAD and
one byte range use the same current creator/workspace/result/project/source
authorization. Export expiry is 15 minutes; retries do not renew it. No signed
bearer URL or token query parameter is introduced. Expiry/size/range information
is not disclosed before authorization. `no-store` and `nosniff` remain mandatory.

Download completion requires the actual expected bytes and whole-artifact hash.
A partial range is not hash-verified by comparing it alone with the full-file
SHA-256. Reassemble and verify the complete artifact before claiming completion.
Permission/expiry is rechecked before each 64 KiB chunk, sends are bounded, and
an interrupted response never becomes a fake complete file. Four download slots
per API process protect retained buffers independently of metadata reads; a fifth
gets `503 service_unavailable`, which can be retried under the existing handle.

PDF uses packaged, embedded Noto Sans Mono (SIL OFL) and a pinned ReportLab
renderer in an isolated child. Vietnamese NFC/NFD is readable/copyable as NFC;
JSON/Markdown preserve original stored text and precision. Project text is inert:
no HTML/image/URL fetching. Unsupported glyphs return `400 invalid_request` and
JSON/Markdown remain available; missing renderer readiness returns unavailable,
not an ASCII substitute. See `docs/k12-exports.md` for exact limits and test scope.

## Explicit deployment preparation

Use the operator migration tooling with the target schema and database secret
referenced through an environment variable. Do not run DDL during API startup.
The appended versions are v11 PDF export constraints, v12 durable execution,
v13 read projections and v14 pinned analysis-version request validation. Existing
v1-v10 DDL/checksums remain fixed. A separate strictly verified read-index ledger
installs only through explicit migrations; partial/drifted extensions are refused.

`scripts/k05_storage_admin.py migrate --schema ...` handles the canonical and
project-policy installation order. `verify` verifies it; `read-indexes` explicitly
installs the known actor indexes when prerequisites exist. Historical result
projections require explicit bounded `rebuild-views --object-root ... --workspace
... --limit ...` backfill, continuing from the returned result cursor. Reads do
not silently backfill. Do not deploy a new capability over an incomplete schema
or assume an older binary can reverse these migrations.

The final fixture/export inventory, source manifest and qualification receipt
will pin the integration build. Until then, local implementation and focused
checks must not be relabelled as all K08-K12 passed or joint Quán acceptance.
