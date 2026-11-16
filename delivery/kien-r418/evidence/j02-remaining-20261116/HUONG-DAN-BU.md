# J02 kiểm và xác nhận trên MacBook ngày 16 tháng 11 năm 2026

Kiên đã sửa lỗi mất membership khiến UI đăng xuất nhầm. Bản này dùng staging Docker của Kiên, dữ liệu synthetic riêng J02. Chưa commit/push; không thay bản bằng SHA nền đơn lẻ. Hai máy hiện cùng LAN: Kiên 192.168.2.10, Bu 192.168.2.7.

## 1 Mở đúng browser và kiểm kết nối trước

Giải nén vào Downloads. Trong Terminal chạy:

```bash
bash ~/Downloads/J02-Bu-kiem-tra-16-11/open-mac.sh
```

Mỗi lần chạy lệnh tạo một hồ sơ Chrome thử riêng. Không mở localhost bằng hồ sơ Chrome thường: nó không có ánh xạ cổng tới staging. URL trong Chrome vẫn là https://localhost:18776/; đường đi thật là máy Kiên cổng 18976/18977. Script chỉ ghim hai khóa chứng chỉ thử đã dùng, không tắt kiểm TLS toàn bộ hoặc đổi DNS hệ thống.

Kết quả bước 1 cần gửi ngay: thấy màn Sign in to Aletheia hay không; nếu lỗi gửi nguyên mã lỗi và giờ. Chưa thử các ca thay đổi membership trước khi Kiên báo tài khoản sẵn sàng.

Tài khoản theo số:

0: j02_2be4f511_0 — owner workspace A
1: j02_2be4f511_1 — owner workspace B
2: j02_2be4f511_2 — admin A và viewer B
3: j02_2be4f511_3 — analyst A
4: j02_2be4f511_4 — reviewer A
5: j02_2be4f511_5 — viewer A dùng ca không còn workspace
6: j02_2be4f511_6 — viewer A và viewer B dùng ca còn workspace B

Mật khẩu nằm trong tệp riêng Kiên gửi qua kênh riêng, không nằm trong ZIP và không đưa vào ảnh/log. Email mỗi tài khoản là username + @aletheia.invalid. Workspace A: ws_79e07c15e3964079ad6a044f2203b854; B: ws_a754dc275d52451cb990f472cfd232a6. Link kết quả đầy đủ trong LINK-KET-QUA.txt. Mỗi account mở bằng một lần chạy open-mac.sh để tách cookie; hai tab cùng account phải mở trong cùng hồ sơ thử.

## 2 Các ca cần chạy và ghi kết quả

T01 — Owner và scope. Đăng nhập account 0, mở workspace A, dán link A, xem 10 claim/citation, reload. Ghi thời gian và ID. Account 1 chỉ được vào B: dán link A phải bị từ chối hoặc về danh sách workspace; không được lóe câu hỏi/claim A. Giữ ảnh và trace.

T02 — Năm role. Lần lượt 0/2/3/4/5 vào A, đọc kết quả và thử export JSON. Owner/admin/analyst có import/analyze; reviewer/viewer không có thao tác này hoặc có lý do không được dùng. Kiểm cả server refusal nếu tráo request, không chỉ ẩn nút. Ghi role, allowed_actions, mã HTTP; không tự thêm grant hoặc sửa payload để bỏ qua kiểm quyền.

T03 — Onboarding/invite qua UI. Account 0 dùng Workspaces, tìm account 5 hoặc 6 bằng email và chọn đúng user_id. Chỉ tạo invite sau khi account đó đã bị remove theo ca T04/T05. Account được mời nhập token trong Accept an invitation. Kiểm sai account → 403 invitation_wrong_account; invite bị revoke hoặc đã dùng → 404 invitation_unavailable; hết 60 giây tự nhiên → 410 invitation_expired. Không dán token vào báo cáo. Giữ ảnh thông báo sau mỗi ca và số request. Kiên đã có bằng chứng first-owner qua form và race owner thật; Bu xác nhận UI không biến 403/404/410 thành thành công.

T04 — Mất workspace cuối. Account 5 mở link A, chủ workspace dùng account 0 remove access account 5. Ghi từ lúc server ACK thay đổi quyền tới lúc nội dung A bị xóa/che; yêu cầu ≤5 giây. GET /v1/session phải còn 200, onboarding needs_workspace; UI ở danh sách/màn chưa có workspace, không Sign in. Nếu thấy còn workspace C thì báo Kiên chuẩn bị lại fixture trước khi chạy; không kết luận ca không-còn-workspace khi vẫn có membership khác.

