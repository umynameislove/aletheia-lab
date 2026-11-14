# K-03 — hoàn tất phần độc lập và bàn giao phụ thuộc

Ngày 2026-11-10. Kiên xác nhận chưa có quyết định IdP/MFA/recovery hoặc frontend
hosted `/v1` mới; yêu cầu hoàn tất phần độc lập và ghi rõ phụ thuộc.
Phần có đủ đầu vào trong phạm vi đã duyệt được triển khai và kiểm local.
Nghiệm thu chung K-03.01–06/J-02 vẫn mở theo các điều kiện dưới đây.

## Nơi làm việc và WIP

Worktree: `C:\Users\Trung Kiên\Documents\ChatGPT\Đồ Án\aletheia-j01-api`.
Branch `feat/product-api`, HEAD `2bbcdc2a7637c840babd8b71910306c64072bc4b` cộng WIP.
Workspace mới `Đồ Án 3` là repo rỗng; không chuyển code sang đó.
Không clone, đổi nhánh, tạo worktree, sao chép WIP, stage, commit, push hoặc deploy.
Truy cập ngoài writable root mới đã đi qua cơ chế duyệt sandbox.

Chỉ sửa source K-03 và hai file auth integration đã được duyệt
`callback_http.py`, `postgres_fresh_login.py`; config/test/docs thuộc K-03.
Không sửa frontend, contract/schema/fixture chung, pyproject hoặc tracker CSV
trong lượt tiếp tục này. Các WIP/dependency có từ trước được giữ lại.

## Phần hoàn thiện thêm

- Callback: OIDC/login không hợp lệ → 400; disabled/unprovisioned/email-unverified
  → draft 403; issuer mismatch → 400; outage → 503; lỗi nội bộ → 500.
  Giữ generic text body, no-store/no-referrer, không phát cookie/redirect khi lỗi.
  Claim đã dùng không được giải phóng để replay; external cancellation truyền ra.
  J-01 vẫn cần duyệt public account-state fixtures/presentation.
- `required_acr`: một giá trị rõ ràng cho issuer đã pin trong RuntimeSettings;
  gửi acr_values và kiểm chính xác signed ID Token acr trước khi tạo identity.
  Thiếu/sai/type bất thường bị từ chối ở normal login và reauthentication.
  amr/auth_time không thay cho ACR yêu cầu. Không thêm wire DTO hoặc lưu token.
  Default chưa đặt để giữ fixture password-only hiện có.
- Recovery với cookie cũ: DB thật tái hiện login-start bị chặn bởi credential
  hết hạn/khác epoch. Normal fresh login giờ được bắt đầu nếu binding cũ không
  còn live; reauthentication vẫn cần credential hiện hành. Missing/random
  credential không được chiếm binding đang trỏ vào context sống.
  Không đổi schema hoặc cơ chế lock.
- Test: callback denial/replay/outage/cancellation; signed ACR và zero session
  khi không đạt assurance; email verification đổi sau lookup; watermark bằng
  thời điểm tạo intent; new subject có email cũ không kế thừa account.
  DB kiểm disable/unverified, reenable với epoch mới và cookie hết hạn còn trên client.

## Kết quả kiểm cuối

| Kiểm tra | Kết quả và phạm vi |
| --- | --- |
| Toàn bộ test_k03_*, test_j01_*, test_k02_* unit tại lúc thu thập | **912 passed in 70.40s**, exit 0, 62 file, không skip |
| Ruff + C901 | Đạt trên 17 module auth K-03/integration và tất cả test K-03 |
| Mypy strict | Đạt trên cùng 17 module; psycopg từ dependency target local có sẵn |
| HTTP/DB trực tiếp | Đạt với PostgreSQL thật, OIDC synthetic, clock host/DB thật |
| Logout-all → từ chối session local | **0,063 giây** cho logout-all và các lần đọc từ chối tiếp theo; cùng máy |
| Dọn DB | K03_DB_OWN_SCHEMA_REMOVED=PASS; chỉ tạo/xóa schema UUID K-03 |

