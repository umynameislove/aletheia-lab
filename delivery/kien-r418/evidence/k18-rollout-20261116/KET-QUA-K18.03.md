# K18.03 — Readiness, drain và rollout

**PASS phần Kiên trên staging Docker ngày 16/11/2026. K18.03 được chốt cho cặp image đã kiểm dưới đây.**

Lượt `attempt-05` chạy API và worker bằng wheel cài trong image, PostgreSQL 16 và Keycloak thật qua TLS. Cụm có 2 API và 2 worker. Khi canary, cụm có tối đa 3 API và 2 worker. Dữ liệu thử có 30 principal, hai workspace và nguồn vật lý S/M/L 1.000/10.000/100.000 dòng. Lượt rollout dùng hai owner của hai workspace và preview nguồn L.

## Các điều kiện đã chốt

| Điều kiện K18.03 | Bằng chứng và kết quả |
| --- | --- |
| Replica mới chỉ nhận traffic sau readiness | 4 canary gồm hai API khi nâng cấp và hai API khi rollback. Mỗi replica được đưa vào danh sách dự kiến khi còn chưa chạy. Ingress ghi readiness chưa đạt và giữ replica ngoài pool. Sau HTTP 200, replica được đưa vào pool và thực sự nhận kết nối. |
| Giữ job đã ACK khi đổi phiên bản | Upgrade có 6/6 job thành công và rollback có 6/6 job thành công, tất cả attempt 1. Mỗi lần chuyển có hai job đang chạy và bốn job chờ. Hai job đang chạy đều kết thúc sau thời điểm SIGTERM worker đầu tiên. Đã đọc output qua API, kiểm workspace và SHA-256 của nguồn, rồi gửi lại cùng khóa và nhận đúng job cũ. |
| Giữ lượt tải đang chạy khi drain API | Hai lượt tải 191.537 byte qua ingress có kết nối tới API cũ tại thời điểm gỡ API khỏi pool. Sau SIGTERM, client tiếp tục nhận đủ byte và khớp cùng SHA-256. Upgrade hoàn tất tải sau tín hiệu khoảng 3,84 giây; rollback khoảng 4,05 giây. |
| Worker giữ lease cũ bị fencing | Một tiến trình dùng image cũ và role worker thật claim job qua `PostgresJobs` với lease 1 giây. Sau khi worker runtime nhận lại job ở attempt 2, lease cũ bị từ chối heartbeat và ghi kết quả. Ledger ghi attempt 1 `lease_lost`, attempt 2 `succeeded`; client đọc lại output đúng SHA. |
| Traffic nền tiếp tục được phục vụ | 184/184 lượt đọc result header đúng workspace. Mean 156,77 ms, p95 223,54 ms, max 615,46 ms. 184/184 lần `SELECT 1` đạt, p95 0,965 ms. Ingress duy trì replica ready khi đổi pool. |
| Tài nguyên cho canary và drain | Đỉnh quan sát 15 kết nối DB. Khi có ba API, ngân sách cấu hình là 3×4 + 2×2 + reserve 4 = 20, PostgreSQL có max 24, trong đó 3 slot superuser. Có 28 mẫu Docker stats trong pha cuối upgrade, rollback và fencing. Tất cả container được inspect với OOMKilled=false. |

Tám tiến trình API/worker cũ được SIGTERM và xác nhận dừng. Worker thoát mã 0. Wrapper theo dõi API chuyển mã trả về `-SIGTERM` của tiến trình con thành 241. Bộ kiểm nhận hai mã 0/241 cùng điều kiện dữ liệu và trạng thái tiến trình, đồng thời từ chối OOM. Log và mã thoát của từng container nằm trong hồ sơ.

## Phiên bản và cấu hình đã kiểm

| Phiên bản | Commit | Image digest |
| --- | --- | --- |
| Trước nâng cấp, đồng thời là bản rollback | `5ac1c54d90ce1ee507e4cdce3b0f8310b49505cf` | `sha256:4b589a4b2945003a0a12ff979b0fe119b6522882596b11e8a8c759b380ea9395` |
| Bản nâng cấp | `fa46dd8cbb2239c6f116456894a2009b45846245` | `sha256:35ab7a8638b1f3ef7134bfeb87a4bbb6aac0a0101d74857cdaabafee251b8add` |

