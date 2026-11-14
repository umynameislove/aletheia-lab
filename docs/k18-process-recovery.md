# K18 — process loss with retained storage

Run the native POSIX qualification against a disposable PostgreSQL database with permission to create UUID-owned schemas and restricted fixture roles:

```sh
python -m pytest -q -s tests/integration/test_k18_process_recovery.py
```

Set `K18_TEST_DATABASE_URL` through the operator's existing secret mechanism. Do not paste a connection URL or password into the command, logs or handoff. The test creates small synthetic inputs, real separate API and worker processes, HTTPS certificates, restricted API/worker roles, and real persisted jobs/objects. It removes its schema, roles, private claim checkpoints and TLS key. It does not start Docker or an identity provider. The native POSIX process-control case is skipped on native Windows; run it inside the Linux Docker test environment on Windows, or on an authorized native POSIX runner.

The API child reuses the existing Uvicorn HTTPS composition fixture with actual PostgreSQL-backed sessions, CSRF, current authorization, runtime role/schema binding and lifespan shutdown. A session is seeded solely for this local fixture. It does not exercise full ManagedRuntime/OIDC discovery or real login. The independent worker uses the actual execution-profile repository, database-owned leases, canonical publication and shared object filesystem. No test rewrites lease/deadline timestamps to accelerate recovery.

Three faults are injected:

| Case | Durable state before signal | Real injected fault | Recovery assertion |
| --- | --- | --- | --- |
| `before-effect` | HTTPS 202 receipt received; worker owns an actual one-second lease | Kill the live API and worker processes with SIGKILL | Restart the same HTTPS endpoint and a new worker, wait the real lease clock, reject the stale worker publication, publish exactly one effect and return the same original receipt on replay |
| `after-acknowledged-effect` | Canonical effect committed and authenticated HTTPS job output received | Kill both live API and publishing worker with SIGKILL | Restart API with retained PG/objects; authenticated consumer receives exactly the previously acknowledged output and verified canonical content |
| `graceful-inflight-api` | An HTTP mutation has entered the API and waits on a real workspace lock | Send Uvicorn SIGTERM while that mutation is in flight | Process remains alive until the lock is released, drains the actual 202 ACK, closes its database pool, then restart processes and consume the durable work |

Every case validates matching job/snapshot identity, a typed HTTPS output, current session access, canonical object bytes, unauthenticated output rejection, same-key receipt replay, one canonical snapshot, one admission audit and one publication audit. The endpoint runs on the same logical origin and port after restart. Recovery time starts immediately before sending the fault signal and ends after authorized HTTPS output and canonical content are usable. It includes process startup, readiness, applicable real lease expiry, publication, database/object access and consumer validation. Each case asserts RTO ≤60 seconds. RPO 0 for **acknowledged effects** is directly exercised by the after-effect case; the before-effect case separately proves retained acknowledged job intent, not an effect that had not yet existed.

The initial local native run observed RTO 1.095s before publication, 0.524s after an acknowledged effect and 0.869s for in-flight graceful API stop. These are observations from a small local fixture, not production guarantees. Raw JSON/logs are in the canonical K18 evidence directory. Reports label unexercised effect-loss values `null`; booleans for a case-specific checkpoint describe whether that checkpoint was exercised.

`test_k18_download_queue_drain.py` adds a concurrent download/queue case. It publishes a valid immutable JSON report with 8 MiB of trailing whitespace, starts a genuinely slow authenticated TLS reader, and confirms metadata still works with a one-connection API pool. The queued job already has its HTTPS 202 ACK. At SIGTERM, only 1,572,864 of 8,393,858 body bytes had been sent, and no final body frame had been observed. The client then intentionally disconnects; this abandoned transfer is never counted as a fully delivered file or as a job cancellation.

After bounded API shutdown and restart with retained storage, the same authorized export supports exact 30-byte Range recovery and a complete SHA-verified download. The accepted job retains its original receipt, runs to terminal through the real worker and has exactly one admission/publication audit. Unauthenticated Range is denied with 401; a subsequently revoked current membership receives 404 without ETag or content-range disclosure. The first native observation took 11.389s from SIGTERM to authorized file and job usability with a configured ten-second graceful limit. This is a bounded stop/restart distribution proof, not large-report rendering, successful completion of the abandoned old transfer, or rolling availability across replicas.

This proves the local **process recovery tier** with PostgreSQL and object storage retained, including the specified concurrent slow-download/queue stop/restart case. It does not prove storage loss, power loss, compromised host resistance, PostgreSQL restart, remote network recovery, real login/provider startup, or 50k traffic. Storage restore/PITR has its separate K18 recovery-point proof. API/worker image rollout, staging download behavior across multiple replicas and cross-person deployment remain K18.03/K18.06 deployment gates; these native fixtures do not close them.
