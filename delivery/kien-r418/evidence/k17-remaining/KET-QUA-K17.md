# Kết quả bổ sung K17 ngày 15/11/2026

**K17 còn mở 6/6 tiêu chí nghiệm thu tải chính.** Các luồng backend còn thiếu đã có phép thử riêng qua HTTPS. Hai bài calibration dài hơn với 100 principal đã chạy hết, giữ đủ mẫu số và đối chiếu tác động. Tỷ lệ thao tác đúng hạn của chúng thấp hơn 99,9%, nên kết quả chức năng của pytest được ghi cùng với kết quả SLO thất bại.

Nhánh `product-api`, HEAD `fa46dd8cbb2239c6f116456894a2009b45846245`. Công việc lần này bổ sung driver, chạy kiểm thử và lưu chứng cứ. Chưa commit hay push. Đây là báo cáo tiến độ kiểm thử K17. Hồ sơ nghiệm thu chung cho Bảo còn chờ đủ tiêu chí.

**Môi trường và phương pháp**

- Docker Engine 28.0.4 báo 16 CPU và 8.135.196.672 byte RAM. Đĩa Windows còn khoảng 19 GB lúc kiểm tra. Các ứng dụng Docker có từ trước vẫn chạy trong lúc đo.
- Cụm HTTPS cho bài lifecycle dùng hai API, hai worker, PostgreSQL 16.15, Keycloak 26.8.2 và ingress TCP trên bridge nội bộ riêng. Mỗi API được cấp 0,75 core/512 MiB/pool 4. Mỗi worker có 1 core/640 MiB/pool 2. Container máy phát có 1 core/512 MiB.
- Image lifecycle là `sha256:35ab7a8638b1f3ef7134bfeb87a4bbb6aac0a0101d74857cdaabafee251b8add`. Runtime SHA-256 là `acbe153cee890b3b5c7c1a8d1a36d100f9ba8cdbce6366005b511a9a7dc5a529`.
- Seed pilot được nhập thật, gồm 30 principal, 2 workspace, 6 project, 6 snapshot và 6 result. Source S/M/L có 1.000/10.000/100.000 dòng. Claim trên result lần lượt 2/16/64, node 6/34/130. Manifest ghi hash của ZIP, object, scope và dữ liệu đã nhập.
- Mỗi bài native chạy một PostgreSQL riêng, một API, một worker hai slot và process máy phát riêng qua TLS kiểm CA. Container thử có trần tổng 4 core/2 GiB, PostgreSQL riêng 1 core/512 MiB. Mã được kiểm hash rồi chép sang filesystem Linux trước khi chạy. Image công cụ là `sha256:6aed97ec4d6691527a72667ecdac67e4964ddcd6c1e4077aab9d8073a249b797`.
- Calibration dùng 100 principal riêng, năm role, tỷ lệ cỡ 60/35/5, warm-up 5 giây và steady 40 giây. Open giữ 20 business action/giây. Closed giữ think-time 5 giây theo profile. Cùng một mã ứng dụng và cùng chính sách deadline. Các job được nhận và export nguyên tệp có mẫu số riêng.

**K17.02 — Các luồng backend bổ sung**

| Phần | Kịch bản và kết quả |
|---|---|
| P06/P29/P30 | Tạo upload, phát lại cùng khóa, gửi part, mở client mới để đọc tiến độ đã commit, gửi các part còn thiếu và phát lại part cũ. Hoàn tất rồi phát lại completion, cùng source ID. Sáu ZIP trong lượt đạt S/M/L có kích thước khoảng 41,3 KB/406 KB/4,08 MB và số part 1/4/32. Hash và toàn bộ byte được đối chiếu ở UploadStatus. |
| P31 | Ba upload riêng bị hủy sau part đầu. Trạng thái đọc lại là cancelled, source chưa được công bố. |
| P09 | Ba confirm dùng mapping từ đúng dữ liệu tổng hợp của fixture. Cả ba job thành công attempt 1. Phát lại cùng intent cho đúng job đã nhận. |
| P10 | Mỗi cỡ chạy refresh cùng source để kiểm unchanged, sau đó upload thế hệ khác và kiểm new_snapshot. Giữ đúng project, snapshot trước/sau và revision. |
| P35 | Đổi tên project riêng bằng If-Match, kiểm tên mới và snapshot hiện tại. |
| P25 | Xóa ba project riêng sau refresh, đọc lại state deleting. Lượt bổ sung chạy analysis và export trên project dùng rồi xóa. Trước xóa, tải đủ byte và kiểm SHA-256. Sau xóa, cả header result và download cũ trả 404. |
| P38 | Thay quota workspace theo revision, GET đọc lại chính xác, rồi khôi phục giá trị. |
| P36/P28 | Tạo lời mời cho identity có thật ở workspace khác, chấp nhận với role viewer, đổi thành reviewer rồi thu hồi. Request capability sau thu hồi bị 403, quan sát sau **149,24 ms**, dưới mốc 5 giây. |
| P02 | Đăng nhập thật qua Keycloak, logout HTTP 204, kiểm hai cookie có Max-Age=0. Client khác dùng cookie cũ nhận 401. |
| P21 | Dừng hai worker của riêng cụm thử, nhận một preview job thật, hủy lúc queued. Đọc lại cancelled, output trả 404. Khởi động lại hai worker sau phép thử. |

