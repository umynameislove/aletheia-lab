# K16 R4.18 — kết quả kiểm tra image local

**Cổng lỗ hổng image đã pass trên ứng viên Linux/amd64.** Trivy 0.76.2 quét đầy đủ 34 gói hệ điều hành và 53 gói Python, ghi **0 CRITICAL, 0 HIGH và 0 phát hiện ở các mức khác**. Image Trixie gốc có 47 HIGH từ 11 CVE. Không có rule ignore, VEX hoặc bộ lọc severity. DB Trivy cập nhật 14/11/2026 08:05:40 giờ Việt Nam, quét offline lúc khoảng 14:34. SBOM CycloneDX có 88 thành phần.

| Bằng chứng | Giá trị |
|---|---|
| Source identity | `65c87fdb0a43e1ef465a1eb27901373a0f4f3d3a4a56da13e5b7b34341b56c58` |
| Runtime identity | `a046bccd9a0beb135951a0928c7ddd131b327302bcacf10c7ddb979757c9d495` |
| Image Linux/amd64 đã quét | `sha256:0f1ffb0bef45044451af1cd1b905e91587f87f284135eeac2bb4db894c26b81a` |
| Image Trixie gốc | `sha256:993fef459d85a4e6fa3a10e966e768749d3d6409dbc7e899be9df96cf7643cb0` |
| Nền Wolfi | `sha256:1f6b749929164c3d9e7cad0289254f39b2ba51e1dddaeb0fa57532ea03f2db31` |
| Công thức đã dùng | `Dockerfile.offline` dùng đúng image gốc làm nguồn package Python, bỏ công cụ build khỏi venv đã sao chép, chuyển venv sang Python 3.12.16 của Wolfi. |
| Runtime | 676 SHA-256 khớp manifest và 52 pin dependency khớp lock. Python 3.12.16, SQLite 3.54.1, OpenSSL 4.0.3. Trivy quét cả package được giữ ngoài venv. |
| Cách ly | UID/GID 10001, root chỉ đọc, CapEff/CapPrm/CapBnd bằng 0, NoNewPrivs=1, seccomp=2. API và worker đều fail closed khi thiếu secret. |
| Tương thích | Import psycopg binary, PyArrow, pandas, cryptography, scikit-learn. Chạy 4 child thật và nhận JSON 31.934 B, MD 32.504 B, PDF 36.059 B, khớp từng byte với receipt wheel R4.18. |
| Khởi động local | Entrypoint thật API và worker, PostgreSQL 16 fixture riêng, secret riêng từng role, TLS xác minh CA, OIDC discovery/JWKS synthetic. API readiness/liveness 200, Host sai 400, phiên ẩn danh 401, worker có 1 kết nối DB bằng role riêng. UID 10001 ghi/đọc mount dữ liệu thử. Lượt pass 15,472 giây. |

Tệp `Dockerfile` là lượt thử cài lại từ wheel. Lượt đó dừng tại bước tải NumPy, chưa chứng minh build thành công. Tệp `Dockerfile.offline` là công thức đã chạy thành công. Lượt offline đầu fail vì stage của image gốc chạy UID 10001 và không có quyền gỡ build tools khỏi venv. Đã sửa quyền riêng của stage build, container cuối vẫn UID 10001. Probe đầu đoán thiếu `nsenter`, được sửa sau khi xác định binary là BusyBox. Lượt khởi động đầu trùng lúc database đang start, lượt sau chờ `pg_isready` và pass. Giữ log các lượt.

## Dùng đúng image đã kiểm

Kiểm SHA-256 của tệp `Aletheia-R4.18-K16-linux-amd64.tar` theo file `.sha256`. Nạp bằng `docker load -i Aletheia-R4.18-K16-linux-amd64.tar`. Dùng tag `aletheia-hosted:r418-k16-wolfi-offline` trong biến `ALETHEIA_IMAGE` của Compose trên môi trường được phép, sau khi đối chiếu digest trong receipt. Chạy qua image thực với cấu hình staging của nhóm. Mỗi lần build mới phải kiểm lại inventory và scan theo digest mới vì các dependency bắc cầu của APK vẫn được giải từ repository tại thời điểm build.

## Còn cần để đóng K16 trên môi trường nhóm

K16.04 phần image local đã pass. K16.03 và K16.05 còn cần kết quả trên môi trường triển khai về **IdP và edge TLS/headers thật, quyền secret/collector, alert tới người trực và người khác thực hành runbook**. Chạy các ca abuse/fairness theo cấu hình tải đã thống nhất. Ca local trong image dùng discovery/JWKS synthetic và lưu dữ liệu trên tmpfs. Phạm vi xác minh không bao gồm IdP thật, collector của nhóm, nhận alert của người trực hay rollover dữ liệu khi thay container. Các trạng thái đó vẫn mở. Hồ sơ nghiệm thu chung cho Bảo chưa xuất.

Khi có môi trường, nhóm cung cấp image digest đã triển khai, Git SHA, timezone/thời điểm, cấu hình đã che secret, kết quả TLS/header và log hoặc receipt của cảnh báo tới người trực. Rà giấy phép khi phân phối image theo SBOM hệ điều hành mới. [Đối chiếu CVE](DOI-CHIEU-CVE.md).
