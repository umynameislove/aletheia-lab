> Cập nhật hiện hành: xem [k04-completion-20261110.md](k04-completion-20261110.md). Tài liệu này giữ bằng chứng snapshot trước; trạng thái route chưa có ở phần lịch sử không đại diện cho bản bổ sung mới.

# K-04 — kết quả triển khai và kiểm chứng 10/11/2026

**Đã triển khai và kiểm local phần hosted hiện có; chưa đủ đóng toàn bộ K-04.**
Không dùng bảng inventory, pure-policy test hoặc fixture để nhận endpoint chưa
tồn tại. Không stage/commit/push/merge, không clone/move/copy repository.

Checkout thực: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api`,
branch `feat/product-api`, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` + WIP.
`Đồ Án3` là workspace bằng chứng. Nguồn nhận diện bản kiểm là JSON manifest SHA256,
không chỉ HEAD. Log và manifest ở
`C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án 3\k04-verification-20261110`.

## Triển khai

| Bước | Đã có code và bằng chứng | Còn mở |
|---|---|---|
| K-04.01 | Operator provision exact issuer/subject; session needs_workspace; HTTP create first-owner, app invite bound target/expiry/revoke/one-use; transfer/member CAS; owner API/raw SQL race ở RC/RR/Serializable; target disabled/unverified bị từ chối | Producer/consumer/joint onboarding và hosted UI chưa nghiệm thu; account-disable recovery thuộc K-03/operator |
| K-04.02 | Một action policy deny-default; current session/account/member; source grant + parent snapshot/job; reviewer review, viewer export và analyst explicit delete được test policy; tất cả route đang compose có allow/deny local | Hosted project/catalog policy bridge K-05; review/export/delete/cancel/retry chưa có endpoint, không nhận pure policy là khả năng hosted |
| K-04.03 | Source/snapshot repository kiểm tenant+parent+payload; job status/output dùng source grant; tráo workspace/object, response target, foreign query từ chối; no-store/errors an toàn | Search/cursor/graph/event/evidence/export/download chưa có route tương ứng; chưa joint J-05; member pagination >200 fail503 |
| K-04.04 | Current check ở nhận việc, dispatch, read, snapshot/job publication; role/grant revision; barrier revoke×enqueue/publish/output; receipt/terminal giữ nguyên; idempotency concurrent không tạo job trùng; đo HTTP/PG dưới5s trên đường có thật; adapter worker K-03 đã bàn giao và kiểm caller | Event/download/range/cache/stream và network in-flight chưa có phép đo; rotation chuyên biệt do K-03 kiểm, chưa joint toàn hành trình |
| K-04.05 | No RLS/GUC; explicit repository scope+composite source/snapshot FK; cùng pg_backend_pid A→B sau success/error/rollback/cancel Future và cancel query thật; raw owner constraint race; API→worker process test đạt trên adapter bỏ global lock | Deployment runtime privileges/rollout baseline thuộc K-05/operator; không lấy test schema owner làm chứng cứ RLS |

Tài liệu dùng: K-04 và K-04.01–.05 trong gói giao việc, ma trận role và Hop-dong,
identity/database/incident cases, J-02/J-05; báo cáo code mới nhất K-02/K-03/J-01.
Snapshot lỗi account-switch cũ đã được báo cáo J-01 thay bằng HTTPX A→B thành
công; K-04 không tự sửa login/OIDC. Contract hiện tại vẫn producer draft, v8
proposal không được nhận là bản đã nghiệm thu.

## Các lượt kiểm có giá trị

Không cộng các lượt chồng nhau. Failed/interrupted/setup error không tính passed.

| Lượt | Kết quả | Phạm vi/giới hạn |
|---|---|---|
| hosted-unit-final.log | **1201 passed,80.79s,exit0** | J-01/K-02/K-03/K-04 unit trên WIP ghi trong start/end manifests; không phải toàn research/product/frontend |
| k04-caller-final.log | **244 passed,8.65s,exit0** | K-04 unit và caller upload capabilities/managed runtime/routes/readiness, sau sửa import cuối |
| postgres-http-final.log | **17 passed,22.80s,exit0** | PostgreSQL thật/schema UUID riêng + HTTPX ASGI, owner/invite/grant/race/pool/snapshot/job; không TLS/IdP/browser |
| worker-https-caller-final.log | **2 passed,11.67s,exit0** | API→worker process riêng + PostgreSQL; bài TLS local dùng backend synthetic. Hai bài riêng, không gọi là một journey TLS+DB+IdP |
| ruff-final.log | **exit0** |9 source modules K-04/caller + test K-04 và2 scripts |
| mypy-final.log | **9 source files,exit0** | strict,follow-imports=silent; không coi là whole-repo typecheck |
| export_k04_contract + contract tests | Schema/payload valid | OpenAPI fragment8 operations +5 payload được xuất từ cùng producer models; chưa consumer/joint accepted |

