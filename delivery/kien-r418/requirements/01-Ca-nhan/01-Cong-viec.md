# Công việc cá nhân — Kiên

Mỗi card là một đầu ra có thể nhận riêng. Các mã J trong card dẫn tới phần
phối hợp; phần cá nhân hoàn thành vẫn cần kiểm consumer để đóng journey chung.
Lịch D1–D12 tham chiếu ngày bắt đầu phase, không là deadline lịch cố định.
Tất cả task mới ở trạng thái **planned / not_run**; audit cũ không tự nhận chúng
đã triển khai. Bắt đầu theo dependencies thực tế, tích hợp theo vertical slice.

| Mã | Đầu ra | Cửa sổ làm | Phối hợp |
|---|---|---|---|
| K-01 | Nền mã, baseline và ranh giới local–hosted | D1 | J-01 |
| K-02 | Hosted API tối giản và hợp đồng phiên bản | D1–3 | J-01, J-03 |
| K-03 | Đăng nhập, session và vòng đời tài khoản | D1–4 | J-02 |
| K-04 | Workspace, phân quyền và cách ly dữ liệu | D2–5 | J-02, J-05 |
| K-05 | PostgreSQL, object store và integrity có giới hạn | D2–6 | J-03, J-08 |
| K-06 | Transactions, revision và idempotency | D3–6 | J-04, J-05 |
| K-07 | Upload, preview và nguồn nhập an toàn | D3–6 | J-03, J-04 |
| K-08 | Pipeline snapshot–comparison–result bất biến | D3–7 | J-03, J-05 |
| K-09 | Durable jobs, retry, cancel và fencing | D3–7 | J-04 |
| K-10 | Read API, pagination, search, graph và cache | D4–7 | J-03, J-05 |
| K-11 | Phân tích thật, evidence và provider boundary | D4–7 | J-03, J-05 |
| K-12 | Export, download và PDF tiếng Việt | D5–8 | J-05 |
| K-13 | Delete, retention và không tái sinh dữ liệu | D5–8 | J-04, J-05, J-08 |
| K-14 | Concurrency, tài nguyên và backpressure | D4–9 | J-06, J-07 |
| K-15 | Job updates, polling/SSE và reconnect | D5–8 | J-02, J-04, J-07 |
| K-16 | Quan sát, bảo mật và vận hành hằng ngày | D3–10 | J-07, J-08 |
| K-17 | Capacity, database và failure dưới tải | D6–11 | J-06, J-07 |
| K-18 | Deploy, migration, backup/restore và bàn giao | D8–12 | J-08 |

## K-01 — Nền mã, baseline và ranh giới local–hosted

**Đầu vào:** main mới nhất; hai nhánh cũ; audit hiện tại; overlay nghiên cứu của Bao.

**Đầu ra:** Một bản inventory module/operation, release SHA và baseline có thể dựng lại; kế hoạch tích hợp giữ toàn bộ công việc cũ.

**Phương pháp và điểm chú ý:** Đọc diff trước khi lấy main làm nền; giữ nhánh cũ và WIP. Chạy luồng synthetic trước tối ưu, ghi phân biệt public main 9 RPC với overlay10 RPC; giữ commit nghiên cứu chưa merge ngoài nhánh sản phẩm.

**Nghiệm thu cá nhân:** Dựng được bản nền, lưu đủ source/build/config; không mất diff người khác, không nhận code chưa merge là capability đã phát hành.

**Phối hợp/đầu nhận:** J-01; bàn giao cho Quân capabilities thật và lỗi/giới hạn đã quan sát.

**Thứ tự:** D1; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-02 — Hosted API tối giản và hợp đồng phiên bản

**Đầu vào:** Domain P3/P6 hiện có; local bridge; contract P01–P38.

**Đầu ra:** Hosted API có schema kiểm đầu vào/đầu ra, readiness, capabilities và lớp repository phù hợp; local bridge vẫn có biên riêng.

**Phương pháp và điểm chú ý:** Ưu tiên modular monolith/API+worker cùng codebase. Tái dùng invariant hiện có qua adapter. Không mở HTTPServer loopback ra Internet hoặc chỉ thay bằng ThreadingHTTPServer. Tách I/O bất đồng bộ với CPU/PDF ở worker; không thêm Kafka/Kubernetes/microservices theo mặc định.

**Nghiệm thu cá nhân:** Schema/error/version được kiểm ở biên; mọi core operation có implementation+test. Conditional operations có disposition/capability=false và đường thay thế đã kiểm cho cùng nhu cầu; không bỏ core journey hoặc bật stub. P01–P38 là inventory nền, thêm routes auth/callback/onboarding khi contract thực cần.

