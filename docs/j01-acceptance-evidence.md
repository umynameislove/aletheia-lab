# J-01 — trạng thái hiện tại 10/11/2026

Nguồn đối chiếu mới nhất: `j01-current-review-20261110.md`. Phần bên dưới
là lịch sử các vòng review, có phát biểu đã cũ. Hiện đã có login/DB/Keycloak
thật và HTTPS **local**, nhưng chưa có hosted UI→persistence được Kiên+Quân
nghiệm thu trên staging chung. Fixture hiện hành là **v7**; v8 là đề xuất,
consumer v5 do Quân báo chưa có trong checkout này. J-01 chưa passed.

Kết quả gần nhất: 927 unit test J01/K02/K03 đạt; 2 integration PostgreSQL/HTTPS
đạt riêng từng phần; 15 payload được JSON Schema độc lập kiểm. Hành trình
Keycloak→HTTPS→ZIP→worker→ProductView cũng đạt qua HTTPX local (xem
full-local-journey.log), chưa có browser chung. Không cộng số test các vòng.
Runtime upload công bố import/analyze theo role, zip_bundle/local_evidence;
không bật chức năng chưa có. Xem inventory `j01-operation-disposition.csv`
cho đủ38 operation và ranh giới chưa nghiệm thu.

---

# J-01 — tình trạng và bằng chứng

Đối chiếu `02-Phoi-hop/Phoi-hop-va-nghiem-thu.md` và `Nghiem-thu-chung.csv`.
Không đánh dấu nghiệm thu khi chưa có bằng chứng hai bên hoặc staging.

## Phản hồi Bu và vòng v7

- Bu xác nhận phân công server/domain cho Kiên, client/design cho Bu, schema
  và fixture do Kiên sửa, consumer test do Bu giữ. Bu dùng base `2bbcdc2`
  và sẽ merge lịch sử; 75 đường dẫn UI/docs đã nhập trong WIP API nhưng chưa
  merge ancestry, commit hay push.
- Bu báo đã chạy consumer v5 với 10 Problem, Session/Capabilities/job/page;
  đây là báo cáo của Bu, chưa có commit consumer v5 trên nhánh GitHub tại lần
  kiểm gần nhất. Không suy ra consumer v7 đã đạt.
- Schema v7 bổ sung cursor không rỗng và status_url tương đối. 15 fixture
  qua jsonschema độc lập và 38 test contract đạt. So sánh URL với workspace/job
  còn do validator runtime thực hiện.
- Bu chỉ ra hai layout demo; Bảo chưa duyệt và chưa có bằng chứng chốt chung.
  Bu cung cấp fanout local 10 RPC và số đo browser local; chưa có journey mix,
  role distribution, think time, abandonment hay số tab quan sát được.
- Staging/identity/storage chưa có owner hoặc URL được cung cấp theo phản hồi Bu.
  Cần Bảo chỉ định quyền/môi trường; phần backend hiện chưa hoàn thiện. Không
  nhầm test local browser với luồng hosted.
- `j01-job-v8-review-proposal.md` ghi thay đổi shape cần review trước.
  `local-error-inventory-v1.json` là inventory literal local; còn 4 lời gọi
  dynamic chưa phân giải và không tự suy ra HTTP status hosted.

## Cập nhật gần nhất

- Bộ hiện tại là `producer-review-v6`, thêm ràng buộc total/total_kind vào
  Page JSON Schema. V5 và các bản trước được giữ làm lịch sử, không ghi đè.
- Kiểm độc lập bằng jsonschema 4.25.1 + FormatChecker: 15 fixture đạt;
  sai code/status, date-time, extension policy và total-kind được kiểm.
  Có script tái lập `scripts/check_j01_schema.py`; dependency dev đã khai báo.
- Nhánh Quân đã push tại `d4e7fb214d3bb6a74b2ddf0f012bf69c7c3e8e7c`.
  Xem `j01-quan-d4e7fb2-review.md`: 201 test Node 22.13 và Live build đạt.
- Browser Chrome Live: 12/12 test đạt trong bản thích nghi setup Windows,
  cùng code Quân, bridge Python và source tổng hợp. Một số ca chủ động giữ,
  làm mất hoặc sửa response để kiểm lỗi; không gọi tất cả response là
  kết quả producer thật. Log và bản script đã dùng:
  `../j01-live-review-fcdb81c5534f4944ab262a55cdf2e1da/`.
