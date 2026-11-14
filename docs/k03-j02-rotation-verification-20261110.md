# K-03/J-02 — worker qua normal rotation: đã sửa và kiểm

2026-11-10. Kiên đã duyệt policy và mở managed_worker.py. K-02/K-04 xác nhận
một writer cho mỗi vùng. **Lỗi normal renewal làm job thất bại đã được sửa.**
Đây là backend/caller local evidence; chưa đóng browser/IdP recovery/J-02 chung.

## Hành vi và ranh giới

Job/JobClaim giữ digest ban đầu để kiểm job/workspace/operation/lease.
Adapter worker chỉ dùng nó làm anchor tới đúng user/context gốc. Mỗi lần read,
dispatch/publication đọc anchor, khóa account FOR SHARE, re-read current
credential cùng context/generation rồi delegate repository/RBAC/source grant
canonical trong cùng connection/transaction. Auth writers dùng account UPDATE.

API tiếp tục dùng PostgresSources strict và từ chối cookie rotated. Worker không
tìm session mới theo user_id; fresh login cùng user không cứu job context cũ.
Read worker được kiểm như writer action ở workspace và source grant qua delegate
PostgresAuthorization; không nhân bản CRUD, SQL grant policy, schema hoặc DTO.
Worker không lấy global advisory lock. Mỗi boundary kiểm lại, không cache quyền
qua computation. fail() chỉ ghi failed/null theo lease, không publish data.

| Vùng | Writer đã thống nhất |
| --- | --- |
| managed_worker.py import; hosted_auth/worker_sessions.py; test_k03_*; docs/k03-* | K-03 |
| authorization/workspace addon; postgres_sources/jobs/workspaces; managed_runtime/upload_runtime/source_http/job_http lượt này | K-04 |
| composition/request_boundary/edge/runtime/server/settings và test/docs K-02 | K-02 |
| Schema/storage K-05 khác | K-05; K-03 chỉ cung cấp interface/yêu cầu và kiểm caller |

K-02/K-04 xác nhận không ghi managed_worker.py/hosted_auth. K-03 không sửa source
RBAC/domain/migration của K-04/K-05. Shared managed_worker chỉ đổi một import;
constructor/pool/lifecycle/scheduler giữ nguyên.

## Kết quả thực tế

| Kiểm | Kết quả |
| --- | --- |
| Unit J01/K02/K03/K04 | **1214 passed in 49.55s**, exit 0, 73 file, không skip |
| K04 authorization/workspace PostgreSQL caller | **16 passed in 9.87s**, exit 0 |
| Focus worker/J02 guards/K02 managed-worker caller | 60 passed in 3.61s |
| Ruff + C901 | Đạt 19 source auth/worker và tất cả test K-03 |
| Strict mypy | Đạt 19 source, exit 0 |
| Main HTTP/DB/compute-child CLI | PASS; exact UUID schema dọn PASS |
| Negative worker DB CLI | PASS; exact UUID schema dọn PASS |

Các phạm vi giao nhau, không cộng các số. Có test/WIP của chat khác; không quy
toàn bộ số lượng cho bản sửa này.

### Main J02 HTTP/DB

Harness dùng compose_upload_runtime/lifespan, PostgreSQL/session/CSRF/admission,
canonical K04 RBAC và migration trong namespace riêng. Có hai account/workspace
hoạt động và administrative owner fixture bổ sung để giữ last-owner invariant
khi A thử đủ năm role; không coi seeding này là normal onboarding.

Đã đạt:
- Hai rotation trước claim và thêm rotation sau claim: job succeeded, captured
  digest giữ nguyên; API/strict repository từ chối digest cũ.
- Confirm snapshot qua rotation: wrong lease không thêm snapshot; đúng lease
  commit snapshot và terminal job.
- Actual compute subprocess WindowsUploadWorker/UploadWorkerSupervisor: rotation
  sau child compute nhưng trước post-read/publication vẫn succeeded; output
  SHA-256 khớp ZIP thật.
- Giữ account A UPDATE, xác nhận worker A thực sự chờ qua pg_blocking_pids;
  worker B hoàn tất trước khi nhả A, chứng minh đường đã kiểm không global serialize.
- Revoke membership chặn publication/status/output/capabilities, không ghi result;
  B vẫn hợp lệ. Lượt local cuối **0,062 giây**.
- Hai session store concurrent renew/logout-all không resurrect credential.
- App reconstruction giữ pending callback/return_to=/account, replay bị từ chối;
  chưa phải rolling deploy/process restart.

IdP synthetic trong harness này. Các seed/status output có dữ liệu tổng hợp;
ca compute-child mới dùng engine thật. Không có browser/IdP thật ở lượt DB.

### Negative và lease

Mười trạng thái được kiểm trước claim và trước generic/snapshot publication:
disabled, unverified, epoch mismatch, idle/absolute expiry, revoke/logout-all,
same-user new context, membership downgrade và source write grant removal.
Worker read cũng deny, result/snapshot không được ghi.
Wrong/expired/reclaimed lease không publish. Đây là committed-state boundaries;
chưa nhận đã kiểm lease expiry giữa snapshot INSERT và terminal UPDATE.

### K04 caller mới

16 integration tests K04 đạt cho normal-user onboarding, owner transfer/last-owner
race, invitation binding/expiry/revoke/replay/current issuer, explicit operator
provision không email-link, scoped grants, pooled connection sau rollback/
cancellation và source-parent publication/reads. Không còn coi các backend API
đó là chưa triển khai. Browser/IdP-provisioning journey và J01/Quân acceptance
cần kiểm trên cùng build/môi trường.

