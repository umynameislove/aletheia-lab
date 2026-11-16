# K14.01 và K14.05 — kết quả bổ sung trên staging Docker ngày 15/11/2026

Đã chạy bổ sung trên staging riêng sẵn có của Kiên, với cùng image, mạng, 2 API, 2 worker, PostgreSQL, Keycloak và TLS ingress. Chỉ khởi động lại API 0 một lần trong ca đo mới khởi động. Đã kiểm mã backend và hash hồ sơ cũ. Chưa commit hay push.

## K14.01 — chi phí theo operation và tài nguyên

**468/468 lượt đọc đạt trên 39 nhóm của 13 operation × S/M/L.** Có 12 lượt thử không gửi HTTP ở lần đầu vì kịch bản chưa lấy ID citation. Lần tiếp theo đo đúng ID từ kết quả danh sách claim. Tổng mẫu số của hai lượt là 468 hoàn tất trên 480 lần dự kiến. Các nhóm đã đạt ở lượt đầu được dùng lại.

Bổ sung **30/30** hành trình phân tích, hỏi tiếp, preview, export, review trong 15/15 nhóm. Chi phí bao gồm xử lý, polling hoặc tải tệp khi phát sinh. `http-requests.csv` ghi theo operation và transport, `action-results.csv` ghi kết quả đầu cuối. Các hành trình dùng các oracle sẵn có, gồm so khớp scope, trạng thái job và hash export.

Tính CPU trung bình từ delta cgroup của API 0/1, worker 0/1, ingress, PostgreSQL, Keycloak và máy phát. Lấy trước/sau các pha, giữ mẫu idle 2 giây cho phép so sánh. Tính CPU/lượt chào và nhu cầu theo λ × chi phí trung bình. Số việc đang xử lý trung bình được ước tính bằng λ × thời gian hoàn tất. Cửa sổ lấy mẫu khoảng 250 ms. Chi phí gộp nền và giám sát được giữ trong kết quả, đặc biệt với pha cỡ nhỏ. CPU của job nhiều bước được đo cả hành trình, nên riêng từng API call có thời gian và byte, chưa có CPU tách rời từ phần DB/worker chạy cùng lúc.

| Vai trò | Số nhóm đạt có đo | CPU/lượt chào nhỏ nhất–lớn nhất ms | RAM đỉnh ở điểm đo MiB | FD đỉnh mẫu/process |
| --- | --- | --- | --- | --- |
| api0 | 54 | 0.36–1183.65 | 102.8 | 12 |
| api1 | 54 | 0.54–78.36 | 108.4 | 12 |
| worker0 | 54 | 3.76–3320.14 | 102.1 | 7 |
| worker1 | 54 | 3.50–1646.46 | 102.2 | 7 |
| ingress | 54 | 0.53–30.27 | 23.5 | 11 |
| generator | 54 | 2.40–30.20 | 38.5 | 9 |
| pg | 54 | 20.04–540.28 | 206.7 | xem đo mixed trước |
| idp | 54 | 0.78–79.75 | 639.6 | xem đo mixed trước |

FD máy phát đọc được từ chính container, đỉnh tại các điểm chụp **9/256 FD**. Số kết nối, CPU/RAM và mạng dưới tải mixed dùng kết quả staging đã đạt. Kết nối DB đỉnh **8**, trần cấu hình **16/20**, API 0 trung bình **0,705/0,75 CPU**. Keycloak/Pg ghi CPU, RAM, PID cho từng pha. FD và RX/TX lúc mixed của hai thành phần có trong báo cáo staging trước.

## K14.05 — mới khởi động, truy cập lặp, ngắt kết nối và gửi trùng

Cấp lại phiên qua HTTPS/Keycloak cho 6 owner vì phiên giả lập cũ đã tới giới hạn nhàn rỗi. Đo ba kết quả ngay sau readiness của API vừa khởi động, sau đó đọc lặp mỗi kết quả 10 lần qua kết nối giữ mở. Đây là mốc lần đọc đầu theo từng kết quả trên API mới khởi động. PostgreSQL, cache hệ điều hành và các dịch vụ còn lại tiếp tục chạy.

