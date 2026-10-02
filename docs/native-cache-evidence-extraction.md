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
