# J-01 Session và Capabilities đề xuất

Trạng thái: **dự thảo để Kiên và Quân review** trên base `2bbcdc2a7637c840babd8b71910306c64072bc4b`. Các file `*-proposal.json` là đầu vào tổng hợp; fixture trong thư mục producer là output hoặc được kiểm khớp với serializer, không phải response API thực tế. Chúng không chứng minh consumer đã chấp nhận contract mới. Model producer thử nghiệm nằm ở `src/aletheia_lab/hosted_contract/session_capabilities.py`. Không thay đổi `p6-product-view/v1`. Xem `j01-review-decisions.md` để biết phản hồi gần nhất và trạng thái các quyết định.

## Session

Đề xuất `GET /v1/session` trả session của actor đã xác thực, `Cache-Control: no-store`. Fixture: `fixtures/j01/session-proposal.json`. Workspace đang mở lấy từ URL; `last_workspace_id` chỉ là gợi ý, không chứng minh quyền. `context_id` đổi khi đăng nhập hoặc đổi tài khoản, giữ khi renew thầm lặng. Cookie/credential IdP không có trong body; `csrf_token` trong fixture là giá trị giả, không dùng trong môi trường thật.

Client dự kiến gửi CSRF token qua `X-CSRF-Token` khi thực hiện mutation. Sai token trả `csrf_invalid`; client không tự gửi lại mutation. Cơ chế cookie, tạo và rotate token, kiểm Origin và error response cần được thiết kế và kiểm ở server trước khi sử dụng.

## Capabilities

Đề xuất `GET /v1/workspaces/{workspace_id}/capabilities` sau xác thực và kiểm membership trên server. Fixture: `fixtures/j01/capabilities-proposal.json`. `role` chỉ để hiển thị, `allowed_actions` là thông tin UI; server vẫn quyết định quyền cho từng request và đối tượng. Không coi giá trị fixture là cấu hình staging hay capability đã được triển khai.

Client dự kiến từ chối version không hỗ trợ, trường bắt buộc thiếu hoặc `workspace_id` sai scope; không tự fallback sang demo. Flag thiếu được xem là false, flag lạ bỏ qua. Cần chốt cách xử lý lỗi, giá trị null, giới hạn và cách thêm trường với Quân.

## Các quyết định chưa chốt

- Version fixture dùng `product-api/v1-draft` theo trao đổi với Quân; ví dụ job trong hợp đồng giao việc dùng `product-api/v2-proposal`. Cần thống nhất một version chung trước khi xem là contract chính thức.
- Giới hạn câu hỏi: local service hiện 2000 ký tự, proposal giao việc ghi 4000. Ví dụ dùng 2000 để review, chưa quyết định giới hạn hosted.
- Keycloak, quản lý invite, polling và staging vẫn là đề xuất cần xác nhận tài nguyên và vận hành. Không khẳng định revoke ≤5 giây khi chưa đo đủ API/download/job.
- `idp_account_url`, chính sách allowlist URL, scope header/context và membership revision cần được chốt; không đưa một URL IdP giả thành mặc định thực tế.
- Cần thêm schema và ví dụ Problem, job, paging, revision và permission matrix; bổ sung auth/onboarding, invite và list job cho hành trình cốt lõi.

## Kiểm chung trước nghiệm thu

Kiên tạo serializer và schema được version hóa từ implementation thực; Quân chạy validator/consumer với fixture sinh từ serializer đó và các case sai version, thiếu trường, sai scope, revoked và unavailable. J-01 còn cần hai layout, thống nhất base/ownership, staging login → dữ liệu được bảo vệ → source → result, cùng tài nguyên và SLO theo bảng nghiệm thu. Chưa có các bằng chứng đó thì J-01 vẫn mở.

## Kết quả kiểm local và Problem đề xuất

- Trên Windows/Python 3.12.15, ba test Session/Capabilities đạt theo output Kiên gửi. Bốn file trong `fixtures/j01/producer-draft` khớp byte khi tính lại từ model hiện tại; nguồn tạo ban đầu của các file đó chưa được xác định. Không coi kết quả này là consumer check hay endpoint thật.
- Model `Problem` có ví dụ dự thảo cho 401 `session_expired`/`session_revoked` và 403 `membership_revoked`/`permission_denied`/`csrf_invalid`. Adapter HTTP read-only K-02 dùng model này, nhưng chưa có backend identity/membership thật; model không tự bảo đảm lỗi thực tế an toàn.
- Sau khi bổ sung mapping Problem, Kiên chạy 9 test thành công và Ruff không báo lỗi trên Windows/Python 3.12.15. Khi model/schema thay đổi, phải tạo lại và kiểm artifact bị ảnh hưởng, không tự ghi đè thư mục có sẵn. Kiểm tra validator status/code trong runtime; JSON Schema sinh từ Pydantic không tự thể hiện ràng buộc validator Python này.
