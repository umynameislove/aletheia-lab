# K16.03 và K16.05 — kết quả staging riêng của Kiên

**K16.03 đạt trên staging riêng. K16.05 đạt bài diễn tập thu thập → cảnh báo → xử lý → hồi phục trên cùng staging.** Cả 5 mục K16 có bằng chứng kỹ thuật trong phạm vi kiểm thử của Kiên. Nghiệm thu trên hạ tầng chung cần thêm bằng chứng nêu ở cuối.

Ngày chạy 15/11/2026. Nhánh `product-api`, SHA `fa46dd8cbb2239c6f116456894a2009b45846245`. Cụm Docker mới cho K16 dùng image `sha256:35ab7a8638b1f3ef7134bfeb87a4bbb6aac0a0101d74857cdaabafee251b8add` và runtime identity `acbe153cee890b3b5c7c1a8d1a36d100f9ba8cdbce6366005b511a9a7dc5a529`. Image triển khai thử này có identity riêng với image đã quét của K16.04.

## Môi trường

- Docker Engine 28.0.4, mạng Docker nội bộ riêng. Ingress TCP phân phối đến hai replica API. TLS kết thúc ở API và Keycloak. Chứng thư được kiểm bằng CA thử riêng và tên máy chủ.
- PostgreSQL 16.15 chạy TLS verify-full, tối đa 24 kết nối. Mỗi API dùng pool 4, mỗi worker dùng pool 2. Mỗi API có 0,75 core, 512 MiB và một slot health riêng. Mỗi worker có 1 core và 640 MiB. Ingress có 0,25 core và 96 MiB. Máy phát có 0,75 core, 384 MiB, cùng host nhưng chạy trong container riêng.
- Keycloak 26.8.2 thực hiện trao đổi mã OIDC qua TLS, dùng realm và tài khoản synthetic. Dữ liệu PostgreSQL gồm 30 principal, 2 workspace, 6 project, 6 snapshot và 6 result. Dataset SHA-256 `4ed0454e815c0c53ffc6773a2df32cb62ed9950881cce892e3de49c68cf594f0`. Đây là quy mô kiểm biện pháp bảo mật và diễn tập vận hành.

## K16.03 — Kiểm biện pháp bảo mật

| Bề mặt | Phương pháp và điều kiện đạt | Kết quả |
|---|---|---|
| TLS và authority | Đi qua ingress với CA và tên hợp lệ. Đổi CA, đổi TLS server name, rồi gửi HTTP Host sai. | TLS 1.3 với CA đúng. Cả hai ca xác minh chứng thư sai bị từ chối. Host sai trả 400. |
| Định danh | Khởi tạo đăng nhập, gửi form đến Keycloak, theo callback rồi lấy phiên. Thử truy cập phiên khi ẩn danh. | HTTP 303 → 200 → 302 → 303 → 200. Phiên ẩn danh trả 401. Cookie khởi tạo có Secure, HttpOnly, SameSite=Lax và host-only. |
| Tenant và CSRF | Dùng phiên owner của workspace A đọc result của workspace B. Gửi thao tác tạo job với Origin sai. | Đọc chéo trả 404. Mutation cross-site trả 403. Cả bốn tuyến từ chối 400/401/403/404 trả `no-store` và `nosniff`. |
| CSP và tệp private | Chạy bộ phục vụ asset cùng image và cấu hình API qua TLS, đọc snapshot web đang có từ mount chỉ đọc. Thử ba đường dẫn tệp ẩn/traversal. | Trang gốc trả 200 với CSP có `default-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`, cùng `no-referrer`, `nosniff`, `no-store`. Ba đường dẫn bị 404. Hash HTML được ghi trong `static-01.json`. |
| Secret của tiến trình | Kiểm mount và chạy kiểm quyền bên trong đủ 2 API và 2 worker. | 4/4 dùng UID 10001, root filesystem chỉ đọc, drop toàn bộ capability và no-new-privileges. Secret đúng vai trò, mode 0400, đọc được, ghi bị chặn. Worker chỉ có DSN worker. |
| Quyền collector | Kết nối DB bằng role collector riêng ở chế độ read-only mặc định. Thử đổi giao dịch sang read-write rồi kiểm quyền thực tế. | 3/3 bị PostgreSQL từ chối `42501` khi thử xóa job, đọc toàn hàng job hoặc đọc định danh. Sampler vẫn đọc được metadata hàng đợi bằng đúng các quyền cột. Collector thiếu mount secret API/worker, object và Docker socket. |
| Chống lạm dụng qua edge | 6 luồng gửi 60 yêu cầu khởi tạo auth, mỗi yêu cầu dùng một `X-Forwarded-For` khác nhau. Cùng lúc gửi 12 lượt đọc của owner thuộc workspace khác. | 28 HTTP 303, 32 HTTP 429 có `Retry-After` 45–46 giây và `no-store`. 12/12 lượt đọc workspace nền thành công, đúng scope. Readiness cuối 200. Cả bài dồn chạy 3,152 giây. |

