K13–K18: DỰNG LẠI VÀ VẬN HÀNH STAGING
Ngày chuẩn bị: 13/11/2026. Đọc cùng release manifest và trạng thái nghiệm thu.

1. Phạm vi và kết quả cần nhận

Đây là cấu hình triển khai thật có chặn cấu hình thiếu, không phải môi trường
đăng nhập dựng sẵn. Docker chưa được build/run trên máy Kiên theo yêu cầu.
Native PostgreSQL 16, HTTPS và worker đã có bằng chứng riêng; image/Linux,
IdP thật, triển khai chéo và tải 50k phải được kiểm trên môi trường được phép.
Không ghi PASS cho các gate đó dựa vào lint, YAML hay bài fixture nhỏ.
Bộ image/Compose này chỉ chứa API và worker. UI, public ingress và collector
không được dựng kèm: cần artifact frontend cùng RC từ Quân, phục vụ đúng
public_origin. Chỉ mở trang callback/API không thay cho nghiệm thu browser UI.

Mỗi lượt gửi lại: release/source/runtime SHA, image ID và RepoDigests,
docker version/compose version, cấu hình đã che secret, CPU/RAM/disk/FD,
lệnh + exit code + log gốc, thời gian UTC, JUnit/JSON và sự cố còn mở.
Không gửi password, DSN, cookie, CSRF, private key hoặc actor JSONL.

2. Máy và quyền cần có

- Windows 11 + WSL2 + Docker Desktop Linux containers, hoặc Linux Docker Engine.
- Python 3.12 và môi trường operator cài đúng wheel + dependency lock.
- PostgreSQL 16 riêng, TLS verify-full, owner migration riêng với API/worker.
- PG16 client pg_dump/pg_restore/pg_basebackup/pg_ctl cho operator. App image
  không có các chương trình này; không dùng client15 mặc định để dump PG16.
- IdP đã đăng ký đúng issuer/client/redirect, chứng chỉ CA được tin cậy.
- Object store persistent, private; backup/WAL/checkpoint ở storage độc lập
  được mã hóa do operator quản lý. HMAC xác thực, không mã hóa backup.
- Chốt dung lượng và ngân sách trước khi chạy; cấu hình 2CPU/2GiB mỗi container
  là điểm khởi đầu cần đo, không phải capacity đã xác nhận hay bảo đảm RSS.

3. Dựng image từ đúng source

Giải nén full source vào thư mục riêng, kiểm SHA256SUMS/release manifest trước.
Đứng ở repository root. Dùng release source SHA64 và SOURCE_DATE_EPOCH trong
manifest (không dùng ngày build tùy ý). Trong PowerShell:

  $release = '<source_identity_sha256 trong release manifest>'
  $epoch = '<source_date_epoch trong release manifest>'
  docker build --pull --progress=plain -f deploy/hosted/Dockerfile `
    --build-arg "ALETHEIA_RELEASE_ID=$release" `
    --build-arg "SOURCE_DATE_EPOCH=$epoch" -t "aletheia-hosted:$release" .
  if ($LASTEXITCODE -ne 0) { throw 'Build failed; stop and retain full log' }
  docker image inspect "aletheia-hosted:$release" > image-inspect.json

Hai stage dùng Python 3.12.16 trên Debian Trixie cùng manifest digest cố định.
Runtime ghim zlib1g 1:1.3.dfsg+really1.3.1-1+deb13u1 theo DSA-6613-1. 52 dependency
runtime, 2 build tool và 1 dependency build (packaging) có exact version + wheel
hashes từ metadata PyPI chính thức; build dùng only-binary và require-hashes, rồi
pip check. wheel được chốt ở 0.46.2 để sửa CVE-2026-24049; audit riêng cả 3
package build sau sửa không có finding đã biết tại thời điểm kiểm. Image build vẫn
cần bằng chứng riêng cho kiến trúc Linux được chọn. Pin source/deps không có
nghĩa image bit-for-bit đã được tái lập trên mọi kiến trúc; ghi image digest.
Trước phát hành phải quét OS packages và image cuối, không lấy pip-audit của
môi trường Mac thay cho image scan. Ghi SBOM/license review của image thực tế.

4. Chuẩn bị thư mục config/secret ngoài source và image

