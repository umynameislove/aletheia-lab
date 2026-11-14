# Nhập code Bu vào worktree API

- Remote kiểm ngày 09/11: main `2bbcdc2a7637c840babd8b71910306c64072bc4b`;
  `feat/product-experience-quan` là `d4e7fb214d3bb6a74b2ddf0f012bf69c7c3e8e7c`.
- Nhập tree diff 75 đường dẫn UI/docs, không tạo commit hoặc merge commit.
  75 đường dẫn được đối chiếu blob với commit Bu; HEAD API vẫn là `2bbcdc2`.
  Commit Bu và nhánh cũ vẫn được giữ. Lần commit/merge sau cần giữ provenance
  và xử lý lịch sử diverged, không hiểu việc áp patch này là đã merge ancestry.
- Trước khi nhập đã sao lưu và xác minh 165 file WIP:
  `../j01-bu-integration-20261109-01/api-wip-before.zip`;
  manifest và kết quả kiểm ở cùng thư mục. Ngay sau khi nhập, hash 165 file
  trước đó đều khớp. Sau đó có chủ động tách hàm trong
  `src/aletheia_lab/hosted_auth/csrf.py` để sửa C901; bản trước có trong backup.
- Kiểm kết hợp J-01/K-02/K-03 và product/bridge: 630 test Python đạt
  trước refactor CSRF. Sau refactor, 196 test CSRF/auth/adapter liên quan đạt;
  Ruff gồm C901 và mypy strict trên csrf.py đạt. Dữ liệu test tổng hợp và
  authority giả lập không chứng minh IdP/DB/staging thật.
- Lần cài frontend sạch trong worktree API bị ngắt khi kéo dài, để lại
  `dashboard/web/node_modules` chưa được xác nhận đầy đủ. Không coi nó là
  cài đặt thành công. Ở worktree kiểm commit Bu cùng lock trước đó, npm test
  201/201, TypeScript và Live build đạt; 12 browser Live test đạt với source
  tổng hợp. Chưa chạy lại frontend trên đúng worktree API sau nhập.
- Chưa commit/push; J-01 vẫn cần consumer v6 và lần chạy hosted chung. K-02,
  K-03 còn các kết nối và nghiệm thu được ghi trong handoff tương ứng.
