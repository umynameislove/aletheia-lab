# K-05 — kết quả và bàn giao ngày 10/11/2026

## Full catalog follow-up — 11/11/2026

Owner bỏ giới hạn 500 MiB cho lượt full. PostgreSQL 16.15 xác nhận bằng SQL:
**200.002 snapshot + 2.000.010 result + 200.002 bundle = 2.400.014 scoped records**,
exit 0 sau 663,91 giây. Cả 8 query families được EXPLAIN ở hai strata; 626 source
inputs không đổi. Schema/object do probe tạo đã dọn và giữ receipt.

Đây là aggregate metadata qua 50.001 workspace, tái dùng 54 canonical template
objects đã validate; chưa phải 2 triệu independent analyses, cold-cache,
50k-user load hoặc 2h soak. Peak toàn task 8.067.649.536 byte (~7,51 GiB).
Các witness 500 MiB và giới hạn của lượt ngày 10/11 dưới đây giữ làm lịch sử.

## Kiểm bổ sung macOS — 11/11/2026

Đã kiểm đủ **100.000 object**: full scrub và phát hiện corruption đạt; hot read
giữ một object/792 byte/bốn SQL. Fixture đã dọn sạch. Query probe có checkpoint
sau commit, giới hạn dung lượng và dọn schema/object do nó sở hữu.

Trong giới hạn kiểm thử 500 MiB do Owner chọn, catalog thực có **4.802 snapshot,
48.010 result**, tổng 57.614 record qua 1.200 workspace; peak toàn bài khoảng
383 MiB. Đây là phép kiểm access path trung gian. Target 200k snapshot/2m result,
cold/large-tenant/pool/fairness, soak và restore vẫn chưa được nghiệm thu.

Bảng chi tiết ngày 10/11 dưới đây giữ nguyên phạm vi và số đo lịch sử.

**Đã triển khai và kiểm nền storage local; chưa đủ nghiệm thu toàn bộ K-05/DB40.** Bốn bước .01/.03/.04/.05 đạt phạm vi storage được ghi dưới đây; .02/.06 và các gate catalog lớn, tải, consumer/restore còn mở. Không dùng implementation/library/fixture thay cho endpoint hay joint acceptance.

## Revision, ownership và nguồn

Checkout WIP đúng: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api`, branch `feat/product-api`, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b`. Workspace `Đồ Án 3` giữ evidence, không phải bản code được clone/chuyển tới. Các thay đổi chưa commit của K-02/K-03/K-04/J-01/frontend được giữ nguyên trong checkout; HEAD không nhận diện hết source được kiểm.

K-05 chỉ ghi các module/script/test/docs K-05 mới và thay đổi scoped integrity trong `project/persistence.py` cùng test có sẵn. Không sửa source/job/auth/runtime/contract/pyproject của writer khác. K-03 giữ worker/session; K-04 giữ authorization/project-policy/source/job và HTTP; K-02 giữ pool/runtime/transport. Đã đọc các chat/báo cáo mới và đối chiếu source thực tế, không dùng trạng thái historical not_run hoặc báo cáo lỗi cũ thay cho hiện trạng. Phối hợp được ghi ở `k05-ownership-handoff.md`; không tự gửi email/tin cho người khác.

Nguồn giao việc đã đối chiếu: K-05 trong 01-Cong-viec, sáu bước trong 08-Ke-hoach, hai tracker task, 02-Kien-truc-va-du-lieu, 04-Hien-trang-va-nguon, toàn bộ 40 DB/26 fault cases, Hop-dong và P01–P38, J-03/J-08, seed_catalog.py/database_observe.sql/README. Đã kế thừa K-03 admission/session/login ports, K-04 workspace/project policy và J-01 upload/job source; không dựng identity/session/policy engine thứ hai.

## Kết quả sáu bước

