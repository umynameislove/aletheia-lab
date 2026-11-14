# K07 — Kết quả sửa race hoàn tất upload đồng thời

Bản sửa đạt kiểm thử chọn riêng trên cả Windows và Linux. **Windows 49/49 pass**, gồm 10 ca ép race mới, 21 ca hồi quy PostgreSQL liên quan trực tiếp và 18 ca HTTP/hợp đồng dữ liệu. **Linux/Python 3.12 đạt 18/18 pass**, gồm toàn bộ 10 ca ép race và 8 ca hồi quy trực tiếp. Tổng 67 lượt thực thi thành công, 49 ca khác nhau; không cộng ca chạy trên hai nền tảng thành ca duy nhất. Các lượt cuối không fail, error hoặc skip.

Ghi kết quả lúc 2026-11-15T01:48:38.499368+07:00. HEAD nền `731f1bc2225805262761a21483cec3c693e6b9d4`, nhánh `product-api`. **Chưa commit, chưa push theo yêu cầu của Kiên.** CI 11/11 của SHA nền có trước bản sửa này, không dùng làm bằng chứng CI cho mã đang sửa ở máy.

## Nguyên nhân và phạm vi sửa

Ba complete_upload chạy đồng thời từng gây lỗi trên CI: một yêu cầu đọc active, yêu cầu khác hoàn tất và xóa part, sau đó yêu cầu đầu đọc lại record ready nhưng gọi bộ kiểm active và trả upload_conflict. Việc kiểm ready trước đây và đọc part nằm ở hai lượt giữ khóa khác nhau.

Chỉ sửa `src/aletheia_lab/hosted/postgres_sources.py`, 8 dòng thêm/6 dòng bỏ theo Git. `_assemble_upload` kiểm ready sau `_upload_record` có xác thực và FOR UPDATE, trả UploadStatus hiện có ngay trong lượt đọc này. Caller nhận UploadStatus hoặc tiếp tục với record/payload. Bỏ lượt kiểm ready riêng trước assembly. Khóa quota, giao dịch publication, kiểm lại byte thực tế, authorization và finally trả reservation được giữ nguyên. Không đổi schema, migration, dependency, API, UI hoặc các tệp kiểm thử cũ. Tệp mới `tests/integration/test_k07_completion_race_postgres.py` khóa thứ tự thực thi bằng barrier để kiểm được cửa sổ tranh chấp chắc chắn.

## Phương pháp và môi trường

PostgreSQL thật 16.15 trong container thử riêng `aletheia-k07-race-test-pg`, image `sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94`. Mỗi ca dùng schema UUID và pool tối đa 8 kết nối. Windows dùng Python 3.11.9, kết nối loopback cổng 59874. Linux dùng Python 3.12.16, pytest 9.2.0, psycopg 3.3.8 trong image có sẵn `sha256:6aed97ec4d6691527a72667ecdac67e4964ddcd6c1e4077aab9d8073a249b797`; source mount chỉ đọc, CPU giới hạn 2, RAM giới hạn 2 GiB. Container Linux dùng cùng namespace mạng với DB, cổng 5432. Không cài thêm thư viện. Connection timeout 5 giây, chờ barrier và settlement tối đa 10 giây trong bài mới. Dữ liệu đều tổng hợp.

Bài mới giữ 2 hoặc 7 caller đã qua admission nhưng chưa vào assembly. Sau đó caller còn lại hoàn tất thật với PostgreSQL, hoặc main test hủy/cho hết hạn. Với hai ca mất quyền, winner hoàn tất trước rồi membership/session của người gọi bị thu hồi bằng transaction DB. Sau khi có witness thời gian DB, giải phóng barrier cho các caller còn lại. Mỗi upload ZIP dài 412 byte, chia 4 part. Mỗi caller giữ reservation 824 byte. Đây là kiểm tính đúng khi có 3/8 caller đồng thời; không phải bài đo RPS, RAM process hoặc sức chịu tải triển khai.

## Trước và sau sửa

| Lượt | Pass | Fail | Skip | Thời gian pytest |
| --- | ---: | ---: | ---: | ---: |
| Trước sửa, fixture hợp lệ | 8 | 2 | 0 | 47.15 giây |
| Sau sửa, 10 ca ép race Windows | 10 | 0 | 0 | 60.06 giây |
| Hồi quy PostgreSQL Windows | 21 | 0 | 0 | 102.42 giây |
| HTTP và models Windows | 18 | 0 | 0 | 2.64 giây |
| Race và hồi quy Linux/Python 3.12 | 18 | 0 | 0 | 76.11 giây |