Copy deploy/hosted/config-template thành một thư mục config riêng. Giữ hai
file api.toml/worker.toml cùng public_origin, issuer/client/redirect, schema và
policy; expected_database_role lần lượt aletheia_api/aletheia_worker. Chốt đủ
public_origin, issuer, client_id, redirect_uri, client_kind; file mẫu bỏ trống
cố ý. object_root=/var/lib/aletheia; API bind0.0.0.0 port18766 trong container.
Auth policy phải có return paths đã duyệt và admission_namespace riêng.

Thư mục config chứa api.toml, worker.toml, auth.toml, api-ca.pem và CA DB/IdP
nếu dùng CA riêng. DSN runtime phải là PostgreSQL URL có sslmode=verify-full,
sslrootcert chỉ đường dẫn file CA BÊN TRONG container; options search_path đúng
schema đã xác nhận. Hostname DSN phải khớp chứng chỉ; không tắt TLS verification.

Thư mục secret chứa mỗi file đúng một giá trị, không có dấu ngoặc/ghi chú:
  database_api       URL role API, không phải owner
  database_worker    URL role worker, không phải owner
  csrf_secret        key ổn định cho cùng deployment
  admission_secret   key riêng cho admission
  login_proof_key    base64 của key ngẫu nhiên32byte
  tls_cert           PEM certificate + chain đúng public hostname
  tls_key            PEM private key

Config và secret dạng text phải là UTF-8 không BOM; PowerShell7 có
Set-Content -Encoding utf8NoBOM. Không dùng mặc định UTF-16 của PowerShell5.
Copy nguyên PEM certificate/key, không chuyển encoding. HMAC backup key là byte
ngẫu nhiên riêng, giữ nguyên byte; không xử lý nó như file text hoặc thêm BOM.

API và worker mount secret riêng theo Compose. Entry point đọc file rồi chuyển
giá trị vào môi trường tiến trình ứng dụng hiện tại; secret không nằm trong
Dockerfile/ARG/image. Người có quyền host/same-process vẫn có thể đọc; phải giữ
host và file ACL private. Compose file mounts không tự mã hóa secret trên host.
UID10001 phải đọc được secret/config và ghi được named volume; kiểm ACL thực tế
trên WSL/Windows. Không chmod777 secret hay source. Owner DSN và backup HMAC key
chỉ ở máy operator, không mount vào API/worker. Không bật shell tracing.

Nếu client confidential: đặt client_secret_env=ALETHEIA_HOSTED_OIDC_CLIENT_SECRET,
thêm secret oidc_client_secret vào service api (giữ toàn bộ list secret hiện có)
và top-level secrets file. Không chuyển public client sang confidential chỉ để
qua preflight. Quản lý key rotation, issuer binding và backup key theo runbook
IdP/deployment đã được duyệt; key không có trong gói bàn giao.

5. Tạo DB/schema/role và migrate bằng owner

DB admin tạo DB riêng, schema aletheia thuộc owner migration; owner không dùng
chạy API. Tạo hai LOGIN NOCREATEDB NOCREATEROLE NOSUPERUSER NOBYPASSRLS NOINHERIT,
không member role khác, không CREATE schema/database. Đặt password qua kênh
operator private. Ví dụ DDL không chứa password:

  CREATE ROLE aletheia_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
  CREATE ROLE aletheia_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
  CREATE ROLE aletheia_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
  CREATE DATABASE aletheia OWNER aletheia_owner;

Kết nối DB đó bằng owner, CREATE SCHEMA aletheia AUTHORIZATION aletheia_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC; không cấp default grants runtime.
Giữ owner URL trong private owner-url.txt (chmod600/ACL tương đương).

  python scripts/k18_schema_admin.py migrate --database-file <owner-url.txt> --schema aletheia
  python scripts/k18_schema_admin.py verify  --database-file <owner-url.txt> --schema aletheia

Lệnh này chỉ explicit migration, không tạo role/account hoặc gọi IdP. Timeout
lock1s/statement30s/whole120s: khi bị giữ lock phải rollback, giữ log, drain rồi
chạy lại; không nới vô hạn. Cài K03 admission riêng bằng admission_migrate_cli
theo docs/k03-admission-migration-runbook.md; provision issuer+subject/account
đúng quy trình K03. Không seed danh tính thử vào staging thật để lách đăng nhập.

