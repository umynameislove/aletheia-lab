# P1 Eval-First Vertical Slice

## Lý do

Rủi ro lớn nhất của Aletheia là xây platform quá rộng nhưng eval yếu. Vì vậy P1 phải chạy end-to-end trước khi mở rộng.

## Scope P1

- Fault type: `data_drift`
- Case count: 15
- Dataset: 1 dataset tabular classification nhỏ/dễ reproduce
- Variants:
  - Plain LLM
  - RAG baseline
  - Evidence-bound
  - Full Aletheia placeholder nếu memory chưa xong
- Metrics:
  - correctness
  - faithfulness
  - abstention
  - divergence label

## Pipeline

```text
base dataset
  -> inject data_drift
  -> train/evaluate
  -> collect evidence
  -> run 4 diagnosis variants
  -> score diagnosis
  -> write error analysis
```

## Quality gate

Không qua P1 nếu thiếu một trong các mục:

- Có case manifest.
- Có hidden ground-truth cause.
- Evidence không leak ground-truth.
- Có ít nhất 2 variants chạy thật.
- Có metric output dạng bảng.
- Có 5-10 lỗi được phân tích thủ công.

## Claim được phép sau P1

Được nói:

> Đã có vertical slice chứng minh pipeline đánh giá chạy được.

Chưa được nói:

> Aletheia tốt hơn baseline.

Muốn nói tốt hơn baseline phải đợi P4/P5.
