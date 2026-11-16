# Kết quả nghiệm thu J01 của Kiên và Quân ngày 16 tháng 11 năm 2026

**J01 đạt 4/4 mục phối hợp trên bản staging LAN đã nhận diện, kèm ba công việc UI tiếp theo có người phụ trách.** Kết luận dựa trên tiêu chí gốc trong `Aletheia-Kien-API.zip`, bằng chứng kỹ thuật của Kiên và thư xác nhận cùng ba trace/ảnh của Quân. Đây là nghiệm thu nền tích hợp và lát chức năng thật của J01; không phải nghiệm thu release cuối hay capacity 50k.

Biên nhận đối chiếu chung: `j01-lan-kien-quan-20261116-031448`. Mã được lập khi nhận phản hồi, không phải trường đã được recorder MacBook ghi trước. Thư gốc và mọi trace được giữ nguyên tại [quan-reply](quan-reply/), có SHA-256 từng tệp. Các nội dung đề nghị trong thư được ghi nhận là phản hồi của Quân, không tự thực hiện thành lệnh thay đổi mã hoặc secret.

## Kết luận từng mục

| Mục | Kết quả | Bằng chứng và phạm vi |
| --- | --- | --- |
| J01.01 | Đạt | Quân đồng ý nền `fa46dd8cbb2239c6f116456894a2009b45846245`, lineage UI-b `93e6d635…`, manifest staging và phân công. Kiên giữ backend/domain/schema/producer; Quân giữ UI/consumer, bao gồm hai sửa staging. Hai patch chưa commit/push được nhận diện riêng; SHA nền không đại diện nội dung patch. |
| J01.02 | Đạt trên contract hiện tại | Quân đồng ý fixture/schema/contract, `question_chars=2000`, layout A mặc định và B lựa chọn đọc. Hồ sơ Kiên có producer 105/105, consumer 18/18 và 17 fixture hợp lệ; đây là các bộ riêng, không cộng thành tổng ca độc lập. Layout A/B được Quân chạy trên kết quả thật và cung cấp ảnh. |
| J01.03 | Đạt | Hai bên đã chạy UI → source → preview → mapping → confirm → analyze → result/reload trên staging có OIDC, PostgreSQL và worker thật. Job MacBook được đọc lại trực tiếp trong PostgreSQL là `succeeded`, output trỏ đúng result; source/hash và record khớp IDs trong trace. |
| J01.04 | Đạt việc thống nhất kế hoạch và quyền thử | Quân đồng ý Docker trên máy Kiên, MacBook ngoài host, synthetic data, cùng LAN, 0 đồng dịch vụ mua mới, mục tiêu và SLO của đề. Hai profile hành vi còn là giả định đến khi khóa campaign. Fanout hiện tại đã đo và có owner xử lý trước J06/J07; không nhận nó thành ngân sách capacity đã đạt. |

Tiêu chí nguyên văn được lưu tại [original-j01-criteria.json](quan-reply/original-j01-criteria.json). Tracker gốc trong ZIP không bị sửa; các báo cáo 10–15/11 là lịch sử. Các J khác không được nâng trạng thái từ lượt này.

## Nhận diện bản chạy

- Nền Git `fa46dd8cbb2239c6f116456894a2009b45846245`. Hồ sơ CI nền 11/11 và Quân xác nhận lại cùng SHA; lần đọc phản hồi này không chạy lại CI. `main` không được thay đổi.
- Runtime identity `acbe153cee890b3b5c7c1a8d1a36d100f9ba8cdbce6366005b511a9a7dc5a529`; image `sha256:35ab7a8638b1f3ef7134bfeb87a4bbb6aac0a0101d74857cdaabafee251b8add`.
- UI build identity `e160a882952708ca4e1c8ebbc6439e902a43b40fcd8ba37c50fbd35d575ae3ab`, gồm hai sửa staging được nhận diện bằng patch/manifest trong gói. CI nền không bao phủ hai sửa chưa commit này.
- Gói gửi Quân SHA-256 `60811526cc51c39cc8f217e88ca1ed7df3eb0bd306ea1b547a0479a0eb2ae813`. Quân báo 50/50 tệp khớp checksum. Source ZIP 662 byte, SHA-256 `bc8328265b884be112c6f782d928969f77368f97f41d0dac53b8fa203dac3981`; đối chiếu PostgreSQL lại khớp.

