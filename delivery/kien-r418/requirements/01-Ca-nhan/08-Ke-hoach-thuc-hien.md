CẬP NHẬT R3 — KIỂM TỔNG K01–K18,13/11/2026

R3 13/11/2026 LOCAL: 2920 unit đạt/13 Windows skip; 418 native PostgreSQL đạt/7 opt-in skip, cùng source2499/runtime667. Hai build giống byte, cài sạch exact52 dependency,667file/4child/PDF Unicode đạt. Windows trả về là OLD639runtime: smoke23PASS, upload20012PASS1FAIL tại part COMMIT5s; không phải kết quả Windows R3. UI Quân R2.1:327unit/13Chrome controlled đạt; live79376/80000B,hosted71772/80000B,maxchunk15315/25000B đạt. Chưa phải nghiệm thu realAPI/IdP/CI/deployment/50k/joint. 

Đọc 01-Ca-nhan/Kiem-tong-K01-K18-20261113-R3.txt. Bu nhận Ban-giao-Bu-Tich-hop-K01-K18-20261113-R3.zip; Kiên nhận Ban-giao-Kien-Windows-K01-K18-20261113-R3.zip và trả evidence. Thư trả lời Quân:02-Phoi-hop/Tra-loi-Quan-K13-K18-R2.1-20261113.txt.

Source2499:912c4df60f10b937b326e9dc7f7279c48264bc7c8a26b701da40be8f22922957
Runtime667:823fab7372763a349c3a81bfe2f7a79eef8b6cb1804f610b7269b865d5945846

K13–K18 vẫn14local/13partial/4mở; full release/primary50k/realIdP/joint chưa đạt. K01 có2inventory/boundary đã kiểm local,2phần lineage/baseline còn partial. Cả hai gói mới giữ API và UI Quân thành component riêng; sau Bu tích hợp phải sinh identity/receipt mới. Không sửa /Users/apple/study/aletheia-lab; ZIP cũ giữ nguyên.

--- LỊCH SỬ TRƯỚC R3, GIỮ NGUYÊN SCOPE RECEIPT CŨ ---

CẬP NHẬT R2 — 13/11/2026

K13–K18 giữ 14 tiêu chí đạt local, 13 partial, 4 mở; chưa pass release chung.
R2: 2.920 unit đạt/13 Windows skip; 411 native đạt/7 skip. Runtime giống nhau
giữa lượt native và bản cuối; delta công cụ/receipt được kiểm riêng. Wheel/sdist
tái dựng, exact 52 dependency, 667 file và 4 child đạt. UI Quân bản sao: 327 unit,
13 Chrome controlled transport đạt; hosted budget còn hai file FAIL.

Đọc 01-Ca-nhan/Chi-tiet-17-muc-K13-K18-20261113-R2.txt và
02-Phoi-hop/Gui-Quan-can-chot-K13-K18-20261113-R2.txt (8 mục cần Quân chốt).
Gói mới: Ban-giao-K13-K18-20261113-R2.1.zip; gói Quân: Ban-giao-Quan-K13-K18-20261113-R2.1.zip.
Source cuối 2498 file: 1bd0376a85d1e46264f7fd8a26add071a634354c22d89bb55cd853ced48f6385
Runtime 667 file: 09ffc3bb4519631a1d4c3622c226172f76a7698606484bff3ca1459176cbe559
Windows native có dependency conditional ngoài profile 52 pin; clean runtime proof
chạy WSL/Linux theo guide R2. Không nới lock/checker hoặc suy diễn Windows đã pass.
Không chạy Docker/real IdP; thư mục aletheia-lab vẫn chỉ đọc. Gói cũ giữ nguyên.

--- LỊCH SỬ TRƯỚC R2 ---

# Cập nhật K13–K18 ngày13/11/2026

Source `c5410f5021643d565bbb5365989459a49a2eeb62ca72fa6e81ca48aa49b683c6`;667runtime `d5209e5f4f64bf51165229df465b403a98ca0003ff722d1dc32d0016f0c2af31`. K13 có5/5tiêu chí local đạt; toàn31child:14passed_local,13partial,4open. K14–K18 chưa được ghi fullPASS.

Kiểm chứng cuối:2900unit;412native/worker/HTTPS/L/PITR unique (2Dockerrecovery chưa chạy);45module Ruff/mypy;wheel/sdist667byteparity/4actualchildren. Native100k/all3exports đạt403.60s;100principal debug486/486positive,21jobs,50wholeexports. Chưa phải primary50k/SLOproduction.

Primaryclaim/graph dimensions bị analyzer64series giới hạn; full50k/200k/2m,stress/soak/DB40F26/cost,realIdP/image/offhostDR,Quán/browser và27jointgates còn mở. Đã chuẩn bịTXT/folder/ZIPvàrunbook cho operator; không chạyDockerMac hoặc sửa referencealetheia-lab. PrivatePG/runtime đãdọn. Chi tiết: [báo cáo](Bao-cao-K13-K18-20261113.txt),CSVnghiệmthu/child và `../Bang-chung/20261113-k13-k18/result-final.json`.

---

# Các bước triển khai và kiểm riêng — Kiên

Đây là phần bóc tách card thành các đầu việc cụ thể, không phải thêm một tầng
kiến trúc hoặc buộc mỗi dòng thành một PR riêng. Gộp những bước cùng slice
để làm hiệu quả; giữ từng điều kiện nhận để tránh bỏ sót. Input chung, output
chung và nghiệm thu card vẫn ở tài liệu công việc; bảng dưới bổ sung output,
phương pháp và phép kiểm của từng bước. Mọi subtask bắt đầu **not_run**.

Dependencies chỉ thứ cần có, không ép đợi toàn bộ card khác hoàn tất. Bắt đầu
interface/fixture trước rồi ghép triển khai thật; mọi thay đổi contract cần
người nhận phía bên kia chạy kiểm. D1–D12 là lịch điều phối trong phần phối hợp,
không ước lượng chắc chắn từng dòng làm xong trong một giờ.