`imports-03` đạt **215/215 request đúng kết quả dự kiến** trong **62,880 giây**. Lượt này có 18 phản hồi 201, 182 phản hồi 200, 15 phản hồi 202. Tenant nền đọc **270/270** lần thành công, trung bình/p95/cao nhất **127,44/199,78/1.710,50 ms**. Đây là thời gian đo bài riêng. Đối chiếu SLO trong tải liên tục nằm ở bảng calibration bên dưới.

| Cỡ | Thời gian chờ confirm tới output | Thời gian chờ refresh mới tới output |
|---|---:|---:|
| S | 2,269 giây | 2,342 giây |
| M | 2,239 giây | 2,318 giây |
| L | 7,212 giây | 6,549 giây |

`delete-integrity-02` bổ sung xác minh result/export sau xóa. Phiên fixture đã hết idle ở lượt trước được lấy lại bằng đăng nhập Keycloak. Preflight này được ghi riêng với số request nghiệp vụ. Tenant nền có **75/75** lượt đọc thành công, trung bình/p95/cao nhất **110,92/165,38/193,04 ms**.

`management-01` giữ nguyên một lượt tổng thất bại do oracle logout đòi HTTP 200 trong khi cấu hình thực trả HTTP 204. Slice settings/invitation/membership trước đó đã đạt, nằm trong cases của result. `logout-01` chạy riêng và đạt. `cancel-01` đã hủy job đúng nhưng oracle đòi output 409. `cancel-02` dùng hợp đồng 404 khi không có output và đạt.

Các luồng bổ sung là fixture dùng riêng, có các tác động thật và kiểm trạng thái. K17.02 còn cần đưa toàn bộ luồng vào chiến dịch tải chính với đầy đủ cohort/denominator. Các route connector P32–P34 vẫn theo capability hiện có. Việc triển khai consumer trình duyệt và giả định tỷ trọng có nguồn riêng.

**K17.03/K17.04 — Calibration thực tế**

| Chỉ số | Open · native-open-02 | Closed · native-closed-01 |
|---|---:|---:|
| Principal được xác thực riêng | 100 | 100 |
| Warm-up / steady | 5 / 40 giây | 5 / 40 giây |
| Offered cả warm-up và steady | 900 | 830 |
| Terminal action / pending action | 900 / 0 | 830 / 0 |
| Positive action trong steady | 782 | 715 |
| Positive hoàn tất đúng deadline | 729 | 682 |
| Tỷ lệ đúng deadline trong steady | **93,2225%** | **95,3846%** |
| Ngưỡng thành công ≥99,9% | Chưa đạt | Chưa đạt |
| Rejection cả warm-up và steady | 49 | 31 |
| Timeout cả warm-up và steady | 9 | 2 |
| Expected denial cả warm-up và steady | 20 | 14 |
| Job đã ACK / quan sát succeeded | 42 / 40 | 32 / 32 |
| Job chưa thấy terminal lúc dừng đo | 2 | 0 |
| Export tải đủ và kiểm hash | 37 | 52 |
| HTTP request thực tế | 1.263 | 1.179 |
| Cửa sổ lấy mẫu generator | 49 mẫu trong 48,137 giây | 49 mẫu trong 48,088 giây |
| CPU generator tăng trong cửa sổ | 3,843 core-second | 3,297 core-second |
| CPU generator trung bình | 0,0798 core | 0,0686 core |
| RSS peak, gồm seed/preflight của process | 206.164 KiB | 201.268 KiB |
| FD generator peak tại mẫu | 51 | 51 |

