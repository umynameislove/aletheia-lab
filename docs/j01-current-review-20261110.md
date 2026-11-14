# J-01 — đối chiếu hiện tại 10/11/2026

Trạng thái chung: **đang thực hiện/chưa đủ nghiệm thu**. Không đổi tracker gốc
thành passed. Chưa commit/push. Base checkout API là `2bbcdc2`, nhưng nội dung
chạy chứa WIP của nhiều phần; dùng manifest SHA-256 để nhận diện gói review,
không dùng riêng HEAD làm release identity.

## Đối chiếu bốn bước

| Bước | Đã có bằng chứng | Còn mở / đầu vào / người phụ trách |
|---|---|---|
| J-01.01 | Worktree feat/product-api giữ WIP; lịch sử bản cũ và patch Quân được giữ theo báo cáo; phân công server/domain Kiên, client/design Quân | Kiên+Quân xác nhận cùng base/content/build và người sửa schema tại thời điểm ghép; không coi áp tree diff là merge ancestry |
| J-01.02 | Producer v7 xuất từ model hiện tại tương đương JSON với v7 đã lưu;15 payload/7schema; negative cases được kiểm độc lập; permission matrix và job/page/revision proposal; inventory disposition đủ38 operation | Quân gửi consumer v5/J01_FIXTURES chưa có trong worktree và chạy đúng v7; hai bên chốt route/response logout, paging/job metadata còn draft; hai layout cần version và xác nhận |
| J-01.03 | Backend có login/session/capabilities, ZIP/source/job/output, snapshot và ProductView ban đầu; API-worker riêng; PostgreSQL/HTTPS thực; Keycloak26.8.2 login A→logout→login B qua HTTPX | Kiên hoàn thiện triển khai/readiness-worker và migration nguồn/snapshot/job còn thử nghiệm; Quân nối hosted UI đang còn dùng local RPC; chủ môi trường cấp staging chung; cả hai chạy browser UI→persistence S01–S05 cùng IDs/hashes |
| J-01.04 | Có mục tiêu và phép tính50k/HTTP/job/byte, SLO tham chiếu và cấu hình pool hữu hạn; đề xuất local smoke phía dưới | Quân cung cấp journey/role/think-time/waterfall; Kiên đo CPU/byte/queue/pool trên release; chủ môi trường/Bảo chốt staging/quota/cost/thời lượng/quyền thử; chưa có capacity50k |

Nguồn điều kiện là `02-Phoi-hop/Cong-viec-phoi-hop.csv` và
`Nghiem-thu-chung.csv`. Hai layout thuộc đầu ra tổng. S06–S09 trong runbook là
smoke bổ sung; không tự biến full J-02/J-08 thành điều kiện mới của J-01.
Disposition 38 operation không có nghĩa đã triển khai hoặc nghiệm thu đủ38.

## Code hoàn thiện trong lần này

- Runtime upload dùng UploadReadBackend; role owner/admin/analyst nhận
  import/analyze, zip_bundle và local_evidence. Reviewer/viewer không nhận
  thao tác ghi. Export/follow-up/refresh/delete chưa bật. Domain SQL tiếp tục
  kiểm session/member trước mutation/publication. Analyze hiện chỉ mô tả
  snapshot đầu tiên, không so metric giữa các snapshot hay kết luận cause.
- Readiness kiểm đủ cột nguồn và bảng snapshot trước khi công bố sẵn sàng;
  trước đây thiếu bảng snapshot vẫn có thể ready rồi confirm/analyze lỗi.
  15 ca mới kiểm thiếu bảng/cột, thành công và cancellation. Cả API và worker
  dùng probe này; không thêm migration tự chạy hay suy ra worker liveness.
- Runbook đã ghi đúng check-config offline → schema/provisioning được duyệt
  → TLS API → process worker riêng → health → smoke; không dùng migration
  admission K03 thay cho toàn bộ schema J01.
- Readiness giữ nguyên deadline và chính sách từ chối. Log chỉ có category
  jobs_schema/admission/clock_bound/timeout, không raw exception/token/SQL;
  cancellation truyền lên. Mục đích hỗ trợ điều tra503, chưa kết luận đã sửa
  nguyên nhân. API readiness kiểm schema, không chứng minh worker đang sống.

## Kết quả chạy thực trong lần đối chiếu

- Bộ unit test_j01_*,test_k02_*,test_k03_* trên WIP hiện tại:
  **927 passed,57.12s,exit0**. Kết quả828 ở đầu lần chạy là revision trước,
  không cộng vào927. Lần đầu collection thiếu đường dẫn jsonschema; đã dùng
  dependency có sẵn và chạy lại đầy đủ, không bỏ test.