## K-01 — Nền mã, baseline và ranh giới local–hosted

**Đầu vào:** main mới nhất; hai nhánh cũ; audit hiện tại; overlay nghiên cứu của Bao.

**Phụ thuộc/đầu nhận:** Không; cần quyền đọc repo và môi trường local dùng dữ liệu tổng hợp.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-01.01 | **Đối chiếu ba nhánh và giữ WIP.** Bảng commit/diff cần giữ và base SHA của hai nhánh mới. | Đọc main và các diff liên quan; sao lưu công việc trước khi tích hợp. | Không mất file hoặc commit; liệt kê rõ phần mới, phần đã merge và phần còn riêng. |
| K-01.02 | **Dựng baseline thực tế.** Lệnh dựng từ môi trường sạch và một hành trình local có kết quả đã loại dữ liệu nhạy cảm. | Dùng nguồn tổng hợp, production build và kho riêng; ghi các phiên bản. | Luồng UI → HTTP → service → store → download chạy được; nếu lỗi, có cách tái hiện rõ ràng. |
| K-01.03 | **Lập bản đồ operation và module.** Ánh xạ P01–P38 và các route bổ sung tới người phụ trách, domain và storage. | Phân biệt phần lõi với lựa chọn triển khai; không đánh dấu stub là tính năng đã có. | Mỗi hành trình lõi có đầu nhận ở backend và kiểm thử dự kiến; không để phần việc thiếu người phụ trách. |
| K-01.04 | **Giữ ranh giới nghiên cứu.** Danh sách public interface và phần Bao chưa tích hợp. | Đọc capability và commit thực tế; không sao chép nhãn đánh giá của con người hoặc dữ liệu evaluator. | Fixture, dữ liệu tải và báo cáo chỉ dùng nguồn tổng hợp hoặc nguồn công khai được phép; không ghi đè overlay. |

## K-02 — Hosted API tối giản và hợp đồng phiên bản

**Đầu vào:** Domain P3/P6 hiện có; local bridge; contract P01–P38.

**Phụ thuộc/đầu nhận:** K-01, J-01; Quân kiểm fixture phía client song song.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-02.01 | **Thiết kế hosted adapter.** API entrypoint, ranh giới repository, cấu hình và health/readiness. | Giữ domain dùng chung; tách API và worker thành các process riêng, không chia thêm microservice. | Không đưa local bridge ra public; tác vụ CPU gây blocking không chạy trên event loop. |
| K-02.02 | **Schema và phiên bản ở hai biên.** OpenAPI/schema và mẫu request, response, error. | Kiểm kiểu, độ dài, field không được khai báo, version/scope và khả năng tương thích. | JSON không hợp lệ, quá cỡ hoặc có key trùng bị từ chối an toàn; Quân parse được mọi payload hợp lệ. |
| K-02.03 | **Bổ sung operation cho auth và onboarding.** Auth start/callback/session, khởi tạo workspace và luồng invite cần thiết. | Có contract thực tế cho lựa chọn IdP-managed hoặc app-managed; P01–P38 không giới hạn toàn bộ tính năng. | Người dùng được cấp tài khoản vào đúng workspace; luồng first-owner và nhận invite chạy được. |
| K-02.04 | **Edge, TLS và routing.** Chính sách proxy/HTTPS/static asset và giới hạn request. | Xác định trusted proxy, timeout cho body/header/toàn request, CSP và chính sách cache dữ liệu riêng tư. | Forwarded header giả không thay đổi identity hoặc ranh giới tin cậy của client IP; body gửi chậm không khóa toàn bộ API. |
| K-02.05 | **Kiểm thử consumer của contract.** Fixture dùng cùng schema; producer test và consumer test chạy trong CI. | Dùng một nguồn contract, khả năng tương thích khi migration và capability flag có phiên bản. | Quân dùng fixture từ serializer thực tế; schema drift làm kiểm tra thất bại trước khi ghép release. |

## K-03 — Đăng nhập, session và vòng đời tài khoản

**Đầu vào:** IdP/OIDC được duy trì hoặc thư viện framework chuẩn; HTTPS và policy session.

**Phụ thuộc/đầu nhận:** K-02 + IdP/staging đã được phép sử dụng.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-03.01 | **OIDC và callback.** Luồng đăng nhập dùng đúng thư viện; issuer/audience/redirect/state/nonce/PKCE phù hợp với flow. | Dùng IdP được duy trì; kiểm callback sai, replay, open redirect và clock skew. | Callback hoặc token sai không tạo session; return-to chỉ nhận các path được phép. |
| K-03.02 | **Khởi tạo và khôi phục tài khoản.** Tài khoản thử, chính sách verified-email và khôi phục qua IdP. | Không tự viết dịch vụ password hashing/reset nếu đã dùng IdP. | Người chưa được provision hoặc đã bị disabled biết trạng thái; khôi phục không bỏ qua MFA hay chính sách identity. |
| K-03.03 | **Vòng đời session.** Cookie/session phía server, thời hạn idle/absolute, rotate/logout và session inventory cần thiết. | Kiểm fixation, race khi renewal và việc dùng lại session sau logout. | Session đã logout/revoke không dùng được; cookie flag và TLS đúng, secret không vào storage phía client. |
| K-03.04 | **CSRF và nguồn request.** CSRF protection của framework và chính sách Origin/Fetch Metadata. | Thử mutation từ cross-site, thiếu token và content-type bất thường. | Request không được phép không tạo effect; SameSite không thay thế toàn bộ kiểm tra CSRF. |
| K-03.05 | **Đổi tài khoản và dùng nhiều tab.** Tín hiệu và chính sách phía server cho logout-all/revoke/reauth. | Phối hợp với Quân kiểm tab A/B và request đang chờ. | Membership/session hết hiệu lực được phản ánh trong ≤5 giây theo chính sách dự án; không lẫn dữ liệu giữa hai tài khoản. |
| K-03.06 | **Abuse và auth outage.** Rate limit và xử lý riêng lỗi đăng nhập. | Thử outage, timeout của IdP staging và refresh storm có giới hạn. | Không redirect/retry vô hạn; không phát sinh phí hoặc traffic vào IdP bên ngoài khi chưa được phép. |

