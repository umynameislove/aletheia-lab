# Hosted operational observations (K16)

The API and managed worker configure safe JSON logging at their process entrypoints. This is a process-local observation system, not evidence of a deployed monitoring service or verified 50,000-user capacity. The release identity is operator-supplied SHA-256 metadata; the release manifest must establish what those bytes identify.

## Correlation and privacy

The HTTP response's server-generated `x-request-id` joins the response to transactional action audit. Incoming request IDs are not trusted. Worker records contain an opaque job ID; `k16_observe.py journey --job-id …` looks up the job's persisted request ID and its committed audit records. Request/action/job/object IDs belong in this restricted operator view and safe logs, never metric labels. The audit trail is authoritative for committed effects; a timed routine returning or a worker settling a failed job is not proof of business success.

`SafeJsonFormatter` uses a fixed event/field allowlist. It never formats logger arguments, arbitrary extras, exception text, stack traces, headers, cookies, source data, questions, URLs or evaluator-only content. Unknown third-party diagnostics become a fixed diagnostic event. Readiness retains only a known reason enum, including the current combined `polling_retention` check. Protect and retain these minimized logs as operational data; opaque correlation IDs still link activity.

HTTP observations cover every request, with fixed route/method/status/outcome dimensions. A successful response is logged first and every 100th occurrence of its series; failures are logged subject to queue capacity. Histogram buckets cover elapsed execution including interruption/cancellation; use the cumulative count and sum rather than averaging percentiles. HTTP bytes count bytes accepted by ASGI `send`, not proof that a remote client received or verified the full file.

The handler uses one daemon writer and a 256-record queue per process. Saturation drops safe log records rather than blocking HTTP I/O; cumulative counters continue. `metric.snapshot` is emitted initially and every 15 seconds with dropped/queued/writer status. A permanently blocked collector can leave the daemon blocked after the bounded two-second shutdown join; this is visible as `writer_alive`. No per-user logging threads or unbounded buffers are created. IPC report-renderer children do not configure this handler.

## Resource observations

`RuntimeTelemetry.bind_pool(pool)` reads only the owned Psycopg pool's public `get_stats()` values for size, available connections and waiting requests. A weak reference does not extend pool lifetime. A missing or failed accessor omits the observation; missing is not zero. `watch_loop(loop)` uses one timer to observe scheduling lateness once per second. Closing the process log owner cancels that timer.

Blocking executor activity/waiters are sampled from the shared admission controller at snapshot time, so queued callers remain visible before admission. HTTP admission and job polling have fixed active/rejected/request counters. Stages `database_transaction`, `blocking_execution` and `report_render` preserve the caller's task and cancellation semantics. Transactions include commit/rollback/deadline handling; admitted blocking execution excludes queue wait. These are routine timings, not inferred CPU time or business completion.

The read-only operator CLI samples bounded pending work independently of request handling:

```sh
python scripts/k16_observe.py snapshot --schema OWNED_SCHEMA --limit 100 --output snapshot.json
python scripts/k16_observe.py journey --schema OWNED_SCHEMA --job-id OPAQUE_JOB_ID --output journey.json
```

Supply the connection string through `ALETHEIA_OPERATOR_DATABASE_URL` using the authorized secret mechanism; never put it in a command argument, report or chat. Output creation is exclusive. The connection uses a read-only transaction, one-second statement timeout, 250-ms lock timeout and a 20-second overall bound. Use a read-only operator DB role in deployed environments.

Queue samples are capped per operation/state. `truncated=true` and `pending_count_exact=false` mean a lower bound, never an exact queue depth. Oldest-age lookup uses the queue index. The K13 due-deletion sample is explicitly unavailable before its schema exists. DB backend/wait/deadlock/temp/block values are database-wide and cumulative; they are neither application pool counts nor a per-load-run attribution. A query timeout/lock failure is unavailable, never a fabricated healthy zero. Journey audit records are bounded and ordered by opaque audit ID; they are not a complete chronological trace.

Run the operator sampler periodically from one authorized operator process, not from every API request/user. Join queue age to process pool/loop snapshots using the same observation window and release. Do not retain raw query text or DB credentials.

## Alerts and operator actions

`AlertEvaluator` consumes consecutive cumulative snapshots for one process. Process/release/counter changes reset the baseline and do not manufacture recovery of an old incident. Window deltas supply HTTP failures and worker exceptions. Initial log loss is visible immediately. Transport failures are separate from server-error burn unless an actual 5xx was observed.

