# Evaluation readiness and trust boundaries

This document describes the provider-neutral infrastructure that prepares an
authorized evaluation for safe execution and offline structural closeout. The
infrastructure is outcome-blind: it binds caller-supplied policy references and
checks technical integrity without choosing or interpreting scientific policy.

> This readiness infrastructure does not authorize registered evaluation, freeze
> scientific policy, admit any mechanism, or establish a scientific result.

## Scope

The readiness layer can:

- validate an immutable, authorized manifest reference;
- project diagnosis-visible project evidence into a canonical outbound context;
- bind that context to an immutable request identity;
- execute the request through a provider-neutral adapter;
- exercise the complete runtime offline with a deterministic fixture adapter;
- preserve attempts, raw responses, parsed responses, and technical errors in an
  append-only content-addressed store;
- publish terminal state only after every required store transition succeeds;
- reduce terminal inventories into a deterministic structural receipt.

The layer includes a production OpenAI transport adapter but no command that
implicitly authorizes a provider send. Constructing that adapter requires an
explicit, immutable model-policy reference, a transport policy derived from that
same research policy, the exact SDK version, the official API endpoint and a
process-local API key. A separate scientific manifest and execution
authorization remain mandatory.

## Architecture

```text
authorized manifest reference
        |
        v
typed project, snapshot, and evidence references
        |
        v
visibility allowlist + recursive leakage scan
        |
        v
canonical context + immutable request identity
        |
        v
provider-neutral adapter boundary
        |
        +---- deterministic fixture adapter (tests only)
        |
        +---- pinned OpenAI Chat Completions adapter (explicit execution only)
        |
        v
raw response / parsed response / technical issue
        |
        v
append-only attempt ledger + content-addressed objects
        |
        v
atomic terminal index
        |
        v
offline structural closeout receipt
```

The implementation is split across four packages:

- `aletheia_lab.evaluation.execution_contracts` owns manifest, case,
  model-policy, attempt, and public-safe issue identities.
- `aletheia_lab.context.evaluation_context` owns the visibility projection and
  outbound context boundary.
- `aletheia_lab.model_gateway` owns provider-neutral request, response, retry,
  timeout, cancellation, and deterministic fixture contracts.
- `aletheia_lab.evaluation.attempt_store` and
  `aletheia_lab.evaluation.structural_closeout` own immutable persistence and
  offline reconciliation.

## Trust boundaries

### Manifest authority

The manifest reference is supplied by an external research owner. It must bind:

- one project and snapshot;
- its canonical content hash and immutable source commit;
- an explicit authorization reference;
- caller-supplied created and frozen timestamps;
- public, diagnosis, or evaluator visibility.

The contract accepts only the `authorized` state. A missing, unknown, or false
authorization cannot be represented as a valid execution manifest. Closeout
also requires a current authorization check so an authorization that became
stale after execution produces `not_authorized` rather than a terminal
scientific disposition.

The manifest contains opaque references for research-owned choices. The
readiness layer does not select a provider, model, prompt, resource policy,
retry policy, endpoint, threshold, denominator, or missingness rule.

### Project and evidence boundary

The context builder accepts only typed project references and a
`ProjectEvidenceView`. It does not open project files, query a store, inspect a
database, follow links, or perform network access. The caller must provide the
exact visibility projection that was authorized for the case.

The builder checks project identity, evidence-bundle identity, projection hash,
selected and omitted evidence IDs, source aliases, and deterministic ordering.
Cross-project evidence, a stale projection, an unknown evidence ID, or a
duplicate source alias produces a public-safe blocker before adapter access.

### Leakage boundary

Outbound data is default-deny. The context stores payload-free evidence
references and hashes rather than raw rows, host paths, or evaluator text. A
recursive scan covers mapping keys, mapping values, nested models, and nested
sequences. It blocks, among other threats:

- hidden labels, evaluator rubrics, gold rationales, and outcome fields;
- API keys, tokens, credentials, and email-like personal data;
- absolute Windows, POSIX, and UNC paths;
- raw rows, redacted caches, and prohibited condition or variant labels;
- Unicode control, bidirectional, and non-ASCII homoglyph text;
- non-finite numbers and unsupported outbound values.

Prompt-injection-like evidence text is retained as untrusted data only when it
contains no prohibited field or value. It is never interpreted as an instruction
by the context builder.

## Adapter contract and deterministic fixture runtime

The gateway exposes a provider-neutral `ProviderAdapter`. An adapter receives a
fully prepared `ProviderCall`; it cannot choose the model, rewrite the prompt,
add evidence, inspect project storage, or switch provider after a failure.

