# K18 native physical PITR qualification fixture

This explicit opt-in integration test creates its own PostgreSQL 16 source and
restore clusters on loopback ports 50814 and 50815. It does not connect to the
normal API database, use Docker or require an IdP. The native fixture currently
runs on macOS/Linux; the Windows Docker recovery qualification remains a
separate deployment exercise.

Requirements: explicit PostgreSQL 16 client/server binary directory containing
`initdb`, `pg_ctl` and `pg_basebackup`; Python development environment; both
ports unused; at least 4 GiB free disk reserve; permission to start native local
processes. The fixture aborts when its owned files exceed 4 GiB. Credentials
and DSN/passfile material are fresh synthetic values written mode 600 beneath
a new UUID directory. No existing cluster or source folder is altered.

```sh
K18_TEST_PITR=1 \
K18_TEST_PG_BIN=/absolute/path/to/postgresql16/bin \
K18_EVIDENCE_DIR=/absolute/path/to/private/evidence \
python -m pytest -q --tb=short tests/integration/test_k18_physical_pitr.py
```

Run it sequentially after other large transfer/load tests. The default suite
skips this test unless both opt-in variables are present. Inspect the JSON,
JUnit and PostgreSQL command/server logs in the evidence folder. A failing
attempt must remain visible alongside a later passing attempt.

The fixture publishes validated native upload/domain records and a legacy
project shared across two workspaces, then takes a real streaming base backup.
After the backup completes, it acknowledges a business project deletion and a
durable succeeded preview job, captures current signed deletion/authority
history, selects an exact WAL restore-point LSN and commits a later witness.
It switches WAL and waits for the required archived segment. The immutable
object archive is captured under the backup/GC barrier, including a tombstone
digest proved absent from the earlier base inventory.

Recovery copies the base archive into a fresh data directory, copies and
hash-checks the object closure, replays archived WAL to the requested LSN and
promotes the isolated target. It proves that the acknowledged delete/job are
present and the later witness is absent. The latest authenticated external
checkpoint is applied before any runtime grant. Old sessions are denied;
fresh synthetic fixture sessions exercise the actual catalog/source consumer
functions: the deleted project remains unavailable, the other workspace's
shared project remains readable, the live native record is unchanged and raw
workspace source bytes remain exact. These synthetic sessions are not an IdP
login qualification. No runtime application grant or public readiness is issued.

The JSON reports RPO only for the two acknowledged pre-target effects checked
by this exercise. It reports local elapsed recovery through the authenticated
checkpoint and actual consumer checks, including base/object copy into fresh
targets. It does not claim production RPO/RTO, network retrieval, power-loss or
full-scale performance. Both owned postmasters are stopped with PID/path/port
checks and their private cluster/credential directory is deleted; sanitized
receipts and logs remain for review. On an ownership/stop failure the directory
is preserved for an operator instead of stopping an unrelated process.