**Phối hợp/đầu nhận:** J-01, J-03; Quân tạo consumer cùng contract, bắt đầu bằng một vertical slice thật.

**Thứ tự:** D1–3; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-03 — Đăng nhập, session và vòng đời tài khoản

**Đầu vào:** IdP/OIDC được duy trì hoặc thư viện framework chuẩn; HTTPS và policy session.

**Đầu ra:** Login/callback/logout/session expiry, refresh hoặc renewal, đổi tài khoản và thu hồi session có hành vi thống nhất; cấp tài khoản/khôi phục qua IdP có quy trình thật.

**Phương pháp và điểm chú ý:** Dùng thư viện xác thực được duy trì, kiểm issuer/audience/state/nonce/PKCE theo flow; cookie Secure/HttpOnly/SameSite và chống CSRF theo framework. Rotate session, idle/absolute expiry; không lưu bearer secret dài hạn trong localStorage. Thử IdP staging thuộc đội, không blast IdP công khai.

**Nghiệm thu cá nhân:** Callback sai/replay/expired bị từ chối; logout vô hiệu hóa server session; API 401 không biến thành dữ liệu demo; có test session fixation, CSRF, refresh race và thông tin đăng nhập không nằm trong log.

**Phối hợp/đầu nhận:** J-02; Quân nghiệm thu UI timeout, nhiều tab, đăng nhập lại và back/forward sau logout.

**Thứ tự:** D1–4; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-04 — Workspace, phân quyền và cách ly dữ liệu

**Đầu vào:** Ma trận quyền trong contract; actor từ session; toàn bộ object/job/export/event.

**Đầu ra:** Membership/role/ownership được kiểm server-side trên mọi đường; workspace onboarding/first-owner có cách provision thật, last-owner và policy revision có invariant rõ.

**Phương pháp và điểm chú ý:** Deny mặc định; kiểm project→snapshot→result→evidence và parent chain. Không tin role/workspace do browser tự khai. Nếu dùng RLS, kiểm cả pooled connection reuse và quyền bypass. Recheck quyền với job publish/download/event; cache không giữ grant cũ.

**Nghiệm thu cá nhân:** Ma trận role×action có ca cho phép và từ chối; tráo ID/paging/search/job/event/download không lộ dữ liệu hoặc tên tài nguyên. Revoke được phản ánh trong tối đa5s theo policy dự án; không hứa thu hồi byte người dùng đã tải.

**Phối hợp/đầu nhận:** J-02, J-05; Kiên giữ chính sách, Quân giữ trạng thái UI nhất quán với phiên bản quyền.

**Thứ tự:** D2–5; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-05 — PostgreSQL, object store và integrity có giới hạn

**Đầu vào:** SQLite/domain identities hiện có; seed 50k; DB 40 cases.

**Đầu ra:** Schema/migration/index/query map, repository scoped và immutable object publication; migration dữ liệu cũ kiểm được.

**Phương pháp và điểm chú ý:** Không quét/hash toàn catalog cho mỗi click. Verify đúng object khi dùng, đối chiếu index/version và background scrub có policy. Metadata transaction không giả atomic với object storage: stage→verify→reference, có orphan reconciliation/grace/GC. Query plan đo trên dữ liệu đủ lớn.

**Nghiệm thu cá nhân:** DB 40 có kết quả và evidence; corruption được phát hiện, reference không trỏ object chưa ready, failed migration giữ bản cũ/rollback path. Hai facade giữ cùng source of truth, không tạo hai store lệch nhau.

**Phối hợp/đầu nhận:** J-03, J-08; Quân kiểm list/result/history sau migrate và restore.

**Thứ tự:** D2–6; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-06 — Transactions, revision và idempotency

**Đầu vào:** Mutation/job requests; expected_revision; hai writer/race schedules.

**Đầu ra:** Một effect cho retry hợp lệ, conflict rõ với stale revision hoặc cùng key khác body; không lost update.

**Phương pháp và điểm chú ý:** Scope key theo actor/workspace/operation/key; lưu body hash làm giá trị đối chiếu, không đưa nó vào unique key để tránh conflict. Dùng conditional write/CAS/locks/constraints trong transaction ngắn. Không giữ transaction khi parse file, gọi provider hoặc render PDF.

**Nghiệm thu cá nhân:** Lost response rồi retry trả cùng receipt/effect; same key khác body conflict; concurrent refresh/review/delete không ghi đè hoặc mất lịch sử; deadlock/serialization được xử lý bounded.