- `test_k02_split_postgres.py` + `test_k02_https_edge.py`:
  **2 passed,8.48s,exit0**. Bài thứ nhất dùng PostgreSQL thật + ASGI và
  worker process riêng; bài thứ hai dùng TLS thật + backend mô phỏng, không
  có DB/IdP. Hai bài kiểm riêng, không cộng thành một hành trình xuyên suốt.
- `check_idp_logout.py` trên local HTTPS factory + PostgreSQL + realm Keycloak
  riêng: đọc capabilities mới, login A, logout local, trang confirm Keycloak,
  fixed signed-out redirect, login B return_to=/, session A bị từ chối, context
  B mới; exit0. Dùng HTTPX với CA được kiểm; chưa có UI hoặc multiple tabs.
- Serializer hiện tại và fixture v7 tương đương JSON cho22file; validator độc
  lập chấp nhận15payload và từ chối invalid code/status, invalid calendar date,
  total_kind không khớp, cursor rỗng và absolute status URL.
- Ruff đạt trên các file đã sửa. Chưa chạy lại toàn bộ research/product test,
  frontend mới hoặc fresh dependency install. Kết quả frontend201/browser12
  trước đây là historical local bridge, không phải hosted acceptance hiện tại.

- Hành trình nối xuyên suốt mới nhất (`full-local-journey.log`) đạt exit0:
  Keycloak thật → HTTPS managed API → ZIP có hash → durable receipt/idempotency
  → managed worker process riêng → preview → confirm snapshot → analyze →
  ProductView ban đầu từ PostgreSQL. Client HTTPX độc lập đọc lại cùng output;
  account B nhận404 với cả job/status và output của A. Worker dừng sạch.
  Result: `p6-result-63ef0d2d9670fd4eaf5823d74d1151538562a6f01b427c3aa6206d4250cb9292`.
  Artifact result SHA256:
  `dc6fdefde0cb7ce1df7857a68ace0dc6643295c2afb49237a8203fe926c06257`.
  Đây vẫn là HTTPX local; không kiểm browser/multi-tab hay staging chung.
- Kiểm Git remote trực tiếp: nhánh `feat/product-experience-quan` vẫn tại
  `d4e7fb214d3bb6a74b2ddf0f012bf69c7c3e8e7c`; chưa thấy consumer v5 mới.
  HEAD API giữ `2bbcdc2`; không có file staged; WIP của nhiều chat được giữ.

Log gốc của lần này nằm trong `j01-verification-20261110` ở workspace Đồ Án3.
Gói gửi Quân chỉ có fixture/schema/tài liệu, không có secret hoặc chứng chỉ
private. Các module/build/repo khác không được tự coi đã qua kiểm từ kết quả này.

## Đề xuất tài nguyên để chốt J-01.04

- Architecture hiện có: modular API + worker process riêng, cùng codebase;
  PostgreSQL và Keycloak local. Default một API pool8 + một worker pool2 +
  reserve2 =12, dưới connection budget16. Đây là budget cấu hình, chưa sizing.
- Máy local quan sát: i5-13500H,16 logical CPU; Docker báo16CPU và
  8.135.200.768byte memory. Không suy số user/RPS từ số core hoặc RAM.
- Đề xuất smoke chung: synthetic accounts/workspaces riêng, tuần tự một
  journey mỗi lần, tối đa1job đang chạy,10phút, không thử tải/không cloud có
  phí; dừng ngay khi scope leak/hash mismatch/secret leak hoặc timeout liên
  tiếp. Kiên giữ backend, Quân giữ browser evidence. Cần cả hai chấp nhận
  trước khi coi là lịch nghiệm thu; localhost chưa Quân truy cập được.
- Mục tiêu50k/15000HTTP/s/20heavy jobs/s và SLO vẫn là tham chiếu của đề.
  833session/s cộng200job poll/s vượt budget900status/s: không cộng trùng,
  cần Quân đưa journey/tab fanout để phân bổ lại. Không tự đổi profile để đạt.
- Chưa chọn provider hoặc xác nhận đơn giá/quota. Chi phí cloud ghi chưa
  biết, không ghi0 giả; lần này không mua/tạo tài nguyên cloud. Kế hoạch50k
  phải được chốt ở J-01; benchmark đầy đủ thuộc J-06/J-07.

## Điều kiện chưa được phép kết luận xong

Consumer chưa chạy fixture hiện hành; hosted UI chưa nối; hai layout chưa
được chấp nhận; staging chung/owner/budget chưa được xác nhận; S01–S05 chưa
có joint_run_id và hai người chấp nhận UI→persistence. Các mục này cần phản
hồi Quân/Bảo hoặc lần chạy chung, không thể thay bằng tự đánh dấu passed.
Đồng thời còn việc Kiên: worker health/deploy, đo sizing và hoàn thiện
runtime/migration để người khác dựng được; không quy toàn bộ thiếu cho Quân.
