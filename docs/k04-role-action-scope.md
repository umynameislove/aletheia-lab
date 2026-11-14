# K-04 — role, action và phạm vi tài nguyên

Ngày 11/11/2026. Bản hiện hành cùng patch K04 kiểm đồng thời source và project cho dữ liệu native, đã có current-source unit/private PG evidence trong receipt cuối. Đây không phải xác nhận joint với Quân/Bảo, bản phát hành hay chứng nhận cho toàn bộ WIP.

Nguồn thực thi là [authorization.py](../src/aletheia_lab/hosted/authorization.py) và các repository/caller bên dưới. Wire DTO vẫn ở `hosted_contract`, cùng version `product-api/v1-draft` hiện có; tài liệu này không tạo một nguồn contract khác hoặc thay fixture v7 đã đóng băng.

## Role và operation hiện có

“Có” là điều kiện role; mỗi lần gọi vẫn cần account/session/member hiện hành, đúng workspace, parent, grant và revision. Grant không nâng role. Action hoặc role không biết bị từ chối.

| Operation/action | Owner | Admin | Analyst | Reviewer | Viewer | Phạm vi bổ sung |
|---|---|---|---|---|---|---|
| Đọc project metadata, catalog, snapshot/result/claim/evidence/graph, event polling | Có | Có | Có | Có | Có | Project `read`; dữ liệu native còn cần source `read` đúng parent |
| Upload/import/preview/confirm | Có | Có | Có | Không | Không | Action nguồn ánh xạ sang `write`; staging/lease còn kiểm đúng chủ sở hữu và trạng thái |
| Analysis/follow-up | Có | Có | Có | Không | Không | Action `analyze`, project `read+write`; native analysis kiểm snapshot/source; xem ranh giới async bên dưới |
| Refresh project upload | Có | Có | Có | Không | Không | Project `read+write`, source cũ và source mới `read+write`, parent/pointer/revision do server pin |
| PATCH project display name | Có | Có | Có | Không | Không | Action `update` → project `read+write`, CAS revision của project |
| Ghi annotation/review | Có | Có | Có | Có | Không | Project `read+review`; native còn cần source `read+review`; chỉ author ghi revision tiếp theo |
| Đọc review/history | Có | Có | Có | Có | Có | Current `read` của result; native kiểm cả source và project |
| Tạo export JSON/Markdown, download/HEAD/Range | Có | Có | Có | Có | Có | Project `read+export`; native còn cần source `read+export`; artifact chỉ cho đúng creator hiện hành |
| Logical DELETE project | Có | Có | Explicit grant | Không | Không | Analyst luôn cần project `read+delete`, kể cả creator; không purge source/object |
| Quản lý member/invite, source/project grant | Có | Có | Không | Không | Không | Admin không sửa/remove owner hoặc tự nâng lên owner; target và CAS phải hợp lệ |
| Chuyển owner | Có | Không | Không | Không | Không | Target active, account enabled/verified, revision đúng; giữ ít nhất một owner còn dùng được |
| Đọc/PATCH workspace settings | Có | Có | Không | Không | Không | Settings revision riêng; quota override nằm trong trusted platform ceiling |
| Cancel/retry job | Có | Có | Job của mình | Không | Không | Vẫn cần quyền operation và parent hiện hành; terminal/deadline/retry policy vẫn áp dụng |

Session/login/logout/renewal và operator identity provisioning không lấy quyền từ một role string do browser gửi. Workspace list/first-owner/accept-invite/self-leave áp dụng các điều kiện account/onboarding/membership riêng ở phần dưới. Không có quyền self-provision identity qua email/domain.

## Project, source và parent native

Owner/admin có quyền implicit trên tài nguyên tồn tại trong workspace của mình. Creator có quyền implicit trên từng source hoặc project theo role hiện hành, ngoại trừ analyst `delete` luôn cần explicit `read+delete`. Creator của project không tự động trở thành creator của mọi source trong project đó.

Thành viên khác cần grant đúng `(workspace, resource, user)`. Grant không rỗng phải có `read`; operation còn cần `write`, `review`, `export` hoặc `delete` tương ứng. Revoke membership chặn mọi grant ngay cả khi các row grant vẫn còn. Reactivate là quyết định tường minh và xét lại role/grant hiện hành.

Patch native dùng `authorize_record` làm điểm kiểm chung:

| Record/đường đọc | Parent dùng để kiểm quyền |
|---|---|
| P3/local canonical record | Project hiện hành; không bổ sung source grant |
| `upload_snapshot` | Project của record **và** `source_id` của chính snapshot |
| `upload_result`, gồm follow-up result | Project của result **và** source của `snapshot_id` đã bind, chỉ resolve `upload_snapshot` trong cùng workspace/project |
| Claim/evidence/graph của native result | Cùng result đã kiểm ở trên; kiểm thêm đúng child-parent trước khi trả |
| Annotation/history/export/download native | Cùng result và source đã bind; kiểm action tương ứng ở cả hai parent |

Không suy quyền từ `mapping.project_id`, nhãn project, client role/header, job JSON hoặc một source khác được phép đọc. Thiếu parent, sai scope, payload/hash/projection không khớp phải fail closed. Canonical object phải được kiểm bytes/hash và typed wrapper; không fallback sang projection cũ khi object hỏng.

List/search records trong project và workspace lọc project, tombstone và source native **trước `LIMIT+1`**. Project chứa nhiều source vẫn giữ semantics metadata theo project policy; source revoke chỉ loại dữ liệu có parent tương ứng. Grant ở source A không mở snapshot/result của source B. Batch checkpoint kiểm đúng ID đã chọn trước headers; source revoke sau page query chặn cả response, không N+1 hoặc refetch trang. Alias `snapshot`/`result` và grouping `snapshot_id` dùng cùng điều kiện.

## Review và lịch sử bất biến

[PostgresReviews](../src/aletheia_lab/hosted/review_repository.py) chỉ ghi trên canonical result hợp lệ. Annotation append revision bằng CAS; revision đầu dùng expected revision 0. Chỉ author cũ được nối revision mới, kể cả owner/admin cũng không ghi đè annotation của người khác. Mỗi revision giữ author, body, status và thời điểm riêng; UPDATE/DELETE lịch sử bị DB chặn. Review không sửa immutable result, claim, evidence hoặc causal status.

Role/grant bị revoke sau canonical I/O phải được kiểm lại trước transaction ghi review. Đọc annotation/history dùng current result read scope, không cần quyền ghi review. Draft chưa lưu và vị trí lưu draft ở browser thuộc nghiệm thu consumer riêng.

## Async, export và xóa logic

Các caller tại [PostgresJobs](../src/aletheia_lab/hosted/postgres_jobs.py) và [project_job_operations.py](../src/aletheia_lab/hosted/project_job_operations.py) kiểm authority hiện hành tại các mốc admission/dispatch/publication/status/output theo operation. API dùng credential hiện hành; worker captured context phải resolve current account/session epoch và membership, không dùng một `Authorization` đã capture vô hạn.

Native analysis dùng đúng snapshot, source và project. Refresh còn kiểm source cũ và source mới, revision/pointer và lineage identity; request chỉ đưa `source_id` đã upload, không đưa filesystem path. Follow-up dùng canonical parent result và giữ immutable parent/snapshot linkage. Bốn ca private PG đã kiểm native follow-up thiếu source.write lúc admission, revoke trước dispatch và write-only/full revoke sau staging trước commit: không publish canonical effect, giữ failed receipt/audit. `_JobCatalogPolicy` map read phục vụ compute sang analyze ở cả source và project; `CanonicalResults.enlist_result` kiểm parent hiện hành trong transaction cuối.

Analysis/follow-up/refresh dùng queue và durable receipt hiện có. Status/output không mở cho mọi member chỉ vì biết job ID; repository kiểm parent và current authority. Cancel/retry còn yêu cầu creator hoặc owner/admin, cùng quyền operation; cancel receipt không hứa process dừng tức thì. Giữ request key khi outcome chưa rõ; không tự thay revision hoặc resend mutation sau 412/CSRF lỗi.

[DurableExports](../src/aletheia_lab/hosted/durable_exports.py) lưu artifact trước descriptor `201`. Handle có nonce riêng, có expiry và bind workspace/project/result/creator/format; không đoán download URL từ result ID. Replay cùng key kiểm quyền hiện hành, không render lại hoặc kéo dài TTL; đổi target/format cùng key trả 409. Handle expired không sống lại. Quyền download kiểm trước metadata/expiry/range/HEAD, sau object I/O và trước chunk tiếp theo; người khác, kể cả owner, không dùng artifact của creator khác.

