# K15.03 và K15.04 — kết quả kỹ thuật phần Kiên

**K15.03 và K15.04 đạt trong phạm vi backend kiểm được tại máy Kiên. Cộng K15.01 và K15.02 đã đạt trước đó, phần kỹ thuật backend của Kiên là 4/4.** Hai thành phần được chứng minh ở hai lượt cùng SHA runtime. Lượt 03 chỉ dùng sáu ca quyền đã hoàn thành. Lượt 04 là phép kiểm riêng reconnect chạy xanh.

HEAD `fa46dd8cbb2239c6f116456894a2009b45846245`, runtime identity `acbe153cee890b3b5c7c1a8d1a36d100f9ba8cdbce6366005b511a9a7dc5a529`. Hai tiến trình API, TLS được kiểm CA riêng, proxy TCP cục bộ, PostgreSQL 16 riêng và phiên synthetic có chữ ký. Hai worker được khởi động sau khi đối chiếu 100 job đang chờ. Tài nguyên Docker ứng dụng 4 CPU, 3 GiB, 256 PID, FD 1.024. PostgreSQL 1 CPU, 512 MiB và `max_connections=24`. Hai API pool 4 và hai worker pool 2. Driver cùng proxy chạy trong container đo. Khởi động một fixture riêng từ các helper native đã có. Staging K14, UI và các ca đã đạt giữ nguyên.

## K15.03. Quyền và scope khi phản hồi đang chờ

| Ca | Kết quả | Thời gian quan sát |
| --- | --- | --- |
| Thu hồi membership khi API 1 đang đợi khóa PostgreSQL | 404, không có job/cursor | 3.41 ms sau commit |
| Vô hiệu hóa tài khoản rồi kiểm hai replica | 401 session_revoked trên cả hai | 48.55 ms cho chuỗi kiểm |
| Thu hồi phiên rồi kiểm hai replica | 401 session_revoked trên cả hai | 65.49 ms cho chuỗi kiểm |
| Tài khoản khác dùng cursor cũ | 400, response không có dữ liệu job | Đi qua API 1 |
| Đổi owner thành viewer có quyền đọc nguồn | Cursor cũ 400, snapshot mới 200 | Kiểm revision membership |
| Phản hồi đã được cấp quyền bị proxy giữ qua revoke | Request mới 404, bộ thử loại phản hồi cũ | 404 sau 20.03 ms. Phản hồi cũ tới sau 4252.39 ms |

Các ca từ chối đi qua HTTP thật, `Cache-Control: no-store`, body không có job, output, revision hay cursor. Kiểm riêng đường output sau revoke và cursor lỗi sau khi mất quyền. Điểm bắt đầu phép đo membership là sau khi giao dịch revoke commit và request chờ khóa của chính giao dịch đó đã được xác thực qua `pg_blocking_pids()`.

Proxy giữ 904 byte TLS của phản hồi API đã tạo khi phiên còn quyền. Phản hồi cũ vẫn có HTTP 200 ở mức giao thức khi proxy thả ra. Bộ thử đo từ lúc bắt đầu request và loại nó trước khi áp dụng trạng thái vì đã quá 4.000 ms. Request mới qua replica kia trả 404 ngay sau revoke. Kết luận về hiển thị chỉ áp dụng cho model consumer trong bài này. Chưa chạy trình duyệt để nghiệm thu che dữ liệu khi tab bị ẩn hoặc đồng hồ bị treo.

## K15.04. Giới hạn nối lại

20 workspace, mỗi workspace có 5 principal hoạt động và 5 job preview được nhận thật. Một client HTTPS có cookie/CSRF riêng cho mỗi principal. Giữ worker chưa nhận việc trong cửa sổ đo để có đúng 100 job chờ. Hai proxy lỗi lần lượt ngắt 15 socket và đóng 15 socket vì idle timeout 2 giây. 70 client còn lại dùng proxy bình thường. Tất cả 30 lỗi có lỗi transport quan sát được ở client. Khi proxy hồi phục, giữ cursor đã có và áp dụng backoff/jitter theo hàm hiện tại. 429 bắt buộc có `Retry-After`. Không gửi lại POST khi nối lại polling.

