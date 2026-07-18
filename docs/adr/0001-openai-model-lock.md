# ADR 0001: Pin the external diagnosis model

- Status: accepted
- Date: 2026-07-18

## Context

The matched diagnosis experiment compares a plain baseline with an
evidence-contract variant. A moving model alias, unequal generation settings, or
provider tools would introduce uncontrolled differences between the two variants
and weaken reproducibility.

## Decision

External diagnosis requests use OpenAI Chat Completions with the exact model
snapshot `gpt-4.1-2025-04-14` and Python SDK `openai==2.46.0`. Both variants use
the same structured-output schema, temperature, top-p, seed, output budget,
timeout, and retry ceiling. Tools, web search, and retrieval are disabled.

The only experimental intervention is the frozen difference between the plain
instruction/evidence renderer and the evidence-contract instruction/renderer.
Model selection is fixed before comparative outputs are inspected.

## Consequences

- Results support claims about this pinned snapshot, not all models or providers.
- Every outbound payload is hash-bound to its source evidence and configuration.
- Preflight validates the complete 15-pair request set without creating a client.
- External execution requires a separate, explicit operator action.
- Cross-model generalization requires an independently specified replication.
