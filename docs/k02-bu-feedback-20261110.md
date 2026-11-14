# K-02 — phản hồi Bu và các điểm cần chốt

## Updated handoff after local completion work

K02 has since verified the separated worker against local PostgreSQL and added
an outer HTTP execution/send deadline and checked job response schema/scope.
Latest verification: 912 related unit tests and a separate two-test local
PostgreSQL/HTTPS run passed. These are local checks, not Quân consumer or shared
acceptance. See `k02-runtime-config.md` for scopes and logs.

Current auth integration must be distinguished from the historical notes below:
`/v1/auth/start`, `/v1/auth/callback`, `/v1/auth/logout`, `/v1/auth/renew` and
`/v1/auth/logout-all` exist in the experimental runtime. Start/callback success
uses 303, return_to is still an exact literal-path allowlist without hash/query,
and callback failures are fixed errors rather than the proposed signed-out
redirect protocol. Logout is 204 when IdP logout is disabled; when explicitly
enabled it returns 200 with `logout_url` (possibly null after provider lookup
failure) after local revocation. That differs from Quân's proposed redirect_to
field and forced SSO logout. Changes remain under K03/J01 review, not assumed
accepted. First-owner/invite and hash-route return paths still require decisions.

Ask Quân for: v7 consumer result and pushed client SHA with J01_FIXTURES support;
acceptance or required differences for these route/redirect/logout/error choices;
review of the existing v8 terminal-job/receipt proposal; and Bảo's environment
owner/origin/test topology/schedule. No credential is needed in the response.
The v7 ZIP hash remains
`6ba4cfae231b374d277c34cd22d19ddcaf7491899d244cac452a8c237d4e55d1`.

Nguồn: nội dung Bu do Kiên cung cấp ngày 2026-11-10; chưa coi đề xuất là
contract được hai bên chấp nhận. Bu báo consumer v5: 17/17 test contract và
205/205 test tổng thể, chưa nhận v7. Đây là kết quả do Bu báo, không phải lần
chạy độc lập trong worktree này.

Gói `j01-producer-review-v7.zip` có SHA-256
`6ba4cfae231b374d277c34cd22d19ddcaf7491899d244cac452a8c237d4e55d1`.
Cả 22 file trong `fixtures/j01/producer-review-v7` khớp byte với file tương ứng
trong ZIP; ZIP có 50 file, nên kiểm 22 file không xác nhận toàn bộ nội dung ZIP.
Validator độc lập chạy lại với v7: 15 fixture đạt, kiểm thêm code/status,
ngày tháng, extension, total/total_kind, cursor rỗng và URL tuyệt đối.
v5, v6, v7 đều dùng `product-api/v1-draft` và cùng tên 22 file fixture/schema.
V6 bổ sung ràng buộc total/total_kind; v7 bổ sung cursor không rỗng và
status_url tương đối. Ngày hợp lệ cần validator bật FormatChecker; pattern
schema đơn thuần không chứng minh lịch đúng. So khớp status_url với workspace
và job cụ thể cần kiểm runtime; URL tương đối một mình chưa đủ.
Job success/result, job failure và receipt replay còn theo đề xuất v8 trong
`j01-job-v8-review-proposal.md`, chưa chốt và không được coi là đã sửa ở v7.

## Chưa thống nhất

- Bu đề xuất `/v1/auth/login`, `/v1/session/logout`; code thử nghiệm hiện dùng
  `/v1/auth/start`, `/v1/auth/logout`. Callback dùng `/v1/auth/callback`.
- Code hiện trả 303 cho start/callback thành công; callback lỗi trả status cố
  định 400/503/500, không redirect với reason như đề xuất. `return_to` hiện là
  allowlist đường dẫn chính xác, không nhận fragment hoặc query; đề xuất của Bu
  cho phép hash route. Phải thống nhất và kiểm redirect trước khi sửa K-03.
- Logout hiện xóa phiên Aletheia, cookie và trả 204; chưa trả Keycloak
  end-session URL. Chính sách IdP logout, token hint, redirect và account switch
  thuộc K-03 và phải được review; không đặt token hint vào JSON/browser khi
  chưa thống nhất rủi ro rò rỉ và chính sách token server-side.
- First-owner, invite, ba mã lỗi đề xuất và CSRF/response cần review K-03/K-04
  và J-01. Không tự xác nhận email hay provisioning policy. Cần làm rõ cách
  biểu diễn quyền tạo workspace khi chưa có workspace để đọc capabilities.
- Bu báo chưa có staging, chủ hạ tầng, sẽ hỏi Bảo. Không có origin, realm,
  lịch, tài khoản hay quyền public tunnel đã xác nhận. Đề xuất tối thiểu hai
  tài khoản và hai workspace với owner/viewer chỉ là dữ liệu synthetic dự kiến.
  Không cung cấp credential qua chat. Chạy ngoài máy cần hạ tầng/quyền đồng ý.
- Bu báo test CI nhận `J01_FIXTURES` đang ở máy Bu, chưa có commit/SHA. Chưa
  thể tuyên bố CI consumer dùng v7 đạt hoặc tự cấu hình workflow trỏ mã chưa có.

Chưa thay code/shared contract/J-01/K-03 theo các đề xuất trên; chưa commit,
push, gửi tin, khởi tạo staging hay mở backend ra mạng.
