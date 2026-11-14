> Cập nhật 10/11/2026: đọc cùng phần tài nguyên trong
> `j01-current-review-20261110.md`. Đã có Keycloak/PostgreSQL local thực;
> sizing/cost và staging chung vẫn chưa được chốt. Các ô "chưa có IdP thử"
> bên dưới là lịch sử, không còn là trạng thái hiện tại. Không coi nguồn số
> tham chiếu là capacity đã đạt hoặc kế hoạch đã được hai bên chấp nhận.

# J-01 Workload và tài nguyên dự thảo

Trạng thái: **giả định để cùng Quân/Bảo chốt**, chưa đo, chưa cấp quyền thử tải hoặc ngân sách. Nguồn: `02-Phoi-hop/Thu-tai-50k.md` trong gói giao việc. Không gọi đây là capacity đã đạt.

## Đơn vị và phép tính xuất phát

| Đại lượng | Giá trị giả định | Ý nghĩa và giới hạn |
|---|---:|---|
| Principal tham gia | 50.000 | Unique authenticated principals theo định nghĩa cửa sổ 30 giây; nhiều tab không thành nhiều người |
| Business action | 5.000 action/giây | Ví dụ 50.000 người × 0,1 action/người/giây; chưa phải hành vi quan sát |
| HTTP tổng | khoảng 15.000 request/giây | Giả định 3 request/action đã gồm fanout/status; phải thay bằng waterfall đo được, không cộng trùng |
| Heavy job | 20 job/giây | Mix tham chiếu 15 analysis, 3 import/refresh, 2 export; chưa có bằng chứng đạt |
| Job đang chờ/chạy | khoảng 400 | 20 job/giây × mean lifetime 20 giây, chỉ là ví dụ Little’s law |
| Poll job | khoảng 200 request/giây | 400 waiter / 2 giây, chỉ cộng riêng nếu chưa tính trong fanout |
| Session check | khoảng 833 request/giây | Kịch bản 50.000 tab đều kiểm mỗi 60 giây; tính tương tác/focus và multi-tab theo thực đo, tránh tính đôi |

Theo dõi scheduled → started/sent → admitted → completed/terminal, cùng dropped, timeout, rejected, pending và drain. Poll/session refresh không tự là business action; 50.000 tab không tự chứng minh 50.000 principal hoạt động. Jitter chỉ giảm đồng pha theo thiết kế, chưa là số đo burst.

## Cần chốt trước staging và tải

1. Quân: journey mix, role, think time và browser waterfall; đánh dấu assumption nếu chưa có quan sát.
2. Kiên: API/worker/DB/object/IdP, fanout, session check, CPU/job, connection pools, bytes và giới hạn queue; đo trên release và fixture được xác định.
3. Cả hai/Bảo theo phân quyền: staging và generator, quyền thử, ngân sách/chi phí, người giữ secrets, lịch và điều kiện dừng. Không tự thuê cloud hoặc chạy lên hệ thống không được phép.
4. Khóa release SHA, frontend build, schema/config và dataset; có 50.000 principal synthetic, tối thiểu 2.000 workspace, 10.000 project, 200.000 snapshot và 2 triệu result cho bài catalog chính theo guide. Quy mô này chưa được tạo hoặc chạy trong J-01.
5. Chốt SLO theo guide, same-release schedule và cách reconcile outcome. Chạy calibration nhỏ trước; không nhận test hiện tại là bằng chứng 50k.

## Budget để chốt cùng chủ môi trường

Reference HTTP mix từ guide: 13.000 read + 1.080 light mutation + 900
status/event read + 20 heavy submission = 15.000 request/giây. Không cộng
thêm 20 submission hoặc polling nếu chúng đã nằm trong mix này.

Trường hợp 50.000 tab hiển thị đều hỏi session mỗi 60 giây tạo mean khoảng
833,33 request/giây, dùng khoảng 92,59% budget status/event 900/giây và chỉ
còn khoảng 66,67/giây trước khi tính thêm job/event. Đây là giả định đối
chiếu ngân sách; idle tabs không chứng minh 50.000 principal làm business
action trong cửa sổ 30 giây. Lock giữa tab và jitter cần được đo, không
tự trừ request chỉ vì thiết kế có cơ chế đó.

| Tài nguyên | Công thức/đầu vào cần đo | Giá trị hiện có | Người phụ trách |
|---|---|---|---|
| API CPU | request/s × CPU-second/request / utilization mục tiêu | Chưa đo | Kiên |
| Worker CPU | job/s × CPU-second/job / utilization mục tiêu | Chưa đo theo S/M/L | Kiên |
| DB pool | mean DB concurrency, residence time, giới hạn DB | Chưa sizing; không lấy số user làm số connection | Kiên |
| Object/network | byte/action × action/s, export/upload distribution | Chưa có histogram theo size | Kiên + Quân |
| IdP/session | login/renew/logout rate, cache và revoke budget | Chưa có IdP thử thật | Kiên |
| Browser/generator | principal thật, tab coordination, generator headroom | Có giả định; chưa có trace/measurement đính kèm | Quân + Kiên |
| Chi phí/giờ | số lượng × đơn giá/giờ + storage/transfer | Chưa chọn provider/đơn giá; chưa phê duyệt ngân sách | Chủ môi trường |

Không điền 0 vào tài nguyên hay chi phí chưa biết. Trước thử tải lớn cần
ghi tên môi trường, loại instance, quota, thời lượng, trần chi phí, người
có quyền dừng và cảnh báo. Kết quả J-01 là phương án được hai bên chấp nhận;
run 50k và capacity thực thuộc J-06/J-07, không cần tự chạy tải lớn để tạo
ra vẻ J-01 đã hoàn tất.

## SLO tham chiếu cần xác nhận

Nguồn: mục 9 của `02-Phoi-hop/Thu-tai-50k.md`, không phải số đã đo:
metadata/session/page 200/500 ms p95/p99; search/subgraph 400/1000 ms;
light mutation 300/800 ms; job ACK 250/600 ms; status 200/500 ms.
SLO end-to-end, browser, size S/M/L, queue wait, correctness và deadline
được giữ nguyên theo guide; không dùng riêng HTTP latency để thay toàn bộ.
Normal 50k cần success đúng và trong deadline ≥99,9% trên eligible scheduled
actions, giữ timeout/rejected/dropped trong mẫu số. Không lấy test chức năng
local hay mean RPS để kết luận đạt SLO.
