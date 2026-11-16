# Danh mục công việc K01–K18 và J01–J08

Đối chiếu ngày 15/11/2026 trên nhánh `product-api`, HEAD `fa46dd8`. Danh mục lấy từ các tệp theo dõi gốc trong `Aletheia-Kien-API.zip`. Các bản trong workspace được đối chiếu trùng byte với tệp gốc tương ứng. Bản này dùng để theo dõi tiến độ của Kiên.

**Kết quả:** Kiên đạt **79/91 mục cá nhân (86,8%)**, còn **12/91**. Có **14/18 nhóm K** đã đạt toàn bộ phần cá nhân. Bộ K có **92 mục** khi tính thêm **K18.06** thuộc hai bên. Bộ J có **32 mục trong 8 nhóm**, chưa tìm thấy biên nhận đủ điều kiện để chốt mục nào trong lần rà này.

Số 79 gồm 78 mục của lượt audit ngày 15/11 và K18.03 chốt bổ sung ngày 16/11/2026. Lần cập nhật này chỉ cộng riêng K18.03; các nghiệm thu khác sau mốc audit được theo dõi trong biên bản riêng. K14, K16, K17 và K18 có bằng chứng đạt từng phần nhưng vẫn còn các bước theo tiêu chí của từng mục. CI 11/11 thuộc SHA `fa46dd8`. Các thử nghiệm cục bộ sau đó có biên nhận riêng.

## Thống kê theo nhóm K

| Nhóm | Phạm vi | Kiên đạt / tổng | Còn mở bên Kiên | Còn mở liên bên |
| --- | --- | --- | --- | --- |
| K-01 | Nền mã, baseline và ranh giới local–hosted | 4/4 | 0 | 0 |
| K-02 | Hosted API tối giản và hợp đồng phiên bản | 5/5 | 0 | 0 |
| K-03 | Đăng nhập, session và vòng đời tài khoản | 6/6 | 0 | 0 |
| K-04 | Workspace, phân quyền và cách ly dữ liệu | 5/5 | 0 | 0 |
| K-05 | PostgreSQL, object store và integrity có giới hạn | 6/6 | 0 | 0 |
| K-06 | Transactions, revision và idempotency | 5/5 | 0 | 0 |
| K-07 | Upload, preview và nguồn nhập an toàn | 5/5 | 0 | 0 |
| K-08 | Pipeline snapshot–comparison–result bất biến | 5/5 | 0 | 0 |
| K-09 | Durable jobs, retry, cancel và fencing | 6/6 | 0 | 0 |
| K-10 | Read API, pagination, search, graph và cache | 5/5 | 0 | 0 |
| K-11 | Phân tích thật, evidence và provider boundary | 5/5 | 0 | 0 |
| K-12 | Export, download và PDF tiếng Việt | 4/4 | 0 | 0 |
| K-13 | Delete, retention và không tái sinh dữ liệu | 5/5 | 0 | 0 |
| K-14 | Concurrency, tài nguyên và backpressure | 3/5 | 2 | 0 |
| K-15 | Job updates, polling/SSE và reconnect | 4/4 | 0 | 0 |
| K-16 | Quan sát, bảo mật và vận hành hằng ngày | 3/5 | 2 | 0 |
| K-17 | Capacity, database và failure dưới tải | 0/6 | 6 | 0 |
| K-18 | Deploy, migration, backup/restore và bàn giao | 3/5 | 2 | 1 |

## Toàn bộ 92 mục K

Cột tiêu chí lấy từ đề bài chi tiết. Mục đã đạt theo phạm vi cá nhân có thể còn nghiệm thu triển khai hoặc nghiệm thu chung của J.

### K-01 — Nền mã, baseline và ranh giới local–hosted

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-01.01 | Đối chiếu ba nhánh và giữ WIP | Không mất file hoặc commit; liệt kê rõ phần mới, phần đã merge và phần còn riêng. | Đạt phần Kiên |  |
| K-01.02 | Dựng baseline thực tế | Luồng UI → HTTP → service → store → download chạy được; nếu lỗi, có cách tái hiện rõ ràng. | Đạt phần Kiên |  |
| K-01.03 | Lập bản đồ operation và module | Mỗi hành trình lõi có đầu nhận ở backend và kiểm thử dự kiến; không để phần việc thiếu người phụ trách. | Đạt phần Kiên |  |
| K-01.04 | Giữ ranh giới nghiên cứu | Fixture, dữ liệu tải và báo cáo chỉ dùng nguồn tổng hợp hoặc nguồn công khai được phép; không ghi đè overlay. | Đạt phần Kiên |  |

### K-02 — Hosted API tối giản và hợp đồng phiên bản

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-02.01 | Thiết kế hosted adapter | Không đưa local bridge ra public; tác vụ CPU gây blocking không chạy trên event loop. | Đạt phần Kiên |  |
| K-02.02 | Schema và phiên bản ở hai biên | JSON không hợp lệ, quá cỡ hoặc có key trùng bị từ chối an toàn; Quân parse được mọi payload hợp lệ. | Đạt phần Kiên |  |
| K-02.03 | Bổ sung operation cho auth và onboarding | Người dùng được cấp tài khoản vào đúng workspace; luồng first-owner và nhận invite chạy được. | Đạt phần Kiên |  |
| K-02.04 | Edge, TLS và routing | Forwarded header giả không thay đổi identity hoặc ranh giới tin cậy của client IP; body gửi chậm không khóa toàn bộ API. | Đạt phần Kiên |  |
| K-02.05 | Kiểm thử consumer của contract | Quân dùng fixture từ serializer thực tế; schema drift làm kiểm tra thất bại trước khi ghép release. | Đạt phần Kiên |  |

### K-03 — Đăng nhập, session và vòng đời tài khoản

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-03.01 | OIDC và callback | Callback hoặc token sai không tạo session; return-to chỉ nhận các path được phép. | Đạt phần Kiên |  |
| K-03.02 | Khởi tạo và khôi phục tài khoản | Người chưa được provision hoặc đã bị disabled biết trạng thái; khôi phục không bỏ qua MFA hay chính sách identity. | Đạt phần Kiên |  |
| K-03.03 | Vòng đời session | Session đã logout/revoke không dùng được; cookie flag và TLS đúng, secret không vào storage phía client. | Đạt phần Kiên |  |
| K-03.04 | CSRF và nguồn request | Request không được phép không tạo effect; SameSite không thay thế toàn bộ kiểm tra CSRF. | Đạt phần Kiên |  |
| K-03.05 | Đổi tài khoản và dùng nhiều tab | Membership/session hết hiệu lực được phản ánh trong ≤5 giây theo chính sách dự án; không lẫn dữ liệu giữa hai tài khoản. | Đạt phần Kiên |  |
| K-03.06 | Abuse và auth outage | Không redirect/retry vô hạn; không phát sinh phí hoặc traffic vào IdP bên ngoài khi chưa được phép. | Đạt phần Kiên |  |

### K-04 — Workspace, phân quyền và cách ly dữ liệu

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-04.01 | Khởi tạo workspace và first-owner | Leave/remove đồng thời không tạo workspace vô chủ hoặc cấp sai quyền. | Đạt phần Kiên |  |
| K-04.02 | Chính sách quyền theo action | Mọi operation có kiểm thử cho phép/từ chối theo object scope; operation lõi được chặn ở server, không chỉ ẩn ở UI. | Đạt phần Kiên |  |
| K-04.03 | Các đường đọc phụ | Không lộ tên, count, source hoặc result ngoài scope được phép; response an toàn và thống nhất. | Đạt phần Kiên |  |
| K-04.04 | Revoke xuyên qua tác vụ async | Không giữ grant cũ vô hạn; không hứa thu hồi các byte đã tải hợp lệ trước revoke. | Đạt phần Kiên |  |
| K-04.05 | Tenant context trong pool | Hai tenant dùng lại cùng DB connection không rò context hoặc bỏ qua policy. | Đạt phần Kiên |  |

