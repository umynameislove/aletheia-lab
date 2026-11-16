# K14.05 — chốt lỗi timeout phân tích S, 15/11/2026

**PASS trong phạm vi bài đo local:** 3 lượt tải liên tiếp đạt **287/287 hành trình, 0 timeout**; gồm **72/72 phân tích S dưới hạn 5 giây**, **147/147 job succeeded**, **140 export kiểm đủ byte/SHA**. Đây là kết quả sửa lỗi cụ thể đã phát hiện, không tự đóng cổng triển khai/primary của toàn K14.05.

Chưa commit, chưa push, không có tệp staged. HEAD vẫn `fa46dd8cbb2239c6f116456894a2009b45846245`. Toàn bộ 1,649 tệp source/test/config/UI trước phiên được đối chiếu byte; số chính xác ghi trong result.json. Runtime identity vẫn `acbe153cee890b3b5c7c1a8d1a36d100f9ba8cdbce6366005b511a9a7dc5a529`, giống ca lỗi gốc. Các thử nghiệm thay đổi hai facade đã được hoàn nguyên đúng byte; không giữ sửa đổi backend, frontend hay kiểm thử đã pass. Chỉ sửa công cụ chuẩn bị môi trường đo.

## 1. Nguyên nhân và bản sửa giữ lại

Ở ca gốc, ba phân tích đã hoàn thành trong DB sau 2,51–2,65 giây, nhưng polling/output observation vượt mốc 5 giây. Phân tích khởi động cho thấy worker one-shot đọc/nạp các module qua source Windows bind-mounted vào container. Mã gốc mất 2,494585 s ở đường này và 0,727037 s ở probe source trên Linux. Đây là một cặp probe quan sát, không phải ước lượng thống kê cho mọi máy. Các mốc job thật bên dưới được dùng để nghiệm thu.

Công cụ mới dùng bind mount Windows chỉ để nhận dữ liệu đầu vào. Trước khi pytest/worker chạy, nó chụp source/test/config được ghim SHA vào `/tmp/k14-native-source` trên hệ tệp Linux; kiểm hash nguồn và bản sao của từng tệp; đặt PYTHONPATH/cwd vào snapshot; tắt ghi bytecode rồi chạy lại đúng pytest cũ. Mỗi lượt chụp 1.663 tệp / 19.577.731 B, trong cùng cgroup và tmpfs đã có, không tăng quota. Copy chỉ diễn ra trước cửa sổ tải. Runtime source tiếp tục được kiểm không đổi sau bài đo.

Cách đặt mã phù hợp với nguyên tắc đóng gói của `deploy/hosted/Dockerfile`: COPY source, build wheel và cài vào venv Linux; compose chỉ mount config/object, không mount source Windows. Đây là fixture kiểm thử, không tuyên bố đã chạy chính image release hoặc staging. Tmpfs nhanh hơn nhiều loại đĩa thực; kết quả này không thay phép hiệu chuẩn trên deployment đích.

Giữ nguyên mã xử lý phân tích, phân quyền/CSRF, fencing/lease, kiểm scope, worker subprocess một lần/job, khoảng polling, ngưỡng 5 giây, dữ liệu và mức đồng thời. Không cache kết quả hay dùng stub để chuyển timeout thành thành công. Những job kết thúc sau hạn vẫn bị tính lỗi theo logic test cũ.

## 2. Phương pháp, thông số và tiêu chí

| Thông số | Giá trị |
| --- | --- |
| Image test ghim | sha256:6aed97ec4d6691527a72667ecdac67e4964ddcd6c1e4077aab9d8073a249b797 |
| App + generator + ingress | 4 vCPU, 3 GiB, 256 PID; nofile 512; root FS read-only; /tmp tmpfs 1,5 GiB, bao gồm bản sao source ~19,6 MB |
| PG riêng | PostgreSQL 16, 2 vCPU/1 GiB; max_connections 24 |
| API / worker | 2 API × pool 4; 2 worker × pool 2 × 2 job slot; worker timeout 90 s, lease 120 s |
| Ingress | TLS thực qua TCP round-robin, tối đa 32 connection |
| Dataset | 2 workspace, 6 project, 30 principal; dùng 6 owner S/M/L; nguồn thật 1k/10k/100k dòng; result native 2/16/64 claim |
| Noise | 3 vòng closed đồng thời: analyze S, preview M, export L; nghỉ 50 ms sau mỗi lượt |
| Tenant nền | Workspace khác luân phiên đọc header S/M/L, readiness qua cùng ingress và admin SELECT 1; nghỉ 50 ms sau chu kỳ |
| Thời gian | Chào tải 60 s/lượt, chờ lượt đang thực thi và drain job; mỗi lượt dùng PG/schema/process mới |
| Tiêu chí | Mọi offered hoàn tất; timeout/failure/reject/conflict/unknown = 0; DB job succeeded; output/export đúng scope và hash |
| SLO | Analyze S ≤5.000 ms; background p95 ≤500 ms; health/admin p95 ≤250 ms; các deadline và polling còn lại giữ nguyên |

