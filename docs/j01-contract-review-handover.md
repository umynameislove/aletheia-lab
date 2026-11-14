# J-01 Bàn giao draft Session Capabilities và Problem

> Vòng tiếp theo: `fixtures/j01/producer-review-v6/` được xuất từ
> `scripts/export_j01_review.py`. Có schema producer chặt và schema consumer
> cho phép extension, cùng ví dụ mã cursor, idempotency, revision, job và page. Các schema
> kiểm cặp code/status trong body; client vẫn phải đối chiếu HTTP status thật.
> Pattern date-time chỉ kiểm hình dạng, cần kiểm ngày lịch và timezone thực tế.
> Các fixture là dự thảo tổng hợp; chưa phải response staging hay xác nhận của Quân.
> Theo dõi tình trạng nghiệm thu tại `j01-acceptance-evidence.md`.
> V6 bổ sung ràng buộc total/total_kind vào Page schema và đã kiểm bằng
> jsonschema 4.25.1 + FormatChecker. Gói v5 đã gửi trước vẫn được giữ nguyên.

> Cập nhật review: dùng `docs/j01-review-decisions.md` và
> `fixtures/j01/producer-review-v2/` cho vòng tiếp theo. Các mục bên dưới
> mô tả vòng bàn giao cũ; không coi schema ở các thư mục cũ là schema mới nhất.
> Quân báo đã chạy consumer với năm Problem cũ và phát hiện client chưa nhận
> đúng shape; đây là phản hồi từ Quân, chưa phải kết quả client sau khi sửa.

## Tình trạng

Base API: `2bbcdc2a7637c840babd8b71910306c64072bc4b`; nhánh local `feat/product-api`; phần contract này chưa commit hoặc push. Consumer được đối chiếu tại `7173649f5229a26ed95b97075cd51340d0f13c99`; chưa ghép hai nhánh và chưa chạy consumer với các fixture này. Tất cả artifact dưới đây dùng dữ liệu tổng hợp, version **`product-api/v1-draft`**, cần Quân review trước khi chốt. Không đổi `p6-product-view/v1`.

## Artifact gửi review

- `fixtures/j01/producer-draft/session.json` và `session.schema.json`; `capabilities.json` và `capabilities.schema.json`. Bốn file đã được so byte với kết quả serialize/schema của model hiện tại; nguồn tạo ban đầu chưa xác định.
- `fixtures/j01/problem-producer-draft/`: năm fixture Problem và `problem.schema.json` được xuất qua model theo output Kiên chạy.
- `src/aletheia_lab/hosted_contract/session_capabilities.py`: model producer thử nghiệm. `docs/j01-session-capabilities-proposal.md`: ý nghĩa và câu hỏi còn mở. `docs/j01-permission-matrix-proposal.md`: ma trận quyền dự thảo từ gói giao việc.
- `tests/unit/test_j01_session_capabilities_contract.py`: Kiên chạy 9/9 pass và Ruff pass trên Windows, Python 3.12.15. Chưa chạy consumer, HTTP hoặc kiểm nghiệm bảo mật/session.

## Các điểm Quân cần kiểm

1. Các trường required, null, version và scope của Session/Capabilities có đủ cho Q-04/Q-05; `last_workspace_id` chỉ là gợi ý, `context_id` xác định lần đăng nhập.
2. Client từ chối wrong version, missing required field, workspace mismatch; unknown feature không bật capability, failure không fallback demo. Server vẫn phải xác thực và phân quyền từng yêu cầu.
3. CSRF token trong fixture chỉ là chuỗi tổng hợp. Header `X-CSRF-Token`, cookie và response `csrf_invalid` là đề xuất; không gửi credential thật hoặc thử vào deployment không được phép.
4. Phân biệt 401 `session_expired`/`session_revoked` và 403 `membership_revoked`/`permission_denied`/`csrf_invalid`; thảo luận chính sách 404 và không tự retry mutation. JSON Schema Problem hiện không biểu diễn validator status/code trong Python, nên cần kiểm quy tắc đó riêng; chưa chốt compatibility policy.
5. Mức `question_chars=2000` và các limit trong fixture chỉ là ví dụ. Contract giao việc có 4000; cần hai bên chốt với owner. Không coi các flag `false`, danh sách trống hoặc mode `hosted` trong fixture là cấu hình staging thực tế.
6. Hai phiên bản UI 56eefe4 và 7173649 có tree giống nhau theo output Git; nhánh 7173649 khác main 2bbcdc2 và chưa chứa RPC metric mới trong consumer. Cần tích hợp có kiểm soát, không thay toàn bộ phần frontend hay domain.

## Chưa nghiệm thu J-01

Chưa có contract version và permission matrix được hai bên chốt, fixture consumer được Quân chạy, hai layout cùng xác nhận, staging login → protected data → source/result thật, hoặc kế hoạch tài nguyên/SLO/chi phí được duyệt. Test model và fixture này không chứng minh auth, CSRF, revoke ≤5 giây, khả năng tải hay API đã được deploy.