### K-05 — PostgreSQL, object store và integrity có giới hạn

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-05.01 | Schema và canonical identity | Scope, parent và hash đúng; hai facade không sinh hai nguồn dữ liệu chuẩn lệch nhau. | Đạt phần Kiên |  |
| K-05.02 | Bản đồ query và index | Mỗi lần click không scan hoặc load toàn catalog; có bằng chứng về cardinality và query plan. | Đạt phần Kiên |  |
| K-05.03 | Publish object và reconcile | Record hiển thị không trỏ tới object bị thiếu; orphan không tăng vô hạn, GC không xóa object còn reference. | Đạt phần Kiên |  |
| K-05.04 | Kiểm integrity theo scope và scrub | Không phục vụ object bị sửa hoặc bị thiếu; scrub phát hiện corruption ngoài hot path. | Đạt phần Kiên |  |
| K-05.05 | Migration dữ liệu cũ | Không mất snapshot/result/history; migration lỗi có rollback/recovery, giữ nguyên nguồn gốc dữ liệu. | Đạt phần Kiên |  |
| K-05.06 | Maintenance và tăng trưởng có giới hạn | Tăng trưởng catalog và mức lock/I/O có giới hạn; backup/GC không làm hỏng consumer đang hoạt động. | Đạt phần Kiên |  |

### K-06 — Transactions, revision và idempotency

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-06.01 | Idempotency ledger | Cùng key/body chỉ tạo một effect; khác body trả conflict, không tạo operation mới. | Đạt phần Kiên |  |
| K-06.02 | Mutation dùng CAS/revision | Hai writer không gây lost update; người dùng nhận đúng revision và outcome. | Đạt phần Kiên |  |
| K-06.03 | Danh mục race | Mỗi race có invariant và outcome thực tế tới consumer; việc DB không crash chưa đủ để nghiệm thu. | Đạt phần Kiên |  |
| K-06.04 | Xử lý deadlock và serialization | Retry giữ idempotency và deadline; không queue/retry vô hạn hoặc tạo effect kép. | Đạt phần Kiên |  |
| K-06.05 | Audit action và commit | Sau khi mất mạng ở thời điểm ACK, vẫn tra được operation/effect với đúng result và quyền. | Đạt phần Kiên |  |

### K-07 — Upload, preview và nguồn nhập an toàn

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-07.01 | Upload staging và resume | Không nhân đôi byte hoặc effect; dữ liệu staging hết hạn được dọn, báo đúng trạng thái. | Đạt phần Kiên |  |
| K-07.02 | Ranh giới tin cậy của file và archive | Chặn archive độc hại, path nguy hiểm và archive bomb; không ghi ngoài namespace hoặc lộ secret/exception không an toàn. | Đạt phần Kiên |  |
| K-07.03 | Preview contract và lease | Source thay đổi hoặc lease hết hạn không tạo snapshot dở dang hay âm thầm dùng mapping cũ. | Đạt phần Kiên |  |
| K-07.04 | Mapping nhiều run | Xử lý đúng hơn 3 run và Unicode; không đoán target/unit hoặc bỏ run để vượt validation. | Đạt phần Kiên |  |
| K-07.05 | Connector tùy chọn có kiểm soát | Luồng hosted import lõi vẫn dùng được; connector được bật phải revoke đúng, connector chưa có không báo ready. | Đạt phần Kiên |  |

### K-08 — Pipeline snapshot–comparison–result bất biến

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-08.01 | Confirm nguyên tử và initial snapshot | Không để project/snapshot dở dang; initial result gắn đúng source generation. | Đạt phần Kiên |  |
| K-08.02 | No-op và adverse refresh | Timestamp đơn thuần không tạo snapshot; refresh lỗi giữ snapshot cũ. | Đạt phần Kiên |  |
| K-08.03 | Result và history bất biến | Thay current pointer không sửa history hoặc citation của result cũ. | Đạt phần Kiên |  |
| K-08.04 | Trường hợp biên về số và schema | Không đổi số hoặc suy ra cause từ delta; thiếu dữ liệu giữ đúng unknown/not-assessed. | Đạt phần Kiên |  |
| K-08.05 | Publish đồng thời | Result cũ không trở thành current; giao task trùng không tạo thêm immutable record trùng. | Đạt phần Kiên |  |

### K-09 — Durable jobs, retry, cancel và fencing

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-09.01 | Submit job và receipt | Retry/key tra đúng job; job đã ACK không mất khi API process chết. | Đạt phần Kiên |  |
| K-09.02 | Worker lease và fencing | Worker cũ không publish được; tra được attempt history, không chạy đồng thời mọi job không giới hạn. | Đạt phần Kiên |  |
| K-09.03 | State machine cho deadline và retry | Mọi job đã nhận ở trạng thái terminal hoặc còn pending trong hạn; lỗi vĩnh viễn không retry vô hạn. | Đạt phần Kiên |  |
| K-09.04 | Ngữ nghĩa cancel | Success đã commit được hiển thị đúng; khi báo cancelled, không worker nào còn quyền commit. | Đạt phần Kiên |  |
| K-09.05 | Fairness và quota của queue | Một tenant không chiếm hết slot; công việc bình thường trong envelope vẫn được phục vụ đúng deadline. | Đạt phần Kiên |  |
| K-09.06 | Mở lại và recovery | Job đã ACK không biến mất; attempt dở không báo complete, consumer tiếp tục được. | Đạt phần Kiên |  |

### K-10 — Read API, pagination, search, graph và cache

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-10.01 | Read projection có giới hạn | Payload có giới hạn trên catalog 2 triệu result; người dùng vẫn truy cập đủ dữ liệu qua các page. | Đạt phần Kiên |  |
| K-10.02 | Gắn cursor với query | Không skip/duplicate do contract mơ hồ hoặc rò dữ liệu khác scope; cursor không hợp lệ có đường khôi phục. | Đạt phần Kiên |  |
| K-10.03 | Search và graph subquery | Không cắt dữ liệu âm thầm; graph relation/source đúng, total là số chính xác hoặc được ghi rõ là ước tính. | Đạt phần Kiên |  |
| K-10.04 | Chính sách cache | Revoke/delete/update không trả payload cũ trái quyền; hot/cold đều giữ đúng dữ liệu và quyền. | Đạt phần Kiên |  |
| K-10.05 | Rebuild index và read model | Không phục vụ index dựng dở hoặc làm mất history; fallback có giới hạn và đúng quyền. | Đạt phần Kiên |  |

### K-11 — Phân tích thật, evidence và provider boundary

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-11.01 | Analysis mode thực tế | UI không gọi deterministic mock là model diagnosis hoặc tự nhận phần nghiên cứu đã merge. | Đạt phần Kiên |  |
| K-11.02 | Domain cho metric observation | Mỗi claim có đúng source, result tái lập được và giữ nguyên bất định về quan hệ nhân quả. | Đạt phần Kiên |  |
| K-11.03 | Membership của evidence và turn | API không tạo citation sai turn/scope; frontend có đủ metadata về quan hệ. | Đạt phần Kiên |  |
| K-11.04 | Adapter tài nguyên và quyền riêng tư của provider | Không tự động gửi private source; output sai hoặc rate limit trở thành trạng thái thật, không giả success. | Đạt phần Kiên |  |
| K-11.05 | Khả năng tái lập theo phiên bản | Mở lại cùng result nhận đúng output đã lưu; không ngầm chạy lại model hoặc đổi finding. | Đạt phần Kiên |  |

### K-12 — Export, download và PDF tiếng Việt

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-12.01 | Export job và parity | Byte của file và nội dung đọc được khớp màn hình/API; không thay input đã pin bằng snapshot mới nhất. | Đạt phần Kiên |  |
| K-12.02 | An toàn Unicode và render | PDF đọc và copy được; renderer không SSRF, thực thi nội dung project hoặc vượt giới hạn tài nguyên. | Đạt phần Kiên |  |
| K-12.03 | Quyền download và range | Không vượt quyền hoặc lộ secret trong signed URL; timeout/cancel có receipt đúng. | Đạt phần Kiên |  |
| K-12.04 | Phân phối file lớn | Download chậm không chặn metadata read; completion được xác định theo byte thực tế và quyền. | Đạt phần Kiên |  |

### K-13 — Delete, retention và không tái sinh dữ liệu

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-13.01 | Chính sách delete và receipt | Phân biệt accepted với purged; người dùng biết dữ liệu nào còn được giữ và tới thời hạn nào. | Đạt phần Kiên |  |
| K-13.02 | Fencing cho delete race | Writer cũ không làm project hoặc result đã xóa xuất hiện lại. | Đạt phần Kiên |  |
| K-13.03 | GC an toàn với reference | Không xóa nhầm source hoặc dữ liệu dùng chung; dọn orphan đúng grace period. | Đạt phần Kiên |  |
| K-13.04 | Lịch retention | Không dùng global lock để quét kho mỗi request hoặc xóa nguồn chưa hết retention. | Đạt phần Kiên |  |
| K-13.05 | Parity giữa restore và tombstone | Không đọc lại dữ liệu đã xóa trái policy; audit trail đủ tra cứu và không chứa raw secret. | Đạt phần Kiên |  |

