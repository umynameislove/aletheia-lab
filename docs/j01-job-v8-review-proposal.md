# Đề xuất job/page tiếp theo — cần Quân review trước khi đổi shape

Nguồn: phản hồi Bu về v5. Chưa có thay đổi payload v7, HTTP route hoặc
producer job bền. Các trường bên dưới là đề xuất, không tự coi là đã chốt.

1. **Receipt:** giữ receipt ban đầu `queued` là ảnh lúc server chấp nhận bền;
   khi retry cùng key trả receipt ban đầu và client GET status_url để biết
   trạng thái hiện tại. Nếu Quân cần receipt trả trạng thái hiện tại, cần đổi
   quy tắc «cùng receipt» và định nghĩa phần nào bất biến trước khi sửa.
2. **Job:** bổ sung `operation` (import/refresh/analysis/export), `created_at`,
   `updated_at`, `request_id`, cùng kết quả hoặc failure có cấu trúc.
   Result phải là tham chiếu scoped theo loại operation: export có thể tạo
   artifact thay vì result; không ép mọi success có result_id.
3. **Failure:** chỉ trả mã/detail an toàn được ánh xạ bởi server, không trả
   traceback hoặc nguồn ngoài quyền. Cần quyết định shape và bảng code trước.
4. **Progress:** null là chưa biết; giá trị số 0..1. Không buộc succeeded = 1,
   cũng không suy result tồn tại từ progress.
5. **Page:** envelope chung nhưng item cần schema riêng theo project/result/
   claim/job; ví dụ dict chuỗi trong v7 chỉ là fixture, không là schema chung
   cho các item thật. Cursor opaque, giới hạn theo capabilities và scope.
6. **Versioning:** gói dự thảo đổi shape phải được gửi Quân trước khi áp dụng,
   có fixture cho từng terminal/operation và negative scope/version. Giữ v7.

Đã thống nhất ở mức đề xuất: 412 revision_conflict khi If-Match stale,
428 khi thiếu precondition, và 409 idempotency_conflict. Không đánh đồng
status local bridge với hosted.

## Inventory lỗi

`fixtures/j01/local-error-inventory-v1.json` là danh sách tĩnh các lời gọi
literal ProductError/BridgeError từ source local; có danh sách lời gọi dynamic
chưa phân giải. Local bridge hiện mặc định 422 cho ProductError, nhưng hosted
chưa có route/mapping tương ứng. Cần đối chiếu từng operation, actor/scope và
chính sách không lộ dữ liệu trước khi ban hành bảng hosted; không sao chép 422
cho mọi lỗi. Các mã `preview_blocked`, `mapping_invalid` có trong source
local; trạng thái HTTP hosted và availability chưa được triển khai/kiểm.
