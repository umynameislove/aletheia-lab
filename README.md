# Aletheia Lab

Evidence-Grounded Failure Diagnosis and Meta-Faithfulness Evaluation for ML/LLM Systems.

Aletheia Lab là đồ án/đề tài nghiên cứu-kỹ thuật nhằm trả lời một câu hỏi rất cụ thể:

> Khi một hệ thống ML/LLM bị lỗi, lời giải thích nguyên nhân của AI có thật sự bám vào bằng chứng, đúng với nguyên nhân thật, và biết từ chối khi thiếu dữ kiện không?

Repo này được dựng theo hướng **eval-first**: chạy được một lát cắt đánh giá nhỏ trước, rồi mới mở rộng benchmark, memory, agent và dashboard.

## Trạng thái hiện tại

Đây là repo skeleton chuyên nghiệp để bắt đầu implementation:

- Có cấu trúc `src/`, `tests/`, `configs/`, `docs/`, `experiments/`, `reports/`.
- Có schema ban đầu cho benchmark case, evidence bundle, metrics.
- Có `docs/adr/` ghi lại các quyết định kiến trúc/nghiên cứu.
- Có CI mẫu và command entrypoint.
- Chưa phải implementation hoàn chỉnh của toàn bộ đồ án.

## Minimal defensible core

Core tối thiểu cần bảo vệ trong đồ án:

1. Benchmark lỗi ML có ground-truth cause.
2. Evidence store ghi lại log/config/metric/artifact có cấu trúc.
3. 4 diagnosis variants:
   - Plain LLM
   - RAG baseline
   - Evidence-bound diagnosis
   - Full Aletheia diagnosis
4. Evaluation đo:
   - Correctness
   - Faithfulness
   - Abstention
   - Divergence giữa groundedness và correctness
5. Human audit nhỏ để chống circularity của LLM judge.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
aletheia --help
```

Nếu chưa muốn cài package, vẫn có thể đọc docs và chỉnh config trực tiếp.

## Repo tree

```text
.
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── CHANGELOG.md
├── CITATION.cff
├── pyproject.toml
├── Makefile
├── .github/
├── configs/
├── data/
├── docs/
├── experiments/
├── reports/
├── scripts/
├── src/aletheia_lab/
└── tests/
```

## Phase chính

| Phase | Mục tiêu | Output bắt buộc |
|---|---|---|
| P1 | Eval-first vertical slice | 15 cases data_drift chạy end-to-end |
| P2 | Benchmark + evidence layer | 120-200 cases có schema sạch |
| P3 | Memory + lineage | failure object, run lineage, reproducibility |
| P4 | Diagnosis variants | 4 variants chạy trên benchmark |
| P5 | Meta-faithfulness evaluation | faithfulness/correctness/abstention + ablation |
| P6 | Human study + release | Cohen's kappa, report, artifact release |

## Tư tưởng thiết kế

- Không khoe “platform to” trước khi có eval.
- Không dùng LLM judge làm thước đo duy nhất.
- Không để evidence chứa leak ground-truth cause.
- Không nhập nhằng giữa “bám evidence” và “đúng nguyên nhân”.
- Không nuốt CrossRoute vào Aletheia; CrossRoute là paper/method nền tảng riêng.

## Dự án nền tảng liên quan

- `projmem`: memory, lineage, và cơ chế ghi lại run/failure.
- `crossroute-audit`: methodology về faithfulness audit và counterfactual evaluation.
- `FactoryLens`: agent/application shell cho case study.
- C-ALIGN/routing paper: nền tảng tư tưởng về faithfulness phải được kiểm chứng bằng counterfactual/mechanistic evidence.

## Case-count policy

Mục tiêu chính thức:

- 200 controlled benchmark cases.
- 10-20 organic failures để kiểm tra external validity.

Stretch:

- 240-300 cases chỉ làm khi 200 cases đã sạch, eval chính đã chạy xong, ablation/human audit đã có, và còn buffer.

## License

TBD. Chọn license trước khi public repo.