The deterministic fixture adapter is keyed by the immutable request identity.
Fixtures explicitly describe each provider step and support:

- valid structured response;
- explicit abstention;
- malformed or empty response;
- timeout and transient error followed by success;
- permanent provider error and retry exhaustion;
- response identity mutation and oversized response;
- cancellation before terminal publication.

Fixtures never inspect a mechanism label or hidden outcome to construct an
answer. They use only opaque request hashes and synthetic response bytes.

The raw response artifact is preserved separately from the canonical parsed
object. Missing usage or cost fields remain `null`; they are never converted to
zero. Parse failure, timeout, cancellation, and provider failure remain visible
technical outcomes.

### Production OpenAI transport

`OpenAIChatCompletionsGatewayAdapter` implements the same provider-neutral
boundary for the frozen OpenAI Chat Completions policy. It is deliberately a
transport implementation, not an execution authority or a scientific variant.
The adapter:

- verifies that the immutable model-policy reference binds the exact scientific
  model-policy hash before a client can be used, while keeping the richer
  transport-policy hash separately reproducible;
- pins `gpt-4.1-2025-04-14`, `openai==2.46.0`, the official API base URL,
  temperature, top-p, seed, output limit, timeout and provider-attempt ceiling;
- disables SDK retries so the gateway remains the only retry authority;
- sends one strict JSON-schema response contract and enables no provider tools,
  retrieval, web access, streaming or provider-side response storage;
- rejects a silent model switch, multiple choices, truncation, refusal,
  malformed usage or an incompatible response envelope;
- maps timeout, cancellation, transient and permanent provider failures into the
  public-safe gateway taxonomy without retaining provider messages, secrets or
  raw provider request identifiers; and
- records unknown usage and cost as `null` rather than fabricating zero-valued
  observations or a volatile price estimate.

