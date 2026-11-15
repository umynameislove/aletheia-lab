# Kết quả bổ sung K03 — phía Kiên

**Kết luận: phần kỹ thuật cá nhân K03 đạt trong staging cô lập thực chạy trên máy Kiên.** Không đổi tracker gốc thành nghiệm thu staging chung hoặc nghiệm thu liên bên. Đây là báo cáo K03 cho Kiên, chưa phải hồ sơ tổng K01–K18 cho Bảo.

## Thay đổi và lỗi đã sửa

URL post-logout cũ là `origin/#/signed-out`. Keycloak 26.8.2 từ chối đăng ký chính xác URL có fragment (lượt 15: HTTP400 `invalid_input`). Sửa duy nhất cách tạo URL đó thành `origin/`; đăng ký chính xác mới trả HTTP204, không dùng wildcard. Hồi quy trước sửa: **1 failed/3,65s**; sau sửa và kiểm các caller liên quan: **8 passed/3,91s**. Không sửa UI, migration, thời hạn phiên hoặc tăng giới hạn/timeout.

Đối chiếu [bộ kiểm URL của Keycloak 26.8.2](https://raw.githubusercontent.com/keycloak/keycloak/26.8.2/services/src/main/java/org/keycloak/validation/DefaultClientValidationProvider.java). Cập nhật hướng dẫn đăng ký post-logout tại `docs/j01-logout-handover-draft.md`; cơ chế logout local, CSRF, callback denial và IdP pinning giữ nguyên.

## Phạm vi đạt theo từng mục

| Mục | Kết quả và phạm vi |
|---|---|
| K03.01 | Giữ kết quả OIDC/callback đã đạt; lượt mới xác nhận callback thật, replay và return-to. |
| K03.02 | Reset email thật, expired/replay bị chặn, mật khẩu cũ bị chặn, OTP cũ giữ nguyên/bắt buộc; disabled IdP account không login được. Policy ở đây là retained-factor. |
| K03.03 | Cookie thật, logout/revoke, không ghi secret phiên vào storage; early renew không rotate; natural-time rotation chỉ một winner. Thời hạn 900/28800 là cấu hình, không tuyên bố soak 8 giờ. |
| K03.04 | Giữ phần CSRF đã đạt; phép tích hợp thiếu CSRF trả 403 mà không logout phiên. |
| K03.05 | Chrome hai tab thật; logout-all xóa A và trả API401 trong 5 giây; logout IdP thật rồi đăng nhập B, không còn workspace A. Không thay BFCache/joint acceptance. |
| K03.06 | Pause/resume IdP thật, không cấp phiên khi unavailable, đăng nhập mới phục hồi; quota 35 auth-start tuần tự và refresh race 12 đồng thời đạt. |

## Cách đọc số liệu

- Baseline Git `5ac1c54d90ce1ee507e4cdce3b0f8310b49505cf` cộng `source.patch`; runtime release identity `e04ff2ad03f40f5021453fc356ad25ad2eec22f8f0fac1ab0085a1ba935cf081` giống nhau ở hai slice được dùng.
- Hai slice so 1416 input chung ngoài driver K03: **không drift**. Mỗi slice có manifest trước/sau bằng nhau; raw byte hash Windows và Git LF có thể khác EOL, được ràng buộc thêm qua patch/Git và CI trên SHA đã push.
- **21 scenario Python khác nhau**, trong đó một scenario chứa **7 ca Chrome**. Không cộng thành 28 test độc lập. Tám test pytest cũng chồng lấn với CI, không cộng dồn với full suite.
- Lượt 16 hoàn tất 20 scenario nhưng dừng ở assertion bài quota: **toàn lượt vẫn FAILED**. Chỉ nhận các scenario đã hoàn tất cùng input không đổi. Lượt 17 dùng `--auth-abuse-only`: **5/5 PASS**, gồm quota còn thiếu và 4 preflight trùng; không chạy lại phần đã đạt.
- Record early-renew của lượt 16 ghi `status: 200` do khóa metadata trùng tên verdict. Assertion HTTP200/no-rotation đã qua; CSV tổng ghi verdict pass kèm `record_status_original=200`. Raw log giữ nguyên, driver đã đổi tên metadata thành `http_status`.
- Thời gian MFA bao gồm chờ bước TOTP thật; thời gian rotation bao gồm chờ 300,344 giây. Không dùng các thời gian đó làm latency API hoặc RPS tối đa.

Phương pháp, giới hạn process/pool/CPU/RAM, TLS, IdP/MFA, policy/session/budget và cách chạy lại: [PHUONG-PHAP-K03.md](PHUONG-PHAP-K03.md). Image/policy/thư viện/port thực tế nằm trong `environment.json` của mỗi lượt và `baseline.json`.

## Kết quả từng scenario Python

| Scenario | Giây thực đo | Slice | Chi tiết kết quả thực tế |
|---|---:|---:|---|
| `real_idp_bootstrap` | 0.312000 | 16 | {"scope": "outside request latency measurement"} |
| `managed_runtime_dependency_preflight` | 1.500000 | 16 | {} |
| `real_managed_runtime_https_ready` | 6.188000 | 16 | {"environment": "staging", "api_pool": 8} |
| `anonymous_cross_site_and_unsafe_return_denied` | 0.172000 | 16 | {} |
| `real_password_wrong_otp_good_otp_http_callback` | 2.344000 | 16 | {"session_status": 200, "cookie_secure": true, "cookie_http_only": true} |
| `real_callback_replay_no_new_session` | 0.140000 | 16 | {} |
| `missing_csrf_denied_without_logout` | 0.266000 | 16 | {} |
| `real_two_accounts_distinct_server_sessions` | 1.750000 | 16 | {} |
| `real_logout_all_preserves_other_account` | 0.594000 | 16 | {} |
| `real_idp_process_outage_no_session_or_retry_loop` | 21.641000 | 16 | {"callback_seconds": 3.1409999999996217, "callbacks_sent": 1, "created_sessions": 0} |
| `real_idp_resume_fresh_login_recovers` | 24.297000 | 16 | {} |
| `real_smtp_expired_reset_link_denied` | 6.687000 | 16 | {"token_ttl_seconds": 2, "wait_seconds": 5} |
| `real_smtp_reset_retains_factor_no_auto_session` | 1.172000 | 16 | {"original_factor_retained": true} |
| `real_reset_replay_and_old_password_denied` | 0.672000 | 16 | {"replay_contexts": 2} |
| `real_new_password_still_requires_original_otp` | 20.984000 | 16 | {} |
| `real_operator_revoke_after_reset_preserves_b` | 0.344000 | 16 | {"denied_status": 401, "other_status": 200} |
| `real_disabled_provider_account_cannot_login` | 1.000000 | 16 | {} |
| `real_chrome_identity_journey` | 66.171000 | 16 | {"browser_cases": 7} |
| `real_early_renew_does_not_rotate` | 22.438000 | 16 | {"record_status_original": 200, "http_status": 200} |
| `real_natural_300s_rotation_12_parallel_renewals_one_winner` | 301.844000 | 16 | {"natural_wait_seconds": 300.34364, "requests": 12, "statuses": {"409": 9, "401": 2, "200": 1}, "rotations": 1, "old_cookie_status": 401, "new_cookie_status": 200, "idle_seconds": 900, "absolute_seconds": 28800, "clock_mutation": false} |
| `real_bounded_auth_start_abuse_subject_limit` | 15.297000 | 17 | {"requests": 35, "maximum_parallel": 1, "statuses": {"303": 30, "429": 5}, "retries": 0, "followed_redirects": 0, "retry_after_min": 49, "retry_after_max": 49, "subject_limit": 30, "window_seconds": 60, "public_idp_requests": 0} |

## Chrome thật — 7/7 đạt

| Ca | Thời gian ms | Kết quả |
|---|---:|---|
| `chrome_real_mfa_callback_secure_cookie_and_storage` | 28713.998 | {"chrome": "Chrome/157.0.8103.52", "bad_otp_denied": true, "session_status": 200} |
| `chrome_second_tab_same_real_session` | 1389.481 | {} |
| `chrome_logout_all_clears_other_tab_within_5s` | 626.793 | {"deadline_ms": 5000} |
| `chrome_real_mfa_relogin_after_local_logout` | 27980.783 | {} |
| `chrome_product_logout_confirms_real_idp_logout` | 628.701 | {} |
| `chrome_b_login_after_product_and_idp_logout_no_a_workspace` | 2759.202 | {"mode": "logout-idp-then-login"} |
| `chrome_current_account_route_revalidation` | 270.430 | {"bfcache_qualification": false} |

API callback khi pause IdP được đo riêng bằng trường `callback_seconds`; không nhầm với tổng thời gian chuẩn bị OTP/pause của scenario. Operator revoke là gọi thao tác tin cậy đã có và commit DB; không đo từ một sự kiện IdP chưa được chuyển tới app.

## Toàn bộ lượt thử được giữ

| Lượt | Trạng thái toàn lượt | Scenario đã ghi | Giây | Giải thích |
|---|---|---:|---:|---|
| 01 | failed | 1 | 79.000 | Fixture: release ID/DB URI chưa đúng định dạng. |
| 02 | failed | 1 | 59.297 | Startup fixture thất bại; log cũ không đủ phân lập nguyên nhân. |
| 03 | failed | 1 | 119.172 | Fixture preflight thiếu biến cấu hình. |
| 04 | failed | 1 | 100.218 | Fixture workspace/owner chưa cùng transaction. |
| 05 | failed | 1 | 88.750 | Fixture PostgreSQL chưa có TLS verify-full. |
| 06 | failed | 9 | 144.063 | Driver đọc DOM khi navigation chưa xong. |
| 07 | failed | 9 | 164.359 | Driver đọc DOM khi navigation chưa xong. |
| 08 | failed | 9 | 146.813 | Keycloak giữ SSO A; trình tự chọn B chưa đúng. |
| 09 | failed | 9 | 157.937 | Keycloak từ chối đổi user trong SSO A. |
| 10 | failed | 9 | 159.890 | Gọi switch sau logout; server yêu cầu phiên đang hoạt động. |
| 11 | failed | 17 | 250.719 | Driver kỳ vọng SSO tự đi qua form mật khẩu. |
| 12 | failed | 17 | 202.219 | Driver kỳ vọng mất cookie A trước khi B xác thực xong. |
| 13 | failed | 17 | 225.532 | Keycloak yêu cầu logout SSO A trước khi đăng nhập B. |
| 14 | failed | 0 | 68.203 | Đăng ký chính xác URI logout có fragment bị từ chối; thiếu HTTP code trong log lượt này. |
| 15 | failed | 0 | 48.297 | HTTP400 invalid_input khi đăng ký URI logout có fragment. |
| 16 | failed | 20 | 551.593 | 20 scenario trước đã hoàn tất; bài quota 4 đồng thời dừng ở HTTP503. Không công nhận cả lượt là pass. |
| 17 | passed | 5 | 116.860 | Lượt quota tuần tự riêng đạt; không chạy lại 20 scenario đã hoàn tất. |

Các lượt lỗi là lịch sử điều tra, không bị đổi nhãn thành pass; không suy ra mọi 503 cũ cùng một nguyên nhân. 503 khi auth-start 4 đồng thời phù hợp giới hạn IdP 2 inflight, nhưng không có trace đủ để quy mọi 503 cho một nguyên nhân. Bài quota cuối được đo tuần tự để tách quota khỏi backpressure; không tăng budget hoặc cho phép 503 trong assertion quota mới.

## Hồ sơ và kiểm tra bảo toàn

- `raw-test-logs.zip`: 931773 byte; SHA-256 `9634bbec1ad5837d0649a335e51b64513f9630b0f6edd291613498d12cb2e0be`. Có đủ console, result, API safe log, build/Chrome log, input manifest và snapshot driver khớp SHA của slice 16/17. Snapshot driver 16 được khôi phục từ delta xác định và chỉ nhận sau khi khớp chính xác hash trước chạy.
- ZIP kiểm lại CRC và SHA từng entry: **121 file hợp lệ**. Bản sao raw còn trên máy ở `attempt-01` đến `attempt-17`, được Git bỏ qua vì ZIP đã chứa chúng.
- Quét JWT/DSN có mật khẩu/private key/callback-code value/password literal: **0 dấu hiệu khớp**; riêng driver cũng kiểm API log bằng các secret fixture đang dùng trước khi xuất. Đây không phải chứng minh mọi cách mã hóa secret đều có thể bị phát hiện.
- Kiểm sau chạy: **0 container IdP/Mailpit, 0 container PG, 0 network còn lại mang nhãn fixture**. Context PG kiểm xóa anonymous volume; không dừng Chrome/container công việc khác.
- Hai file bằng chứng K09.03 đã sửa từ trước được so SHA và giữ nguyên; không stage/push chúng. Các artifact K07/K09 cũ cũng không thuộc commit này.
- `scenario-results.csv`: verdict, thời gian và toàn bộ tham số đo từng scenario; `attempts.json`: lịch sử đầy đủ; `result.json`: verdict kỹ thuật và giới hạn; `source.patch`: đúng ba tệp tracked liên quan thay đổi.

## Giới hạn nghiệm thu

Không nhận thay staging chung/production, việc chọn issuer/assurance/lost-factor của nhóm, toàn bộ BFCache/back-forward/A01–A32, webhook IdP tự động, soak 8 giờ hoặc sức tải 50k. Không đổi các mục phối hợp thành đã nghiệm thu chỉ vì có kết quả kỹ thuật local.

Commit/push chỉ trên `product-api`. Báo cáo này được lập trước commit nên raw result giữ đúng `committed=false/pushed=false` tại thời điểm chạy. CI sau push được ghi theo đúng SHA trong receipt CI riêng; không dùng CI của commit cũ thay cho commit mới.
