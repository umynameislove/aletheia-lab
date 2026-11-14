# K04 — vận hành workspace, quyền và runtime canonical

Ngày 11/11/2026. Bản hiện hành sau tích hợp và kiểm. Receipt cuối nằm trong `k04-resume-20261111/final-k04-handoff.json`. Manifest cuối phải ghi cả source, SQL, config và test của đúng lượt; HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` không mô tả hết WIP chưa commit.

Tài liệu này cập nhật quy trình hiện hành của `k04-workspace-runbook.md`. Bằng chứng lịch sử tại [k04-verification-20261110.md](k04-verification-20261110.md) cùng receipt cũ chỉ áp dụng snapshot của chúng; [k04-completion-20261110.md](k04-completion-20261110.md) là báo cáo hiện hành. Các mô tả cũ V1–V4, chưa có cursor, export deterministic hoặc chưa có native publication không dùng để vận hành bản hiện hành.

## Ownership và môi trường

Repo thực duy nhất là `Đồ Án/aletheia-j01-api`; `Đồ Án 3` chứa chuẩn bị và bằng chứng. K04 tích hợp runtime/policy trong phạm vi được giao; K03 giữ auth/session/worker-context và test K03. K05/K06/K07 đang tạm dừng theo phối hợp hiện tại. Trước mỗi sửa chung, kiểm WIP, writer và preimage SHA256; caller gửi interface/patch cho writer. Chưa stage, commit, push hoặc merge.

Test dùng PostgreSQL riêng, cổng loopback riêng, schema và LOGIN role ngẫu nhiên, dữ liệu tổng hợp; chỉ dọn tài nguyên của lượt test. Không dùng hoặc restart DB chung để có kết quả kiểm mới. API/worker là trusted service; browser không nhận DB credential. Isolation nằm ở current authorization, query có tenant/parent scope và composite constraints; thiết kế không nhận bảo đảm RLS chống SQL tùy ý bằng principal đã bị chiếm quyền.

## Migration và principal

Operator xác nhận schema, owner, backup và maintenance window trước khi chạy DDL. Schema owner dùng cho migration/provision; API và worker dùng hai LOGIN role riêng, không phải owner. Runtime role không có SUPERUSER, BYPASSRLS, CREATEDB, CREATEROLE, INHERIT hay membership vào role khác, không sở hữu schema/table và không có CREATE trên database/application schema. Không dùng `SET ROLE` để thay kiểm đăng nhập thật.

| Thành phần | Phiên bản hiện tại và trách nhiệm |
|---|---|
| Canonical persistence | `persistence_migration.migrate(conn, schema)` V1–V10; bao gồm nền J01, workspace extension hiện hữu, canonical records, native upload publication, follow-up, login DB clock, runtime operations và durable exports. |
| Workspace | `workspace_migration` V1, giữ nguyên SQL/checksum cũ; canonical migration gọi extension này. CLI `k04_workspace_admin.py migrate` chỉ là đường workspace extension, không thay full canonical migration. |
| Project policy | `project_policy_migration.migrate(conn, schema)` V1, sau canonical; một project-grant store trong schema hiện hữu. |
| Review | `review_migration.migrate(conn, schema)` V1→V2; V2 cho parent `result` và `upload_result`; giữ nguyên metadata V1, history và checksum resource cũ. |
| Admission | `hosted_auth.admission_migration.migrate(conn, schema)` V1 độc lập của K03. |
| Action audit | Tùy chọn, `action_audit_migration.migrate(conn, schema)` V1→V2; V2 bổ sung parent/subject scope. Chỉ bật sau explicit migration, grant và kiểm profile tương ứng. |
| Preview/resumable | Extension K07 riêng. Profile API và worker phải đồng nhất; chỉ nhận extension có checksum/shape đúng. Không cài chỉ để vượt readiness. |

Các API migration nhận connection đang trong transaction, không commit hộ. Thứ tự operator trên connection trỏ đúng schema:

```python
await migrate_canonical(conn, schema)
await migrate_projects(conn, schema)
await migrate_reviews(conn, schema)
await migrate_admission(conn, schema)
# Chỉ với profile audit đã được chọn:
if audit_enabled:
    await migrate_action_audit(conn, schema)