Trong lượt1201, K-05 sửa/thêm object_store/postgres_objects/postgres_catalog và
K-03 sửa test_worker trong lúc chạy. Trong lượt17, catalog/test_worker đổi;
trong lượt244, hai file object K-05 đổi. **Các source/test K-04 thực sự được dùng
không đổi giữa start/end**; các path đổi được lưu rõ trong manifest. Không dùng
những lượt này chứng nhận phần K-05/K-03 mới phát sinh hoặc toàn checkout đang
thay đổi. Lượt worker-https-caller-final có648file start/end giống nhau, không
đổi source/test trong lúc chạy. Worker-session adapter K-03 SHA256
`91566a7b4db859ae60b1a2161b2718ed9b126fd27aedc5fa1e65fd04bcae9058`,
managed_worker SHA256
`4c75819cf8818bad96f480a9e7f067b642d763971fca59964464253c2f3f21b5`.
Adapter dùng account SHARE thay global gate, giữ exact captured context và
delegate source write grant hiện hành. Các ca multi-rotation/compute child và
denial chuyên biệt là bằng chứng riêng K-03, không cộng vào count K-04 ở đây.

Lượt đầu HTTP unit gặp sandbox tmp_path setup errors, sau đó76passed; lượt unit
K-04 có1fail do test importlib không đăng ký module Pydantic, đã sửa test loader.
Lượt PG mở rộng có3fail/10pass do fixture clock_timestamp lệch microsecond giữa
các cột; đã thay statement_timestamp. Lỗi Ruff import đã sửa; strict mypy đầu
thiếu dependency path/annotation rồi chạy lại đạt. Những lượt lỗi giữ log riêng,
không được gộp thành passed.

## Đo revoke

`postgres-http-final.log` ghi:

- Revoke HTTP response hoàn tất → response output404 đầu tiên: **0.017177s**.
- Bắt đầu HTTP mutation revoke → response output404: **0.047466s**. Đây là upper
  bound chứa cả commit DB; actual commit-to-denial không lớn hơn số này.
- Barrier multi-connection commit → enqueue/publication/output bị từ chối:
  **0.036555s**, local PostgreSQL; không dùng sleep ngẫu nhiên điều khiển race.

Thời gian monotonic perf_counter, synthetic accounts/workspaces, cùng máy,
PostgreSQL localhost15432, HTTPX ASGI và pool hữu hạn. Đây là bằng chứng ≤5s cho
các request/repository đang có, không phải SLO tải, browser/staging hoặc mọi
byte network đang in-flight. Response đã được authorize trước revoke có điểm
linearization tại read transaction; không hứa thu hồi dữ liệu đã tải hợp lệ.
Stream/range/download dài cần điểm kiểm riêng trước dữ liệu mới và ca thực tế.

## Review và dựng lại

- `docs/k04-role-action-scope.md`: ma trận và source-parent policy/error codes.
- `docs/k04-workspace-runbook.md`: migrate/provision/onboarding/member/transfer/
  revoke, lỗi và phục hồi; explicit production/joint boundaries.
- `docs/k04-ownership-handoff.md`: writer và interface K-02/K-03/K-05.
- `scripts/k04_workspace_admin.py`: CLI operator; secret chỉ qua env reference.
- `scripts/export_k04_contract.py` và `fixtures/j01/k04-workspaces-v1-draft`:
  extension để Quân review, không overwrite fixture v7.
- `run_checks.ps1`, `run_quality.ps1`, start/end JSON manifests trong workspace
  evidence ghi môi trường isolated Python/dependency targets và lệnh cụ thể.

K-04 không cài dependency mới, không khởi động IdP hoặc dùng dịch vụ có phí.
Test chỉ tạo và dọn namespace của chính nó, không sửa schema/data chat khác.
CSV nghiệm thu chỉ cập nhật đúng các dòng K-04 sau backup; giữ trạng thái mở
vì các điều kiện còn thiếu ở bảng trên, không tự ghi passed dựa riêng test.
