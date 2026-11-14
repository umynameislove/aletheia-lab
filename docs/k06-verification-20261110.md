# K-06 — bằng chứng và điều kiện còn mở, 10/11/2026

## Kiểm bổ sung macOS — 11/11/2026

### Kiên: phần bổ sung không dùng Docker/IdP

`test_k06_effect_races_postgres.py` đạt 15 ca: hai thứ tự export publication với
membership/project/source revoke hoặc HTTP delete; mất quyền sau verified object
read trước download; hai HTTP note cùng revision; và role downgrade trước/sau
note commit. Các ca dùng CSRF, session server, restricted API LOGIN và native PG,
kiểm export/reference/note/audit thực, không sửa canonical result cũ. Đây là
ASGI/DB proof; phần UI bốn binding vẫn do Quân nhận.

`test_k06_api_shutdown_postgres.py` đạt hai ca qua hai Uvicorn HTTPS process
thật. API A đang chờ khóa PostgreSQL thì nhận SIGTERM; kiểm cả client nhận ACK
và client disconnect. Job queued và một admission audit được kiểm đã commit
trước khi API B replay; B và API A khởi động lại trả cùng receipt. Publication
do parent dùng repository thật thực hiện, không giả là một worker process mới.
Lifespan đóng caller-owned pool, DB connections theo application_name về zero,
schema/role/temp/TLS key riêng được dọn. Đây là seeded-session composed API;
full ManagedRuntime startup có OIDC discovery vẫn thuộc đợt IdP đã hoãn.

Receipt mới: `Aletheia-Kien-API/Bang-chung/20261111-kien-remaining/` với
`k06-effects/result.json`, `k06-api-shutdown/result.json` và các log/cleanup.
Không suy DB replica failover, Windows/container, 50k/SLO hoặc restore/RPO/RTO
từ các bài local này. Production runtime giữ nguyên; chỉ bổ sung test.

### Các witness local trước đó

Đã chạy lại idempotency/CAS/audit trên PostgreSQL 16.15 riêng. Các race mới kiểm
20 writer cho project metadata và workspace settings, cùng hai publication
refresh tại một revision: một successor, không sửa result/snapshot cũ.

Đã kiểm native PostgreSQL graceful restart và crash với WAL replay thực; receipt
nguồn/job, terminal output và ba audit action giữ nguyên. API process-loss tại
committed ASGI ACK boundary cũng được kiểm lại. Các fake quan sát SQL cũ được
cập nhật theo current source scope; parser bounded admission được giữ nguyên.

Metadata, workspace settings và refresh/current-pointer đã có implementation;
không còn là các port core bị thiếu. Hai mươi writer PostgreSQL riêng cho mỗi
metadata/settings cho một winner, 19 revision conflict và một audit. Hai publisher
refresh trên hai backend PostgreSQL riêng cho một successor, một conflict và giữ
nguyên result/snapshot cha.

Các test mới bổ sung phạm vi local cụ thể:

- `test_k06_worker_process_recovery_postgres.py`: hai subprocess độc lập. Worker
  A thoát sau claim; test chờ lease hết bằng đồng hồ PostgreSQL thật. Worker B
  claim rồi dừng trước publication trong khi lease còn sống; replay lease cũ A
  bị từ chối và chưa có snapshot. B commit snapshot rồi thoát bằng `os._exit(74)`
  trước acknowledgement. Completion lặp, claim lại và enqueue cùng key không
  tạo effect mới; test kiểm terminal output, một snapshot, revision 1 và đúng
  một audit admission và một audit publication.
- Cùng module kiểm cooperative scheduler stop: một preview child thật đã tính
  xong, scheduler dừng tại checkpoint trước publication, nhận stop rồi hoàn tất
  job đang chạy. Job kế tiếp vẫn queued và một worker chạy sau đó hoàn tất nó;
  pool không bị giữ tại checkpoint. Đây không phải thử OS signal khi child còn
  đang tính toán hoặc managed service shutdown.
