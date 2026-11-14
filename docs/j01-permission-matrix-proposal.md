# J-01 Ma trận quyền đề xuất để review

Trạng thái **đề xuất, chưa được Kiên và Quân nghiệm thu, chưa chứng minh server enforcement**. Dựa trên ma trận ban đầu trong `02-Phoi-hop/Phoi-hop-va-nghiem-thu.md` của gói giao việc. Mọi hành động còn phụ thuộc membership, scope và grant của đối tượng; client không tự cấp quyền dựa vào role hay fixture.

| Hành động | Owner | Admin | Analyst | Reviewer | Viewer |
|---|---|---|---|---|---|
| Đọc project/result/evidence được cấp | Có | Có | Có | Có | Có |
| Tạo/import/refresh/analysis | Có | Có | Có | Không | Không |
| Review/annotation có lịch sử | Có | Có | Có | Có | Không |
| Export dữ liệu được đọc | Có | Có | Có | Có | Có, trừ policy cấm |
| Cancel/retry job | Theo scope và quyền | Theo scope và quyền | Job được quyền thao tác | Export/review job của mình | Export job được phép của mình |
| Quản lý members/settings | Có | Có, không thay owner trái policy | Không | Không | Không |
| Delete project | Có | Có | Theo project grant tường minh | Không | Không |
| Transfer owner/delete workspace | Có, giữ last-owner hợp lệ | Không | Không | Không | Không |

## Những điểm phải chốt và kiểm

- Lấy actor từ session server; kiểm membership và object grant phía server. Các câu “Có” trong bảng chỉ áp dụng khi thỏa scope và policy hiện hành.
- Lưu policy/version và ví dụ denied; kiểm last-owner, self-revoke, creator bị revoke, actor đổi role trong transaction và job/download đang chạy.
- Client dùng `allowed_actions` để hiển thị nhưng server phải kiểm lại. Scope không hợp lệ hoặc object ngoài quyền cần chính sách response đã chốt để không lộ sự tồn tại.
- Mục tiêu revoke tối đa 5 giây là yêu cầu cần đo trên API, download, event/cache và publication của job; chưa có kết quả đo trong artifact này.
- Cần Quân đối chiếu màn hình, hành vi denied/revoked, consumer fixture và Bảo xác nhận các lựa chọn có ảnh hưởng phạm vi; bản này không thay thế permission matrix được hai bên chấp nhận.
