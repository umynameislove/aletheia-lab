# Evidence Contract v2

## 1. Mục đích và trust boundary

`EvidenceBundle` là artifact nội bộ, bất biến và có version cho đúng **một**
benchmark context. Nó chứa identity, provenance, visibility và cả các item chỉ
evaluator được xem. Model chẩn đoán không nhận bundle nội bộ trực tiếp; model chỉ
nhận `DiagnosisEvidenceView` được tạo bằng whitelist projection.

Ba khái niệm không được trộn với nhau:

- **visibility:** actor nào được xem một item;
- **evidence sufficiency:** bằng chứng hiện có đủ cho cấp độ claim nào;
- **correctness/support:** diagnosis output có đúng và được bằng chứng hỗ trợ hay không.

Bundle hợp lệ không có nghĩa diagnosis đúng. Citation hợp lệ cũng không tự chứng
minh quan hệ nhân quả.

## 2. Versions

| Artifact | Version |
|---|---|
| P1 case artifacts | `p1-cases/4` |
| Evidence item | `evidence-item/2` |
| Evidence bundle | `evidence-bundle/2` |
| Diagnosis evidence view | `diagnosis-evidence-view/1` |
| Condition rubric | `condition-rubric/1` |

Các model dùng Pydantic strict, `extra="forbid"` và frozen. `EvidenceItem`,
`EvidenceBundle`, `DiagnosisEvidenceView` và `ConditionRubric` phải khai báo
`schema_version` tường minh; bỏ field này hoặc gửi version khác đều fail closed.
V1 không được parse âm thầm như v2. Hiện tại không có migration v1 tự động;
caller phải tái tạo bundle từ nguồn đã xác minh.

## 3. EvidenceItem v2

Mỗi item ghi:

- `schema_version` và `evidence_id` ổn định;
- `kind` và một hoặc nhiều `evidence_roles`;
- `title`, `content` và SHA-256 của chính content;
- source path bắt buộc, tương đối, POSIX và đã normalize;
- `collector_version` và timestamp do caller cung cấp nếu có;
- visibility: `public`, `diagnosis` hoặc `evaluator`;
- redaction state: `none`, `redacted` hoặc `withheld`;
- metadata bất biến, có thứ tự canonical;
- provenance links đến evidence IDs có thật trong cùng bundle.

Checksum được tính từ UTF-8 content. `EvidenceItem.from_content(...)` tự tính
checksum; model validator vẫn tính lại để bắt payload đã bị sửa đồng bộ một phần.

Mỗi item phải có ít nhất một observable `evidence_role`; tuple rỗng không phải
bằng chứng có thể chấm hay trích dẫn. Source path bị từ chối nếu thiếu, là absolute
path, Windows path, chứa `..`, `.` segment hoặc separator không canonical. Timestamp,
nếu có, phải là ISO-8601 có timezone. NaN/Inf không hợp lệ.

## 4. EvidenceBundle v2

Bundle nội bộ ghi:

- `evidence_bundle_id`, internal `case_id`, `case_family_id`;
- locked `validation_state=schema_validated`, chỉ tồn tại sau khi toàn bộ model validator chạy;
- opaque `diagnosis_context_id`;
- internal `evidence_condition`;
- dataset và split manifest SHA-256;
- tuple các evidence items;
- required, missing và intentionally-withheld evidence roles.

Validator tính lại:

1. evidence IDs phải duy nhất;
2. provenance links phải resolve trong bundle;
3. required/withheld roles phải đúng canonical condition rubric;
4. missing roles phải đúng hiệu giữa required roles và diagnosis-visible roles;
5. missing roles phải bằng chính xác canonical intentionally-withheld roles;
6. mỗi withheld role phải được materialize bằng item `evaluator + withheld` để
   evaluator kiểm chứng phép can thiệp;
7. intentionally-withheld roles không được diagnosis-visible;
8. diagnosis-visible evidence ID phải condition-neutral;
9. diagnosis-visible item không được chứa structural answer-key marker.

Hệ quả phương pháp luận: `full` và `noisy` không thể tự khai thiếu decisive
evidence mà vẫn mang nhãn sufficient. `missing_key` không thể chỉ khai danh sách
withheld; evaluator-side phải thật sự giữ các counterpart bị ẩn.

`EvidenceBundle.canonical_sha256()` hash canonical JSON với sorted keys,
deterministic item/role/metadata ordering và `allow_nan=false`. Kết quả không phụ
thuộc `PYTHONHASHSEED`.

Ví dụ bundle đầy đủ nằm tại
`examples/data_drift_case/evidence_bundle.example.json`.

## 5. Visibility và projection

| Visibility/state | Internal bundle | Diagnosis projection |
|---|---:|---:|
| `public` + `none/redacted` | có | có |
| `diagnosis` + `none/redacted` | có | có |
| `evaluator` | có | không |
| bất kỳ item `withheld` | chỉ hợp lệ khi evaluator-only | không |

