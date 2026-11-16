# K14.01 và K14.05 — kết quả bổ sung ngày 15/11/2026

**Chưa đóng toàn bộ hai mục.** Đã bổ sung đo CPU theo hành trình/process, hoàn thành 68/68 hành trình còn thiếu ở lượt 06 và đo tải hỗn hợp thật. Lượt tải hỗn hợp mới nhất (08): **52/55 hành trình trong hạn, 3 timeout phân tích S**. Tenant nền, health và quản trị đạt ngưỡng p95. Toàn bộ 30 job trong DB của lượt 08 succeeded sau drain; job thành công không xóa timeout phía gọi. Không đổi timeout thành thành công.

Đây là hồ sơ kiểm thử kỹ thuật nội bộ của Kiên, chưa phải nghiệm thu tổng K01–K18 gửi Bảo. Chưa commit, chưa push; HEAD vẫn `fa46dd8cbb2239c6f116456894a2009b45846245` trên `product-api`. Không sửa `src`, `tests` gốc, cấu hình hay UI; chỉ thêm công cụ/bài đo K14 trong `delivery` và bằng chứng mới. Các thay đổi K03/K09.03 có sẵn trước phiên được giữ nguyên.

## 1. Đối chiếu tiêu chí

| Mục | Đã đo thêm | Còn mở |
| --- | --- | --- |
| K14.01 | CPU trước/sau theo PG, 2 API, 2 worker, ingress, generator; RAM/FD và pool trong tải hỗn hợp | Ngân sách trên topology triển khai thật, đầy đủ workload/operation, NIC và headroom từng thành phần; chưa có staging riêng |
| K14.05 | Trộn phân tích S + preview M + export L; tenant nền đọc S/M/L; health/admin đồng thời; đếm timeout và drain job | Phân tích S còn timeout ở tải hỗn hợp; chưa chứng minh SLO ổn định và fairness trên triển khai/workload đích |

CPU được quy thuộc cho **toàn hành trình và giao thức hoàn tất**, gồm nhiều endpoint khi có job/poll/download; không giả định đây là số CPU độc lập cho từng endpoint P07/P08/P20. K14.01 vì thế chưa thể coi là hoàn tất toàn bộ operation chỉ từ bảng này.

## 2. Môi trường và ngân sách

Docker Desktop/WSL2: 16 CPU logic, 8.135.200.768 B RAM của VM; còn workload khác của người dùng, không dừng hay chỉnh chúng. Image test được ghim `sha256:6aed97ec4d6691527a72667ecdac67e4964ddcd6c1e4077aab9d8073a249b797`, Python 3.12; bind source hiện tại chỉ đọc. PostgreSQL 16 riêng, tên/nhãn/port/image được xác minh trước khi thao tác và tự dọn sau lượt thử.

| Thành phần | Cấu hình |
| --- | --- |
| Ứng dụng + generator + ingress | Chung cgroup 4 vCPU / 3 GiB / 256 PID, nofile 512, root FS chỉ đọc, /tmp tmpfs 1,5 GiB |
| API | 2 process, pool tối đa 4/process; HTTPS qua ingress TCP round-robin |
| Worker | 2 process, pool tối đa 2/process, 2 slot/process; worker timeout 90 s, job lease 120 s |
| PostgreSQL | Cgroup riêng 2 vCPU / 1 GiB; fixture max_connections=24 |
| Điều khiển/đo | Pool chủ tối đa 2 + 1 kết nối giữ context schema. Trần runtime 2×4 + 2×2 + 2 + 1 = 15; không nhầm thành 14 hoặc coi 24 là ngân sách production |
| Ingress | Tối đa 32 connection; số byte TLS được ghi riêng, không phải đo NIC ngoài host |
| Máy phát | Cùng cgroup/host với ứng dụng, đo riêng PID; không phải generator độc lập đã hiệu chuẩn |

PG và bài đo chia sẻ network/PID namespace để đọc đúng cây process DB; chúng vẫn khác cgroup. CPU/RSS/FD lấy ở đúng PID/start tick, không cộng generator với các API/worker con thêm lần nữa. FD của PG bị giới hạn quyền đọc nên ghi **không đo được**, không thay bằng 0.

## 3. Dữ liệu và phương pháp