Sau khi schema/policy/provisioning được duyệt, grant bằng owner:
  python scripts/k18_schema_admin.py grant --database-file <owner-url.txt> --schema aletheia --role aletheia_api --profile api
  python scripts/k18_schema_admin.py grant --database-file <owner-url.txt> --schema aletheia --role aletheia_worker --profile worker

Grant gồm CONNECT DB và known column-level profile, action audit bắt buộc.
Sau restore chỉ được làm bước grant SAU mục8, không chạy grant sớm.

6. Start/canary/drain

PowerShell, các đường dẫn phải tuyệt đối và Docker Desktop truy cập được:
  $env:ALETHEIA_IMAGE = "aletheia-hosted:$release"
  $env:ALETHEIA_CONFIG_DIR = '<absolute config folder>'
  $env:ALETHEIA_SECRET_DIR = '<absolute private secret folder>'
  docker compose -f deploy/hosted/compose.yaml config --quiet
  docker compose -f deploy/hosted/compose.yaml up -d
  docker compose -f deploy/hosted/compose.yaml ps
  docker compose -f deploy/hosted/compose.yaml logs --no-color > runtime-log.jsonl
  docker compose -f deploy/hosted/compose.yaml exec api /opt/venv/bin/python -m aletheia_lab.hosted.container_health

Health probe kiểm đúng instance qua loopback, CA+SNI+Host public đã cấu hình;
không verify=False. /health/ready kiểm dependency, /health/live chỉ process.
Không đưa traffic business vào instance chưa ready. Docker unhealthy không tự
restart; readiness overload không nên gây restart cả fleet. Uvicorn giới hạn
connection gồm keep-alive: dự phòng health trong app không đảm bảo được slot ở
ingress khi ingress đã đầy. Đo keep-alive/backlog/LB trước tăng replicas.

Canary bằng account thử được phép: đăng nhập thật, upload/preview/confirm,
analyze/follow-up, poll->terminal/output, tải đủ3format và kiểm hash, thu hồi
quyền->deny, delete->retained. Ghi current source/image/schema/client identities.
Không chỉ kiểm200 của /health để nghiệm thu business.

Trước nâng RC: dừng nhận mutation mới ở ingress, để download/job đang chạy drain,
gửi SIGTERM (Compose stop), chờ grace và ghi backlog/lease. Pool+job chưa xong
phải được phục hồi bằng fencing; force kill chỉ trong bài test riêng.

  docker compose -f deploy/hosted/compose.yaml stop

KHÔNG down -v, volume prune hay xóa object volume. Khi deploy nhiều replica,
tổng tất cả API/worker process×pool + admin reserve phải <=DB budget; chạy
scripts/k14_resource_plan.py với đúng số replica thực, không chỉ budget mỗi app.
Compose này một API/một worker; không hứa rolling HA hay50k bằng --scale.

7. Migration V14->V16 và rollback

V15 thêm retention/fences, V16 thêm job revision; review extension V3. DDL/checksum
V1–V14 và review V1/V2 giữ nguyên. Bài native có read traffic kiểm bytes và lịch
sử cũ không đổi. DDL vẫn lấy table lock ngắn, không hứa zero-lock/zero-downtime.
Old binary kiểm ledger strict sẽ không ready với version lạ: drain RC cũ trước
migrate, rồi start RC mới. Chỉ backward wire/DTO đã kiểm; không coi đó là xác nhận
hai binary có thể chạy rolling song song. Không chạy down DDL. Sự cố code dùng
forward fix; restore là quy trình offline riêng, cần checkpoint quyền/xóa mới
nhất. Backup trước nâng và giữ image/source/config/CA/key inventory ngoài host.

8. Backup logic + checkpoint mới nhất + restore

Đây là hai artifact độc lập: (a) bundle snapshot DB+object và (b) checkpoint
quyền/xóa cập nhật SAU snapshot, giữ ngoài bundle cũ. Đặt key HMAC ngẫu nhiên
>=32byte trong file private, tách storage và quyền truy cập. HMAC không mã hóa.
Backup scheduling, encryption, replica/WAL monitoring và off-host durability là
trách nhiệm operator; không có public endpoint backup/purge trong app.

  python scripts/k18_recovery_admin.py capture --database-file <owner-url.txt> --schema aletheia --object-root <managed catalog root> --bundle <NEW bundle dir> --pg-bin <PG16 bin> --key-file <HMAC key file>
  python scripts/k18_recovery_admin.py checkpoint --database-file <owner-url.txt> --schema aletheia --object-root <managed catalog root> --checkpoint <NEW checkpoint dir> --key-file <HMAC key file>
  python scripts/k18_recovery_admin.py verify --bundle <bundle dir> --key-file <HMAC key file>

