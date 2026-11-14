# K04 — workspace, phân quyền và cách ly dữ liệu

Cập nhật 11/11/2026. Repo duy nhất: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api`, branch `feat/product-api`, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` cùng WIP chưa commit. Phiên bản được kiểm nằm trong manifest SHA256/start–end của từng lượt, không chỉ HEAD. Không stage, commit, push, merge, đổi branch, clone hoặc di chuyển repo.

Bản này thay trạng thái hiện hành trong báo cáo ngày 10/11; các receipt cũ vẫn mô tả đúng snapshot lịch sử của chúng. Không sửa gói J01 đã niêm phong hoặc dùng kết quả cũ để xác nhận source mới.

## Kết quả theo yêu cầu gốc

| Phần | Backend hiện hành và chứng cứ |
|---|---|
| K04.01 | Operator provision exact issuer/subject; first-owner có gate hiện hành; invitation exact account/expiry/revoke/replay; member CAS/ETag; transfer và last usable owner. Có HTTP/PG thật, nhiều kết nối và deferred owner guard. Ba ca A29 ép transfer đối đầu remove/invite trên hai physical backend, quan sát lock wait, đã đạt trên baseline. |
| K04.02 | Một policy deny mặc định, actor từ session và account/member hiện hành; năm role và grants theo action; analyst delete luôn cần explicit read+delete. Native upload descendants cần cả quyền nguồn và project theo quyết định của Kiên. Review append-only/history và author CAS; logical delete giữ receipt/history. |
| K04.03 | Canonical native snapshot/result, claim/evidence/graph; scoped search/cursor/list trước LIMIT; metadata project riêng; export durable JSON/Markdown với nonce, creator và expiry. Wrong tenant/child và inaccessible dùng safe denial. Không còn giới hạn “upload chưa nối canonical” của báo cáo cũ. |
| K04.04 | Current authorization tại API, captured worker context, dispatch và fenced publication; kiểm lại sau object/render I/O và trước dữ liệu trả ra. Source-only revoke được kiểm ở read/review/export, follow-up và list delivery. Revoke không xóa terminal truth hoặc hứa thu hồi bytes đã gửi hợp lệ. |
| K04.05 | Trusted repository scope và composite FK; hai LOGIN role API/worker thật, explicit role/schema, nonowner và per-column grants; same physical connection tái sử dụng sau success/error/rollback/cancel. Không chọn RLS và không nhận chống raw SQL của principal bị chiếm quyền. |

Chi tiết requirement → code → test nằm trong `k04-resume-20261111/requirements/audit.md`. Ma trận hiện hành: [k04-role-action-scope.md](k04-role-action-scope.md). Vận hành: [k04-workspace-runbook.md](k04-workspace-runbook.md).

## Những sửa đổi của lượt hoàn thiện này

Hai lỗi đã có RED proof trên PostgreSQL riêng: (1) giữ project grant nhưng thu hồi source vẫn đọc được sáu đường native/export; (2) mất INSERT column privilege cần thiết nhưng readiness vẫn xanh. Sửa tại policy/repository/runtime hiện hữu, giữ nguyên HTTP DTO, migration/checksum và contract gốc.

Quyền native đi theo chính snapshot nguồn của record, không bắt người dùng có quyền mọi source của project. SQL lọc trước LIMIT; checkpoint của danh sách kiểm lại batch ID đã trả về. Bước chốt result/follow-up, review và export dùng cùng policy trên transaction đang commit. DB connection không giữ trong lúc render/object I/O hoặc gửi bytes. Project metadata và P3 records tiếp tục dùng scope riêng hiện hành.

Workspace readiness thêm kiểm ACL của các cột mà manager thực sự ghi và quyền cần cho row locks; managed clock dùng gate đó để session/capability/mutation trả 503 khi dependency mất quyền. Không tự GRANT, DDL hoặc ghi health row. Gate này không phải chứng nhận mọi write ACL của toàn bộ sản phẩm.

Hai test cũ được chỉnh theo hành vi hiện hành: corruption phải báo typed `CatalogIntegrityError`; download dùng handle được tạo bằng POST, thay URL deterministic tự dựng. Giữ các assertion isolation/revoke/range. Các fake caller được cập nhật theo interface thật; K03 giữ writer của test auth.

## Kiểm chứng và phiên bản

Baseline tiếp nhận: `k02-verification-20261111/production-freeze-v2.json`, 621 production files, runtime set `488b140c055dfd4d64cb0f0358f84e3eee32c736c63266859811b528245d46f3`; kiểm trước sửa không drift. Các lượt chồng nhau không cộng thành tổng test độc lập.

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


Mỗi lượt PG dùng container PostgreSQL16 riêng, image đã có sẵn, cổng loopback ngẫu nhiên, schema/role tổng hợp. Receipt xác nhận đúng ownership và dọn container/anonymous volume. Không dùng/reset DB hoặc IdP dùng chung; không in DSN/token/password. Source thay đổi trong lúc kiểm phải được nêu trong receipt, không gọi là stable.

## Bàn giao và phần nghiệm thu còn mở

Phần backend Kiên có thể tự thực hiện của K04.01–.05 đã được đối chiếu và kiểm theo phạm vi trên. Sáu dòng tracker K04 có đề xuất trạng thái/evidence chính xác cho writer CSV phối hợp, không ghi đè dòng task khác. Các fixture/schema hợp đồng dùng nguồn maintained hiện hữu; không tạo repository/contract hoặc UI song song. TXT cho Quân chỉ là bản nháp chưa gửi.

Quân còn actual hosted mount và browser hai account/hai workspace: cache/draft/toast/bfcache, chuyển context, reconnect/poll UX và wire variants P02/P13/P22/P23/P25. Joint J02/J05 cần cùng build/schema/config và owner acceptance. Bảo/owner còn production provisioning/privacy, staging origin/IdP/TLS/quota/budget/rollout và restore policy. Unit/ASGI/PG local không tự đóng các gate đó; không nhận 50k load, browser background hoặc distributed SLO từ kết quả local.

K03 auth recovery/revoke và J02 backend proof được bàn giao qua local checkpoint và hash của writer K03; báo cáo K04 không nhận đã chạy browser/IdP thay K03. Final manifest ghi rõ proof nào được tái sử dụng và source nào đã thay đổi.
