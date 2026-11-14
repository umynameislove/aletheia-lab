# Phản hồi Bu về J-01 v5

Nguồn: `Gui-Kien-J01-v5-tra-loi.txt` do Kiên cung cấp. Phần consumer
v5 dưới đây là **kết quả Bu báo**, chạy trên thay đổi chưa push; không coi
commit `d4e7fb2` là bản chứa consumer v5.

- Bu báo 10 Problem, Session, Capabilities, receipt, job, page và các ca
  sai scope/status/version đã kiểm trên fixture v5; chưa kiểm HTTP/staging.
- Bu xác nhận phân công: Kiên sửa schema/fixture, Quân giữ test consumer;
  thay đổi shape cần báo trước. Bu nói sẽ merge main vào nhánh mình và push
  consumer v5 sau. Remote hiện kiểm được vẫn tại `d4e7fb2`.
- Hai layout được Bu chỉ ra qua URL demo; Bảo chưa duyệt và chưa có bằng chứng
  hai bên cùng chốt. Workload/fanout và số đo browser là báo cáo của Bu;
  journey mix, role, think time, số tab chưa có.
- Staging: Bu chưa có URL/người giữ identity/storage; đề xuất Bảo chỉ định
  người giữ và cấp quyền, Kiên dựng backend, Quân chạy UI.
- Đồng ý 412/428 và jobs.idempotency chỉ bật khi backend thật hỗ trợ bền.
  question_chars 2000 vẫn cần Bảo chấp nhận so với 4000 trong brief.

## Xử lý producer

V6 đã bổ sung điều kiện total_kind/total. V7 thêm pattern status_url tương đối
và minLength cho cursor; không đổi trường payload. Kiểm độc lập với
jsonschema + FormatChecker: 15 fixture đạt; các negative về status, ngày,
extension, total_kind, cursor rỗng và URL tuyệt đối bị từ chối. Pattern URL
không chứng minh workspace/job khớp các trường khác: validator runtime và
consumer phải kiểm so sánh đó. Pattern ngày không thay validator lịch.

Receipt `queued` hiện là **ảnh lúc chấp nhận**, không là trạng thái hiện tại;
client phải đọc status_url. Quy tắc này còn chờ Bu xác nhận. Các đề xuất
thêm operation, timestamps, result/failure và schema từng loại item là thay
đổi shape; cần thiết kế và báo Bu trước khi thay. Không tuyên bố job center
đã đủ dữ liệu hoặc K-09 đã hoạt động. Bảng mã nghiệp vụ đầy đủ cần inventory
và chốt theo từng operation, không tự đoán status.