| Chỉ số | Kết quả |
| --- | --- |
| Đối chiếu job đang chờ và revision | 100/100 |
| Client bắt đầu sau khi hồi phục, 30 client lỗi | 503.25–1000.04 ms |
| Lượt GET trong cửa sổ hồi phục | 103 gồm 100 HTTP 200, 2 HTTP 429 và 1 lỗi transport thêm |
| HTTP 200 sau nối lại, mean / p95 / max | 166.41 / 359.66 / 380.95 ms |
| Mẫu pg_stat_activity và số kết nối DB đỉnh | 52 mẫu, đỉnh 4, gồm kết nối fixture |
| Socket proxy còn mở sau drain | 0 |
| Đối chiếu cuối sau worker | 100/100 succeeded ở attempt 1, 100/100 hash nguồn khớp trong 43.60 s |

Ba lượt cần retry trong cửa sổ phục hồi. Hai 429 chờ 1.392 ms và 1.009 ms. Lỗi socket thêm chờ 509 ms. `reconnect-http.csv` ghi dữ liệu từng lần gửi, trạng thái, thời gian chờ, độ trễ và byte. Các lỗi vận chuyển ban đầu của 30 kết nối bị chặn được đếm riêng khỏi 103 lượt phục hồi. Phần ghi nhận cuối dùng 100 snapshot terminal và 100 lần kiểm output thật. Tất cả 100 ID khớp ledger ban đầu.

| Vai trò | Mẫu | CPU trung bình core | RSS đỉnh mẫu MiB | FD trước / đỉnh / sau |
| --- | --- | --- | --- | --- |
| postgres | 52 | 0.191 | 288.24 | không đọc được / không đọc được / không đọc được |
| generator | 52 | 0.202 | 151.55 | 19 / 324 / 20 |
| api0 | 52 | 0.256 | 141.29 | 9 / 67 / 12 |
| api1 | 52 | 0.246 | 141.46 | 9 / 64 / 12 |

FD của tiến trình đo gồm client, đầu proxy và kết nối upstream. API còn kết nối pool sau khi client đóng. Đỉnh FD trước/sau của PostgreSQL không đọc được trong namespace hiện tại. Các bộ đếm proxy lưu riêng số socket, số byte TLS, kết nối sang từng replica và lỗi có chủ đích. Đỉnh từng proxy xảy ra ở các thời điểm có thể khác nhau, nên không cộng các đỉnh để gọi là số đồng thời của cả hệ.

## Lịch sử và tái lập

| Lượt | Kết quả |
| --- | --- |
| 01 | Fixture update membership khóa sai thứ tự, HTTP 500, chưa có ca đạt. |
| 02 | Khóa workspace trước như luồng quản trị, ca membership đạt 404 sau 4,70 ms. Oracle chưa chấp nhận mã hợp lệ session_revoked. |
| 03 | K15.03 đạt đủ 6 ca. Lượt tổng thất bại tại K15.04 khi client thử chưa backoff sau một socket đóng trong lúc hồi phục. |
| 04 | Chỉ chạy K15.04. Client thử dùng backoff sẵn có cho lỗi TLS/HTTP, tái lập đủ 100 job từ đầu và đạt. |

Lượt từng lỗi được lưu nguyên. Bản quyền K15.03 có sáu ca đạt trong lượt 03. Lượt 04 xanh fixture reconnect riêng, 1 pytest đạt trong 64,97 s. Ruff check và format check đạt trên ba tệp mới. So SHA của mã chạy và bằng chứng K14 cũ, xác nhận dọn container và PostgreSQL riêng của mỗi lượt. Không sửa mã chạy ứng dụng hay tiêu chí K15.01/K15.02.

Driver dùng Python và Docker có bộ phụ thuộc đã ghim. Tham số đầu là thư mục kết quả mới. Đặt `K15_RECONNECT_ONLY=1` để chạy riêng K15.04. Tệp manifest, kết quả thô và tool theo phiên bản đã đo nằm trong thư mục này.

**Phạm vi tiếp nhận:** Kiên có kết quả backend cho cả hai mục trên fixture hai replica. Ca tab background/foreground, BFCache và triển khai chung cần kết quả riêng. Chưa commit hay push.