Managed catalog root là <storage.object_root>/catalog, có .k05-object-store;
không đưa raw user folder vào. Bundle dùng exported snapshot + shared GC barrier,
stream hash/count và object closure; file DSN/key không nằm trong bundle.
Lưu manifest_sha256 của checkpoint MỚI NHẤT vào inventory tin cậy ngoài backup.
Chạy checkpoint theo RPO và sau revoke/delete quan trọng; nếu checkpoint lỗi/gap
không mở runtime restore. Giữ/delete checkpoint theo yêu cầu quyền/xóa và DR,
không tự xóa lịch tombstone vì backup cũ vẫn còn khả năng phục hồi.

Restore DB hoàn toàn MỚI thuộc owner, chưa có schema app/table/routine/defaultACL
hay grant role khác; không trỏ vào deployment đang chạy. Kết nối runtime bị chặn.
Tool từ chối target bẩn, REVOKE PUBLIC CONNECT/TEMP trước pg_restore, giữ DB đóng
với runtime trong toàn bộ cửa sổ chức năng SECURITY DEFINER và checkpoint.

  python scripts/k18_recovery_admin.py restore --database-file <NEW owner-url.txt> --bundle <bundle dir> --object-root <NEW managed catalog root> --pg-bin <PG16 bin> --key-file <HMAC key file> --checkpoint <LATEST checkpoint dir> --checkpoint-sha256 <LATEST trusted external manifest SHA256>

Nếu lỗi giữa chừng: target luôn giữ offline; giữ evidence, dựng target mới rồi
chạy lại. Không mở DB từ bundle cũ để "xem thử" bằng API. Lệnh restore kiểm HMAC,
DB/object hashes, schema/audit/policy/review; áp đủ chuỗi fence và quyền mới nhất;
revoke mọi session cũ; KHÔNG cấp runtime grants/CONNECT. Account/grant được tạo
sau backup mà thiếu ở DB cũ cần owner reprovision đúng current policy; không tự
khôi phục owner cũ để qua invariant. Không dùng old bearer/session để đăng nhập.

Operator tiếp tục kiểm nội dung/job/idempotency/tenant scope, deleted URL deny,
shared object còn đọc được, role/revoke đúng, source/backup retention trung thực.
Chỉ sau đó cấp CONNECT/profile bằng mục5 và start canary, đăng nhập mới qua IdP.
RTO kết thúc lúc consumer được phép dùng đúng, không phải lúc postgres chạy.

9. Physical PITR và process recovery

pg_dump logic không phải PITR. Physical DR cần base backup + chuỗi WAL liên tục
đến recovery target, object archive đầy đủ và checkpoint quyền/xóa mới nhất.
Đọc docs/k18-native-pitr-fixture.md để tái lập bài native fixture opt-in; fixture
tách2cluster, không nhắm PG đang dùng. Physical restore phải chặn mạng/pg_hba
runtime từ đầu vì physical backup mang theo role/grant; checkpoint áp trước khi
mở. Không lấy shared disk/cached local timing làm bằng chứng mất host/storage.
Mục tiêu staging: RPO<=5phút/RTO<=30phút phải đo với dataset/backup thực tế.

Process tier giữ nguyên DB/objects: docs/k18-process-recovery.md có actual API+
worker SIGKILL, SIGTERM/drain, stale-worker fencing, ACK replay và authorized
HTTPS consumer. RPO0/RTO<=60s trong fixture này không chứng minh chịu power loss,
host compromise, mất DB hay provider thật. Download/rolling/browser thật còn
cần lượt operator chung. Không xóa logs failed run khi rerun đạt.

10. Nguồn hướng dẫn đã đối chiếu

Docker digest/non-root/build: https://docs.docker.com/build/building/best-practices/
Compose file secrets: https://docs.docker.com/compose/how-tos/use-secrets/
PostgreSQL16 PITR: https://www.postgresql.org/docs/16/continuous-archiving.html
Registry image metadata/PyPI wheel hashes được giữ trong evidence K18 của RC.
