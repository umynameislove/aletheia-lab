# J-01 Job paging và revision đề xuất

Trạng thái: **dự thảo để đối chiếu consumer**, không phải endpoint hoặc schema đã chạy. Contract chung `product-api/v1-draft` đang được đề xuất; gói giao việc có ví dụ `product-api/v2-proposal`, cần hai bên chốt version trước khi triển khai. Mọi ID và payload dưới đây là tổng hợp.

## Job receipt và trạng thái

Sau khi input và job được commit bền, API dự kiến trả 202 với receipt:

```json
{"schema_version":"product-api/v1-draft","job_id":"job_synthetic_001","status":"queued","workspace_id":"ws_synthetic_001","status_url":"/v1/workspaces/ws_synthetic_001/jobs/job_synthetic_001","request_id":"req_synthetic_001"}
```

202 chỉ ghi nhận job đã được nhận bền, không khẳng định result đã hoàn thành. Trạng thái dự kiến: `queued`, `running`, `cancel_requested`, `succeeded`, `failed`, `cancelled`, `expired`. Cancel có thể thua commit đã hoàn tất; UI hiển thị outcome thực. Progress chưa biết là `null`, không giả 100%. Job status/list phải kiểm session, membership và scope; chi tiết request/result và lưu trữ còn cần thiết kế.

Idempotency dự kiến theo actor, workspace, operation và key; cùng key/cùng request trả cùng receipt, cùng key/request khác trả 409 không tạo effect thứ hai. Cần chốt thời gian giữ và cách khôi phục lost ACK, kiểm qua storage thật.

## Page

Ví dụ hình dạng để review, chưa chốt tên trường hoặc API cụ thể:

```json
{"schema_version":"product-api/v1-draft","workspace_id":"ws_synthetic_001","items":[],"next_cursor":null,"total":null,"total_kind":"unknown"}
```

Cursor dự kiến opaque và gắn scope/query/snapshot theo contract thực. `null` là chưa biết, không tự đổi thành 0. Default 50, tối đa 200 là đề xuất trong gói giao việc, chưa chứng minh giới hạn deployment. Cần thống nhất quy tắc exact/estimated/unknown và test sai scope, thay query, hết hạn và dữ liệu đổi.

## Revision và conflict

Ví dụ revision `12` trong Capabilities chỉ là dữ liệu mẫu. Với mutation cần chống lost update, dự kiến gửi ETag strong qua `If-Match` và nhận `412 revision_conflict` khi stale, hoặc `428 precondition_required` khi thiếu điều kiện bắt buộc; client giữ draft. Chưa có bằng chứng CAS, transaction hoặc quyền đã hoạt động.

## Cần Quân kiểm

Review shape, version, null, scope, queued/terminal, lost ACK và conflict/draft. Sau khi có serializer thật, hai bên dùng cùng fixture và kiểm sai version/trường/scope; không duy trì hai DTO độc lập. Routes auth start/callback, tạo workspace, invite accept/list/revoke và list job đang chạy cần được định nghĩa cùng flow thực, chưa có trong tài liệu này.
