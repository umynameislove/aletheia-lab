# Benchmark Protocol

## Mục tiêu benchmark

Tạo các controlled injection families để đo khả năng chẩn đoán lỗi của AI mà
không gán nhãn failure trước khi quan sát tác động thật lên model.

## Semantics của một case family

```text
injected_change
  -> observed_outcome
  -> failure_eligibility (accuracy-regression/v1, threshold = 0.01)
  -> hidden_failure_cause (chỉ khi eligible_failure)
```

- `case_family_id`: SHA-256 canonical của dataset/split và injection identity;
  không chứa evidence condition. Ba context `full`, `missing_key`, `noisy` dùng chung ID này.
- `case_id` và `public_id`: định danh context, phải duy nhất trong toàn bộ 15 contexts.
- Accuracy delta `<= -0.01`: `eligible_failure`.
- Accuracy delta `>= +0.01`: `improvement_control`.
- Khoảng còn lại: `stable_control`.
- Controls vẫn giữ injection provenance và measured outcome nhưng không được có hidden failure cause.

## Case chính thức

Mục tiêu chính: 200 controlled cases.

Đề xuất phân bổ:

```text
4 fault types × 10 injection settings × 5 evidence conditions = 200 contexts
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

- Injected changes chỉ trở thành asserted failure cause khi measured outcome qua eligibility gate.
- Eligible injected failures là dominant-cause cases, không đại diện toàn bộ real-world multi-cause failures.
- Organic cases chỉ là validity check, không phải main benchmark.
- Evidence collection do người làm thiết kế nên có risk “evidence được dọn sẵn”.

Mitigation:

- dùng negative controls;
- withheld/counterfactual evidence;
- blind human audit subset;
- công khai manifest/schema.