| Bước | Trạng thái | Code và bằng chứng thực | Giới hạn/đầu nhận |
|---|---|---|---|
| K-05.01 | Passed phạm vi storage local | Explicit migration V1–V4, checksum/installed signatures, fresh/adopt/reapply/verified-prefix upgrade/rollback; tenant/parent/object FK, canonical immutability, artifact-reference retention. Same canonical ID ở hai tenant; source/job ports giữ dữ liệu/receipt qua adoption; LOGIN role riêng dùng catalog nhưng không DDL/ledger. | Chưa production rollout, whole-runtime role enforcement hay joint mixed-version release. K-02/operator giữ topology/config/grants; K-04 project policy có ledger riêng. |
| K-05.02 | Blocked nghiệm thu đầy đủ | Access-path map đủ38 operation+5 đường bổ sung; SQL lọc tenant/project/type/snapshot/grant/tombstone trước LIMIT; keyset và ID search literal, không slice ProductView trong memory. Có plans/query count/rows/buffers thực trên2002snapshots/20060results; V4 index workspace/type/ID được EXPLAIN chọn tự nhiên. | Target200k/2m thất bại trong budget900s; cold cache/per-tenant M/L/sparse-grant/pool wait chưa đủ. K-17/operator mở khóa môi trường/budget và workload; không ngoại suy. |
| K-05.03 | Passed managed filesystem+PostgreSQL local | Stage/fsync/hash/length/schema→leased ready reference; metadata rollback/duplicate/partial write/abrupt child crash; shared refs, live/cancelled-reader leases, grace, staged/orphan/missing-metadata recovery. Khóa theo catalog-schema+digest, không serialize mọi tenant. | Một authoritative catalog/root, filesystem dùng chung giữa các process. Không nhận remote store/power-loss deployment. K-13/K-18 quyết định retention/delete/restore. |
| K-05.04 | Passed correctness trong scope đã kiểm | Typed scoped reads so canonical ID/hash/schema/length/parent/artifact projection; reauthorize sau object I/O; corruption fail-closed, scope tốt tiếp tục đọc. Full local audit và batch object/catalog scrub còn nguyên; constructor/persist không hash toàn catalog. | DB03 target100k vẫn blocked: actual0/100/1k/17k, hot read luôn1object/792byte/4SQLite statements. Full clean scrub kiểm tới1k; 17k scrub hết budget. Không gọi đây là coverage100k. |
| K-05.05 | Passed importer local có giới hạn | Read-only checkpointed SQLite/P3v2/P6v4, streaming DB fingerprint, explicit provisioned mapping/current actor; preserves IDs/hash/count/parents/current pointers/history/turn/evidence/ProductView semantics. Project-atomic checkpoint, interruption/cancel/resume/conflict, clock/revoke/tombstone tests và parity thật sau commit/replay. | Object64MiB/project256MiB, source tổng hợp. Không tự mở local path ở hosted. Quân kiểm browser/consumer sau migrate/restore; representative multi-project backfill/process restart vẫn DB30/K-18/K-17. |
| K-05.06 | Blocked nghiệm thu đầy đủ | Numeric observations vacuum/analyze/dead tuples/WAL/index/temp/locks/pool-wait categories/jobs/orphan; reader đồng thời31writes/30revisions/15deletes + normal VACUUM và GC; lock-timeout rollback/reuse. | Chưa2h mixed soak/fairness/50k/failover/disk-full hoặc backup/restore/RPO/RTO cùng consumer. K-17/K-18 tiếp nhận. Không thêm lịch tự động. |

Passed ở bảng này là **đúng boundary storage local**, không tự đóng J-03/J-08 hoặc các task giao nhau. Tổng K-05 vẫn blocked.

## Kiểm cuối trên snapshot ổn định