## K-04 — Workspace, phân quyền và cách ly dữ liệu

**Đầu vào:** Ma trận quyền trong contract; actor từ session; toàn bộ object/job/export/event.

**Phụ thuộc/đầu nhận:** K-03 + ma trận role/object của J-02.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-04.01 | **Khởi tạo workspace và first-owner.** Khởi tạo workspace, chuyển owner và quy tắc last-owner. | Dùng transaction và unique constraint; người dùng không được tự nâng quyền lên owner. | Leave/remove đồng thời không tạo workspace vô chủ hoặc cấp sai quyền. |
| K-04.02 | **Chính sách quyền theo action.** Ma trận role và quyền trên resource; không tin role string do client tự khai. | Dùng authorization helper tập trung và deny-by-default trên API. | Mọi operation có kiểm thử cho phép/từ chối theo object scope; operation lõi được chặn ở server, không chỉ ẩn ở UI. |
| K-04.03 | **Các đường đọc phụ.** Search/cursor/evidence/graph/event/export/download dùng cùng ranh giới quyền. | Thử tráo ID, cache key, quan hệ child-parent và query token. | Không lộ tên, count, source hoặc result ngoài scope được phép; response an toàn và thống nhất. |
| K-04.04 | **Revoke xuyên qua tác vụ async.** Role/policy revision tại dispatch, publish, stream và download. | Chốt thời điểm kiểm quyền và expiry; kiểm lại trước effect hoặc dữ liệu mới. | Không giữ grant cũ vô hạn; không hứa thu hồi các byte đã tải hợp lệ trước revoke. |
| K-04.05 | **Tenant context trong pool.** Scope ở repository/RLS và binding của worker role. | Nếu dùng RLS: đặt setting trong transaction, áp dụng USING/WITH CHECK và dùng role không phải owner. | Hai tenant dùng lại cùng DB connection không rò context hoặc bỏ qua policy. |

## K-05 — PostgreSQL, object store và integrity có giới hạn

**Bằng chứng full catalog ngày 11/11/2026:** Owner bỏ giới hạn 500 MiB. Actual
SQL: 200.002 snapshot/2.000.010 result/200.002 bundle, 8 query types × 2 strata ×
5 EXPLAIN, source stable, exit 0 và cleanup. Phạm vi aggregate metadata; chưa
cold/50k/restore; full catalog này không phải bài soak. Receipt
`../Bang-chung/20261111-macos/k05/fulltarget-summary.json`.

**Storage soak riêng đã đạt:** current installed wheel chạy 7.200,73 giây với
hai tenant đọc/ghi/update/delete, VACUUM/ANALYZE và GC sau grace 60 giây thật;
633 file bản cài/626 input build giữ nguyên, schema/object/temp riêng đã dọn.
`pg_dump` cùng namespace không ngắt hai reader. Receipt
`../Bang-chung/20261111-completion/k05-soak/current2h/qualification.json`.
Không suy 50k/SLO/fairness/fault hoặc restore/PITR/RPO/RTO từ storage scope này.


**Đầu vào:** SQLite/domain identities hiện có; seed 50k; DB 40 cases.

**Phụ thuộc/đầu nhận:** K-01 + K-02; các invariant của schema/domain.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-05.01 | **Schema và canonical identity.** Table/FK/unique/check constraint cho metadata/session/job/object reference. | Ánh xạ invariant từ SQLite/P3 sang PostgreSQL; phân biệt mutable revision với immutable payload. | Scope, parent và hash đúng; hai facade không sinh hai nguồn dữ liệu chuẩn lệch nhau. |
| K-05.02 | **Bản đồ query và index.** Danh sách query quan trọng cùng plan/count/rows/buffers trên catalog lớn. | Thiết kế theo filter/order/paging/tenant; chỉ tạo index có mục đích. | Mỗi lần click không scan hoặc load toàn catalog; có bằng chứng về cardinality và query plan. |
| K-05.03 | **Publish object và reconcile.** Quy trình stage/hash/verify/ready-reference và dọn orphan. | Tiêm lỗi giữa lúc ghi object và commit metadata; xác định grace period và lease. | Record hiển thị không trỏ tới object bị thiếu; orphan không tăng vô hạn, GC không xóa object còn reference. |
| K-05.04 | **Kiểm integrity theo scope và scrub.** Validation khi sử dụng, kiểm quan hệ index/source và audit nền. | Giữ các biện pháp phát hiện corruption; có lý do rõ khi thay việc scan toàn kho lúc mở. | Không phục vụ object bị sửa hoặc bị thiếu; scrub phát hiện corruption ngoài hot path. |
| K-05.05 | **Migration dữ liệu cũ.** Importer có phiên bản từ SQLite sang PostgreSQL/object storage và báo cáo parity. | Sao chép dữ liệu cũ vào staging; đối chiếu ID/hash/count/semantic export/quyền. | Không mất snapshot/result/history; migration lỗi có rollback/recovery, giữ nguyên nguồn gốc dữ liệu. |
| K-05.06 | **Maintenance và tăng trưởng có giới hạn.** Metric cho vacuum/analyze/WAL/index/temp/orphan retention. | Soak có write/update/delete, cùng với read; không chỉ đọc catalog nhỏ. | Tăng trưởng catalog và mức lock/I/O có giới hạn; backup/GC không làm hỏng consumer đang hoạt động. |