Cả hai run đối chiếu được `planned = offered + not_offered`, `offered = terminal + pending` và tổng terminal outcome. Các snapshot 30 giây ghi intended-engaged, actually-started, completing, waiting và idle. Sau khoảng 7 giây, run open đã quan sát đủ 100 intended-engaged trong cửa sổ. Số này thuộc 100 principal của pilot.

Trong run open, export bị 49 rejection gồm S/M/L, có timeout ở analysis S, graph S và header S/M. Trong run closed, export có 31 rejection và header S/M có hai timeout. Log ghi nhận report_render bị hủy quanh giới hạn adapter và API có lúc chờ pool. Các dấu hiệu này cần được đo tiếp trên cấu hình triển khai đã chọn để quyết định số replica, budget và cách tối ưu. Các mức tải và deadline đã đo được giữ nguyên trong receipt.

49 mutation chưa rõ của run open và 31 của run closed được kiểm metadata sau khi cả process API/worker đã thoát. Các witness đó đều không có effect sau quiescence. Kết quả deadline/rejection ban đầu giữ nguyên. Hai job đã ACK còn thiếu quan sát terminal trong run open được ghi rõ. Run này chưa có chứng cứ drain cho hai job đó.

Pytest của mỗi calibration trả thành công vì các assertion chức năng, scope, integrity và accounting đạt. `calibration100-result.json` của chúng vẫn ghi `observed_subset_success_target_met=false`. Kết luận SLO của cả hai run là chưa đạt. `summary.json` giữ cả hai loại kết quả.

Máy phát và SUT có process riêng cùng host. Mẫu RSS bao gồm cả seed và preflight, chưa đo chi phí tăng thêm trên từng VU. Bằng chứng NIC/clock/headroom toàn máy tại nhu cầu đích còn thiếu. Mỗi operation × size vẫn thiếu 10.000 mẫu để đánh giá p99 theo guide.

**K17.05 — Fault có tải nền**

| Sự cố native PostgreSQL | Khoảng thời gian fault | Lượt đọc tenant khác trong fault | Độ trễ đọc cao nhất | Tác động trước hồi phục / sau hồi phục |
|---|---:|---:|---:|---|
| Khóa dòng project | 263,39 ms | 17 | 24,72 ms | 0 mutation / 1 delete và replay cùng receipt |
| Cạn pool một slot | 251,77 ms | 21 | 8,07 ms | 0 mutation / 1 delete và replay cùng receipt |

**2/2 ca fault đạt**, không có lượt đọc nền thất bại, payload/hash đúng và pool hết waiter sau hồi phục. Bộ kiểm này chạy tại repository native PostgreSQL. Ma trận `database-fault-matrix.csv` giữ đủ **40 ca DB + 26 ca fault**, trạng thái lịch sử và các receipt bổ sung. Các cột primary-workload của lần này đều còn false.

**K17.01 — Dữ liệu vật lý và model lớn**

Adapter hiện tại tạo snapshot và result thật nhưng model metric đang có giới hạn tối đa 64 claim. Pilot đã nhập dữ liệu với các hash có trong manifest. Nó còn cách bộ đích 50.000 principal, 2.000 workspace, 10.000 project, 200.000 snapshot và 2.000.000 result.

Đã chạy lại prototype cửa sổ số học, giữ đúng trạng thái prototype trong receipt.

| Cỡ | Dòng nguồn | Claim / evidence | Node / edge | JSON thật đã tạo |
|---|---:|---:|---:|---:|
| S | 1.000 | 100 / 100 | 202 / 300 | 252.238 byte |
| M | 10.000 | 1.000 / 1.000 | 2.002 / 3.000 | 2.520.603 byte |
| L | 100.000 | 10.000 / 10.000 | 20.002 / 30.000 | 25.258.208 byte |

Các file prototype có hash nguồn, hash từng cửa sổ và tính mean/min/max từ dữ liệu tương ứng. Chúng vượt mốc node cần thiết bằng các claim/evidence thật trong prototype. Chưa nối model đó vào canonical serializer, worker, projection, capability và export của sản phẩm. Chính sách model và contract cần được xác định khi triển khai. Bài này chưa nhập đầy đủ catalog đích. Bộ bảo vệ primary của CLI vẫn từ chối manifest thiếu quy mô/geometry.

**Kiểm công cụ và lịch sử**

