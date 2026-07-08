# Contributing to Aletheia Lab

Cảm ơn bạn đã đóng góp. Repo này theo nguyên tắc **eval-first** và
**evidence-first**: mọi thay đổi phải bám vào bằng chứng (test, log, output),
không over-claim, và không thực hiện hành động public/destructive khi chưa được
duyệt.

## 1. Nguyên tắc cốt lõi

- Ưu tiên thay đổi nhỏ, an toàn, có test.
- Không commit dữ liệu thật, secret, `.env`, file lớn, hoặc output sinh tự động.
- Không đánh dấu task `Done` khi chưa có bằng chứng (test/commit/doc).
- Không đẩy (push), tag, release khi chưa có người review đồng ý.

## 2. Quy trình làm việc

1. Nhận một micro-task từ GitHub Issues (label `task`) hoặc bảng công việc
   nội bộ của nhóm; mỗi task có ID và acceptance criteria rõ ràng.
2. Tạo branch theo quy ước: `feat/<taskid>-<slug>`, `fix/<taskid>-<slug>`,
   `docs/<taskid>-<slug>`, `exp/<taskid>-<slug>`.
3. Làm đúng scope của task, không trộn nhiều concern.
4. Chạy kiểm thử cục bộ trước khi mở PR:
   ```bash
   make lint
   make hygiene   # chặn tracking/docx/junk lọt vào repo
   make test
   ```
5. Mở Pull Request, điền đầy đủ `PULL_REQUEST_TEMPLATE`.
6. Chờ ít nhất 1 reviewer duyệt. Không tự merge PR của chính mình.

## 3. Quy ước commit (Conventional Commits)

Định dạng: `<type>(<scope>): <mô tả ngắn, thức mệnh lệnh>`

Các type: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `exp`, `ci`.

Ví dụ tốt:

```
feat(benchmark): add data_drift injector for tabular cases
fix(evaluation): correct abstention rate when evidence is empty
docs(protocol): clarify P1 quality gate checklist
```

Nguyên tắc message: viết như con người, mô tả *cái gì thay đổi và tại sao*,
không dán nguyên văn prompt, không ghi chú kiểu do AI sinh ra, không kể lể quá trình.

## 4. Định nghĩa Done

Một task chỉ Done khi: artifact yêu cầu tồn tại, đúng scope, có test hoặc lý do
bỏ qua rõ ràng, đã cân nhắc security/privacy, và bằng chứng khớp với claim.

## 5. Cấu trúc thư mục

Xem `README.md` mục "Repo tree". Tài liệu kỹ thuật ở `docs/`; kế hoạch/tracking
nằm ở folder tracking riêng của nhóm, KHÔNG đưa vào repo.

## 6. Báo lỗi / đề xuất thí nghiệm

Dùng issue template trong `.github/ISSUE_TEMPLATE/`.
