# K-03/J-02 — đề xuất policy recovery/MFA cần review

Ngày 2026-11-10. Kiên đã duyệt **fixture local giữ OTP cũ khi reset password;
mất factor chờ policy riêng**. Luồng real IdP/SMTP/Authlib/PG đã đạt theo
[k03-recovery-mfa-verification-20261110.md](k03-recovery-mfa-verification-20261110.md).
Chưa chốt IdP/policy production, trusted event bridge hoặc nghiệm thu hosted.
Keycloak 26.8.2 chỉ là provider local để kiểm; chưa phải lựa chọn production.
Tài liệu này không thay cấu hình realm, source, schema hoặc tracker hiện có.

## Hành vi provider đã kiểm nguồn

Flow mặc định có sub-flow `Reset - Conditional OTP` và execution `reset-otp`.
Nguồn đúng tag 26.8.2: [DefaultAuthenticationFlows.java](https://raw.githubusercontent.com/keycloak/keycloak/26.8.2/server-spi-private/src/main/java/org/keycloak/models/utils/DefaultAuthenticationFlows.java).

Trong 26.8.2, `reset-otp` luôn yêu cầu `CONFIGURE_TOTP`. Default `Remove none`
giữ credential cũ nhưng vẫn cho cấu hình OTP mới; các lựa chọn khác có thể xóa
một/tất cả credential. Vì thế chỉ kiểm credential cũ còn tồn tại chưa chứng minh
factor ban đầu vẫn bắt buộc. [ResetOTP.java](https://raw.githubusercontent.com/keycloak/keycloak/26.8.2/services/src/main/java/org/keycloak/authentication/authenticators/resetcred/ResetOTP.java).

`Send Reset Email` mặc định `force-login=only-federated`: user nội bộ dùng cùng
authentication session có thể được đăng nhập sau reset. `force-login=true` yêu
cầu đăng nhập lại. Nguồn đúng 26.8.2: [ResetCredentialEmail.java](https://raw.githubusercontent.com/keycloak/keycloak/26.8.2/services/src/main/java/org/keycloak/authentication/authenticators/resetcred/ResetCredentialEmail.java).
[Hướng dẫn admin 26.5.7](https://www.keycloak.org/docs/26.5.7/server_admin/index.html)
mô tả điều chỉnh hai hành vi này; source tag 26.8.2 ở trên là căn cứ phiên bản.

## Quyết định cần IdP owner/operator chốt

| Lựa chọn | Policy phải xác định |
| --- | --- |
| Reset password, vẫn giữ factor đã đăng ký | Đề xuất cho fixture bảo thủ: copy Reset Credentials flow, Disable sub-flow Reset - Conditional OTP; cấu hình Send Reset Email force-login=true; browser flow bắt buộc password + factor hiện có trước khi phát code. Người mất factor dùng quy trình riêng. |
| Recovery khi mất factor | Chốt bằng chứng danh tính/fallback, người có quyền duyệt, cách thu hồi factor cũ và tái đăng ký; email reset đơn thuần không mặc nhiên đủ quyền thay factor. Kiểm từng đường fallback. |

Chọn một phương án và ghi version/export/hash realm/client/flow được duyệt.
Không suy MFA từ nhãn `acr="1"`, `amr` hoặc `auth_time`. Owner phải định nghĩa
mapping ACR tới flow/factor thật; K-03 chỉ kiểm đúng signed claim được yêu cầu.
Khi bật/đổi required_acr, invalidate session epochs và pending intents cũ; session
hiện không lưu assurance để kiểm lại retroactively. Không đổi policy đồng thời
giữa các API worker. Ứng dụng chỉ chuyển tới UI IdP, không nhận password/MFA/reset token.

## Hợp đồng revocation cần K-04/K-05

IdP password reset, disable hoặc kết thúc SSO chưa tự chứng minh app session bị revoke.
K-04/IdP owner chọn trusted event/admin mechanism; K-05 bảo đảm transaction chung
đổi binding/enabled, tăng account_epoch và đặt revocation watermark dùng clock đã chốt.
Writer phải serialize với begin/commit/renew/logout theo cùng policy của store.
Không tin browser header/event làm lệnh revoke. Sau reenable không phục hồi epoch cũ.
K-03 đã có test fence; SQL writer trong harness chỉ là fixture, chưa phải event bridge.

## Ca kiểm cần chạy trên môi trường được duyệt

| Ca | Bằng chứng cần có |
| --- | --- |
| Bình thường | Login bằng password + factor, recovery trên đúng origin, đổi password, fresh login lại vẫn cần factor cũ; subject không đổi; Authlib xác minh issuer/audience/nonce/ACR. |
| Thiếu/sai factor | Password mới nhưng thiếu OTP hoặc OTP sai không phát code/session; email reset không dẫn tới enroll factor ngoài policy. |
| Hết hạn | Link recovery thật hết hạn bằng clock thật bị từ chối; không đổi credential hoặc cấp session; không sửa chữ ký/token để giả expiry. |
| Replay | Link đã dùng không được đổi password lần hai hoặc tạo thêm session; thử cùng và khác browser context. |
| Sai identity/disabled | Link thuộc A trong context B không cấp A/B ngoài policy; account disabled vẫn bị từ chối; không tự mở lại binding. |
| Subject mới/email cũ | Không liên kết account/membership/owner theo email; cần provisioning rõ ràng. |
| Session cũ và race | Epoch/watermark thu hồi session/pending login/renewal cũ; đo API, job publication, poll và mọi tab ≤5 giây theo policy; account khác không bị ảnh hưởng. |
| Điều tra lỗi | Không log URL query/reset link, cookie, password, OTP/seed, provider body hoặc raw PII; record chỉ giữ outcome/version/timing đã loại secret. |

## Checkpoint trước fixture mới — lịch sử

Ở checkpoint trước, realm local chỉ đủ fixture password A/B, chưa có recovery/SMTP/MFA flow
được duyệt. Smoke A→B và signed ACR synthetic không chứng minh các ca trên.
Đã thấy cached Keycloak 26.8.2; chưa có cached mail capture image hay thư viện SMTP
server. Tạo TLS container riêng + SMTP capture + HTML/reset/OTP driver an toàn
ước tính 430–550 dòng, trước cả trusted app-epoch bridge; không thêm harness đó
khi chưa có policy và mail fixture phù hợp. Không sửa realm/container J-01.

Sau khi owner chốt policy: dùng container/realm UUID riêng, HTTPS kiểm certificate,
SMTP sink local chỉ nhận địa chỉ tổng hợp; chạy các ca trên, dọn đúng tài nguyên
đã tạo, bàn giao version/hash/log an toàn cho J-01/Quân. Cần kiểm browser hosted
và chấp nhận chung để đóng A23/J-02.04; admin reset đơn thuần chưa đủ bằng chứng.