Trong 60 yêu cầu auth, độ trễ trung bình/p95 quan sát/cao nhất là **177,27 / 643,87 / 740,01 ms**. Với 12 lượt đọc nền là **211,63 / 753,63 / 753,63 ms**. Phép đo dùng đồng hồ monotonic phía client và giữ kết quả 429 trong mẫu số.

Bằng chứng về giới hạn ZIP, PDF sandbox và SSRF dùng các kết quả đã lưu theo threat model trong [tài liệu gốc](../../../../docs/k16-threat-model.md). Lần này bổ sung kiểm TLS, các header, quyền secret/collector và từ chối lạm dụng qua service đã dựng. Snapshot web chỉ dùng để xác minh HTTP policy của bộ phục vụ tệp. HSTS vắng mặt trong cấu hình staging cục bộ này.

## K16.05 — Thu thập, cảnh báo và diễn tập hồi phục

Collector chạy riêng với UID 10002. Công cụ điều phối trên host đọc snapshot safe JSON qua Docker và chuyển sang collector, không cấp Docker socket cho collector. Snapshot tiến trình được phát mỗi 15 giây. Sampler hàng đợi truy vấn PostgreSQL mỗi khoảng hai giây bằng role chỉ đọc. Bộ nhận chạy UID 10003, trên mạng Docker nội bộ, ghi receipt ra mount riêng và trả HTTP 204. Nó từ chối request thiếu bearer token bằng HTTP 403. Đây là kênh nhận dành cho diễn tập. Nhãn chủ trách nhiệm trong receipt là `kien-k16-staging`.

Các rule trong [runbook gốc](../../../../docs/k16-observability-runbook.md) vẫn dùng `AlertEvaluator`. Diễn tập này cố ý gây quá tải ở hàng đợi để kích hoạt `queue_age` (tuổi ≥30 giây) và `http_burn` (≥5× ngân sách lỗi 1%, tối thiểu 20 request trong cửa sổ). Collector giữ trạng thái firing của burn nếu cửa sổ tiếp theo có dưới 20 request. Cảnh báo pool wait, event-loop lag, worker error và log drops dùng định nghĩa hiện có. Mỗi rule đó cần sự cố tương ứng nếu muốn có receipt firing riêng.

### Thứ tự thao tác

1. Thu baseline của hai API, hai worker và hàng đợi. Gửi 120 yêu cầu với 120 result ID khác nhau. Đối chiếu chiều metric trên cả hai replica.
2. Dừng hai worker của staging K16. Gửi 72 POST preview từ workspace nóng, dùng khóa idempotency riêng. Cùng lúc đọc một workspace nền và gửi readiness liên tiếp. Ghi đủ 202/503, thời gian và `Retry-After`.
3. Chờ tuổi hàng đợi và burn đạt ngưỡng. Kiểm alert thực đến máy nhận và có receipt 204. Đối chiếu với snapshot đúng release.
4. Khởi động lại hai worker. Đối chiếu tất cả job được nhận với trạng thái terminal, số lần thử và hash của nguồn trong output. Gửi probe readiness theo tuần tự để tôn trọng một slot health trên mỗi API. Đối chiếu alert resolved từ cửa sổ có đủ mẫu và hàng đợi đã rút.

### Kết quả lượt Alerts 05

| Chỉ số | Đo được |
|---|---|
| Tải preview | 72 lượt ở 6 luồng, 8 HTTP 202 và 64 HTTP 503 có Retry-After 1 giây. Trung bình/p95/cao nhất **218,14 / 403,80 / 419,64 ms**. |
| Workspace nền và health dưới tải | **12/12** lượt đọc trả 200 và đúng workspace, độ trễ trung bình/p95/cao nhất **291,49 / 609,25 / 609,25 ms**. **6/6** readiness trả 200. |
| Burn khi quá tải | API 0 có **33/60** phản hồi 5xx, burn **55,00×**. API 1 có **31/60**, burn **51,67×**. Đây là tỷ lệ của cửa sổ thu thập từng API. |
| Tuổi hàng đợi lúc báo động | **8** job chờ, job cũ nhất **31,964 giây**. Sau khi worker nhận việc, số job trong hàng đợi và tuổi job chờ đều về **0**. |
| Khớp kết quả consumer | **8/8** job duy nhất `succeeded`, tất cả ở attempt 1, khớp SHA-256 nguồn trong output. Driver thấy drain xong sau **16,384 giây** tính từ lúc bắt đầu kiểm hồi phục. |
| Cửa sổ hồi phục | **120/120** readiness trả 200. Lượt đọc workspace nền sau hồi phục trả 200 trong **94,25 ms**. Cửa sổ burn về 0 có **0/65** lỗi trên API 0 và **0/66** trên API 1. |
| Bộ nhận cảnh báo | **6/6** receipt khớp thông báo gửi, mỗi thông báo có HTTP 204. Ba firing và ba resolved tương ứng với API 0, API 1 và hàng đợi. |
| Thời gian ứng phó | **33,375 giây** từ lúc bắt đầu tải đến thao tác khởi động lại worker. **23,984 giây** từ lúc bắt đầu thao tác hồi phục đến khi thu đủ bằng chứng hồi phục. |
| Cardinality | **120/120** result ID khác nhau trả 404, cùng family `results` và không có series mới. Tổng series trước/sau là **12/12** trên API 0, **15/15** trên API 1. |
| Tài nguyên quan sát | 50 bản quan sát của collector gồm 12 snapshot API, 6 snapshot worker và 32 mẫu hàng đợi. Pool API đỉnh 4, số chờ pool đỉnh 0, lag đỉnh tại mẫu **2,160 ms**, log drop đỉnh **0**. |