Seed mới cho mỗi lượt: 2 workspace, 6 project, 30 principal/session tổng; 6 owner S/M/L được dùng. Một workspace tạo tải, workspace còn lại đọc nền. Native source vật lý S/M/L có 1.000/10.000/100.000 dòng; result vẫn 2/16/64 claim, graph 6/34/130 node. **Không đạt kích thước primary 100/1.000/10.000 claim hay L ≥10.000 node; không có bài 50.000 user.** `seed-manifest.json` giữ kích thước ZIP/result, inventory và hash vật lý; từ lượt 05 profile buộc đúng SHA-256 byte của manifest.

Mỗi pha chạy một hành trình tại một kích thước, không có foreground khác; 20 lượt cho read/review, 2 lượt cho job/export. Ghi CPU trước/sau cho từng vai trò, cả CPU child đã reap; có self-check child thực chạy CPU ≥0,1 s. Độ phân giải /proc là 0,01 CPU-s. Không trừ heartbeat/DB maintenance: đây là **gross CPU**, rất nhạy nhiễu khi mẫu nhỏ.

`CPU trung bình trên lượt chào = ΔCPU / offered`; `λ quan sát = offered / thời gian pha`; `nhu cầu CPU trung bình = λ × CPU/lượt`. Giữ cả lượt lỗi/timeout trong mẫu số CSV. Đây là tốc độ tuần tự quan sát, không phải công suất tối đa, dự báo p99 hay chứng minh Little’s Law khi hệ thống bão hòa. Khi hành trình lỗi, bảng phải giữ cờ lỗi; không dùng mẫu chi phí của một partial request để tuyên bố capacity thành công.

Tải hỗn hợp: ba vòng tuần tự đồng thời cho phân tích S, preview M và export L, nghỉ 50 ms sau mỗi hành trình; bắt đầu cùng lúc, ngừng chào sau 60 s rồi đợi lượt đang chạy. Tenant nền luân phiên đọc header S/M/L, readiness qua cùng TLS ingress và admin SELECT 1; nghỉ 50 ms sau một chu kỳ. Đây là 3 vòng closed nhỏ, không phải open-loop/RPS cố định hay thử burst lại phần đã pass.

Ngưỡng không đổi: background p95 ≤500 ms; health/admin p95 ≤250 ms; read 0,5 s, graph 1 s, review 2 s, export 10 s, analyze/follow-up S/M/L 5/20/120 s, preview 120 s. Polling vẫn theo server, không tăng deadline, sửa số lỗi thành pass hoặc thay bằng stub. Export phải tải đủ và khớp byte/SHA; job phải có output đúng scope. p95 là quantile quan sát trên số mẫu hữu hạn, không khẳng định p99.

## 4. Toàn bộ lần thử — không xóa lượt lỗi

| Lượt | Pha đã ghi | Offered / completed của pha riêng | Pytest exit | Thời gian wrapper (s) | Kết luận |
| --- | --- | --- | --- | --- | --- |
| attempt-01 | 0 | 0 / 0 | 1 | 89.250 | failed |
| attempt-02 | 8 | 160 / 159 | 1 | 71.953 | failed |
| attempt-03 | 51 | 804 / 315 | 1 | 226.703 | failed |
| attempt-04 | 46 | 740 / 738 | 1 | 169.344 | failed |
| attempt-05 | 46 | 740 / 737 | 1 | 168.969 | failed |
| attempt-06 | 7 | 68 / 68 | 1 | 228.250 | failed |
| attempt-07 | 0 | 0 / 0 | 1 | 161.344 | failed |
| attempt-08 | 0 | 0 / 0 | 1 | 129.359 | failed |

Các con số pha riêng ở bảng không bao gồm mixed; xem các bảng mixed phía dưới. Toàn bộ 8 lượt pytest mới đều failed; chỉ nhận đúng slice đã hoàn thành, không gọi toàn lượt pass.

