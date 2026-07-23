# Evidence Contract v3

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
| P1 case artifacts | `p1-cases/5` |
| Evidence item | `evidence-item/3` |
| Evidence bundle | `evidence-bundle/3` |
| Diagnosis evidence view | `diagnosis-evidence-view/2` |
| Condition rubric | `condition-rubric/2` |
| Evidence store | `evidence-store/2` |
| Machine leakage audit | `evidence-leakage-audit/2` |
| Blind review packet | `human-evidence-blind-packet/2` |
| Review mapping packet | `human-evidence-review-mapping/2` |
| Human review record | `human-evidence-review/3` |

Các model dùng Pydantic strict, `extra="forbid"` và frozen. `EvidenceItem`,
`EvidenceBundle`, `DiagnosisEvidenceView` và `ConditionRubric` phải khai báo
`schema_version` tường minh; bỏ field này hoặc gửi version khác đều fail closed.
Version cũ không được parse âm thầm như v3. Hiện tại không có migration
tự động; caller phải tái tạo bundle từ nguồn đã xác minh.

## 3. EvidenceItem v3

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

## 4. EvidenceBundle v3

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

Hệ quả phương pháp luận: `full` và `noisy` chỉ hỗ trợ
`bounded_causal_hypothesis`, không hỗ trợ `causal_conclusion` hay
`strong_causal_conclusion`. `missing_key` không thể chỉ khai danh sách
withheld; evaluator-side phải thật sự giữ các counterpart bị ẩn và mọi
hypothesis phải nêu uncertainty.

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

Evidence ID, role, title và content trong projection phải trung lập, không
được mã hóa `full`, `missing_key`, `noisy`, expected behavior hay evaluator
intent. Comparison bổ sung dùng ID `secondary-comparison` và role
`secondary_distribution_comparison`. Từ `distractor` chỉ được tồn tại trong
evaluator-side metadata, không được xuất hiện trong `DiagnosisEvidenceView`.

Projection không xuất:

- internal case/bundle/family IDs;
- source path, collector metadata hoặc provenance links;
- condition label, sufficiency label hoặc expected behavior;
- allowed/forbidden rubric;
- missing/withheld role lists;
- evaluator-only hoặc hidden-ground-truth items.

Tương tự, `DiagnosisInput` của case schema v5 dùng opaque context ID và không còn
chứa `public_id` hay `evidence_condition`. Condition chỉ được suy ra trong phân
tích evaluator-side, không được dùng để gợi model nên commit hay abstain.

## 6. Canonical P1 condition rubric

| Condition | Causal sufficiency | Allowed behavior | Forbidden behavior |
|---|---|---|---|
| `full` | `bounded_hypothesis_supported` | observation, comparison, qualified bounded hypothesis, citation | causal/strong causal conclusion; remediation; unsupported extra cause |
| `missing_key` | `bounded_hypothesis_tentative_only` | observation, comparison, tentative bounded hypothesis kèm uncertainty; yêu cầu evidence còn thiếu | causal/strong causal conclusion; confident cause; remediation |
| `noisy` | `bounded_hypothesis_supported` | như `full`, đồng thời xử lý secondary comparison như một quan sát bình thường | causal/strong causal conclusion; chọn unsupported comparison; blanket abstention |

`missing_key` không cấm model báo cáo metric/distribution quan sát được,
nhưng cấm chuyển chúng thành causal conclusion. `full` và `noisy` cũng
không được nâng thành causal conclusion; chúng chỉ cho phép bounded hypothesis.

Rubric là typed evaluator contract trong `evidence/rubric.py`. Validator so toàn
bộ rubric với canonical object, nên sửa expected behavior, tráo sibling hoặc thêm
claim level đều bị bắt.

## 7. Ví dụ hợp lệ rút gọn

```json
{
  "schema_version": "evidence-item/3",
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
{"schema_version": "evidence-item/3", "evidence_roles": [], "source_path": null}
```

Bounded-hypothesis-supported condition tự khai thiếu decisive evidence, `missing_key` không có
evaluator-held counterpart, schema version bị bỏ, và ID như `missing_key-001`
cũng đều bị từ chối.

## 9. Output contract cho evidence-bound diagnosis

Diagnosis runner ghi tối thiểu:

- incident summary dựa trên facts nhìn thấy;
- ranked hypotheses;
- atomic claims;
- supporting/counterevidence IDs;
- missing evidence request;
- confidence/uncertainty;
- abstention status và reason;
- proposed next checks nhưng không tự thực thi.

## 10. Collector, store và audit gate

Collector tạo `EvidenceBundle` cho đủ ma trận 5 family × 3 condition:

1. toàn bộ source cases phải qua `validate_p1_cases` trước khi collect;
2. diagnosis-visible item chỉ được dựng từ `DiagnosisInput` đã validate;
3. counterpart bị giữ lại trong `missing_key` được lấy từ sibling `full`
   cùng family, không từ ground truth;
4. source provenance dùng `diagnosis_input.json` cùng opaque `source_context_id`;
5. persisted store lưu 15 bundle, machine leakage report, blind packet A–C và
   evaluator mapping packet cho vòng D/paired-family audit;
6. loader tính lại exact file set, file SHA, canonical bundle SHA, diagnosis-view SHA
   và store SHA;
7. independent validator re-collect từ source cases và yêu cầu persisted bundle
   bằng canonical collector output. Vì vậy việc sửa content rồi rehash đồng bộ
   không thể bypass gate;
8. machine leakage report được recompute, không tin boolean PASS đã lưu.

Artifact layout:

```text
evidence-store/
  store-manifest.json
  bundles/<canonical-bundle-sha256>.json
  audit/machine-leakage-report.json
  audit/human-review-blind-packet.json
  audit/human-review-mapping-packet.json
```

Lệnh chuẩn:

```bash
PYTHONPATH=src python -m aletheia_lab benchmark generate-p1-evidence \
  --cases-dir experiments/p1/cases \
  --output-dir experiments/p1/evidence-store

PYTHONPATH=src python -m aletheia_lab benchmark validate-p1-evidence \
  --cases-dir experiments/p1/cases \
  --store-dir experiments/p1/evidence-store
```

Structural và machine semantic audit không được gọi là human review. Quy trình
review có hai stage bắt buộc:

1. Reviewer chỉ mở `human-review-blind-packet.json`. Mỗi entry chỉ có opaque
   `review_id` và `diagnosis_view`; không có condition, sufficiency, expected behavior,
   bundle/family ID hay evaluator intent. Reviewer hoàn tất A–C trước.
2. Sau đó reviewer mới mở `human-review-mapping-packet.json` để làm D và
   paired-family audit. Mapping chứa hash của blind packet; mỗi entry có hash
   binding của review ID, view hash, bundle, family, condition và rubric.

`HumanReviewRecord` phải bind cả hai packet hash, bao phủ chính xác 15 entry và
5 family, có rationale cho mọi decision, attestation, reviewer ID và cam kết
không dùng AI. Timestamp và chữ ký tự do không nằm trong public record; lịch sử
Git và packet hash giữ provenance kỹ thuật. Thiếu entry/field, hash bị sửa hoặc tráo,
`UNCERTAIN`, hay bất kỳ blocker nào đều fail; không được promote thành PASS.

Evidence validation không tự claim chất lượng chẩn đoán. Kết luận về model phải
dựa trên diagnosis outputs và evaluator artifacts riêng.