T05 — Còn workspace khác. Account 6 có A và B. Mở A, account 0 remove membership A. Nội dung A phải mất trong ≤5 giây, session vẫn 200. Chọn B và tiếp tục được; không đăng nhập lại. Gửi thời gian, status session/capabilities/header và ảnh A đã mất/B mở được.

T06 — Đổi ngữ cảnh khi request chậm. Account 2 có A và B. Bật throttling có độ trễ hoặc giữ response bằng công cụ Quân đang dùng; mở link A rồi chuyển B trước khi response A hoàn tất. Giải phóng success và error cũ: không đổi về A, không toast/draft/kết quả cũ, không Sign in do lỗi cũ. Ghi rõ response thật hay fault được điều khiển. Kiên đã có các ca giữ response thật và late refusal; Bu có thể đối chiếu bằng chứng và ghi xác nhận consumer.

T07 — Job/download đang chờ gặp revoke. Báo Kiên trước để phối hợp. Account 2 gửi follow-up; cần có bằng chứng worker running trước revoke, sau đó không xuất bản kết quả mới. Với account 4 đang tải JSON, giữ response trước khi trả body cho ứng dụng, revoke A rồi giải phóng: không bắt đầu download mới hoặc hiện thông báo thành công ở context cũ. Không gọi file đã tải hợp lệ trước revoke là rò rỉ mới. Gửi job/attempt IDs, mốc ACK revoke, trạng thái UI, event download; không ghi credential/token.

T08 — Hai tab và account switch. Hai tab cùng account 3 cùng mở A; Sign out all sessions ở tab thứ hai, tab đầu phải xóa nội dung trong ≤5 giây và API session 401. Sau đó đăng nhập account khác ở đúng quy trình IdP; Back/Forward/reload/link cũ không phục hồi dữ liệu, draft, review mark hoặc toast của account trước. Ghi mọi redirect bất thường.

T09 — Back và BFCache. Từ kết quả chuyển sang trang khác rồi Back. Ghi pageshow.persisted hoặc lý do browser không dùng BFCache. Chrome máy Kiên báo no-store không đủ điều kiện: Back tải mới đã kiểm an toàn. Không tắt no-store để biến ca này thành pass. Nếu Mac thực sự restore BFCache, kiểm private content vẫn bị che tới khi revalidate; sau logout/revoke không lóe nội dung cũ. Ghi Chrome/Safari riêng nếu có chạy, không coi một browser đại diện browser kia.

T10 — Mất kết nối và phục hồi. Khi đang xem A, bật Offline ở DevTools trong 8–10 giây; nội dung phải được che trong ≤5 giây. Online trở lại phải xác nhận quyền rồi mới hiện, không login loop và không tự gửi lại mutation. Kiểm đăng nhập/reauth đi về route an toàn, không chạy lại import/analyze trước đó. Ghi số request, retry, mutation và thời gian che/phục hồi. Đây là fault mạng browser; không ghi thành đã shutdown IdP. Phần IdP thật/renew storm đã có biên bản K03 để tái sử dụng, cần giữ đúng giới hạn.

## 3 Kết quả gửi lại

Gửi một TXT/JSON và ảnh/trace đã che secret, gồm:
- Ngày giờ ICT, IP, Mac/OS/Chrome hoặc Safari, tên bản UI/manifest trong gói.
- T01–T10: PASS/FAIL/NOT RUN, số lượt, bước thực hiện, IDs synthetic, mong đợi/thực tế, thời gian ms, HTTP status, console, retry/mutation/download events.
- Với revoke: mốc server commit/ACK, mốc nội dung private mất; nếu không có trace đủ mốc thì ghi giới hạn phép đo.
- Chốt từng J02.01/.02/.03/.04 đồng ý hay còn lỗi cụ thể. Có thể chấp nhận bằng chứng backend Kiên đã cung cấp, không tự nhận đã chạy lại phần đó.
- Vướng cần quyết định: BFCache thực trên Mac (hoặc chứng cứ browser không cho cache theo policy), return-to sau reauth và hành vi còn membership. Không dùng UI hiding thay server enforcement.

Nếu gặp lỗi, dừng đúng ca đó, giữ trace, báo Kiên; các ca đã đạt không cần chạy lại. Không gửi HAR nguyên bản chứa cookie/token/password/invitation token. Các bước thay role chỉ áp dụng 7 account synthetic J02 trong gói này.