- 01: thiếu trường database_url_env trong cấu hình bài đo; chưa có pha. Raw demand-status còn ghi running do lỗi ghi trạng thái ban đầu; wrapper/exit/JUnit xác định **failed**, không phải đang chạy.
- 02: project_current M có 1 HTTP 503/20; nguyên nhân 503 chưa được chứng minh. Đồng thời phát hiện cách đo generator cộng trùng CPU tiến trình con, nên không nhận CPU lượt 02–03 làm bằng chứng.
- 03: project_current L, graph S, follow-up S có timeout. Thiếu composition workspace route gây 404 ở workspace_directory, stop dây chuyền các hành trình sau. Mixed của lượt này không phải tải hợp lệ vì noise bị harness stop; không nhận số latency đẹp ở lượt này để chốt fairness.
- 04: sửa phân chia PID/CPU và ghép route workspace trong **fixture mới**; 45 pha đầu 738/738 hoàn tất. Dừng tại preview vì fixture chưa ghép route preview.
- 05: thêm migration/grant/preview runtime đúng helper production; phân tích S 1/2 timeout ở 5.000,600 ms; preview job failed do bài đo chưa tạo private worker root. Giữ 737/738 hành trình trước preview, không xóa timeout.
- 06: chỉ chạy lại pha thiếu hoặc chưa đạt: phân tích S + preview S/M/L + review S/M/L, **68/68 hoàn tất**, không chạy lại 44 pha đã đạt của lượt 05. Mixed 46/50 trong hạn, 4 timeout. Teardown fixture drop schema khi worker còn sống gây deadlock và che lỗi assertion ban đầu; console/pytest giữ cả hai. Không sửa production để xử lý lỗi teardown này.
- 07: chỉ đo lại mixed để phân loại timeout, không chạy lại 68 hành trình đã đạt. Sửa thứ tự teardown: dừng/reap các process và task trước khi drop schema; drain job vẫn giữ timeout phía gọi là lỗi. Lượt này **52/53 trong hạn, 1 timeout phân tích S**, không còn lỗi deadlock che kết quả.

`hanh-trinh-moi-lan-thu.csv` ghi mọi pha; `cpu-theo-hanh-trinh-va-process.csv` ghi toàn bộ delta và mẫu số. Có mẫu thành công cho 51 cặp hành trình×kích thước từ các lượt khác nhau, **không phải một cohort 804/804 hoặc 51/51 toàn lượt pass**. Lượt 04/05/06 khác composition và manifest; không gộp chúng thành một nghiệm thu hiệu năng ổn định.

## 5. Hai cửa sổ mixed hợp lệ

### Lượt 06

Cửa sổ kể cả hoàn tất lượt cuối: 63.245 s; offered 50, completed 46, timeout 4, tỷ lệ timeout 8.000%. Không có reject/transport retry được ghi trong mixed mới nhất. Số đo request của harness loại trừ health/session/background/admin; các mẫu đó ghi riêng.

| Đường đo | Số mẫu | Mean ms | p95 ms | Max ms | Ngưỡng p95 ms |
| --- | --- | --- | --- | --- | --- |
| background | 393 | 66.819 | 137.655 | 375.218 | 500 |
| health | 393 | 31.618 | 58.439 | 259.216 | 250 |
| admin | 393 | 1.162 | 1.444 | 73.879 | 250 |

### Lượt 07

Cửa sổ kể cả hoàn tất lượt cuối: 64.174 s; offered 53, completed 52, timeout 1, tỷ lệ timeout 1.887%. Không có reject/transport retry được ghi trong mixed mới nhất. Số đo request của harness loại trừ health/session/background/admin; các mẫu đó ghi riêng.

| Đường đo | Số mẫu | Mean ms | p95 ms | Max ms | Ngưỡng p95 ms |
| --- | --- | --- | --- | --- | --- |
| background | 460 | 52.054 | 96.471 | 345.614 | 500 |
| health | 460 | 24.312 | 37.232 | 143.726 | 250 |
| admin | 460 | 1.099 | 1.317 | 68.196 | 250 |

### Lượt 08

Cửa sổ kể cả hoàn tất lượt cuối: 63.651 s; offered 55, completed 52, timeout 3, tỷ lệ timeout 5.455%. Không có reject/transport retry được ghi trong mixed mới nhất. Số đo request của harness loại trừ health/session/background/admin; các mẫu đó ghi riêng.

| Đường đo | Số mẫu | Mean ms | p95 ms | Max ms | Ngưỡng p95 ms |
| --- | --- | --- | --- | --- | --- |
| background | 450 | 56.753 | 125.767 | 429.745 | 500 |
| health | 450 | 23.458 | 31.499 | 107.483 | 250 |
| admin | 450 | 0.836 | 1.239 | 2.266 | 250 |

### Chi tiết lượt 07

| Hành trình | Kích thước | Kết quả | Số lượt |
| --- | --- | --- | --- |
| analysis_terminal | S | completed | 13 |
| analysis_terminal | S | timeout | 1 |
| export_whole_file | L | completed | 25 |
| preview_lease | M | completed | 14 |

