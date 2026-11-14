# J-01: handoff logout Keycloak — bản thử nghiệm

Trạng thái: đề xuất để Quân review; không phải contract đã chốt hoặc staging
được nghiệm thu. Code đang là WIP chưa commit, base checkout `2bbcdc2` không
đại diện cho toàn bộ nội dung đang chạy.

## Hành vi backend đang thử

1. Khi operator bật `ALETHEIA_HOSTED_IDP_LOGOUT=enabled`, client gửi
   `POST /v1/auth/logout` với body JSON `{}`, cookie phiên và `X-CSRF-Token`
   của session hiện tại. Endpoint kiểm Origin/CSRF, thu hồi session server và
   xóa cookie session/CSRF.
2. Nếu tạo được URL IdP, response là `200 {"logout_url":"https://…"}`. Client
   cần điều hướng trình duyệt đến URL đó; response 200 chỉ xác nhận logout
   local, chưa chứng minh Keycloak đã kết thúc phiên. Keycloak thử nghiệm yêu
   cầu người dùng xác nhận thêm trước khi redirect.
3. Nếu không lấy được URL, response vẫn `200 {"logout_url":null}` và session
   local đã được thu hồi. Không báo logout IdP thành công. Hiện chưa có route
   khôi phục URL sau khi session đã bị thu hồi; cần thống nhất UX/retry.
4. Keycloak phải được cấu hình cho `post_logout_redirect_uri` bằng origin app
   cộng `/#/signed-out`. Đây là URL cố định từ cấu hình server, không nhận URL
   tùy ý từ client. Màn signed-out và hành vi dẫn đến login tiếp chưa triển khai
   trong backend này; client cần điều phối sau khi người dùng xác nhận logout.
5. Khi chế độ logout IdP bật, `GET /v1/auth/start?mode=switch` bị từ chối
   `400`; luồng đề xuất là logout đầy đủ trước, sau đó login mới với
   `return_to=/`. Không dùng đường dẫn project/result của tài khoản cũ.
6. Chế độ IdP logout mặc định **disabled**; khi tắt, endpoint trả `204` sau
   logout local, không có bằng chứng Keycloak đã logout. Không bật cho realm
   dùng chung trước khi chủ môi trường/Bảo duyệt tác động đến ứng dụng khác.

## Bằng chứng và điểm còn mở

- HTTPS local + PostgreSQL + Keycloak 26.8.2, hai tài khoản tổng hợp: đăng nhập
  A → logout local → trang xác nhận Keycloak → xác nhận → redirect đúng
  `/#/signed-out` → login B với `return_to=/`; token session A cũ bị từ chối,
  context của B khác A. Chạy qua HTTPX, không phải browser. Script:
  `../j01-browser-two-20261110/check_idp_logout.py`.
- Realm thử nghiệm đăng ký wildcard `https://localhost:18446/*` để kiểm luồng;
  production/staging cần người quản trị đăng ký chính xác URI được chấp thuận.
- Có lần callback/login-start trả `503` trong test local; chạy lại từ đầu đạt.
  Nguyên nhân chưa được xác định; không coi luồng đã ổn định.
- Back-channel logout, UI nhiều tab/back-forward cache và job của A hoàn thành
  sau khi logout chưa được nghiệm thu. Code job thử nghiệm kiểm lại session
  trước publication; không hứa job vẫn hoàn tất sau logout.
- Không có bằng chứng realm staging có dùng chung hay Bảo đã duyệt logout IdP.
  Cần Quân review response/UX, xác nhận hai layout và cùng chạy A06/A08/A09/
  A26/A28 trên browser, cùng SHA/build/schema xác định, sau khi có staging.

## Phạm vi mã đã kiểm

Trên worktree chưa commit, SHA-256 tại lần kiểm:

- `hosted_auth/logout_http.py`: `94342681291a174ef67bcd2670222bb8f0bec9058996cda087ada5d34cb1173c`
- `hosted_auth/oidc_discovery.py`: `5dff8dd20c538836005631630dd43bed3aca73dfa21f709a20f3328613301c8a`
- `hosted_auth/start_http.py`: `2ad6e530d53fbe72b3fa9f298275e80e0ac69b334752de2a4266d782f84d308b`
- `hosted/upload_runtime.py`: `a2811f05d7b8eb4d2c7456d8cd00112a8bbd9320a5b18754e907a3e82791f704`

Các hash trên chỉ nhận diện từng file, không phải SHA release hoặc bằng chứng
producer/consumer trên cùng bản phát hành.
