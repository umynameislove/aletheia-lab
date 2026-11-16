# K17.01 — Tích hợp model phân tích cửa sổ quan sát

Ngày 15/11/2026. Worktree `product-api`, SHA nền `fa46dd8cbb2239c6f116456894a2009b45846245` cộng các thay đổi cục bộ. **PASS phần tích hợp model vào tuyến sản phẩm cho ba cỡ mẫu S/M/L.** Việc nhập catalog 2 triệu kết quả và đo tải 50.000 principal vẫn là các bước riêng.

Model `upload-snapshot-windowed-metric-observation/v3` được chọn qua `api.analysis_model` sau migration 18. Job lưu phiên bản model khi nhận yêu cầu. Mỗi claim tổng hợp min, max, mean của tối đa 10 quan sát cùng source mapping, mục dữ liệu, run, metric, class và unit. Evidence giữ vị trí bản ghi, SHA-256 của các toán hạng, SHA-256 của ZIP nguồn và tham chiếu đến snapshot đã lưu. Tính toán dùng biểu diễn số hữu hạn trong snapshot. Trường hợp mean là phân số thập phân vô hạn, báo cáo giữ tử số và mẫu số. Kết luận nhân quả ở trạng thái `unverified`.

Model dùng lại hai phiên bản schema ProductView hiện có. Job phân tích trả tham chiếu kết quả. Trình duyệt mở header, claims, evidence và lineage qua các API phân trang có kiểm tra quyền. Báo cáo JSON, Markdown và PDF lấy từ kết quả đầy đủ. Worker kiểm tra dữ liệu ở đầu vào và bên ghi kiểm chứng bằng cách tính lại từ snapshot đã lưu trước khi công bố.

| Cỡ | Quan sát | Claim / evidence | Node / cạnh | ZIP nguồn (byte) | Snapshot (byte) | Bản ghi kết quả (byte) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| S | 1.000 | 100 / 100 | 202 / 300 | 44.105 | 339.614 | 309.205 |
| M | 10.000 | 1.000 / 1.000 | 2.002 / 3.000 | 432.388 | 3.393.079 | 3.075.894 |
| L | 100.000 | 10.000 / 10.000 | 20.002 / 30.000 | 4.349.126 | 34.107.729 | 30.841.703 |

Các số byte trên đo bằng bộ kiểm thử hình học trên Windows, bao gồm toàn bộ nội dung đã tuần tự hóa. `geometry.log` ghi kích thước, thời gian và SHA-256 của từng định dạng báo cáo. `geometry.xml` có ba ca kiểm thử.

Mẫu ZIP có khai báo class/unit và tên tiếng Việt tạo 10 claim từ 10 nhóm quan sát trên 5 run. Ca này kiểm snapshot v2, ProductView v2 và bản ghi kết quả v2. Bằng chứng nằm trong `v2-dimensions.json`.

Phép thử tích hợp dùng HTTPS, một API và một tiến trình worker riêng, PostgreSQL độc lập với vai trò API/worker giới hạn quyền và phiên synthetic có xác thực. Tiến trình chạy Python 3.12 phía Linux trong Docker, dùng 4 CPU và giới hạn RAM 2 GiB cho nhóm tiến trình kiểm thử. PostgreSQL dùng 2 CPU. Đọc claim tối đa 50 mục/trang và lineage tối đa 100 node/trang. Thời gian phân tích đo từ sau HTTP 202 đến khi đọc được output, gồm khoảng chờ giữa các lần kiểm tra trạng thái. Cấu hình thử dùng backend timeout 60 giây, request timeout 90 giây và giới hạn phản hồi JSON 1 MiB. Trình duyệt Chrome được kiểm riêng với bộ phản hồi API kiểm soát được để xác minh điều hướng, tải lại, chuyển trang lineage và thao tác xuất JSON. HTTPS tích hợp tự kiểm toàn bộ byte báo cáo độc lập bằng SHA-256.

Các lần thử lỗi được giữ lại. Lần 01 cỡ L hết 240 giây trước khi có kết quả. Lần 02 giao dịch công bố cỡ L chạm giới hạn 5 giây. Lần 03 bộ kiểm thử gửi `limit=200` trên tuyến giới hạn 100 nên nhận HTTP 400. Lần 04 dùng đúng giới hạn API.

| Cỡ | Confirm đến đọc output (s) | Analyze đến đọc output (s) | Trang claim | Trang graph | Kiểm quyền ngoài workspace |
| --- | ---: | ---: | ---: | ---: | ---: |
| S | 2,46 | 3,14 | 2 | 7 | HTTP 404 |
| M | 11,31 | 18,08 | 20 | 62 | HTTP 404 |
| L | 100,59 | 162,03 | 200 | 610 | HTTP 404 |

| Cỡ | JSON byte / giây | Markdown byte / giây | PDF byte / giây |
| --- | ---: | ---: | ---: |
| S | 308.920 / 0,97 | 310.083 / 0,87 | 202.028 / 1,33 |
| M | 3.075.609 / 2,23 | 3.083.072 / 2,25 | 1.880.090 / 5,74 |
| L | 30.841.418 / 18,43 | 30.911.881 / 24,02 | 18.697.727 / 55,73 |

Thời gian export tính từ lúc yêu cầu tạo đến lúc tải và kiểm SHA-256. Cả chín lần đều khớp SHA-256 và đủ byte. Kết quả phân tích trả tham chiếu 465 byte ở cả ba cỡ. Phản hồi graph lớn nhất 49.793 byte và phản hồi danh sách claim lớn nhất 38.614 byte. Cỡ L đọc cả 20.002 node và 30.000 cạnh, gồm đối chiếu điểm đầu/cuối của từng cạnh. Evidence lấy từ claim mẫu ở mỗi cỡ khớp SHA-256 nguồn và snapshot. Bài tích hợp ghi 1.217 lượt yêu cầu xác minh có HTTP 2xx, ngoài ba lượt từ chối truy cập khác workspace. `native-pipeline-04/window-pipeline.json` lưu từng lượt, các SHA-256 và số đếm. Tổng thời gian pytest 493,67 giây, gồm bài nâng cấp schema.

Lượt đọc output của confirm cỡ L mất 30,47 giây. Phép thử HTTPS xác nhận backend trả thành công. UI có thời hạn riêng 65 giây cho lượt này khi máy chủ bật model mới. Bộ kiểm thử client xác nhận đường xử lý này và Chrome kiểm tra luồng điều hướng. Kết quả thời gian ở trên là số đo của môi trường kiểm thử, dùng để mô tả tuyến sản phẩm ở ba cỡ dữ liệu.

Migration 17 → 18 đã chạy trên PostgreSQL và giữ nguyên 17 hàng lịch sử, gồm checksum và chữ ký cấu trúc. Chạy lại migration trả `unchanged`. `migration-checksums.json` đối chiếu 17 mã SHA-256 của DDL với dữ liệu lưu trước khi sửa.

Kết quả hồi quy trên máy gồm 63 ca backend, 361 ca UI và 3 ca Chrome. Bản build hosted, Ruff, ngân sách maintainability và xuất OpenAPI qua kiểm tra. Bản thử OpenAPI xác minh 102 thao tác và 1.097 tham chiếu nội bộ. Thay đổi vẫn ở worktree cục bộ.