`project_diagnosis_evidence(...)` chỉ xuất:

- opaque `diagnosis_context_id`;
- evidence ID, kind, observable roles, title, content và content checksum.

Evidence ID trong projection phải trung lập, không được mã hóa `full`,
`missing_key` hay `noisy`. Nhờ đó model không thể suy ra expected behavior từ tên item.

Projection không xuất:

- internal case/bundle/family IDs;
- source path, collector metadata hoặc provenance links;
- condition label, sufficiency label hoặc expected behavior;
- allowed/forbidden rubric;
- missing/withheld role lists;
- evaluator-only hoặc hidden-ground-truth items.

Tương tự, `DiagnosisInput` của case schema v4 dùng opaque context ID và không còn
chứa `public_id` hay `evidence_condition`. Condition chỉ được suy ra trong phân
tích evaluator-side, không được dùng để gợi model nên commit hay abstain.

## 6. Canonical P1 condition rubric

| Condition | Causal sufficiency | Allowed behavior | Forbidden behavior |
|---|---|---|---|
| `full` | sufficient | mô tả facts; chẩn đoán với citation; uncertainty vẫn được phép | unsupported extra cause; blanket abstention |
| `missing_key` | insufficient cho causal conclusion | mô tả facts; bounded hypothesis; abstain trên cause; yêu cầu decisive evidence | confident/strong causal conclusion; causal remediation |
| `noisy` | sufficient | chẩn đoán với citation và bác distractor không support | chọn distractor do salience; unsupported extra cause; blanket abstention |

`missing_key` chỉ cấm causal commitment quá mức. Nó **không** cấm model nói rằng
metric hoặc distribution đã quan sát thay đổi. `noisy` vẫn sufficient vì decisive
evidence còn hiện diện.

Rubric là typed evaluator contract trong `evidence/rubric.py`. Validator so toàn
bộ rubric với canonical object, nên sửa expected behavior, tráo sibling hoặc thêm
claim level đều bị bắt.

## 7. Ví dụ hợp lệ rút gọn

```json
{
  "schema_version": "evidence-item/2",
  "evidence_id": "metric-001",
  "kind": "metric",
  "evidence_roles": ["metric_comparison", "symptom"],
  "title": "Evaluation metric comparison",
  "content": "Accuracy moved from 0.91 to 0.72 on the evaluation split.",
  "source_path": "metrics/p1-example.json",
  "content_sha256": "9cd438f2df963641520d4f46fa0ac0f0c5913050f460de49c038c829f311a178",
  "collector_version": "fixture/1",
  "collected_at": "2026-07-07T00:00:00+00:00",
  "visibility": "diagnosis",
  "redaction_state": "none",
  "metadata": [],
  "provenance_links": []
}
```

## 8. Ví dụ bị từ chối

Checksum giả:

```json
{"content": "Accuracy changed.", "content_sha256": "000000..."}
```

Path traversal:

```json
{"source_path": "../ground_truth.json"}
```

Hidden item bị gắn diagnosis-visible:

```json
{
  "title": "Answer key",
  "content": "The cause_label is data_drift.",
  "visibility": "diagnosis"
}
```

Caller-controlled boolean cũ không còn tồn tại:

```json
{"leakage_check_passed": true}
```

Field này bị `extra="forbid"`; code không tin một boolean tự khai báo thay cho
việc validate/projection.

Thiếu provenance hoặc role:

```json
{"schema_version": "evidence-item/2", "evidence_roles": [], "source_path": null}
```

Sufficient condition tự khai thiếu decisive evidence, `missing_key` không có
evaluator-held counterpart, schema version bị bỏ, và ID như `missing_key-001`
cũng đều bị từ chối.

## 9. Output contract cho evidence-bound diagnosis

Diagnosis runner sau này phải ghi tối thiểu:

- incident summary dựa trên facts nhìn thấy;
- ranked hypotheses;
- atomic claims;
- supporting/counterevidence IDs;
- missing evidence request;
- confidence/uncertainty;
- abstention status và reason;
- proposed next checks nhưng không tự thực thi.

## 10. Claim boundary và phần chưa làm

P1-G5A chỉ hoàn thành **schema/rubric contract**. Chưa được claim toàn bộ P1-G5,
vì còn thiếu:

- collector thật tạo bundle cho đủ 15 contexts;
- immutable persistence/round-trip gate cho canonical artifacts;
- semantic leakage review và human spot-check;
- diagnosis runner, LLM outputs và evaluator metrics.

Structural marker scan không thay thế semantic leakage audit. Evidence vẫn có thể
quá “sạch”, ám chỉ đáp án bằng cách diễn đạt hoặc chứa correlation bị hiểu nhầm
thành causality; các rủi ro đó phải được audit trong P1-G5B.