## K-06 — Transactions, revision và idempotency

**Đầu vào:** Mutation/job requests; expected_revision; hai writer/race schedules.

**Phụ thuộc/đầu nhận:** K-04 + K-05; ranh giới transaction.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-06.01 | **Idempotency ledger.** Scope key, body digest, response/receipt và chính sách expiry. | Đặt unique theo actor/workspace/operation/key; dùng body hash để phát hiện conflict. | Cùng key/body chỉ tạo một effect; khác body trả conflict, không tạo operation mới. |
| K-06.02 | **Mutation dùng CAS/revision.** Expected revision cho project/note/settings/current pointer. | Conditional update trong transaction; xử lý rõ client dùng revision cũ. | Hai writer không gây lost update; người dùng nhận đúng revision và outcome. |
| K-06.03 | **Danh mục race.** Các lịch refresh × refresh, note × note, delete × publish, role × effect. | Dùng barrier xác định cho cửa sổ commit; không dựa vào sleep ngẫu nhiên. | Mỗi race có invariant và outcome thực tế tới consumer; việc DB không crash chưa đủ để nghiệm thu. |
| K-06.04 | **Xử lý deadlock và serialization.** Retry transaction có giới hạn và lỗi conflict an toàn. | Thử deadlock/timeout trên staging; không retry side effect ngoài transaction. | Retry giữ idempotency và deadline; không queue/retry vô hạn hoặc tạo effect kép. |
| K-06.05 | **Audit action và commit.** Liên kết request/operation/revision/effect. | Ghi metadata an toàn, đủ tra cứu duplicate hoặc response bị mất; không ghi secret hay raw payload. | Sau khi mất mạng ở thời điểm ACK, vẫn tra được operation/effect với đúng result và quyền. |

## K-07 — Upload, preview và nguồn nhập an toàn

**Đầu vào:** Wizard/mapping Quân; giới hạn S/M/L; local root grant và hosted upload.

**Phụ thuộc/đầu nhận:** K-04 + K-05 + consumer preview của Q-07.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-07.01 | **Upload staging và resume.** Chunk/session manifest, quota và operation complete/cancel. | Thử part trùng, sai thứ tự hoặc bị thiếu; kiểm length/hash và retry. | Không nhân đôi byte hoặc effect; dữ liệu staging hết hạn được dọn, báo đúng trạng thái. |
| K-07.02 | **Ranh giới tin cậy của file và archive.** Format được phép và giới hạn expanded bytes/count/depth/path/symlink. | Xử lý nội dung nhập như dữ liệu; parser có giới hạn, không eval/pickle hoặc thực thi project. | Chặn archive độc hại, path nguy hiểm và archive bomb; không ghi ngoài namespace hoặc lộ secret/exception không an toàn. |
| K-07.03 | **Preview contract và lease.** Candidate/source seal và lease/version khi confirm. | Kiểm lại source ngay lúc confirm; giữ trạng thái unresolved/sensitive-withheld. | Source thay đổi hoặc lease hết hạn không tạo snapshot dở dang hay âm thầm dùng mapping cũ. |
| K-07.04 | **Mapping nhiều run.** Target/baseline/metric/class/unit/direction/threshold. | Tái sử dụng validator của domain; yêu cầu lựa chọn tường minh khi chưa rõ ý định người dùng. | Xử lý đúng hơn 3 run và Unicode; không đoán target/unit hoặc bỏ run để vượt validation. |
| K-07.05 | **Connector tùy chọn có kiểm soát.** Grant/revoke nguồn hoặc phương án hosted upload thay thế. | Chỉ bật connector khi có allowlist, chống SSRF và xử lý credential; thể hiện rõ khác biệt capability. | Luồng hosted import lõi vẫn dùng được; connector được bật phải revoke đúng, connector chưa có không báo ready. |

### Class/unit đã triển khai local sau đối chiếu Quân — 11/11/2026

Đã triển khai trong `/Users/apple/study/aletheia-lab-feat-product-api` và kiểm
producer/consumer local. `/Users/apple/study/aletheia-lab` vẫn chỉ đọc; việc đọc
source không thay cho peer sign-off hoặc nghiệm thu staging.

| Phần | Hành vi hiện tại | Ràng buộc |
|---|---|---|
| Class | Người dùng chọn cột class; không chọn thì `class_label:null` (observation không có chiều class) | Nhãn nguồn phải là text NFC có giới hạn; không ghép vào metric/run, không tự suy từ target |
| Unit | Khai báo unit theo metric hoặc chọn rõ Unknown; có thể chọn cột nguồn để đối chiếu | Cột nguồn phải khớp khai báo; không tự suy ratio/percent hoặc đổi ms/s |
| Direction/threshold | Một policy theo metric áp dụng cho mọi class | Absolute threshold cùng unit; so sánh inclusive `>=` bằng Decimal |
| Kết quả | Giữ class/unit/origin trong observation, snapshot, change, JSON/Markdown và UI | Origin là source/declared/unknown; UI dùng operands/delta/status server |

Request mới dùng `project-mapping-request/v2`; mapping/result/snapshot/comparison
và ProductView có model v2 riêng. Request cũ vẫn đúng v1; 12 schema v1 đã đối
chiếu nguyên vẹn. Không backfill hoặc sửa lịch sử; cấm so sánh trộn snapshot
v1/v2 và cấm đổi unit giữa snapshots, kể cả unit unknown(null) sang known;
đây là unit value, không phải tên `unit_origin`. Identity v2
phân biệt `(run, metric, class, unit, step)`. Chặn duplicate, unit mâu thuẫn và
nhãn chứa mẫu credential/PII bằng các guard hiện hữu trước publication.