### K-14 — Concurrency, tài nguyên và backpressure

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-14.01 | Ngân sách process và pool | Không dùng 50k DB connection hoặc thread/task không giới hạn; còn tài nguyên dự phòng cho admin/health. | Còn mở bên Kiên | Đo trên topology triển khai đã khai báo: toàn bộ replica/process/pool, CPU theo operation, RAM/FD/NIC/ingress và dự phòng health/admin. Giữ nguyên kết quả local 2 API + 2 worker đã đạt. |
| K-14.02 | Ranh giới tác vụ blocking | Tác vụ nặng không làm tín hiệu liveness của event loop sai lệch hoặc chiếm hết API worker. | Đạt phần Kiên |  |
| K-14.03 | Admission và backpressure | Trả 429/503/Retry-After đúng khi ngoài envelope; không tính việc từ chối tải 50k bình thường thành kết quả latency tốt. | Đạt phần Kiên |  |
| K-14.04 | Cancel và dọn tài nguyên | Trả connection/FD/temp/lease; không xóa job đã nhận chỉ vì browser mất mạng. | Đạt phần Kiên |  |
| K-14.05 | Fairness và burst | Tenant nền được phục vụ theo policy; không priority inversion hoặc khuếch đại retry. | Còn mở bên Kiên | Đo fairness/burst với workload S/M/L của môi trường triển khai; chứng minh tenant nền, retry, queue và deadline trong envelope. Không dùng debug local nhỏ thay capacity thực. |

### K-15 — Job updates, polling/SSE và reconnect

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-15.01 | Chọn cơ chế cập nhật | Không gắn một DB connection với mỗi stream hoặc biến mọi người dùng thành poller chu kỳ 2 giây. | Đạt phần Kiên |  |
| K-15.02 | Event identity và resume | Không mất event hoặc tạo effect trùng; gap yêu cầu resync, không giả complete. | Đạt phần Kiên |  |
| K-15.03 | Quyền và scope của stream | Revoke theo chính sách ≤5 giây; buffer/error không phát dữ liệu riêng tư sau khi mất quyền. | Đạt phần Kiên | Đạt 6 ca thu hồi quyền qua hai replica. UI, BFCache và staging chung theo nghiệm thu phối hợp. |
| K-15.04 | Ngân sách reconnect | Không rò connection hoặc tạo bão reconnect đồng bộ; trạng thái consumer nhất quán với job ledger. | Đạt phần Kiên | 100/100 job khớp ledger sau reconnect. Kết quả thuộc fixture backend hai replica đã thử. |

### K-16 — Quan sát, bảo mật và vận hành hằng ngày

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-16.01 | Observability theo hành trình | Điều tra được một lỗi/retry mà không cần raw data bí mật. | Đạt phần Kiên |  |
| K-16.02 | Error và logging an toàn | Không có raw exception/token/private source trong response, client bundle, log hoặc báo cáo. | Đạt phần Kiên |  |
| K-16.03 | Threat model và biện pháp kiểm soát | Có bằng chứng từ chối đúng dự kiến; không còn lỗ hổng nghiêm trọng đã biết trên đường triển khai. | Đạt trên staging LAN chung | Bổ sung 16/11: TLS/IdP, header, secret/collector đã kiểm; xem k16-remaining/lan-20261116. |
| K-16.04 | Hardening dependency và image | Không lệch credential/image; có inventory image/config, không coi kết quả scanner đơn lẻ là pentest đầy đủ. | Đạt phần Kiên |  |
| K-16.05 | Alert và runbook | Alert dẫn tới hành động rõ ràng, không bùng cardinality; người còn lại dùng được runbook. | Đạt trên staging LAN chung | Bổ sung 16/11: Quân nhận cảnh báo và khôi phục, 6/6 receipt, 8/8 job đúng hash; xem k16-remaining/lan-20261116. |

### K-17 — Capacity, database và failure dưới tải

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-17.01 | Adapter cho seed và import | Không coi việc sinh JSONL là benchmark DB; có manifest cho dữ liệu đa dạng và phân bố hot/cold. | Còn mở bên Kiên | Model lớn đã tích hợp và kiểm S/M/L. Mẫu đã nhập 8 snapshot và 8 result, khớp hash. Còn nhập và xác minh catalog đích 50k principal, 200.000 snapshot và 2 triệu result. |
| K-17.02 | Harness từ hành vi tới tải | Dựng lại được các count engaged/starting/completing/waiting/idle của 50k người dùng, cùng count scheduled/drop/reject. | Còn mở bên Kiên | Đã chạy các fixture riêng cho upload, confirm, refresh, quản lý workspace, logout và cancel. Còn chạy toàn bộ luồng trong chiến dịch chính và đối chiếu số đếm. |
| K-17.03 | Đánh giá generator và tài nguyên | Generator không trở thành nút thắt rồi bị quy lỗi cho server; target/staging đã được phép sử dụng. | Còn mở bên Kiên | Đã hiệu chuẩn trên host chung. Còn máy phát riêng và số đo NIC, clock, CPU, RAM, FD, headroom ở tải đích. |
| K-17.04 | Normal, stress, spike và soak | SLO và job completion theo hướng dẫn; failed/partial/timeout không bị bỏ khỏi mẫu số. | Còn mở bên Kiên | Calibration 100 principal đã có. Còn ramp 5k/10k/25k/50k, 3 closed + 3 open, stress/spike và soak chính 2 giờ. |
| K-17.05 | Liên kết DB, fault và tải | Không mất việc đã ACK, không stale commit hoặc phục vụ dữ liệu hỏng; báo riêng tier process và storage. | Còn mở bên Kiên | Đã có 2/2 ca fault riêng. Còn đối chiếu DB40/F26 dưới tải đích và kiểm effect cùng telemetry. |
| K-17.06 | Báo cáo capacity và chi phí | Nếu chưa đạt 50k, giữ gate ở trạng thái mở; không ngoại suy từ 10k lên 50k hoặc coi test double là capacity của provider. | Còn mở bên Kiên | Có ước tính lưu trữ và kết quả mẫu. Còn đo tải thực đạt SLO rồi chốt phí, tài nguyên dự phòng và hệ thống phụ thuộc. |

### K-18 — Deploy, migration, backup/restore và bàn giao

| Mã | Việc cần làm | Tiêu chí của đề bài | Trạng thái | Bước còn lại hoặc bằng chứng mới |
| --- | --- | --- | --- | --- |
| K-18.01 | Release có thể tái dựng | Người còn lại dựng lại được mà không cần path hoặc secret cục bộ từ máy Kiên. | Còn mở bên Kiên | Đóng gói/build image và release theo SHA cuối, lưu digest/lock/SBOM/config identity, kiểm API/worker/volume/secret/TLS và canary IdP trên môi trường triển khai. Không liệt kê xác nhận rebuild của Quân. |
| K-18.02 | Khả năng tương thích khi migration | Không lock table kéo dài hoặc mất dữ liệu; rollback code không được coi là đảo ngược migration phá hủy dữ liệu. | Đạt phần Kiên |  |
| K-18.03 | Readiness, drain và rollout | Không nhận việc trước ready hoặc bỏ job đã nhận; worker cũ bị fencing. | Đạt phần Kiên — 16/11/2026 | Upgrade 5ac1c54 → fa46dd8 và rollback trên staging 2 API/2 worker, canary tối đa 3 API. 12/12 job thành công, 2 tệp tải khớp SHA, 4 canary, stale lease bị chặn và job được phục hồi. [Biên bản](k18-rollout-20261116/KET-QUA-K18.03.md). |
| K-18.04 | Backup, PITR và object | Mục tiêu storage DR là RPO ≤5 phút/RTO ≤30 phút, có bằng chứng; thông báo backup thành công chưa đủ. | Còn mở bên Kiên | Thử host-loss và khôi phục DB/WAL/object/authority-checkpoint từ bản mã hóa ngoài host, trên dataset đích; đo RPO ≤5 phút, RTO ≤30 phút. PITR local đã pass giữ đúng phạm vi. |
| K-18.05 | Process recovery | Đúng tier: RPO 0 cho effect đã ACK và RTO ≤60 giây; không tuyên bố chịu power loss hoặc hostile host khi chưa kiểm. | Đạt phần Kiên |  |
| K-18.06 | Bàn giao chéo giữa hai người | Release dùng cùng SHA/schema; hoàn thành 90% đầu việc không bù cho lỗi dữ liệu/quyền hoặc gate 50k chưa đạt. | Còn mở liên bên | Kiên và Quân dựng lại phần của nhau trên cùng bản phát hành, đối chiếu SHA/schema và ký các cổng áp dụng. Chưa có biên nhận chung. |

