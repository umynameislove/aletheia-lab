# K-07 / K-05 migration compatibility — 2026-11-10

Historical defect report. In the 11 November source, core verification recognizes
only an independently verified, checksum-pinned preview extension; focused
compatibility tests pass against private native PostgreSQL. The binary source
insertion patch is installed, and source/chunk reads use binary bytea transport.
Failed L runs below remain historical failures. On 11 November, a new native-PG
run passed exact 200 MiB/50-part transfer, persisted resume and binary source
readback with matching SHA256. Its monitored PG/evidence/temp peak was
252,702,720 bytes, within the agreed 500 MiB cap. This compressible fixture proves
the physical-byte journey, not incompressible I/O capacity or joint acceptance.

Observed on the existing uncommitted `feat/product-api` checkout, using actual PostgreSQL and a UUID-owned `k07_compat_*` schema. No core ledger signature/checksum was rewritten and no shared implementation was edited by this check.

`tests/integration/test_k07_migration_compatibility_postgres.py` asserts the required behavior: supported K-07 extensions must leave K-05 verification usable. Current result is **1 passed / 1 failed in7.04s**, with unchanged before/after source hashes in workspace `k07-prepared/migration-compat-current`.

| Sequence | K-05 core verification | Extension verification | Core ledger |
|---|---|---|---|
| Fresh K-05 V6 | true | — | original V1–V6 rows |
| V6 → K-07 staging | true | staging=true | unchanged |
| V6 → staging → preview | false | staging=true; preview=true | unchanged |

The preview extension adds the required `k07_jobs_preview_ref` index and `k07_confirm_preview_guard` trigger to `j01_jobs`. K-05 `_signature` includes every index shape on its base tables and every noninternal trigger/function body on them. It therefore correctly notices a new shape but currently cannot distinguish a recognized verified extension from unknown drift. The resulting stored/actual signatures were `e118222d7559e0445755c77d8e586894dc079cdce2f71dbd4662d47d0b31e78f` and `e95a11519c62dfdec61c9fc837687a4a04c940284bd1322a63a8a3f780327982` in this schema.

The test's failed assertion is retained as a real integration defect, not marked xfail or counted passed. K-05's migration owner must choose and implement the compatibility boundary. K-07 must not refresh a historical core signature to make readiness green.

## Narrow owner options

1. **Recognize exact independently verified extensions in the core signature projection.** Before ignoring the known preview index/trigger in base facts, verify the installed K-07 ledger checksum, exact index/trigger/function definition and enabled state against supported bytes. Ignore only those exact verified additions, and still compare the projected base shape with its previously stored signature. Unknown objects, altered predicates, disabled guards and modified trigger functions must continue to reject readiness. This preserves existing core migration bytes and allows independent K-07 ledger ownership. Avoid name-only wildcard exclusions and do not temporarily drop/disable a guard to calculate a signature.

2. **Append an explicit core adoption migration for the supported K-07 extension shape.** The upgrade precondition must first prove that base-only projection matches the old stored signature and every declared K-07 extension matches its supported ledger/DDL. Store the newly accepted combined shape only in a new append-only version, leaving all historical DDL/checksums/signatures unchanged. The migration owner must test prior V4/V5/V6 upgrades with installed extensions and unrelated-drift rejection. Merely rerunning `_signature` and overwriting a prior row is not adoption.

Until an option is implemented and verified, full import readiness stays closed. The new operator `migrate-imports` action installs staging and preview in one caller transaction, verifies both extensions and the core, and rolls the whole extension transaction back if verification fails. Existing `migrate|verify|cleanup` staging commands remain available. No startup DDL is added.

## Restricted runtime roles

`hosted/preview_runtime_privileges.py:grant_preview_runtime(conn,schema,role,profile=api|worker)` reuses `grant_upload_runtime` and the existing K-02 role validator. API gets preview/failure/ledger SELECT and `UPDATE(preview_id)` for preview row locks. Worker additionally gets INSERT preview/failure, `UPDATE(payload)` only on failure diagnostics, and `UPDATE(snapshot_id)` on existing upload snapshots because the immutable snapshot check in the frozen publication trigger uses `FOR SHARE`. No preview payload update, source deletion/truncation, preview cleanup, ledger write, DDL or role-creation authority is granted. Unexpected inherited delete/truncate authority is rejected rather than silently accepted.

The actual nonowner API/worker regression passed: preview create/read, structured failure INSERT+upsert, and confirm publication through the enabled guard. It also attempted and observed denial for source/preview DELETE, preview payload UPDATE, migration-ledger UPDATE and CREATE TABLE. Roles were UUID-owned LOGIN/NOINHERIT/NOSUPERUSER/NOBYPASSRLS principals and were dropped after the owned schema. This is a focused database permission witness, not full managed-runtime or joint acceptance.

The first restricted-role attempt failed because its owned worker staging directory had not been created; that failed test is retained. After fixing that fixture, the selected role test plus14 upload-model checks completed **15 passed in4.67s**. A separate operator rollback/old-command test completed **1 passed in5.62s**. Later static tightening adds inherited TRUNCATE rejection and prints operator success after commit; those final changes require a focused rerun once the shared local database service is healthy.

## L transfer evidence and resource boundary

The shared owner applied the reviewed two-line source patch; its SHA256 is `2d2715c56367d669798bf94ec4f86f812d0a9d41a4ae7f687549712115e02661`. The first L200MiB rerun still timed out at the60s publication transaction: **1 failed,22 deselected,82.17s**, source hashes stable. A diagnostic rerun removed duplicate fixture copies and observed completion reaching ready, but its subsequent existing full `SELECT payload,sha256` text-bytea source read caused PostgreSQL to close both read and admin connections unexpectedly. That run is **1 failed,27 deselected,81.43s**, not an accepted L result. The test-owned schema `k07_upload_a89aac9b3c474abaacd252e0dd45d31f` could not be cleaned at that point because the server connection died. Only that exact verified-owned schema may be cleaned after service health is established; this check does not authorize restarting/reconfiguring shared services.

The source-read failure is consistent with pressure from whole200MiB bytea text/hex expansion, but that cause has not been proven by server logs. The next source owner remedy should use a bounded binary/streamed source-read boundary before repeating the L journey. No target is reduced. The test now emits ready/progress/timing evidence before a subsequent read so that a read failure cannot hide the completed stage, but no further large run was started after the shared-service failure. No capacity or50k claim follows from any of these functional checks.
