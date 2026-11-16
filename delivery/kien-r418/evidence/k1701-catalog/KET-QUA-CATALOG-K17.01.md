# K17.01 — Nhập và xác minh catalog đích

Ngày 15/11/2026. Catalog đầy đủ được thiết kế để sinh trực tiếp trên máy kiểm thử, nơi có PostgreSQL và kho tệp kết quả. Máy của Kiên chỉ cần giữ mã nguồn, kế hoạch, thông số đo và manifest đã lọc bỏ dữ liệu đăng nhập. Các tệp phiên synthetic và dữ liệu thử có thể giữ trên máy chạy.

Kế hoạch `docs/k17-seed-window-catalog.json` ghim model theo cửa sổ quan sát và giữ tỷ lệ project S/M/L 60/35/5 của kế hoạch hiện có. Tỷ lệ này là lựa chọn của kế hoạch, còn tài liệu thử tải mô tả 60/35/5 là mix tham chiếu của tác vụ. Hai tỷ lệ có thể được thiết kế riêng cho catalog đã lưu và tác vụ phát tải.

| Đơn vị | Catalog đích | S | M | L |
| --- | ---: | ---: | ---: | ---: |
| Principal riêng | 50.000 | 30.000 | 17.500 | 2.500 |
| Workspace | 2.000 | | | |
| Project | 10.000 | 6.000 | 3.500 | 500 |
| Snapshot | 200.000 | 120.000 | 70.000 | 10.000 |
| Result | 2.000.000 | 1.200.000 | 700.000 | 100.000 |
| Dòng metric | 1.820.000.000 | 120.000.000 | 700.000.000 | 1.000.000.000 |

Giới hạn số bản ghi chuẩn là 2.390.000, gồm 190.000 bản so sánh giữa các thế hệ. Bộ nạp tạo ZIP vật lý theo từng project và thế hệ, chạy mapping và refresh thật, lưu kết quả và chỉ mục phân trang, đọc lại quan hệ snapshot/result theo workspace và băm lại nguồn cùng các tệp kết quả. Mỗi result trên một snapshot có câu hỏi và định danh riêng. Kế hoạch thêm một workspace 50 thành viên và một workspace 500 thành viên bằng principal hiện có trong catalog.

Mẫu S/M/L từ lần kiểm tích hợp model cho kích thước ZIP, snapshot và result. Nhân kích thước theo số record của từng cỡ cho **6.306.739.620.000 byte**, khoảng **6,31 TB**, trước khi cộng bản so sánh, chỉ mục PostgreSQL, WAL, dung lượng tạm và bản sao lưu. Đây là ước tính theo số mẫu và phân bổ trên. Ổ C có **67.532.652.544 byte trống** lúc kiểm. Bộ nạp đã từ chối preflight trước khi tới bước mở kết nối DB hoặc tạo thư mục đích. Biên nhận `full-target-preflight.json` lưu kết quả và SHA-256 của kế hoạch.

Mẫu vật lý đã nhập trên PostgreSQL Docker riêng, gồm 20 principal, 2 workspace, 4 project, 8 snapshot và 8 result. Mỗi project có hai thế hệ, trong đó mỗi thế hệ được nhập từ ZIP riêng. Tổng cộng 224.000 dòng metric, tạo 22.400 claim/evidence. Bộ nạp đọc lại cả 8 ZIP và 20 tệp đối tượng, gồm 8 snapshot, 8 result và 4 bản so sánh. Tất cả khớp SHA-256. PostgreSQL xác minh số lượng projection và quan hệ giữa result, snapshot, workspace và nguồn. Thời gian nhập và xác minh 378,52 giây. PostgreSQL dùng 2 CPU và tiến trình nạp có giới hạn RAM 2 GiB. Hạ tầng mẫu và dữ liệu tạm đã dọn sau khi lưu số đo.

| Dung lượng mẫu đã đo | Byte |
| --- | ---: |
| Tệp chuẩn trong kho đối tượng | 145.442.424 |
| PostgreSQL, gồm bảng, TOAST và chỉ mục | 294.518.784 |
| Kế hoạch, manifest và danh mục riêng của bộ nạp | 52.675 |
| Tổng | 440.013.883 |

Ước tính theo mẫu vật lý `storage-sizing.json` tách kho đối tượng khoảng **6,23 TB** và các bảng PostgreSQL chính khoảng **21,64 TB**, tổng **27,87 TB** với tỷ lệ 60/35/5 trong kế hoạch đang dùng. Ước tính lấy kích thước trung bình theo cỡ cho từng loại tệp. Bảng claim/evidence/graph và quan hệ citation được ngoại suy theo số claim, từ 22.400 trong mẫu lên 1,82 tỷ ở catalog đích. Kích thước thực có thể thay đổi theo đặc tính chỉ mục khi dữ liệu lớn, tỷ lệ S/M/L và cách tổ chức lưu trữ. WAL, dung lượng tạm, các bảng còn lại và bản sao lưu chưa nằm trong con số này.

Kiểm thử độc lập trên PostgreSQL xác minh 500 principal riêng tạo đúng hai nhóm 50 và 500 thành viên. Kiểm hồi quy xác nhận bộ nạp giữ phiên owner còn hiệu lực, cấp phiên mới cho cùng owner khi phiên cũ hết hạn, và từ chối dùng manifest còn đánh dấu `INCOMPLETE` cho bài tải chính. Chi tiết mẫu vật lý nằm tại `physical-pilot-01/window-catalog-measurement.json`.

Quy trình nhập ngoài máy dùng target PostgreSQL dành riêng đã chạy migration và đường dẫn kho tệp trống. Hướng dẫn chạy tại `docs/k17-native-seed.md`. Cần kiểm cả ổ chứa tệp lẫn ổ PostgreSQL trên host đích. Sau khi nhập, người vận hành giữ bản dữ liệu và `actors.jsonl` tại host đích, còn kết quả đối chiếu chỉ cần chuyển qua manifest và báo cáo công khai.

**Trạng thái K17.01.** Mẫu nhập và đối chiếu đã đạt. Catalog đích 50.000 principal và 2.000.000 result vẫn chưa được nhập hoặc xác minh. Cần target có tài nguyên và một phép đo quá trình nhập tại đó để chốt tốc độ, hạn chạy và dung lượng thật. Thay đổi đang ở worktree cục bộ.