Lượt đầu hai file test mới có 23 failed/17 passed, tái hiện callback 500 và thiếu
required_acr. Sau sửa: 93 test tập trung đạt, sau bổ sung/review: 107 test liên
quan đạt. Lượt full trước sửa stale-cookie là 875 passed; lượt cuối 912 ở trên
bao gồm test mới từ các chat song song. Không cộng các số hoặc quy toàn bộ chênh
lệch cho K-03.

Lệnh DB đầu đọc nhầm DPAPI nhị phân bằng ConvertTo-SecureString và chưa chạy
được kiểm DB. Đã sửa sang ProtectedData.Unprotect(CurrentUser), không in
credential. Lượt DB tiếp theo tái hiện lỗi stale-cookie; sửa root cause rồi kiểm
lại đạt, gồm cả random-cookie/live-context từ review độc lập. Password truyền
qua environment cho tiến trình test, rồi xóa biến/clear buffer. Schema UUID
được dọn cả ở lượt test thất bại.

Rate-limit/migration/packaging có bằng chứng trước tại
[k03-shared-rate-limit-verification-20261110.md](k03-shared-rate-limit-verification-20261110.md),
[k03-admission-migration-rollout-20261110.md](k03-admission-migration-rollout-20261110.md),
[k03-migration-package-verification-20261110.md](k03-migration-package-verification-20261110.md).
Không chạy lại Docker/rollout vì code migration/quota không đổi.
Wheel đã kiểm thuộc lượt trước; không coi là release đã kiểm cho mọi auth source mới.

## Sáu phần và điều kiện nghiệm thu còn thiếu

| Phần | Đã triển khai/kiểm local | Điều kiện đóng nghiệm thu chung |
| --- | --- | --- |
| K-03.01 | Authlib/JOSE, Code+PKCE, issuer/audience/redirect, state/nonce/skew/replay, bounded transport; smoke Keycloak+DB ở báo cáo runtime trước | IdP/client/HTTPS và build được duyệt; public callback/error; browser đúng build |
| K-03.02 | Exact issuer+subject, verified/disabled policy, no email linking/auto-owner, commit epoch/watermark; signed ACR gate; DB account-policy | K-04 provisioning/invitation/first-owner; MFA/recovery thật; trusted epoch bridge; public account states |
| K-03.03 | Server store/cookies/expiry/rotation/CAS, renew/logout/all, fixation/replay/race; DB recovery với cookie cũ | K-05 supported session schema/transactions, topology/failover và vận hành |
| K-03.04 | CSRF library/context proof, Origin/Fetch Metadata/media/bypass/no-effect, runtime integration | Edge/cookie/trusted proxy được duyệt; browser same/cross-origin trên deployment |
| K-03.05 | Backend epoch/intent fencing; Keycloak+DB A→B HTTPX/ASGI trước đây; số đo local hiện tại | Quân hosted /v1 consumer, UI/cache/draft fence, tab/BFCache/late response; membership/job/download ≤5s; J-02 |
| K-03.06 | Shared PG quota/multi-process, bounded outage/backoff/deadline/audit, migration/adoption/rollout/rollback/installed CLI | K-05/operator production adoption, budgets/key/proxy/NAT/load/restore; accepted retry UX; deployment/failover |

## Gói phối hợp cần nhận

- **J-01 + Quân:** chốt fixture/version cho Session/Problem và draft
  renewal_conflict/409, rate_limited/429, service_unavailable/503, service_failure/500.
  Start/callback dùng text, admission có thể trả Problem JSON; chốt presentation.
  Không hiểu mọi 403 là session revoked. 429 có Retry-After integer 1…window,
  window ≤3600; chốt fallback thiếu/sai header và explicit retry có giới hạn.
  Không replay callback hoặc tự lặp mutation/redirect.
- **Quân + J-01:** consumer hiện dùng local /api/, live sessionGuard=null;
  chưa có hosted /v1 transport. Cần bootstrap session, CSRF trong memory,
  BroadcastChannel không chứa secret, fence response cũ và wipe context cũ.
  Kiểm hai tab/BFCache/renew/logout-all/switch/late job/download trên cùng build.
  Hosted login không làm local RPC trở thành backend có phân quyền.