- Runbook để kiểm slice hosted: `j01-staging-acceptance-runbook.md`.
  Chưa có login/storage hosted thật; Docker CLI có nhưng engine chưa chạy.
  Hoàn thiện triển khai backend vẫn thuộc Kiên, không quy toàn bộ phần thiếu cho Quân.

Các mục bên dưới ghi tiến trình trước cập nhật này; khi có chỗ mô tả v5 hoặc
chưa cài validator, dùng kết quả v6 và validator ở trên làm trạng thái hiện tại.

## J-01.01 — base và quyền sở hữu: đang thực hiện

- API worktree `feat/product-api` tại `2bbcdc2a7637c840babd8b71910306c64072bc4b`; WIP chưa commit.
- Patch Quân SHA-256 `23407ef347d720247f519618821edc17b407ab435554c27a8283af61ff95fcb7`
  được áp trong worktree kiểm tra riêng trên cùng base; chưa merge vào nhánh API.
- Theo phân công, Kiên giữ server/domain, Quân giữ client/design; schema/test
  chung cần một người sửa tại mỗi thời điểm. Cần Quân xác nhận base chung và
  ownership sau khi đối chiếu trạng thái mới nhất; chưa suy ra xác nhận từ patch.

## J-01.02 — contract và fixture: đang thực hiện

- `fixtures/j01/producer-review-v5` do `scripts/export_j01_review.py` xuất:
  Session, Capabilities, 10 Problem, receipt/job/page và schema dự thảo.
  Các file không đại diện response từ deployment. Với receipt, URL phải đúng
  workspace/job; với page, total unknown phải là null: đây là validator Python,
  chưa được diễn đạt đầy đủ trong JSON Schema. Consumer phải kiểm scope,
  URL, total-kind và HTTP status theo ngữ cảnh thực.
- Producer Problem schema có điều kiện code/status; consumer schema cho phép
  extension. Pattern date-time chỉ kiểm hình thức; cần validator lịch thực.
  Chưa có kiểm thử JSON Schema độc lập mới trên máy này: cài thư viện thất bại
  vì không có distribution được truy cập.
- Test local trên Windows/Python 3.12.15: 118 test J-01 + K-02 liên quan đạt,
  Ruff đạt. Cùng patch Quân: 12/12 test hosted v2 đạt; khi đọc fixture v5,
  Session/Capabilities và năm lỗi session cũ được nhận, năm mã mới chưa được
  `readProblem` nhận. Cần Quân cập nhật bảng mã và chạy consumer v5; chưa
  có bằng chứng hai chiều trên bộ v5.
- Patch UI trên Node 22.13: npm test 181 pass, 1 fail do A8 phát sinh lỗi
  `Event.returnValue` sau test; tsc và build Live/demo đạt với
  `--configLoader runner`. Dependency sao chép từ môi trường cùng lock,
  không phải `npm ci` sạch. Chưa kiểm browser/staging.
- Mẫu progress 0..1 và Page item mẫu là đề xuất cần Quân review; không
  khẳng định đã có API job/page, idempotency bền hoặc CAS.

## J-01.03 — vertical slice: blocked theo bằng chứng hiện có

Chưa có staging được xác định, IdP/identity thật, persistence hosted, endpoint
source→result hay joint run do cả Kiên và Quân xác nhận. Adapter hiện tại
không triển khai OIDC hoặc domain worker. Cần chủ hệ thống cung cấp môi trường,
cấu hình và quyền truy cập; Kiên triển khai backend và cả hai cùng chạy dữ liệu
tổng hợp, lưu release SHA và bằng chứng request→persistence.

## J-01.04 — targets và tài nguyên: đang thực hiện

Phép tính và giả định ban đầu ở `j01-workload-resources-proposal.md`.
Quân báo có waterfall local và bảng giả định, chưa cung cấp dữ liệu đó trong
gói này; think time, tỷ lệ bỏ dở và số tab chưa đo. Chưa có sizing, cost,
quyền thử, lịch và giới hạn dừng được hai bên/chủ hệ thống chấp thuận. Không
coi con số giả định là capacity hay kết quả 50k.

Hai layout được yêu cầu trong bảng nghiệm thu tổng J-01 cũng chưa có bằng chứng
hai bên cùng xác nhận. `question_chars=2000` là đề xuất được Quân đồng ý;
tài liệu gốc nêu 4000 nên cần người giao việc chấp thuận.