## Toàn bộ 32 mục J

Các mục J là việc phối hợp. Yêu cầu có bằng chứng hai bên và hành trình browser → API → persistence/worker → browser/download thật. Bảng chính thức hiện chưa có joint run ID cho các nhóm này. Cột ghi chú tóm tắt phần kỹ thuật đã có và việc còn lại của nhóm.

### J-01 — Chốt nền, mô hình sản phẩm và hợp đồng tích hợp

Phụ trách: Kiên: API/schema/capabilities/data boundaries; Quân: personas/screens/client contract.

Đã có ghép UI-b/backend và CI của SHA đã push. Còn xác nhận chung cùng RC, contract, hành trình đầy đủ và tài nguyên tải.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-01.01 | Chốt base và quyền sở hữu file | Cùng base SHA và người phụ trách từng file; Kiên giữ server/domain, Quân giữ client/design. | Không mất việc; hai phía dùng cùng contract version. | Chưa chốt chung |
| J-01.02 | Chốt contract và fixtures | Auth, job, error, scope, paging và schema dùng cùng fixture hợp lệ. | Lỗi và lựa chọn có điều kiện rõ ràng; không để hai DTO tự lệch. | Chưa chốt chung |
| J-01.03 | Vertical slice sớm | Login → protected data và một luồng source → result thật. | Cả hai chạy được lát chức năng này; blocker có owner trước khi mở rộng. | Chưa chốt chung |
| J-01.04 | Chốt targets và tài nguyên | Workload 50k, SLO, tài nguyên/chi phí và nghiệm thu chung. | Không nhận capacity giả hoặc tự cấp quyền dùng provider/cloud có phí. | Chưa chốt chung |

### J-02 — Danh tính, quyền và đổi ngữ cảnh xuyên mọi màn

Phụ trách: Kiên: session/RBAC/revoke; Quân: auth/account/workspace UX và client epoch.

Backend K03/K04 và các ca HTTP đã có bằng chứng. Còn đối chiếu cùng browser, IdP và đổi ngữ cảnh trên RC chung.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-02.01 | Hành trình theo role và kiểm từ chối | Hai tài khoản và workspace với đủ các role. | Server và browser đúng quyền; không lóe nội dung riêng trước khi bị từ chối. | Chưa chốt chung |
| J-02.02 | Vòng đời onboarding, invite và owner | Người dùng mới, invite expired/revoked, first owner và last owner. | Người dùng bình thường vào đúng workspace; không chỉ có đường seed bởi admin. | Chưa chốt chung |
| J-02.03 | Switch/revoke khi async đang chạy | Request, draft, cache, event, job và download khi đổi account/role. | Revoke trong ≤5 giây theo policy; ngữ cảnh cũ không xuất hiện ở account mới. | Chưa chốt chung |
| J-02.04 | Auth outage và recovery | IdP/app restart, refresh storm và return-to sau reauth. | Không login loop, credential leak hoặc gửi mutation nhiều lần. | Chưa chốt chung |

### J-03 — Nguồn tới kết quả có thể đọc và kiểm chứng

Phụ trách: Kiên: upload/pipeline/analysis/read APIs; Quân: wizard/investigation/source reader.

Đã có luồng S/M/L qua API/worker/PG và Chrome với API kiểm soát. Còn kiểm hành trình chung từ trình duyệt thật, cùng dữ liệu/quyền và các ca UI.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-03.01 | Import, mapping và persist | Nguồn S/M/L → preview → confirm → snapshot. | Payload UI, hash và DB record đúng; không có snapshot dở. | Chưa chốt chung |
| J-03.02 | Analysis, claim và nguồn | Analysis job → result → claim → mọi citation/counterevidence. | Không bịa cause hoặc gắn sai evidence; mode minh bạch. | Chưa chốt chung |
| J-03.03 | Refresh, history và no-op | Result cũ/mới được ghim qua no-op, refresh bất lợi và missing data. | Kết quả cũ không đổi; generation mới đúng nguồn. | Chưa chốt chung |
| J-03.04 | Scale, lỗi và keyboard | Page/graph lớn, validation/unknown/denied và mobile keyboard. | Đủ nội dung và cách khôi phục; không truncate hoặc rò hidden scope. | Chưa chốt chung |

### J-04 — Tác vụ nền và races khi người dùng thao tác thật

Phụ trách: Kiên: durable ACK/lease/fence/CAS; Quân: jobs/progress/retry/cancel/context fencing.

K06/K09/K13 có bằng chứng backend cho idempotency, lease, cancel và xóa. Còn đối chiếu hiệu ứng cùng UI trên một lượt chung.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-04.01 | Lost ACK và retry | Submit đã commit nhưng client không nhận response. | Một business effect; không mất accepted job. | Chưa chốt chung |
| J-04.02 | Worker, lease và cancel | Kill worker, chuyển lease, thử worker cũ publish và race cancel/commit. | Cancelled khác cancel_requested; success đã commit được giữ đúng. | Chưa chốt chung |
| J-04.03 | Race khi điều hướng hoặc đổi ngữ cảnh | Ask/import/refresh/export/delete đang chạy trong lúc switch. | Không thay URL, result, toast hoặc draft của ngữ cảnh mới. | Chưa chốt chung |
| J-04.04 | Delete, revoke và publication | Tombstone hoặc membership thay đổi trước publish/download. | Không phục sinh dữ liệu hoặc bypass quyền; terminal receipt phản ánh đúng thực tế. | Chưa chốt chung |

### J-05 — Review, nguồn, export và vòng đời dữ liệu

Phụ trách: Kiên: permission/revisions/export/delete/storage; Quân: evidence/review/history/download UX.

Đã kiểm API evidence, export ba định dạng và vòng đời/xóa trên mẫu. Còn chạy chung hai reviewer, UI tải/xóa và đối chiếu khôi phục.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-05.01 | Ngữ nghĩa citation | Claim A với B chưa cite, counterevidence và old turn. | Reader, API và export giữ đúng quan hệ. | Chưa chốt chung |
| J-05.02 | Review cộng tác | Hai reviewer thực hiện save/revoke, gặp conflict và mở lại. | Không lost update hoặc báo note local đã được lưu trên server. | Chưa chốt chung |
| J-05.03 | Export và parity | Browser mở JSON/Markdown/PDF tiếng Việt cho cùng result. | Đọc/copy được; không sai version hoặc dữ liệu. | Chưa chốt chung |
| J-05.04 | Delete, retention và shared references | Delete × export × restore với source/shared objects. | Không xóa ngoài phạm vi hoặc phục sinh dữ liệu trái policy; người dùng hiểu outcome. | Chưa chốt chung |

### J-06 — Hành vi người dùng, SLO và thiết kế phép tải

Phụ trách: Quân chủ trì behavior/UX; Kiên chủ trì waterfall/resources/generator; cả hai khóa workload.

Có workload tham chiếu và đo calibration. Còn chốt nguồn hành vi, máy phát riêng, catalog đích, lịch và ngưỡng đã thống nhất.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-06.01 | Bằng chứng hành vi | Nhiệm vụ, think time, wait, revisit, cancel và reconnect theo role. | Profile có nguồn đối chiếu; không suy rằng sáu người đại diện cho 50k. | Chưa chốt chung |
| J-06.02 | Waterfall và phép tính tài nguyên | Action → HTTP → DB/job/byte/polling. | Không tính đôi polling hoặc coi một người dùng bằng một DB connection. | Chưa chốt chung |
| J-06.03 | Đánh giá dữ liệu và generator | 50k actor, 2 triệu result, payload S/M/L và generator headroom. | Payload và quyền thật, không dùng chung admin; generator không là bottleneck. | Chưa chốt chung |
| J-06.04 | Khóa workload và lịch chạy | Closed cohort 50k/open-arrival, ba lần chạy, fault, soak và SLO. | Không đổi mix sau outcome; có quyền và budget trước bài tải lớn. | Chưa chốt chung |