| Nguồn | Bên nhận xác nhận firing (UTC) | Bên nhận xác nhận resolved (UTC) |
|---|---|---|
| API 0 · http_burn | 10:57:00.636942 | 10:57:45.513735 |
| API 1 · http_burn | 10:57:00.720529 | 10:57:45.603976 |
| Hàng đợi · queue_age | 10:57:21.754802 | 10:57:34.154787 |

Các cửa sổ dùng chênh lệch bộ đếm tích lũy theo từng tiến trình và thời điểm quan sát monotonic của collector. Timestamps từ Docker và từ bộ nhận được lưu riêng. Tuổi hàng đợi lấy từ cột `created_at` trong PostgreSQL. Hàng đợi rỗng xác nhận worker đã lấy job. Bài kiểm tiếp tục đến khi đọc được trạng thái terminal và hash của đủ 8 job.

### Lịch sử lượt thử

| Lượt | Quan sát |
|---|---|
| Edge 01 | Hai API khởi động trước khi Keycloak hoàn tất import realm. Bài kiểm chờ readiness hết hạn. |
| Edge 02 | Khởi động lại hai API sau khi Keycloak sẵn sàng. Kiểm TLS, auth, tenant, CSRF, headers và abuse đạt theo bảng trên. |
| Static 01 | Asset và policy qua service thật đạt. Ba đường dẫn private/traversal bị từ chối. |
| Alerts 01 | Kiểm cách ly ban đầu nhầm thư mục cha có sẵn trong image với mount dữ liệu. Collector dừng trước khi phát tải. |
| Alerts 02 | UID collector bị từ chối đọc cấu hình nguyên cụm mode 0400. Đổi sang mount riêng chỉ chứa schema/release identity và CA, giữ quyền tệp ban đầu. |
| Alerts 03 | Nhận hai cảnh báo burn và cảnh báo queue age. Driver đọc nhầm khóa `state` thay cho `status` trong hợp đồng job. Giữ nguyên receipt lượt này. |
| Alerts 04 | Driver đọc đúng hợp đồng, kiểm trạng thái và hash đầu ra. Loạt 48 health probe đồng thời vượt slot health dành riêng, nên lượt hồi phục toàn trình chưa đạt. |
| Alerts 05 | Lặp lại trên cùng release, gửi health probe tuần tự. Exit 0, 6/6 receipt hữu hiệu và 8/8 job thành công đúng kết quả. Collector role được xóa sau lượt. |

### Tái lập và nghiệm thu môi trường chung

`delivery/kien-r418/tools/k16_edge_probe.py` chạy phép thử qua HTTPS. `k16_run_remaining.py` nhận đường dẫn tới state của staging K16 và một thư mục đầu ra mới. Công cụ điều phối dùng `k16_collector.py` cho collector và webhook thử. Thư mục `alerts-05` giữ receipt, các snapshot, quyền tệp thực tế, tải đầu vào và kết quả hồi phục. Ruff check và format check đạt trên ba tệp công cụ mới.

K16.03 còn bước ghi nhận domain/IdP, mọi header theo policy edge và quyền secret/collector trên chính môi trường chung. K16.05 còn bước kiểm đường cảnh báo tới người trực của nhóm và có người thứ hai thực hành runbook. Webhook nội bộ của lượt này xác nhận collector và routing kỹ thuật. Các receipt gắn chủ trách nhiệm thử và chưa có xác nhận của người trực thật.

Sau khi ghi chứng cứ, đã dừng đủ bảy container K16. Volume staging chỉ chứa vật liệu thử và được giữ để tái lập. Cả bảy service staging K14 có từ trước vẫn chạy, theo `staging-stop.json`.

Kết quả CI và chứng cứ của K16.01, K16.02 và K16.04 dùng từ các lượt trước. Các tệp mới nằm trong working tree. Chưa commit hay push.


## Chốt ngày 16/11/2026

K16 đạt 5/5 trong phạm vi staging LAN đã khai báo, có Quân nhận cảnh báo và thực hành khôi phục từ MacBook. Phần này cập nhật hai bước môi trường chung/người nhận còn mở trong lịch sử phía trên. Xem [kết quả chốt](lan-20261116/KET-QUA-CHOT-K16-20261116.md) và [kết quả server](lan-20261116/result.json).