await grant_runtime(conn, schema, api_role, profile="api", audit=audit_enabled)
await grant_runtime(conn, schema, worker_role, profile="worker", audit=audit_enabled)
# Commit transaction thành công rồi mới báo adopted/upgraded.
```

Tên trên tương ứng các hàm maintained trong bảng, không phải CLI hoặc một migration engine mới. Hai LOGIN role phải được operator tạo trước; `grant_runtime` không tạo role. Hàm kiểm owner/role/schema và chỉ grant bảng/cột đã cài đúng profile. Runtime startup không GRANT, DDL, ghi health row, sửa checksum hoặc tự repair dữ liệu.

Unknown version, checksum/structure drift, workspace ownerless hoặc snapshot/source sai tenant phải rollback và điều tra. Không drop/recreate, sửa ledger/signature để làm ready, tự nâng analyst hay chuyển object sang tenant khác. Canonical upgrade chỉ áp dụng suffix đã biết sau khi prefix được xác minh. Rollback release cần giữ additive tables/constraints và writer tương thích; không tự down-migration xóa invite/grant/history/export.

## Cấu hình API và worker

Mỗi process đọc TOML explicit qua `ALETHEIA_HOSTED_CONFIG`, auth policy qua `ALETHEIA_HOSTED_AUTH_POLICY` và secret qua tên biến môi trường đã khai báo. Điền cùng `storage.expected_schema`, nhưng `storage.expected_database_role` phải khớp LOGIN thực của từng API/worker; mỗi process có DSN riêng. Pool `role="api"|"worker"` chỉ chọn budget, không đổi principal thay operator. Staging yêu cầu `sslmode=verify-full` và đúng trust root. Không đặt DSN/password/session/token trong config review hoặc log.

API/worker dùng chung canonical storage `storage.object_root/catalog`; importer/native publication cũng phải dùng chính root này. Không tạo catalog/identity/session/queue thứ hai. Tổng API pool + worker pool + admin reserve phải trong connection budget được cấu hình; số này là budget, không phải chứng nhận tải.

`verify_runtime_binding` kiểm `current_user = session_user = expected_database_role`, schema và principal không có quyền owner/DDL quá mức, cùng relation resolution để từ chối bảng shadow sai schema/temp. Full startup verify canonical, project policy, review và audit nếu bật; upload profile phải đồng nhất trước khi nhận việc. Worker dùng adapter captured-context của K03 và cùng catalog được inject vào source/job repositories.

## Readiness và lỗi dependency

`/health/ready` là dependency check; capability chỉ là hint, mọi operation vẫn kiểm quyền hiện hành trong transaction. Readiness mất thì trả `503 service_unavailable`, không đổi thành 403 hoặc tự grant quyền để tiếp tục. Clock/dependency gate được bound bởi deadline; cancellation bên ngoài phải truyền tiếp, log chỉ fixed category, không SQL/DSN/provider detail.

| Gate | Hành vi phải đọc đúng |
|---|---|
| Session/account/clock | Current cookie/context/account epoch và DB clock theo policy K03. |
| Workspace | Bản đã tích hợp/kiểm thêm SELECT kiểm cần thiết INSERT/UPDATE-column privileges và quyền UPDATE tối thiểu cho row locks; health và managed clock cùng dùng gate. Private PG đã kiểm 14 ca thu hồi/khôi phục column ACL và503/no effect/recovery. |
| Catalog/export | `CatalogAccess.is_ready` kiểm quyền SELECT hiện tại trên canonical/project/review/export relations bằng `LIMIT 0`, không đọc private rows. Không suy rằng gate này chứng minh mọi quyền ghi export/settings. |
| Runtime operations | Kiểm schema/cột project metadata và settings hiện có; không tự sửa schema hoặc chính sách quota. |
| Audit enabled | Cần audit V2 và quyền SELECT + INSERT đúng cột; disabled không buộc có audit schema. |
| Upload/follow-up | Current queue readiness, profile staging/preview đồng nhất với profile lúc compose và canonical follow-up khi được bật. |

Khi mất quyền, operator so actual schema/role/current ACL với reviewed `grant_runtime` và manifest. Khôi phục quyền chỉ là operator action sau khi rõ nguyên nhân; startup/readiness không được tự làm. Render worker bận, timeout hoặc đã đóng trả unavailable; integrity/schema lỗi giữ safe failure. Không ghi effect để thử health.

## Provision, first owner và invitation

Identity do operator provision bằng exact issuer+subject đã xác minh từ IdP; email giống nhau không link account. JSON private tối đa 8 KiB:

```json
{"identity":{"issuer":"https://id.example.invalid","subject":"exact-idp-subject","email":"synthetic@example.invalid","email_verified":true},"display_name":"Synthetic user"}
```

```text
python -B scripts/k04_workspace_admin.py provision --schema <schema> --expected-issuer https://id.example.invalid --identity-file <private-file>
```

CLI nhận secret qua `ALETHEIA_HOSTED_DATABASE_URL` hoặc tên `--database-url-env`, không nhận DSN trực tiếp. Provision kiểm workspace migration đã sẵn sàng; không migrate, sửa binding hay enable lại account cũ. Chưa provision bị K03 từ chối. Account enabled/verified, `needs_workspace` và chưa có membership active mới tạo workspace cùng first owner trong một transaction; session hint `can_create_workspace` dùng đúng gate này.

Mọi mutation cần current session cookie, bound CSRF cookie/header, Origin và body hợp lệ. Actor lấy từ server session; `user_id` trong path/body là target. Invite chỉ owner/admin, target provisioned enabled/verified, không invite role owner. TTL 60–604800 giây; DB giữ digest, token chỉ trả ở lần tạo và không xuất trong pending list. Code không tự gửi email/chat. Không đặt token trong URL/log/storage/tracker.

Accept kiểm đúng identity và trạng thái/revision của creator manager lúc commit. Đúng target hết hạn: `410 invitation_expired`; sai account: `403 invitation_wrong_account`; unknown/used/revoked hoặc creator không còn hợp lệ: `404 invitation_unavailable`. Invitation chưa là membership trước khi accept commit. Exact-email target lookup chỉ hỗ trợ chọn identity đã provision; không suy quyền từ email/domain.

## Membership, CAS và scope hiện hành

| Operation | Current rule |
|---|---|
| Read/export | Năm role đều có trần quyền; vẫn cần workspace/member và object grants hiện hành. Export handle thêm creator binding. |
| Import/preview/confirm/analyze/refresh/update | Owner/admin/analyst; source/project action gate cần thỏa theo resource. |
| Review | Owner/admin/analyst/reviewer; append-only annotation, tác giả sửa chính annotation bằng CAS, giữ history. |
| Delete | Owner/admin; analyst cần explicit read+delete, kể cả object mình tạo. Reviewer/viewer không delete. |
| Manage members/settings | Owner/admin; admin không sửa owner hoặc tự nâng owner. Settings không vượt platform ceiling. |
| Transfer owner | Owner, target active và account enabled/verified; nâng target và hạ actor thành admin, tăng cả revision. |

Unknown action/role bị deny. Object cùng tenant vẫn cần đúng parent; immutable result/history không cấp quyền hiện tại. Source grant và project grant là hai scope khác nhau. Với native snapshot/result, yêu cầu là **source AND project** theo source của chính snapshot parent: list/search/cursor, claim/evidence/graph, review, export/download và queue/publication. P3 event và project metadata giữ project scope hiện hành. Đã kiểm caller đọc/review/export/list delivery và follow-up admission/dispatch/final publication; project creator hoặc project grant không được dùng để vượt source revoke. Dùng exact snapshot trong receipt cuối.

Member/source/project grant mutations dùng quoted integer `If-Match`; revision stale trả 412, thiếu precondition trả 428. Success trả revision/ETag theo producer contract. Stale/lost response chỉ refresh facts và đối chiếu outcome; không tự retry với revision mới. Operation dùng idempotency key phải giữ key khi retry cùng request chưa rõ commit; request khác cùng key trả 409. Retry job là quyết định mới có durable lineage/key mới và vẫn kiểm current parent/actor.

Self-leave giảm active; remove/leave cập nhật onboarding/last workspace. Last usable owner không được leave/remove/demote. Account locks đi trước workspace/member; owner transfer/member mutations và deferred owner guard bảo vệ schedule đồng thời. Disable account khẩn cấp thuộc K03; operator recovery sau xác minh ownership, không tự promote người còn lại. Manual revoke sessions dùng exact issuer+subject qua CLI `revoke-sessions`, private identity file và expected issuer; xem handoff K03 của đúng phiên bản, không có public recovery endpoint hay automatic IdP webhook mới.

Directory workspace/member/invitation dùng signed opaque cursor, mặc định 50/tối đa 200; target lookup mặc định/tối đa 20. Cursor pin actor/context/workspace/member revision/query/purpose; mỗi trang kiểm current facts, filter quyền trước LIMIT. Search là exact canonical ID; total unknown được khai báo, không trả count ngoài scope. DELETE invitation hỗ trợ body thật sự rỗng sau Origin/CSRF và bounded stream; body không rỗng vẫn theo parser. Không đánh đồng page limit với giới hạn tổng số member.

## Canonical publication, export và revoke

Native upload snapshot/result được publish vào cùng canonical records/current pointers với scoped FK và verified typed projection; không dùng `j01_jobs.result` làm fallback store cho catalog. Review V2 nhận native `upload_result`. Logical delete ghi tombstone, giữ history/object/job receipt và chặn current access/reimport theo policy; không nhận là physical purge.

Export JSON/Markdown vẫn render trong bounded worker hiện hữu rồi trả descriptor 201, nay có row durable trong `k02_exports`. Handle chứa result ID, format và nonce ngẫu nhiên 32 hex; digest object không phải download capability. Row immutable, mặc định hết hạn sau 15 phút theo DB clock (DDL tối đa một giờ); expiry không là xóa vật lý. Same key được scope theo workspace+creator, replay phải cùng result/format và còn hạn; expired trả `410 export_expired`, muốn xuất lại dùng operation/key mới. Handle chỉ người tạo lấy được, sau kiểm current membership, project/action và source policy khi native liên quan. Không cấp quyền bằng việc biết URL/nonce.

Download/HEAD/single Range kiểm quyền trước headers/size và trước chunk tiếp theo; checkpoint cũng kiểm creator, immutable descriptor và expiry. Revoke hoặc expiry sau headers kết thúc partial response, không gửi HTTP status thứ hai. DB connection không bị giữ trong lúc chờ gửi bytes. Không thu hồi bytes đã gửi hợp lệ trước revoke. Events là bounded canonical `regression_event` JSON/polling và job polling, chưa quảng cáo SSE/PDF.

API kiểm quyền lúc enqueue/status/output; worker K03 resolve exact captured context và current credential/account epoch, rồi source/project action hiện hành ở dispatch và fenced publication. Rotate cookie không được chuyển việc sang session khác cùng account. Cancel chặn publication và giữ terminal truth; không hứa CPU dừng ngay. Revoke không xóa result/job receipt để giả có hiệu lực.

## Điều tra lỗi và phục hồi

| Public outcome | Cách đối chiếu |
|---|---|
| 401 expired/revoked | K03 current cookie/context/account epoch; không lấy client role/hint làm authority. |
| 403 permission/membership/CSRF | Current role/member, source+project actions, owner/target policy và Origin/CSRF; không lộ object metadata. |
| 404 not_found | Absent và inaccessible cùng nội dung; không dò tenant qua lỗi, count hoặc child ID. |
| 410 invitation/export expired | Tạo quyết định/token/export mới sau current auth; không kéo dài row immutable. |
| 409 idempotency / 412 revision | So intent/outcome server, giữ key đúng request và refresh revision trước xác nhận mới. |
| 503 dependency | So exact release manifest, schema/role/ACL/migration/profile/clock và fixed diagnostic; rollback transaction lỗi trước pooled reuse. |

Commit ACK không rõ phải đối chiếu durable receipt/idempotency; chỉ retry transaction được thiết kế cho 40P01/40001. Không retry mù render/object I/O hoặc mutation đã có thể commit. Restore/release gate thuộc owner K05/K17/J08; local pool test không xác nhận backup/RPO/RTO hoặc tái cấp quyền sau restore.

## Bằng chứng và gate bàn giao

Baseline tham chiếu: `production-freeze-v2.json`, 621 production files, runtime set `488b140c055dfd4d64cb0f0358f84e3eee32c736c63266859811b528245d46f3`. Receipt K02 trước có 1125 selected units, strict mypy 81 modules, 24 private PG và 63 TLS checks; đây là snapshot lịch sử, không cộng với lượt mới hoặc dùng để xác nhận bytes đã đổi. Bản baseline K04 bổ sung có 500 unit passed / 39.97 s; root giữ start/end hash của lượt đó.

| Lượt | Kết quả và phạm vi |
|---|---|
| Full backend unit J01/K02/K03/K04/K05/K06/K07 | **2497 passed, 2 skipped /138.76s**, 1474 file đầu vào start/end không đổi. Hai ca K05 query-probe cần DB được chạy thật trong lượt focused bên dưới; không tính skip là pass. |
| Full K04 và caller PG | **133 passed, 2 failed /523.01s**, 1474 đầu vào không đổi, toàn bộ **79 ca K04 đạt**. Giữ log cả hai lỗi; không gọi lượt này là full pass. Một assertion K05 cũ thiếu pointer result được sửa test-only. K06 không sửa test/production: row cho thấy DB wall clock khi claim/terminal sớm hơn created khoảng 4–5 giây; constraint đã fail closed. |
| Fresh private focused replay | **28 passed, 1 failed /145.43s**: hai caller trên, A29 sau chỉnh import, native source revoke và Range/download revoke đạt; còn assertion query-probe cũ chờ 6 query plan trong khi maintained script có 8. Current source stable. Read-only monitor gồm 572 mẫu ghi nhận 7 bước lùi DB khoảng 4,86 giây trong khi host/monotonic tiến; K06 đạt khi chạy nguyên test nhưng môi trường clock chưa được sửa. |
| Query-probe final | **6 passed /12.69s**, gồm hai ca DB từng skip. Chỉnh test-only kiểm literal đủ 8 query ở cả large/small; giữ counts/copies/cleanup/cold-cache-unclaimed/data-statements1/failure checks. Source stable, own cleanup verified. Không chạy lại các phần đã đạt không bị sửa. |
| Quality | **Ruff trên 28 file và strict mypy trên 10 module đạt** theo config repo thật. Lỗi import-order A29 ban đầu giữ log, đã chỉnh và chạy lại; production không đổi. |
| Package | Wheel/sdist offline; **620 installed Python/SQL files khớp**, import 10 module K04 từ installed target đạt, 622 package inputs stable. Wheel SHA256 `606e7630d419a5131f85ef9558d2eef5b6e6608eb9d69202e91847c7d4b06705`. Đây là build local mới, không thay gói J01 lịch sử hay joint receipt. |

Revoke đo bằng `perf_counter`, từ return của mutation đã commit đến checkpoint đầu tiên từ chối: **nguồn native 0.020700s**, **project grant 0.017851s**. ASGI + actual API LOGIN + PG16.15 local, test chunk 32 byte: 32 byte hợp lệ trước revoke, 0 byte sau revoke. Đây là đo có barrier, không phải TLS/browser/staging/distributed/background SLO. Production chunk tối đa 64 KiB theo implementation hiện hữu.

Receipt chính: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án 3\k04-resume-20261111\final-k04-handoff.json`; manifest `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án 3\k04-resume-20261111\final-files.json`. Các per-run before/after, failed/red log, clock evidence, dependency versions và cleanup proof nằm cùng thư mục. Các lượt chồng nhau không cộng tổng.

Không joint browser/staging/load acceptance từ unit/ASGI/PG. Quân giữ actual host mount, account/workspace cache/draft/toast/bfcache và reconnect/poll UX; Bảo/owner giữ production privacy/provisioning/IdP/origin/TLS/quota/budget/rollout. K04 cung cấp backend evidence và interface trên cùng build/schema/config; joint J02/J05 và wire variants giữ trạng thái riêng. Các gói/receipt cũ không sửa hoặc relabel thành kết quả của bản mới.