Timeout duy nhất: sequence 24, analysis_terminal S, 5,000302241 s so với deadline 5 s. Sau đó đối chiếu DB: 28/28 job succeeded, drain thêm 0,002182 s; không còn job nonterminal. Harness đã quan sát output terminal 27 job trước hạn, 1 lượt hết hạn vẫn tính timeout. Không đồng nhất “DB succeeded” với “hành trình dùng được đúng hạn”.

HTTP của harness lượt 07: {"job_poll_resync": 27, "job_poll_snapshot": 51, "request_body_bytes": 4969, "requests": 199, "response_body_bytes": 5070606, "response_completed": 199, "response_duration_microseconds": 64880901, "status_200": 146, "status_201": 25, "status_202": 28}

25 export đã được kiểm đầy đủ byte/SHA. 28 ACK job, 199 request/199 response đầy đủ; 202×28, 201×25, 200×146. Không có phản hồi 429/503 hay lỗi scope/harness fatal ở lượt 07. Bảng `tai-hon-hop-tung-hanh-trinh.csv` giữ riêng thời gian và kết quả của tất cả 53 lượt.

Telemetry worker:

| Operation | Count | Mean ms | Min ms | Max ms | Outcome |
| --- | --- | --- | --- | --- | --- |
| analyze | 14 | 2474.857 | 2252 | 2761 | settled |
| preview | 14 | 2436.643 | 2260 | 2722 | settled |

Suy luận cần giữ đúng giới hạn: worker thực thi job khoảng 2,3–2,8 s, còn protocol polling yêu cầu đợi 2,0–3,499 s kèm jitter. Việc DB hoàn tất và phía gọi vượt 5 s phù hợp với trễ queue/compute/polling. Lượt 07 chưa có timeline ACK→claim→commit→poll; lượt 08 đã bổ sung timeline bên dưới và xác nhận độ trễ quan sát polling/output sau khi DB hoàn tất; **chưa có căn cứ sửa deadline, giảm interval trái policy hay sửa backend tùy đoán**.

## 6. CPU, RSS, FD và pool trong mixed

| Lượt | Vai trò | Mẫu | CPU trung bình (core) | RSS tree đỉnh mẫu (MiB) | FD tree đỉnh mẫu |
| --- | --- | --- | --- | --- | --- |
| attempt-06 | postgres | 393 | 0.3091 | 372.96 | không đọc được |
| attempt-06 | generator | 393 | 0.0601 | 201.12 | 25 |
| attempt-06 | api0 | 393 | 0.6963 | 244.75 | 23 |
| attempt-06 | api1 | 393 | 0.1888 | 233.91 | 20 |
| attempt-06 | worker0 | 393 | 0.6169 | 339.09 | 23 |
| attempt-06 | worker1 | 393 | 0.4518 | 342.64 | 23 |
| attempt-06 | ingress | 393 | 0.0110 | 36.23 | 16 |
| attempt-07 | postgres | 460 | 0.2638 | 347.24 | không đọc được |
| attempt-07 | generator | 460 | 0.0593 | 310.76 | 25 |
| attempt-07 | api0 | 460 | 0.4329 | 234.74 | 20 |
| attempt-07 | api1 | 460 | 0.4003 | 234.63 | 20 |
| attempt-07 | worker0 | 460 | 0.5617 | 327.03 | 22 |
| attempt-07 | worker1 | 460 | 0.4329 | 342.55 | 23 |
| attempt-07 | ingress | 460 | 0.0099 | 36.15 | 16 |
| attempt-08 | postgres | 450 | 0.2791 | 335.24 | không đọc được |
| attempt-08 | generator | 450 | 0.0599 | 306.76 | 25 |
| attempt-08 | api0 | 450 | 0.4282 | 238.26 | 20 |
| attempt-08 | api1 | 450 | 0.4048 | 234.77 | 20 |
| attempt-08 | worker0 | 450 | 0.5619 | 328.44 | 22 |
| attempt-08 | worker1 | 450 | 0.5134 | 332.73 | 23 |
| attempt-08 | ingress | 450 | 0.0103 | 35.98 | 16 |

