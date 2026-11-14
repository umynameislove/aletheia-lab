# K04 — ownership và interface hiện hành

Cập nhật 11/11/2026. Trạng thái backend và bằng chứng: [k04-completion-20261110.md](k04-completion-20261110.md). Các phân công ngày 10/11 là lịch sử; dùng phân công dưới đây trong lượt resume hiện tại.

| Writer | Phạm vi hiện tại |
|---|---|
| K04/root | Workspace/member/project policy/review/delete; sole integrator của runtime/worker/repository/CLI chung khi cần cho K04; nhận exact-preimage patch và kiểm caller. |
| K03 | `hosted_auth/*`, auth config/test/docs và bằng chứng recovery/session/worker captured context. Gửi runtime request qua local handoff; không đồng thời ghi runtime chung. |
| Coordinator | Một writer cho shared CSV, package/contract/fixture và trạng thái liên task. K04 cung cấp sáu row patch/evidence; không ghi đè task khác. |
| K05/K06/K07 | Đang tạm dừng theo điều phối. Giữ WIP/baseline; chỉ root tích hợp sửa nhỏ thật sự cần thiết cho K04. |
| Quân/J01/K02 joint | Actual consumer/mount/browser và release/wire acceptance chờ đầu mối; lượt K04 này không sửa UI/consumer hoặc gửi tin. |

Repo thực duy nhất: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api`. `Đồ Án 3` chứa artifact chuẩn bị/bằng chứng, không phải một repository hay contract thứ hai. Trước sửa, kiểm WIP và toàn bộ preimage SHA256; giữ backup/receipt. Không stage, commit, push, merge, reset, clean, clone hoặc chuyển worktree/branch.

## Policy và worker

`PostgresAuthorization.authorize_record(conn, auth, workspace, project, record, action)` kiểm project hiện hành, resolve đúng native snapshot/source trong cùng tenant/project rồi kiểm source theo action. Native `upload_result` đi theo source của `snapshot_id`; P3/local records vẫn chỉ dùng project policy. `record_source_filter` là predicate chung cho fixed SQL alias `r`, dùng trước LIMIT và trong batch delivery check.

`CatalogAccess` và canonical read kiểm current policy trước/sau object I/O. Review, durable export và `CanonicalResults.enlist_result` kiểm action trên transaction ghi cuối cùng. List checkpoint kiểm batch ID đã trả về, không N+1; download checkpoint kiểm creator/expiry/current source+project trước dữ liệu tiếp theo. Không giữ connection trong render, object read/stage hoặc ASGI send.

`PostgresSources._access(conn,digest,workspace,*,action)` vẫn là điểm worker intercept của K03. Worker resolve credential hiện hành của đúng captured context/account epoch trước delegate; API dùng current cookie nghiêm ngặt. `_JobCatalogPolicy` đổi read phục vụ compute thành analyze cho cả project lẫn source. Follow-up admission/dispatch/status/output và publication cùng đi qua parent scope hiện hành; không có session, permission, queue hoặc catalog thứ hai.

Lock theo account/workspace/resource, không thêm global gate: account/session/member trước project/record/source policy; result writer lấy project UPDATE trước khi enlist. Source grant mutation khóa actor/target account trước grants; source SHARE tương thích native publication. Retry chỉ definite serialization/deadlock trong transaction callback có deadline; không retry object I/O/unknown commit ACK.

## Readiness và schema

Workspace manager kiểm SELECT/schema cùng required INSERT/UPDATE column ACL hiện hành; managed clock dùng gate này để protected request fail503 khi mất quyền. Không có health write, automatic GRANT hoặc startup repair. Các gate catalog/runtime khác giữ phạm vi được mô tả trong runbook, không nhận là kiểm mọi write privilege.

Canonical migration V10, workspace/project policy extension hiện hữu, ReviewV2 và optional auditV2 dùng một schema/ledger nguồn. API/worker LOGIN tách biệt, explicit expected role/schema và cùng canonical object root. Lượt sửa policy này không đổi migration/checksum, public DTO hoặc wire variants đang chờ Quân.

## Bằng chứng và bàn giao

Manifest cuối trong `k04-resume-20261111` ghi exact source/test/SQL/config và từng lượt unit/PG/quality. Gói J01 sealed cùng receipt cũ giữ nguyên; cần build lại trên source hiện hành trước release/joint package claim. K03 caller readiness đã được writer K03 áp dụng bằng local relay, không K04 ghi đồng thời test auth.

Tracker giữ joint/browser/staging gate mở đến khi đúng owner ký; số test chồng nhau không cộng. TXT gửi Quân/Bảo là draft chưa gửi. Không dùng unit/ASGI/PG local để nhận browser background, production/RLS, 50k load hoặc distributed revoke SLO.
