# Phương pháp bổ sung bằng chứng K03

Phạm vi: bốn mục cá nhân còn thiếu bằng chứng chạy thật: K03.02, K03.03,
K03.05 và K03.06. K03.01 và K03.04 đã đạt; không sửa lại mã đã đạt.
Chỉ sửa URL post-logout do backend tạo: bỏ fragment, dùng origin app cộng `/`
để đăng ký chính xác được trên Keycloak. Sửa kiểm thử hồi quy và hướng dẫn liên
quan. Không sửa UI, hợp đồng API, migration hoặc chính sách thời gian.

## Môi trường và đường đi thực tế

- Máy Windows của Kiên; Python 3.11.9, Chrome headless 157.0.8103.52.
  Phiên bản thư viện thực tế được lưu ở `baseline.json`.
- Một PostgreSQL 16.15 riêng, Keycloak 26.8.2 riêng và Mailpit v1.33.1 riêng.
  Chỉ dùng image cache đã được fixture kiểm, không dùng IdP công khai.
  Image identity thực tế nằm trong `environment.json` của từng lượt chạy.
- PostgreSQL giới hạn 512 MiB, 1 CPU, 128 PID và `max_connections=24`.
  Keycloak giới hạn 1 GiB/2 CPU; Mailpit 128 MiB/1 CPU.
  Đây là giới hạn fixture, không phải số đo RAM/CPU hoặc chứng nhận sức tải.
- Cổng ngẫu nhiên chỉ bind loopback. API dùng `https://127.0.0.1:<cổng>`;
  Keycloak dùng `https://localhost:<cổng>` để cookie host không trộn nhau.
  Mailpit HTTP chỉ là hộp thư thử riêng trên loopback.
- Chạy entry point thật `aletheia_lab.hosted.server`, qua `ManagedRuntime`,
  với `environment="staging"`; không gọi trực tiếp LoginCoordinator để thay
  cho callback HTTP. API gọi IdP discovery/JWKS/token endpoint thật qua TLS.
- API và PostgreSQL xác minh TLS; DB dùng `sslmode=verify-full` và CA của
  fixture. API role riêng không superuser/không BYPASSRLS, migrate/grant bằng
  kết nối chủ riêng. Workspace và owner được tạo trong cùng transaction.
- UI hosted được build từ source hiện tại vào thư mục tạm; không ghi đè build
  đang có. Chrome dùng profile riêng và chỉ pin SPKI của hai chứng thư fixture;
  không tắt xác minh mọi chứng thư và không thay trust store của máy người dùng.
- API một process, pool tối đa 8; mẫu cấu hình giữ connection budget 16,
  admin reserve 2, concurrency limit 64, backlog 128, pool wait 5 giây,
  backend timeout 5 giây và request timeout 30 giây. Không tăng deadline.

Môi trường trên là staging cô lập chạy trên máy Kiên, được dựng để kiểm tra
chức năng. Nó không phải chứng nhận một staging dùng chung đã triển khai lên
hạ tầng của nhóm, cũng không thay người nhận xác nhận nghiệm thu liên bên.

## Chính sách thực sự được kiểm

- OIDC public client, Authorization Code + PKCE S256; callback URI chính xác;
  thư viện Authlib/joserfc hiện có, discovery và chữ ký thật từ Keycloak.