**Phối hợp/đầu nhận:** J-04, J-05; Quân gửi đúng revision/key và xử lý409 mà không làm mất draft.

**Thứ tự:** D3–6; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-07 — Upload, preview và nguồn nhập an toàn

**Đầu vào:** Wizard/mapping Quân; giới hạn S/M/L; local root grant và hosted upload.

**Đầu ra:** Hosted staging/upload resumable có quota, source identity, preview lease và confirm revalidation; không đọc arbitrary server path.

**Phương pháp và điểm chú ý:** Kiểm kích thước trước/sau giải nén, file count/depth/path/symlink, duplicate chunks, content type theo nội dung; file nhập là dữ liệu trơ. Connector chỉ bật nếu có allowlist/SSRF defense và credential boundary. Giữ Unicode NFC/NFD và nhiều run.

**Nghiệm thu cá nhân:** Retry chunk không nhân dữ liệu; source đổi sau preview bị từ chối an toàn; hết lease/quota/cancel không tạo project nửa vời. Import thất bại không mất snapshot trước hoặc xóa nguồn gốc.

**Phối hợp/đầu nhận:** J-03, J-04; Quân kiểm progress, resume và mapping từ chính preview phiên bản đó.

**Thứ tự:** D3–6; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-08 — Pipeline snapshot–comparison–result bất biến

**Đầu vào:** P3 mapping/baseline/threshold; source manifest; fixture edge cases.

**Đầu ra:** Confirm/refresh/analysis tạo đúng generation; no-op giữ snapshot; result/history gắn đúng input revision.

**Phương pháp và điểm chú ý:** Giữ canonical identities và decimal/precision rules. Snapshot/bundle/comparison/current pointer publish cùng transaction. Mọi result phải pin project/snapshot/parent/analysis version và evidence source hash; query của generation cũ vẫn đọc đúng.

**Nghiệm thu cá nhân:** Hơn3 runs, baseline/target/class/unit, zero/negative/tiny values và adverse threshold đúng; mutation không sửa result cũ; concurrent publish có outcome xác định. Causal status không được tự nâng từ metric delta.

**Phối hợp/đầu nhận:** J-03, J-05; đối chiếu UI, API và exported report cùng một result.

**Thứ tự:** D3–7; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-09 — Durable jobs, retry, cancel và fencing

**Đầu vào:** Job contract, idempotency, deadline/quota, worker lifecycle.

**Đầu ra:** Job nhận bền trước202, lease/heartbeat/epoch, retry bounded, cancel và terminal states truy được.

**Phương pháp và điểm chú ý:** Có thể dùng queue PG hoặc thư viện bền phù hợp; thêm broker khi có nhu cầu đo được. Job và publication có fence, tombstone và quyền hiện hành; submitter revoke theo policy đã chốt. SKIP LOCKED chỉ dùng đúng queue claim. Không hứa exactly-once delivery; bảo đảm một business effect qua idempotency/fence.

**Nghiệm thu cá nhân:** Kill sau ACK không mất job; stale worker không commit; cancel_requested khác cancelled; success đã commit có thể thắng cancel và UI nhận đúng outcome. Mọi job đã nhận có terminal hoặc còn trong deadline rõ.

**Phối hợp/đầu nhận:** J-04; cùng Quân kiểm mất mạng, reload, retry, cancel và delete trong lúc chạy.

**Thứ tự:** D3–7; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-10 — Read API, pagination, search, graph và cache

**Đầu vào:** Catalog200k snapshot/2m result; per-object authorization; consumer page sizes.

**Đầu ra:** Đường đọc bounded; cursor gắn scope/query/revision; subgraph có continuation; cache có policy quyền/version.

**Phương pháp và điểm chú ý:** Không load toàn ProductView rồi mới cắt trong memory. Thiết kế query theo access path, kiểm N+1/index/pool waits. Cache chỉ khi profiling chỉ ra giá trị; không thêm Redis bắt buộc. Revoke/delete/current pointer làm invalidation đúng.

**Nghiệm thu cá nhân:** List/search/evidence/graph đọc đủ dữ liệu theo contract; totals chính xác hoặc nói rõ estimate. Hot/cold/large tenant không lộ chéo hoặc làm scan toàn kho; stale cursor/query conflict có lỗi rõ.

**Phối hợp/đầu nhận:** J-03, J-05; Quân kiểm scroll/filter/deep link và evidence membership theo cursor đúng.

**Thứ tự:** D4–7; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-11 — Phân tích thật, evidence và provider boundary