## Phương pháp và số lượt

Quân chạy **một hành trình đầu cuối** ngày 16/11/2026 từ 03:14:48 đến 03:16:01 ICT, MacBook Pro M1 RAM 8 GB, Chrome 157 hồ sơ thử, Wi-Fi `192.168.2.52`, tài khoản synthetic u0. Ba tệp JSON là ba đoạn của cùng lượt, không tính thành ba lượt đầu cuối. Có thêm hai kiểm tra layout A/B trên kết quả này. Cộng với lượt Chrome của Kiên đã lưu trước đó, hồ sơ có hai lượt đầu cuối do hai người thực hiện, không phải tải đồng thời nhiều người dùng.

| Thao tác | MacBook Quân | Kiên đã lưu trước đó |
| --- | ---: | ---: |
| Mở workspace | 468 ms | 1747 ms cho login OIDC và vào workspace |
| Mở màn Connect | 183 ms | Không tách đo |
| Upload và preview | 3392 ms | 2969 ms |
| Confirm mapping | 3000 ms | 2778 ms |
| Analyze đến kết quả dùng được | 2621 ms | 3163 ms |
| Reload kết quả | 593 ms | 806 ms |
| Layout A chọn claim và panel nguồn | 815 ms | Không tách đo |
| Layout B đọc liên tục | 1215 ms | Không tách đo |

Mỗi thời gian là **một mẫu**, không phải p95/p99, không dùng để so tốc độ hai máy: mốc login/workspace khác nhau và đồng hồ hai host chưa được đồng bộ. Thời gian DB không dùng để trừ trực tiếp thời gian browser.

Mapping cuối nằm trong `b.mapping` và hai ảnh mapping: `dataset.csv`, target `target`, identifier `id`; `metrics.csv`, name/value/run; step None; class `class`, unit `unit`; metric `độ_chính_xác`, Higher is better, threshold `0.08`, unit `%`; giữ cả 5 run `lần-0` đến `lần-4`, reference `lần-0`. `a.form` là trạng thái trước khi điền xong, còn các ô trống; không dùng nó để suy ra mapping cuối thiếu trường.

Câu hỏi `What is observed in these validated metrics?` trả 10 claim, kết luận `Did not conclude`, `Cause not established` và ghi rõ causal attribution chưa xác minh. Reload giữ URL/IDs; trace ghi Checking your access hết ở khoảng 212 ms, kết quả giữ đến hết khoảng theo dõi 10 giây.

## Request và bằng chứng trực tiếp

| Đoạn trace | Tổng entry | HTTP thành công | Request bị hủy |
| --- | ---: | --- | ---: |
| a upload preview | 17 | 11 × 200, 1 × 201, 1 × 202 | 4 × ERR_ABORTED |
| b mapping analyze reload | 30 | 28 × 200, 2 × 202 | 0 |
| c layout | 3 | 3 × 200 | 0 |
| Tổng ba đoạn | 50 | 46 phản hồi 2xx | 4 |

Cả ba `console` đều rỗng; đoạn b không có alert sau confirm. Bốn request bị hủy nằm ở session/workspaces/members/invitations khi chuyển màn trước upload; các bước nghiệp vụ sau đó hoàn tất. Giữ nguyên dấu vết, không gọi cả 50 entry là 50 HTTP thành công.

IDs của MacBook:

