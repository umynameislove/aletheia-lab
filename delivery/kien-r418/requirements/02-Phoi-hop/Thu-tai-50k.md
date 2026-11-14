# Thử tải 50.000 người dùng hoạt động

**Mục tiêu của phase 12 ngày:** hoàn thiện và kiểm sản phẩm tích hợp, hướng tới 50.000 người dùng hoạt động đồng thời trên một cấu hình được công bố.
Đây là hướng dẫn thực hiện và nghiệm thu, **chưa có kết quả chạy tải**. Các ngưỡng SLO, thời lượng và cỡ dữ liệu bên dưới là mục tiêu của dự án; nguồn kỹ thuật giải thích phương pháp, không xác nhận Aletheia đã đạt.
Website giới thiệu dành cho phase sau. Không đổi phạm vi thành chỉ dựng demo/build, không bỏ auth, database, jobs hoặc kiểm tính đúng để lấy con số tải đẹp.

## 1. Chốt đầu vào từ ngày 1

- Ghi release SHA, frontend build, API/schema version, migrations, image digest, cấu hình và chế độ phân tích sẽ kiểm. Hai gói Kiên–Quân dùng cùng bản contract và workload.
- Kiểm quyền sử dụng staging, load generators, database, object storage, IdP và provider; lập ngân sách CPU/RAM/disk/IOPS/network, thời gian và chi phí trước khi đặt lịch chạy lớn.
- Tài liệu này không cấp quyền thuê hạ tầng, gọi API trả phí hoặc tạo hóa đơn. Chưa được cấp tài nguyên thì vẫn hoàn thiện implementation/fixtures/kiểm nhỏ và báo gate 50k chưa đạt.
- Chọn địa chỉ đích thuộc đội quản lý, có allowlist và giới hạn thời lượng/tải. Không chạy vào production, GitHub, IdP hoặc provider của bên khác khi chưa được cho phép đúng phạm vi.
- Ghi API/worker replicas, processes, connection pools, queue limits, database/storage versions, durability, cache, TLS/proxy, vùng mạng và cấu hình máy phát tải.
- Ghi toàn bộ thao tác đã có/chưa có theo capabilities; mock, local analysis và real provider phải phân biệt. Không dùng UI giả available hoặc response dựng sẵn thay chức năng cần nghiệm thu.
- Giữ research overlay riêng với public release. Không lấy private research, bài chấm, evaluator-only truth hoặc credential làm fixture sản phẩm.
- Khóa release và initial dataset cho một đợt so sánh; mỗi run dùng bản clone/reset có manifest và namespace riêng, không để cache/job/result sinh từ run trước âm thầm làm run sau nhẹ hơn.
- Nếu thay code/schema/config/durability hoặc workload, ghi phiên bản mới và chạy lại phần bằng chứng bị ảnh hưởng; không ghép các run khác release thành ba lần lặp của một cấu hình.

## 2. “50k active” nghĩa là gì?

**Primary target là 50.000 unique authenticated principals tham gia hành trình đúng quyền**, không phải 50.000 tài khoản/tab/socket rỗng hoặc VUs được cấu hình.
Chốt cohort/profile trước run. Một principal được tính là engaged trong cửa sổ trượt 30 giây khi có business action có ý nghĩa được lên lịch trong cửa sổ đó, hoặc đang chờ một job đã được nhận và còn trong deadline/age bound đã khai báo.
Báo riêng intended-engaged, actually-started, unique-completing, valid-waiting và idle; không lấy số người hoàn tất thành công để định nghĩa quần thể rồi loại người gặp lỗi khỏi mẫu số.
Failure/drop/timeout không làm principal hoặc action biến khỏi offered cohort hay mẫu số SLO. Nếu máy phát không tạo đủ nhu cầu 50k thì chưa chứng minh target dù server phản hồi nhanh.
Think-time/cadence và tỷ lệ waiting phải khóa theo profile; không kéo window hoặc cộng người chờ vô hạn sau khi xem số để đủ 50k. Job L 120s có thể được tính là valid-waiting, nhưng chưa phải completed action.
Một người mở nhiều tab chỉ tính một principal. Health/metrics/auth refresh/retry loop không là business action thành công; status polls không tự tạo thêm active users.
Business actions gồm mở project/result, xem claim/citation, tìm/filter, import/refresh, hỏi tiếp, export, review/cancel/delete đúng quyền; chỉ tính thành công khi assertions về nội dung/trạng thái và deadline đều đạt.
Load fixture gắn actor → workspace/role/object thật; không dùng một admin/token chung cho mọi người. Có thể đăng nhập trước cho bài steady, nhưng session middleware vẫn phải kiểm thật.
Login/refresh/revoke storm có bài riêng trên test IdP được phép; giãn thời điểm bắt đầu và think time theo seed/phân phối đã ghi; không đồng bộ 50k lần bấm rồi gọi đó là hành vi người dùng bình thường.