Image bản trước được build offline từ `git archive` của đúng commit. Image mới là image staging đã cài wheel và xác minh trong hồ sơ K14. Cặp phiên bản này dùng cùng schema. Ingress của bài diễn tập là TLS passthrough trong `edge.py`, chủ động kiểm readiness và có lệnh cập nhật pool trước khi drain. Cổng tải riêng làm chậm đường truyền để lượt tải còn hoạt động lúc chuyển API. Ingress và bộ điều khiển được lưu kèm để tái lập bài kiểm.

| Thành phần | RAM cao nhất tại điểm lấy mẫu MiB | Giới hạn MiB |
| --- | ---: | ---: |
| API, theo từng container | 88,92 | 512 |
| Worker, theo từng container | 185,80 | 640 |
| PostgreSQL | 117,10 | 640 |
| Keycloak | 542,20 | 640 |
| Ingress diễn tập | 18,06 | 384 |
| Bộ phát tải | 64,75 | 384 |

Mẫu Docker stats chạy từ 14:03:08 tới 14:04:29 giờ Việt Nam, bao phủ pha cuối upgrade, rollback và fencing. Docker stats có thể trả số 0 cho container vừa dừng. Bảng tổng hợp chỉ lấy mẫu có giới hạn RAM dương. Số liệu áp dụng cho workload thử của lượt này. Ngân sách tài nguyên ở 50.000 người dùng tiếp tục được xét trong K14/K17.

## Lịch sử các lượt kiểm

- Build đầu tiên lỗi truyền tar qua Docker với `COMPRESSION_ERROR`. Build lại bằng thư mục context tạm đã đạt.
- Attempt 01 dừng khi hai luồng cùng gọi health, chạm lane health chỉ có một slot. Lượt tiếp dùng duy nhất luồng canary readiness.
- Attempt 02 hoàn tất phần nghiệp vụ upgrade nhưng bộ điều khiển dừng vì chỉ chấp nhận mã thoát 0 của API wrapper. Đã xác định và xử lý mã 241 của SIGTERM.
- Attempt 03 hoàn tất upgrade; ingress diễn tập dừng trong rollback vì Windows từ chối đổi tên tệp trạng thái đang được đọc.
- Attempt 04 dừng khi bộ điều khiển Windows gặp cùng loại xung đột ở chiều đọc tệp. Bộ phối hợp bổ sung retry có giới hạn cho cả đọc và đổi tên nguyên tử.
- **Attempt 05 đạt cả upgrade, rollback và fencing.** Bản script đúng byte của lượt này được lưu trong `attempt-05/input`. Các log lỗi và kết quả từng phần của lượt trước được giữ lại với trạng thái riêng.

## Trạng thái K18 sau lượt này

K18.02, K18.03 và K18.05 đã đạt phần Kiên, tương ứng **3/5 mục cá nhân**. K18.01 và K18.04 còn mở. K18.06 là mục bàn giao liên bên còn mở. Bài này kiểm rollout và rollback trên schema tương thích của cặp image đã nêu. Release có thay đổi image, ingress hoặc schema cần phép xác nhận phù hợp với thay đổi đó.

## Tái lập và hồ sơ

`setup_rollout.py` dùng image nền và helper staging đã có để tạo fixture mới. Sau khi PostgreSQL và Keycloak sẵn sàng, chạy `run_rollout.py attempt-NN` trong môi trường có Docker. Bộ điều khiển ghi từng lần readiness, đổi pool, SIGTERM, drain và image thực tế. Chạy `verify_results.py` để đối chiếu các điều kiện trực tiếp từ receipt của attempt đã chốt.

`summary.json`, `attempt-05/*-result.json`, `controller-events.jsonl`, `edge-canaries.jsonl` và `probe-samples.json` chứa bằng chứng thành công. `runtime-logs.zip` giữ log container. `final-runtime-inspection.json` giữ image, giới hạn tài nguyên, OOM và trạng thái tiến trình. `cleanup.json` xác nhận đã dọn 17 container, 11 volume và mạng thử. Cụm LAN ban đầu vẫn có đủ 7 container đang chạy. Thư mục bí mật tạm cũng được dọn sau khi kiểm đường dẫn. Các image dùng cho phép đo được giữ để đối chiếu.

Kiểm tra tập trung bằng các assert của bài chạy, kiểm độc lập receipt và Ruff. Toàn suite sản phẩm và chiến dịch 50k nằm ngoài lượt này. Mã sản phẩm và lịch sử commit được giữ tại cặp SHA đã ghi.