Có một lượt chính rồi hai lượt lặp được ấn định, cùng source/snapshot hash. Khi một lượt lỗi, chuỗi thử dừng và không chạy tiếp để tìm một chuỗi xanh. Tốc độ là kết quả của vòng closed, không phải mức RPS tối đa được ấn định. Mỗi lượt là mẫu độc lập trên fixture mới; không gọi tổng này là một lượt 287 người dùng.

## 3. Kết quả cuối

| Lượt | Actual window s | Offered/completed | S count | S mean ms | S p95 ms | S max ms | Job succeeded | Export verified |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| native-01 | 62.713 | 95/95 | 24 | 2498.175 | 2724.547 | 2737.605 | 49 | 46 |
| native-02 | 61.905 | 96/96 | 24 | 2488.513 | 2617.526 | 2620.281 | 49 | 47 |
| native-03 | 61.923 | 96/96 | 24 | 2462.247 | 2580.162 | 2592.061 | 49 | 47 |

Gộp mô tả 72 phân tích S: mean 2482.978 ms, p95 quan sát 2634.963 ms, max 2737.605 ms, 0/72 timeout. Cả ba pytest integration đều exit 0; log và JUnit nằm trong từng thư mục native-01/02/03.

| Lượt | Job attempt analyze mean/max ms | DB job không thành công | Timeout/reject/unknown | OOM |
| --- | --- | --- | --- | --- |
| native-01 | 1321.498 / 1990.114 | 0 | 0 / 0 / 0 | False |
| native-02 | 1280.271 / 1494.286 | 0 | 0 / 0 / 0 | False |
| native-03 | 1317.416 / 1556.410 | 0 | 0 / 0 / 0 | False |

Attempt DB đo thời gian started→finished, không phải CPU. Các job đều thành công ở attempt 1, failure_kind null. Tất cả output cần dùng đã được phía gọi đọc và kiểm trước hạn; kết quả DB succeeded sau drain không được dùng thay điều kiện đó.

| Lượt | Đường | Samples | Mean ms | p95 ms | Max ms |
| --- | --- | --- | --- | --- | --- |
| native-01 | background | 424 | 65.059 | 140.043 | 328.200 |
| native-01 | health | 424 | 22.206 | 32.444 | 125.869 |
| native-01 | admin | 424 | 1.194 | 1.429 | 47.825 |
| native-02 | background | 428 | 63.364 | 132.545 | 342.880 |
| native-02 | health | 428 | 21.984 | 32.787 | 129.088 |
| native-02 | admin | 428 | 0.948 | 1.321 | 5.863 |
| native-03 | background | 427 | 63.626 | 136.968 | 344.549 |
| native-03 | health | 427 | 22.094 | 40.993 | 131.255 |
| native-03 | admin | 427 | 1.116 | 1.647 | 30.693 |

Mỗi đường background/health/admin có tổng 1279 mẫu. p95 dùng cùng quy tắc order statistic của helper cũ; số mẫu hữu hạn, không khẳng định p99 hay tỉ lệ lỗi production bằng 0.

| Lượt | HTTP harness | Response đủ | ACK 202 | Export 201 | Byte response | Business actions/s | HTTP/s gồm background + health |
| --- | --- | --- | --- | --- | --- | --- | --- |
| native-01 | 313 | 313 | 49 | 46 | 9278609 | 1.515 | 18.513 |
| native-02 | 315 | 315 | 49 | 47 | 9470823 | 1.551 | 18.916 |
| native-03 | 315 | 315 | 49 | 47 | 9470930 | 1.550 | 18.878 |

HTTP trên chỉ tính cửa sổ đo, không gộp preflight/session/startup vào workload. Không có transport retry hay 429/503 được ghi ở ba lượt cuối; tổng job admission khớp 147 dòng ledger và 147 job ID khác nhau, output của toàn bộ job đã kiểm. Các export có descriptor/byte/SHA khớp.

## 4. CPU, RAM, FD và phương pháp đo

| Lượt | Vai trò | CPU mean core | RSS tree peak mẫu MiB | FD tree peak mẫu |
| --- | --- | --- | --- | --- |
| native-01 | postgres | 0.3367 | 366.73 | không đọc được |
| native-01 | generator | 0.0638 | 201.37 | 25 |
| native-01 | api0 | 0.5060 | 238.28 | 20 |
| native-01 | api1 | 0.7243 | 238.85 | 20 |
| native-01 | worker0 | 0.5970 | 273.42 | 21 |
| native-01 | worker1 | 0.9481 | 340.78 | 22 |
| native-01 | ingress | 0.0115 | 36.52 | 16 |
| native-02 | postgres | 0.3423 | 363.45 | không đọc được |
| native-02 | generator | 0.0640 | 201.43 | 25 |
| native-02 | api0 | 0.6550 | 236.42 | 20 |
| native-02 | api1 | 0.5849 | 240.53 | 20 |
| native-02 | worker0 | 0.7849 | 317.02 | 22 |
| native-02 | worker1 | 0.7546 | 329.51 | 21 |
| native-02 | ingress | 0.0113 | 36.34 | 16 |
| native-03 | postgres | 0.3383 | 372.39 | không đọc được |
| native-03 | generator | 0.0651 | 203.74 | 25 |
| native-03 | api0 | 0.6687 | 238.56 | 20 |
| native-03 | api1 | 0.5662 | 238.50 | 20 |
| native-03 | worker0 | 0.8395 | 340.89 | 22 |
| native-03 | worker1 | 0.7364 | 340.81 | 22 |
| native-03 | ingress | 0.0118 | 36.29 | 16 |