- `test_k06_refresh_tombstone_postgres.py`: refresh đã compute trước delete, kiểm
  cả hai thứ tự commit. Delete-first chặn publication và job thành failed, không
  thêm snapshot/audit publish. Refresh-first commit đúng một successor, delete
  dùng revision cũ conflict rồi delete revision hiện tại tạo tombstone. Cả hai
  nhánh giữ current pointer đúng, một deletion record, giữ hai source record
  và từ chối đọc result/job output sau tombstone.

Native POSIX SIGTERM được kiểm bổ sung bằng entry point `managed_worker.main`
thật, một LOGIN worker role riêng với audit bật và source 233.146 byte/10.000
metric rows. Test quan sát PID child cùng file metrics đã materialize khi job
còn running, gửi SIGTERM thật tới parent, nhận exit 0 và xác nhận child đã reap.
Job đang chạy thành succeeded; backlog vẫn queued. Một managed process mới xử
lý backlog rồi cũng dừng bằng SIGTERM với exit 0. Hai receipt replay đúng; DB
giữ một canonical snapshot, revision 1, hai admission và hai publication audit.
Schema, role, input/materialization và fixture riêng đều được dọn. Lượt này đạt
**1 passed, 11.30 s**, có `native-sigterm/final.log`, `result.json` và
`managed-sigterm-observation.json` trong evidence completion. Đây là worker
POSIX graceful drain cho confirm; không phải Windows/container signal, managed
API shutdown, replica/failover, browser hoặc nghiệm thu chung.

Review độc lập đã phát hiện và sửa test fixture để K05/K06 DSN trỏ cùng DB,
cùng Runner/SelectorEventLoop cho child psycopg trên Windows. Qualification
K06-only bắt đầu khi K05 env chưa đặt và đạt **4 passed, 5.42 s**, ghi riêng tại
`/Users/apple/study/Aletheia-Kien-API/Bang-chung/20261111-completion/k06-only-postgres.log`;
không suy ra đã chạy trên Windows chỉ từ portable test structure.

Các mục final-pending ngày 10/11 trong bảng log bên dưới là lịch sử. Nghiệm thu
managed API/service lifecycle, Windows/container signal, replica/failover, export/download, browser,
restore và deployment vẫn có gate riêng; các witness local trên không tự đóng
các gate đó. Lượt combined cuối được ghi riêng tại `combined-integration-final.log`
và `combined-integration-final-result.json` dưới evidence completion; chỉ dùng
kết quả đã hoàn tất, không cộng các lượt lặp. `combined-integration-final` có
một spy fixture lỗi thời và được giữ làm lịch sử failed; receipt hiện hành là
`combined-integration-current-result.json`, đạt 297 test sau khi sửa spy theo
parser dispatch thật. Witness POSIX SIGTERM bổ sung được ghi riêng, không cộng
như một lượt combined mới.

**Bản bàn giao tạm thời ngày 10/11, giữ làm lịch sử.** Implementation và các lượt kiểm local bên dưới có
bằng chứng riêng; lượt kiểm kết hợp cuối của chat chính chưa được chốt lúc viết.
Không đánh dấu K-06 hoặc journey phụ thuộc thành passed từ tài liệu này. Không
cộng các lượt test lặp/chồng nhau; failed, skipped, deselected hoặc interrupted
không thuộc passed. API process-loss local đã có witness `request-fence`; không
suy ra failover hoặc network/TLS acceptance. Owned PG16 graceful/SIGKILL/WAL
restart local có witness riêng trong `recovery/`, không restart DB chung.