**Đầu vào:** Validated metric operands/evidence; public mock; giao diện module nghiên cứu do Bao sở hữu.

**Đầu ra:** Analysis job trả quan sát có nguồn và runtime metadata thật; mock/local metrics/provider tách capability và kết quả.

**Phương pháp và điểm chú ý:** Tái dùng logic/commit nghiên cứu được Bao cho tích hợp; không chép dữ liệu nội bộ hoặc viết đè overlay. Chưa có module thì thống nhất interface và làm local observation từ validated snapshot; không giả diagnosis model. Provider adapter có allowlist/minimized payload/quota/cost/cancel/deadline, chỉ bật khi được cấp quyền.

**Nghiệm thu cá nhân:** Claim trích đúng operands và source; citation thuộc chính claim/turn; không suy cause từ metric đổi. Provider timeout/rate-limit/invalid output không thành success; test-double throughput không nhận là real-model throughput.

**Phối hợp/đầu nhận:** J-03, J-05; Quân hiển thị loại analysis, nguồn và uncertainty bằng ngôn ngữ người dùng.

**Thứ tự:** D4–7; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-12 — Export, download và PDF tiếng Việt

**Đầu vào:** Result immutable, quyền hiện hành, JSON/Markdown/PDF và S/M/L.

**Đầu ra:** Export job/file có parity, streaming hoặc chunk hợp lý, MIME/filename/length/hash rõ và download có quyền.

**Phương pháp và điểm chú ý:** Không dựng PDF nặng trong request path; renderer hạn chế tài nguyên/mạng. Unicode/font nhúng phù hợp, CSV formula nếu hỗ trợ định dạng đó phải được xử lý. Proxy hoặc URL ngắn hạn phải phù hợp revoke policy; range/retry không bypass quyền.

**Nghiệm thu cá nhân:** Report đúng result/snapshot/claim/citation và precision; PDF tiếng Việt đọc/copy được. Token URL/log không lộ; file hết quyền/hết hạn bị chặn; export lớn không bóp nghẹt reads.

**Phối hợp/đầu nhận:** J-05; Quân tải và mở file qua browser, kiểm màn đang đọc và report cùng nội dung.

**Thứ tự:** D5–8; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-13 — Delete, retention và không tái sinh dữ liệu

**Đầu vào:** Project/source/shared references; jobs/caches/exports; retention policy.

**Đầu ra:** Delete receipt và tombstone/fence; purge/GC có deadline; phân biệt xóa sản phẩm, nguồn gốc và backup retention.

**Phương pháp và điểm chú ý:** Tombstone chặn writer cũ; xóa metadata/object theo references, không phá shared object. Reconcile staged objects sau grace. Restore không resurrect tenant/project bị xóa mà thiếu tombstone/redaction history; phát hành policy giữ backup rõ.

**Nghiệm thu cá nhân:** Delete×refresh/job/export/download race giữ quyền/toàn vẹn; không trả success purge khi mới nhận job. Không xóa thư mục nguồn của người dùng. Sau restore, user không đọc lại project đã xóa trái policy.

**Phối hợp/đầu nhận:** J-04, J-05, J-08; Quân kiểm confirmation, receipt, background cleanup và kết quả cũ không hiện lại.

**Thứ tự:** D5–8; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-14 — Concurrency, tài nguyên và backpressure

**Đầu vào:** Measured CPU/I/O/payload costs; API/DB/worker/generator budgets.

**Đầu ra:** Bounded pools/queues/payloads/FDs/timeouts; admission/fairness theo workspace và operation; overload có outcome rõ.

**Phương pháp và điểm chú ý:** Tính tổng connection qua mọi replica/process/worker; tách CPU khỏi async event loop. Không một DB connection hoặc heavy thread/người dùng. Đo cold/warm RSS, queue age và noisy tenant; đặt limit từ số đo, không tăng workers vô hạn.

**Nghiệm thu cá nhân:** Reads/health không bị worker job storm làm chết; overload429/503/Retry-After đúng, jobs đã ACK không mất. Trong normal50k vẫn đạt SLO/success, không dùng reject để làm latency đẹp.

**Phối hợp/đầu nhận:** J-06, J-07; Quân kiểm UI waiting/busy/retry và không tạo request storm.

**Thứ tự:** D4–9; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-15 — Job updates, polling/SSE và reconnect

**Đầu vào:** Job/event IDs; permission revision; browser fanout và reconnect behavior.

**Đầu ra:** Status/event feed có ordering/cursor/resume bounded, polling fallback có backoff/jitter/dedup và quyền đúng.

