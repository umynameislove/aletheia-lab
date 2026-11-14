# K15 — current job snapshot polling

`GET /v1/workspaces/{workspace_id}/jobs/{job_id}/updates[?cursor=<opaque>]` provides small, authorized status snapshots. Only enable clients when workspace capabilities advertise `jobs.polling_v1`. Existing job GET, output, cancellation and retry contracts are unchanged. The route uses short database transactions and releases the connection before sending the response. It holds no connection per browser, stream or waiting interval. No SSE infrastructure or per-user database cache is introduced.

Example pending response (cursor abbreviated):

```json
{
  "schema_version": "product-api/v1-draft",
  "workspace_id": "workspace1",
  "job_id": "job1",
  "revision": 2,
  "disposition": "snapshot",
  "job": {"schema_version":"product-api/v1-draft","workspace_id":"workspace1","job_id":"job1","status":"running","progress":null,"operation":"preview","created_at":null,"deadline_at":null,"terminal_at":null,"attempt":1,"max_attempts":3,"retryable":null,"output_url":null},
  "cursor": "opaque-signed-value",
  "poll_after_ms": 2173,
  "authorization_valid_for_ms": 4000
}
```

This protocol coalesces **current state**, not a historical event stream. The durable per-job revision starts at 1 and increases on public status metadata changes. Heartbeats and lease renewal do not increase it. A client may miss intermediate states. `snapshot` contains current state; `unchanged` has `job:null` and requires the client to retain its matching scope/revision state; `resync` contains a full current snapshot after a revision gap greater than one or catalog authority change. Do not infer that every event was delivered. A client without cached state must omit its cursor and fetch a snapshot.

The signed cursor expires after 300 seconds. It binds workspace, job, actor, session context/generation and membership revision. Treat it as opaque; do not log or persist it beyond that scope. Every call independently checks current account, session, membership and parent source/project authorization **before** reading or validating the cursor. A cursor is never a grant. Cross-scope or stale membership continuation returns 400 `cursor_invalid`; expired continuation returns 410 `cursor_expired`. Clear the old cursor and retry once without it only while the same current session and scope remain authorized. A 401/403/404 clears private state and stops polling. Hidden/deleted parents remain unavailable.

Quán frontend binding:

1. Watch only pending jobs that the active view needs. Keep at most one in-flight poll per `(session generation, workspace, job)` across callers. Abort obsolete reads when switching workspace, job, session or tab visibility; keep the accepted job itself. Never submit a replacement job merely because status delivery was interrupted.
2. Record a monotonic `requestStarted` immediately before the request. Freshness expires at **`requestStarted + authorization_valid_for_ms`**, never receipt time. Discard a response received after that deadline; slow network delivery cannot extend private-data visibility. Retain the existing four-second authority cover/wipe: if the next successful check cannot occur before expiry, cover/wipe private UI at expiry. Offline, background-tab throttling and reconnect backoff do not extend that authority lease. Verify actual browser timer/background behavior with the existing visibility handlers. This client gate is required for the user-visible ≤5s revocation objective.
3. Validate the exact response schema and matching workspace/job, then discard versions older than the cached revision. Deduplicate by `(session generation, workspace, job, revision)`. On `unchanged`, retain cached state; on `resync`, replace it and invalidate dependent action state. Keep cursor on every successful poll. A newer revision may skip intermediate states.
4. For a pending job, wait the returned `poll_after_ms` (2,000–3,499 ms, including server jitter and stable-state backoff). HTTP `Retry-After` is an integer **ceiling** for clients using standard seconds; prefer the precise JSON milliseconds on successful responses. A terminal response has `poll_after_ms:null`: stop watching it. Retry/cancel permission remains a current control decision from the existing job/control endpoint; `retryable:null` in this protocol deliberately means unknown.
5. On network error/429/503, apply equal-jitter exponential retry, reset after success, honor `Retry-After` as a minimum. Reference `reconnect_delay_ms`: ceiling grows 1s, 2s, 4s, 8s, 16s, 30s; delay is `max(Retry-After, ceiling/2) + random[0,ceiling/2)`. A server minimum can exceed 30s. Keep the UI covered after its four-second authority lease; resume only after a fresh authorized snapshot. Stop on terminal state, explicit logout, unauthorized response or disposed view.

The API admits at most 16 polling transactions per process, at most four from one workspace at once, with no server queue or persistent per-actor map. Overload returns 429 plus `Retry-After: 1`; this is bounded backpressure, not a normal-capacity success. Duplicate browser polls still consume database work; frontend deduplication and jitter are required. Multiple replicas multiply these local limits and must fit the fleet connection budget. No backend can protect private UI that a client keeps displaying past its authority lease.

Use the K15 native evidence for durable revision, current revocation, cursor scope/expiry and metadata-only behavior. Compare real request rate, SQL rate, body bytes, CPU/RSS, connection occupancy, latency and rejection rate for 100 pending jobs and a 30% reconnect burst. The local evidence does not certify 50k browser users. Joint Quán browser validation, background/slow-network authority cover, multi-replica deployment and declared 50k normal-load acceptance remain external gates.

The K14 native process follow-up also observes 20 actual queued preview jobs through this HTTP protocol, across two ten-job bursts. Three cursors per burst resume through a new verified TLS client transport after the actual worker has completed the queued work: six logical job observations over two fresh transports, not six distinct network clients. Every continuation has a monotonic revision, scoped job identity and a ledger-consistent terminal snapshot; each actual output is checked against its source SHA-256. All 60 same-key admission requests still yield exactly 20 completed jobs. This adds real HTTP/TLS resume evidence to the earlier adapter probe. It does not force an existing socket failure, run a proxy idle timeout, exercise browser hide/show or prove a simultaneous reconnect storm. Those joint/deployment cases remain separate; the readonly Quán reference now contains the polling/deletion bindings and request-start freshness work, whose matching-release browser replay is owned by the consumer acceptance run.
