# Structured-output compatibility and failure diagnostics

## Evidence and limits

Both retired synthetic attempts have three terminal `provider_output_truncated`
failures: first at a configured 600-token cap, then at 2,048. Independent archive
audits reproduce their original authorization, lease, receipt and store identities.
The adapter assigns this code to Chat Completions `finish_reason="length"`.
[OpenAI's Chat reference](https://developers.openai.com/api/reference/resources/chat)
defines that reason as reaching the generation limit. However, the old adapter
discarded the usage, partial content and finish metadata when raising the error.
The archived records therefore do not independently reveal actual output token
counts, a repeated token sequence, or the provider's internal decoding behavior.

The old conclusion that a larger cap alone would resolve compatibility was not
established. A valid synthetic response fits in roughly 65 local tokens; this
shows that the requested schema does not intrinsically require more than 2,048
tokens, not that every model generation must finish within that cap. The exact
cause of the provider's repeated truncation remains unconfirmed without new live
evidence. These probes do not establish the cause of all 102 historical failures.

## Prospective transport correction

CSR-03S binds a separate transport contract, recovery protocol v3 and authorization
v3. Its request identities cannot reuse either failed compatibility authority.
The scientific envelope `diagnosis-provider-output/2` and its archived registration
remain unchanged. The correction has four parts:

1. The wire schema omits string regexes, including lookahead and Python-specific
   Unicode escapes. These are a suspected constrained-decoding risk, not a proven
   provider bug. The wire schema still fixes closed objects, required fields,
   terminal unions, array bounds, and the exact visible citation IDs.
2. The original full schema remains the local acceptance gate. Additionally,
   claim text, material-part text and abstention reason are checked for canonical
   Unicode before canonical JSON can normalize them. Nothing trims, repairs,
   relabels or rescues a returned claim. The normalizer independently enforces
   the same text boundary.
3. Strict-schema preflight now traverses `anyOf` branches and rejects nested
   objects that are not closed or do not require all properties.
4. Failed response records retain allowlisted finish reason, configured cap,
   available token usage, UTF-8 byte count, content SHA and snapshot-match status.
   They do not retain partial response text, refusal text, credentials or arbitrary
   provider errors. Missing/invalid usage remains unknown, never zero. Existing
   attempt records without diagnostics retain their original serialization.

OpenAI documents the [Structured Outputs subset](https://developers.openai.com/api/docs/guides/structured-outputs),
including nested unions and string constraints. Documentation alone does not
prove acceptance or correct generation for every concrete regex. We therefore
test the pinned SDK's actual HTTP serialization with a mock transport and require
a separate live synthetic gate. The request continues using the supported
`max_tokens` parameter; changing its spelling is not claimed as a causal fix.

## What does not change

The snapshot remains `gpt-4.1-2025-04-14`. Every model-backed variant retains the
same 2,048-token output cap, prompts, evidence, tools, context and retry policy.
There are still 315 model-backed and 45 deterministic requests in the diagnosis
census. No output from a failed run is reused or pooled. Selection remains fixed
at 200 eligible claims; code tests cannot guarantee enough eligible claims or
scientific validity. Human packets and main/sealed outcomes remain unopened.

The rehearsal reports both local-acceptance and provider-wire schema-set hashes,
the transport hash and the new protocol hash. Costs use the actual projected
schema, both permitted attempts and a bounded overhead allowance. These are local
estimates, not exact provider bills or a server-enforced account spending cap.

## Execution and acceptance

Merge with green CI before authorizing a new private `claim-support-recovery-run-v3`.
Retain both prior directories. Every authorize/preflight/execute command now
requires `--retired-structured-output-run` pointing to the failed v2 directory,
in addition to `--retired-compatibility-run` for the first failure. Both histories
are independently checked, and overlap with either is forbidden.

Use the [operator sequence](claim-support-normalization-recovery.md#recovery-execution-boundary):
rehearse → authorize compatibility → preflight → execute once → independent verify.
Never chain a failed preflight to execution; use `&&`. New authorization and
confirmation hashes must come from the merged source, not this document.

Only `recovery_compatibility_pass` reconstructed from all three parsed and
normalized synthetic records permits separately authorizing diagnosis. Offline
tests, HTTP 200, or three terminal records alone are not that gate. Another
failure remains terminal; inspect its diagnostics rather than restarting the
same lease, raising the cap automatically, or launching 360 requests anyway.
