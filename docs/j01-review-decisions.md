# J-01 — phản hồi review và hợp đồng làm việc dự thảo

Base API được kiểm: `2bbcdc2a7637c840babd8b71910306c64072bc4b`, worktree `feat/product-api`. Nội dung này là **đề xuất của phía Kiên**, chưa thay thế quyết định chung hoặc chứng minh staging. Khi có mâu thuẫn, phần dưới cập nhật các câu hỏi mở trong các draft cũ; không sửa hợp đồng giao việc gốc.

## Problem và bảo vệ scope

- Đề xuất Quân hỗ trợ `application/problem+json` với `code`, `detail`; chỉ tin mã đã biết khi HTTP status, trường `status` và bảng mã khớp. Mã lạ hoặc sai cặp phải xử lý lỗi chung, fail closed; không suy diễn revoke thành expired hay permission_denied. Định dạng local `{code,safe_message}` vẫn cần xử lý riêng ở bridge hiện hữu, không chuyển đổi thầm lặng.
- Mã mới snake_case chữ thường, bắt đầu bằng chữ, tối đa 64 ký tự: `^[a-z][a-z0-9_]*$`. Chặt hơn regex gợi ý của Quân vốn cũng cho phép dấu chấm và gạch ngang. `detail` là thông điệp an toàn, tối đa 1000 ký tự; client có thể giới hạn hiển thị 256 mà không cắt mã. Không đưa bí mật, stack trace hay dữ liệu ngoài quyền vào detail.
- `session_expired`, `session_revoked`: 401; `membership_revoked`, `permission_denied`, `csrf_invalid`: 403. Đề xuất object không tồn tại và object ngoài quyền cùng 404 `not_found` với nội dung và metadata không tiết lộ tồn tại. `membership_revoked` dùng ở ngữ cảnh workspace đã xác thực theo chính sách được phép; không dùng như oracle thăm dò object.
- Bảng mã producer và JSON Schema biểu diễn các cặp đã biết; consumer vẫn phải đối chiếu thêm với HTTP status thực tế. Schema consumer cho phép extension không biết; producer vẫn kiểm chặt. Kiểm fixture không chứng minh hành vi HTTP staging.

## Session, capabilities và version

- Draft thống nhất `product-api/v1-draft`, đề xuất khi hai bên chốt phát hành mới đổi `product-api/v1`. `p6-product-view/v1` giữ nguyên. Version ví dụ cũ trong tài liệu giao việc không phải bằng chứng đã thay đổi version triển khai.
- Lấy CSRF từ `GET /v1/session`, gửi `X-CSRF-Token` khi mutation; sai token không tự retry. Cơ chế cookie/Origin, rotate token và đăng nhập lại chưa được triển khai/kiểm thực tế. `context_id` đổi khi đăng nhập/đổi account, giữ khi renew; `Cache-Control: no-store` cho response riêng tư. Login start/callback, logout, return-to và `idp_account_url` cần thiết kế cùng IdP và allowlist, chưa có URL thực.
- Đề xuất `allowed_actions` dùng từ vựng UI `import`, `refresh`, `analyze`, `follow_up`, `export`, `delete`. Chúng chỉ là gợi ý theo workspace/ngữ cảnh, không chứng minh quyền trên object; server phải kiểm lại từng request và object. Chưa áp enum đóng cho actions để thống nhất khả năng mở rộng với Quân.
- `supported_export_formats`: `json`, `markdown`, `pdf`. Theo phản hồi Quân, dùng `analysis.counterfactual` và `analysis.prospective`; hosted không đọc tên cũ `counterfactual`, `prospective`. Feature thiếu được xem là tắt, không fallback sang demo.
- Đề xuất hosted `limits.question_chars=2000`, khớp local và đề xuất Quân, thay cho phương án 4000 trong ví dụ giao việc; cần người phụ trách yêu cầu chấp nhận thay đổi. Các limit còn lại trong fixture chỉ là ví dụ.
- Input tổng hợp `*-proposal.json` được giữ để tái tạo; file consumer nên dùng là output serializer và schema đi kèm. Các bản producer cũ được giữ như WIP/bằng chứng lịch sử; không coi mọi bản cũ còn đồng bộ model mới.

## Job, paging và revision

Các điểm sau là đề xuất cụ thể để hai bên review, **chưa có endpoint, storage bền hay phép thử lost ACK**:

- Dùng header `Idempotency-Key` trên các operation hỗ trợ, key ngẫu nhiên tối đa 128 ký tự; phạm vi actor + workspace + operation + key và fingerprint request chuẩn hóa. Ghi key, fingerprint và receipt cùng transaction bền trước 202. Cùng key/cùng fingerprint trả cùng receipt; payload khác trả 409 `idempotency_conflict`. Đề xuất giữ tối thiểu 24 giờ từ lúc nhận, nhưng cần chốt thời gian khôi phục thực tế, quota và clock; không retry sau hạn như thể vẫn được bảo vệ. Feature `jobs.idempotency` chỉ bật khi backend thực sự bảo đảm idempotency bền. Client chỉ retry cùng key khi được hỗ trợ; local RPC hiện tại không có bảo đảm này.
- Trạng thái kết thúc: `succeeded`, `failed`, `cancelled`, `expired`; `expired` phải có policy retention, không ngụ ý job đã hủy trước khi hết hạn. Progress không biết là null. Retry/cancel và kết quả tranh chấp phải dựa trạng thái thực của server.
- Cursor opaque gắn actor/scope, filter, sort, snapshot và thời hạn. Đề xuất `400 cursor_invalid` cho cursor sai định dạng, scope hoặc query; `410 cursor_expired` cho cursor hết hạn hợp lệ. Không trả chi tiết scope hay object bị che. Chưa xác nhận chính sách HTTP với Quân.
- Revision dự thảo dùng `If-Match` với ETag strong, server thực hiện CAS nguyên tử. Thiếu precondition: 428 `precondition_required`; stale: 412 `revision_conflict`, client giữ bản nháp. Không khẳng định đã có endpoint hay CAS.

## Phạm vi đã biết và việc còn mở

- Quân báo đã chạy consumer 5 fixture cũ và phát hiện client chưa đọc Problem; kết quả do Quân báo, chưa được chạy độc lập ở worktree này. Quân cũng báo một bản ghép UI với main đã đạt 152 test/build/43 pytest; cần SHA/ref hoặc patch của bản đã ghép để tái lập, không đồng nhất với nhánh 7173649 đã kiểm trước đó.
- Quân cần review fixture/schema mới và chạy consumer hai chiều; hai layout, base chung và quyền sửa file phải được hai người xác nhận. Chưa có bằng chứng slice staging login → protected data → source → result dùng identity và storage thật do cả hai chạy.
- Workload 50.000 principal, khoảng 833 request/s khi 50.000 tab hiển thị đều kiểm mỗi 60 giây chỉ là giả định. Client báo có jitter, phối hợp tab và tính request hoạt động; cần journey mix/waterfall từ Quân, tài nguyên/cost/quyền thử và số đo thực trước nghiệm thu.
- Server revoke ≤5 giây, download/range, publication của job và client tab yên 60 giây cần đo riêng; hiện chưa có bằng chứng đạt. PDF tiếng Việt, K-10, định dạng step/locale của phân tích là việc riêng cần theo dõi; review J-01 không chứng minh chúng đã sửa.
