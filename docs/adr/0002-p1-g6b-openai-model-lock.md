# ADR 0002: P1-G6B external model lock

- Status: accepted and frozen before external results
- Date: 2026-07-18

## Decision

The primary G6B matched pilot uses OpenAI Chat Completions with the exact model
snapshot `gpt-4.1-2025-04-14` and Python SDK `openai==2.46.0`. Both B1 and A3 use
the same snapshot, structured-output schema, temperature, top-p, seed, output
budget, timeout and retry ceiling. Tools, web search and retrieval are disabled.

The only authorized matched intervention is the already frozen difference between
the B1 plain renderer/instruction and the A3 evidence-contract renderer/instruction.
The model choice must not change after inspecting comparative results.

## Rationale and claim boundary

The full GPT-4.1 model reduces the risk that a low capability floor explains poor
diagnosis performance while retaining a dated, reproducible non-reasoning snapshot.
This run can support a bounded claim about one fixed model snapshot. Cross-model or
cross-provider generalization requires a later replication and is not a P1 claim.

## Execution boundary

G6B-1 is offline only. Preflight must prove 15 matched pairs / 30 requests, select
the frozen eight-request smoke subset, hash the exact outbound payload set, estimate
the maximum token cost and reject secret or evaluator metadata. No external request
is authorized until the preflight passes and the user explicitly confirms G6B-2.