CPU trung bình tính trên hai snapshot đầu/cuối của cửa sổ sampled, không lấy quota trừ mean để tuyên bố headroom production. RSS cộng cây có thể đếm trùng trang chia sẻ; lấy mẫu có thể bỏ lỡ child rất ngắn. Log getrusage giữ lifetime peak, có thể chứa peak trước exec; bảng trên dùng RSS hiện tại từ /proc tại từng mẫu. FD mỗi process có trần 512; không so FD toàn cây với trần một process.

| Vai trò monitor 07 | Số mẫu | FD process peak | Pool size peak | Pool waiting peak | Blocking waiting peak |
| --- | --- | --- | --- | --- | --- |
| api0 | 649 | 17 | 2 | 0 | 0 |
| api1 | 653 | 17 | 2 | 0 | 0 |
| ingress | 682 | 17 | n/a | n/a | n/a |
| worker0 | 654 | 16 | 2 | 0 | 0 |
| worker1 | 654 | 16 | 2 | 0 | 0 |

Ingress cuối lượt 07: `{"accepted": 27, "active": 3, "peak_active": 4, "rejected": 0, "to_api_bytes": 517475, "to_client_bytes": 6073997}`. Đây là byte chuyển tiếp TLS và connection của ingress, không phải RX/TX NIC ngoài host. Không có phép đo đồng thời tổng pg_stat_activity mới để thay số đỉnh connection của lượt cũ.

## 7. Bằng chứng local đã pass được giữ nguyên

Không chạy lại hay sửa các bộ sau:

- Windows K14: 20 passed trong 53,81 s (wrapper 57,812 s).
- Queue-pressure-3, HEAD cũ `49df44f610d148c27a520d601de6f486b5c4b335`: 2 API + 2 worker; runtime connection peak 8; cấu hình 14/16 gồm admin reserve 2 của **bài cũ**. Readiness 480/480 p95 20,41 ms; admin 478/478 p95 1,55 ms. App+generator mean 1,854 core/RAM peak 767 MiB; PG mean 0,265 core/RAM peak 201,35 MiB.
- Hai burst cũ: 22 job khác nhau hoàn tất, 66 ACK 202, 2 phản hồi 503 dự kiến khi đầy queue; same-key replay không tạo job trùng. Tenant nền vào hai claim đầu và hoàn tất trước noisy queue cạn. Background 478/478 p95 153,77 ms; hot L 194/194 p95 137,52 ms; 6 reconnect p95 54,53 ms.
- Bài cũ đã ghi FD/ingress/network PG: ingress 4/32 connection, 0 reject, 0 còn mở sau drain; PG RX 23.896.341 B/TX 87.196.668 B. Không gắn các số cũ này thành kết quả đo mới trên fa46dd8.

`prior-evidence-references.json` lưu đường dẫn đầy đủ và SHA-256 của hồ sơ cũ, không chỉnh nội dung hay trạng thái các lượt đã pass.

## 8. Việc thực tế còn thiếu để đóng hai mục

1. Timeline đã chứng minh phân tích S hoàn tất trong DB sau 2,51–2,65 s nhưng đường polling/output hiện tại chưa bảo đảm toàn hành trình ≤5 s: lượt 07 timeout 1/53, lượt 08 timeout 3/55. Cần đáp ứng ngân sách end-to-end trên cấu hình đích với giao thức đang công bố. Không tự nâng SLO hay giảm poll interval đang là policy của phần đã pass; không được tuyên bố K14.05 pass hoàn toàn.
2. Chưa có staging riêng hay topology được nhóm chốt. Cần môi trường được phép thử với số replica/process và quota CPU/RAM/FD/NIC, DB/admin budget, ingress/proxy, workload vật lý S/M/L và máy phát riêng; sau đó đo đồng thời toàn bộ fleet cùng kết quả thực thi/deadline, burst/cold-warm và tenant nền. Không yêu cầu mật khẩu/token trong chat.
3. Workload hiện tại chỉ là 17 recipe debug (51 cặp S/M/L), chưa phải toàn bộ hành vi sản phẩm hay primary dataset. Đóng ngân sách/fairness cho toàn tải đích cần chính workload và geometry đã khai báo; không bù số bằng bản ghi trùng hay ngoại suy 3 vòng lên 50k.

Chưa đánh dấu 2 mục K14 này PASS; số mục cá nhân đã đóng ở bảng kiểm trước chưa được tăng. Không triển khai, sửa K17 hoặc xuất nghiệm thu tổng cho Bảo trong phiên này.

## 9. Tái lập và kiểm tra tính toàn vẹn

