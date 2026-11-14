# K-07: archive, canonical identity and mapping producer

Current code update, 2026-11-11: mapping identifiers use one packaged Unicode 14
repertoire shared with generated browser validation. Canonical NFC metric, run
and field names are accepted without renaming. Invisible/default-ignorable
characters are rejected; sensitive-label checks also detect their use to conceal
private field names. Existing ASCII identities and scientific fixtures retain
their bytes. Receipts and prepared-patch descriptions below refer to their
historical 10 November snapshots; seal/cache changes are now integrated.

Unicode 14 matches the oldest supported Python runtime (3.11), keeping NFC
validation consistent with Python 3.12 and the browser. Newer Kawi characters
and combining marks are rejected, including sequences that older normalizers
would mistakenly consider NFC. The superseded Unicode 15 policy is retained
only in external historical evidence.

The independent metric-row check now executes 100,000 observations through a
real isolated parser and retains every record index, run/metric/step identity and
source hash. Its measured V1 snapshot output is 36,815,222 bytes. Confirm alone has
a bounded 64 MiB worker-output budget; reports, preview, analysis, follow-up and
refresh retain their previous 32 MiB output budget. This is distinct from the
200 MiB physical-byte check and does not certify large analysis or 50k load.

The completion pass adds an explicit `project-mapping-request/v2` for class and
unit metadata. A selected class source column supplies a bounded canonical
label; an absent class column means aggregate observations. Every metric
definition declares a unit or `null` for unknown. An optional unit source column
must match that declaration exactly. Observations retain `class_label`, `unit`
and `unit_origin` (`source`, `declared` or `unknown`) through mapping, snapshots,
comparison, results and history. Spaces in class labels and units such as `%`
and `items/s` are supported without renaming source values.

Direction and absolute regression threshold apply to every class of that metric.
Comparison requires the same class and unit, rejects mixed V1/V2 snapshots and
rejects a changed unit instead of inventing a conversion. V2 changes use exact
decimal subtraction over the stored numeric values; this also rejects rehashed
tiny-delta forgeries. V1 requests, schemas, identities and stored history retain
their original behavior. The completion check compared all ten captured V1
schemas exactly and passed 133 focused domain/property/local-pipeline tests.
Publication also applies the existing sensitive-label, PII and source-secret
checks to class/unit values, including user-declared units.
Current evidence is under
`/Users/apple/study/Aletheia-Kien-API/Bang-chung/20261111-completion/`;
the 10 November results below remain historical receipts.

Local evidence, 2026-11-10. The implementation checkout is the existing
`aletheia-j01-api`, branch `feat/product-api`, HEAD `2bbcdc2`, with substantial
pre-existing uncommitted WIP. No Git index, commit, branch or worktree operation
was performed. Exact preimages were saved before changes. A concurrent K-02
`follow_up` dispatcher update was retained by a localized merge.

## Implemented before the shared-writer freeze

| Area | Result | Evidence / limit |
|---|---|---|
| ZIP format and bytes | ZIP stored/deflated only; bounded compressed/expanded bytes, entries and depth; actual CRC/length/hash verification | `source_bundle.py`; no extraction while inspecting |
| Paths and links | Traversal, absolute/drive/UNC/ADS, reserved Windows names, ambiguous case/NFC/NFD paths and parents, file/directory collision, symlinks/special types and legacy Unix link extra fields rejected | NFC and NFD are each accepted independently without renaming source entries; canonically equivalent collisions fail closed |
| Materialization | Exclusive writes into a unique private tree, then atomic rename; all materialized bytes checked against the manifest | Owned temporary tree is removed on write failure; brief Windows rename sharing errors receive bounded retry |
| Trust boundary | Existing P3 importer/parser reused; no imported code execution, provider call, fake Git history or raw archive `.git` acceptance | Imported Python/notebooks remain untrusted text/data; inherited content validators and redaction apply |
| Canonical identity | New trusted `grant_immutable_project_root` derives logical identity from exact source SHA while retaining current physical path, device/inode and reparse checks | Existing local `grant_project_root` identities and authorization are unchanged |
| Reopening snapshots | Hosted content projection has a documented fixed audit marker; actual capture time stays in the upload envelope | Same source and mapping across two roots/times yields the same snapshot ID and different record hashes; no timestamp is claimed as source history |
| Mapping | Existing domain validator handles arbitrary run counts; target/baseline/item/field/value/definition issues expose bounded fixed codes and original payload field positions | No alternative mapping parser or scientific semantics were introduced |
| Unicode metrics | Canonical NFC Unicode metric, run and field identifiers use the same frozen Unicode 14 policy in P3, P6 and browser validation; unsupported identifiers retain explicit exclusions | `độ_chính_xác` is supported without removing accents. Noncanonical/invisible identifiers fail closed; sensitive labels remain withheld. Archive filenames retain exact unambiguous NFC or NFD source bytes |
| Worker input | Trusted supervisor file IPC under private `inputs/`, regular single-link files, compressed-size bound and pre/post stat checks | Browser DTOs receive no server path. This avoids base64 IPC for large sources; durable PostgreSQL parts, not this worker buffer, establish resumability |