- Bộ unit/seed/rotation có 64 ca. Lượt `native-remaining-01` đạt 62, lỗi hai ca CLI do bản chép nguồn Linux thiếu thư mục scripts. Driver đã thêm các script K17 vào manifest nguồn. Chạy riêng đúng hai ca còn lỗi ở `native-cli-02` đạt **2/2**. Các receipt cũ giữ nguyên.
- Bài rotation dùng thời gian thực, policy rotation 1 giây/idle 60 giây/hard expiry 120 giây. Có **8 lần xoay cookie** qua **8 HTTP 200**. Context giữ nguyên, cookie gốc trả 401, cookie hiện tại trả 200.
- `imports-01` dùng source trùng dữ liệu seed. Confirm kết thúc failed/conflict. Kịch bản tạo project rồi xóa cần dữ liệu riêng, nên lượt sau dùng seed ngẫu nhiên được ghi theo run. Ca trùng byte đó còn nguyên trong DB thử và history.
- `imports-02` đã confirm thành công nhưng driver đọc nhầm output như snapshot đầy đủ. Hợp đồng confirm trả snapshot_id. `imports-03` tìm project mới qua catalog, kiểm snapshot của nó rồi mới tiếp tục và đạt cả ba cỡ.
- `native-open-01` dừng trước phát tải vì tmpfs 768 MiB thiếu mức dự phòng 512 MiB ngoài trần seed 500 MiB. Driver tăng tmpfs lên 1,5 GiB, giữ nguyên guard seed. `native-open-02` sau đó chạy đủ và ghi kết quả tải thất bại ở trên.
- `delete-integrity-01` dừng khi phiên seed hết idle. Driver dùng luồng đăng nhập thật trước bài dedicated, `delete-integrity-02` đạt.
- Driver host `k17_run_lifecycle.py` kiểm label/owner/network/image của cụm K17 trước khi chạy. `driver-entrypoint-01` gọi tới lúc API đang khởi động và bị lỗi kết nối. Sau khi thêm chờ readiness vào preflight, `driver-entrypoint-02` chạy qua entrypoint này và đạt logout. Mẫu số business request giữ riêng với preflight.

Ba công cụ mới gồm `k17_lifecycle_probe.py`, `k17_run_lifecycle.py` và `k17_verify_remaining.py`. Driver host nhận đường dẫn state của cụm K17 đang chạy, tên lượt mới và một phase trong `imports`, `management`, `logout`, `cancel`, `delete_integrity`. Với phase cancel, nó dừng hai worker đúng owner và khởi động lại trong finally. Công cụ native nhận thư mục đầu ra mới và nhóm `faults`, `open`, `closed`, `remaining` hoặc `cli`. Ruff check và format check được chạy trên cả ba.

Sau khi lưu bằng chứng, cụm K17 được dừng. Volume và state được giữ để tái lập. Cả bảy service K14 có từ trước vẫn chạy, được đối chiếu riêng trong `staging-stop.json`.

**K17.06 — Capacity và chi phí**

Hai calibration mới chưa xác lập được mức tải đạt đầy đủ SLO. Do đó chưa có verified envelope để tính chi phí trên một journey đạt chuẩn. Chi phí điện/phần cứng, DB, object, bandwidth, generator và IdP chưa có đơn giá hoặc phân bổ được cung cấp, nên được ghi `unpriced`. Lần này không tạo tài nguyên cloud hay dịch vụ trả phí.

**Các điều kiện còn thiếu để đóng sáu mục**

| Mục | Còn thiếu |
|---|---|
| K17.01 | Nối model có geometry đích vào toàn tuyến sản phẩm, nhập catalog vật lý đủ số lượng và kiểm source/hash/scope trên tài nguyên đủ chứa dữ liệu. |
| K17.02 | Chạy toàn bộ luồng đã khai báo trong chiến dịch chính, có cohort và mức nhu cầu đủ 50k, session maintenance và consumer receipts tương ứng. |
| K17.03 | Hiệu chuẩn theo nhu cầu đích, bổ sung NIC/clock/headroom và phân biệt rõ tài nguyên generator với SUT. |
| K17.04 | Xử lý điểm nghẽn, rồi chạy đủ ramp 5k/10k/25k/50k, ba normal closed và ba open theo 10 phút warm-up/30 phút steady, stress/spike và soak 2 giờ. |
| K17.05 | Chạy và đối chiếu toàn bộ DB40/F26 gắn với tải đã khai báo, có telemetry và reconciliation theo từng ca, đủ process/storage tier. |
| K17.06 | Có mức tải thực tế đạt SLO trước, rồi tổng hợp chi phí có nguồn và tài nguyên dự phòng trên journey hoàn tất đúng. |