### J-07 — Thử tải, lỗi dưới tải và trải nghiệm đồng thời

Phụ trách: Kiên: protocol load/API/DB/worker/infra; Quân: browser cohort/UX/content/reconnect.

Có calibration 100 principal và ca fault riêng. Còn chiến dịch 50k, stress/spike/reconnect, soak và fault dưới tải đích.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-07.01 | Normal closed/open | Cohort đang tham gia có principal không trùng và lượt đến theo lịch, gắn với từng hành trình. | Đạt SLO, ≥99,9% đúng hạn và counter khớp; không quan sát thấy mất dữ liệu. | Chưa chốt chung |
| J-07.02 | Stress, spike và reconnect | Breakpoint, rejection và recovery theo policy. | Không queue vô hạn, retry storm hoặc chỉ đo health trả HTTP 200. | Chưa chốt chung |
| J-07.03 | Soak, leak và cleanup | Soak chính 2 giờ ở 50k; mở rộng 8 giờ khi có rủi ro theo chu kỳ hoặc tài nguyên phù hợp. | Không bỏ job pending khỏi báo cáo hoặc để dung lượng đĩa tăng vô hạn; consumer tiếp tục được. | Chưa chốt chung |
| J-07.04 | Fault dưới tải | API/worker/DB/object bị lỗi hoặc quyền thay đổi sau ACK. | Không mất accepted work, stale commit hoặc tenant leak; RPO/RTO đúng với tier thực tế. | Chưa chốt chung |

### J-08 — Release rehearsal, restore và nghiệm thu cuối

Phụ trách: Kiên: deploy/migrate/backup/restore/runbook; Quân: asset/API compatibility/usability/cross-browser.

Đã có migration, PITR và phục hồi tiến trình ở phạm vi local. Còn dựng chéo, rollout và restore trên RC đích, rồi đối chiếu các cổng phát hành.

| Mã | Việc phối hợp | Kết quả cần bàn giao | Tiêu chí | Tình trạng |
| --- | --- | --- | --- | --- |
| J-08.01 | Kiểm chéo từ clean setup | Kiên chạy chuỗi UI, Quân chạy chuỗi API. | Không phụ thuộc máy hoặc token cá nhân của người viết. | Chưa chốt chung |
| J-08.02 | Deploy, migrate và rollback | Traffic còn chạy khi đổi API/assets/schema. | Không mất ACK/history hoặc trả dữ liệu hỏng. | Chưa chốt chung |
| J-08.03 | Restore và thao tác tiếp theo | DB, object, session/role, job và tombstone. | Consumer, quyền và hash đúng; RPO/RTO đúng tier. | Chưa chốt chung |
| J-08.04 | Quyết định release cuối | Case cá nhân/phối hợp, gate, bằng chứng, chi phí và giới hạn. | Không lấy mức hoàn thành 90% để bù lỗi nghiêm trọng về an toàn/tính đúng đắn của dữ liệu; 50k chưa đạt thì gate còn mở. | Chưa chốt chung |

## Các cổng và ca kiểm của đề bài

Các bảng dưới đây đi kèm K/J. Số hàng trong các bảng này được theo dõi riêng với 124 mục công việc.

### 27 cổng phát hành

Tracker gốc có 19 cổng đang làm, 8 cổng chưa có lượt chạy chung và 0 cổng được ghi đã chốt. Các cổng giao riêng Quân được đưa vào để đối chiếu, với người phụ trách ghi đúng theo đề bài.

| Cổng | Việc kiểm | J liên quan | Người phụ trách | Tiêu chí | Tracker gốc |
| --- | --- | --- | --- | --- | --- |
| G01 | Git/build | J-01,J-08 | Cả hai | Pull main/ghi SHA; reproducible production build và dependencies; không mất thay đổi riêng | Đang làm |
| G02 | Contract | J-01 | Kiên + Quân | Current/proposed capabilities phân biệt; OpenAPI/errors/events/paging và fixtures thống nhất; mọi P01–P38 có disposition | Đang làm |
| G03 | Identity/tenant | J-02 | Kiên | Mọi read/write/event/download/job kiểm object authorization; revoke giữa chừng có witness; cross-tenant negative tests đạt | Đang làm |
| G04 | Storage | J-03,J-08 | Kiên | Source→object→metadata→snapshot/result đúng hash/scope; migration parity, atomicity, corruption/failure đúng policy | Đang làm |
| G05 | Jobs | J-04 | Kiên | ACK bền, idempotency, retries/lease/fence/CAS/cancel/delete races; mọi ACK có terminal hoặc pending còn trong deadline | Đang làm |
| G06 | Import/mapping | J-03 | Cả hai | Local Unicode/Windows và hosted upload an toàn; preview expired/changed; 3+ run, target/metric/class/unit/threshold không đoán ngầm | Chưa ghi nhận lượt chạy chung |
| G07 | Analysis truth | J-03 | Cả hai | Mock/local evidence/provider phân biệt; response/evidence/caller nghĩa đúng; thiếu dữ liệu/abstain/technical không đánh đồng | Chưa ghi nhận lượt chạy chung |
| G08 | Citation | J-05 | Quân + Kiên | Từng claim mở đúng mọi citation/counterevidence của result; URL stale không đổi sang evidence khác; server cùng kiểm quyền | Chưa ghi nhận lượt chạy chung |
| G09 | Navigation/races | J-02,J-04 | Quân | Async import/follow-up/refresh/export không đè project mới; reload/back/deep link/cross-tab đúng snapshot/result | Chưa ghi nhận lượt chạy chung |
| G10 | Data at scale | J-03 | Cả hai | 10.000 claims/node truy cập đủ bằng paging/subgraph; bounded payload/render, không truncate hay silently cap | Đang làm |
| G11 | Review/annotations | J-05 | Cả hai | Local vs server-saved rõ, revision conflict, scopes; nguồn text không thực thi HTML/script | Chưa ghi nhận lượt chạy chung |
| G12 | Design | J-01,J-08 | Quân | Hai layout concept cùng nội dung, design review, một system mọi màn; trạng thái thực + long content + light/dark/mobile đều tốt | Chưa ghi nhận lượt chạy chung |
| G13 | Accessibility | J-02,J-03,J-08 | Quân | Áp dụng WCAG2.2 AA, keyboard/reader/zoom/focus/touch/contrast; checklist bằng chứng gồm manual, không chỉ static test | Chưa ghi nhận lượt chạy chung |
| G14 | UX behavior | J-06 | Quân | Nhiệm vụ/roles/think-time/mix có nguồn; usability problems đã xử lý/giới hạn; không bịa representative population | Chưa ghi nhận lượt chạy chung |
| G15 | 50.000 mixed/open | J-07 | Kiên + Quân | Đạt 50k unique-active định nghĩa, full profile/SLO/correctness/dataset; server + browser trải nghiệm; không chỉ idle hoặc health | Đang làm |
| G16 | DB under load | J-07 | Kiên | Query plans/count, hot rows, connection/pool waits, cold/warm catalog, contention, maintenance/migration theo traffic; không deadlock/loss | Đang làm |
| G17 | Heavy/fairness | J-07 | Kiên | Job storm/noisy tenant/reconnect có envelope/admission/recovery/completion đúng; normal work vẫn được phục vụ theo policy | Đang làm |
| G18 | Soak | J-07 | Cả hai | 2h primary50k, RAM/FD/DB queue/heap không tăng vô hạn; drain/cleanup khớp; hạn chế 24h còn thiếu được ghi;8h extension nếu cần kiểm chu kỳ dài và có tài nguyên,không claim tier chưa chạy | Đang làm |
| G19 | Export | J-05 | Cả hai | JSON/MD/PDF giữ cùng claim/evidence/precision/limitations; tiếng Việt thật, permission/revoke/expiry/large download đúng | Đang làm |
| G20 | Delete/retention | J-04,J-05 | Kiên + Quân | Cancel dialog không xóa; accepted-delete/purge/backups phân biệt; shared/source giữ đúng; stale worker không resurrect | Đang làm |
| G21 | Deploy | J-08 | Kiên | TLS/config/secrets, liveness/readiness/drain, constrained container, repeatable staging; rollout/canary/rollback thực | Đang làm |
| G22 | Recovery | J-08 | Kiên | Restore clean env từ DB/object/keys policy; Process crash RPO=0/RTO≤60s; storage DR RPO≤5min/RTO≤30min đo bằng witness, không chỉ backup command success | Đang làm |
| G23 | Security | J-02,J-08 | Kiên + Quân | Tenant/auth/CSRF/CSP/XSS/upload/path/SSRF/throttle/dependency và no secret logging; các threat/failure cases có kết quả | Đang làm |
| G24 | Observability | J-07,J-08 | Kiên | Trace action/request/job và version, redacted logs, low-cardinality histograms, alert ownership/burn/queue/DB; dashboards hỗ trợ điều tra | Đang làm |
| G25 | Cost/envelope | J-06,J-07 | Kiên | Resource/cost per completed correct action/job, model mode, throughput/drop/completion; 50.000/stress kết luận đúng phạm vi | Đang làm |
| G26 | Cross-owner replay | J-08 | Cả hai | Mỗi người dựng lại vertical slice của người kia; docs từ máy sạch và permission sai có expected outcome | Đang làm |
| G27 | Delivery | J-08 | Cả hai | Source/config/migration/fixtures/raw/report/runbook/known issues đủ, tách dữ liệu nội bộ và quyền deploy | Đang làm |

