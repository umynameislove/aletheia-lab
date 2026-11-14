# Windows 200 MiB completion investigation

The later `Ket-qua-Windows-K08-K12-20261112-153319.zip` return is a different
failure: its old 639-file runtime passed the smoke rerun (23 tests), then the
strict upload phase passed 12 tests and failed while committing one 4 MiB part
under the unchanged five-second transaction deadline. It never reached final
completion or readback. That archive has no part index/progress/commit-wait
witness or PostgreSQL server log, so the original commit outcome and reason for
the stall remain unknown. The earlier 60-second final INSERT case below must
not be used as the explanation for this new result.

An explicit replay now rechecks current authority, the sealed manifest and
active upload state under the existing upload-row lock, then checks the part's
stored size/hash before sending its payload again. A matching committed part
returns progress without a second payload INSERT. This does not automatically
retry an unknown transaction or convert the first failure into success. The
current consumer already retains the upload intent and reads status before an
explicit retry; it does not need a duplicate recovery implementation.

The strict transfer test still requires all 50 parts, 200 MiB and exact source
readback with the same deadlines. Its v2 diagnostics now cover create, each
part, SQL/body/context exit, completion and readback. On failure a bounded
read-only fixture-owner observation records visible metadata before fixture
cleanup, preserves the original exception, and makes no recovery writes. A
missing row is not a rollback certificate. Controlled real PostgreSQL commit
and rollback tests separately verify same-intent recovery, one stored part,
current authorization and no repeated payload bind for a committed part.
Neither those faults nor a native Mac pass reproduce or qualify the Windows
commit stall; the new Windows operator run must retain fresh diagnostics.

The returned Windows archive keeps two different outcomes: the aggregate run
has 316 passes, one failure and seven skips; the isolated 200 MiB rerun passes.
The isolated result does not replace the aggregate failure. Source/test identity
and raw receipts are recorded under the canonical package's
`Bang-chung/20261112-windows-receiving/200m` directory.

All 50 parts and 209,715,200 bytes were committed before completion failed.
The final source INSERT ran for 59.984 seconds against a 60-second transaction
deadline. Observations mostly show PostgreSQL `ClientRead`, with no lock
blockers. This identifies a pending client-to-server transfer; it does not prove
which Windows/VM resource caused the stall. The recorded container was not
restarted or marked OOM-killed; its later RAM reading is not a peak measurement.

The unchanged completion method sent the entire verified bundle back to
PostgreSQL even though its parts were already stored there. A controlled native
PostgreSQL probe reproduces the same driver cancellation exception: psycopg
3.3.8 binary, paused during a real 16 MiB send, raises `TypeError` when its
cancellation path resumes the pending C generator. This is a driver-mechanism
reproduction, not a reproduction of Windows network speed or aggregate load.
The [pinned driver source](https://github.com/psycopg/psycopg/blob/3.3.8/psycopg_c/psycopg_c/_psycopg/generators.pyx)
and [async cancellation path](https://github.com/psycopg/psycopg/blob/3.3.8/psycopg/psycopg/connection_async.py)
explain why a cleanup exception can replace the original cancellation.

Completion now uses a private typed reference to the inspected upload. Its
final INSERT assembles ordered staged bytes on the server and verifies their
count, byte size and SHA-256 against the validated seal. PostgreSQL provides
[SHA-256 for binary strings](https://www.postgresql.org/docs/16/functions-binarystring.html)
without a new extension. Current authorization, upload locking, manifest
identity, publication, ready state and part cleanup remain in the same
transaction. Missing or altered bytes after inspection cannot publish a source.
The CPU offloads and existing 60-second/200-MiB/50-part bounds are preserved.

Transaction handling classifies a masked driver error as a timeout only when
its own timeout context actually expired. Commit uncertainty remains
`TransactionOutcomeUnknown`; a body failure remains `TransactionUnavailable`.
Neither is automatically replayed. A new explicit caller cancellation remains
cancellation after cleanup. An unrelated early `TypeError` remains unchanged.

Native verification covers actual 1/20/200 MiB transfers, source/readback hashes,
small final INSERT parameters, reordered parts, altered/missing parts, actual
driver cancellation, closed-connection replacement, zero durable effect and
pool recovery. The old disk-full injection test was updated to recognize its
known INSERT after a leading CTE; its real PostgreSQL error and rollback/retry
assertions remain. The original failing test-group log is retained.

The affected Windows machine must rerun both its aggregate suite and the
isolated case with the new source. Native passes do not certify Windows
aggregate stability. No driver pin, DDL, timeout or test-size relaxation is
part of this fix.
