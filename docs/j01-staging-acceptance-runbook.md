# J-01 — kế hoạch chạy chung staging

Trạng thái: chuẩn bị nghiệm thu, chưa thực thi. Chủ backend: Kiên;
consumer/layout: Quân; quyền hạ tầng và budget: chủ môi trường do Kiên xác định.
Đã có HTTPS/Keycloak/PostgreSQL local riêng để kiểm backend; chưa có staging chung được xác nhận để Quân chạy browser. Kết quả hiện tại xem `j01-current-review-20261110.md`; không coi các vòng local là lần nghiệm thu chung.

## Điều kiện bắt đầu

1. Ghi SHA backend, SHA client, trạng thái WIP, hash schema/fixture/build.
   Hai người xác nhận cùng version và permission matrix. Không dùng bản
   local đã sửa khác mà chưa ghi hash làm release identity.
2. Có HTTPS origin của app và cấu hình IdP thật: issuer, discovery/JWKS,
   client ID, redirect URI đã đăng ký và cơ chế giữ secret phía server.
   Không gửi secret trong báo cáo hay fixture. Người quản trị xác nhận
   lifecycle login/logout, test accounts và quyền tạo workspace/onboarding.
3. Có DB/object storage thật, cấu hình migration/backup, namespace thử riêng,
   quyền upload và thư mục dữ liệu tổng hợp. Nhận quyền trước khi dùng
   tài nguyên có phí; không seed một admin chung để thay hành trình người dùng.
4. Có runtime API/worker, health/readiness và log request_id an toàn.
   Không dùng backend synthetic trong unit test làm identity staging.
5. Chốt 2000/4000 ký tự với người giao việc; chốt các lựa chọn invite,
   polling/SSE và budget. Thiếu đầu vào nào ghi blocker có owner,
   không bật capability trước khi chức năng thật hoạt động.

## Danh mục route cần bổ sung cho slice

Đây là các ranh giới cần kiểm; login/session/capabilities và slice ZIP/jobs đã có runtime local. Onboarding và các route gốc của bảng 38 operation chưa triển khai hết. Dùng `j01-operation-disposition.csv` để đối chiếu, không coi bảng này là OpenAPI hoàn chỉnh:

| Route | Ngữ nghĩa cần kiểm |
|---|---|
| GET /v1/auth/start | Bắt đầu login; return_to chỉ tới đường dẫn app được cho phép, không open redirect |
| GET /v1/auth/callback | Đổi code qua IdP phía server; kiểm state/issuer/audience/nonce theo thư viện auth được chọn |
| POST /v1/auth/logout | CSRF + hủy server session; thông báo client và các tab; không chỉ xóa cookie |
| GET /v1/session | Session model hiện có; private no-store; phân biệt expired/revoked |
| GET /v1/workspaces/{workspace_id}/capabilities | Membership hiện tại, đúng scope, không tin role do client gửi |
| Routes onboarding/workspace | First owner hợp lệ, người dùng mới vào được workspace, không chỉ seed tay |
| Upload/preview/confirm → snapshot | Hosted input không phải filesystem path; snapshot bền và kiểm được hash |
| Submit analysis → receipt → result | 202 sau nhận bền; scope/session được kiểm trước publication |

Chọn thư viện auth được duy trì, cấu hình theo IdP; không tự triển khai
crypto hoặc bỏ xác thực để làm smoke test. Account URL chỉ được xuất từ
cấu hình IdP đã allowlist; chưa có URL thật thì không giả fixture thành cấu hình.

## Khởi chạy slice có kiểm soát

Các lệnh dưới dùng PowerShell trong checkout API, với Python đã cài dependency
hosted và cấu hình do người vận hành cấp. Không tự sinh secret thay cấu hình
staging; không ghi secret vào command history hoặc artifact gửi review.
`$J01Python`, `$J01Config`, `$J01Auth`, `$J01Cert`, `$J01Key` là đường dẫn đã
được người vận hành xác nhận, không phải các chuỗi placeholder để chạy nguyên.

1. Kiểm release/hash, các biến database, login-proof, CSRF và admission; kiểm
   TLS/issuer/redirect URI, private storage root và schema/provisioning.
   `admission_migrate_cli` chỉ quản lý admission, không cài bốn schema J01.
   Các file `docs/j01-experimental-*-schema.sql` vẫn chỉ dành thử nghiệm local;
   không chạy lên database chung để thay một migration đã review.
