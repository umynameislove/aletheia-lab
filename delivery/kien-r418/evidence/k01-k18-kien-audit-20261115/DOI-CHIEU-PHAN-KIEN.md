# Đối chiếu phần việc Kiên — K01 tới K18

Kiểm theo HEAD `fa46dd8cbb2239c6f116456894a2009b45846245` ngày 15/11/2026. CI của SHA này **11/11 PASS**. Đây là bảng đối chiếu công việc, không phải báo cáo nghiệm thu tổng cho Bảo.

**Trong phạm vi Kiên: 76/91 mục đã đạt, 15/91 mục còn mở; tương ứng 13/18 nhóm không còn việc cá nhân và 5/18 nhóm còn việc.** Bộ gốc có 92 mục con; một mục bàn giao/xác nhận liên bên được loại khỏi danh sách theo yêu cầu.

Đạt ở đây là phạm vi kỹ thuật cá nhân/local/CI đã có chứng cứ, không tự nâng thành nghiệm thu production/staging chung. Các mục còn mở chủ yếu thiếu triển khai, dữ liệu/tải primary và diễn tập vận hành; không có 15 lỗi code/CI đang đỏ.

| Nhóm | Đạt / mục Kiên được xét | Còn mở |
|---|---:|---:|
| K01–K13 | 66/66 | 0 |
| K14 | 3/5 | 2 |
| K15 | 2/4 | 2 |
| K16 | 3/5 | 2 |
| K17 | 0/6 | 6 |
| K18 | 2/5 | 3 |

## 15 mục còn thuộc Kiên

| Mục | Việc còn thiếu |
|---|---|
| K-14.01 — Ngân sách process và pool | Đo trên topology triển khai đã khai báo: toàn bộ replica/process/pool, CPU theo operation, RAM/FD/NIC/ingress và dự phòng health/admin. Giữ nguyên kết quả local 2 API + 2 worker đã đạt. |
| K-14.05 — Fairness và burst | Đo fairness/burst với workload S/M/L của môi trường triển khai; chứng minh tenant nền, retry, queue và deadline trong envelope. Không dùng debug local nhỏ thay capacity thực. |
| K-15.03 — Quyền và scope của stream | Kiểm phía server/proxy/multi-replica khi revoke trong lúc có update/response đang chờ; dữ liệu, cursor và buffer không vượt quyền, mục tiêu phản ánh ≤5 giây. Loại phần UI/BFCache của Quân. |
| K-15.04 — Ngân sách reconnect | Chạy mất TLS/network, proxy idle timeout và reconnect đồng loạt qua nhiều replica; đo connection/FD, request fanout, retry và khớp job ledger. Fresh TLS transport đã pass không thay một sự cố socket thật. |
| K-16.03 — Threat model và biện pháp kiểm soát | Kiểm hardening trên deployment thật: edge TLS/security headers, quyền secret/collector và abuse theo tải được công bố. IdP/MFA local K03 và image local đã pass được giữ nguyên. |
| K-16.05 — Alert và runbook | Triển khai và thử collection/routing cảnh báo thật, tới đầu mối trực; lưu bằng chứng trigger, hành động/runbook và kiểm cardinality. |
| K-17.01 — Adapter cho seed và import | Hoàn thiện adapter/seed và import dữ liệu primary vật lý: 50.000 principal, 2.000 workspace, 10.000 project, 200.000 snapshot, 2.000.000 result; S/M/L đạt 100/1.000/10.000 claim, L ≥10.000 node, đủ hash/nguồn. |
| K-17.02 — Harness từ hành vi tới tải | Hoàn thiện các hành trình còn thiếu trong harness tải và đối chiếu count scheduled/started/completed/waiting/idle/drop/reject; logout/renewal/upload/confirm/refresh/cancel/delete/membership/invite/settings phải được thực thi, không chỉ khai báo disposition. |
| K-17.03 — Đánh giá generator và tài nguyên | Hiệu chuẩn máy phát tải riêng; đo CPU/RAM/FD/NIC/clock cùng cửa sổ và chứng minh generator không là nút thắt. |
| K-17.04 — Normal, stress, spike và soak | Chạy primary normal 3 closed + 3 open, các mức 5k/10k/25k/50k, stress/spike/soak 2 giờ (8 giờ khi có căn cứ), giữ đủ mẫu số lỗi/timeout và SLO. |
| K-17.05 — Liên kết DB, fault và tải | Chạy DB40/F26 gắn với tải thực: fault quanh commit/ACK/lease/quyền, telemetry đồng thời, reconciliation effect; tách tier process khỏi storage. |
| K-17.06 — Báo cáo capacity và chi phí | Từ envelope đã đo đạt, lập capacity/cost/headroom thực tế cho infra/DB/object/bandwidth/generator/IdP/provider; không ngoại suy 10k lên 50k. |
| K-18.01 — Release có thể tái dựng | Đóng gói/build image và release theo SHA cuối, lưu digest/lock/SBOM/config identity, kiểm API/worker/volume/secret/TLS và canary IdP trên môi trường triển khai. Không liệt kê xác nhận rebuild của Quân. |
| K-18.03 — Readiness, drain và rollout | Thử rollout/upgrade/rollback image nhiều replica qua ingress: readiness, drain job đang nhận, worker cũ bị fencing và đủ tài nguyên dự phòng. |
| K-18.04 — Backup, PITR và object | Thử host-loss và khôi phục DB/WAL/object/authority-checkpoint từ bản mã hóa ngoài host, trên dataset đích; đo RPO ≤5 phút, RTO ≤30 phút. PITR local đã pass giữ đúng phạm vi. |

## Phần đã đạt được giữ nguyên

- K01/K02: 9/9 cá nhân đã có CI. K03: 6/6 theo phạm vi kỹ thuật cá nhân cùng CI mới; raw scenario 16 vẫn giữ trạng thái failed toàn lượt, chỉ nhận slice đã hoàn tất, quota slice 17 riêng đạt.
- K07 race và K09.03 đã sửa và có CI; không mở lại.
- Windows K14 mới có **20 passed/53,81 s**; không dùng timeout cũ của R4.18 để gán lỗi hiện tại. K14.01/.05 đã đạt local 2 API + 2 worker; cổng deployment và CPU theo operation vẫn mở.
- K16.04: giữ kết quả **image đã quét** 0 HIGH/0 CRITICAL. Image đó gắn R4.18 cũ; đóng gói/image của SHA phát hành cuối được xét ở K18.01, không tự xem scan cũ áp dụng cho digest mới.
- K18 migration/process recovery/PITR local đã có kết quả. Chúng không chứng minh host-loss/off-host DR hoặc rollout image nhiều replica.
- K17 đã có tool/proposal/calibration và một phần recipe; **0/6** là số mục đã đóng theo tiêu chí primary, không phải chưa làm gì. Quyết định/phê duyệt từ người khác không được liệt kê thành việc Kiên phải xác nhận.

Danh sách không bao gồm phần UI/BFCache/peer signing do Quân xác nhận. Không chạy lại test, sửa code, commit/push hoặc xuất nghiệm thu cho Bảo trong lần rà này.

`doi-chieu-92-muc.csv` giữ đủ 92 hàng và trạng thái được đối chiếu; `audit.json` ghi phép đếm cùng đường dẫn/hash bằng chứng.
