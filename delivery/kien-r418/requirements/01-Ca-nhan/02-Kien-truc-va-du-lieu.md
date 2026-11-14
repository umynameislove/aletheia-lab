# Kiên — kiến trúc, dữ liệu và cửa sổ lỗi cần giữ

## Kiến trúc tối thiểu

```text
Browser ─ HTTPS/static assets ─ Hosted API ─ PostgreSQL(metadata/session/jobs)
                                  │                    ↑
                             object storage ← bounded worker processes

Local app ─ loopback bridge ─ local ProductService/SQLite
```

API/worker dùng chung domain schema và canonical identities. Tách process theo
loại tài nguyên, không biến mỗi domain thành một microservice. Static assets
có content hash/cache; private API không được CDN cache thiếu identity/permission.
PostgreSQL là lựa chọn cho hosted multiwriter/shared state; SQLite giữ local.
Queue PG hoặc thư viện bền đủ cho bước đầu; broker/cache/search service chỉ thêm
khi có bottleneck và kế hoạch vận hành cụ thể. Không bắt buộc Kubernetes/Redis/Kafka.

## Hiện trạng có thể kiểm trong code

| Điểm | Bằng chứng | Việc cần làm |
|---|---|---|
| Local transport | `dashboard/bridge.py`: HTTPServer, loopback, request body tới 1 MiB/timeout 5s | Hosted adapter có ASGI/production server/limits; giữ local boundary riêng |
| Identity | `X-P6-Bridge` public constant, service chưa nhận actor | Session/membership/object permission ở hosted mọi route |
| DB hot path | `project/persistence.py`: constructor verify_integrity, load/hash toàn catalog | Scoped queries/verification+background scrub, giữ fail-closed integrity |
| Domain | P3+P6 giữ hash/snapshot/parent/evidence và shared store | Reuse domain qua repository/adapters, không ghi DTO vào DB tùy tiện |
| Analysis | main có deterministic_mock; overlay có thêm metric observations | Capabilities thật; pin version/inputs; không expose private research |
| Async | RPC đồng bộ, không có durable hosted job lifecycle | Job ACK bền, attempt/fence/deadline và consumer receipt |