2. Kiểm cấu hình offline (không chứng minh IdP/DB/worker sẵn sàng):

   ```powershell
   & $J01Python -B -m aletheia_lab.hosted check-config --config $J01Config
   if ($LASTEXITCODE -ne 0) { throw 'Cấu hình chưa hợp lệ.' }
   ```

3. Mở API với TLS, giữ terminal hoặc dùng supervisor của môi trường:

   ```powershell
   & $J01Python -B -m aletheia_lab.hosted.server `
       --config $J01Config --auth-policy $J01Auth `
       --tls-cert $J01Cert --tls-key $J01Key
   ```

4. Trong process riêng, với cùng config/auth và DB namespace được xác nhận,
   khởi chạy worker. API không tự chạy scheduler sau khi tách K02:

   ```powershell
   $env:ALETHEIA_HOSTED_CONFIG = $J01Config
   $env:ALETHEIA_HOSTED_AUTH_POLICY = $J01Auth
   & $J01Python -B -m aletheia_lab.hosted.managed_worker
   ```

5. Kiểm `/health/ready` qua origin/TLS đúng. Đây là readiness phụ thuộc API,
   không chứng minh có worker đang sống. Kiểm cả exit/restart của worker
   bằng supervisor và job smoke được cấp quyền, không suy ra từ HTTP 200.
6. Chạy S01–S05 với dữ liệu tổng hợp, kiểm receipt, nguồn/snapshot/result ID,
   hash và đọc lại persistence. Khi chưa có hosted UI, HTTPX chỉ là bằng
   chứng backend local. Chỉ nghiệm thu khi Kiên và Quân chạy cùng gói/version
   bằng browser, ghi đủ joint_run_id và kết quả hai bên.

## Kịch bản và bằng chứng cần giữ

| Case | Thao tác | Quan sát bắt buộc |
|---|---|---|
| S01 | Login bằng browser, gọi session | context_id/expires_at đúng, request_id; không ghi credential |
| S02 | Chọn workspace rồi đọc capabilities | UI và API cùng scope, quyền từ server |
| S03 | Upload nguồn tổng hợp → preview → confirm | payload và hash, snapshot ID; kiểm DB/object tương ứng |
| S04 | Analyze → job → result | receipt bền, trạng thái thực, result đọc lại từ persistence |
| S05 | Reload và mở result bằng URL | Cùng snapshot/result, không thay bằng demo |
| S06 | Đổi ID sang object ngoài quyền | 404 không lộ tồn tại; không có dữ liệu workspace khác |
| S07 | Session expired/revoked, membership revoked | API và UI xử lý đúng mã; giữ/xóa draft theo policy |
| S08 | Logout rồi login account khác | context đổi, nội dung cũ không quay lại sau response muộn |
| S09 | Restart API rồi đọc lại result | Dữ liệu còn, quyền vẫn được kiểm; ghi giới hạn restart scope |

S01–S05 mô tả slice J-01. S06–S09 là smoke bổ sung và chuẩn bị J-02–J-08,
không tự bổ sung chúng thành điều kiện đóng J-01 thay cho đề giao việc.
Kiên lưu request/response đã redacted và đối chiếu persistence; Quân lưu
browser outcome trên cùng IDs. Hai người ghi joint_run_id, release identities,
expected/observed và ký chấp nhận cùng một lần chạy. Test local bridge với
nguồn tổng hợp có giá trị baseline nhưng không thay S01–S09 hosted.

## Layout và điều kiện đóng

Quân cung cấp hai layout theo đề, ghi tên/phiên bản hoặc link và trạng thái
review của Kiên. Chưa nhận diện đủ artifact thì không tự ghi approved.
Đóng J-01 sau khi: base/ownership được thống nhất; contract producer/consumer
hai chiều đạt; hai layout được xác nhận; slice S01–S05 được hai người chạy và
chấp nhận; workload,
SLO, tài nguyên, budget và quyền thử được chấp nhận. Nếu chưa đủ, báo đúng
phần nào đang thực hiện/blocked và giữ nguyên tracker chưa passed.