### 40 ca database

Tracker gốc ghi 12/40 ca đạt. Một số ca có bằng chứng bổ sung trên staging riêng hoặc theo luồng riêng. Ma trận chạy dưới tải chính cần kết quả riêng.

| Mã | Mảng | Kịch bản | Điều phải giữ | Tracker gốc | Biên nhận bổ sung trong K17 |
| --- | --- | --- | --- | --- | --- |
| DB01 | metadata/read | cold cache large catalog | same object/permission with bounded query/payload; explain buffers/rows | Chưa chạy theo tracker gốc | — |
| DB02 | metadata/read | warm same catalog | same semantics; cache key tenant/revision correct | Chưa chạy theo tracker gốc | — |
| DB03 | integrity | 0/100/1k/100k unrelated objects | record touched validation bounded; full audit retains coverage | Đạt theo tracker gốc | — |
| DB04 | tenant | cross workspace result/evidence IDs | no leak including cache/404/events/download | Chưa chạy theo tracker gốc | — |
| DB05 | query | filter/sort/cursor changed between pages | stable query-bound cursor, no mixing/omission | Chưa chạy theo tracker gốc | — |
| DB06 | query | each hot read/search query on M/L | query count/plan/index/rows/buffers align expected scope | Chưa chạy theo tracker gốc | — |
| DB07 | query | page50 claims with citations | bounded queries and payload, no fetch all | Chưa chạy theo tracker gốc | — |
| DB08 | transaction | 20 writers same revision | one winning CAS or defined serialization; no lost update | Đạt theo tracker gốc | — |
| DB09 | transaction | unrelated tenants concurrently | no global lock serializing independent work | Chưa chạy theo tracker gốc | native-faults-01/row_lock.json |
| DB10 | transaction | blocked row/long transaction | bounded wait safe error; untouched data and proper retries | Chưa chạy theo tracker gốc | native-faults-01/row_lock.json |
| DB11 | transaction | reverse lock order controlled in staging | detected, resolved according policy; no duplicate effect | Chưa chạy theo tracker gốc | — |
| DB12 | connections | all pool slots busy | bounded waiting, admission, healthy cleanup and no connection leak | Chưa chạy theo tracker gốc | native-faults-01/pool_exhaustion.json |
| DB13 | connections | all replicas/workers/admin/migration | sum pool within DBbudget; admin reserve retained | Chưa chạy theo tracker gốc | — |
| DB14 | idempotency | same key same body | same durable receipt, one business effect | Đạt theo tracker gốc | imports-03/result.json (replay with new-client resume only) |
| DB15 | idempotency | same key different body | 409, no second effect; hash not inside uniqueness | Đạt theo tracker gốc | — |
| DB16 | ACK | commit succeeds response lost | lookup same operation/receipt; retry safe | Đạt theo tracker gốc | imports-03/result.json (replay with new-client resume only) |
| DB17 | publication | snapshot+current pointer+lifecycle callback | one visible transaction or none | Đạt theo tracker gốc | — |
| DB18 | publication | bytes partial before publish | not visible; recover/orphan policy | Chưa chạy theo tracker gốc | — |
| DB19 | publication | object verified DB commit fails | no false result; safe delayed reconciliation | Chưa chạy theo tracker gốc | — |
| DB20 | delete | two projects share immutable bytes | one delete retains other reference/data | Chưa chạy theo tracker gốc | delete-integrity-02/result.json (dedicated lifecycle only) |
| DB21 | job | ownerA expires ownerBclaims Areturns | fence blocks A terminal; B output wins once | Đạt theo tracker gốc | — |
| DB22 | delete | project tombstone before worker commit | no resurrection; terminal matches policy | Đạt theo tracker gốc | delete-integrity-02/result.json (dedicated lifecycle only) |
| DB23 | job | cancel_requested concurrent terminal | one defined terminal; not fake cancellation | Chưa chạy theo tracker gốc | — |
| DB24 | job | worker dies after side effect | idempotent completion with effect ledger | Đạt theo tracker gốc | — |
| DB25 | queue | broker down after DBcommit | accepted job remains durable, dispatcher resumes | Chưa chạy theo tracker gốc | — |
| DB26 | queue | same event redelivered | no duplicate effect; delivery may repeat | Đạt theo tracker gốc | — |
| DB27 | auth | revoke between queue and publish/download | no unauthorized final visibility per policy | Chưa chạy theo tracker gốc | management-01/result.json (successful membership slice only) |
| DB28 | migration | two appversions during rollout | read/write compatible; trace versions | Chưa chạy theo tracker gốc | — |
| DB29 | migration | DDL under normal traffic | bounded lock, no outage masked by errors | Chưa chạy theo tracker gốc | — |
| DB30 | migration | interrupt/backfill twice | checkpoint/resume, checksum/count parity | Chưa chạy theo tracker gốc | — |
| DB31 | migration | concurrent index creation interrupted | invalid index recognized/repaired before acceptance | Đạt theo tracker gốc | — |
| DB32 | release | new application rolled back | old version still safe on compatible schema | Chưa chạy theo tracker gốc | — |
| DB33 | recovery | failover while accepted work pending | no ACK lost; idempotent reconnect/fences | Chưa chạy theo tracker gốc | — |
| DB34 | integrity | bad hash/length in one project | fail closed scoped; no corrupt bytes served | Đạt theo tracker gốc | — |
| DB35 | resource | db WAL/object/temp full controlled | safe admission/rollback, exact recovery | Chưa chạy theo tracker gốc | — |
| DB36 | recovery | restore DB/object to clean env | RPO/RTO witness, IDs/permissions/tombstones/jobs match | Chưa chạy theo tracker gốc | — |
| DB37 | retention | restore older backup after deletion | policy handles deleted content, no silent resurrection | Chưa chạy theo tracker gốc | — |
| DB38 | resource | 2h mixed + heavy with large catalog | RSS/FD/locks/pool/queue bounded, no ack gap | Chưa chạy theo tracker gốc | — |
| DB39 | fairness | 50-80percent traffic one tenant | others keep declared success/deadline and quota | Chưa chạy theo tracker gốc | — |
| DB40 | resource | provider slow/timeout/429 | no DBtransaction held while waiting; safe budget/retry policy | Chưa chạy theo tracker gốc | — |

### 26 ca sự cố

Tracker gốc ghi 4/26 ca đạt. Biên nhận bổ sung giữ phạm vi của từng lượt.