Checkout ngày 10/11: `C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án/aletheia-j01-api`,
`feat/product-api`, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` cộng WIP.
Evidence root ngày 10/11: `C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k06-verification-20261110`.
Mỗi `source-before.json`/`source-after.json` ghi content SHA-256 được kiểm; HEAD
không đủ khi K-02/K-03/K-04/K-05/J-01 cùng làm việc. Manifest mới có thể làm bằng
chứng cũ thành historical; final run phải kiểm lại dependency thực bị thay đổi.

## Trạng thái năm bước theo tầng bằng chứng

| Bước | Thiết kế / code | Offline / double | PostgreSQL / concurrency thật | HTTP consumer boundary | Process/restart/fault | Nghiệm thu chung / phần mở |
|---|---|---|---|---|---|---|
| K-06.01 | Reuse source/job ledger; exact fingerprint, current auth, no automatic expiry; receipt lookup trước fresh-admission eligibility; persisted claim fence | Validation/receipt/retry tests có; final combined pending | 20 connections cùng/khác body, scope/revoke và claim source/mapping fence; P19 fingerprint ghim expected revision | 201/202 replay, 409; P19 original key/revision 2 vẫn trả receipt sau publish revision 3 | API child exit 73 tại committed ASGI 202 boundary; owned PG16 graceful/SIGKILL-WAL restart giữ receipt/output/audit | Blocked điều kiện toàn bước: final manifest, joint consumer và K-09/K-13/K-18 job/retention/recovery lifecycle |
| K-06.02 | Member/owner/source+project grant/review/delete/metadata/settings CAS trong short transaction; If-Match/ETag; canonical follow-up/refresh revision | K-04 caller tests; final combined có receipt riêng | 20 writers: một winner/19 conflicts cho review/member/project grant/metadata/settings; hai refresh publisher có một successor; immutable result/history | 428/412/200 + ETag; P19 fresh stale key 412, same key khác expected revision 409 | Chưa có plain-CAS unknown-commit replay receipt; consumer phải reconcile revision/history | Metadata/settings/refresh core ports đã có local witness; joint draft recovery và full managed lifecycle vẫn riêng |
| K-06.03 | Catalogue có route/invariant/schedule/owner/unlock; không dùng interface thay endpoint | Deterministic barrier/events | Admission/CAS/revoke; blocked workspace A không cản B; pool hết 2 slots bounded wait/reuse; cả hai refresh/delete commit order | Parsed receipt/output/history, persisted counts, P19 revoke/tombstone; native ANALYZE viewer grants và corruption fail closed | API/PG recovery, hai worker process, live stale-lease fence, after-commit loss, cooperative drain và actual native managed-worker SIGTERM có local scope | Managed API/service lifecycle, Windows/container signal, replica/failover, export/download và joint browser còn mở; xem CSV |
| K-06.04 | Mặc định 3 attempts trong một deadline 5s; chỉ 40001/40P01 retry toàn DB-only transaction | Deadline/backoff/cancellation/nontransient tests | Real deadlock/serialization/lock timeout/cancel/rollback; 2-slot exhausted pool; retry sau revoke bị từ chối | Safe 503; giữ nguyên revision/idempotency conflict; final combined pending | Boundary ACK error injection, actual API loss và owned PG restart là các witness tách biệt; chưa replica failover/host loss | Local policy/code đã kiểm trong phạm vi nêu; shared staging/load/full runtime budget/provider-wait/failover còn K-02/K-09/K-17/K-18 |
| K-06.05 | Immutable audit V1+V2: safe IDs, exact parent/subject, commit cùng effect; fixed safe failure outcome logs | Validation/schema/admin CLI tests | Rollback/cancel/retry/drift/immutability; mutation + audit; native review scope | P19 HTTP request ID = admission request/audit ID; optional managed runtime wiring chờ final owner manifest; cheap probe không thay full verify | API/owned-PG process recovery giữ đúng receipt và audit count; không claim replica/host-storage recovery | Blocked rollout/joint: final K-04/K-02 runtime/grants/readiness validation và consumer/operator acceptance |

`blocked` trong tracker là trạng thái điều kiện nhận đầy đủ còn mở, không phủ
nhận code hoặc local checks đã có. Không ghi passed toàn bước chỉ vì một nhóm
test local đạt; cũng không ghi N/A khi endpoint core chưa được xây.

## Nội dung implementation trong phạm vi này

- `hosted/transactions.py`: một callback DB-only được retry toàn transaction;
  bounded deadline/attempt/backoff, current auth/revision được đọc lại ở mỗi
  attempt, unknown commit không tự replay.
- `hosted/postgres_sources.py`, `postgres_jobs.py`: reuse unique ledgers hiện
  hữu; freeze request trước attempt; current parent policy trước replay;
  durable receipt/effect và optional audit cùng transaction. Retry admission
  lookup receipt trước fresh-admission state eligibility theo J-01.
- `hosted/review_repository.py`, `workspace_management.py`, `project_access.py`,
  `project_mutations.py`: short transaction retry; CAS giữ expected revision,
  append history/commit cùng optional audit. Parse/stage/object I/O ngoài DB tx.
- `hosted/action_audit.py`, `action_audit_schema.sql`,
  `action_audit_scope_schema.sql`, `action_audit_migration.py`: additive immutable
  safe V1+V2 metadata; exact review result parent và grant target IDs; separate checksum
  ledger, no token/body/request secret. `scripts/k06_transactions_admin.py` là
  explicit migrate/verify operator command, không migration lúc startup.
- HTTP ETag/If-Match và runtime/admin integration thuộc K-04/K-02 ownership.
  Common contract/fixtures thuộc J-01; canonical schema/object/catalog thuộc
  K-05. Không tạo ledger/revision system thứ hai hay sửa migration checksum cũ.

Xem [runbook](k06-transactions-runbook.md) cho bảng 12 action, retention/retry
policy, receipt lookup và trình tự audit rollout. Current source contract là
`product-api/v1-draft`: 409 idempotency conflict, 412 revision conflict, 428 missing
precondition. Original 05/11 synthetic 409 revision example là lịch sử.

## Các lượt bằng chứng đã ghi riêng, chưa cộng thành final total

Đường dẫn lịch sử trong bảng tương đối với evidence root ngày 10/11 ở đầu tài
liệu; receipt completion ngày 11/11 có đường dẫn tuyệt đối riêng.

| Lượt | Kết quả được ghi | Giới hạn/version |
|---|---|---|
| `idempotency/idempotency-final` | 6 passed, 0 skipped, 5.55s; exit0; full source/integration-fixture manifest stable | Repository-level localPG, 20actualbackend PIDs; no restart claim. Nếu source/jobs đổi sau manifest, phải kiểm lại phần ảnh hưởng |
| `idempotency/cas-http-final` | 4 passed, 27.87s; exit0 | Global703-file manifest chỉ đổi unselected/unimported runtime-privilege test; `dependency-stability.json` giữ mọi source/selected test/imported helper unchanged. Không gọi toàn tree frozen |
| `transactions` | Log29passed3.03s cho helper unit+5PG; final-unit24passed1.08s là lượt chồng khác; không cộng | Actual40P01/40001/lock/cancel; commitACK loss injected **sau real commit**, không DB restart |
| `transactions/cas-fixed-*` | 22passed26.64s; selected ownCASauditPG+K04reviewunit; scoped manifests stable | Included rollback on audit failure, server40001 abort→revoke recheck, retry→one review+audit. Không đồng nhất22test là22PG tests |
| `audit` | 34passed23.48s, exit0, scoped manifests stable | Audit unit+PG schema/drift/immutability, not managed runtime rollout |
| `request-fence` | 7passed18.89s, exit0,251files unchanged | Includes actualAPIchildexit73 at ASGI202 send after realcommit, separateprocess same-key replay onejob/oneaudit; mutated claim source/mapping denied; receipt-before-parentstate lookup. NotPGrestart/networkTLS/workerfailover; laterP19bridge/V2runtime finalrun stillpending |
| `recovery/verified` | 2passed11.14s, exit0,611files unchanged; ownedcleanuptrue | OwnPG16 graceful restart và SIGKILL137→start/WALredo preserve same source/job receipt, terminal output and3auditactions; fsync/synchronous_commit/full_page_writes on. Exactmanifest, no replica/host-loss/networkTLS claim |
| `recovery/stable-final` | 2passed6.94s, exit0; ownedcleanuptrue | Stricter test guard and stable synthetic timestamps. Globalmanifest changed only postgres_jobs.py during concurrentP19integration; source_stable=false retained. Prior failed log/readiness/timestamp fixture attempts retained and not counted |
| `follow-up-http-first` | 2 failed, 8.55s, not passed | Historical output500 exposed incomplete canonical result summary; source fix and full local HTTP rerun are recorded below. Keep the failed run; do not add its partial checks to passed count |
| `follow-up-http-json-fixed` | 3 passed, 18.29s, exit0; 274 files unchanged | Actual P19 HTTP/PG admission/publication/replay qua revision2→3; membership/tombstone deny replay. Native ANALYZE output là canonical ProductView cho owner + explicitly granted viewer; project/source revoke 404, corrupt owned object 500 an toàn, restore 200 |
| `pool-isolation` | 2 passed, 1.58s, exit0; 258 files unchanged | DB09 blocked A/committed B + actual PG lock witness; DB12 pool2 held → one 0.2s deadline before callback → release/reuse both slots. Không runtime load/fairness capacity claim |
| `native-review-current` | 19 passed, 7.34s, exit0; 266 files unchanged | Native uploaded-result review PG + existing K04 review unit; không đồng nhất19 tests đều PG |
| `/Users/apple/study/Aletheia-Kien-API/Bang-chung/20261111-completion/k06-only-postgres.log` | 4 passed, 5.42s; K05 env ban đầu không đặt | Hai test worker recovery/drain và hai refresh/delete commit-order cases; UUID-owned PostgreSQL schema và filesystem thật. Fixture ghim K05=K06; Windows Selector branch đã sửa nhưng chưa thực chạy Windows. Không OS-signal/replica/export/browser/restore claim |
| `/Users/apple/study/Aletheia-Kien-API/Bang-chung/20261111-completion/native-sigterm/final.log` | 1 passed, 11.30s | Managed worker entry point thật nhận native POSIX SIGTERM khi child đã materialize metrics và còn sống; exit0/drain/restart/backlog/receipt/canonical effect/audit/cleanup được kiểm. Không Windows/container/API-service/replica/browser claim; không cộng lặp với witness cũ |
| `admission-after-deps` / `admission-fault-fixed` | Lượt lịch sử có failed caller expectations/process-fault work | Giữ log nguyên; không cộng passed count. Lượt correctedprocess witness ở request-fence, final combined vẫn pending |
| `idempotency/interrupted-result.json` | Interrupted trước pytest output trong default sandbox | Không là test pass. Approved localPG invocation sau đó có log riêng |

Ruff của hai integration files idempotency/CAS-HTTP đạt (`idempotency/ruff.log`,
`idempotency/cas-ruff.log`). Transaction/audit agents có Ruff/mypy logs riêng;
final lint/typecheck/test tổng và final file manifest của chat chính vẫn pending.

## Race và DB/fault coverage

`k06-race-cases.csv` giữ scenario, persisted invariant, đúng tầng bằng chứng,
owner và unlock cho mọi ca bắt buộc. Các điểm phải giữ khi bàn giao:

- DB08: đã có 20 writer thật cho review/member/project grant/metadata/settings,
  không chỉ nhiều asyncio task trên một connection. Refresh có test riêng với
  hai backend PostgreSQL thật, một successor và một conflict.
- DB09/12: controlled independent-tenant progress và actual two-slot pool exhaustion
  có witness local; chưa chứng minh multi-replica runtime admission/load capacity.
  DB10/11 actual lock timeout/deadlock và safe pool reuse có witness.
- DB14/15/16: durable receipt/effect count và serializer chính; lostresponse bằng
  repository reconstruction không là restart. Boundary ACK error injection sau
  actual commit không tự chứng minh API crash. request-fence bổ sung actual API
  process loss tại ASGI202 boundary. OwnedPG16 recovery bổ sung actualgraceful và
  SIGKILL/WALrestart với committedreceipt/audit; vẫn không replicaPGfailover hoặc
  host/storage-loss durability.
- DB17/21–27: publication/current-pointer, hai process takeover, stale lease khi
  owner mới còn sống, after-commit worker loss và cả hai refresh/delete commit
  order đã có controlled local scope. Managed attempt/deadline/heartbeat/retry
  lifecycle và export/download còn cần witness riêng. Không thêm competing
  race-prevention framework.
- DB09/33/40 và F08/09/10/23: independent tenant progress, failover, full pool,
  provider wait và restart/drain chưa được nhận hoàn tất bằng local tests khác.
- Native worker SIGTERM bổ sung graceful drain trong khi child thật đang làm
  việc, rồi restart xử lý backlog. Chỉ thao tác confirm và POSIX local được
  kiểm; không thay Windows/container hoặc managed API/service qualification.
- J04/J05 browser lostACK/cancel/revoke/delete/draft recovery, shared staging và
  consumer joint acceptance chưa có joint_run_id. Local process/restart witnesses
  không thay Windows/container/API-service shutdown, replica/failover hoặc restore acceptance.

## Người nhận và bước mở khóa

| Đầu nhận | Phần còn mở | Điều kiện mở khóa |
|---|---|---|
| K-02 | Metadata/settings/refresh và native worker SIGTERM đã có local witness; full managed runtime composition còn phải nhận riêng | Same producer schema; final runtime/worker/readiness manifest, managed API/service và Windows/container lifecycle, consumer coverage |
| K-04 | Policy/HTTP ETags, optional audit wiring/grants and request correlation | Freeze owner files, approved patch by preimage; `audit=True` migration+privilege+runtime readiness test, retain auth lock order |
| K-05 | Canonical publication/shared schema handoff | Same migrated authority; V5 native upload và V6 follow-up extensions append-only, không sửa checksum cũ; final caller/worker/runtime content validation |
| K-08/K-09 | Refresh/current pointer và local multi-connection/process publication đã kiểm; full managed attempts/deadline/lease-renewal/retry lifecycle còn riêng | Final managed runtime schedules; giữ local live stale-lease, after-commit loss, terminal và cooperative drain receipts |
| K-13 | Tombstone/delete/purge/ledger retention and restore policy | Race/delete policy across jobs/exports; no resurrected data; explicit retained/purged receipt and recovery window |
| K-17/K-18 | Exhaustion/soak/failover/restore/deployment durability | Owned staging/resources; bounded load/fault witness and canonical backup/DB/object/permissions consistency |
| J-01/Quân, J04/J05 | Consumer schema/version, lostACK/draft recovery UI and joint acceptance | Same final producer fixtures/HTTP version; browser/staging journey with persistedIDs/hashes and both sides' acknowledgement |

No staging/commit/push/merge trong phần công việc này. Git read ở sandbox mặc
định có lúc bị từ chối cho checkout ngoài workspace; final Git status phải lấy
từ approved read của chat chính, không suy ra từ việc test pass.

## Tracker

Chưa áp tracker ở bản này. Updater dự thảo và raw-byte preimages ở evidence
`trackers/`; chỉ cập nhật đúng parent K-06 và năm child K-06.01–.05 sau khi final
results chốt. Giữ tất cả field/row task khác byte-for-byte, backup trước apply,
reject nếu preimage SHA thay đổi. Các core/joint dependencies còn mở giữ blocked;
observed/evidence phân biệt local code/PG/HTTP/API/worker process-loss, lease
takeover, cooperative drain và native POSIX managed-worker SIGTERM với
Windows/container/API-service lifecycle, replica/failover/restore/staging chưa
được nhận.
