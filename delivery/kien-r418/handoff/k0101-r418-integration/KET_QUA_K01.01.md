# K01.01 — Đối chiếu nhánh và bảo toàn công việc

Ngày chốt: 14/11/2026. **PASS trong phạm vi tiêu chí cá nhân K-01.01**: “Không mất file hoặc commit; liệt kê rõ phần mới, phần đã merge và phần còn riêng.” Đây là hồ sơ kỹ thuật cho mục này; chưa phải nghiệm thu chung K01–K18 cho Bảo.

## Bản ghép có thể kiểm tra và khôi phục

- Nhánh local: `codex/k0101-r418-integration`.
- SHA cuối: `cf9738b1accef69e6f1402fb241bad17fcc3a477`.
- Merge commit: `040d5dc03c6c1133f7e6a971755656459d462eec`, giữ cả cha API `2bdf855c…` và UI `d4e7fb2…`. Hai commit mới do Codex tạo tại máy Kiên; không gán tác giả hoặc xác nhận review cho Quân.
- Main kiểm từ remote và fetch đủ lịch sử: `2bbcdc2a7637c840babd8b71910306c64072bc4b`. Remote UI: `d4e7fb214d3bb6a74b2ddf0f012bf69c7c3e8e7c`. Không dùng cached main cũ làm căn cứ remote mới nhất.
- Đã kiểm main, API và UI trên là tổ tiên của SHA cuối. Đây là merge có lịch sử Git, thay cho chỉ chép tree diff.
- Bundle phát hành trong thư mục này giữ lịch sử main, API, UI, Kiên cũ và Quân cũ. ZIP source chứa đúng 2.557 tệp của commit cuối. SHA-256 từng gói ở file `.sha256` đi kèm.

## Phương pháp và kết quả

| Kiểm tra | Phương pháp | Kết quả |
|---|---|---|
| Đối chiếu main và các nhánh | Đọc refs thật, fetch, bỏ giới hạn lịch sử nông, kiểm tổ tiên và divergence | Đã làm; số đầy đủ trong `evidence/result.json` |
| Giữ công việc trước ghép | 5 kho: HEAD, mọi ref hiện có, status, patch staged/unstaged, ZIP tệp WIP và đích symlink | 183 đường dẫn WIP được lưu; không đưa dữ liệu WIP thô vào gói gửi |
| Kiểm sau ghép | So HEAD/status/patch và SHA/đích symlink trước–sau; bảo toàn giá trị mọi ref gốc | Cả 5 kho nguyên trạng; chỉ thêm 6 ref cho công việc tích hợp trong kho tham khảo |
| Phục hồi bản ghép độc lập | Clone từ bundle qua đường dẫn local, checkout sạch, không dùng mạng; so mọi tệp với manifest | 2.557/2.557 khớp từng byte; status sạch |
| Phục hồi bản sao 5 kho | Clone bare độc lập từ từng bundle và `git fsck --connectivity-only`; kiểm mọi commit được ref gốc trỏ tới | 5/5 đạt; ghi tại `original-bundles-offline-restore.json` |
| Bảo toàn backend đã kiểm | So SHA từng tệp runtime với R4.18 đã niêm phong | 676/676 khớp; runtime `a046bccd9a0beb135951a0928c7ddd131b327302bcacf10c7ddb979757c9d495` |
| Ghép UI đúng bản phản hồi cuối | So 180 SHA riêng với manifest của gói Quân, xử lý 5 conflict theo cây này | 180/180 khớp; UI `5515bc0e3d7e9cfd3be1c302dd3228bfbbf970bb6e7971675707538f21510bb7` |

Manifest source tích hợp: `5562a20b532ece14c237dad905ba24b0e71ec5beecbb0cbb6eaabc0089cf030a`. Đây là identity mới cho backend R4.18 + UI phản hồi cuối + cấu hình bảo toàn xuống dòng trong Git. Không gán lại identity source R4.18 cũ `65c87fdb…` cho cây đã ghép.

### Phần mới, phần đã ghép và phần giữ riêng