| Mã | Sự cố đưa vào | Kết quả mong đợi | Tracker gốc | Biên nhận bổ sung trong K17 |
| --- | --- | --- | --- | --- |
| F01 | API after ACK | job durable or known terminal; no lost receipt | Chưa chạy theo tracker gốc | — |
| F02 | worker after effect | dedupe/fence prevents duplicate effect | Đạt theo tracker gốc | — |
| F03 | API slow body | other tenant requests bounded and served | Chưa chạy theo tracker gốc | — |
| F04 | lease expires | stale publish rejected | Đạt theo tracker gốc | — |
| F05 | duplicate delivery | exact business effect once | Đạt theo tracker gốc | — |
| F06 | cancel during commit | defined terminal receipt | Chưa chạy theo tracker gốc | cancel-02/result.json (queued cancellation only) |
| F07 | delete during export | no forbidden download or resurrection | Đạt theo tracker gốc | delete-integrity-02/result.json (dedicated lifecycle only) |
| F08 | DB unavailable | readiness/admission and accepted jobs preserved | Chưa chạy theo tracker gốc | — |
| F09 | DB failover | reconcile commit outcome | Chưa chạy theo tracker gốc | — |
| F10 | pool exhausted | bounded waits no leak | Chưa chạy theo tracker gốc | native-faults-01/pool_exhaustion.json |
| F11 | deadlock | retryable transaction policy | Chưa chạy theo tracker gốc | — |
| F12 | object slow | deadline and no API monopoly | Chưa chạy theo tracker gốc | — |
| F13 | object missing/corrupt | scoped fail closed | Chưa chạy theo tracker gốc | — |
| F14 | disk full | no partial visible state | Chưa chạy theo tracker gốc | — |
| F15 | outbox/broker unavailable | durable backlog retry | Chưa chạy theo tracker gốc | — |
| F16 | provider timeout/429 | separate technical and safe retry/budget | Chưa chạy theo tracker gốc | — |
| F17 | auth expires | prompt session recovery no private cache leak | Chưa chạy theo tracker gốc | — |
| F18 | membership revoke | events/download/jobs obey permission | Chưa chạy theo tracker gốc | management-01/result.json (successful membership slice only) |
| F19 | network lost after response | idempotent lookup | Chưa chạy theo tracker gốc | imports-03/result.json (replay with new-client resume only) |
| F20 | SSE reconnect gap | dedupe/resume or reconcile status | Chưa chạy theo tracker gốc | — |
| F21 | browser navigation race | late mutation does not replace new scope | Chưa chạy theo tracker gốc | — |
| F22 | migration interrupted | compatible rollback/backfill resume | Chưa chạy theo tracker gốc | — |
| F23 | container restart/drain | no dropped ACK or stale owner | Chưa chạy theo tracker gốc | — |
| F24 | backup restore | RPO/RTO and canonical content | Chưa chạy theo tracker gốc | — |
| F25 | noisy tenant | fairness envelope | Chưa chạy theo tracker gốc | — |
| F26 | 10x job burst | controlled admission and completion accounting | Chưa chạy theo tracker gốc | — |

### 32 ca danh tính

Các ca này có phần API và UI. Tracker gốc chưa ghi lượt nghiệm thu đồng bộ của cả hai. Báo cáo K03 có bằng chứng backend cho các ca đã thử.

| Mã | Nhóm | Kịch bản | Kết quả cần kiểm | API / UI | J liên quan |
| --- | --- | --- | --- | --- | --- |
| A01 | login/callback | Invalid issuer/audience/state/nonce hoặc reused callback | Không tạo session;error safe và return-to allowlist | Kiên / Quân | J-02,J-04,J-05 |
| A02 | session | Session fixation/renewal concurrent | Rotation/expiry theo policy;không session attacker hoặc logout nhầm người khác | Kiên / Quân | J-02,J-04,J-05 |
| A03 | CSRF | Mutation từ origin khác hoặc thiếu CSRF proof | Từ chối trước effect;SameSite không thay toàn bộ defense | Kiên / Quân | J-02,J-04,J-05 |
| A04 | roles | Viewer sửa/import hoặc client gửi role=owner | Server từ chối;không chỉẩn nút | Kiên / Quân | J-02,J-04,J-05 |
| A05 | objects | Thay workspace/project/result/evidence IDs ở mọi route | Không lộ object/tên/metadata/traversal qua paging/search | Kiên / Quân | J-02,J-04,J-05 |
| A06 | accounts | Alogout→Blogin khi A request/error/event đến muộn | Không Acontent/draft/toast/URL/result hiện ở B | Kiên / Quân | J-02,J-04,J-05 |
| A07 | workspaces | Switch workspace có draft,cache và subscription | Epoch/cleanup;không đọc/sửa workspace cũ qua state còn lại | Kiên / Quân | J-02,J-04,J-05 |
| A08 | tabs | Logout/revoke ở tab1;tab2 đang đọc/sửa | Session invalid trong≤5s hoặc lần tương tác sớm hơn;no stale private view | Kiên / Quân | J-02,J-04,J-05 |
| A09 | history | Back/forward/bfcache sau logout | Không private content flash từ account trước;reauth và safe recovery | Kiên / Quân | J-02,J-04,J-05 |
| A10 | jobs | Revoke creator khi queued/running/publishing | Policy đúng ở dispatch/publish;không grant captured vô hạn hoặc lost ACK | Kiên / Quân | J-02,J-04,J-05 |
| A11 | events | Role revoke trong SSE/poll/resume | Không phát thêm unauthorized data sau policy deadline;resync/reauth rõ | Kiên / Quân | J-02,J-04,J-05 |
| A12 | downloads | Revoke/expire khi download/range/retry | Mỗi request/stream theo policy;không bypass qua URL/cache | Kiên / Quân | J-02,J-04,J-05 |
| A13 | last-owner | Hai admin/owner đồng thời bỏ owner cuối | Transaction giữ ít nhất owner hợp lệ hoặc transfer được authorize | Kiên / Quân | J-02,J-04,J-05 |
| A14 | RLS/pool | Hai tenants liên tiếp trên cùng pooled connection | Không tenant context leak;testrole không owner/superuser/BYPASSRLS | Kiên / Quân | J-02,J-04,J-05 |
| A15 | cache | Permission change trong cached list/detail/search | Key/revalidation đúng,không stale grant tới data nhạy cảm | Kiên / Quân | J-02,J-04,J-05 |
| A16 | uploads | Một user confirm/upload chunk của user/tenant khác | Scope/lease/input identity được kiểm;không object/candidate leak | Kiên / Quân | J-02,J-04,J-05 |
| A17 | login-storm | Expired-session/refresh/reconnect đồng loạt | Bounded+jitter;không blast IdP/provider bên ngoài | Kiên / Quân | J-02,J-04,J-05 |
| A18 | logging | Auth/denied/error/log injection và client telemetry | Không cookie/token/secret/rawsource/PII hoặc unsafe exception bị log | Kiên / Quân | J-02,J-04,J-05 |
| A19 | save-conflict | Role/revision đổi trong lúc save note | Conflict/denied giữ draft an toàn đúng account;không lost update | Kiên / Quân | J-02,J-04,J-05 |
| A20 | mock/hosted | Local header hay demo session gửi lên hosted | Không xem X-P6-Bridge/demo data là identity hoặc quyền hosted | Kiên / Quân | J-02,J-04,J-05 |
| A21 | provision | User mới chưa được cấp workspace/role | Có onboarding/provisioning thật;không auto-owner hoặc đoán quyền theo email domain | Kiên / Quân | J-02,J-04,J-05 |
| A22 | invitation | Invite token reused/expired/revoked hoặc sai verified identity | Không grant membership;token không bị lộ trong logs/referrer,UI có hướng xử lý | Kiên / Quân | J-02,J-04,J-05 |
| A23 | recovery | IdP recovery/MFA/disabled account và return-to | Recovery do IdP chuẩn;không bypass policy,không app thu password/MFA secret | Kiên / Quân | J-02,J-04,J-05 |
| A24 | sessions | Logout-all/credential change trong lúc có renewal đồng thời | Old session không resurrect;quyết định server và mọi tab nhất quán | Kiên / Quân | J-02,J-04,J-05 |
| A25 | object-write | Cùng object ID ở hai tenant hoặc child/parent scope trái nhau | FK/repository/permission cùng giữ scope,không sửa object ngoài workspace | Kiên / Quân | J-02,J-04,J-05 |
| A26 | browser-cache | Private page khôi phục từ browser back/bfcache sau logout | Không private content flash;revalidate/reauth và safe loading state | Kiên / Quân | J-02,J-04,J-05 |
| A27 | audit | Hiển thị activity của account/member đã bị thu hồi | Event list chỉ hiện thông tin được phép;không credential/private profile tràn qua audit path | Kiên / Quân | J-02,J-04,J-05 |
| A28 | preferences | Locale/theme/draft/settings khi đổi account/workspace | Public preferences và sensitive work state được phân biệt;không draft/content account cũ | Kiên / Quân | J-02,J-04,J-05 |
| A29 | owner-transfer | Transfer owner/leave workspace raced với invite/remove member | Không last-owner invalid hoặc auto-grant authority;consumer thấy outcome đúng | Kiên / Quân | J-02,J-04,J-05 |
| A30 | publication | Role/source permission đổi sau job compute trước commit | Publish kiểm policy hiện hành/fence;UI không nhận unauthorized result là done | Kiên / Quân | J-02,J-04,J-05 |
| A31 | drain | Session/job/event khi rollout API và asset version | Không mất quyền/revive session hoặc nhận DTO không tương thích;reauth/resync rõ | Kiên / Quân | J-02,J-04,J-05 |
| A32 | restore | Restore DB/objects nhưng membership/tombstone/session history thiếu | Không resurrect quyền/dữ liệu đã xóa;chặn phục vụ tới khi consistency/recovery policy đạt | Kiên / Quân | J-02,J-04,J-05 |