Evidence gốc: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án 3\k05-verification-20261110`.

| Lượt | Kết quả | Phạm vi |
|---|---|---|
| final-tests.log + final-result.json | **108 passed,167.16s,exit0,không skip** | K-05 unit/PG thực + P3/P6 persistence/lifecycle/evidence regression. UUID schema và role test riêng, controlled barriers, filesystem thật; không IdP/browser/staging/load acceptance. |
| final-source-before.json / final-source-after.json | **109 file giữ nguyên SHA256, changed_files=[]** | Source/domain/shared dependencies và test/script được chọn ở lượt trên. Đây là danh tính WIP đã kiểm; thay đổi sau lượt này phải có kiểm tương ứng. |
| final-ruff.log | All checks passed | Source/scripts/tests K-05 và hai file local đã sửa; không whole-repo lint. |
| final-mypy.log | **9 source files,strict,follow-imports=silent,đạt** | Sáu module persistence, local ProjectStore và2CLI. Không nhận toàn repo/harness benchmark là whole-repo typecheck. |
| domain-regression.log | **428 passed,1skipped,34.21s** | Lượt rộng product/project trước hardening local cuối; tập persistence bị ảnh hưởng đã kiểm lại trong108. Skip không được tính passed. |

Không cộng các lượt chồng nhau. Lượt đầu local18 có1fail do migration rebuild graph bị kiểm projection trước khi index tồn tại; đã tách decode nội bộ khi migrate rồi verify sau rebuild, kiểm lại đạt. Các lỗi fixture timestamp/last-owner/imports/typed casts và partial-file cleanup giữ trong log gốc; không tính passed. Lượt tổng trước96passed có source K-04 đổi; lượt V3 sau104passed/1failed vì dependency itsdangerous của cursor chưa vào PYTHONPATH. Đã dùng dependency có sẵn đúng target, không cài/đổi môi trường chung, rồi chạy lượt108 ổn định trên V4. Không lấy các con số đó cộng thành test độc lập.

## Query plan, target và tài nguyên

`schema/query-scale-20.json`: actual24,064records =2,002project_bundles+2,002snapshots+20,060results, catalog82,690,048byte,50.907s cho prepare/seed/plans. Large template có100snapshots/1002results mỗi tenant; small2/20. Sáu data query mỗi strata, mỗi execution1SQL; warm execution medians khoảng0.0255–0.1265ms, page51rows53–56buffer hits, snapshot-result page10rows13hits, point lookup1row8hits trong mẫu đó. Đây không phải HTTP/authorization/pool latency hoặc capacity. Full per-node plans/loops/filter rows/buffers giữ trong JSON.

Template có bytes và typed domain records thật; repetition giữa các tenant chia sẻ physical immutable content theo hash. Không dùng JSONL logical seed làm backend import. Profile này không thay50kprincipals/10kprojects hoặc lịch sử rất lớn trong một tenant. Fresh backend/cache state không phải cold cache; không flush cache của các chat khác.

Target attempt coreV2: budget900s, TimeoutError/exit1, stdout ghi1200/2000copies committed; **không có final aggregate snapshot/result count hay large final plan**, nên không ghi achieved/passed. Observer trước đó đếm thực1100project rows, first tenant100snap/1002results và catalogallocated4,854,317,056byte; không nhân số đó để nhận target. Shared PG container có limit512MiB; các sample khoảng304–320MiB và43–84%CPU, block-I/O counter không dùng được. Chưa chứng minh nguyên nhân bottleneck. `schema/target-progress.log`, `target-observer.jsonl`, `target-cleanup.json` giữ witness. Owned schema đã được dọn và xác minh; không restart/delete container/chat khác. V3/V4 thay jobs/index sau lượt target, giữ riêng version/checksum; không gọi đó là benchmark target trên V4.

DB03: `objects/integrity-100k-budgeted.json`, actual17,000real indexed files trong120s budget, khoảng77,090,816byte allocation estimate. Mỗi hot read first/warm/after unrelated corruption vẫn1object/792byte/4SQL. Full clean scrub tới1000; tier17000 chỉ audit820objects trước hết budget. Partial/nonzero, không passed100k; cleanup owned fixture hoàn tất ở126.906s. Script mới `k05_integrity_probe.py` giữ phép kiểm có thể lặp và deadline; không ngoại suy.

## DB/fault matrix và vận hành

- `docs/k05-database-cases.csv`: đủ40scenario/invariant gốc, owner/code/test/evidence/env/unlock từng ca. **DB18/DB19/DB31/DB34 passed scope local storage;36 blocked**. DB31 đã chạy CONCURRENTLY qua nhiều connection, cancel tạo invalid index, verify từ chối rồi rebuild/verify đạt. DB40 provider chậm không bị tự đánh passed.
- `docs/k05-fault-cases.csv`: đủ26case, **F13 passed scope storage,25 blocked**, có đầu nhận và cách mở khóa. Không N/A vì chưa có implementation.
- `docs/k05-persistence-runbook.md`: migrate/verify/operator role, query/maintenance/scoped/local scrub, timeout, topology/cutover và recovery.
- `docs/k05-object-policy.md`: publication, directory fsync/platform boundary, cancellation/leases, hai reconcile cursor, orphan GC, alert/isolate/restore canonical bytes và rebuild projection policy.
- `docs/k05-legacy-import-runbook.md`: mapping/source-copy/limits, parity, resume/conflict/recovery và no dual-write.

Observation WAL là cluster-wide và DB counters có các client khác; không gán toàn delta cho K-05. Mixed witness `mixed-maintenance.json` ghi31writes/30updates/15deletes và reader/GC thật; short run không chứng nhận backup hay2hsoak. No recurring schedule/cloud/paid service was created.

## Caller và điều kiện còn mở

K-04 helper `authorize_project` và policy extension đã được catalog sử dụng; list có grant SQL trước LIMIT, typed reads recheck policy sau I/O. `list_workspace_records(kind,after,limit,search)` là internal keyset; public actor/query-bound signed cursor ở HTTP owner. Search hiện là exact literal project/record ID (ký tự `%` không là wildcard), không nhận full text search. K-04 giữ HTTP/DTO/member/export/event/cancel/retry; K-02 giữ runtime pool/readiness/expected role/schema; K-03 giữ session/worker continuity. Storage-admin migrate cài core rồi K-04 project policy trong outer transaction; importer CLI đòi verify cả hai trước ghi.

Cần giữ rõ upload provenance: J-01 hiện dùng `UploadSnapshot` ID `upload-snapshot-*`, Git `not_supplied`, `j01_sources/j01_upload_snapshots/j01_jobs` là authority cho uploaded flow. Local P3/P6 importer/catalog giữ P3 snapshot và ProductLifecycleRecord có scope `p3-snapshot-*`. Không ép uploaded snapshot thành Git snapshot hoặc chép job-output DTO vào một P6 lifecycle record không hợp lệ. **Managed upload runtime chưa được K-05 tự chuyển sang catalog này.** Điểm ghép Kiên/J-01/K-04 phải giữ một authority cho payload: project/source grant bridge và atomic publication/read adapter trên schema/provenance đã thống nhất; không tạo thêm result store song song. Các port object stage/ready/finish và catalog policy/read/paging đã có; không dùng legacy importer làm mutation engine. Chi tiết caller/lock/revision/tombstone hướng dẫn ở ownership handoff.

Quân/J-03 cần đối chiếu list/result/history/current pointer/old result bằng cùng IDs/hashes qua hosted UI, privacy/cache/role/context và source reader thật. Quân/J-08+K-18 cần clean build/deploy/compatible rollback/restore DB+objects+permissions/jobs/tombstones, file/result parity và RPO/RTO. K-17/operator cần môi trường/quota/budget và profile cho catalogtarget/cold/M-L/pool/soak/fairness/fault/50k. Đây là các đầu vào/gate còn mở, không được thay bằng fixture hoặc tự ký joint acceptance.

## Git và tracker

Không stage/commit/push/merge, reset/clean/discard, chuyển nhánh/worktree, clone/copy/move repo hoặc ghi đè WIP của task khác. Final Git check giữ branch/HEAD và staged rỗng. Truy cập/sửa repo/tài liệu ngoài workspace đều dùng escalation công cụ đúng quyền; không dùng tool khác né sandbox. Không log credential/DSN/token/cookie/raw source thật.

Tracker gốc chỉ cập nhật K-05 parent+6subrows,4DBstoragecase vàF13 sau backup, giữ nguyên byte mọi record khác (BOM/newlines/quoted multiline included). Parent K-05 và .02/.06 còn blocked; DB40 và các ca còn thiếu không đóng. `tracker-update.json` ghi source/backup/before-after SHA và các row IDs; updater tự kiểm CSV tổng hợp trước khi ghi thật. Không dùng số test overlap hay một main SHA để đóng toàn đồ án.