- Kiên cũ `1700adc24d3c7c4abbdf1d33b815fcc3be6c6a90`: đã nằm trong main; main hơn 9 commit, nhánh cũ hơn 0. Không áp lại.
- Quân cũ `9c85392e89949b3d165efd7e5327cc4d59beec8e`: main hơn 73 commit, nhánh cũ còn 1 commit riêng. Giữ commit và ref trong bundle; không cherry-pick lại UI cũ. Xác nhận lịch sử Bu trong `docs/k01-baseline-inventory.md` đã ghi không cần lấy thêm phần dashboard cũ trong checkout Bu đã kiểm. Kết quả UI mới dùng manifest 180 tệp hiện tại.
- API `2bdf855c…`: giữ nguyên commit cha. Phần backend đã sửa tới R4.18 được đưa vào commit tích hợp cuối, runtime khớp bản đã test.
- UI `d4e7fb2…`: giữ commit cha; cập nhật lên đúng 180 tệp phản hồi cuối. Có 10 fixture `j01-review-v2` cũ không nằm trong manifest mới, được bỏ khỏi cây hiện tại nhưng vẫn giữ trong lịch sử/bundle. Tệp Inspector.tsx vắng so với main đã được thay đổi từ nền API, không phải mất do đóng gói. Danh sách đầy đủ có trong inventory.
- 183 WIP: mỗi đường dẫn có quyết định riêng trong `wip-disposition.csv`: đã đưa vào nguyên byte, được bản R4.18/UI mới thay thế nhưng giữ bản cũ, hoặc giữ riêng ngoài sản phẩm. Các symlink cũng được lưu đúng đích. Không áp lại WIP cũ chưa review lên mã đã pass.
- Phạm vi WIP được kê là tệp tracked thay đổi và tệp untracked không bị Git ignore trong 5 kho quan sát được. Không tuyên bố đã sao lưu máy Bảo/Quân hoặc các thư mục riêng chưa được cung cấp. Thư mục cache/dữ liệu ignored không bị thao tác.

### Chi tiết bảo toàn cần biết

Git mặc định có thể chuẩn hóa CRLF của `src/aletheia_lab/hosted/capacity_assertions.py`. Bản tích hợp bổ sung đúng một quy tắc `.gitattributes` cho tệp này: `-text !eol`. Đã kiểm cả Git archive và checkout độc lập giữ nguyên byte R4.18. Không sửa logic, test, cấu hình K16 hoặc image đã pass.

Bundle đầu của `_repo_aletheia_ref` được tạo khi kho còn shallow: verify trong kho gốc chưa chứng minh phục hồi độc lập. Lượt clone độc lập đã phát hiện thiếu commit cha. Đã fetch đủ lịch sử và tạo `refs-complete.bundle`; bản bổ sung này clone/fsck thành công. Bản cũ giữ làm dấu vết, không dùng để khôi phục. Từng bundle đúng cần dùng được chỉ rõ trong `original-bundles-offline-restore.json`. WIP/patch và các bundle gốc ở `.private/k0101-r418-snapshots-v2`, chỉ giữ tại máy Kiên.

### Liên hệ với kết quả test đã có

Không chạy lại các bộ đã hoàn thành: backend và UI được ràng buộc bằng SHA từng tệp với bằng chứng R4.18 trước đó. Backend unit có 2.969 pass, 5 deselected; UI local đã kiểm 358/358, build hosted và budget hosted đạt. Đây là dẫn chiếu kết quả trước, không phải lượt test mới trên CI của commit `cf9738b…`.

`git diff --check` so với main đầy đủ còn 29 cảnh báo định dạng có sẵn trong các tệp nguồn đã chọn, gồm khoảng trắng cuối dòng/blank EOF. Giữ nguyên các byte đã pass; ghi nguyên log, không ghi check này là pass. Đây không phải lỗi mất file/commit của K01.01. Các lỗi/giới hạn về PDF timeout, tải, staging, IdP hoặc nghiệm thu release vẫn thuộc mục tương ứng; không được biến thành pass bằng bản ghép này.

## Việc còn cần Quân/Bu ở cổng phối hợp

K01.01 không còn thiếu bản đối chiếu, WIP, lịch sử ghép hoặc gói khôi phục tại máy Kiên. Cổng review/push/CI chung vẫn mở: chưa có tên người review backend, nhánh nhóm được Quân chấp nhận, SHA trên remote và đường dẫn CI của đúng cây ghép. Không suy diễn việc tạo commit local thành Quân đã review hoặc CI đã xanh.

Tin nhắn sẵn để Kiên gửi nằm ở `TIN_NHAN_GUI_BU.txt`. Gói này không chứa nghiệm thu cuối cho Bảo.
