# Qwen3.8 technology amendment for diagnosis sensitivity

## Decision and timing

This prospective amendment replaces the unexecuted
`Qwen3-Coder-30B-A3B-Instruct` local candidate with
[`Qwen3.8-27B`](https://huggingface.co/Qwen/Qwen3.8-27B). The official model
was released on 2026-08-14, before the prior candidate was frozen on
2026-09-20. The recency gap was found before any local Qwen inference, before
any protected main outcome was opened, and with zero registered main attempts
consumed. The predecessor candidate and freeze remain unchanged as historical
records.

The replacement improves the currency of the secondary model-family
sensitivity. It is not selected from Aletheia answer quality. Qwen's published
benchmark values are model-author results, not Aletheia evidence, and they do
not license a cross-model superiority claim.

## Frozen identity

- Base model: `Qwen/Qwen3.8-27B`, revision
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`, Apache-2.0.
- Architecture: dense `Qwen3_5ForConditionalGeneration`, 27,781,427,952
  parameters, 64 language-model layers, native 262,144-token context.
- Quantization: [`ggml-org/Qwen3.8-27B-GGUF`](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF),
  revision `efbb3b1f70a21d97fd4495240648405f7228554f`, file
  `Qwen3.8-27B-Q8_0.gguf`, 28,595,763,648 bytes, SHA-256
  `aab65c67ef0dad127960efef9247f1832bca105faa1c7a052cc039b223cf86a1`.
- Runtime: [`llama.cpp v0.4.1`](https://github.com/ggml-org/llama.cpp/releases/tag/v0.4.1),
  commit `b29c606e28a01b1bc8c1351026a0fa6e616bf6c4`, Metal, Release.
- Input modality: text only. Vision projection, DFlash, and MTP sidecars are not
  loaded and are outside the study intervention.

The model author documents thinking as the default and separately recommends
non-thinking sampling at temperature 0.7, top-p 0.8, top-k 20, min-p 0,
presence penalty 1.5, and repetition penalty 1.0. Aletheia prospectively uses
that non-thinking mode because each response must be one short constrained JSON
object and the 600-token budget is an answer budget, not a combined hidden
reasoning budget. This choice is part of the intervention and limits
generalization to other Qwen3.8 reasoning settings.

## Unchanged scientific boundary

The exact 12-family, 72-request census is unchanged because its identities are
derived only from the already sealed family, evidence-condition, and variant
selection; they contain no model identity. The comparison remains B1 versus A3
within Qwen across `full`, `missing_key`, and `noisy`. It remains descriptive,
is reported separately from GPT-4.1, is never pooled into the primary estimate,
and does not increase the primary independent-family count.

No fallback model or quantization is permitted. If the exact Q8 artifact fails
the predeclared schema, memory, crash, truncation, or runtime gate, the failure
is preserved and local Qwen sensitivity is reported as operationally
infeasible. This avoids selecting a substitute from observed answer quality.

Before any of the 72 sensitivity requests run, the exact bytes, source and
embedded chat-template equality, pinned Metal build, loopback-only endpoint,
and seven synthetic calls must pass outcome-blind calibration. Independent
methods review and a new ready manifest remain mandatory afterward.