## 3. Dùng cả hai mô hình tải

**Closed sessions:** giữ tập principal và hành trình có think time để kiểm concurrency, trạng thái người dùng, nhiều tab, chờ/cancel/reconnect và active coverage.
Closed throughput có thể tự giảm khi server chậm; vì vậy số VU giữ nguyên không chứng minh hệ thống vẫn nhận đủ nhu cầu. [k6: open và closed models](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)
**Open arrivals:** lên lịch business actions độc lập thời gian hoàn tất của action trước, giữ arrival rate theo workload đã chốt và tính cả lần không gửi/không hoàn tất.
Không dùng closed test thay open test, không dùng open RPS thay bằng chứng 50k unique active. Hai bài trả lời hai câu hỏi khác nhau.
Ghi các số scheduled → started/sent → admitted → completed/terminal, cùng dropped, rejected, timeout, interrupted và pending lúc drain; phải reconcile được chênh lệch.
Một business action có thể tạo nhiều HTTP requests, một job chạy nhiều giây hoặc nhiều lần poll; ghi waterfall thực để không nhầm actions/s, RPS, jobs/s và số việc đang chạy.

## 4. Workload xuất phát và cách hiệu chỉnh

Các con số sau là giả định thiết kế kế thừa tỷ trọng gói trước, tăng primary từ 10k lên 50k; **chưa phải hành vi đã quan sát hoặc số đo capacity**.

| Thành phần | Mức xuất phát để lập ngân sách |
|---|---|
| 50k người, khoảng một business action mỗi 10 giây | 5.000 actions/s; với closed test, chu kỳ phải gồm response + think time |
| Tạm 3 HTTP requests/action, đã tính fanout/status | Khoảng 15.000 RPS; thay bằng waterfall đo được trước run chính |
| HTTP mix tham chiếu | 13.000 reads + 1.080 light mutations + 900 status/event reads + 20 heavy submissions mỗi giây |
| Heavy submissions | 15 analysis + 3 import/refresh + 2 export mỗi giây; giữ thêm profile job-heavy riêng |
| 50k client cùng poll mỗi 2 giây | Riêng polling đã là 25.000 RPS, không vừa budget 900 status/s |
| 20 jobs/s, mean lifetime 20 giây | Khoảng 400 jobs đang chờ/chạy; nếu một waiter/job poll 2 giây thì khoảng 200 RPS |

Quân chủ trì quan sát nhiệm vụ của người mới, analyst, reviewer và admin; Kiên đo action → HTTP → DB/job để cập nhật workload.
Ghi tasks, vai trò, thiết bị/mạng, think time, quay lại đọc, filter/search, double click, nhiều tab, bỏ dở, waiting và reconnect. Dùng dữ liệu giả và có đồng ý nếu ghi hình.
Nhóm 6–10 người phù hợp có ích để tìm vấn đề UX, không đại diện cho quần thể 50k. Không tự giao thêm việc cho người đang chấm corpus.
Chưa có quan sát thì ghi profile là giả định; giữ profile tham chiếu và mọi kết quả, bổ sung profile mới trước run thay vì bỏ phần bất lợi sau khi xem số.
Báo riêng read-heavy/job-heavy và tenant lạnh/nóng để không lấy trung bình che bottleneck. Polling/reconnect đã nằm trong hệ số requests/action thì không cộng lần hai.

