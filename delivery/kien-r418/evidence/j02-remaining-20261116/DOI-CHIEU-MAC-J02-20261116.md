# Đối chiếu kết quả J02 của Quân ngày 16 tháng 11 năm 2026

Quân ghi **T01–T10 PASS** trên bản staging J02 qua LAN, với các ngoại lệ UX nêu ở từng ca. J02.01 và J02.03 đã có xác nhận của Quân. J02.02 được xác nhận phía server và còn hai lỗi UI đã phân công. J02.04 còn mở vì Sign out rồi Sign in chưa đổi được tài khoản bằng luồng UI chuẩn. T08 có kết quả đạt cho thu hồi hai tab và cách ly sau khi đổi tài khoản thủ công, còn bước chuyển đổi tài khoản trực tiếp qua UI bị vướng Keycloak.

Máy thử là MacBook Pro M1 8 GB, macOS 15.3.2, Chrome 157.0.8103.51, IP `192.168.2.7`. Quân dùng hồ sơ Chrome riêng cho mỗi tài khoản. SHA-256 ZIP phản hồi `14c43f1e8f62fc15f06fd5b7a907f3cc1b5439e8542a676e8ab523098708877c`. CRC hợp lệ, khớp 56/56 file theo manifest nội bộ, báo cáo rời trùng byte với bản trong ZIP. Hai ảnh token T03 đã được kiểm trực quan và vùng mã được che. Gói gửi Quân nhận diện bởi checksum `9dc6bd39…db2f`. Gói Mac không kèm manifest từng asset, nên dùng bằng chứng kết nối và cấu hình staging của lượt này để nhận diện bản chạy. [Báo cáo gốc](00-Bao-cao-J02-Quan.txt) · [Kiểm gói](mac-j02-package-check.json).

| Ca | Kết quả được đối chiếu |
| --- | --- |
| T01 | Owner A mở 707 ms, reload 1.245 ms, 10 claim có nguồn và 15 phản hồi 2xx. Owner B bị chặn A bằng capabilities 403/header 404, observer ghi 0 lần lóe nội dung A. Link B mở sau 641 ms. |
| T02 | Cả 5 role đọc A và export JSON thành công 201→200. Reviewer/viewer bị chặn cả trên UI và khi gọi thẳng job analyze/preview 404. Câu giải thích follow-up của UI cần ghi đúng giới hạn role. |
| T03 | 5 lượt kết quả theo báo cáo, gồm sai account 403, đã thu hồi 404, hết hạn 410, accept 200 và replay 404. FL-147 khiến Quân phải dùng DevTools mới lấy được mã để kiểm. |
| T04 | UI mất nội dung sau ACK 1.966 ms. Session 200, `needs_workspace`, không còn workspace. |
| T05 | Lượt đo lại mất nội dung sau ACK 1.895 ms. Session 200, mở B từ danh sách sau 317 ms. Lượt đầu thiếu mốc. FL-148 khiến dán link B ngay sau khi mất A không tự mở workspace B. |
| T06 | Hai lượt có điều khiển giữ 200 thật và bơm 503 tới muộn, UI vẫn ở B. Chưa chạy ca giữ đến hết deadline client. |
| T07 | Follow-up đang chạy mất quyền rồi thất bại `authorization`. Nội dung mất 2.181 ms sau ACK. Download 200 bị giữ, thả sau ACK 1.027 ms, 0 sự kiện download, 0 thông báo thành công. |
| T08 | Thu hồi tab còn lại khoảng 197 ms từ lúc bấm, session 401 `session_revoked`. Sau khi đổi account thủ công, Back×4/Forward×4, reload và link cũ ghi 0 lần lóe dữ liệu A. Việc đổi account trực tiếp qua UI còn lỗi. |
| T09 | Chrome tải lại khi Back, `pageshow.persisted=false` và revalidate trước khi hiện nội dung. Giữ `no-store`. Safari và ca restore BFCache thật chưa có bằng chứng. |
| T10 | Offline giả lập che dữ liệu sau 2.969 ms, online phục hồi sau 630 ms, 9 request sau khi online, 0 mutation và 0 login loop. |

### Đối chiếu T07 với server