- **J-01 + Quân + operator:** chốt IdP logout proposal: disabled → local 204;
  enabled → 200 {logout_url: string|null} sau local revoke. URL cần browser đi
  qua IdP/confirmation; null chưa hoàn tất IdP logout. Cần URI chính xác
  public_origin + '/#/signed-out' và UX tương ứng. Enabled mode từ chối
  mode=switch; proposal full logout rồi fresh login return_to=/.
- **K-04 + K-05:** provision exact issuer+subject, invitation/first-owner/membership;
  trusted writer transaction đổi enabled/verification, epoch và revocation
  watermark. Disable→reenable đơn thuần có thể làm session cũ sống lại nếu không
  tăng epoch. Cần event/admin bridge được xác thực cho recovery/compromise/disable.
  SQL writer trong harness chỉ là fixture.
- **IdP owner/operator:** chọn provider/client/environment, recovery UI,
  MFA/fallback và ACR/mapping flow. Khi bật/đổi required_acr, invalidate session
  epochs và pending intents cũ qua trusted writer; session chưa lưu assurance
  để kiểm lại retroactively. Kiểm normal/expired/replayed recovery, MFA vẫn bắt
  buộc, disabled account và subject continuity.
- **K-05 + operator/K-02:** duyệt migration/role/search_path, backup/restore,
  key/namespace, quota/NAT/fixed-window bursts, trusted source/readiness,
  quiet-window policy change và audit redaction. Đã có local evidence để review.

Không thêm dịch vụ password/reset/provisioning hoặc frontend auth riêng.
Không đánh dấu tracker ngoài repo passed. Real MFA/recovery, browser và J-02
chung chưa được kiểm hoàn tất.

## Nhận diện source đã kiểm

Hash nhận diện file WIP; không phải SHA release.

| File | SHA-256 |
| --- | --- |
| `src/aletheia_lab/hosted_auth/callback_http.py` | `b8291fa79f3033a1c3bfd203579a8eeb17d2e30b5c26c12776ea54aa8bf42d60` |
| `src/aletheia_lab/hosted_auth/postgres_fresh_login.py` | `e1de7dbfeb4776cd650ea86ed6725dc54b22c9ed86a9f6aaa630c3946fa56ba0` |
| `src/aletheia_lab/hosted_auth/settings.py` | `bd3ef032268d34a33f82124102d9334fc0553d88097cd96b1310f013c55a51cd` |
| `src/aletheia_lab/hosted_auth/oidc.py` | `d09fcaf308d005b6e04add912ff38d40f9d99265856c2f7bb8bb2735a4dc5fe3` |
| `tests/unit/test_k03_auth_assurance.py` | `bb4490eab1f1d9da135d2a5b620860f2b9c886f578e6970b6dea98f49a516a47` |
| `tests/unit/test_k03_callback_denials.py` | `8280797752c6e67637284bc4e962d601f431f406e3c765e273e5d5cdbe922841` |
| `tests/unit/test_k03_login_coordinator.py` | `09bda6b6674f67569ae17eabceeba27e9590517430c6e3d701350b57610d748e` |
| `tests/unit/test_k03_postgres_http.py` | `0272a78a4300725fb0bc5f8ba9bccadf1284fb983820d407d0b6dbf7a0c6ccb8` |

## Kiểm WIP cuối lượt

Branch/HEAD vẫn như trên, staged rỗng. Hash của tám source/test trong bảng khớp
file cuối đã kiểm; admission_migration.py, admission_migrate_cli.py,
postgres_admission.py và admission_schema.sql giữ nguyên hash đầu lượt.
Có 11 file đã tồn tại được K-03 chỉnh đúng phạm vi và ba file K-03 mới.

Snapshot cũng quan sát 12 file đã tồn tại ngoài các chỉnh sửa này thay đổi trong
lúc làm việc, gồm managed_runtime/upload_runtime, composition/job_http/settings,
config và báo cáo/test K-02/J-01. K-03 không ghi hoặc khôi phục chúng. Các kết quả
unit là bằng chứng tại lần thu thập 62 file, không đóng băng WIP của chat khác
hoặc chứng nhận một release chung.
