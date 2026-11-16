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

## Bổ sung kết quả CI GitHub

Xác minh lúc 2026-11-15T01:31:08.867665+07:00. Commit `731f1bc2225805262761a21483cec3c693e6b9d4` trên `product-api`, PR #193, đạt **11/11 check thành công**. Ba workflow gồm ci, hosted backend và dependency-audit. CI checkout merge tạm `1f57a8210daf4dad24a98fc1574be230e0c2521d`; cây mã merge và cây mã head cùng hash `e2c8fab2300b2ec8ba6e7d7cf7a4182eef36239f`. Mã nguồn giữ nguyên trong toàn bộ lần theo dõi này.

| Workflow / job | Lượt thực chạy | Thời gian job | Kết quả |
| --- | ---: | ---: | --- |
| ci / test (3.12) | 2 | 2889 giây | success |
| ci / security | 1 | 62 giây | success |
| ci / windows-project (0) | 1 | 1271 giây | success |
| ci / quality | 1 | 67 giây | success |
| ci / test (3.11) | 1 | 4583 giây | success |
| ci / application | 1 | 86 giây | success |
| ci / windows-project (1) | 1 | 1545 giây | success |
| hosted backend / dashboard | 1 | 131 giây | success |
| hosted backend / backend | 1 | 932 giây | success |
| dependency-audit / audit (3.11) | 1 | 42 giây | success |
| dependency-audit / audit (3.12) | 1 | 46 giây | success |

Thời gian bảng tính từ startedAt đến completedAt của GitHub, gồm cài đặt và dọn môi trường. Các job thành công của lượt 1 được GitHub giữ kết quả khi chạy lại riêng Python 3.12, không phải thực chạy lại toàn bộ. `ci-731f1bc/jobs.csv` có timestamps UTC, job ID và link từng job.

Linux dùng image ubuntu-26.04, Python 3.11.18 và 3.12.16, PostgreSQL 16.16. Dịch vụ PostgreSQL có image pin theo workflow, cổng loopback 5432, dữ liệu tổng hợp. K09_TEST_DATABASE_URL có trong cả matrix và job hosted native. Workflow lấy cả hai tệp test K09 mới qua glob hiện có; không sửa workflow hoặc nới điều kiện pass.

Python 3.11 đạt **11.452 pass, 56 skip, 0 fail**, 7 warning, pytest 2.777,62 giây; coverage **88,73%**, qua ngưỡng 88%. Python 3.12 ở lượt cuối đạt **11.452 pass, 56 skip, 0 fail**, 7 warning, pytest 1.939,56 giây. Mypy strict của cả hai interpreter đạt trên 403 tệp. Bộ kiểm tái lập Python 3.11 chạy ba lần, mỗi lần 4.041 pass/9 skip, mất 567,02 / 590,30 / 554,78 giây. Bộ tương thích đánh giá Python 3.12 của lượt chạy lại đạt 4.041 pass/9 skip trong 819,13 giây. Các số này có ca trùng nhau, không cộng thành số ca duy nhất. Skip giữ nguyên nghĩa là không thực thi, không tính là pass.

Job hosted native có 430 dấu pass và 11 dấu skip trong log quiet, thêm 4 ca query-probe pass. Đây là số đếm dấu tiến độ vì cấu hình -qq không in dòng tổng kết. Offline hosted đạt 2.985 pass/13 skip/5 deselect trong 58,01 giây. Consumer UI đạt 359/359, 0 fail, 10.851,230815 ms. Application đạt 162 pass trong 30,71 giây, coverage 92,31%. Chất lượng mã, maintainability, repo hygiene, Bandit và hai audit thư viện đều success. Bộ K09.03 chọn riêng trước đó vẫn là **61/61, 0 skip**; các số của CI tổng không thay thế kết quả chọn riêng đó.

**Lượt đầu có lỗi, đã lưu nguyên bằng chứng.** Python 3.12 lần đầu ghi 1 fail / 11.451 pass / 56 skip trong 1.676,52 giây. Ca fail là `test_k07_upload_staging_postgres.py::test_real_resume_out_of_order_parallel_retry_lost_ack_and_complete_once`. Ba complete_upload đồng thời gặp cửa sổ record chuyển ready giữa đọc metadata và assemble, trả upload_conflict. Traceback nằm ở postgres_sources.py, không đi qua worker được sửa cho K09.03. GitHub từ chối yêu cầu rerun khi lượt đang chạy; sau khi Python 3.11 kết thúc đã chạy lại duy nhất job Python 3.12 đúng một lần, cùng SHA, và pass. Không sửa test, không thêm skip, không sửa K07. Race K07 vẫn là rủi ro chưa được sửa; lượt rerun xanh không chứng minh nguyên nhân gốc đã hết.

Metadata, log ZIP nguyên gốc của cả hai lượt, snapshot 11 check và SHA-256 nằm trong `ci-731f1bc/`. `github-ci-result.json` tổng hợp phương pháp và kết quả; `test-summary-lines.json` dẫn từng dòng tổng kết về đúng entry log; `manifest.json` kiểm toàn vẹn các tệp. Hồ sơ CI được cập nhật trên máy, không tạo commit nguồn mới để SHA đã kiểm vẫn giữ nguyên. K09.03 đã qua các kiểm thử nêu trên và CI của SHA sửa. Phần browser A10, staging/tải và sửa race K07 nằm ngoài lần xử lý này.

[CI cuối](https://github.com/umynameislove/aletheia-lab/actions/runs/43615235322/attempts/2), [hosted backend](https://github.com/umynameislove/aletheia-lab/actions/runs/43615235242), [dependency-audit](https://github.com/umynameislove/aletheia-lab/actions/runs/43615235240).
