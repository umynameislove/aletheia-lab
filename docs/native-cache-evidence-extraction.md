# Native cache evidence extraction

This development comparison asks whether a language extractor preserves endpoint
roles and uncertainty as faithfully as a producer-aware parser on identical
evidence. Both use the existing finite artifact-lineage resolver. It does not
assume a language model improves over a parser, or test causal diagnosis accuracy.

## Native source and independent reference

The source is actual stdout and query logging from `joblib.Memory(verbose=20,
mmap_mode=None)` while loading locally generated cache values. Joblib's query
argument hash identifies filtered function arguments, not the serialized output's
SHA256. The loading message names a directory before opening `output.pkl`.
Those fields cannot warrant the identity of consumed bytes. See the installed
version's pinned [backend](https://github.com/joblib/joblib/blob/1.5.3/joblib/_store_backends.py)
and [query implementation](https://github.com/joblib/joblib/blob/1.5.3/joblib/memory.py).

The five fixed controls are A requested/A consumed, B substituted at A's cache
key, A restored, B requested/B consumed, and A substituted at B's cache key.
These are synthetic values and controlled file replacement, not natural incidents
or another admitted model failure mechanism. All serialized inputs are generated
inside a new private subprocess cache. No model fitting or historical artifact
loading occurs. Never load pickle files from an untrusted source; Joblib documents
that loading can execute code in its
[persistence security guidance](https://joblib.readthedocs.io/en/latest/user_guide/persistence.html#security-considerations).

Caller intent is frozen from the original serialized snapshot before replacement.
The consumer bridge reads the file actually opened by the native backend and
passes that exact buffer to the original `joblib.numpy_pickle.load`. Returned
values and fingerprints are checked against the fixed replacement schedule,
independently of the extraction output. The bridge is instrumentation, not an
authentication service or evidence that a pathname alone identifies consumed bytes.
The source bundle retains the native texts, independently frozen A/B snapshots,
control schedule, consumer observation and reference separately.

`attempt-0` is a caller-bound scope within one payload, not a globally authenticated
request identifier recovered from native logging.

## Matched inputs and scoring

Each load supplies two views. `log_only` has native messages and caller intent;
`with_consumer` also includes the same-buffer consumed-byte witness. The parser
and language extractor receive the same exact document text, identities, producer
semantics and caller-bound scope. Private control labels, answers, returned values,
absolute paths and serialized bytes never enter the API payload.

The parser uses the native producer grammar and versioned endpoint fields. Native
cache keys, reported paths and success messages never become endpoint facts.
Log-only reference is therefore ambiguous, including in healthy cases. A guessed
singleton cannot earn credit just because it coincides with hidden runtime truth.
The reference checks requested and consumed roles independently before applying
the resolver, not status equality alone.

The language model proposes endpoint facts with exact digests and document/field
citations; it does not diagnose or override source authority. Both proposed and
parsed facts run through the same unchanged resolver. Assessment retains:

- exact visible fact frame, grounding and role/provenance errors;
- eligible facts omitted, including contrary evidence;
- raw proposed resolution and unwarranted singleton commitments;
- conservative exact-frame guarded resolution, paid cost and latency;
- paired results for each control and the full failure denominator.

The guard can reject harmless omissions. Its safe accepted results do not establish
flawless model extraction. This separation follows the translation-versus-inference
distinction studied in [LINC](https://aclanthology.org/2023.emnlp-main.313/) and
semantic log roles studied in [SemParser](https://arxiv.org/abs/2112.12636), not a
claim to invent those architectures.

Ten views remain dependent observations from one controlled producer cluster.
The report is a descriptive census with no population interval, natural-source
generalization or untouched final-validation claim. Document structure and this
source are exposed development material. A parser ceiling, tie or model failure
is a valid result; it must not trigger weakening the comparator or hiding controls.

## Private lifecycle

`scripts/native_cache_extraction.py` implements `prepare`, `preflight`, `execute`
and `verify`. Preparation creates the native source in a private directory outside
Git. Preflight checks independent reference, code/runtime/source identities,
bounded inputs and the actual SDK arguments using an injected client with no
network transport. That synthetic client does not measure model efficacy.

The paid path uses the existing bounded development caller: GPT-4.1 snapshot
`gpt-4.1-2025-04-14`, `https://api.openai.com/v1/chat/completions`, temperature 0,
seed 731, at most 8,192 input and 1,024 output tokens per call, `store=false`,
90-second timeout and zero SDK retries. Ten calls reserve $0.245760 at the frozen
$2/$8 per million token rates, under a $0.25 ceiling. This is a reservation, not
an observed charge; rates correspond to the
[pinned model documentation](https://developers.openai.com/api/docs/models/gpt-4.1).
`store=false` is not a zero-retention guarantee by the provider.

Execution requires the current approved plan digest and a fresh paid-transfer
decision. It creates an immutable lease before calls, saves each terminal response
privately and prohibits automatic replay. Technical/invalid results remain in all
ten slots. Verification reconstructs assessment and aggregate from saved responses
without network calls. A crash retains completed slots and the lease; it does not
authorize rerunning them. The operator can inspect retained slots before deciding
on any separate continuation.

Example offline preparation from the checkout:

```sh
PYTHONPATH=src python scripts/native_cache_extraction.py prepare \
  --root . --study-dir ../memory/native-cache-extraction-development-v1
PYTHONPATH=src python scripts/native_cache_extraction.py preflight \
  --study-dir ../memory/native-cache-extraction-development-v1
```

Source/cache files, private plans, raw responses and receipts are not repository
content. The older development datasets and registered outcomes are unchanged.

## Canonical source-locator correction

The retained native experiment uses an application-defined source locator:
`/documents/{document.id}/{field}` selects a visible document by ID, decodes its
JSON `text`, and then names an endpoint field in that decoded object. It is not
an [RFC 6901 JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901.html#section-4)
into the provider envelope: `documents` is an array and `text` is a string.
The first prompt left that two-stage interpretation implicit, and its schema
allowed any pointer string. A correct document citation can therefore fail the
required field-level locator contract without having an incorrect endpoint.

`scripts/native_cache_citation.py replay` verifies the retained experiment first,
then reports original strict metrics separately from role/digest/source fidelity
and a supplementary parser-assisted citation replay. No original module, source,
response, assessment or receipt is modified. Alias resolution starts from the
cited visible ID or zero-based index, not a search for a matching digest. It
requires the exact document hash, independently bound scope/authority, recognized
closed producer grammar and the proposed role/digest. Explicit field aliases
must preserve the field. Whole-`text` citations can be expanded only because the
accepted document grammar contains exactly one eligible endpoint. This supplies
omitted citation precision through the parser; it is not merely a syntactic
rewrite. Counts of text expansion and explicit-field locator substitution are
reported separately. Duplicate aliases, wrong source/field/hash/role, invented
endpoints and contrary-witness omissions never become accepted facts.

The separate prospective development interface check uses the same frozen visible
documents, model, budgets, parser and strict resolver. Its prompt explicitly
defines the custom locator and its response schema lists every visible document
ID crossed with both possible endpoint field names. The enum is built from IDs,
not parser eligibility or gold facts; unsupported combinations remain possible.
It does not constrain proposed digests or hashes to the answer. Supported
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
can constrain enum syntax, but they cannot ensure the model chooses grounded,
semantically correct evidence. Live results use the original strict scorer with
no citation normalization and separate content-fidelity diagnostics.

This is a technical interface correction within an established extraction plus
symbolic-inference architecture, not a method novelty claim. The separation of
translation and inference also appears in [LINC](https://aclanthology.org/2023.emnlp-main.313/);
it does not guarantee faithful extraction or usefulness over the strong parser.
Cached replay is post-hoc development analysis. A fresh paid run tests the
revised generation contract on already exposed inputs, not independent
validation, natural incidents or semantic superiority.

Offline commands:

```sh
PYTHONPATH=src python scripts/native_cache_citation.py replay \
  --predecessor-dir ../memory/native-cache-extraction-development-v1
PYTHONPATH=src python scripts/native_cache_citation.py prepare \
  --predecessor-dir ../memory/native-cache-extraction-development-v1 \
  --study-dir ../memory/native-cache-citation-development-v1
PYTHONPATH=src python scripts/native_cache_citation.py preflight \
  --predecessor-dir ../memory/native-cache-extraction-development-v1 \
  --study-dir ../memory/native-cache-citation-development-v1
```

`execute` requires this new plan's digest and separate paid-transfer approval;
`verify` reconstructs it offline from all ten retained calls. Preparation and
verification bind the unchanged predecessor tree, revised contract and actual
per-view SDK schemas. The original directory cannot be reused or overwritten.
The ten-call reservation remains $0.245760 at frozen rates, with a $0.25 ceiling;
no call is automatic and no failed slot is silently retried. The new private
directory contains only the new plan and its own execution artifacts; it reuses
the predecessor source rather than generating a second native corpus.

## Independent SQLite producer and nested-schema transfer

`scripts/sqlite_evidence_transfer.py` tests the selected cited-fact extraction
interface on a second native producer: Python's `sqlite3` reading a BLOB from an
isolated in-memory table. SQLite is a relational store here, not a cache. Its
native [trace callback](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.set_trace_callback)
reports statements executed by SQLite, not the identity of a returned column.
[BLOB storage](https://sqlite.org/datatype3.html#storage_classes_and_datatypes)
preserves the supplied bytes. The SQL lookup key and returned declaration are
therefore not substitutes for hashing the returned BLOB. SQLite is
[public-domain software](https://sqlite.org/copyright.html); the Python wrapper
uses the [PSF license](https://docs.python.org/3.12/license.html).

The source has the same five intervention roles as the development comparison,
but different artifact bytes, a different native retrieval mechanism and a new
nested wrapper grammar. Before generating evaluation bytes, preparation records
the prompt, schema policy, parser, resolver, controls, views, metrics, budgets,
producer runtime and code identities in `selection.json`. Adapter tests use
separate `offline-*` fixtures. This is **documentation-informed producer/schema
transfer with an adapted strong parser**, not zero-shot unknown-schema transfer
or an untouched source whose documentation was never examined. Neither new
model outputs nor evaluation bytes are used to tune the adapter before this
prospective run. An outcome-driven revision belongs to a subsequent development
variant, not the same evaluation receipt.

Every control performs a real native SELECT and captures three evidence layers:

| Visible source | Bound interpretation | Endpoint authority |
|---|---|---|
| Native SQL trace and returned `manifest.declared_sha256` | Executed statement and row declaration | Neither establishes returned-byte identity |
| Caller pin `selection.expected.sha256` | Intended artifact digest frozen before replacement | Requested endpoint |
| Same-buffer witness `cell.sha256` | SHA256 of the BLOB actually returned by SELECT | Retrieved endpoint, not proof of downstream model consumption |

Scope and authority are supplied by the trusted local harness, not recovered
from a document's claims about itself. The witness is controlled instrumentation,
not host authentication, request attestation or a production trust service. No
arbitrary database, external SQL, serialized model or historical pickle is loaded.
Both replacement directions, restoration and legitimate B/B remain in the census.

The independent reference hashes the frozen pre-replacement artifact and the
exact saved returned buffer, checks the intervention schedule, and constructs
the full role/digest/locator/document-hash frame without calling the parser.
Preparation compares the parser to that frame and to a separate expected visible
status. Without the BLOB witness, all five views are ambiguous; with it, the
expected statuses are no fault, fault, no fault, no fault, fault. The reference
does not credit a guess that happens to match hidden runtime truth.

The LLM and parser receive identical document bytes, producer semantics and
caller-bound scope. Both use the unchanged finite resolver. The custom locator
now permits fixed nested object paths after decoding the selected document's
`text`; it does not implement general JSON Pointer escaping or array traversal.
The response enum crosses **all** visible IDs with **all** grammar paths, including
ineligible declaration paths. It never encodes eligible ID/path pairs, gold
digests or gold document hashes. Full exact-frame equality is still required
for guarded admission; live output is never normalized.

Primary reporting compares exact cited frames with the matched parser and retains
all ten scheduled slots. Diagnostics separate role/digest/document-hash equality,
locator-only discrepancies, grounding, unsupported facts, eligible omissions,
raw visible-resolution violations, unwarranted singleton commitments, guarded
coverage/status, cost and latency. Locator-only equality is not proof of valid
source provenance; it does not bypass the exact-frame guard. Technical and invalid
results remain failures in the denominator. The comparator is neither weakened
nor tuned to make the LLM appear useful.

Five controls crossed with two views give ten dependent evaluation slots but
only **six distinct complete SDK inputs**. Case IDs distinguish private execution
slots and do not enter the API payload. This is one new producer cluster in
addition to the earlier development producer, not ten independent replications.
No population interval, natural-incident generalization, downstream-consumption
claim, causal-mechanism admission or LLM superiority follows from offline tests.
The translation/inference distinction follows prior architectures such as
[LINC](https://aclanthology.org/2023.emnlp-main.313/); the scientific question is
source fidelity and false authority across this explicitly bounded transfer.

### SQLite lifecycle and paid boundary

`prepare`, `preflight`, `execute` and `verify` use one private directory outside
Git. The source is independently replayed with native SQLite before execution;
preflight audits the actual bounded caller's complete SDK arguments with an
injected non-network client. It captures ten scheduled wires and six distinct
inputs. This is an interface check, not a model result. Offline fake execution
tests resource ceilings, malformed output, all-slot failures, immutable leases,
code/runtime/source drift, tampered receipts and hash-seed stability.

The paid policy remains GPT-4.1 `gpt-4.1-2025-04-14` at
`https://api.openai.com/v1/chat/completions`: maximum ten calls, temperature 0,
seed 731, at most 8,192 input and 1,024 output tokens, timeout 90 seconds,
`store=false` and zero retries. The maximum reservation at frozen rates is
$0.245760 under a **$0.25 ceiling**, not an observed charge. Only synthetic
SQLite statements, nested declaration/pin/witness documents, their source hashes,
producer semantics and the extraction contract are sent. Control names, case
IDs, answers, returned BLOBs, private frozen snapshots, file paths and earlier
research outputs are withheld. Provider retention is not eliminated by
`store=false`.

```sh
PYTHONPATH=src python scripts/sqlite_evidence_transfer.py prepare \
  --root . --study-dir ../memory/sqlite-evidence-transfer-v1
PYTHONPATH=src python scripts/sqlite_evidence_transfer.py preflight \
  --root . --study-dir ../memory/sqlite-evidence-transfer-v1
```

The operator reviews this payload, destination and ceiling before a paid
`execute --confirm-plan-sha256 <current-plan-digest>`. Execution creates a lease
before the first call and retains every terminal response privately. A used
directory cannot be automatically rerun. `verify` reconstructs each assessment
and the aggregate offline without network calls. A tie, extraction failure or
false-authority result is reported as observed; none authorizes a retry or
changes earlier development receipts. Acquisition efficacy is a separate
conditional question, not a prerequisite for this extraction-only comparison.