The request shape follows the official
[Chat Completions API](https://platform.openai.com/docs/api-reference/chat/create),
while the adapter's retry classification follows the documented
[API error guidance](https://developers.openai.com/api/docs/guides/error-codes/).
The optional `llm` dependency group is required only for an explicitly
authorized live send; importing and testing the provider-neutral package does
not import the OpenAI SDK or access the network.

Every adapter invocation runs behind a gateway-owned monotonic deadline. The
gateway returns a structured timeout by that deadline even if an adapter blocks,
and it discards any response that arrives after timeout or in-flight
cancellation. A timeout may retry only up to the immutable attempt limit.
Caller cancellation after adapter start has one canonical public result whether
it races with adapter completion or response validation; provider metadata and
late response bytes are not published from that race.

Python cannot safely terminate arbitrary code inside a thread. The supervisor
therefore uses a daemon worker to guarantee the gateway return boundary. The
production adapter applies the identical timeout to the OpenAI transport and
disables hidden SDK retries, so abandoned work remains bounded by both the
gateway and the client. Conformance tests exercise that translation without a
live request; they do not authorize external-provider execution.

### Diagnosis variant registry

The complete evaluation census is resolved before any development runner or
provider call can select a variant. B0--B3, A1--A3, FULL and CodeGraph each have
a distinct package-local factory and a versioned capability envelope. Registry
construction binds the implementation source to the frozen model, information,
tool, evidence, prompt and response-schema policies and rejects missing,
duplicated, aliased or policy-incompatible factories.

Request bindings carry the registry and variant hashes, implementation version
and source hash, all policy hashes, context hash, evidence-content hash and the
tool-ledger hash required by retrieval-capable paths. Web, shell, project
execution and silent fallback remain unrepresentable. This registry establishes
implementation and fairness readiness only; it does not run a model, perform
retrieval or establish a scientific result.

## Retry invariants

Every retry reconstructs an attempt from the immutable initial request. The
following fields cannot change:

- manifest and authorization references;
- project, snapshot, case, family, variant, and evidence hashes;
- context, prompt, and response-schema hashes;
- provider, model, model version, and model-policy hash;
- resource and retry policy references.

Only the attempt ordinal, injected timing, and provider-attempt metadata may
change. A changed request identity or adapter binding fails closed. There is no
fallback provider, model, prompt, or context.

## Immutable store lifecycle

The store uses explicit transitions:

```text
prepared
  -> started
    -> attempt_recorded
      -> response_recorded          (when raw bytes exist)
        -> parsed_or_failed
          -> closeout_pending
            -> terminal_published
```

`terminal_published` is represented by an atomic terminal index. A response
file, parsed object, technical issue, or `closeout_pending` ledger entry is not
terminal on its own.

Objects are addressed by SHA-256. Ledger entries bind request, manifest, case,
snapshot, context, prompt, model policy, attempt, response, parse, issue, and
result hashes. Writes use a temporary sibling, flush and `fsync`, and a
create-only link. Existing identical bytes are an explicit idempotent replay;
different bytes are an overwrite conflict.

### Crash recovery and replay

On restart the store verifies directory membership, canonical file names,
contiguous sequence numbers, previous-entry links, object hashes, result links,
and terminal-index ancestry. A stale stage file is ignored but never promoted.
A truncated ledger, forged link, changed object, or cross-request response
blocks further use.

A crash before terminal publication retains its available technical artifacts
without creating a terminal result. The offline reducer reports the expected
request as `incomplete`. Replaying identical events is idempotent and does not
increment attempt count; conflicting replay is rejected.

## Offline structural closeout

The reducer reads terminal inventories only. It verifies current authorization,
expected request identities, counts, provenance, provider/model/context
equality, artifact linkage, duplicate or unexpected terminals, and the store
digest. Its possible states are:

- `complete_uninterpreted`;
- `incomplete`;
- `invalid_provenance`;
- `duplicate_or_replay`;
- `technical_failure`;
- `not_authorized`.

An abstention or parse failure can be structurally complete because the
technical outcome was preserved correctly. Structural completeness is not a
scientific pass. The reducer never calls an adapter or network service and does
not mutate the store.

## Portability and reproducibility

Canonical JSON uses sorted keys, stable separators, finite values, UTC-aware
timestamps, and path-free public references. Tests compare identities across
process hash seeds, different store-root shapes, timezone and locale variables,
and Windows/POSIX path representations.

### Windows and POSIX operation

Store paths are constructed with `pathlib` and public identities never contain
the local store root or a platform separator. Atomic create uses a temporary
sibling followed by a create-only link, so callers must keep each store on one
filesystem and must not share a request directory between concurrent hosts.
Both Windows and POSIX runs treat leftover stage files as nonterminal crash
artifacts. Directory `fsync` is attempted where the operating system exposes a
supported directory handle; correctness never depends on it being available.

The blocking CI configuration runs the complete suite on Python 3.11 and 3.12.
The evaluation profile runs on both interpreters and on the blocking Windows
job. Pip cache keys bind `pyproject.toml`. The dependency-audit workflow prints
a canonical resolved-distribution inventory and its SHA-256 before running the
vulnerability audit.

## Test profiles and runtime expectations

Run the development profile once with:

```bash
python scripts/run_test_profile.py evaluation
```

Run the reproducibility gate three times with distinct process hash seeds:

```bash
python scripts/run_test_profile.py evaluation --repeat 3
```

`--show-command` emits a JSON argv array rather than shell text. This preserves
Unicode, Windows separators, and interpreter paths containing whitespace.

Each evaluation run has a five-minute hard timeout and always reports the 20
slowest tests. Ordinary unit and property tests target two seconds; a complete
fixture-provider integration path targets 15 seconds. The authoritative full
suite remains separate and preserves the global 88 percent coverage gate.

The controlled mutation audit copies source into a temporary directory, applies
one guard mutation at a time, and requires the mapped regression test to fail:

```bash
python scripts/run_evaluation_mutation_audit.py
```

No mutated source is written back to the repository.
The mutation audit is also a blocking CI quality step; it includes explicit
mutations for the adapter deadline and in-flight cancellation guards.

## What this infrastructure does not decide

This infrastructure does not decide:

- mechanism admission, status, inventory, or denominator;
- primary, secondary, exploratory, or excluded analysis membership;
- endpoint, threshold, margin, confidence interval, alpha, power, or
  multiplicity correction;
- missing-data, parse-failure, retry-exhaustion, or abstention scoring policy;
- provider, model, model version, temperature, token budget, or price policy;
- prompt text, variant count, prompt order, case census, seed, split, or stop
  rule;
- ground-truth tier, evaluator rubric, or judge acceptance threshold;
- claim wording or authority to open a sealed outcome;
- whether a technical pass is a scientific admission;
- whether local fixture output can be combined with an external-provider
  estimate.

All such values must come from separately authorized, immutable research
artifacts. Missing scientific policy is never filled by a production default.

The outcome-blind diagnosis feasibility lint and the complete B0--B3, A1--A3, FULL
and CodeGraph policy census are defined separately in
[`diagnosis-evaluation-freeze.md`](diagnosis-evaluation-freeze.md). This freeze
adds scientific policy without weakening any trust boundary in this document.
