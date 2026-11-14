# K-06 — transactions, revision, idempotency và điều tra lost response

## Current API-copy checkpoint — macOS, 11/11/2026

Source hiện hành ở `/Users/apple/study/aletheia-lab-feat-product-api`; checkpoint
Windows bên dưới giữ làm lịch sử. Metadata/settings/refresh ports đã được nối.
Native PostgreSQL đã kiểm hai worker process, live stale-lease takeover,
commit-before-ACK loss/replay và hai thứ tự refresh/delete. Managed worker POSIX
SIGTERM thật trong lúc child đang compute cũng đã đạt: graceful drain, child
reap, backlog còn queued rồi process mới xử lý; không suy từ cooperative stop.
Chi tiết và giới hạn ở [bằng chứng hiện hành](k06-verification-20261110.md),
evidence `Aletheia-Kien-API/Bang-chung/20261111-completion/native-sigterm/` và
`combined-integration-current-result.json`.

Hai composed API process HTTPS còn có witness SIGTERM khi admission chờ DB
lock, mất ACK/disconnect, replay qua API khác và restart; queued job/audit có
trước replay, một canonical effect, caller-owned pool đóng trong lifespan.
Đây chưa phải full ManagedRuntime startup/shutdown với OIDC. Test cleanup còn
kiểm riêng TLS private key được xóa khi startup hoặc DROP ROLE lỗi.

Lượt macOS không dùng Docker. Windows/container receiving giữ scope riêng;
full ManagedRuntime/IdP, replica/failover và joint browser/release gates không
được đánh dấu passed bằng các witness POSIX/PG local trên.

## Historical Windows checkpoint — 10/11/2026