## Phiên bản WIP đã kiểm

Branch feat/product-api, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` cộng WIP.
Manifest642 source/test hashes đầu lượt:
`435358a125408a2cea47262c9fffe09fb545e06071b73f6218b1d1ea820b23bf`.
Bốn file K05 legacy_import.py/persistence_migration.py/postgres_catalog.py/
postgres_objects.py đổi song song. Dependency search cho thấy chúng thuộc
caller/test K05 riêng; không nhận đã kiểm bởi các kết quả J01/K02/K03/K04 trên.

Scoped manifest loại đúng bốn file đó có **638 file**, đã đối chiếu sau test,
các hash đều khớp: `2b48158e688339beeb0a1b33f6c7c883ffe00c776459587de78a92ae09c7403f`.
Metadata: `C:/Users/TRUNGK~1/AppData/Local/Temp/aletheia-k03-worker-verification-hnicv2oh/tested-scope-manifest.json`.
Chỉ lưu path/hash, không sao chép WIP hoặc credential.


| File trọng yếu | SHA-256 |
| --- | --- |
| `src/aletheia_lab/hosted_auth/worker_sessions.py` | `91566a7b4db859ae60b1a2161b2718ed9b126fd27aedc5fa1e65fd04bcae9058` |
| `src/aletheia_lab/hosted/managed_worker.py` | `4c75819cf8818bad96f480a9e7f067b642d763971fca59964464253c2f3f21b5` |
| `src/aletheia_lab/hosted/authorization.py` | `fbcf2d6b62bd161518d799cd9037220d62efbb1e1f75adb66f31f28c4cb4e061` |
| `src/aletheia_lab/hosted/postgres_sources.py` | `bf99b72b36deb85e2061b9a48eb99256869b218383a71ececf63bd584a5f9bd4` |
| `src/aletheia_lab/hosted/postgres_jobs.py` | `a509e77ff88513ef45ba6253161b16223ab298c20f13c84b6fb415bf504095eb` |
| `src/aletheia_lab/hosted/workspace_schema.sql` | `753801b917c512b9d1bee678d197b306434091edfdc3f999e0511b97af5221b1` |
| `tests/unit/test_k03_worker_sessions.py` | `0d0174486db45e669c2a3f5123fd12eca0a870ec76c47442e35eceaf7afeed2f` |
| `tests/unit/test_k03_j02_backend.py` | `4a965420b433a2d6548ba2e9789df90d2349ca870bbab15cff72828c3c8b39b7` |
| `tests/unit/test_k03_worker_postgres.py` | `b85de4627a4e36d527ae99986e9902fd1d7be0f77b43953f447fc60f364edf4c` |

## WIP và giới hạn

Không stage/commit/push/merge, clone/worktree mới hoặc deploy. Chỉ UUID schemas/
TEMP riêng; credential DPAPI qua env, không in và không đưa vào compute child.

Script update test K03 từng lỗi newline khi mở file, làm rỗng harness do K03 tự
tạo. Đã khôi phục chính xác hash211dce4a… từ lịch sử nội dung do cùng chat tạo,
không dùng Git/worktree khác; sau đó compile temp, kiểm hash và atomic replace.
Các lượt kiểm cuối ở trên chạy sau khôi phục/cập nhật.

Captured credential rows phải tồn tại tới khi dependent jobs kết thúc. Cleanup
K05 phải giữ anchor hoặc migrate sang durable context anchor; đây là yêu cầu
interface, không thêm repository/contract source khác.

Còn mở cho K03/J02 chung: hosted /v1 browser của Quân; real A→B/tab/BFCache/cache/
draft/download/event; IdP recovery/MFA policy/trusted epoch bridge; staging/TLS/
rollout/restore và joint acceptance. Keycloak local không chạy trong lượt này;
không đổi evidence IdP lịch sử thành kết quả mới.


## Caller checks do owner K02/K04 thực hiện sau bàn giao

K02 xác nhận 75 focused tests đạt10,59s và Ruff+C901 đạt trên đúng adapter91566a7b…
và managed_worker4c75819c…. Owner K02 chỉ cập nhật integration fixture/test của họ:
HTTP+CSRF renewal hai lần sau enqueue/trước worker; worker process mới vẫn hoàn
thành theo context gốc, API trả401 cho các cookie cũ và cookie hiện tại đọc output
bền. Manifest651file start/end khớp theo báo cáo owner. Root đã kiểm hash manifest:
005cd8bd920bb4a98067068be45d9d862f2366018067527764ba4289436a30c2.
Artifact: Đồ Án3/k02-verification-20261110/worker-caller-manifest-73833343ecc54883b379ecf2d82863e1.json.
Log worker-callers-rotation-final.log đã đọc để đối chiếu; không cộng75 với1214.

K04 xác nhận hai caller integration API-worker PostgreSQL +HTTPS local đạt11,67s
trên cùng hai sourcehash, 648source/test start/end khớp theo snapshot riêng.
Log Đồ Án3/k04-verification-20261110/worker-https-caller-final.log đã đọc.
Root kiểm hash k04-verified-files.json:
9edf94403a31861e515296360951a3033d1dae546a6ec5b9a6ff11a23b97b956.
Những lượt này củng cố caller/source đã bàn giao; không phải browser/realIdP/
staging hoặc joint acceptance. K02/K04 giữ writer cho file của họ; K03 không
sửa/khôi phục các integration changes của owner.
