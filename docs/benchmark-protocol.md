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
- Internal `case_id` và `public_id`: định danh context, phải duy nhất trong toàn bộ 15 contexts.
- Diagnosis chỉ thấy `diagnosis_context_id` SHA-256 opaque; không thấy internal ID,
  `evidence_condition` hoặc expected-behavior rubric.
- Accuracy delta `<= -0.01`: `eligible_failure`.
- Accuracy delta `>= +0.01`: `improvement_control`.
- Khoảng còn lại: `stable_control`.
- Controls vẫn giữ injection provenance và measured outcome nhưng không được có hidden failure cause.

## Evidence conditions

Benchmark hiện tại dùng ba evidence condition:

1. Full evidence
2. Missing key evidence
3. Noisy evidence

## Leakage policy

Evidence visible to diagnoser không được chứa:

- exact ground-truth label nếu label quá trực tiếp;
- tên script injection tiết lộ lỗi;
- comment kiểu “this case was injected with data drift”;
- answer key hoặc hidden notes.

`full`, `missing_key`, `noisy` là evaluator-side intervention labels. Model chỉ
nhận các observable facts sau whitelist projection; label không được đưa thẳng
vào prompt hoặc mã hóa trong evidence ID vì có thể gợi model nên commit
hay abstain.

Ba condition P1 là controlled evidence interventions trên cùng family:

- `full`: toàn bộ decisive roles phải diagnosis-visible;
- `missing_key`: chính xác canonical decisive roles bị ẩn khỏi model, nhưng phải
  được materialize trong internal bundle dưới dạng evaluator-only withheld items;
- `noisy`: toàn bộ decisive roles vẫn hiện diện và có thêm một
  secondary comparison trung lập. Việc item này được thiết kế làm nhiễu
  chỉ được ghi trong evaluator-side metadata, không lộ cho model.

Quy tắc này biến `missing_key` thành phép ablation có thể audit, thay vì một
nhãn do caller tự khai.

## Construct validity

Phải báo cáo thẳng:

- Injected changes chỉ trở thành asserted failure cause khi measured outcome qua eligibility gate.
- Eligible injected failures là dominant-cause cases, không đại diện toàn bộ real-world multi-cause failures.
- Evidence collection do người làm thiết kế nên có risk “evidence được dọn sẵn”.

Mitigation:

- dùng negative controls;
- withheld/counterfactual evidence;
- blind human audit trên toàn bộ 15 context;
- công khai manifest/schema.