Trước run, xuất danh sách journeys với tỷ trọng, actor/role, input size, transition/think-time distribution, HTTP fanout, expected output và deadline của từng action.
Một action “xem claim” chỉ thành công khi đúng claim, đúng result/snapshot và mở được các citation được chọn; HTTP 200 hoặc trang skeleton chưa đủ.
Một action “submit analysis” đo ACK riêng, còn job phải được theo tới terminal/result dùng được; cancel, expiry và provider failure có trạng thái đúng, không fake success.
Đo chi phí fanout từ browser thật: polling, token refresh, SSE fallback hoặc nhiều tab có thể nhân tải dù business action rate không đổi.

## 5. Dữ liệu phải đủ lớn và đủ khác nhau

- Có 50k synthetic principals riêng; giữ tối thiểu 2k workspaces, 10k projects, 20 snapshots/project và 10 results/snapshot: 200k snapshots, 2 triệu results như yêu cầu catalog trước.
- Có workspace dùng chung ít nhất 50 thành viên, nhóm nóng 500 thành viên, project riêng và project cộng tác; một người có thể thuộc nhiều workspace nhưng chỉ đếm một principal.
- Ít nhất một result có 10k claims và một graph có 10k nodes; kiểm truy cập đủ qua pagination/subgraph, không tải toàn bộ rồi cắt ở browser hoặc âm thầm truncate.
- Có tiếng Việt NFC/NFD, tên dài, hơn ba run, nhiều citation/counterevidence, thiếu class/unit/threshold, giá trị 0/âm/rất nhỏ và dữ liệu cần quyền khác nhau.
- Fixture manifest ghi seed, row/object/index counts, physical bytes, entropy/compression/reuse/dedup, generations, phân phối thường/lệch và phần cold/warm cache.
- Seed payload vật lý S/M/L, không chỉ hàng triệu row trỏ tới cùng một object đã cache; phân biệt shared/hot payload với unique bytes cho bài I/O/network.
- Không cần đổ mọi payload vào RAM cùng lúc hoặc nhân mọi byte ×5 chỉ vì users ×5. Dataset nhỏ chỉ dùng debug, không thay dataset chính khi công bố 50k.

| Cỡ | Dữ liệu mỗi tác vụ | Yêu cầu giữ |
|---|---|---|
| S | ≤1 MiB, 1.000 dòng metric, 100 claim/evidence | Đủ nội dung/quyền; bài nền nhanh |
| M | ≤20 MiB, 10.000 dòng, 1.000 claim/evidence | Hành trình chính, phân trang và jobs |
| L | ≤200 MiB, 100.000 dòng, 10.000 claim/evidence, graph 10k nodes | Upload stream/chunk, quota/staging; không trả toàn payload về browser |

Mix tham chiếu 60% S / 35% M / 5% L. Byte size, số dòng và số claims là các trục riêng; không coi một số MiB tương đương cố định một số records.
Giữ giới hạn local bridge; hosted upload lớn phải có cơ chế riêng. Archive cần cap cả sau giải nén, file count/depth/path/symlink và xử lý an toàn. [OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)

## 6. Sizing theo cơ chế, không theo tên framework