| Cỡ | Đọc đầu ms | Trung vị 10 lần đọc lặp ms | Chậm nhất đọc lặp ms |
| --- | --- | --- | --- |
| S | 69.23 | 67.06 | 112.12 |
| M | 61.90 | 53.59 | 68.91 |
| L | 51.98 | 69.71 | 131.85 |

Mỗi đợt chào 3 job preview S/M/L của tenant tạo tải và 1 job của tenant nền. Mỗi job nhận 3 POST đồng thời dùng chung một khóa. Mở TLS thật để gửi một request trạng thái rồi chủ động hủy socket phía client. Client mới dùng lại cursor đã nhận trên lần đọc trước, kiểm workspace/job, revision và SHA đầu ra, sau đó đối chiếu ledger. Cửa sổ kèm các lần đọc lặp một kết quả L của tenant nền, health và SELECT 1. Sáu người dùng ở đây là danh tính tổng hợp đã đăng nhập thật.

| Đợt | ACK | Job duy nhất | Socket chủ động ngắt | Poll sau nối lại | Nền trước job tải cuối ms |
| --- | --- | --- | --- | --- | --- |
| 1 | 12 | 4 | 4 | 7 | 4005.56 |
| 2 | 12 | 4 | 4 | 7 | 3794.76 |
| 3 | 12 | 4 | 4 | 7 | 1981.52 |

Cả 12 job thành công ở attempt 1 và khớp SHA nguồn. Có 36 ACK, 12 kết nối bị hủy có chủ đích, 21 poll sau nối lại. Gửi trùng không tạo thêm job. Không kiểm lịch sử từng sự kiện vì giao thức chỉ cung cấp ảnh chụp trạng thái mới nhất. Không suy ra một cơn bão reconnect quy mô lớn hoặc hành vi trình duyệt từ 12 lần ngắt này.

| Đường đo | N | Mean ms | p95 ms | Max ms | Ngưỡng p95 ms |
| --- | --- | --- | --- | --- | --- |
| background | 121 | 89.450 | 138.712 | 716.759 | 500 |
| health | 121 | 34.359 | 55.157 | 152.235 | 250 |
| admin | 121 | 0.441 | 0.701 | 2.029 | 250 |

Giữ nguyên chứng cứ 116/116 hành trình mixed, 67 job và 49 export của lượt trước, cùng hai burst có 503 dự kiến khi queue đầy và retry đúng khóa. Không chạy lại các phiên đó.

## Phạm vi chốt

**Hai mục đã có phép đo bổ sung trên staging Docker của Kiên. Mốc nghiệm thu đầy đủ vẫn còn 2 mục mở.** K14.01 đã có số đo tất cả thành phần của staging và bộ operation đang chạy, nhưng thiếu CPU tách từng bước của các hành trình nhiều thao tác và bộ operation đầy đủ trên tải đích. K14.05 đã có các ca mới khởi động, truy cập lặp, ngắt/reconnect và gửi trùng trong bộ dữ liệu hiện tại. Tiêu chí còn lại của môi trường đích gồm tải vật lý S/M/L đủ kích thước và máy phát ngoài host.

Điểm chặn kiểm được từ mã nguồn là `SeedPlan.primary_gaps()` và guard `--require-primary`. Fixture hiện có 30 principal, 6 project, 2/16/64 claim và 6/34/130 node, dưới yêu cầu 50.000 principal và 100/1.000/10.000 claim. Điều này không thay được bằng tăng số lượt gửi. Mạng đã đo là bridge Docker. Hạ tầng và dữ liệu lớn thuộc phạm vi nghiệm thu primary riêng, chưa phát sinh sử dụng dịch vụ trả phí.

Staging vẫn chạy trên mạng nội bộ. Log thô, các lần lỗi, bảng tài nguyên, manifest SHA và công cụ thử nằm trong thư mục bằng chứng. Tệp nguồn thử mới nằm riêng trong thư mục staging. Các phần và bằng chứng đã pass giữ nguyên.