CPU là delta /proc của cây đúng PID/start tick (kể cả child đã reap), generator tách riêng; trung bình trên cửa sổ snapshot, không ngoại suy capacity. RSS cộng cây có thể đếm trùng trang chia sẻ; không bao gồm toàn bộ cache tmpfs/OS và không lấy quota trừ RSS để tuyên bố headroom. Sampling có thể bỏ qua child rất ngắn. FD PG không đọc được do quyền, giữ null thay vì 0. Pool, FD từng process, ingress byte/connection và log telemetry 100 ms được giữ trong *.resources.jsonl. CPU/FD/NIC triển khai thật không được suy từ bài nhỏ này.

## 5. Lịch sử điều tra và bảo toàn bằng chứng

| Lượt | Cấu hình | Completed/offered | Timeout | Ghi chú |
| --- | --- | --- | --- | --- |
| Trước sửa / attempt-08 | Source Windows, mã gốc | 52/55 | 3 | 30/30 succeeded |
| after-01 | Thử lazy product | 62/66 | 4 | Đã hoàn nguyên; không phải bản sửa cuối |
| after-02 | Thử lazy product + project | 81/81 | 0 | Đã hoàn nguyên; không phải bản sửa cuối |
| after-03 | Lặp lazy product + project | 73/74 | 1 | Đã hoàn nguyên; không phải bản sửa cuối |
| native-01 | Mã gốc, source Linux cùng SHA | 95/95 | 0 | 49/49 succeeded |
| native-02 | Mã gốc, source Linux cùng SHA | 96/96 | 0 | 49/49 succeeded |
| native-03 | Mã gốc, source Linux cùng SHA | 96/96 | 0 | 49/49 succeeded |

Các thử nghiệm lazy import đầu tiên chưa giải quyết ổn định, nên đã hoàn nguyên cả hai facade về đúng byte gốc. Test mới dùng để kiểm thử hướng thử nghiệm đó cũng đã được lưu trong archive rồi gỡ khỏi cây tests. 47 test hồi quy và kiểm 92 export từng chạy đạt ở hướng thử nghiệm; chúng không được cộng vào số test nghiệm thu của bản sửa môi trường cuối. regression-paths.txt là danh sách dự kiến cho hướng đã bỏ, chưa chạy cả danh sách.

Hồ sơ cũ k14-missing-demand được kiểm toàn bộ hash và giữ nguyên, gồm cả ba timeout gốc. Những lượt failed không được đổi trạng thái. Báo cáo này thay thế riêng kết luận “ba timeout phân tích S chưa xử lý” trong phạm vi local, không thay trạng thái toàn bộ K14.01/K14.05 hay K01–K18. Đây chưa phải hồ sơ nghiệm thu tổng gửi Bảo.

## 6. Chốt kiểm tra, tái lập và giới hạn

- Đối chiếu tất cả tệp source/test/config/UI với baseline đầu phiên: đúng byte; Git diff trên các cây đó rỗng, HEAD không đổi. Test tải delivery/kien-r418/tests/test_k14_missing_demand.py cũng khớp hash của lượt lỗi gốc; chỉ driver chuẩn bị source thay đổi.
- Negative guard: đưa SHA sai vào manifest; Docker exit 1 đúng dự kiến, từ chối trước khi pytest/API/job chạy. Guard được tính PASS vì chặn đúng source drift. Ruff check và format --check cuối đều PASS.
- Ba cửa sổ cuối đều đạt, không có OOM, nguồn native không đổi, không còn job nonterminal. Container/volume PostgreSQL của lượt thử được guard dọn; kiểm không còn container theo nhãn riêng. Không dừng/xóa container người dùng khác.
- reproduction/ lưu đúng driver và test đã chạy, kiểm khớp inputs-before. Mỗi thư mục native giữ đầy đủ result, demand-result, manifest source, profile/seed, JUnit, log và telemetry. CSV all-actions/job-timeline/resource-summary giữ các mẫu để đối chiếu.
- Tái lập: dùng Python có deps dự án, image test ghim đã có, đặt K14_MEASURE_PHASES=mixed-only rồi chạy driver với --output trỏ thư mục mới. Driver tự chụp source, kiểm hash và dọn tài nguyên. Không dùng lại thư mục bằng chứng cũ.
- Chưa commit/push và chưa chạy CI mới theo yêu cầu. Chưa kiểm trên staging/production, wheel/image release cuối hay 50k user. Dataset chưa đạt geometry primary. Nếu chạy lại worker trực tiếp trên bind source Windows, độ trễ I/O đã quan sát vẫn có thể lặp lại; sử dụng môi trường có source cài trên Linux để áp dụng kết quả này.