Trong cùng hệ và khoảng đo ổn định, Little’s law dùng **mean**: `in-flight = arrival rate × mean time in system`; không dùng p99 hoặc tổng sessions thay mean concurrency. [John Little, 2011, §1–2](https://projectproduction.org/journal/reprint-littles-law-as-viewed-on-its-50th-anniversary/)

| Worksheet phải điền bằng số đo | Cách dùng |
|---|---|
| HTTP arrivals | `active × actions/user/second × observed requests/action + extra polling/reconnect`, không tính đôi |
| HTTP in-flight | `Σ route arrivals × measured mean route duration`; 15k RPS × 0,2 s chỉ là ví dụ 3.000 HTTP in-flight |
| Polling | `observed waiters / poll interval`; SSE vẫn có connection/event/heartbeat/reconnect budgets |
| Job CPU | `Σ jobs/s × measured CPU-seconds/job / target utilization`; I/O wait không đồng nghĩa CPU core |
| DB connections | Tổng pool của mọi API replica/process + workers + migration/health/admin reserve; không một connection/người dùng |
| Memory | Process replication + concurrent payload/jobs + connections + cache + buffers; đo peak và steady drift |
| Network/storage | Actual request/response/upload/download bytes, object operations, DB/WAL/index writes, IOPS và retention |

Ví dụ response trung bình 20 KiB ở 15k RPS đã khoảng 307 MB/s ≈2,46 Gbps trước TLS/request/upload; đây là minh họa ngân sách, không số đo của sản phẩm.
Ở nhịp giả định trên, primary soak 2 giờ có khoảng 36 triệu actions/108 triệu HTTP/144.000 heavy submissions. Extension 8 giờ có khoảng 144 triệu actions/432 triệu HTTP/576.000 heavy submissions; dự trù dữ liệu sinh thêm, cleanup và quan sát, không chỉ số VUs.
Các con số 108.000 analysis (primary 2h) hoặc 432.000 analysis (extension 8h) không phải số real-provider calls được cho phép. Chế độ thực thi và ngân sách provider phải được giới hạn riêng ngay từ ngày 1.
Giữ transactions ngắn; không giữ connection khi chờ provider, parse/hash file hoặc render PDF. Tăng PG max_connections cũng tăng tài nguyên, không chữa query chậm. [PostgreSQL connection settings](https://www.postgresql.org/docs/current/runtime-config-connection.html)
Đo query count/plan/actual rows/buffers, lock/pool waits, hot rows, queue age và catalog growth. `EXPLAIN ANALYZE` thực chạy statement; write probes chỉ dùng trong staging có kiểm soát. [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html)
Chi phí gồm app/worker/DB/object/bandwidth/IdP/observability/generator/provider; báo cost mỗi journey/job hoàn tất đúng, không tính trên mỗi 202. Tách estimate và tiền thực đã được cho phép chi.

## 7. Máy phát tải cũng phải được kiểm

- Tách máy/process phát tải khỏi SUT; ghi OS/runtime/tool version, CPU/RAM/NIC/FD/port limits, clock và RTT; chia principal/seed rõ giữa các generators.
- Chạy calibration 100 VUs và payload thật để đo memory/CPU/network trước khi dự trù 50k; file upload hoặc script state lớn có thể làm VU rất tốn RAM.
- Theo dõi headroom; hướng dẫn k6 đề nghị khoảng 20% CPU idle, không để generator đạt trần rồi quy latency/drop cho server. [k6 Running large tests](https://grafana.com/docs/k6/latest/testing-guides/running-large-tests/)
- Nếu thiếu headroom, tối ưu harness hoặc phân tải qua nhiều generators rồi kiểm lại; không khẳng định một máy chắc chắn sinh được 50k users từ benchmark của công cụ.
- Giữ `dropped_iterations`: có thể do thiếu VUs hoặc SUT chậm; phân nguyên nhân, không xóa scheduled load chưa gửi khỏi báo cáo. [k6 Dropped iterations](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/dropped-iterations/)
- Protocol load tạo tổng nhu cầu; một cohort browser trên thiết bị/network đã ghi chạy journeys dưới cùng tải để kiểm render/UX. Báo actual browser count, không gọi đó là 50k full browsers.
- Không vô hiệu hóa auth, tenant checks, integrity/hash, durable commit hoặc effects để tạo RPS. Test IdP/provider phải ghi rõ; không blast IdP ngoài hoặc dùng fake provider để nhận real-provider capacity.

## 8. Ma trận thử bắt buộc

| Bài | Cách chạy và đầu ra cần có |
|---|---|
| Smoke | 1/10/100 người, dữ liệu/quyền/jobs thật; đi hết login → import → result → citation → follow-up → export/delete; lỗi căn bản phải sửa trước tải lớn |
| Ramp/calibration | Giữ các mốc 5k → 10k → 25k → 50k; mỗi mốc đủ ổn định để đo throughput, latency, CPU/queue/pool và tính đúng; ghi cả cold/warm |
| Normal 50k closed | 10 phút warm-up + 30 phút steady, ít nhất 3 run; 50k intended-engaged coverage và các completing/waiting counters, journeys/content/SLO, tất cả seed/order/config và outcomes |
| Normal open arrivals | Ít nhất 3 run cùng release/dataset, mỗi run 10 phút warm-up + 30 phút steady; target 5k actions/s và waterfall tương ứng, tạm 15k RPS; scheduled/sent/completed/drop/reject/drain phải khớp |
| Stress | Tăng workload vượt normal envelope theo step đã chốt; tìm bottleneck, admission/rejection và khả năng hồi phục, không hứa một mức ngoài 50k |
| Breakpoint | Ghi mức đầu không còn giữ SLO hoặc chạm resource guard; báo maximum verified good throughput và điều kiện, không lấy mức HTTP 200 cao nhất |
| Spike | Đề nghị 25k → 50k trong ≤30 s, giữ 10 phút rồi về nền; thêm 30% clients reconnect/refresh trong 60 s; đo burst, retries, queue và hồi phục |
| Soak | Primary 2 giờ tại 50k, giữ gate 2h của gói trước với concurrency mới; đo RAM/FD/heap/pool/queue/WAL/temp/object growth/completion/drain. Extension 8 giờ khi cần kiểm chu kỳ leak/retention dài hơn và đủ resource; không tuyên bố đã kiểm chu kỳ chưa thử |
| Heavy/fairness | 100/200 heavy jobs/s (5×/10× reference), nền đọc và tenant nóng 50–80% traffic; quota/admission, queue age và tenant bên cạnh vẫn có kết quả đúng |
| Fault | API/worker chết sau ACK, lost response, stale lease, object chậm/lỗi/corrupt, disk full, DB failover/migration; kiểm durable effects, recovery và thao tác tiếp theo |
| Drain/rollout | Dừng admission/claim mới theo policy, xử lý việc đã nhận, chuyển version/lease an toàn; old worker không publish trái fence; thử khi còn nền tải và ghi mức tải |

Các cửa sổ lỗi phải có witness trước/sau; không chỉ kill process rồi kiểm một request HTTP 200:

| Điểm tiêm lỗi/race | Expected outcome cần đối chiếu |
|---|---|
| Object đã ghi, metadata chưa commit | Không có result visible trỏ tới generation dở; staged/orphan object được reconcile theo policy |
| Job đã commit, response/ACK tới client bị mất | Lookup/retry cùng idempotency key tìm đúng durable job; không sinh effect thứ hai |
| Worker đã tính xong, lease bị thay trước publish | Worker cũ không commit; retry hợp lệ hoặc terminal failure được ghi rõ |
| Cancel/delete chạy cùng refresh/export | Receipt phản ánh outcome thật; tombstone/fence chặn resurrect; source/shared objects còn đúng |
| Session/role bị revoke khi event/job/download đang mở | Hết quyền theo deadline đã công bố, không lộ dữ liệu thêm qua một đường phụ |
| Restart/restore xong | Đọc lại result cũ, no-op/refresh mới, follow-up, export và delete tiếp được theo quyền; IDs/hash/current pointers không sai |

Trong bài fault, RTO bắt đầu từ lúc tiêm lỗi và kết thúc khi consumer dùng được đúng chức năng; thời gian phát hiện cũng nằm trong số đo, không chỉ từ lúc bấm restart tới lúc process vừa lên.
RPO đối chiếu những durable effects đã được acknowledged có thể mất sau recovery; không chỉ nhìn tuổi bản backup hoặc thông báo database đã start.

Stress, breakpoint và fault phải có mức dừng theo CPU/RAM/disk/queue/error đã định trước. Khi thấy lộ tenant, sai dữ liệu, mất effect đã ACK hoặc corruption được phục vụ, dừng và giữ evidence để sửa.
Ngoài envelope có thể từ chối có kiểm soát; không dùng rejection ấy làm pass normal 50k. Queue vô hạn/retry đồng loạt có thể khuếch đại overload. [Google SRE: Cascading failures](https://sre.google/sre-book/addressing-cascading-failures/)
Không chỉnh thresholds, payload mix hoặc loại case sau khi xem outcome để biến fail thành pass; phần nào bị ảnh hưởng bởi bản sửa thì chạy lại gate liên quan, giữ predecessor và mọi run.

## 9. SLO phải giữ đủ các nhóm

Bảng này giữ các nhóm từ `CHUNG/04_SLO_VA_CAPACITY.md` của gói 04/11; primary concurrency hiện là 50k. Đây là ngưỡng dự án, không phải latency mà tài liệu kỹ thuật bảo đảm.

| Nhóm thao tác | p95 / p99 mục tiêu | Điều phải đúng |
|---|---|---|
| Session/capability/project/result metadata/list/claim/evidence page ≤50 items | 200 / 500 ms HTTP | Principal/scope/version; không full-catalog scan |
| Search/filter phức tạp/graph subgraph ≤200 nodes | 400 / 1.000 ms HTTP | Cursor gắn query/snapshot; đủ dữ liệu, không truncate ngầm |
| Light mutation/annotation/cancel ACK | 300 / 800 ms HTTP | Commit bền, quyền, idempotency/CAS |
| Job ACK | 250 / 600 ms HTTP | Durable job trước 202; ACK khác completion |
| Job status/event refresh | 200 / 500 ms HTTP | Scope/order/terminal thật, không giả 100% |
| Mở result/evidence/navigation khi data ready | 700 / 1.500 ms click → usable content | Nội dung/citation/focus; tách network và render |
| Local interaction/filter cached/selection | 100 / 200 ms interaction → update | Không mất selection hoặc bỏ dữ liệu |
| Job local S | 2 / 5 s end-to-end | Queue + execution + commit + usable result |
| Analysis local M | 10 / 20 s end-to-end | Đúng chức năng/nguồn/caller; provider ngoài báo riêng |
| Import/refresh M | 15 / 30 s end-to-end | Đọc/kiểm/hash/commit, snapshot dùng được |
| Export JSON/Markdown M | 5 / 10 s end-to-end | Nội dung/quyền/precision, tải file được |
| Export PDF M | 15 / 30 s end-to-end | Unicode, renderer an toàn, parity với report khác |
| Job L | 60 / 120 s end-to-end | Báo từng loại, không gộp S/M che tail |
| Cancel tới terminal an toàn | 2 / 5 s | Worker không còn quyền commit khi báo cancelled |

Queue wait có mục tiêu riêng: S 250/500 ms và M/L 2/5 s p95/p99; end-to-end vẫn phải đạt. Không cộng p99 queue với p99 execution để tính p99 tổng.
Download lớn báo TTFB, byte-last, throughput theo bytes/network; delete ACK theo mutation, purge/GC theo deadline size/retention đã công bố; auth/logout/rename/permission/refresh/retry/deep-link đều có dòng đo.
Provider/model jobs có bảng riêng theo model/quota/input/output. Báo bootstrap/cold start, normal/warm và failure recovery riêng; load fake-provider không xác nhận provider thật.
Web Vitals: p75 LCP≤2,5 s, INP≤200 ms, CLS≤0,1, mobile/desktop riêng; mức mở rộng giữ LCP≤1,8 s, INP≤150 ms, CLS≤0,05. Lab/synthetic/field RUM tách nhau. [Web Vitals](https://web.dev/articles/vitals)
Giữ thêm p95/p99 actions ở trên; không đổi p75 thành p99 hoặc lấy một lần Lighthouse làm đại diện người dùng. Accessibility kiểm theo criteria WCAG 2.2 AA và browser thật. [WCAG 2.2](https://www.w3.org/TR/WCAG22/)

## 10. Điều kiện nghiệm thu không được thương lượng bằng điểm tổng

- Normal 50k: business success **đúng và trong deadline ≥99,9%** trên scheduled/offered eligible business actions; failure/timeout/429/503/outcome-unknown/dropped actions vẫn được reconcile trong mẫu số.
- Negative security tests có expected deny riêng; không trộn chúng với positive business-success rate. Cancel có chủ ý phải có semantics/denominator chốt trước, không loại job thất bại sau outcome.
- Zero observed cross-tenant leak, sai citation/result/state, duplicate effect, accepted job lost, stale-owner commit, corrupted object served hoặc source/shared object bị xóa ngoài ý muốn trong các bài bắt buộc.
- “Zero observed” không là proof không thể có lỗi. Kiểm cả DB/object/effect sau response, quyền bị revoke giữa chừng và một tenant hợp lệ bên cạnh. [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
- Core reads/mutations cần ≥10.000 mẫu theo **operation × size stratum × run** cho p99, ít nhất 3 run; đánh giá từng run, không gộp đẹp hơn. Job/admin/rare routes có dedicated run dưới background load hoặc nêu thiếu precision.
- Báo completed-response percentiles cùng success-by-deadline/deadline-inclusive observations; timeout không biến mất vì thiếu response latency. Không cộng/bình quân p99 giữa stages/replicas.
- Generator có headroom; active coverage, offered/completed/drop/reject/pending/drain khớp; auth/quyền/durability/integrity và dataset không bị giảm khi tối ưu.
- Primary soak đủ 2 giờ tại 50k; không tăng vô hạn RAM/FD/queue/temp, completion/cleanup/drain khớp. Extension 8 giờ là điều kiện bổ sung nếu giữ claim về chu kỳ dài hoặc có drift/rủi ro chưa giải được bằng 2h; phải báo not_run nếu chưa thử. Stress/spike cần cho thấy lỗi được kiểm soát và khả năng hồi phục.
- Process-crash tier: mục tiêu RPO=0 đối với durable acknowledged effects, RTO≤60 s tới consumer được phục vụ đúng; thử API/worker death không tự là disk/controller/host/power-loss proof.
- Storage DR: mục tiêu RPO≤5 phút/RTO≤30 phút bằng restore vào env sạch rồi kiểm DB/object/hash/quyền/current pointers/jobs/tombstones. Backup command thành công chưa đủ. [PostgreSQL PITR](https://www.postgresql.org/docs/current/continuous-archiving.html)
- Schema migration/rollback và drain phải giữ client/API/job compatibility; rollback code không tự đảo schema destructive. [GitLab: avoiding migration downtime](https://docs.gitlab.com/development/database/avoiding_downtime_in_migrations/)
- Không có secret/token/payload riêng trong client bundle, logs, traces, metrics hoặc load report; resource quotas chống noisy tenant và chi phí ngoài ý muốn phải hoạt động. [OWASP API 4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/)

## 11. Bàn giao bằng chứng và sử dụng 12 ngày

Ngày 1 chốt resource/authority/workload/SLO/dataset và hợp đồng; ngày 2 có vertical slice thật và generator calibration, không đợi tới cuối phase mới đo.
Ngày 3–7 tích hợp và chạy dần smoke/5k/10k/25k, kiểm races/quyền/data trước khi tối ưu; ngày 8 phải có lần thử 50k để thấy sizing/bottleneck.
D8–D11 là cửa sổ đo normal/open, ba run, stress/spike/fault, browser journeys, primary soak 2h (extension 8h khi cần) và restore/drain; lịch phân người/ngày chính thức nằm trong `Phoi-hop-va-nghiem-thu.md`, không tạo lịch riêng ở guide này. D12 tổng hợp và kiểm chéo.
Nếu thiếu tài nguyên hoặc không đạt 50k, không hạ primary về 5k/10k/25k hay đổi profile thành toàn idle; bàn giao verified envelope, bottleneck, remedy, owner và gate còn mở.
Mỗi báo cáo gồm release/config/topology/dataset/tool identity, command/profile/seed, thời gian, raw-safe metrics, per-operation n/percentiles, active-window coverage, counters và expected/actual correctness.
Giữ mọi run, warm-up, failures, interrupted attempts và lý do rerun; phân `passed`, `failed`, `not_run`, `blocked`. Không dùng tài liệu kế hoạch, screenshot hoặc HTTP 200 để đóng gate.
Ghi cost/resource mỗi journey/job hoàn tất đúng, limits của IdP/provider/browser cohort và các lỗi chưa xử lý. Một người chạy lại một lát chức năng của người kia từ clean setup.
Một target chưa đạt vẫn chưa đạt dù build đã xanh. Một capacity run đạt không tự là production deployment hoặc availability 99,9% trên cửa sổ 28 ngày. [Google SRE: implementing SLOs](https://sre.google/workbook/implementing-slos/)
Quyết định release dựa trên evidence của cả hai người và các gate dữ liệu/bảo mật/capacity; tài liệu này không tự cấp quyền push/deploy hoặc công bố đã phục vụ 50k.