Shared editor dùng cho local và HostedConnect. Hosted chỉ gửi v2 khi server
bật `features["mapping.class_unit_v2"]`; server cũ giữ nguyên request v1.
Fixture hiện hành: `fixtures/metric-dimensions-v2` và
`fixtures/metric-dimensions-hosted-v2` trong API copy. Đã kiểm CSV/JSON,
5 run Unicode, 2 class, ngưỡng bằng đúng delta, refresh lỗi giữ parent,
reload/follow-up/report và queue/canonical PostgreSQL thật.

HostedConnect hiện đã chọn `/uploads` và `/imports` theo capabilities modern,
giữ key/upload ID khi mất ACK, đọc progress server và chỉ gửi phần thiếu;
preview hết hạn yêu cầu review lại. Form/route abort chặn mutation kế tiếp.
Bản cuối đã kiểm native HTTPS/API/PG/separate worker với ZIP 4.207.407 byte,
hai parts, lost response part1 sau API200, leased confirm và saved-result reload.
`native-hosted-browser/qualification.json` trong evidence mới là scope tổng hợp
cuối; seeded session và exact SPKI test pin không thay IdP/public CA/joint run.

Bài 100.000 dòng v2 kiểm **isolated upload-worker preview/confirm và giữ đủ
observations**, chưa phải HTTP→PG persistence 100k hoặc L analysis/refresh: snapshot thực 42.915.275 byte vượt input worker
16 MiB hiện tại; confirm riêng có output cap 64 MiB, output operation khác
vẫn 32 MiB (gồm refresh). Các bước đó từ chối có giới hạn.
K08/K11/K14 nhận phần xử lý snapshot lớn; không tăng mọi budget hay gọi bài
parser này là đủ S/M/L end-to-end. Nghiệm thu chung Quân, IdP và deployment vẫn
được ghi riêng trong tracker.

Nguồn đối chiếu Quân: `Connect.tsx`, `types.ts`, `data/format.ts`, `Project.tsx`.
Bằng chứng hiện tại: `../Bang-chung/20261111-completion/domain-v2-final.json`,
`hosted-v2/result.json` và `frontend-final-receipt.json` trong cùng evidence root.

## K-08 — Pipeline snapshot–comparison–result bất biến

**Đầu vào:** P3 mapping/baseline/threshold; source manifest; fixture edge cases.

**Phụ thuộc/đầu nhận:** K-05 + K-06 + K-07; input đã validation.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-08.01 | **Confirm nguyên tử và initial snapshot.** Bundle/mapping/snapshot/current pointer trong cùng transaction. | Tiêm lỗi trước/sau commit; dùng object identity có scope. | Không để project/snapshot dở dang; initial result gắn đúng source generation. |
| K-08.02 | **No-op và adverse refresh.** Giữ identity khi không thay đổi, tạo revision mới khi có thay đổi. | Kiểm source state seal, metric boundary/precision và transaction callback. | Timestamp đơn thuần không tạo snapshot; refresh lỗi giữ snapshot cũ. |
| K-08.03 | **Result và history bất biến.** Pin result/turn/parent/evidence theo source và analysis version. | Mở result cũ sau nhiều lần refresh; đối chiếu hash/operands/export. | Thay current pointer không sửa history hoặc citation của result cũ. |
| K-08.04 | **Trường hợp biên về số và schema.** Output tham chiếu cho zero/negative/tiny/null và class/unit. | Đối chiếu decimal threshold cùng metric before/after/removed/added. | Không đổi số hoặc suy ra cause từ delta; thiếu dữ liệu giữ đúng unknown/not-assessed. |
| K-08.05 | **Publish đồng thời.** Generation CAS, tombstone và role revision khi publish. | Dùng cơ chế của K-06/K-09; không bổ sung một transaction engine khác. | Result cũ không trở thành current; giao task trùng không tạo thêm immutable record trùng. |

## K-09 — Durable jobs, retry, cancel và fencing

**Đầu vào:** Job contract, idempotency, deadline/quota, worker lifecycle.

**Phụ thuộc/đầu nhận:** K-04 + K-05 + K-06; consumer trạng thái của Q-11.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-09.01 | **Submit job và receipt.** Input reference cùng job được lưu bền trước HTTP 202; có đường tra receipt. | Commit state trước response; giả lập response mất trên đường về. | Retry/key tra đúng job; job đã ACK không mất khi API process chết. |
| K-09.02 | **Worker lease và fencing.** Owner/epoch/expiry/heartbeat, cùng giới hạn claim/publish. | Kill hoặc partition worker; chuyển lease rồi cho worker cũ quay lại. | Worker cũ không publish được; tra được attempt history, không chạy đồng thời mọi job không giới hạn. |
| K-09.03 | **State machine cho deadline và retry.** Phân biệt lỗi retryable/terminal, max attempts và expiry. | Tính clock/deadline từ cam kết ban đầu; không reset để hợp thức hóa result trễ. | Mọi job đã nhận ở trạng thái terminal hoặc còn pending trong hạn; lỗi vĩnh viễn không retry vô hạn. |
| K-09.04 | **Ngữ nghĩa cancel.** Cancel khi queued, cancel_requested khi running và outcome có fencing. | Tạo race với result commit; kiểm effect trước khi báo cancelled. | Success đã commit được hiển thị đúng; khi báo cancelled, không worker nào còn quyền commit. |
| K-09.05 | **Fairness và quota của queue.** Admission theo tenant/job class và queue có giới hạn. | Tạo hot tenant/job storm cùng read và job nhỏ chạy nền. | Một tenant không chiếm hết slot; công việc bình thường trong envelope vẫn được phục vụ đúng deadline. |
| K-09.06 | **Mở lại và recovery.** Job status/event ledger và reconcile khi startup. | Restart API/worker/queue owner; UI tra lại bằng cùng ID. | Job đã ACK không biến mất; attempt dở không báo complete, consumer tiếp tục được. |

## K-10 — Read API, pagination, search, graph và cache

**Đầu vào:** Catalog200k snapshot/2m result; per-object authorization; consumer page sizes.