Python documentation cảnh báo `http.server` không dành cho production; ở đây
cũng có serial head-of-line được probe cũ kiểm. Điều này không có nghĩa mọi ứng dụng Python hoặc
SQLite đều yếu; cần sửa đúng implementation và deployment của dự án.
[Python http.server](https://docs.python.org/3/library/http.server.html),
[SQLite WAL](https://www.sqlite.org/wal.html),
[FastAPI deployment](https://fastapi.tiangolo.com/deployment/concepts/).

## Một nguồn dữ liệu và các invariant

- Metadata: actor/workspace/memberships/permissions; projects/previews/snapshots/
  comparisons/results/turns/claims/evidence/graph; annotations/revisions;
  jobs/attempts/leases; exports/deletion receipts/tombstones/audit entries.
- DB foreign keys/unique/checks phải giữ tenant và parent scope. Lập access-path
  map cho 38 operations và index từ query thật; không chỉ thêm index cho mỗi cột.
- Immutable payload ở object storage: canonical bytes/hash/size/schema;
  mutable pointer/revision ở transaction. Index/projection không được trở thành
  một nguồn sự thật khác mà không có version và quy tắc rebuild.
- Source/input/preview/metric definitions phải pin đúng lúc confirm, kiểm lại
  source changed/lease expiry. Mapping có target/baseline/direction/unit và
  decimal thresholds; giữ no-op snapshot và lịch sử result cũ.
- Không verify toàn kho trong request. Xác thực object đúng lúc dùng, verify
  source/record relations, có full/background scrub và cảnh báo corruption riêng.
- Metadata+object không là một transaction xuyên hai hệ: upload/stage/hash;
  commit ready-reference; orphan cleanup sau grace; quyền/retention xác định.

## Transactions cần có negative race tests

| Cửa sổ | Invariant |
|---|---|
| Two same-key submissions | Một logical operation/receipt; body khác dùng cùng key phải báo conflict |
| Refresh vs refresh | CAS/revision quyết định; không mất update hoặc có hai current pointer |
| Publish vs delete | Tombstone và worker epoch chặn stale result; object orphan reconcile |
| Role revoke vs publish/download | Policy hiện hành được kiểm; không dùng captured grant vô hạn |
| Upload ready vs metadata failure | Không có metadata trỏ tới object chưa sẵn; orphan không bị giữ vĩnh viễn |
| ACK lost/worker killed | Durable job còn truy được; retry không side-effect kép |
| Cancel vs successful commit | Giữ terminal truth; cancel_requested không phải undo |
| GC vs shared reader/job | Không xóa object đang được ref/lease; reader integrity giữ đúng |
| Migration vs old/new clients | Compatibility window rõ; rollback không mất schema/data |
| Restore vs deletion history | DB+objects+permissions/jobs/tombstones cùng recovery point |

Giữ DB transactions ngắn; không giữ connection khi chờ I/O ngoài, PDF/provider
hoặc input từ người dùng. Tính tổng pool budget của mọi process/replica + worker + admin reserve;
đặt lock timeout, query cancellation và fairness. PG `SKIP LOCKED` phù hợp queue claim, không biến
ordinary result reads thành đúng bằng cách bỏ các row bị lock.
[PG locking](https://www.postgresql.org/docs/current/explicit-locking.html),
[PG SELECT](https://www.postgresql.org/docs/current/sql-select.html).

## Auth và trust boundaries

Ưu tiên IdP/OIDC được duy trì kết hợp server session; không tự viết password service trong
12 ngày. Session cookie/TLS/CSRF, rotation/logout/expiry/revoke phải nhất quán.
RBAC là đầu vào của policy; membership + resource grant quyết định quyền với object.
Ẩn nút theo role không thay việc kiểm quyền ở server. Với RLS kiểm bypass/owner role,
transaction-local tenant context và pooled connection reuse.
[OAuth security BCP](https://www.rfc-editor.org/rfc/rfc9700.html),
[OWASP session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[OWASP authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

Hosted upload không nhận root path. Archive limits gồm expanded bytes/count/
depth/symlink/traversal và staging cleanup. URL connectors cần SSRF/egress
allowlist nếu triển khai. Không dùng pickle, không chạy code không tin cậy và không cho renderer truy cập
mạng. Giữ provider payload tối thiểu, có capability rõ và budget; không gửi raw
source tự động. Credential nằm trong secret store/config, không trong request log.

## Nguyên tắc tối ưu và nghiệm thu

1. Đo một slice hoàn chỉnh và tài nguyên theo stage trước; phân biệt CPU/hash/serialize,
   DB scan/lock, object I/O, queue và network. Threading không tự giải CPU/GIL
   hoặc transaction races; async không làm blocking code tự thành nonblocking.
2. Dùng query có giới hạn, paging và projected payload; không load toàn store/view.
   Profile cache hot/cold trước khi chọn thay đổi cache/index/worker/replica.
3. Giữ cùng correctness, quyền, durability và payload trước–sau; tối ưu không được
   bỏ hash/security/commit hoặc trả thiếu data để giảm p99.
4. Đo per-operation/size/tenant strata; N+1, lock/pool waits, GC/catalog growth và
   replica resource multipliers. Endpoint healthy không thay việc hoàn tất tác vụ.
5. Build/lock/image/config/migration/runbook phải giúp dựng lại hệ thống; backup phải restore
   DB+objects+permissions/jobs và kiểm đầu ra thực tại consumer. Process loss
   trên retained storage khác host/power/storage loss; không tuyên bố đạt tier chưa thử.

Không sửa thuật toán/nghiên cứu Paper A/B hoặc lịch sử public attribution
trong phase này. Thay đổi domain contract cần phối hợp Quân/Bao ở đúng interface;
không thêm private tracking/human labels vào public repo hoặc fixture gửi ra ngoài.
