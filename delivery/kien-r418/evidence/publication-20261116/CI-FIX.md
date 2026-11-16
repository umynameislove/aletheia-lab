# Kiểm lại lỗi CI khi bổ sung model cửa sổ

SHA phát hiện: `293b1417d293d2ed1cd3bf7ddcca26a2c916e029`.

## Kết quả trước sửa

- Dashboard GitHub: 2.992 ca Python đạt, 13 bỏ qua theo điều kiện và 5 không thuộc lựa chọn; 363 ca Node đạt.
- Matrix Python 3.12: 11.474 ca đạt, một ca fail tại kiểm nâng schema. Bài kiểm cố định số ledger cuối là 17 trong khi schema hiện tại đã có migration 18.
- Native backend bị dừng ở giới hạn job 20 phút sau khi log đã ghi một ca fail. Không coi lượt này là đạt.

## Sửa và phương pháp kiểm

Giữ đường nâng v16→v17 và kiểm thêm v16→schema hiện tại. Với mỗi đích, so sánh nguyên 16 dòng checksum/signature cũ, kiểm replay migration không tạo thay đổi và so sánh nguyên byte JSON canonical của record. Ca riêng v17→v18 tiếp tục kiểm ledger và replay như trước.

Job native CI được cấp 40 phút vì đã thêm catalog vật lý và luồng xuất toàn bộ model lớn. Giữ đủ tập kiểm hiện tại; log dùng tên từng ca và thời gian 20 ca chậm nhất để xác định vị trí nếu job bị dừng.

Chạy lại trên Linux Python 3.12.16, pytest 9.2.0 và PostgreSQL 16 trong mạng Docker riêng, không dùng database staging đang phục vụ. Repository được mount chỉ đọc. Database và mạng riêng được dọn sau lượt kiểm.

| Kiểm tra | Kết quả |
| --- | --- |
| Tranh chấp follow-up sau ACK | PASS |
| Nâng v16→v17, giữ checksum và canonical bytes | PASS |
| Nâng v16→v18, giữ checksum và canonical bytes | PASS |
| Nâng v17→v18, giữ prefix ledger và replay | PASS |
| Tổng kiểm PostgreSQL thật | 4/4, 24,00 giây |
| Contract profile sau sửa | 126/126, 47,55 giây |
| Ruff cho bài kiểm đã sửa | PASS |

Log đầy đủ và JUnit nằm cạnh báo cáo. Có cảnh báo Hypothesis dùng database trong RAM vì repository mount chỉ đọc; không có ca kiểm bị bỏ qua trong lượt bốn ca này. Kết quả này là kiểm tập trung, chưa thay cho kết luận 11/11 CI trên SHA bàn giao cuối. Các lượt CI cũ bị hủy được giữ đúng trạng thái lịch sử.