These default operational rules are provisional, not a measured product SLO. The burn rule uses a 1% provisional budget and fires at 5× after at least 20 HTTP requests. The release/load acceptance guide retains its own stricter business-action deadlines and denominator.

| Alert | Default trigger | Owner and response | Recovery witness |
|---|---|---|---|
| queue_age | oldest pending ≥30 s | Operator: inspect worker readiness, oldest durable job, lease and stage timing; preserve original deadlines | Age falls and accepted work reconciles to terminal |
| pool_wait | ≥1 waiter | Operator: inspect pool plus DB lock snapshot; reduce admission before changing pool limits | Waiting returns below threshold and the next authorized mutation succeeds |
| event_loop_lag | ≥250 ms | Operator: inspect admitted blocking stages, worker CPU and health reserve | Delay falls and an independent tenant completes a meaningful request |
| http_burn | ≥5× window burn | Operator: join safe request ID to action audit; inspect release/dependency readiness | A later valid window recovers, with effects/data checked |
| worker_error | new execution exception | Operator: join job to durable attempt/audit before retry | Defined terminal/retry outcome, no duplicate committed effect |
| log_drops | initial loss or new loss | Operator: restore collector throughput; treat sampled traces as incomplete | Drop counter stops increasing and writer remains alive |

The local test drives actual readiness 503 responses through the ASGI wrapper, observes 20 requests and a firing burn alert, then restores 200 responses and observes resolution. A real blocked output stream proves queue/drop bounds. Native PostgreSQL tests hold an actual table lock, observe the read-only sampler fail at its lock bound, release it and verify recovery. These are local fixture exercises; deployed routing, paging and on-call delivery remain an operator acceptance step.

The follow-up replay `tests/integration/test_k16_runbook_replay_postgres.py` combines actual verified TLS sockets with an owned PostgreSQL dependency lock. Twenty readiness probes fail, the alert fires, and the operator's read-only sampler reports its real lock timeout rather than zero work. The operator rolls back the lock-holding transaction; twenty readiness probes, a current authorized session/capability read and a subsequent source commit/full hash check then pass, and the alert resolves. Alert windows use actual monotonic timestamps. The fixture verifies its schema is gone after cleanup. This is a separate local runbook replay, not a second person's production on-call exercise.

## Dependency and image evidence

```sh
python scripts/k16_dependency_report.py --output NEW_EMPTY_OUTPUT_DIRECTORY
python -m pip_audit --local -f json -o NEW_AUDIT_FILE.json
```

The report resolves the installed base+hosted transitive dependency closure with markers/extras, checks declared constraints, emits exact observed version pins, hashes installed metadata/license documents and produces SPDX 2.3. The 2026-11-13 local closure has 52 packages, all covered by the contemporaneous vulnerability audit with zero known findings. The whole development environment has 119 audited packages and one local project excluded because it is not on PyPI. Scanner results are a time-bound database observation, not a penetration test or assurance that no vulnerability exists.

The generated pins describe this Mac environment. They are not wheel artifact hashes, a reproducible Linux image lock or a Windows qualification. SPDX output is validated against the official SPDX 2.3.1 schema. The original frozen receipt had four unidentified license declarations; the follow-up inventory now identifies their exact installed notice sets using `scripts/k16_license_reviews.json`. ItsDangerous 2.2.0 matches its tagged upstream BSD-3-Clause text. ReportLab, SciPy and xlrd retain their complete multiple notices as explicit SPDX `LicenseRef` extracted text alongside the core BSD declaration. ReportLab includes Bitstream Vera and DarkGarden font/GPL notices; SciPy includes conditional binary-runtime and bundled source notices; xlrd's earlier-work notice has advertising and retained-acknowledgement clauses and is not silently labelled standard BSD-4-Clause.

Identification is gated by the exact version and complete discovered license-file/hash set; the SciPy review also binds the observed Mac architecture. A changed, oversized, unsafe, truncated or different-platform notice set remains `NOASSERTION` and requires a new review. The custom references identify known exact text; they are not standardized license substitutions or redistribution approval. `licenseConcluded` remains `NOASSERTION`: no package-wide binary audit or legal compatibility verdict is asserted. Old sealed receipts and their four-unknown observation remain unchanged. See `docs/k16-license-notice-review.md` for the primary sources and distribution-review boundary.

K18 owns Dockerfile, image pin/build, non-root user, secret injection and deployment topology. Static source review is distinct from a built/scanned image or deployed TLS/monitoring validation. Do not attach a Docker build or real-IdP pass to these local K16 receipts.
