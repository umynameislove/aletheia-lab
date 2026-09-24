# Recovery-v2 relation scoring

This is a forward continuation of the sealed technical recovery, not a rerun of
the registered diagnosis attempt. **Do not invoke the main pipeline's `execute`
command**: that entrypoint begins with the diagnosis stage. Use only
`scripts/diagnosis_main_recovery_scoring.py` for this recovery.

The `inspect` command is read-only and credential-free. It verifies the full
1,024-terminal recovery ledger, the predecessor and smoke receipts, the frozen
scoring preparation, all 3,181 blind relation requests, and the cost schedule.
The only provider-visible task fields are `claim_text`, `claim_type`, and
`visible_evidence`. No variant, hidden truth, reference label, or human label is
included. The provider is the pinned `gpt-4.1-2025-04-14` Chat Completions
endpoint; response storage is disabled by the frozen gateway policy.
The recovered request census contains at most five visible-evidence items per
claim; the frozen response cap remains 600 tokens.

The worst-case reservation under the frozen $2/M input and $8/M output rates,
600 output tokens, and at most two attempts per request is **$86.862556**. The
local operator ceiling is **$90**; this is a dispatch guard, not a guarantee
about the provider's final invoice. At one second minimum between attempt
starts, 3,181 calls take at least about 53 minutes, plus response latency and
possible retries.

On a clean, synchronized `main`, `prepare` writes a private, self-hashed plan
in a new directory outside the repository and prints its SHA-256. It makes no
provider call. `execute` requires that exact SHA-256, the same source commit,
the same verified recovery, and a configured frozen OpenAI SDK/key. It creates
an immutable lease before dispatching relation requests. Completed request
terminals can be resumed; an in-progress terminal cannot be silently replayed.
The original recovery directory is never modified. `verify` is read-only and
rebuilds the results from the completed private relation store. Its private
analysis input is an output for the separately registered analysis step; it is
not published to the repository.

The CLI takes one `--memory-root` directory and resolves the sealed census,
predecessor, smoke, recovery v2, and new scoring-v1 directory beneath it. The
`prepare` and `inspect` commands need no API credential.
