# Predecessor provider failure audit

The verified predecessor store has identity
`0e3e6e64e99b8436680c425de968c5e3f2369d57e06cc59002999d381f28a470`.
All 360 request shards were checked using the independent store integrity reader.
There are 258 parsed terminals and 102 provider-failed terminals. Every provider
failure has issue code `permanent_provider_error` at stage `model_gateway`;
none has a persisted raw response. The audit does not modify the store.

## What can and cannot be concluded

The predecessor OpenAI adapter maps several distinct failures to that same code:
non-stop finish reasons, refusal, wrong model/response shape, invalid usage,
and non-retryable provider exceptions. The opaque attempt reference is not a
recoverable provider response or a recorded finish reason. The stored artifacts
therefore do not identify the exact cause of any of the 102 failures.

Output truncation is a possible explanation, not an established finding.
Neither changing the response schema nor increasing a token budget would prove
that the historical failures were diagnosed or resolved. The frozen resource
budget must not be changed silently.

The later CSR-03 synthetic compatibility run is distinct from this predecessor
audit. Its three probes all recorded `provider_output_truncated` at the
registered 600-token ceiling, so truncation is established for those three
synthetic probes only. It must not be generalized to the 102 predecessor
failures.

## Recovery requirement

Before authorizing another full run, the recovery adapter must distinguish safe
failure categories (including truncation, refusal, invalid envelope and provider
HTTP error) without persisting arbitrary exception text, credentials, or refused
content. Tests must preserve failure accounting and non-retryable semantics.
Compatibility of the exact recovery schema with the provider remains unverified
by offline tests. Paid compatibility checks require separate operator approval.

The audit is one completed part of recovery preparation, not an execution-ready
receipt or evidence that 360 requests will succeed. The original results and
denominators remain unchanged.

## Implemented recovery diagnostics and rehearsal

`OpenAIRecoveryAdapter` inherits the unchanged pinned transport and adds
allowlisted terminal reasons for truncation, refusal/content filtering, invalid
envelopes, HTTP errors and incompatible schemas. Non-retryable failures remain
non-retryable. Unknown causes remain generic; the adapter does not guess.
Tests verify these reasons survive immutable terminal publication without
storing provider exception messages or refused content.

The [OpenAI response contract](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
distinguishes stop reasons including length and content filtering; these are
transport diagnostics, not human-support labels or scientific dispositions.

```bash
PYTHONPATH=src python scripts/claim_support_recovery.py rehearse
PYTHONPATH=src python scripts/claim_support_recovery.py require-live-ready
```

Rehearsal builds 360 distinct recovery requests using a synthetic offline
authority, proves no request identity overlaps its predecessor counterpart,
and preserves evidence and variant bindings. It executes no provider call and
publishes no authorization or attempt store. The deterministic B0 response uses
the new envelope while retaining exactly the same observation text.

Local token accounting includes the recovery instruction and reports serialized
schema tokens separately. The estimate is at frozen rates, excludes relation
assignment and retries, and is not an authorized cost ceiling or a claim about
provider-billed tokens. Provider schema overhead still requires validation.

The production boundary is implemented separately from rehearsal. It requires
a three-schema synthetic compatibility phase before full diagnosis authority,
uses phase-specific create-only leases and stores, and independently regenerates
each completion receipt. Until this code is merged, CI is green and an operator
creates the phase authorization with an explicit cost ceiling, live execution
remains blocked. Do not use the predecessor executor or the rehearsal's
synthetic authority.
