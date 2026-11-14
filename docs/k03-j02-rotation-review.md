# K-03/J-02 — job đang chạy gặp session rotation

Ngày 2026-11-10. **Kiên đã duyệt policy/file; đã áp source và kiểm local.**

## Bằng chứng

Harness `tests/unit/test_k03_j02_backend.py` dùng PostgreSQL thật đã xác nhận:
job được enqueue/claim với credential hiện hành; normal renew phát credential
mới và giữ nguyên context_id; publication dùng digest cũ bị SourceNotFound.
Marker `PUBLICATION_DENIED_POLICY_OPEN` ghi nhận hành vi, không gọi đó là pass
của chính sách tiếp tục job. Đây không phải quyền truy cập bị phục hồi.

Lý do: worker giữ credential_digest lúc nhận job, trong khi PostgresSources._access
chỉ nhận credential active hiện hành. Normal rotation làm digest cũ thành rotated.
HTTP vẫn phải từ chối cookie rotated; không nới guard API để sửa việc này.

## Policy đã được Kiên duyệt

Job đã được server nhận có thể tiếp tục qua normal renewal của đúng server context
khi session, account epoch, membership và quyền thao tác vẫn hợp lệ.
Logout/logout-all, account switch, expiry, disable hoặc mất quyền vẫn chặn tại
lần truy cập/effect/publication tiếp theo. Job không chuyển sang context mới dù
cùng tài khoản đăng nhập lại. K-06/K-09 và J-01 cần biết quyết định này.

## Thay đổi đã thực hiện

| File | Mục đích |
| --- | --- |
| Mới `src/aletheia_lab/hosted_auth/worker_sessions.py` | Adapter PostgresSources chỉ dành cho worker. Trong transaction/gate hiện có, dùng captured digest để tìm chính context gốc còn sống và credential active hiện tại của nó; delegate guard strict hiện hành để kiểm account/expiry/membership/write. Không trả credential ra wire hoặc log. |
| `src/aletheia_lab/hosted/managed_worker.py` | Chỉ worker tạo adapter mới; API tiếp tục tạo PostgresSources strict như cũ. Giữ interface constructor và lifecycle/pool/lease. |
| `tests/unit/test_k03_j02_backend.py` và test_k03_* bổ sung | Chuyển characterization thành ca positive normal rotation cùng context; giữ các negative/revoke/switch/expiry/forged-scope. |
| `docs/k03-*.md` | Ghi policy, bằng chứng, giới hạn và bàn giao K-06/K-09. |

Không đổi schema/migration, JobClaim/wire model hoặc generic API cookie policy.
Không dùng cached actor/membership, không nhận context/user/role từ browser.

Pseudo-flow tại mỗi worker repository access:
1. Giữ cùng transaction, account SHARE để serialize với auth writers account UPDATE.
2. Tra đúng context từ captured credential đã lưu phía server; không tìm session
   khác chỉ theo user_id.
3. Tra active credential cùng context/generation; session/account epoch,
   expiry/enabled/verification vẫn phải hợp lệ.
4. Delegate strict repository guard bằng credential hiện hành, kiểm lại workspace
   và quyền read/write trong cùng transaction trước effect.
5. Bất kỳ bước nào không hợp lệ đều fail closed; HTTP reader/mutation không dùng adapter.

## Ca phải đạt trước bàn giao

- Normal renew trước dispatch, giữa compute và publish; nhiều rotation cùng context.
- Old browser cookie vẫn 401 và không dùng adapter worker để gọi API.
- Logout/all, account disable/epoch, expiry hoặc chuyển account/context không
  làm job cũ tiếp tục; account B không bị ảnh hưởng.
- Role/membership đổi trước publication: không result hoặc snapshot trái quyền.
- Context giữ nguyên ở normal renew; lease/fence và query scope vẫn từ DB.
- Unit/race + real UUID PostgreSQL harness; managed-worker wiring test hiện có.

Kiên đã mở managed_worker.py; K02/K04 xác nhận writer và interface mới.
Xem k03-j02-rotation-verification-20261110.md cho evidence/hash cuối.

## Callgraph đã review

managed_worker.py tạo một sources instance rồi truyền chung cho
PostgresJobs(pool, sources) và UploadOperations(sources, supervisor).
Override worker-only vì vậy đi qua source read/snapshot, complete và
complete_snapshot. Giữ nguyên digest ban đầu trong JobClaim và j01_jobs để
đối chiếu lease/creator/job; chỉ thay digest dùng kiểm quyền bằng credential
hiện hành của đúng context gốc.

Bản cuối kế thừa K04 _access(action)->Authorization và không dùng global gate:
capture user/context, account SHARE, re-resolve exact context/current credential
rồi delegate cùng tx. Read worker map sang preview ở workspace/source grant.
Các mô tả write=True/global gate ở draft đầu là lịch sử trước cập nhật K04.