Job ID `job_0dbd95f86c69421fa5751ef2dfe74440` trùng tuyệt đối. Trace Mac ghi `running` cùng heartbeat trước khi gửi revoke. PostgreSQL ghi attempt bắt đầu trước audit gỡ quyền 313,262 ms và job failed sau mốc audit 1.369,660 ms. Job và attempt cùng `authorization`, không có output hoặc `job.publish`. Quân ghi project revision 2 và `current_result_id` giữ nguyên. ACK MacBook xuất hiện sớm hơn audit server khoảng 4,9 giây ở cả hai lần revoke, cho thấy hai đồng hồ lệch. Khoảng ACK→UI và ACK→thả response dùng riêng đồng hồ Mac, còn khoảng attempt→audit→terminal dùng riêng đồng hồ PostgreSQL. Trace download ghi request `net::ERR_ABORTED`, session 200, 0 event và 0 thông báo tải thành công. [Biên nhận server T07](mac-t07-server-receipt.json).

### Các điểm Quân nhờ xác nhận

1. **Logout.** Hai API của lượt Mac dùng cấu hình mặc định tắt `ALETHEIA_HOSTED_IDP_LOGOUT`, vì vậy logout thường thu hồi phiên app và trả 204, còn phiên SSO Keycloak có thể dùng lại. `logout-all` hiện chỉ thu hồi tất cả phiên app và trả 204, kể cả khi bật hỗ trợ logout IdP cho tuyến thường. Cấu hình client `k14-staging` hiện ghi `post.logout.redirect.uris=+` và chưa đăng ký chính xác `https://localhost:18776/` theo bài kiểm K03. Chưa có request/response gốc của trang Keycloak “Logout failed” để kết luận lý do thông báo cụ thể. Việc bên Kiên còn mở là cấu hình đúng handoff cho logout thường, thống nhất hành vi IdP cho logout-all với consumer và kiểm lại logout A → đăng nhập B trong cùng hồ sơ Chrome. [Đối chiếu cấu hình](mac-j02-idp-logout-config.json).
2. **Thời hạn phiên.** Idle 900 giây, absolute 28.800 giây, ngưỡng xoay 300 giây. Polling không gia hạn idle. Renew session sau ngưỡng xoay, khi phiên còn hợp lệ, gia hạn idle tới giới hạn absolute. Lượt account 0 có expiry 11:23:25, phù hợp mốc 11:24. Báo cáo Mac ghi lần 11:42 trả 401 `session_expired`, nhưng gói chỉ chứa trace của lượt T08 sau đó, nên chưa đối chiếu được phiên chính xác của mốc 11:42. [Cấu hình và thời điểm phiên](mac-t07-session-receipt.json).
3. **Viewer B.** Đúng chính sách. Account 6 có membership viewer đang active ở B. Result tồn tại, nhưng cả `project_grant` và `source_grant` của account 6 đều rỗng. Role cho phép đọc khi tài nguyên có grant phù hợp. Bản native kiểm cả project và source, nên result/header 404 trong fixture này là kết quả mong đợi. [Đối chiếu grant PostgreSQL](mac-j02-b-viewer-grants.json).
4. **Reviewer/viewer gọi thẳng analyze/import.** Đúng chính sách. Adapter job ánh xạ `AuthorizationDenied` sang 404 `not_found` để che thông tin tài nguyên. Giữ 403 trên các tuyến có contract 403 riêng, ví dụ capabilities hoặc kiểm lời mời sai account. UI nên giải thích bằng role/capabilities đã biết thay vì đoán từ 404.

Quân giữ FL-147 về sao chép mã mời và FL-148 về mở link B trong cùng tab sau khi mất workspace A. Copy được mã chỉ bằng cách đổi loại input trong DevTools được ghi là thao tác thử tạm. Kết quả accept/deny của server vẫn đạt. Câu chữ follow-up của reviewer cũng được Quân ghi nhận trong T02.

### Thu hồi tài khoản thử

Đã đổi mật khẩu sang giá trị ngẫu nhiên mới, vô hiệu hóa đúng 7 tài khoản `j02_2be4f511_*` trong Keycloak và kết thúc phiên SSO. Từng thao tác trả 204 và từng tài khoản được kiểm lại còn 0 phiên SSO. Operator đã thu hồi phiên app của 7 tài khoản bằng `revoke_identity`. Kiểm sau commit ghi 0 phiên app active. Account 2, 4, 5, 6 vẫn mất quyền A. Tệp cấp mật khẩu cũ và các giá trị xác thực trong cấu hình tạm đã được dọn. [Biên nhận thu hồi](mac-j02-fixture-retirement.json).

Chưa commit/push. J02 cần xử lý logout/đổi account và kiểm lại phần UI còn mở trước khi đóng toàn bộ.
