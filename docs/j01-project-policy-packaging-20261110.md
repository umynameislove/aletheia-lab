# J01/K04 — đóng gói project policy SQL, 10/11/2026

Đã thêm đúng `project_policy_schema.sql` vào package-data của
`aletheia_lab.hosted` trong pyproject.toml. Giữ entry workspace_schema.sql,
admission_schema.sql và mọi dependency/cấu hình khác. Không sửa SQL, loader,
contract, fixture v7, frontend hay migration K04. Không stage/commit/push/merge.

Chỉ một đoạn byte được thay trong pyproject.toml. Backup nguyên byte và receipt:
`pyproject-before-project-policy.toml`,
`project-policy-package-data-receipt.json` trong thư mục bằng chứng này.

| File | SHA256 đã kiểm |
|---|---|
| pyproject.toml trước | 000643d06e4f7a037840b1698624bc1a05564001541a23a77ffe8022af2f5c60 |
| pyproject.toml sau | 50d3f5b3c7152d56afaf1289424ea7107d4b61860a85d4fda7600575c8601e0a |
| project_policy_schema.sql | bfa7d6e75f35e242dd9ffb02bdcd725cf6fdf88a126821f5e0ef5c4df8a35d8e |
| project_policy_migration.py | d7b3a9aa38047231c844a7f18a8fba86e3876723458c45a80594f095e4599bbe |

Kiểm thực tế:

- Wheel trực tiếp từ source: ba SQL canonical và loader project policy đều
  xuất hiện đúng một lần, SHA256 khớp source.
- Sdist: đủ bốn resource, đúng byte; giải nén artifact tự tạo có chặn path
  traversal/symlink bằng helper packaging đã có.
- Dựng wheel từ sdist: đủ và đúng byte như source.
- Cài rebuilt wheel bằng pip --no-index/--no-deps/--no-compile vào TEMP mới.
  Child Python -I -B chạy ngoài checkout, thêm duy nhất installed target vào
  sys.path, kiểm module nằm trong target và đọc resource bằng importlib.resources.
  Không import hay gọi migration, không kết nối DB/IdP.
- Kiểm lại pyproject và bốn resource sau lượt chạy: hash đầu/cuối giữ nguyên.
- Ba test hiện có trong test_k03_migration_package.py: **3 passed,0.58s,exit0**.
  Kết quả này không được gọi là test hành vi migration project policy.

Artifact probe `check_project_policy_package.py` tái sử dụng helper build/sdist
của test_k03_migration_package.py. Egg/build/bdist/dist và installed target đều
nằm dưới TEMP riêng, không cài thêm dependency hoặc đổi venv dùng chung.

Log: `project-policy-package.log`; chi tiết đường dẫn/hashes:
`project-policy-package-verification.json`. TEMP đã kiểm:
`C:\Users\TRUNGK~1\AppData\Local\Temp\aletheia-j01-project-package-1935ongb`.

Giới hạn: source là WIP chung trên feat/product-api, commit nền 2bbcdc2.
Đây là kiểm resource trong artifact, không phải release đã đóng băng, fresh
dependency install, SQL migration rehearsal, production rollout hay nghiệm thu
J01/K04 cùng Quân. Các ZIP review cũ được giữ nguyên và không chứa thay đổi mới
này. K04/K05 vẫn sở hữu và kiểm hành vi migration/canonical DB.