**Phụ thuộc/đầu nhận:** K-04 + K-05; nhu cầu paging của Q-03/Q-08/Q-09.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-10.01 | **Read projection có giới hạn.** Page cho project/result/claim/evidence có giới hạn kích thước. | Query/projection ngay trong storage; không load toàn bộ rồi cắt trong memory. | Payload có giới hạn trên catalog 2 triệu result; người dùng vẫn truy cập đủ dữ liệu qua các page. |
| K-10.02 | **Gắn cursor với query.** Opaque cursor pin tenant/filter/sort/snapshot/revision. | Thử đổi query, dùng cursor khác scope và insert/delete giữa các page. | Không skip/duplicate do contract mơ hồ hoặc rò dữ liệu khác scope; cursor không hợp lệ có đường khôi phục. |
| K-10.03 | **Search và graph subquery.** Search/filter có index và subgraph continuation. | Đo rows/count/query plan; không tải toàn bộ graph về browser mỗi lần click. | Không cắt dữ liệu âm thầm; graph relation/source đúng, total là số chính xác hoặc được ghi rõ là ước tính. |
| K-10.04 | **Chính sách cache.** Cache key theo tenant/object/version/visibility; có invalidation/revalidation. | Chỉ thêm cache khi profile cho thấy cần; kiểm quyền trước khi đọc cache. | Revoke/delete/update không trả payload cũ trái quyền; hot/cold đều giữ đúng dữ liệu và quyền. |
| K-10.05 | **Rebuild index và read model.** Projection version, quy trình rebuild và quy tắc checksum. | Dừng giữa rebuild/rollback schema; đối chiếu canonical record bất biến. | Không phục vụ index dựng dở hoặc làm mất history; fallback có giới hạn và đúng quyền. |

## K-11 — Phân tích thật, evidence và provider boundary

**Đầu vào:** Validated metric operands/evidence; public mock; giao diện module nghiên cứu do Bao sở hữu.

**Phụ thuộc/đầu nhận:** K-08 + K-09 + public analysis interface; Q-08/Q-09.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-11.01 | **Analysis mode thực tế.** Capability và runtime/version cho demo/local/provider. | Dùng public implementation hoặc interface Bao cung cấp; không sao chép private study. | UI không gọi deterministic mock là model diagnosis hoặc tự nhận phần nghiên cứu đã merge. |
| K-11.02 | **Domain cho metric observation.** Claim từ operand chính xác của snapshot đã validation. | Tái sử dụng scientific semantics, direction, decimal và giới hạn unknown. | Mỗi claim có đúng source, result tái lập được và giữ nguyên bất định về quan hệ nhân quả. |
| K-11.03 | **Membership của evidence và turn.** Binding citation/counterevidence và projection khi source bị ẩn. | Thử claim A/evidence B, turn cũ, result khác và source đã redacted. | API không tạo citation sai turn/scope; frontend có đủ metadata về quan hệ. |
| K-11.04 | **Adapter tài nguyên và quyền riêng tư của provider.** Payload tối thiểu, model/cost/retention/timeout và giới hạn schema. | Dùng test double cho lỗi; chỉ gọi endpoint trả phí thực tế khi có quyền cụ thể. | Không tự động gửi private source; output sai hoặc rate limit trở thành trạng thái thật, không giả success. |
| K-11.05 | **Khả năng tái lập theo phiên bản.** Pin input, analysis version, source identity và output đã chấp nhận. | Retry job không lẫn version/mode; export và runtime đồng nhất. | Mở lại cùng result nhận đúng output đã lưu; không ngầm chạy lại model hoặc đổi finding. |

## K-12 — Export, download và PDF tiếng Việt

**Đầu vào:** Result immutable, quyền hiện hành, JSON/Markdown/PDF và S/M/L.

**Phụ thuộc/đầu nhận:** K-04 + K-09 + K-11; result projection; Q-13.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-12.01 | **Export job và parity.** JSON/Markdown/PDF từ cùng result bất biến. | Render nền; kiểm schema, precision và citation có nội dung tương đương. | Byte của file và nội dung đọc được khớp màn hình/API; không thay input đã pin bằng snapshot mới nhất. |
| K-12.02 | **An toàn Unicode và render.** Chính sách font/renderer/network/resource cho PDF. | Thử tiếng Việt NFC/NFD, token dài, link/image độc hại và payload cỡ L. | PDF đọc và copy được; renderer không SSRF, thực thi nội dung project hoặc vượt giới hạn tài nguyên. |
| K-12.03 | **Quyền download và range.** Authenticated proxy hoặc URL ngắn hạn có scope. | Thử revoke/expiry/range/retry/filename/header/cache. | Không vượt quyền hoặc lộ secret trong signed URL; timeout/cancel có receipt đúng. |
| K-12.04 | **Phân phối file lớn.** TTFB, thời gian tới byte cuối, throughput và giới hạn cho client chậm. | Không giữ DB transaction trong lúc stream; chạy tải export/download nền. | Download chậm không chặn metadata read; completion được xác định theo byte thực tế và quyền. |

## K-13 — Delete, retention và không tái sinh dữ liệu

**Đầu vào:** Project/source/shared references; jobs/caches/exports; retention policy.