```text
workspace ws_k17_stgda2093353a73_0000
job       job_98ac275417fb416891954bb623d5a6c0
project   p3-project-8d30974979d3ed40cd431a04c510ea910843f7a1fae377e2b22193855a452da6
snapshot  upload-snapshot-442879879adf47a5bb80fd2ee1fb6bfcdee8f38efdce23571668409f1f9ccec7
result    p6-result-0175b5c38d5c7caf1ee039798e0c0ee6875d13d4809b5663974d81fc4f8caef1
```

Workspace/project/snapshot/result trùng lượt Kiên trên cùng ZIP và câu hỏi; job là mới. Kiểm đọc trực tiếp PostgreSQL được lưu tại [postgres-witness-quan.json](quan-reply/postgres-witness-quan.json). Schema thực là `k17_seed_97e97d480c774754bfa46bbc8ff61369`; lần dò ban đầu ở public/stg không có job, sau đó đã xác định đúng schema, không sửa DB. Lượt kiểm chỉ đọc, xác nhận analyze succeeded, đúng result ID, source ZIP 662 byte/hash và record result tồn tại. Trace MacBook không có hash response body; không suy ra đã hash độc lập toàn bộ response trên máy Quân.

## Layout và giới hạn bằng chứng

Layout A có claim trong hash, panel mở, heading `Run lần-0, metric độ_chính_xác`; layout B có `layout=reading`, panel đóng, tiêu đề giữ và 10 claim. Ảnh B thể hiện nguồn ngay dưới claim.

`c.B.sources_inline=0` được giữ nguyên: bộ đếm này chưa được xác thực, script selector không đi kèm. Mã UI render nguồn inline trong `.wl-inline` khi `layout=reading`, phù hợp ảnh, nhưng không kết luận số card inline được máy đếm đúng. Ảnh được đặt tên layout A và ảnh sau reload **trùng byte**; giữ cả hai như đầu vào người dùng, không tính là hai ảnh độc lập hay hai lượt. Những hạn chế này không phủ nhận việc hai layout hiển thị trong trace/ảnh và xác nhận của Quân.

## Việc chuyển tiếp và điều kiện trước campaign hoặc release

| Việc | Người giữ | Điều kiện hoàn tất |
| --- | --- | --- |
| Đưa hai sửa staging HostedApp/session vào nhánh UI | Quân | Gửi SHA ghép và CI bao phủ đúng nội dung trước nghiệm thu release; J01 hiện nhận diện bằng patch/manifest. |
| Thống nhất giới hạn câu hỏi đầu và follow-up | Quân | Bản chạy có caps 2000 nên cả hai ra 2000. Hiện logic min(2000,caps) và min(4000,caps) sẽ lệch nếu caps tăng; cần thống nhất và kiểm lại trước tăng caps. Lựa chọn 2000 không tự sửa yêu cầu 4000 của tài liệu cũ. |
| Giảm fanout kiểm quyền và đo lại | Quân chủ trì UI, Kiên đối chiếu server | Phải khóa trước J06/J07. 65 request/60 giây/tab đến từ session/capabilities/header khoảng mỗi 3 giây ở tab kết quả hiện trên màn hình. 50k tab tương tự ngoại suy khoảng 54.167 request/giây; đây là kịch bản tham chiếu, không phải tải đo hoặc ngân sách được chấp nhận. Tab ẩn không poll. Mọi phương án phải giữ policy revoke ≤5 giây; không chỉ nới lease để giảm RPS. |

Quân đã đồng ý ký J01 với các ghi chú này. J01 không nhận ba việc chuyển tiếp là đã triển khai; chúng còn ở checklist UI/campaign/release. K14.01/K14.05, K17, K18 và các J khác giữ kết luận riêng. Không thay đổi code đã pass, không chạy lại campaign, không commit/push trong lần đối chiếu này.

Mã K16 đã được thu hồi theo biên bản trước. Đề nghị đổi mật khẩu u0 trong thư là việc vệ sinh tài khoản cần xử lý riêng; lần đọc phản hồi này chưa đổi password hoặc làm mất session đang dùng.