Ngày 10/11/2026, Asia/Saigon. Tài liệu cho WIP trong checkout
`C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án/aletheia-j01-api`, branch
`feat/product-api`, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b`.
HEAD không nhận diện đủ WIP; dùng manifest đi kèm từng lượt kiểm trong
`C:/Users/Trung Kiên/Documents/ChatGPT/Đồ Án 3/k06-verification-20261110`.

Trạng thái: implementation và bằng chứng local đã có; lượt kiểm kết hợp cuối,
runtime rollout và final combined đang hoàn thiện; API process-loss và PG16
graceful/SIGKILL crash restart trên retained storage local có witness riêng;
chưa là replica failover/host-storage loss hoặc nghiệm thu chung. Xem
[bằng chứng và phần còn mở](k06-verification-20261110.md) và
[danh mục race](k06-race-cases.csv). Không chạy migration hay cấp quyền lên dữ
liệu đang sử dụng chỉ vì tài liệu có ví dụ lệnh.

## Một ledger hiện hữu, hai loại retry

Source upload dùng ngay `j01_sources`, unique
`(workspace_id, creator_id, request_key_hash)`: operation ngầm định là upload
source. Hash nội dung SHA-256 của **đúng ZIP bytes** là giá trị đối chiếu, không
nằm trong unique key. Cùng nội dung giải nén nhưng ZIP bytes khác vẫn là request
khác. Không chuẩn hóa tên/Unicode, rezip hay sửa numeric payload để ép replay.

Job dùng ngay `j01_jobs`, unique
`(workspace_id, creator_id, operation, key_hash)`, với `operation` hiện có là
`preview`, `confirm`, `analyze`, và canonical `follow_up` P19 đang tích hợp. Actor lấy từ current server session, không lấy
từ body. Request hash nằm ngoài unique key. Fingerprint là JSON đã kiểm request
shape, sort object keys, compact separators, `ensure_ascii=True`, `allow_nan=False`.
Array order, ID/Unicode code points, integer/float/boolean khác nhau được giữ
phân biệt. Request được sao chụp từ canonical JSON trước retry transaction để
caller không sửa dict đang chờ. Retry liên quan còn ghim `retry_of_job_id` trong
fingerprint; không được thay parent bằng cùng key.

Follow-up fingerprint còn ghim canonical project/result parent, selection kind/ID,
question và expected revision. Replay key đã commit được lookup trước current
revision CAS, nên original key/revision vẫn tìm original receipt sau publication
đã tăng project revision; fresh key dùng revision cũ phải 412. Actual P19 HTTP/PG
admission→typed canonical publication→parsed output/replay đã kiểm local;
không công bố capability từ adapter/interface riêng hoặc nhận joint UI từ test này.

Retry do mất mạng dùng **cùng key và cùng request**. Server reauthorize current
session/member/source trước khi trả receipt. Một row được commit bền trước ACK;
replay không append audit/effect. Receipt job là ảnh `queued` ban đầu với cùng
`job_id`, `request_id`, `workspace_id`, `status_url`, kể cả khi job đã hoàn tất.
GET status để biết trạng thái mới. Cùng key khác request trả
`409 idempotency_conflict`; stale If-Match là `412 revision_conflict`, thiếu
precondition là `428 precondition_required`, không gộp thành một 409 chung.

Retry business action qua job retry endpoint là operation mới liên kết job cũ.
Chỉ **admission mới** yêu cầu parent `failed` hoặc `cancelled` và một key mới;
quyền/parent được kiểm lại. Theo semantics J-01 hiện hành, lookup receipt cũ diễn
ra trước eligibility của admission mới: replay key đã nhận vẫn trả receipt đó
khi state hiện tại của parent đã đổi; fresh key với parent không eligible trả
`409 job_state_conflict`. Key đã dùng cho một request khác luôn conflict.

## Retention và capability

Implementation hiện tại không có TTL/expiry hoặc GC tự động cho source/job
idempotency rows. Policy local là giữ ledger cùng durable source/job record,
chưa cho phép tái dùng key theo tuổi; bảo đảm replay chỉ còn khi row được giữ.
Không có policy 24 giờ đã được phê duyệt. Operator không xóa ledger để thử lại
mutation; K-09/K-13/K-18 phải chốt recovery/retention/backup trước rollout.

Không có GC cũng có nghĩa catalog/ledger/audit tăng theo accepted operations.
Cần quan sát row count, table/index/WAL bytes và tuổi ledger; workload/soak,
quota, archive và retention capacity chưa được nghiệm thu. Không xóa key sớm hơn
receipt/job recovery window khi bổ sung GC. Audit metadata không thay receipt
ledger. Không hứa exactly-once delivery toàn hệ thống; phạm vi đang kiểm là một
durable admission/publication theo ledger, CAS và lease fence của các port có thật.

`jobs.idempotency` chỉ được công bố khi runtime dùng backend PG có bảo đảm đã
kiểm và readiness tương ứng. Annotation/member/grant/delete CAS endpoints không
công bố hỗ trợ Idempotency-Key: gửi header này không tạo replay guarantee.

## Mutation → revision → transaction/effect → audit

Audit trong bảng dưới chỉ được ghi khi repository bật `audit=True` và extension
đã được operator cài/verify. Mỗi action row rollback cùng business transaction.

| Action | Revision / replay | Ranh giới durable effect | Audit correlation |
|---|---|---|---|
| `source.put` | Actor/workspace/upload/key; exact-byte SHA | Reauthorize, insert/reconcile source receipt và audit cùng commit; inspect ZIP ngoài connection | source ID là operation/effect; request context nếu có |
| `job.admit` | Actor/workspace/operation/key; hash request + retry parent | Current source policy, durable request/job/queued receipt và audit cùng commit | operation/job/effect ID = job ID; durable request ID |
| `job.cancel` | Current creator/manager; state CAS | Chỉ queued/running → cancelled; terminal truth đã commit được giữ | actor thực hiện cancel; operation/job ID và audit cùng update |
| `job.complete` | Current authority + running/lease digest/expiry fence | Một conditional terminal update + result + audit | job ID, durable admission request ID |
| `job.fail` | Current lease; failure sau revoke không publish dữ liệu | Một conditional failed update; không gắn success result | job ID, durable request ID |
| `job.publish` | Current source/project authority + persisted request/lease/CAS fence | Immutable native upload snapshot hoặc canonical result, pointer/projection và succeeded/result reference cùng commit | effect snapshot/result ID, canonical project parent, operation/job ID |
| `member.update` | Expected membership revision, account/workspace/member locks | Role/active/revision/onboarding và audit cùng commit | actor/workspace/target member/revision |
| `owner.transfer` | Expected target member revision; last-owner guard | Owner changes/onboarding và audit cùng transaction | target member và resulting revision |
| `source_grant.set` | Expected grant revision; absent = 0 | Current manager, source/target scope, grant+revision và audit | effect/parent_id = source ID, subject_id = exact target user, revision |
| `project_grant.set` | Expected grant revision; absent = 0 | Current manager, canonical project/tombstone và target member, grant+audit | effect/parent_id = project ID, subject_id = exact target user, revision |
| `review.put` | Expected annotation revision; author-owned append | Read/verify canonical result ngoài final transaction; reauthorize, append immutable revision+audit | effect = annotation ID, parent_id = exact result ID, immutable revision, actor/workspace |
| `project.delete` | Expected canonical project revision | Stage canonical tombstone ngoài transaction; reauthorize, project UPDATE lock/CAS, ready refs/tombstone/parents/revision/audit cùng commit | tombstone effect ID và resulting project revision |

Direct trusted `snapshot.publish` cũng audit khi insert thật; replay cùng
snapshot không append audit. Workspace creation và invitation create/accept/revoke
có metadata audit bổ sung trong port K-04; chúng không tạo ledger mới. Lịch sử
snapshot/result/annotation không được UPDATE để thực hiện undo. Tombstone là
logical deletion, giữ canonical bytes/history; không phải purge hay xóa source gốc.

Claim/request của worker còn được đối chiếu với persisted job request trước
completion/publication. Sửa source ID hoặc confirm mapping trong captured claim
không được publish dưới receipt ban đầu; lease hợp lệ không thay request fence.

Project display metadata, workspace settings, refresh/current-pointer publication
và export job journeys chưa có đủ mutation endpoint tích hợp trong phạm vi này.
Follow-up P19 đã có route K-02 và K-05 V5/V6 canonical DB publication/parent
bridge. `follow-up-http-json-fixed` có3passed18.29s cho actual HTTP/PG follow-up
receipt/CAS/publication/revoke và native ANALYZE output/viewer grants/corruption.
P19 original parent/history immutable, một effect/audit và original receipt vẫn
replay sau revision2→3. Chưa nhận full worker-process/joint browser acceptance.
K-02/K-08/K-09/K-13
phải hoàn thiện port/route và lifecycle thật rồi áp cùng
CAS/idempotency/fence; đây là core dependency còn mở, không ghi N/A.

## Retry transaction có giới hạn

`hosted.transactions.run_transaction` chỉ bọc callback DB ngắn, toàn transaction.
Mặc định tối đa 3 attempts trong **một deadline tổng 5 giây**, không reset deadline
qua attempt. Caller giữ expected revision ban đầu; mỗi attempt re-read quyền và
current revision, stale revision trả conflict, không tự đổi expected revision.

- PostgreSQL `40001 SerializationFailure` và `40P01 DeadlockDetected`: rollback
  hoàn toàn, trả connection rồi mới retry; exponential backoff có jitter 0.5–1.5,
  nền 25ms tăng theo attempt, trần nền 200ms; cùng deadline tổng.
- `55P03 LockNotAvailable` (gồm lock timeout) và `57014 QueryCanceled`: không
  tự lặp transaction; trả `TransactionUnavailable`, map HTTP 503 an toàn.
- Operational/Interface error trước commit: unavailable; tại commit boundary:
  `TransactionOutcomeUnknown`, không tự replay. Timeout/cancellation tại commit
  được phân loại unknown; external cancellation truyền lên và không mở attempt mới.
- Validation/permission/revision/idempotency rejection không trở thành transient
  retry. Connection context sở hữu commit/rollback; autocommit bị từ chối.

Không gọi IdP/provider, parse/archive/hash file lớn, đọc object store, render PDF
hay chờ người dùng trong callback DB. Không coi external side effect đã rollback
cùng PostgreSQL. API outer deadline/cancellation vẫn thắng helper. Lock timeout,
statement timeout và pool budget deployment phải được K-02/operator cấu hình;
helper không tạo global lock hoặc tăng connection pool.

## Lost ACK hoặc outcome chưa rõ

1. Giữ original actor/workspace/request và Idempotency-Key, không phát sinh key
   mới để chữa lỗi mạng. Nếu credential rotate, lấy current session hợp lệ của
   đúng account/context theo auth policy; server vẫn kiểm quyền hiện hành.
2. Nếu đã có job ID/receipt: GET
   `/v1/workspaces/{workspace_id}/jobs/{job_id}`, và khi succeeded GET
   `/v1/workspaces/{workspace_id}/jobs/{job_id}/output`. Mọi lookup reauthorize
   current parent; revoked/foreign tenant không được đọc receipt/output.
3. Nếu ACK mất trước khi client biết job ID: submit lại đúng endpoint với cùng
   key và request. Receipt lookup/reconcile trả original receipt; đọc status URL
   để phân biệt queued/running/terminal. Source upload tương tự với exact bytes.
4. Với plain CAS mutation không có durable idempotency receipt: đọc current
   revision và history trong scope hiện hành, đối chiếu outcome; không blind
   auto-retry bằng expected revision mới. Consumer giữ draft nếu stale. Nếu chưa
   xác định được effect, báo unknown và điều tra; không tự nhận failed/succeeded.
5. Operator được phép đọc metadata audit theo workspace/actor/job/operation/
   request ID. Không thêm public audit lookup route hoặc đưa SQL admin credential
   cho browser. Audit row tồn tại sau commit là witness; absence sau mất kết nối
   chưa tự chứng minh rollback trước khi DB connection được reconcile.

## Cài extension audit và bật runtime theo owner

Audit SQL là extension riêng có V1 `action_audit_schema.sql` và additive V2
`action_audit_scope_schema.sql`, cùng `k06_action_audit_migrations`; không đổi
checksum K-04/K-05 hoặc frozen V1. V1 SHA256
`3f89544e36d382c4e50333fb68b9741c83dae0c8e6198ee5deb21ec1e25d72cd`;
V2 SHA256 `a391dd7e8473fda2be2c34f8ef5417d9ad6521f925be5d587ceaa7f06d886075`.
V2 thêm nullable bounded `parent_id`, `subject_id` để tra đúng result/target,
không ghi raw payload. Verified V1 upgrade append-only; fresh/reapply/drift
verify giữ signature V1 projection và verify V2 đầy đủ. Operator dùng
credential có quyền schema owner/migration; CLI không startup-DDL và không log DSN:

```powershell
python scripts/k06_transactions_admin.py migrate --schema <owned_application_schema> --database-url-env ALETHEIA_HOSTED_DATABASE_URL
python scripts/k06_transactions_admin.py verify --schema <owned_application_schema> --database-url-env ALETHEIA_HOSTED_DATABASE_URL
```

Hai lệnh kiểm schema/checksum/signature; success JSON chỉ được in sau transaction
commit. Existing baseline/membership schema phải được migrate/provision trước;
CLI không tự tạo user/workspace/role, không ghi đè drift/collision.

Runtime rollout cần K-04/K-02 hoàn thành integration: explicit operator
`grant_runtime(..., profile="api"|"worker", audit=True)` sau migrate/verify, chỉ
SELECT/INSERT audit và SELECT migration ledger; không UPDATE/DELETE/TRUNCATE/DDL
hoặc ghi migration ledger. Sau đó bật
`ALETHEIA_HOSTED_ACTION_AUDIT=enabled` cho API và worker; default disabled giữ
compatibility. Readiness phải fail closed khi enabled nhưng thiếu schema/grants.
Library `action_audit.is_ready` chỉ SELECT V2 columns/ledger LIMIT0 và kiểm
11 INSERT-column privileges; đây là cheap runtime probe, không thay full
checksum/catalog signature verification lúc startup/operator migrate.
**Lúc viết bản này, patch grants/runtime đang chờ owner xác nhận**: kiểm source,
test và final manifest trước dùng các bước rollout này; library audit=True test
không chứng minh managed runtime đã bật.

Committed audit lưu safe IDs, action, revision và DB timestamp; unique scope chống
append trùng cùng operation/effect. UPDATE/DELETE/TRUNCATE bị trigger từ chối.
`aletheia.action` chỉ log fixed `rejected`, `rolled_back`, `outcome_unknown` cùng
request correlation; log không chứng minh successful commit. Không lưu token,
cookie, DSN, raw source/question/body, private note hay stack trace trong audit.
Audit/request ID tự sinh ở repository khi không có HTTP context không thay
client receipt; dùng durable job request ID để tra lost response.

## Kiểm và giới hạn bàn giao

Test dùng synthetic data, UUID-owned disposable schema, barrier/events và nhiều
PG connections. `request-fence` ghi7passed18.89s với source/job/admission tests,
bao gồm child API `os._exit(73)` đúng ASGI202 send boundary sau real commit;
process khác replay cùng key tìm một job/admission audit. Đây là actual API
process-loss local, không PG restart, TCP response-loss, TLS hay fullworkerdrain.
`recovery/verified` còn có2passed11.14s trong owned PG16 container: graceful
restart và SIGKILL137→start với interrupted/automatic recovery/WAL-redo markers,
postmaster start time mới; fsync/synchronous_commit/full_page_writes on. Fresh
connections giữ original source/job receipt, succeeded output và3auditactions.
Container/anonymous volume được verify ownership rồi remove; không restart DB
chung. `recovery/stable-final` kiểm lại guard/fixture mới2passed6.94s nhưng source
jobs đổi giữa lượt và được ghi source_stable=false; không gọi tree frozen.
PostgreSQL fault thực, boundary error injection và unit double là
các tầng khác nhau. HTTPX ASGI không thay browser/HTTPS/IdP/staging joint run.

Chưa có quyền deploy/cấp dịch vụ trả phí; không stage, commit, push, merge, reset,
clean, clone, chuyển nhánh hay di chuyển WIP. Chỉ cập nhật đúng K-06 tracker rows
sau final result, có raw-byte backup và preimage guard; giữ nguyên task khác.