**Phụ thuộc/đầu nhận:** K-05 + K-06 + K-09; câu chữ về retention của Q-13.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-13.01 | **Chính sách delete và receipt.** Làm rõ scope, source, dữ liệu dùng chung và thời hạn giữ backup. | Lưu tombstone và delete operation bền vững trước khi báo đã nhận; purge async khi cần. | Phân biệt accepted với purged; người dùng biết dữ liệu nào còn được giữ và tới thời hạn nào. |
| K-13.02 | **Fencing cho delete race.** Các race delete × refresh/job/export/comment/source replace. | Kiểm tombstone/lease/revision trong transaction publish. | Writer cũ không làm project hoặc result đã xóa xuất hiện lại. |
| K-13.03 | **GC an toàn với reference.** Reachability/refcount/lease cho object dùng chung hoặc còn ở staging. | Xóa một project có object dùng chung; giữ trường hợp reader/job còn reference. | Không xóa nhầm source hoặc dữ liệu dùng chung; dọn orphan đúng grace period. |
| K-13.04 | **Lịch retention.** Cleanup theo expiry cho export/job/log và ngân sách capacity. | Chạy retention dưới tải, cùng với reader đang hoạt động. | Không dùng global lock để quét kho mỗi request hoặc xóa nguồn chưa hết retention. |
| K-13.05 | **Parity giữa restore và tombstone.** Recovery point chứa quyền và deletion history. | Restore rồi mở URL cũ, export và refresh tiếp bằng người dùng cũ hoặc đã bị revoke. | Không đọc lại dữ liệu đã xóa trái policy; audit trail đủ tra cứu và không chứa raw secret. |

## K-14 — Concurrency, tài nguyên và backpressure

**Đầu vào:** Measured CPU/I/O/payload costs; API/DB/worker/generator budgets.

**Phụ thuộc/đầu nhận:** K-02 + K-05 + K-09; số đo baseline.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-14.01 | **Ngân sách process và pool.** Số replica API/worker, DB connection và giới hạn memory/FD/payload. | Tính theo Little's Law và CPU từ giá trị trung bình đã đo; cộng đủ mọi replica/process. | Không dùng 50k DB connection hoặc thread/task không giới hạn; còn tài nguyên dự phòng cho admin/health. |
| K-14.02 | **Ranh giới tác vụ blocking.** Chuyển tác vụ CPU/hash/PDF/parse vào worker có giới hạn. | Profile event-loop lag, CPU, process memory; kiểm timeout/cancel của I/O. | Tác vụ nặng không làm tín hiệu liveness của event loop sai lệch hoặc chiếm hết API worker. |
| K-14.03 | **Admission và backpressure.** Giới hạn theo tenant/operation/job queue/thời gian. | Tạo overload tại API, DB pool, queue và object I/O. | Trả 429/503/Retry-After đúng khi ngoài envelope; không tính việc từ chối tải 50k bình thường thành kết quả latency tốt. |
| K-14.04 | **Cancel và dọn tài nguyên.** Client disconnect/deadline/job cancel theo đúng ngữ nghĩa. | Thử exception/cancel/timeout tại mọi chỗ đã lấy tài nguyên. | Trả connection/FD/temp/lease; không xóa job đã nhận chỉ vì browser mất mạng. |
| K-14.05 | **Fairness và burst.** Tình huống noisy tenant/hot row/reconnect/double-click. | Trộn kích thước job/read, trạng thái cold/warm và các burst lặp lại. | Tenant nền được phục vụ theo policy; không priority inversion hoặc khuếch đại retry. |

## K-15 — Job updates, polling/SSE và reconnect

**Đầu vào:** Job/event IDs; permission revision; browser fanout và reconnect behavior.

**Phụ thuộc/đầu nhận:** K-03 + K-04 + K-09; cơ chế cập nhật của Q-11.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-15.01 | **Chọn cơ chế cập nhật.** SSE hoặc status polling có giới hạn; giải thích lựa chọn fallback. | Đo active subscription/update/poll fanout thay vì chọn theo xu hướng. | Không gắn một DB connection với mỗi stream hoặc biến mọi người dùng thành poller chu kỳ 2 giây. |
| K-15.02 | **Event identity và resume.** Monotonic cursor, retention, gap/resync và dedup. | Thử mất mạng, event trùng và restart stream owner. | Không mất event hoặc tạo effect trùng; gap yêu cầu resync, không giả complete. |
| K-15.03 | **Quyền và scope của stream.** Session/member revision/expiry và binding với workspace. | Đổi tài khoản/role khi stream đang mở; replay cursor khác scope. | Revoke theo chính sách ≤5 giây; buffer/error không phát dữ liệu riêng tư sau khi mất quyền. |
| K-15.04 | **Ngân sách reconnect.** Backoff/jitter/heartbeat/buffer và giới hạn phía server. | Cho 30% client reconnect; thử tab background/foreground và proxy idle timeout. | Không rò connection hoặc tạo bão reconnect đồng bộ; trạng thái consumer nhất quán với job ledger. |

## K-16 — Quan sát, bảo mật và vận hành hằng ngày

**Đầu vào:** Threat model, error contract, release image/dependencies, action/request/job IDs.

**Phụ thuộc/đầu nhận:** K-02/J-01 từ đầu; mở rộng instrumentation theo từng lát chức năng.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-16.01 | **Observability theo hành trình.** Metric có cardinality thấp, trace ID an toàn và timer từng stage. | Liên kết action → API → job → DB/object → result; nêu rõ sampling. | Điều tra được một lỗi/retry mà không cần raw data bí mật. |
| K-16.02 | **Error và logging an toàn.** Public error code ổn định và structured log đã redacted. | Thử log injection, path/exception/source content; quét auth cookie và credential. | Không có raw exception/token/private source trong response, client bundle, log hoặc báo cáo. |
| K-16.03 | **Threat model và biện pháp kiểm soát.** Bao phủ auth/tenant/XSS/CSRF/upload/path/SSRF/throttle. | Gắn attack surface với kiểm thử; không chỉ chấm điểm security checklist. | Có bằng chứng từ chối đúng dự kiến; không còn lỗ hổng nghiêm trọng đã biết trên đường triển khai. |
| K-16.04 | **Hardening dependency và image.** Dependency được khóa phiên bản, SBOM/license, container non-root và secret injection. | Rebuild với cấu hình đã pin; phân loại finding của scanner theo khả năng tiếp cận thực tế. | Không lệch credential/image; có inventory image/config, không coi kết quả scanner đơn lẻ là pentest đầy đủ. |
| K-16.05 | **Alert và runbook.** Alert cho queue age/pool wait/error/saturation/burn cùng người phụ trách. | Diễn tập một overload/failure; kiểm detection → action → consumer recovery. | Alert dẫn tới hành động rõ ràng, không bùng cardinality; người còn lại dùng được runbook. |

