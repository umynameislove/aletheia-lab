# J-01 — kiểm độc lập và cách tái lập

Nguồn API: base `2bbcdc2a7637c840babd8b71910306c64072bc4b` cộng WIP trên
`feat/product-api`. Consumer đã kiểm: Quân `d4e7fb214d3bb6a74b2ddf0f012bf69c7c3e8e7c`.
Không có commit mới hoặc deployment mới từ lượt này.

## Producer và schema

Windows, Python 3.12.15:

- 118 test contract J-01 và adapter K-02 liên quan đạt.
- Ruff kiểm package hosted_contract, hai script J-01 và hai test contract đạt.
- `jsonschema[format]==4.25.1` cài vào thư mục dependency riêng trong workspace,
  không thay dependency ở venv đang dùng. Trong sandbox, cần chạy với quyền đọc
  được dependency đó; đây là hạn chế môi trường, không phải schema failure.
- `scripts/check_j01_schema.py fixtures/j01/producer-review-v6` đạt với
  Draft 2020-12 + FormatChecker: 15 payload hợp lệ; 10 cặp mã/status sai bị
  từ chối; ba ngày giờ sai bị từ chối; producer từ chối extension còn
  consumer nhận extension; total unknown chỉ nhận null.
- Scope request, quyền, HTTP status thực tế, URL receipt và timezone/calendar
  vẫn phải kiểm ở runtime. Không suy ra quyền từ JSON Schema.

Sau khi cài dev dependencies trong môi trường dành riêng cho project,
từ thư mục worktree có thể tái lập:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'
# $J01Python là python.exe trong môi trường đã cài .[dev].
& $J01Python -B scripts/check_j01_schema.py fixtures/j01/producer-review-v6
& $J01Python -B -m pytest tests/unit/test_j01_session_capabilities_contract.py tests/unit/test_j01_job_page_contract.py tests/unit/test_k02_hosted_application.py tests/unit/test_k02_request_boundary.py tests/unit/test_k02_json_resource_limits.py -o addopts='' -q -p no:cacheprovider
```

Exporter dùng thư mục output mới và từ chối ghi đè. V5 giữ nguyên; v6 cập nhật
schema Page để biểu diễn quan hệ total/total_kind vốn đã được model kiểm.
V6 không thay payload mẫu hoặc tự chuyển version thành product-api/v1.

## Browser Live local

Đã chạy test Live của Quân với Chrome headless và bridge Python thật:
12 pass, 0 fail. Chỉ sửa setup trong bản sao script để gọi Vite qua Node trên
Windows và dùng config loader runner; không sửa code hay assertions của Quân.
Temp/store/source nằm trong workspace riêng và được test cleanup; logs/script
được giữ ở `../j01-live-review-fcdb81c5534f4944ab262a55cdf2e1da/`.

Các ca gồm import/mapping, response đến muộn/lost ACK, source thay đổi,
analysis và UI hiển thị, paging/Unicode/graph. Một số ca rewrite response
để kiểm browser nên không coi tất cả dữ liệu là producer thật. Đây là local
loopback, không có login hoặc membership hosted. Không thay bằng chứng
J-01.03 staging. Các test browser demo/focus khác chưa chạy trong lượt này.

## Trạng thái môi trường

Docker CLI được tìm thấy nhưng Docker engine không kết nối được. Chưa nhận
cấu hình IdP/hosting hoặc DB/object storage của staging. Runbook và inventory
đầu vào ở `j01-staging-acceptance-runbook.md`; phép tính và phần cần đo ở
`j01-workload-resources-proposal.md`. Chưa nhận capacity hoặc SLO là đạt.