Bản công cụ đúng byte đã chạy lượt 06/07/08 được lưu ngay trong thư mục mỗi lượt, khớp `inputs-before.json`; các lượt 01–05 chỉ giữ hash phiên bản công cụ, không có snapshot byte cũ đầy đủ. Các log/result/JUnit của mọi lượt vẫn còn. `inputs-before.json` và `inputs-after.json` chứng minh toàn bộ source/test/config/UI được theo dõi không đổi trong mỗi lượt.

Công cụ hiện tại mặc định chạy 7 pha resume-missing rồi mixed. Đặt `K14_MEASURE_PHASES=mixed-only` để chỉ đo mixed. Chạy bằng Python có dependency dự án, truyền `--output` là thư mục mới chưa tồn tại; không tái dùng thư mục bằng chứng cũ. Khi chuyển checkout khác cần giữ đúng các helper được fingerprint và image ghim, không chỉ copy một file test.

Ruff check và format --check đều đạt; xem validation.log. Self-check CPU child đã reap đạt ở mọi lượt. Các pytest mới **chưa pass toàn lượt**, không dùng Ruff để thay nghiệm thu tích hợp. Không chạy lại toàn suite/CI vì chưa commit/push và không sửa production.

Hai loại container thuộc bài đo đã được kiểm không còn sau dọn; PG context xác nhận đã dọn container/volume của chính lượt đó. Không quét/xóa container người dùng khác. `cleanup-and-scope.json` ghi kiểm tra cuối. `SHA256SUMS.txt` và manifest.json niêm phong các file bằng chứng hiện có; hash chỉ phát hiện thay đổi, không thay chữ ký/review độc lập.


## 10. Lượt chẩn đoán 08 — timeline xác định nguyên nhân timeout

Chỉ đo mixed, không chạy lại các pha riêng đã hoàn thành. 55 offered, 52 completed, 3 timeout (5,455%); 25 export verified, 30 ACK job, 30/30 job succeeded ở attempt 1. Không có failed/authorization failure, HTTP 429/503 hay transport retry được quan sát ở lượt này. Toàn bộ pytest vẫn FAILED vì giữ nguyên deadline 5 s.

| Sequence | Queue (ms) | Attempt (ms) | DB terminal sau bắt đầu HTTP (ms) | Action timeout (ms) |
| --- | --- | --- | --- | --- |
| 1 | 96.841 | 2435.293 | 2581.542 | 5003.026 |
| 17 | 29.206 | 2457.354 | 2507.771 | 5000.825 |
| 38 | 70.460 | 2564.245 | 2647.785 | 5001.568 |

Anchor DB clock có nửa RTT khoảng 2,333 ms; chênh lệch 2,35–2,49 s tới deadline lớn hơn sai số này. Các lần đọc trạng thái vẫn thấy queued/running/unchanged ngay trước commit; lượt đọc kế tiếp hoặc tải output rơi sát/sau mốc 5 s. Sequence 17 chưa kịp đọc lần tiếp theo vì backoff; sequence 1 bị hủy HTTP trạng thái tại deadline; sequence 38 đã thấy succeeded nhưng GET output bị hủy ở deadline. Đây là trễ hành trình qua polling/output, không phải job compute thất bại hay queue bị kẹt vĩnh viễn.

Raw HTTP counters lượt 08: `{"job_poll_resync": 26, "job_poll_snapshot": 52, "request_body_bytes": 5160, "requests": 204, "response_body_bytes": 5061310, "response_completed": 202, "response_duration_microseconds": 64099716, "status_200": 147, "status_201": 25, "status_202": 30}`. Cửa sổ 63.651 s có 204 HTTP của harness + 450 background + 450 health; trung bình 17.345 HTTP/s trên cửa sổ này. Đây là mức tải nhỏ đã chạy, không phải throughput cực đại; 3 vòng noise và 1 vòng kiểm tra nền, 6 owner identity, không phải 50k concurrent user.

Các trường `requests`, `actions`, `job_timeline`, `db_clock_anchor` trong attempt-08/demand-result.json lưu toàn bộ bằng chứng trước khi dọn DB. `timeout-timeline.json` liên kết đúng sequence, job và request, chỉ giữ metadata; không lưu cookie/CSRF/secret/cursor hay nội dung riêng. Ngưỡng/giao thức production và mọi phần đã pass được giữ nguyên.
