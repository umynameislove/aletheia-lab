# Cập nhật K17 sau tối ưu export

**Bài thử 100 tài khoản riêng đã đạt 100% thao tác đúng deadline trong cả open và closed trên cấu hình hai API, một worker hai slot và PostgreSQL 2 core.** K17 vẫn còn 6/6 mục nghiệm thu tải chính. Các lượt bổ sung dưới đây kiểm điểm nghẽn vừa phát hiện bằng bộ dữ liệu calibration nhỏ.

Nhánh `product-api`, HEAD `fa46dd8cbb2239c6f116456894a2009b45846245`. Mã tối ưu nằm trong working tree, chưa commit hay push. Runtime source của hai lượt đạt có SHA-256 `d87c8d278e22944b194a0262dc17ae4099f83033907704e39bda766927d24b2b`. Source SHA trong từng result định danh mã thực thi của lượt đó.

**Thay đổi và kiểm hồi quy**

`project/import_content.py` trước đây nạp PyArrow và Parquet ngay khi import module. Worker xuất JSON cũng đi qua module này qua các dependency của ProductView. Đã chuyển hai import tới đúng hàm đọc metadata Parquet. Các giới hạn input/output, số slot renderer, quyền, sandbox, timeout và nội dung render dùng cấu hình hiện có.

- **14/14 ca đạt** trên Windows, gồm ca khởi động worker, các collector có đọc Parquet thật và bộ renderer chạy JSON/Markdown/PDF bằng process thật.
- Ca mới khởi động một Python process độc lập, kiểm module report worker đã import được mà PyArrow và NumPy chưa bị nạp.
- Bốn export JSON trước sửa và bốn export sau sửa đều có **3.587 byte**, SHA-256 `bd05e2415a582684b260376de3273383f6fc6857740d91387c0b63157f04de13`.
- Thời gian bốn lượt Windows trước sửa là **1,149/0,995/0,928/1,022 giây**, sau sửa là **0,811/0,706/0,808/0,821 giây**. Đây là mẫu quan sát nhỏ trên máy có tác vụ khác chạy cùng lúc.
- Ruff check và format check đạt trên các tệp thay đổi.

**Cách đo và cấu hình**

Giữ 100 principal có session và role riêng, năm role và tỷ lệ S/M/L 60/35/5. Mỗi run dùng **5 giây warm-up, 40 giây steady**, action slots 32, queue 128 và cùng deadline nghiệp vụ trong profile. Open giữ **20 business action/giây**. Closed giữ think-time 5 giây. Seed được tạo mới cho từng UUID schema, với cùng plan và geometry calibration. Mỗi run lưu profile hash và dataset hash riêng.

Các run dùng PostgreSQL thật và worker thật, qua TLS xác minh CA. Hai API được đặt sau TCP passthrough chia kết nối. Fixture tái sử dụng Edge đã có, không tiêm fault. API có pool 4/replica, worker pool 2. PostgreSQL giữ 24 kết nối tối đa và 512 MiB. Trần CPU DB được ghi riêng, memory và deadline không đổi. Container ứng dụng/máy phát có trần tổng 4 core/2 GiB.

| Run | API / CPU DB | Positive đúng hạn trong steady | Rejection / timeout cả run | ACK job / succeeded | Export đầy đủ, đúng hash |
|---|---|---:|---:|---:|---:|
| Open cũ, native-open-02 | 1 / 1 | 729/782 · 93,22% | 49 / 9 | 42 / 40 | 37 |
| Sau lazy import, native-open-03-lazy-parquet | 1 / 1 | 765/782 · 97,83% | 18 / 0 | 42 / 42 | 68 |
| Hai API, native-open-04-two-api | 2 / 1 | 511/782 · 65,35% | 21 / 255 | 40 / 33 | 63 |
| native-open-06-two-api-pg2 | 2 / 2 | **782/782 · 100%** | **0 / 0** | **42 / 42** | **86** |
| native-closed-02-two-api-pg2 | 2 / 2 | **745/745 · 100%** | **0 / 0** | **34 / 34** | **91** |

Lượt hai API với DB 1 core cho thấy tăng replica đơn lẻ chưa giải quyết được giới hạn của cấu hình. Trên hai API với DB 2 core, cả open và closed hết các rejection/timeout được quan sát. Các cấu hình thất bại và kết quả ban đầu được giữ nguyên. Run open 03 có source hash riêng trước khi formatter loại dòng trống cuối module. Hai run đạt dùng cùng runtime source hash nêu đầu báo cáo.

| Chỉ số ở cấu hình đạt | Open | Closed |
|---|---:|---:|
| Tổng offered, gồm warm-up | 900 | 859 |
| Offered trong steady, gồm negative control | 800 | 757 |
| Positive trong steady | 782 | 745 |
| Positive đúng hạn trong steady | 782 | 745 |
| Completed cả warm-up và steady | 880 | 846 |
| Expected denial cả run | 20 | 13 |
| Terminal action / pending action | 900 / 0 | 859 / 0 |
| HTTP request thực tế | 1.313 | 1.258 |
| Byte HTTP request body | 11.746 | 10.086 |
| Byte HTTP response body | 6.300.083 | 7.037.701 |
| Kết nối qua ingress | 63 | 58 |
| Kết nối tới API 0 / API 1 | 32 / 31 | 29 / 29 |
| Kết nối ingress đồng thời đỉnh | 33 | 33 |
| Số job còn thiếu terminal | 0 | 0 |
| Effect mutation chưa rõ sau reconciliation | 0 | 0 |

Cả hai run khớp planned/offered/terminal/pending. Tất cả job đã ACK được quan sát succeeded và tất cả export đã nhận được tải đủ/hash đúng. API và worker được dừng trước bước đối chiếu metadata sau run. Bộ đếm cgroup PostgreSQL không tăng `nr_throttled` trong khoảng trước/sau bài thử của hai run DB 2 core. Mốc này bao gồm cả setup và seed, chưa chứng minh headroom toàn máy ở 50k.

`native-open-05-two-api-pg2` dừng trước phát tải vì công cụ đọc cố định đường dẫn cgroup v2 trong môi trường đang có cgroup v1. Công cụ đã hỗ trợ cả hai đường dẫn và ghi unavailable nếu không đọc được. Run 06 mới là lượt đã thực thi đầy đủ.

**Phạm vi nghiệm thu**

Các con số trên xác nhận mức calibration đã chạy và việc xử lý điểm nghẽn export trong cấu hình được công bố. Native fixture dùng phiên synthetic đã xác thực qua API và source/debug payload riêng từng người dùng. Geometry của nó vẫn thấp hơn yêu cầu 100/1.000/10.000 claim và catalog 2 triệu result. Những bài lifecycle có Keycloak thật được giữ trong báo cáo trước.

Từng operation × size còn thiếu 10.000 mẫu để chốt p99. Chưa có ba run normal closed và ba open ở 50k theo 10 phút warm-up/30 phút steady, chưa có soak chính 2 giờ, stress/spike đầy đủ và DB40/F26 dưới tải đích. Các mục K17.01–K17.06 vì vậy vẫn mở ở phạm vi nghiệm thu tải chính.

Đầu việc còn lại và ma trận 66 ca nằm trong `KET-QUA-K17.md` và `database-fault-matrix.csv`. Hồ sơ nghiệm thu cho Bảo còn chờ đủ kết quả tải chính.
