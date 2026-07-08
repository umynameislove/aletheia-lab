# ADR 0001: Eval-first core

## Status

Accepted.

## Context

Aletheia có scope rất lớn: benchmark, evidence, memory, diagnosis, agent, dashboard, paper. Rủi ro lớn nhất là xây nhiều nhưng không có evaluation đủ sâu.

## Decision

Repo ưu tiên P1 vertical slice và P5 evaluation ngay từ đầu.

Mọi module systems phải phục vụ câu hỏi nghiên cứu:

> Diagnosis có đúng, bám evidence, và biết abstain không?

## Consequences

- Dashboard/agent/cloud là optional.
- Config, schema, tests và tracking xuất hiện từ đầu.
- Benchmark/evaluator được xem là kernel của repo.
- Nếu deadline căng, cắt UI trước, không cắt evaluation.
