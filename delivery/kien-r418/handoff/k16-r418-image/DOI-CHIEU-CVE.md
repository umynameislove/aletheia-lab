# K16 — đối chiếu 11 CVE của image Trixie R4.18

Baseline `sha256:993fef459d85a4e6fa3a10e966e768749d3d6409dbc7e899be9df96cf7643cb0`. Trivy ghi 47 HIGH và 0 CRITICAL. Mỗi dòng dưới đây ghép các phát hiện theo CVE nguồn. Số dòng scanner được giữ trong báo cáo gốc, không đưa vào danh sách ignore.

| CVE | Số phát hiện | Bề mặt trong image baseline | Cách kiểm ở ứng viên |
|---|---:|---|---|
| [CVE-2025-69720](https://security-tracker.debian.org/tracker/CVE-2025-69720) | 4 | `infocmp` xử lý terminfo phá cấu trúc, package ncurses 6.5+20250216-2. | Xác minh phiên bản ncurses và quét inventory mới. |
| [CVE-2026-119373](https://security-tracker.debian.org/tracker/CVE-2026-119373) | 1 | SQLite FTS5, cấu hình detail=none, rowid âm và prefix MATCH. | Kiểm SQLite 3.54.1 đang được Python nạp và bản ghi scanner. |
| [CVE-2026-119374](https://security-tracker.debian.org/tracker/CVE-2026-119374) | 1 | SQLite FTS5 secure-delete và dữ liệu database đã chế tác. | Kiểm SQLite 3.54.1 đang được Python nạp và bản ghi scanner. |
| [CVE-2026-119379](https://security-tracker.debian.org/tracker/CVE-2026-119379) | 1 | SQLite session extension xử lý changeset đã chế tác. | Kiểm SQLite 3.54.1 đang được Python nạp và bản ghi scanner. |
| [CVE-2026-16742](https://security-tracker.debian.org/tracker/CVE-2026-16742) | 2 | Scanner gắn với libsystemd và libudev. Dịch vụ bị ảnh hưởng là systemd-homed. | Kiểm inventory và không có lệnh homed/homectl trong image ứng viên. |
| [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369) | 1 | libacl xử lý đường dẫn có thành phần do đối tượng cục bộ kiểm soát dưới caller đặc quyền. | Kiểm inventory và capability root thực tế. |
| [CVE-2026-76642](https://security-tracker.debian.org/tracker/CVE-2026-76642) | 9 | util-linux mount và đường dẫn thực thi hook khi có quyền. | Kiểm phiên bản thành phần util-linux và không có lệnh mount trong PATH của ứng viên. |
| [CVE-2026-78408](https://security-tracker.debian.org/tracker/CVE-2026-78408) | 9 | nsenter của util-linux với quyền và tùy chọn join cgroup. | Ứng viên có lệnh nsenter trỏ vào BusyBox. Inventory và scan kiểm thành phần util-linux. Capability bằng 0. |
| [CVE-2026-78409](https://security-tracker.debian.org/tracker/CVE-2026-78409) | 9 | Mount được fstab cho phép, fast path X-mount.subdir. | Kiểm inventory, lệnh mount và profile quyền. |
| [CVE-2026-78410](https://security-tracker.debian.org/tracker/CVE-2026-78410) | 9 | Bind mount được fstab cho phép, dữ liệu đường dẫn do đối tượng cục bộ kiểm soát. | Kiểm inventory, lệnh mount và profile quyền. |
| [CVE-2026-9538](https://security-tracker.debian.org/tracker/CVE-2026-9538) | 1 | Scanner gắn với perl-base, thành phần bị ảnh hưởng là Archive::Tar. | Kiểm không có perl trong PATH và inventory không có package nguồn bị ảnh hưởng. |

Debian Security Tracker ngày 14/11/2026 vẫn ghi các CVE trên còn ảnh hưởng Trixie. Các nhánh Debian khác có package đã vá theo từng advisory. Ứng viên cục bộ dùng [Wolfi](https://edu.chainguard.dev/open-source/wolfi/wolfi-with-dockerfiles/) và package được ký, tránh trộn kho Debian. [Recipe SQLite của Wolfi](https://github.com/wolfi-dev/os/blob/main/sqlite.yaml) ghi SQLite 3.54.1. Kết quả có hiệu lực với đúng image ID được quét và profile triển khai đã kiểm.

Trivy nhận diện 34 gói Wolfi và 53 gói Python. Cả 11 CVE trên vắng trong báo cáo của ứng viên. Không có mức HIGH hay CRITICAL mới. Dữ liệu scan và SBOM được niêm phong cùng receipt.
