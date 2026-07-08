# Benchmark Protocol

## Mục tiêu benchmark

Tạo các failure cases có ground-truth cause rõ ràng để đo khả năng chẩn đoán lỗi của AI.

## Case chính thức

Mục tiêu chính: 200 controlled cases.

Đề xuất phân bổ:

```text
4 fault types × 10 injection settings × 5 evidence conditions = 200 cases
```

## Organic validity cases

Thêm 10-20 lỗi thật từ quá trình phát triển để kiểm tra transfer từ synthetic/injected failures sang debug thật.

## Evidence conditions

Mỗi fault type nên có nhiều evidence condition:

1. Full evidence
2. Missing key evidence
3. Noisy evidence
4. Counterfactual evidence
5. Minimal evidence

## Leakage policy

Evidence visible to diagnoser không được chứa:

- exact ground-truth label nếu label quá trực tiếp;
- tên script injection tiết lộ lỗi;
- comment kiểu “this case was injected with data drift”;
- answer key hoặc hidden notes.

## Construct validity

Phải báo cáo thẳng:

- Injected faults là dominant cause, không đại diện toàn bộ real-world multi-cause failures.
- Organic cases chỉ là validity check, không phải main benchmark.
- Evidence collection do người làm thiết kế nên có risk “evidence được dọn sẵn”.

Mitigation:

- dùng negative controls;
- withheld/counterfactual evidence;
- blind human audit subset;
- công khai manifest/schema.