Trước sửa, hai ca ready với 3/8 caller cùng tái hiện UploadRejected(upload_conflict); tám ca hủy, hết hạn và mất quyền pass. Sau sửa cả 10 ca pass. Hai lượt fixture chẩn đoán ban đầu có sai tên trường peak và sai mốc created_at khi ép hết hạn; đã sửa fixture trước lượt so sánh hợp lệ, log vẫn được giữ và không tính là bằng chứng lỗi ứng dụng.

## Kết quả từng tình huống

| Tình huống | Caller | Kết quả caller chờ | Source / audit source.put | Reservation peak | Xác minh sau witness Windows / Linux |
| --- | ---: | --- | ---: | ---: | ---: |
| ready | 3 | identical_ready_receipts | 1 / 1 | 2472 byte | 38.417 / 8.311 ms |
| ready | 8 | identical_ready_receipts | 1 / 1 | 6592 byte | 46.326 / 29.25 ms |
| cancelled | 3 | upload_conflict | 0 / 0 | 1648 byte | 16.339 / 6.805 ms |
| cancelled | 8 | upload_conflict | 0 / 0 | 5768 byte | 66.304 / 15.222 ms |
| expired | 3 | upload_expired | 0 / 0 | 1648 byte | 15.627 / 5.944 ms |
| expired | 8 | upload_expired | 0 / 0 | 5768 byte | 62.108 / 21.004 ms |
| membership | 3 | authorization_denied | 1 / 1 | 2472 byte | 16.514 / 7.798 ms |
| membership | 8 | authorization_denied | 1 / 1 | 6592 byte | 36.509 / 19.842 ms |
| session | 3 | authorization_denied | 1 / 1 | 2472 byte | 21.157 / 16.273 ms |
| session | 8 | authorization_denied | 1 / 1 | 6592 byte | 55.717 / 6.602 ms |

Trong ca ready, mọi caller nhận cùng UploadStatus/source_id, đúng một source và một source.put audit, byte nguồn đọc lại trùng payload, part đã được dọn và không chạy inspect lần nữa. Khi membership/session mất hiệu lực, caller chờ nhận AuthorizationDenied dù winner đã tạo source. Hủy trả upload_conflict, hết hạn trả upload_expired. Mọi tình huống đều trả reservation về 0; peak đo tại bộ admission là 2.472 hoặc 6.592 byte khi có winner, 1.648 hoặc 5.768 byte khi hủy/hết hạn. Đây là reservation logic của payload thử, không phải RSS đo của process.

Cột thời gian tính từ mốc DB sau chốt trạng thái/thu hồi đến sau các câu kiểm DB cuối, có cả overhead xác minh; không diễn giải thành latency trả lời, percentile hoặc cam kết hiệu năng. `race-cases.csv` chứa 20 witness theo hai nền tảng, upload ID, timestamps, số part, byte, outcome, audit và reservation. `result.json` liệt kê tên và thời gian của từng ca trong mọi profile. JUnit có thời gian suite riêng, khác thời gian pytest do phạm vi đo.

21 hồi quy Windows kiểm resume/out-of-order/lost-ack/complete-once, quota, cancel/expiry, role/parent grant, hash/corruption và migration fail-closed, parser cancel, disk-full rollback/retry, hoàn tất không ghi lại bundle qua client, byte bị đổi/mất sau inspection, reconciliation part khi mất ACK/rollback, giữ reservation đến khi parser được dọn, và quota chung giữa source.put với chuyển reservation thành source. Chỉ chạy lại các kiểm tra liên quan; không thay mã của các phần đó.

Ruff hai tệp đã thay đổi đạt. C901 runtime giữ trần 10 và đạt. Mypy strict trên đúng một runtime file đạt. Bandit ở mức severity/confidence medium trở lên đạt, không có finding ở mức cấu hình. Git diff --check đạt.

## Bằng chứng và giới hạn nghiệm thu

`raw-test-logs.zip` giữ nguyên toàn bộ log và JUnit trước/sau, hồi quy, Linux, static và các chẩn đoán fixture; `log-manifest.json` ghi hash/số byte từng entry. `source.patch` giữ diff production với zero context để không có dòng khoảng trắng của patch bị bộ kiểm whitespace báo lỗi; khi áp patch dùng `git apply --unidiff-zero`. `environment.json` ghi môi trường và hash hồ sơ K09.03 từ trước lần xử lý này; đã đối chiếu lại và không thay đổi chúng. `manifest.json` ghi hash các tệp hồ sơ. Container/volume DB thử riêng đã dọn sau khi xác minh ID và nhãn; container Linux tự dọn, Docker và các container khác giữ nguyên.

Chốt bản sửa race K07 trong phạm vi kiểm thử nêu trên tại máy. Chưa commit/push nên chưa có CI GitHub cho bản sửa mới; không gán kết quả CI cũ cho mã này. Không chạy browser, staging hoặc bài tải triển khai và không xuất nghiệm thu toàn bộ K01–K18 cho Bảo ở lần này.