## K-17 — Capacity, database và failure dưới tải

**Đầu vào:** Guide50k; seed; workloads/J-06; DB 40/F26 và operation38.

**Phụ thuộc/đầu nhận:** J-06 + smoke test chức năng/bảo mật + RC ổn định và quyền sử dụng tài nguyên.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-17.01 | **Adapter cho seed và import.** 50k principal, 200k snapshot và 2 triệu result, cùng payload vật lý cỡ S/M/L. | Dùng generator streaming và import thực tế; kiểm hash/count/scope/enum và cấp quyền. | Không coi việc sinh JSONL là benchmark DB; có manifest cho dữ liệu đa dạng và phân bố hot/cold. |
| K-17.02 | **Harness từ hành vi tới tải.** Script closed cohort và open-arrival trên API thực tế. | Actor/role/token không dùng chung admin; assert business action và theo dõi job tới terminal. | Dựng lại được các count engaged/starting/completing/waiting/idle của 50k người dùng, cùng count scheduled/drop/reject. |
| K-17.03 | **Đánh giá generator và tài nguyên.** Calibration 100 VU; xác định headroom cho NIC/CPU/RAM/FD và clock skew. | Đo chi phí theo VU/payload, chia ID không trùng giữa các máy; pin source/dependency. | Generator không trở thành nút thắt rồi bị quy lỗi cho server; target/staging đã được phép sử dụng. |
| K-17.04 | **Normal, stress, spike và soak.** Cùng RC/profile; 3 lần chạy normal closed/open, soak chính 2 giờ và phần kéo dài 8 giờ có lý do. | Giữ các mức 5k/10k/25k; kiểm stress breakpoint/recovery, không sửa profile sau khi thấy outcome để đạt pass. | SLO và job completion theo hướng dẫn; failed/partial/timeout không bị bỏ khỏi mẫu số. |
| K-17.05 | **Liên kết DB, fault và tải.** Kết quả DB 40/F26 cùng telemetry của pool/lock/query/queue/object/browser. | Tiêm lỗi trước/sau commit/ACK/lease/role; kiểm effect đã lưu bền tới consumer. | Không mất việc đã ACK, không stale commit hoặc phục vụ dữ liệu hỏng; báo riêng tier process và storage. |
| K-17.06 | **Báo cáo capacity và chi phí.** Envelope đã kiểm chứng, nút thắt, chi phí trên hành trình hoàn tất đúng và cách khắc phục. | Tổng hợp chi phí infra/DB/object/bandwidth/generator/IdP/provider nếu có. | Nếu chưa đạt 50k, giữ gate ở trạng thái mở; không ngoại suy từ 10k lên 50k hoặc coi test double là capacity của provider. |

## K-18 — Deploy, migration, backup/restore và bàn giao

**Đầu vào:** Release candidate, image/config/schema, persistent DB/objects/session/jobs, runbook.

**Phụ thuộc/đầu nhận:** K-05/K-09/K-13 + artifact J-07 và release candidate.

| Mã | Công việc và đầu ra | Cách làm/điểm chú ý | Điều kiện nhận |
|---|---|---|---|
| K-18.01 | **Release có thể tái dựng.** Image/build/schema/config/fixture và lệnh thao tác. | Dựng staging từ máy sạch; pin dependency và image. | Người còn lại dựng lại được mà không cần path hoặc secret cục bộ từ máy Kiên. |
| K-18.02 | **Khả năng tương thích khi migration.** Chiến lược expand-contract và cửa sổ tương thích của API/client cũ–mới. | Giữ traffic khi migrate; bước phá hủy dữ liệu có phương án recovery. | Không lock table kéo dài hoặc mất dữ liệu; rollback code không được coi là đảo ngược migration phá hủy dữ liệu. |
| K-18.03 | **Readiness, drain và rollout.** Startup/canary/graceful stop an toàn cho cả job đang chạy. | Kill/restart/upgrade khi queue và download đang hoạt động. | Không nhận việc trước ready hoặc bỏ job đã nhận; worker cũ bị fencing. |
| K-18.04 | **Backup, PITR và object.** Backup policy và recovery point cho key/quyền/job/tombstone. | Restore trên môi trường sạch; kiểm hash/nội dung và operation tiếp theo. | Mục tiêu storage DR là RPO ≤5 phút/RTO ≤30 phút, có bằng chứng; thông báo backup thành công chưa đủ. |
| K-18.05 | **Process recovery.** Mất API/worker khi storage vẫn được giữ. | Đo từ thời điểm tiêm lỗi tới khi consumer thực sự sử dụng đúng. | Đúng tier: RPO 0 cho effect đã ACK và RTO ≤60 giây; không tuyên bố chịu power loss hoặc hostile host khi chưa kiểm. |
| K-18.06 | **Bàn giao chéo giữa hai người.** Runbook, lỗi còn mở, giới hạn đã đo và báo cáo release chung. | Kiên dựng UI; Quân dựng API và restore; đối chiếu 27 gate cùng bằng chứng cá nhân/J. | Release dùng cùng SHA/schema; hoàn thành 90% đầu việc không bù cho lỗi dữ liệu/quyền hoặc gate 50k chưa đạt. |

Một bước có output nhưng chưa thực hiện check vẫn là implemented/chưa nghiệm thu. Nếu implementation choice không áp dụng, liên kết capability/giải pháp thay thế đã kiểm và lý do; không đánh dấu core journey hoàn tất bằng cách ghi N/A.