**Phương pháp và điểm chú ý:** Chọn SSE hay polling từ nhu cầu đo được; SSE không bắt buộc mọi nơi. Không connection DB suốt lifetime stream. Resume quá retention trả resync; mỗi tab/account/workspace có scope và cancellation. Revoke ngắt hoặc từ chối trong policy tối đa5s.

**Nghiệm thu cá nhân:** Reconnect không tạo duplicate job/event hoặc bão đồng bộ; expired stream không phát dữ liệu cũ; pending jobs không bị đếm là completed. Có số liệu subscriptions/poll RPS/heartbeat/buffer.

**Phối hợp/đầu nhận:** J-02, J-04, J-07; Quân bảo đảm một subscription cần thiết và dispose khi đổi scope.

**Thứ tự:** D5–8; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-16 — Quan sát, bảo mật và vận hành hằng ngày

**Đầu vào:** Threat model, error contract, release image/dependencies, action/request/job IDs.

**Đầu ra:** Logs/metrics/traces có correlation, alerts và runbook; security controls trên HTTPS/headers/errors/abuse; dữ liệu quan sát được tối thiểu hóa.

**Phương pháp và điểm chú ý:** Theo dõi golden signals + queue/pool/lock/GC/tenant fairness; không log token, raw source, question có dữ liệu riêng hoặc evaluator truth. Kiểm dependencies/license/SBOM/image/non-root/secret injection. Health không chỉ200 khi DB/queue không nhận được việc.

**Nghiệm thu cá nhân:** Điều tra một journey hỏng tới đúng version/worker/object được; log injection và error paths không leak; alert saturation/recovery có diễn tập. Monitoring không tự gây cardinality/IO không giới hạn.

**Phối hợp/đầu nhận:** J-07, J-08; Quân có client error/UX telemetry cùng correlation nhưng không gửi nội dung nhạy cảm.

**Thứ tự:** D3–10; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-17 — Capacity, database và failure dưới tải

**Đầu vào:** Guide50k; seed; workloads/J-06; DB 40/F26 và operation38.

**Đầu ra:** Harness và kết quả load/open-arrival/stress/spike/soak/fault có mẫu số đúng, DB metrics/query plans và cost theo journey/job.

**Phương pháp và điểm chú ý:** Baseline trước tối ưu; kiểm generator headroom; cùng release/dataset; 50k unique active và arrivals kiểm riêng. Không chỉ bắn GET hoặc health, không bỏ write/export/auth. Theo dõi job tới terminal, DB locks/pool, orphan/object/cache/recovery; lưu failed/partial runs.

**Nghiệm thu cá nhân:** Đủ các bài và SLO trong guide; DB 40/F26 không còn not_run nếu claim đã kiểm. Không đạt50k thì gate chưa đạt với bottleneck/correction plan, không đổi định nghĩa tải hoặc nới correctness.

**Phối hợp/đầu nhận:** J-06, J-07; Kiên chủ trì server/DB/generator, Quân đo browser/journey dưới cùng tải.

**Thứ tự:** D6–11; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.

## K-18 — Deploy, migration, backup/restore và bàn giao

**Đầu vào:** Release candidate, image/config/schema, persistent DB/objects/session/jobs, runbook.

**Đầu ra:** Topology tái lập, rolling/drain/rollback có chiến lược schema; backup/PITR+objects+permissions/jobs được restore thật và đo RPO/RTO.

**Phương pháp và điểm chú ý:** Không cần K8s: deployment nhỏ minh bạch, health/readiness/graceful stop, expand-contract migration theo compatibility. Không giả reverse migration khi schema phá hủy; dùng forward-fix/backup restore đã kiểm. Quản lý config/secrets, rollback asset/API versions và cleanup.

**Nghiệm thu cá nhân:** Máy sạch đi hết journey; upgrade dưới traffic không mất job đã ACK; process-loss retained-storage RPO0/RTO≤60s, còn storage/host loss phải có mục tiêu và bài riêng. Restore kiểm nội dung/quyền tới consumer; Quân tự dựng lại bằng docs.

**Phối hợp/đầu nhận:** J-08; cả hai ký cùng release evidence, cost, known limits và giao source/runbook.

**Thứ tự:** D8–12; contract/scope cần cho card phải có trước khi triển khai
consumer. Nếu bị chặn, giữ task blocked với artifact/owner cần bàn giao; tiếp
phần không phụ thuộc. Không bỏ card chỉ vì vượt thời gian dự kiến.