Logical delete ghi tombstone bằng project CAS và giữ immutable history/object/job truth. Retry sau commit có durable receipt theo policy hiện có. Nó không xóa source, không purge vật lý và không thêm source grant để xóa cả project. Đọc metadata về trạng thái deleting tuân theo repository metadata riêng; quyền đọc dữ liệu thông thường vẫn bị tombstone chặn.

## Onboarding, member và owner

Identity do operator provision bằng exact issuer+subject sau xác minh IdP. Enabled/verified identity đã provision có thể có session `needs_workspace`; chỉ khi không có membership active mới được tạo workspace cùng first-owner trong một transaction. Session có `can_create_workspace` là hint, server vẫn kiểm lại.

Invite do owner/admin tạo cho identity đã provision, enabled và verified; không invite role owner. Token chỉ trả một lần, DB giữ digest; expiry 60–604800 giây, revoke và consume một lần. Accept kiểm đúng account target, current issuer account/manager role/revision và trạng thái invitation. Exact-email lookup chỉ tìm target đã provision, không liên kết identity qua email và không gửi email tự động.

Member patch dùng `If-Match` revision của target. Owner cuối còn sử dụng được không được leave/remove/demote. Transfer nâng target lên owner, hạ actor thành admin và tăng cả revision. Owner có thể giảm/remove owner khác khi còn owner hợp lệ; admin không tác động owner. Account locks, workspace lock và deferred owner constraint giữ invariant khi nhiều transaction cùng sửa. Không suy nghiệm thu race từ việc chỉ gọi tuần tự từng API.

## Cursor, capabilities và delivery

Cursor ký gắn purpose, workspace, actor, context, membership revision và query. Mỗi trang kiểm current facts; cursor không phải grant. Grant revoke được áp dụng lại ngay cả khi membership revision không đổi. Search hiện là exact canonical ID, tổng số có thể `null` với `total_kind=unknown`; không lộ tên/count ngoài scope. Graph pagination có node/edge phases; phải theo `next_cursor` opaque đến hết.

[UploadHostedBackend.capabilities](../src/aletheia_lab/hosted/upload_runtime.py) trả hint theo role, composition và readiness hiện hành: `allowed_actions`, backend `local_evidence`, source `zip_bundle`, export `json/markdown`, các feature catalog/review/export/event polling, app invitation, job controls, follow-up/refresh và resumable/preview lease khi profile tương ứng sẵn sàng. Không suy quyền trên một resource cụ thể từ feature `true`. Project/settings metadata dùng repository policy kể cả khi không có một feature key riêng.

Các mutation vẫn kiểm session, Origin/CSRF, body/schema, idempotency và CAS tại server. Resource không tồn tại hoặc ngoài scope dùng safe `404/not_found`; stale revision dùng `412/revision_conflict`, thiếu precondition 428, readiness/storage/deadline không sẵn sàng 503. Lỗi không trả token, SQL/driver detail, object handle/path hay nội dung riêng tư. Private response dùng no-store.

Stream không giữ DB connection trong lúc gửi bytes. Mỗi checkpoint dùng current facts, không hứa thu hồi byte đã tải hợp lệ trước revoke. Mục tiêu ≤5s phải có đo từ mốc commit/return xác định trong receipt; local ASGI/PG không thay browser, staging hoặc SLO phân tán.

## Ranh giới bằng chứng và ownership

Chọn repository tenant isolation cùng composite FK/constraint và API/worker LOGIN role riêng; không tuyên bố dùng RLS hoặc chống arbitrary SQL dưới principal đã bị chiếm. Pool reuse sau success/rollback/cancellation, privilege revoke/readiness, raw SQL last-owner và actual restricted API role phải ghi đúng source/schema/config/hash đã kiểm.

K04 giữ policy/workspace/member/review/delete và tích hợp runtime; K03 giữ auth và auth caller tests. K02 refresh/metadata/settings/quota/graph/durable export/delete replay đã có trong baseline hiện hành, không tạo repository hoặc queue khác để làm lại. CSV tracker dùng một coordinator writer, chỉ cập nhật row K04 khi đúng receipt đã được đối chiếu.

Chưa có tuyên bố Quân UI/consumer, joint J02/J05, public deployment hay production operator policy. Connector/PDF/SSE/physical purge chỉ được công bố nếu có implementation/profile và nghiệm thu tương ứng; profile hiện tại dùng upload ZIP, JSON/Markdown và polling. Bằng chứng lịch sử giữ nguyên và chỉ chứng nhận đúng bytes của lượt đó.