- Lượt cuối bật tùy chọn có sẵn `ALETHEIA_HOSTED_IDP_LOGOUT=enabled` trong
  fixture và đăng ký đúng `origin/` tại client của realm thử.
  Sign out đi qua trang xác nhận logout thật của Keycloak rồi quay về app.
  URL cũ có fragment bị Keycloak 26.8.2 từ chối đăng ký chính xác; lượt cuối
  không dùng wildcard để bỏ qua lỗi này. Không thay cookie IdP bằng thao tác xóa cookie của driver. Cơ chế xác nhận
  và tham số này được đối chiếu với [hướng dẫn Keycloak](https://www.keycloak.org/securing-apps/oidc-layers).
- Hai subject khác nhau, hai identity binding được provision rõ ràng, hai
  workspace riêng. Email dùng miền thử `.invalid`.
- Browser flow bắt buộc mật khẩu và OTP. Mỗi lượt đăng nhập thử OTP sai trước
  rồi OTP đúng. Chờ bước TOTP mới thật để tránh dùng lại mã OTP đã tiêu thụ.
- `required_acr="1"` là mapping của realm thử; việc MFA bắt buộc được quan sát
  trực tiếp bằng các màn mật khẩu/OTP và không có phiên mới trước OTP đúng.
  Không diễn giải riêng chuỗi ACR này thành cấp bảo đảm production đã được chọn.
- Reset mật khẩu qua email SMTP thật vào Mailpit, giữ nguyên factor OTP ban đầu.
  Không thêm chức năng tự thay OTP khi mất factor. Đây là policy retained-factor
  đã có trong fixture hiện hành, không tự quyết định quy trình mất thiết bị.
- Idle 900 giây, absolute 28.800 giây, rotation 300 giây, giao dịch login 300
  giây, clock skew 30 giây. Lượt mới chờ tự nhiên mốc rotation 300 giây.
  Không gọi thời hạn absolute 8 giờ là một bài soak 8 giờ đã chạy.
- IdP timeout 3 giây, tối đa 2 trao đổi đồng thời, backoff đầu 1 giây/tối đa
  30 giây; không thêm retry. Thời gian callback HTTP đo bằng monotonic clock
  bao gồm cả vận chuyển và xử lý HTTP, có thể lớn hơn riêng timeout IdP.
- Auth admission giữ 30 yêu cầu/nguồn/60 giây và global 600/60 giây theo từng
  operation. Key nguồn do server suy ra từ peer, không do client tự khai.

## Kịch bản và tiêu chí đạt

1. API sẵn sàng qua HTTPS và kiểm phụ thuộc đúng staging. Anonymous trả 401;
   auth start cross-site và return-to ngoài allowlist trả 400.
2. Mật khẩu/OTP thật qua IdP, callback HTTP tạo cookie Secure/HttpOnly/
   SameSite=Lax/host-only. Callback dùng lại không tạo thêm session.
3. Mutation thiếu CSRF trả 403 và không đăng xuất phiên hợp lệ. Đây là kiểm
   kết nối của lượt mới, không sửa lại phần CSRF đã đạt.
4. A/B có subject và session khác nhau. Logout-all A vô hiệu hóa A trên server
   trong khi B vẫn trả 200.
5. Tạo code đăng nhập hợp lệ rồi pause đúng container Keycloak của lượt đó;
   gửi một callback, đòi `idp_unavailable`, no-store, không tạo session và
   `/health/live` vẫn 200. Luôn unpause trong finally; đăng nhập mới sau phục hồi
   phải thành công. Không dùng stop/start làm đổi cổng và tạo lỗi kết nối giả.
6. Reset link TTL 2 giây, chờ 5 giây rồi thử phải bị từ chối. Reset hợp lệ TTL
   120 giây không cấp session tự động, không tạo factor OTP mới. Replay cùng
   browser và browser sạch đều bị từ chối; mật khẩu cũ bị từ chối; mật khẩu
   mới vẫn cần OTP cũ và quay về đúng subject A.
7. Gọi đúng thao tác operator `PostgresSessionStore.revoke_identity` trong
   transaction riêng. A phải 401 trong 5 giây, B vẫn 200. Đây là thu hồi do
   operator tin cậy; không tuyên bố có webhook IdP tự động.
8. Disable tài khoản tại IdP thì không đi qua được OTP/callback để tạo phiên.
   Khôi phục trạng thái account chỉ trên realm thử của chính lượt chạy.
9. Chrome đăng nhập thật, kiểm cookie/storage; mở tab thứ hai; bấm chức năng
   Sign out all sessions hiện có. Tab thứ hai phải bỏ workspace A và API401
   trong 5 giây. Sau đó đăng nhập lại A bằng MFA.
10. Dùng nút Sign out thật của sản phẩm và xác nhận logout trên IdP, sau đó
    dùng Sign in để đăng nhập B bằng MFA; phải trở về đúng B, không hiện
    workspace A. Đây là luồng đổi account qua logout/login được sản phẩm hỗ trợ
    khi bật đăng xuất IdP. Không thêm link/nút giả vào DOM trong lượt cuối.
    Các lượt trước với IdP logout bị tắt bị Keycloak từ chối đổi user trong SSO
    của A; được giữ nguyên là lượt không đạt, không gọi đó là lỗi backend đã sửa.
11. Điều hướng route workspace sau chuyển account phải vẫn là B. Đây không
    phải phép đo browser BFCache/back-forward đầy đủ.
12. Với session cấp bởi IdP thật, early renew trả 200 nhưng không rotate. Đọc
    thời điểm DB, chờ tự nhiên tới `rotated_at + 301 giây`, gửi 12 renew HTTP
    đồng thời, cùng cookie/CSRF cũ. Đòi đúng 1 thành công, 11 bị 401/409, DB
    generation tăng đúng 1; cookie cũ 401, cookie mới 200 đúng A.
13. Gửi 35 auth-start tuần tự trên nguồn loopback, không follow
    redirect/retry. Đòi chỉ 303/429, có ít nhất 5 lần 429 với Retry-After dương,
    no-store và không có session. Không phát tải vào IdP công khai.
    Bản thử 4 đồng thời trước đó gặp 503, phù hợp giới hạn IdP 2 inflight;
    không có trace đủ để quy mọi 503 cho một nguyên nhân. Lượt đó giữ nguyên là không đạt tiêu chí quota, không tăng giới hạn IdP hay
    nới assertion để đổi thành pass. Bài quota tuần tự tách riêng giới hạn số
    lần gọi; bài 12 renew đồng thời ở bước 12 kiểm tranh chấp refresh.

Hai bài 12 renew/35 auth-start là thử tranh chấp và giới hạn có chủ đích,
không phải phép đo RPS tối đa, số người dùng đồng thời hay bài 50.000 người.

## Bằng chứng, bảo toàn và chạy lại

`cases.jsonl`/`result.json` ghi từng ca và thời gian thực đo;
`browser-outcomes.jsonl` ghi riêng các ca Chrome. Không cộng các ca Chrome
vào số scenario của Python như những phép kiểm độc lập không chồng lấn.
`inputs-before.json` và `inputs-after.json` kiểm hash source/config/UI, unit test
và các driver K03 mới. Helper HTTPS ở integration, helper CDP gốc, schema-admin
và lockfile còn được ràng buộc qua Git HEAD và kiểm không có diff theo dõi;
không diễn giải manifest này thành toàn bộ dependency/runtime của hệ điều hành.
Mọi lượt lỗi được giữ, không đổi nhãn
thành pass. Lỗi trình tự/fixture được phân biệt với lỗi backend.

Mỗi lượt chỉ xóa container/network/volume/thư mục tạm do chính lượt tạo và có
kiểm identity/nhãn sở hữu. Không dừng container hoặc Chrome của công việc khác.
Không lưu mật khẩu, OTP seed, cookie, callback code, private key hay DSN vào gói
bằng chứng; log API được so với secret đang dùng trước khi xuất.

Để lặp lại trên máy đã có đủ dependency, Chrome, Docker và image cache: chạy
`delivery/kien-r418/tools/k03_real_http_acceptance.py --output <thư-mục-mới>`
bằng Python có hosted/test dependencies, với `PYTHONUTF8=1` và
`PYTHONDONTWRITEBYTECODE=1`. Thư mục output phải chưa tồn tại. Driver hiện dùng
Chrome tại đường dẫn chuẩn Windows; không coi driver này là công cụ triển khai.

Khi các bước còn lại đã có bằng chứng hợp lệ, dùng `--auth-abuse-only` để chỉ
chạy phần quota còn thiếu cùng preflight cần thiết, tránh chạy lại MFA và chờ
rotation đã đạt. Báo cáo kết quả chỉ rõ từng slice; một lượt dừng ở assertion
cuối không được đổi nhãn thành toàn bộ lượt chạy thành công.