Worker ceilings are 200 MiB ZIP, 512 MiB expanded, 100,000 entries and depth 32.
The upload adapter explicitly aligns P3 item/total/count budgets; the inherited
line cap is 1 MiB. These settings alone do not establish a successful L journey.
Finite-number and decimal threshold comparison rules remain in place. The
completion pass uses the explicit V2 class/unit contract above; it does not
infer metadata or convert units, and does not rewrite frozen research artifacts.

## Tests

The consolidated local regression selected 14 files covering hosted archive,
worker, snapshot, local import boundary, P3 mapping/integrity and product
preview/candidates. It completed **179 passed, 1 skipped**. The earlier targeted
20-pass run and the new K-07 20-pass run are subsets and must not be added to
this total. The skipped platform case is not a pass.

The new tests cover five runs; zero, negative and `1e-300` observations;
Unicode filenames; explicit Unicode metric exclusions; different worker roots
and capture times; changed source bytes; field-addressed mapping failures;
concurrent materialization; synthetic disk-full cleanup; and private worker input
scope. Existing tests exercised a real subprocess worker. These are local
filesystem/process/unit tests, not PostgreSQL/browser/joint/staging acceptance.

Evidence is in the task-owned workspace directory
`k07-verification-20261110/archive-domain-regression.log`, with the captured
preimages and preparation scripts beside it.

## Prepared follow-ups after the shared-writer freeze

Existing shared files were frozen once the owner coordination requirement was
received. These follow-ups were prepared and tested through module overlays;
they were not silently applied over another owner's WIP.

1. `prepared-seal/source-seal.patch` supplies the preview's source-state seal to
   confirm. The worker recomputes the versioned source/policy projection and
   rejects a mismatch as `source_changed`, which the producer maps to the
   existing `PreviewChanged` boundary. Its regression fails against the prior
   checkout and passes against the prepared overlay. This seals parser/policy
   version drift even when exact source bytes are unchanged.
2. `prepared-cache/cache-cleanup.patch` layers over that patch. Each adapter
   import uses an owned temporary materialization and releases it after admitted
   artifacts are loaded, including parser/mapping errors. File-input workers own
   `materializations/<input-stem>` so the supervisor can identify an exact tree
   after reaping a killed child. Source inputs stay owned by the parent.
   Trusted Windows private paths use extended syntax to handle nested staging
   beyond the legacy path limit. The legacy cache-tampering test is adjusted for
   scoped lifetime; direct `_materialize` tamper rejection remains explicitly
   tested.
3. The combined seal/cache overlay completed **25 passed**: three new cache
   tests, one adjusted existing test, the 20 K-07 tests and the seal regression.
   This is overlapping follow-up evidence, not 25 additional accepted gates.
   Prepared implementation lint checks pass.

Both packages contain exact before/after files, SHA-256 manifests and unified
patches. They require owner integration and a rerun against the final shared
checkout before being counted as implementation acceptance. A hard process kill
skips Python `finally`; K-02 must reap the child and remove only the exact
request-owned scratch directory, using native Windows path handling.

## Cross-owner findings to retain

- The current supervisor cancellation regression demonstrates that its outer
  call can return before inner child cleanup/reaping finishes. This can race
  parent input deletion on Windows. The failing witness is retained in
  `prepared-seal/current-red.log`; it is not counted as passed. K-02 owns the fix.
- Legacy direct `PostgresSources.put` must share aggregate quota admission with
  resumable uploads or its route must be retired after consumer coordination.
  New-upload reservation quotas alone do not bound that legacy route.
- Completion joins durable chunks into a full source in the API process and
  inspects the archive in a thread. Concurrent completion memory needs a bounded
  admission decision; no stream-memory claim is made.
- L requires 100,000 metric rows independently of the 200 MiB byte axis. Embedded
  observations may exceed the existing 32 MiB worker output envelope; measure
  the full worker confirm, not only archive transfer or dataset metadata.
- Preview lease expiry/current role/source grant/publication fencing,
  PostgreSQL migrations, upload receipts and consumer context fencing belong to
  the integrated K-07/J owner checks. Backend tests do not close browser or joint
  acceptance, and upload success does not close K-08/K-11 analysis/history gates.

No public deployment, paid provider call, user-source deletion or acceptance CSV
completion was performed by this archive/mapping subtask.
