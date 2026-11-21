# Historical state evidence in hosted project analysis

A historical state analysis checks whether the original contract allowed the
model state observed by the producer during the operation. Current workspace,
project and source permissions control access to the stored result and citation.
An alias, a response value, and a model currently loaded in a producer are
insufficient to infer the historical state.

## Producer and import contract

The producer supplies `historical-state-receipt/v1` with operation and request IDs,
consumer, producer version, execution mode, original receipt digest, and an
observation. The observation is either null or a namespace, generation and
state SHA-256. The graph SHA-256, visible output pair and current alias are
optional diagnostic fields. A separate `historical-state-contract/v1` identifies the
consumer and the one allowed state. Use a null contract if the original is
unavailable. A producer verdict is outside the input schema.

Before confirming the import, an operator verifies the normalized receipt
against its pinned producer evidence, obtains the historical contract from
its own trusted record and prepares a `historical-state-registrations/v1` JSON manifest.
Its `entries` contain these fields.

| Key | Value |
| --- | --- |
| `workspace_id` | Authorized destination workspace |
| `project_id` | Canonical project ID derived from the intended ZIP and mapping |
| `source_sha256` | SHA-256 of the exact ZIP bytes |
| `receipt_sha256` | SHA-256 of the normalized receipt using `canonical_project_sha256` |
| `evidence` | `historical-state-input/v1` object containing the typed `receipt` and `contract` |

The operator can dry-run `UploadProject.confirm` on the exact ZIP and mapping
in a private temporary directory and read `snapshot.mapping.project_id`.
The temporary source ID and capture time are independent of that derived
project ID. The integration test builds this manifest from the same
deterministic ZIP fixtures it submits over HTTPS.

Set the worker's absolute manifest path and the independently stored SHA-256 of
the manifest's exact bytes in the hosted TOML configuration. Use a read-only
worker mount in a managed deployment and give all confirm workers the same pin.

```toml
[worker.historical_state]
manifest_path = "/run/aletheia/historical-state-registrations.json"
manifest_sha256 = "<64 lowercase hex digits from the reviewed manifest>"
```

The manifest is bounded to 128 scopes and 1 MiB. Startup rejects malformed
content, a digest mismatch or duplicate scopes. Canonical catalog support is
required. Confirm binds evidence only after the worker verifies the authorized
source and mapping, by matching workspace, derived project and ZIP digest.
The approved receipt and contract are then part of the immutable snapshot
identity. The client supplies the normal ZIP and mapping. Registry changes
apply to later confirmations. Refreshing a source does not copy historical
evidence forward. Previously published results keep their original snapshot.

## Requests, results and replay

An authorized confirm output includes `historical_state_project_id` when the
snapshot has registered evidence. A refresh output advertises
`historical_state_available: true` when its returned snapshot has evidence.
Ordinary import and refresh outputs omit these fields.

Send the authenticated request to
`POST /v1/workspaces/{workspace_id}/projects/{project_id}/analyses`
with the usual CSRF header and idempotency key.

```json
{
  "schema_version": "product-api/v1-draft",
  "snapshot_id": "<confirmed snapshot ID>",
  "question": "Was the state used by the original operation allowed?",
  "backend": "historical_state"
}
```

The normal job output points to a stored Product result with
`view.evidence[].historical_state` in the canonical record. That evidence gives the
original operation/request IDs, receipt and contract digests, the observed
and allowed state, execution mode and decision. It cites the exact snapshot
and imported source. An authorized citation read and JSON export deliver the
same evidence. Model `historical-state-evidence/v1` is frozen in the durable job request.
Repeating the same key and payload resolves to the original job. Changing
snapshot, question or backend under the same key conflicts when both requests
are otherwise valid. The default backend remains `local_evidence`.

| Decision | Meaning |
| --- | --- |
| `allowed` | The observed state matches the historical contract. |
| `denied` | The observed state differs from the historical contract. |
| `unknown` | The supported contract is present and the observation is missing. |
| `underdefined` | The supported consumer's contract is unavailable. |
| `unsupported` | The consumer is outside the supported predicate family. |

Unknown, underdefined and unsupported are completed analyses with an
abstention. `fresh` identifies a new execution attested by the approved
producer receipt. `replay` uses retained evidence. The API evaluates the
historical predicate and has no producer runtime or downstream sink. The
configured pin authenticates the operator's selection by content. Assurance
about the underlying observation comes from independent producer evidence.

## Verify locally

With the hosted development dependencies and an isolated PostgreSQL 16 test
database, run from this checkout.

```sh
PYTHONPATH=src python -B -m pytest -o addopts= -q \
  tests/unit/test_historical_state_evidence.py tests/unit/test_historical_state_projection.py
PYTHONPATH=src K05_TEST_DATABASE_URL="$LOCAL_TEST_DATABASE_URL" \
  python -B -m pytest -o addopts= -q \
  tests/integration/test_k18_historical_state_pipeline.py
```

Follow the [fixture instructions](../fixtures/historical-state/v1/README.md) to generate
a new CPU/SQLite receipt. Pass its absolute JSON path in
`HISTORICAL_STATE_FRESH_INPUT` to include it in the hosted test. The test writes
`historical-state-product-witness.json` under its pytest temporary directory with
verdicts, stable source and receipt digests, object and result digests, and
request-scope checks. GPU confirmation cohorts and dashboard acceptance use
separate evidence.

Apply persistence migration 19 to a verified v18 schema before enabling this
backend. The migration extends the job CHECK constraint for the new analysis
model. On a registered source, the admitted receipt is part of the
`hosted-upload-snapshot/v3` identity. The snapshot retains the original mapping
and its metric dimensions. The historical result uses
`hosted-upload-result/v2` and `p6-product-view/v2`, with a typed
`historical_state` evidence item. Legacy mappings use an unknown unit in
that historical view. Metric results keep the format selected by their mapping.
Drain v18 workers before migration. Their readiness check requires the v18
ledger, so a rollback to v18 also requires a verified v18 data recovery point.

Use a fresh schema for disposable fixtures from pre-release builds. Earlier
builds used a different migration 19 checksum or stored historical receipts
under the metric snapshot schema. Retained data from those builds requires a
separately verified upgrade or recovery from its own backup.
