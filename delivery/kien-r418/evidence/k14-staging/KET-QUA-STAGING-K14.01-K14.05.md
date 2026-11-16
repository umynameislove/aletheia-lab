# K14.01 và K14.05 trên staging Docker riêng

Đã dựng staging cô lập trên máy Kiên với 2 API, 2 worker, PostgreSQL 16, Keycloak và ingress TLS. Image chứa wheel từ `fa46dd8cbb2239c6f116456894a2009b45846245`. Đối chiếu 677 tệp trong gói cài đặt khớp release identity đã ghi. Các container dịch vụ dùng cùng mã nhà kho đã được kiểm thử trước đó.

**Bài đo staging với bộ dữ liệu native đạt các ngưỡng tài nguyên và fairness đã chạy. K14 còn 2 mục mở theo tiêu chí đầy đủ của workload đích.** Môi trường này dùng 30 principal tạo sẵn trên một Docker Desktop. Bộ dữ liệu lớn theo đặc tả, máy phát tải độc lập và NIC ngoài host vẫn thiếu.

## Cấu hình và cách chạy

Staging `aletheia-k14-stg-da2093353a73` đang chạy trên mạng Docker internal. URL trong mạng Docker là `https://localhost:18776` qua DNS nội bộ của bộ phát tải. Không có cổng được publish hiệu lực trên Windows. Cả hai replica và ingress trả 200 khi kiểm TLS bằng CA riêng. Keycloak thực hiện authorization code qua HTTPS và endpoint phiên trả 200. Kịch bản tải dùng phiên tổng hợp đã seed sẵn, tách phép đo login khỏi thời gian hành trình nghiệp vụ.

Image `sha256:35ab7a8638b1f3ef7134bfeb87a4bbb6aac0a0101d74857cdaabafee251b8add`. Hai API có quota 0,75 CPU và 512 MiB mỗi replica, hai worker 1 CPU và 640 MiB mỗi replica, một job slot mỗi worker. PostgreSQL 1 CPU và 640 MiB, IdP 1 CPU và 640 MiB. Ingress 0,25 CPU và 96 MiB, giới hạn 32 kết nối. Generator 0,75 CPU và 384 MiB trong container riêng trên cùng máy. Số đo không gộp giới hạn thành lời hứa chịu tải.

Hai tenant có 6 project, 6 snapshot, 6 result và 6 nguồn vật lý. Dùng 6 owner chia S/M/L của hai tenant. Bộ dữ liệu có 1.000/10.000/100.000 dòng nguồn, tương ứng 2/16/64 claim và 6/34/130 graph node. Kích thước ZIP, kết quả và checksum từng nhóm nằm trong `deployment-dataset.json`.

## K14.01. Ngân sách kết nối và tài nguyên

Với 2×4 pool API, 2×2 pool worker và reserve quản trị 4, trần cấu hình là **16/20 kết nối**. PostgreSQL có `max_connections=24`. Đã lấy 338 mẫu `pg_stat_activity` dưới tải hỗn hợp, đỉnh **8 kết nối** gồm API, worker và tài khoản quan sát. Có hai slot readiness dành riêng theo cấu hình API. `connection-plan.json` và `summary.json` lưu số theo từng vai trò.

| Thành phần | Mẫu | CPU TB / quota | RAM đỉnh mẫu / giới hạn MiB | PID đỉnh | FD đỉnh/process | RX / TX byte |
| --- | --- | --- | --- | --- | --- | --- |
| api0 | 363 | 0.705 / 0.75 | 157.6 / 512 | 9 | 17 | 60,679,605 / 36,471,492 |
| api1 | 368 | 0.215 / 0.75 | 151.8 / 512 | 8 | 15 | 21,643,045 / 10,222,313 |
| ingress | 368 | 0.009 / 0.25 | 16.9 / 96 | 5 | 17 | 12,069,396 / 12,071,272 |
| worker0 | 360 | 0.474 / 1.00 | 158.0 / 640 | 24 | 11 | 21,635,192 / 4,220,090 |
| worker1 | 364 | 0.397 / 1.00 | 158.6 / 640 | 24 | 10 | 19,815,157 / 3,853,154 |
| pg | 79 | 0.400 / 1.00 | 172.8 / 640 | 16 | 177 | 43,543,353 / 122,055,845 |
| idp | 79 | 0.024 / 1.00 | 586.1 / 640 | 42 | 251 | 0 / 0 |
| generator | 79 | 0.040 / 0.75 | 41.1 / 384 | 6 | không đọc được | 11,283,675 / 1,110,398 |

API 0 dùng trung bình 0,705/0,75 CPU trong cửa sổ mixed, khoảng 94% quota. API 1 dùng 0,215/0,75. Ingress chia theo kết nối TCP và client dùng lại kết nối, nên cần xem từng replica khi đọc dư địa. FD của generator không đọc được với quyền đo hiện tại và được giữ null.

CPU lấy delta bộ đếm cgroup theo cửa sổ, RAM lấy đỉnh quan sát, gồm phần do container được tính ở cgroup. FD là đỉnh mẫu của một tiến trình. Mạng lấy byte giao diện trong Docker bridge. Bộ lấy mẫu PG/IdP/generator dùng thêm tiến trình đọc bộ đếm, nên số CPU của nhóm này có kèm chi phí quan sát. `metrics` lưu chuỗi CPU, RAM, FD, ingress và telemetry pool. `auxiliary-resource-samples.jsonl` lưu dữ liệu thô cho ba thành phần đo bằng shell.

