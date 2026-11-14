# K09.03 — Kết quả sửa phân loại lỗi mất quyền sau claim

K09.03 đạt trong phạm vi backend và các ca hồi quy trực tiếp. Lượt cuối có **61 pass, 0 fail, 0 error, 0 skip**, gồm **51 unit** và **10 PostgreSQL**. Unit mất 8.98 giây theo pytest. PostgreSQL mất 65.75 giây theo pytest và 70,754 giây theo wrapper gồm khởi động Python và thu log.

Tiêu chí K09.03 yêu cầu job đã nhận đi tới terminal hoặc còn pending trong hạn, retry có giới hạn và deadline giữ từ cam kết ban đầu. Phần sửa nằm tại `src/aletheia_lab/hosted/upload_job_worker.py`, bộ phân loại xử lý riêng `AuthorizationDenied` và gọi `jobs.fail(..., failure_kind="authorization")` cho execution profile. Các ca legacy và lỗi filesystem PermissionError được kiểm riêng. Bản sửa có 5 dòng thêm và 2 dòng bỏ theo Git diff trong đúng một tệp runtime. Hai tệp test K09 mới ghi các ca hồi quy.

Source nền là `d3e9f4fa9bdca1055cb9044d76ef9ff82fe1fa80`. Hash của ba tệp kiểm trong `result.json` ràng buộc chính xác mã đã chạy. Python 3.11.9, Windows-10-10.0.26200-SP0. PostgreSQL 16.15 chạy trong container thử riêng, Docker 28.0.4. Image DB `sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94`. Mỗi ca dùng schema UUID riêng với pool tối đa 4 kết nối. Các phiên, nguồn và workspace là dữ liệu tổng hợp. Lease native 30 giây, timeout chờ barrier/kết thúc trong các ca mới là 5 giây. Connection timeout 5 giây áp dụng cho lần chạy sau khi khôi phục Docker.

Trước sửa, bốn ca PostgreSQL đều tái hiện `failure_kind=permanent`. Sau sửa, cả bốn đều ghi `authorization`. Worker được giữ tại barrier sau khi claim đã được lưu trong DB. Bài thử đọc `running`, attempt 1, started_at và heartbeat_at, sau đó thu hồi membership hoặc session bằng transaction DB. Hai điểm kiểm gồm chuẩn bị và xuất bản snapshot. Owner thứ hai đọc trạng thái và attempts qua repository, đồng thời kiểm serialization DTO.

| Thu hồi | Điểm chặn | Claim đến thu hồi | Thu hồi đến terminal | Kết quả |
| --- | --- | ---: | ---: | --- |
| membership | prepare | 111.477 ms | 30.224 ms | failed / authorization / attempt 1 |
| membership | publication | 1057.26 ms | 34.425 ms | failed / authorization / attempt 1 |
| session | prepare | 98.955 ms | 15.01 ms | failed / authorization / attempt 1 |
| session | publication | 101.246 ms | 21.924 ms | failed / authorization / attempt 1 |

Các thời gian trên lấy từ cùng clock PostgreSQL và là bốn mẫu của bài kiểm đúng chức năng. Mỗi ca xác nhận result=NULL, output URL vắng, lease được dọn, 0 canonical record được xuất bản, 0 job.publish audit, đúng 1 job.fail audit và chỉ 1 job. Credential đã bị thu hồi bị chặn khi đọc status/attempts. Khi quyền được phục hồi, retry job cũ vẫn bị từ chối. Claim tiếp theo trả về rỗng.

Bộ unit mới có 20 tổ hợp của năm operation preview/confirm/analyze/follow_up/refresh, hai điểm prepare/publication và hai profile execution/legacy. Hai ca bổ sung kiểm PermissionError của filesystem và mất quyền ở heartbeat, có chờ compute được dọn trước settlement. Cùng 29 ca K09 hiện có, bộ unit đạt 51/51.

Sáu ca PostgreSQL hồi quy trực tiếp kiểm transient backoff/attempt exhaustion, permanent/revoke, successful retry xóa failure_kind cũ, revision conflict, deadline chặn publication và manual retry giữ nguyên deadline/attempt budget. Cùng bốn ca mới, lượt native đạt 10/10. Tên từng ca, thời gian và properties nằm trong JUnit.

Ruff trên ba tệp đạt. C901 của mô-đun runtime giữ trần 10 và đạt. Mypy strict kiểm đúng một tệp runtime đạt. Bandit theo mức severity/confidence của CI đạt với 0 finding ở mức đã cấu hình.

Docker đã gián đoạn giữa lượt kiểm PostgreSQL trước. Khi tiếp tục, engine đã hoạt động, container thử đang dừng. Đã start đúng container, nhận cổng loopback mới 58478 và chờ pg_isready. Lượt có kết quả cuối bắt đầu 23:08 ngày 14/11/2026 giờ Việt Nam. Các lượt fixture unit ban đầu có lỗi dựng exception và kết quả preview đã được sửa. Log trước/sau, log fixture và lượt gián đoạn được giữ trong `raw-test-logs.zip`. `log-manifest.json` ghi hash và số byte của từng entry, đã giải nén so từng byte sau khi đóng gói.

`native-cases.csv` chứa job ID tổng hợp, timestamps claim/heartbeat/revoke/finished, failure_kind, retry, output và audit của bốn ca. `result.json` ghi tổng kết, môi trường và hash mã. Hồ sơ này là kết quả riêng K09.03. Bài chạy dùng worker/repository và PostgreSQL thật với barrier được điều khiển trong test. Receipt browser A10 của Quân giữ phạm vi nguồn của lần chạy đó. Nghiệm thu toàn bộ K01–K18 cho Bảo tiếp tục theo điều kiện bàn giao đã yêu cầu.