### 38 thao tác API

Danh mục ghi 35 thao tác đã triển khai trong phạm vi local và 3 thao tác tắt theo capability. Nghiệm thu và số đo tải là các bước riêng.

| Mã | HTTP | Đường dẫn | Điều phải giữ | Trạng thái triển khai gốc |
| --- | --- | --- | --- | --- |
| P01 | GET | /v1/session | Auth expired is 401; never send secret/token to UI logs | Có triển khai local |
| P02 | POST | /v1/session/logout | Revoke session/stream; invalidate client scope | Có triển khai local |
| P03 | GET | /v1/workspaces | Cursor and scoped total; no cross-workspace names | Có triển khai local |
| P04 | GET | /v1/workspaces/{workspace_id}/capabilities | True supported modes, formats, limits and allowed actions | Có triển khai local |
| P05 | GET | /v1/workspaces/{workspace_id}/projects | Filter/sort/cursor bound to actor and scope | Có triển khai local |
| P06 | POST | /v1/workspaces/{workspace_id}/uploads | Bounded resumable transfer; not a server filesystem root | Có triển khai local |
| P07 | POST | /v1/workspaces/{workspace_id}/imports/preview | Upload or scoped connector ref; 202 only after durable job | Có triển khai local |
| P08 | GET | /v1/workspaces/{workspace_id}/imports/{preview_id} | Expiry/source version/withheld fields; no unsafe defaults | Có triển khai local |
| P09 | POST | /v1/workspaces/{workspace_id}/imports/{preview_id}/confirm | Idempotency + explicit mapping; output project/snapshot | Có triển khai local |
| P10 | POST | /v1/workspaces/{workspace_id}/projects/{project_id}/refresh | Revision/fence, unchanged vs new snapshot, immutable past | Có triển khai local |
| P11 | GET | /v1/workspaces/{workspace_id}/projects/{project_id} | Current revision and active/deleting state, not all evidence | Có triển khai local |
| P12 | GET | /v1/workspaces/{workspace_id}/projects/{project_id}/snapshots | Stable immutable snapshot IDs | Có triển khai local |
| P13 | POST | /v1/workspaces/{workspace_id}/projects/{project_id}/analyses | Snapshot + backend capability; no direct browser provider | Có triển khai local |
| P14 | GET | /v1/workspaces/{workspace_id}/results | Project/snapshot/filter scope before pagination | Có triển khai local |
| P15 | GET | /v1/workspaces/{workspace_id}/results/{result_id} | Project/snapshot/analysis version/capability explicit | Có triển khai local |
| P16 | GET | /v1/workspaces/{workspace_id}/results/{result_id}/claims | Exact citation refs, bounded page; unknown not false | Có triển khai local |
| P17 | GET | /v1/workspaces/{workspace_id}/results/{result_id}/evidence/{evidence_id} | Citation belongs to result; redaction + source version | Có triển khai local |
| P18 | GET | /v1/workspaces/{workspace_id}/results/{result_id}/graph | Scope/node/cursor limits; report truncated or next cursor | Có triển khai local |
| P19 | POST | /v1/workspaces/{workspace_id}/results/{result_id}/follow-ups | Parent+selection same scope; idempotency | Có triển khai local |
| P20 | GET | /v1/workspaces/{workspace_id}/jobs/{job_id} | Deadline/retry policy, exact terminal/output | Có triển khai local |
| P21 | POST | /v1/workspaces/{workspace_id}/jobs/{job_id}/cancel | May return cancel_requested; not fake cancelled | Có triển khai local |
| P22 | GET | /v1/workspaces/{workspace_id}/events | If SSE chosen: revocation, resume cursor, heartbeat, quotas | Có triển khai local |
| P23 | POST | /v1/workspaces/{workspace_id}/results/{result_id}/exports | Format/version; durable and authorized output | Có triển khai local |
| P24 | GET | /v1/workspaces/{workspace_id}/exports/{export_id}/download | Revalidate scope; content-disposition safe; TTL and revocation | Có triển khai local |
| P25 | DELETE | /v1/workspaces/{workspace_id}/projects/{project_id} | Tombstone + durable deletion, idempotent receipt, no source deletion | Có triển khai local |
| P26 | PUT | /v1/workspaces/{workspace_id}/results/{result_id}/annotations/{annotation_id} | If persisted review shipped: revision conflict, scope, no research label | Có triển khai local |
| P27 | GET | /v1/workspaces/{workspace_id}/members | Least privilege; only fields needed for settings | Có triển khai local |
| P28 | PATCH | /v1/workspaces/{workspace_id}/members/{member_id} | Role revision/revoke, avoid removing last owner accidentally | Có triển khai local |
| P29 | PUT | /v1/workspaces/{workspace_id}/uploads/{upload_id}/parts/{part_number} | Bytes/count/checksum caps, resumable parts, no cross-actor overwrite | Có triển khai local |
| P30 | POST | /v1/workspaces/{workspace_id}/uploads/{upload_id}/complete | Verify full length/hash and immutable binding before preview | Có triển khai local |
| P31 | DELETE | /v1/workspaces/{workspace_id}/uploads/{upload_id} | Idempotent cancel/cleanup, no removal of referenced evidence | Có triển khai local |
| P32 | POST | /v1/workspaces/{workspace_id}/connectors/pair | If connector enabled: expiry/scope, explicit user device grant, no raw root in public URL | Tắt theo capability |
| P33 | POST | /v1/workspaces/{workspace_id}/connectors/{connector_id}/revoke | Stops new collection; revoke credentials and stream, preserve immutable accepted snapshot | Tắt theo capability |
| P34 | GET | /v1/workspaces/{workspace_id}/connectors | Only safe labels/status/scopes, no credentials or absolute source path | Tắt theo capability |
| P35 | PATCH | /v1/workspaces/{workspace_id}/projects/{project_id} | Display metadata revision conflict; never mutate immutable snapshot | Có triển khai local |
| P36 | POST | /v1/workspaces/{workspace_id}/invitations | If membership is app-managed: expiry/revoke, scoped role, no arbitrary role escalation | Có triển khai local |
| P37 | GET | /v1/workspaces/{workspace_id}/settings | Retention/limits/source policy and provenance, no secret values | Có triển khai local |
| P38 | PATCH | /v1/workspaces/{workspace_id}/settings | Versioned policy bounded by platform limits; no client-selected unlimited quota | Có triển khai local |

## Nguồn đối chiếu chính

- [92 mục K](../requirements/01-Ca-nhan/09-Dau-viec-chi-tiet.csv), [32 mục J](../requirements/02-Phoi-hop/Cong-viec-phoi-hop.csv) và [điều kiện nghiệm thu chung](../requirements/02-Phoi-hop/Phoi-hop-va-nghiem-thu.md).
- [Tracker 8 nhóm J](../requirements/02-Phoi-hop/Nghiem-thu-chung.csv) và [27 cổng phát hành](../requirements/02-Phoi-hop/Gate-phat-hanh.csv).
- [Audit cá nhân trước](k01-k18-kien-audit-20261115/DOI-CHIEU-PHAN-KIEN.md), [K15 backend hai replica](k15-remaining/summary.json), [K14 staging](k14-staging-closeout/KET-QUA-BO-SUNG-K14.01-K14.05.md) và [K16 edge/alert](k16-remaining/KET-QUA-K16.03-K16.05.md).
- [K17 calibration và ma trận ca kiểm](k17-remaining/KET-QUA-K17.md), [model lớn](k1701-large-model/KET-QUA-NOI-MODEL-K17.01.md) và [nhập catalog](k1701-catalog/KET-QUA-CATALOG-K17.01.md).
- [K03 và CI 11/11](k03-real-http/KET-QUA-K03.md).
