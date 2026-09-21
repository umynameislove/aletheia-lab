# Qwen3.8 calibration transport correction

## Chronology and observed boundary

The Qwen3.8 development-only calibration was invoked twice before any model
generation completed.

1. The first invocation stopped during local artifact verification because the
   repository virtual environment could not resolve CMake. It did not start the
   server or create a server log.
2. The second invocation used the corrected environment and loaded the exact
   pinned Q8 model successfully. The loopback server passed `GET /health`, then
   rejected the first chat-completion request with HTTP 400 because `messages`
   was absent at the top level. It accepted no calibration request and started
   no model generation.

The two failure receipts and the retained second server log are stored outside
Git. The forward correction records only their SHA-256 identities and aggregate
facts. No private path, prompt content, response content, main outcome, or
evaluator data is published.

## Root cause

The transport used `canonical_execution_json` for the HTTP request body. That
function is intentionally a hashing namespace: it wraps its input under
`schema_version` and `payload`. It is correct for execution identities but is
not a provider wire serializer. Consequently, llama.cpp received the intended
chat fields nested below `payload` instead of receiving `messages` at the API
top level.

This is a deterministic transport defect. The evidence does not establish a
Qwen model failure, quantization failure, schema-generation failure, memory
failure, or scientific result.

## Prospective correction

The corrected transport serializes the provider payload itself as deterministic
UTF-8 JSON with sorted keys, compact separators, preserved Unicode, and
non-finite numbers forbidden. A byte-level regression test verifies that
`messages` remains top-level and that neither execution-identity wrapper field
appears on the wire.

The observed server also auto-created four slots, although calibration and the
72-request sensitivity are serial. The corrected frozen server flags set one
slot explicitly. This removes an unnecessary runtime default and keeps the
32,768-token context and memory envelope tied to one request at a time; it does
not alter prompts, evidence, decoding, response schema, or comparisons.

The passing calibration receipt schema advances to v2 and binds the technical
correction identity. The candidate, model bytes, llama.cpp revision, chat
template, six B1/A3 development cells, repeatability replicate, seed, sampling,
and 72-request census remain unchanged.

## Re-execution rule

The failed v1 log is immutable. A corrected calibration may run only from the
merged, green correction and must use new create-only v2 receipt and server-log
paths. Another failure must be retained and reviewed; it cannot be deleted or
silently replayed.

The correction authorizes only this synthetic operational calibration. It does
not authorize the 72-request Qwen sensitivity, the GPT main study, outcome
opening, or a registered attempt. Qwen remains a separately reported secondary
model-family sensitivity and cannot be pooled with GPT-4.1.
