# Bổ sung V3.2 — claim về báo cáo đo lường

Tài liệu này bổ sung cách đọc claim V3.2 cho rubric chính trong
`RATER_GUIDE.md`; không thay đổi bốn nhãn hoặc thứ tự ưu tiên của rubric đó.

## Việc bạn đang đánh giá

Chỉ đánh giá claim có được **báo cáo đang hiển thị** hỗ trợ hay không.
Không suy luận về một thí nghiệm gốc không được hiển thị, nguyên nhân gây lỗi,
model nào tốt hơn hoặc kết quả mà nhóm nghiên cứu mong muốn.

Mỗi claim có một hoặc nhiều mệnh đề, ngăn cách bằng dấu `;`. Dạng điển hình là:

`In the displayed measurement report, ev-example#/payload/observed/score = 0.25`

Đây là ví dụ hướng dẫn, không thuộc bộ chấm. `ev-example` chỉ một evidence item;
phần sau `#` là đường dẫn JSON, đi lần lượt qua các khóa `payload`, `observed`,
`score`. Nếu khóa chứa `~1` thì giải mã thành `/`, `~0` thành `~`.
Đường dẫn tới phần tử mảng dùng chỉ số bắt đầu từ 0. Không lấy một số giống nhau
ở trường khác thay cho đúng trường được claim chỉ ra.

## Áp dụng bốn nhãn — xét mâu thuẫn trước

1. `contradicted`: ít nhất một mệnh đề bị evidence trực tiếp phản bác. Một giá
   trị khác tại đúng evidence item và đúng đường dẫn là mâu thuẫn trực tiếp,
   ngay cả khi mệnh đề khác được hỗ trợ.
2. `unsupported`: không có mệnh đề nào được xác lập hoặc trực tiếp phản bác.
3. `partially_supported`: có mệnh đề được xác lập, có mệnh đề chưa được xác lập,
   và không mệnh đề nào bị phản bác.
4. `fully_supported`: mọi mệnh đề đều được xác lập.

So sánh giá trị số chính xác, không làm tròn hay tự thêm sai số cho phép.
Các cách viết cùng giá trị số, như `0.250` và `0.25`, là tương đương.
Phân biệt dấu âm/dương, giá trị delta với giá trị observed/clean.
Thiếu evidence item hoặc thiếu trường không có nghĩa giá trị bằng 0 và không
tự tạo ra mâu thuẫn. Hash, provenance hoặc file nguồn tồn tại không thay thế
cho một phép đo bị thiếu. Đọc tất cả evidence được cung cấp, không bổ sung
Internet, repo, dữ liệu lịch sử hoặc kiến thức về đáp án mong đợi.

## Cách làm và nộp

Đọc `JOB.md`, `RATER_GUIDE.md`, tài liệu bổ sung này, rồi
`blind-packet.json`. Giữ nguyên packet.
Sao chép `submission-template.json` thành `completed-submission.json`.
Chỉ sửa các ô trả lời và attestation, không đổi ID, thứ tự hoặc hash.

Với từng claim:

1. Đọc đầy đủ claim và mọi `visible_evidence`; `excerpt` là JSON của báo cáo.
2. Kiểm tra từng mệnh đề tại đúng evidence ID và đường dẫn.
3. Điền đúng một `support_label` trong bốn nhãn ở trên.
4. Trong `evidence_ids_used`, ghi các evidence ID thực sự dùng, có trong chính
   claim đó. Nhãn khác `unsupported` cần ít nhất một citation; `unsupported`
   có thể dùng `[]` nếu không evidence nào liên quan.
5. Viết `rationale` từ 20 đến 1.000 ký tự, chỉ rõ trường nào được xác lập,
   thiếu hoặc xung đột. Có thể viết tiếng Việt. Không chép rationale chung
   cho mọi câu.

Hoàn thành toàn bộ số câu ghi trong JOB, kiểm tra lần hai nội dung, citation,
thứ tự và cú pháp JSON. Attestation phải phản ánh đúng thực tế: tự con người
chấm, độc lập, không dùng model và đã đọc rubric. Không xác nhận điều không đúng.
Nộp riêng `completed-submission.json` cho điều phối; không gửi cho người chấm kia.

## Những lỗi cần tránh

- Bỏ citation ở nhãn có hỗ trợ hoặc mâu thuẫn.
- Thấy một phần đúng rồi bỏ qua phần khác bị phản bác.
- Nhầm “không thấy” với “thấy một giá trị khác”.
- Dùng con số của sai trường, sai evidence item hoặc làm tròn để khớp.
- Sửa claim/evidence thay vì báo vấn đề cho điều phối.
- Đoán nhãn từ vị trí câu hoặc cố cân bằng số nhãn trong bài nộp.

Không dùng AI để giải thích câu, chọn nhãn, viết rationale hoặc kiểm tra bài.
Không trao đổi đáp án giữa hai người chấm. Nếu dữ liệu hỏng, không đọc được,
hoặc quy tắc còn mơ hồ, báo đúng `blind_claim_id` cho điều phối, không đoán.
Các lỗi hoặc câu hỏi không được giải quyết bằng cách xem nhãn tự động.