Đã có mẫu đạt cho **51/51 cặp hành trình × kích thước** từ hai lần chạy. Lượt đầu có **617/618** hành trình hoàn tất do một timeout đọc danh mục L. Lượt tiếp chỉ chạy 13 nhóm thiếu và hoàn tất **206/206**, sau đó chạy mixed. Tổng mẫu đo riêng theo lượt **823/824**. Timeout gốc được giữ nguyên như một lỗi của lượt đầu.

`cpu-per-journey.csv` có ΔCPU và CPU/lượt chào cho mỗi replica và ingress. Cột nhu cầu trung bình dùng λ quan sát × CPU/lượt, và số việc đang xử lý ước tính dùng λ × thời gian bình quân. Hành trình nhiều request gộp cả job, poll và download. Mẫu chụp 250 ms có thể bỏ lỡ pha rất ngắn, được đánh dấu trong CSV. Chưa đo CPU riêng của mọi endpoint, DB cho mọi pha, NIC vật lý hay ngân sách primary.

## K14.05. Tải hỗn hợp và hai đợt burst

Chào tải 90 giây với ba luồng song song gồm phân tích S, preview M, export L. Mỗi luồng nghỉ 50 ms sau một hành trình. Tenant nền luân phiên đọc header S/M/L qua ingress, gọi readiness và thực hiện `SELECT 1` qua kết nối quan sát TLS. Cửa sổ thực tế **92.486 giây**, đạt **116/116 hành trình**, **67/67 job thành công**, **49 export khớp dữ liệu tải về**, 0 timeout và 0 reject. Phân tích S **33/33 đạt**, dài nhất 3126.95 ms với hạn cũ 5.000 ms.

| Kịch bản | Đường kiểm tra | N | Mean ms | p95 ms | Max ms | Ngưỡng p95 ms |
| --- | --- | --- | --- | --- | --- | --- |
| Mixed | background | 338 | 146.522 | 274.764 | 413.360 | 500 |
| Mixed | health | 338 | 68.573 | 169.533 | 258.076 | 250 |
| Mixed | admin | 338 | 0.482 | 0.779 | 10.557 | 250 |
| Burst | background | 178 | 115.056 | 230.256 | 656.725 | 500 |
| Burst | health | 178 | 33.860 | 63.004 | 108.801 | 250 |
| Burst | admin | 178 | 0.416 | 0.654 | 1.152 | 250 |

| Đợt | Giây | Gửi lần đầu | ACK 202 | 503 dự kiến | Job tenant nền | Job tenant tải | Nền trước job cuối ms | Job retry sau drain |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 18.417 | 14 | 10 | 4 | 2 | 8 | 10239.77 | 1 |
| 2 | 17.182 | 14 | 10 | 4 | 2 | 8 | 13448.86 | 1 |

Mỗi đợt chào 12 intent preview luân phiên S/M/L của tenant tải và 2 intent của tenant nền khi cả hai worker đang chạy. 503 có `Retry-After` từ 1 đến 5 giây và `Cache-Control: no-store`. Gửi lại khóa đã được nhận trả cùng job. Sau drain, gửi lại một intent bị từ chối ở mỗi đợt tạo đúng một job và replay tiếp trả cùng job. 22 job khác nhau thành công ở attempt 1, đầu ra khớp SHA nguồn. Sáu intent 503 còn lại được giữ là từ chối do bão hòa. Chứng cứ cho biết job tenant nền đầu tiên hoàn tất trước job tenant tải cuối trong cả hai đợt.

## Lưu bằng chứng và mở lại staging

Lưu các log setup lỗi, log readiness, JUnit hoặc exit của bộ chạy, dataset manifest, cấu hình quota, chuỗi đo, kết quả từng hành trình và ledger burst. Lỗi setup gồm lần build Docker qua tar bị lỗi truyền, tên schema chưa đúng guard của seed, Docker dừng hai container nền, API bootstrap lần đầu và bộ đọc cgroup ban đầu chỉ hỗ trợ v2. Các lần khôi phục dùng tài nguyên gắn nhãn của staging. Đã kiểm SHA các báo cáo cũ và mã backend đang ở nguyên HEAD.

Các lệnh `stage_up.py status`, `stage_up.py stop` và `stage_up.py resume` điều khiển đúng các container đã ghi theo nhãn sở hữu. `run_load.py` chạy các bài đo trong mạng staging và giữ tệp kết quả mỗi lần. Phiên tổng hợp có thời hạn 15 phút không hoạt động. Chạy lại sau khoảng này cần cấp phiên mới hoặc seed một staging mới. Các container và volume có thể dùng lại để kiểm readiness.

Cửa sổ đạt ở đây giới hạn trong dữ liệu native, cùng một máy và mức tải đã nêu. Vẫn cần dữ liệu primary đúng kích thước, phạm vi operation đầy đủ, máy phát riêng và phép đo tài nguyên mạng trên triển khai đích để đóng hai mục theo mức nghiệm thu đã yêu cầu. Chưa commit hay push.
