# Hosted claim evidence review

Review checks a typed authorization assertion against the original historical
receipt and contract. The original result remains the source of the selected
claim and its citations. The review produces its own immutable receipt.

## API and authority

Send `POST /v1/workspaces/{workspace_id}/results/{result_id}/claims/{claim_id}/reviews`
with the current session, CSRF proof, and an idempotency key.

```json
{"schema_version":"product-api/v1-draft","asserted_verdict":"denied"}
```

Admission and publication require the existing review permission on the exact
result and its source. The server resolves the requested claim and binds the
workspace, project, source, snapshot, result, claim, citation set, contract and
policy. The candidate hash includes the requested assertion. During job
retention, reusing the key with the same candidate returns the same job.
A different authorized candidate with the same key returns a conflict.

The existing job output returns the receipt after the job succeeds. Save the
job ID as the review ID for subsequent reads.
`GET /v1/workspaces/{workspace_id}/results/{result_id}/claims/{claim_id}/reviews/{review_id}`
checks current project and source read permission, including after job pruning.
The receipt follows the parent result's object retention and deletion policy.

In the hosted dashboard, select a claim in Reader, Table or 2D and open
**Check claim evidence**. Choose the assertion to check. The review link
pins the claim and review ID for reopens. **Your review** holds the
user's own notes.

Download by appending `/export?format=json|markdown|pdf` to the receipt URL.
Export requires current project and source export permission. JSON returns
the retained receipt bytes. Markdown and PDF include its scope, finding,
citations and digest. All three return `X-Aletheia-Receipt-Sha256` for the
canonical receipt and a strong ETag for the downloaded bytes.

## Verification and persistence

- `approve_in_scope` means the assertion matches the decision derived from
  the original evidence. `revise` means it contradicts that decision.
- `unknown` records missing observations or authority. `unsupported` records
  cases outside the check's scope. `tool_error` records evidence the check
  could not reconcile. These outcomes are distinct from queue failure.
- A local receipt records `local_check` and `not_incurred`. The finding's
  execution mode identifies the original evidence producer's fresh or replay operation.
- The worker verifies the binding again before publishing. Lease, deadline,
  current rights, object metadata, object reference, review index and job
  completion participate in the publication transaction.

Apply the persistence migrations through version 21 and reapply the
runtime role grants. The API reads the review index. The worker publishes
receipts through the existing object leases and writes the provider attempt ledger.

## Optional provider comparison

The default path works offline. For one approved comparison, pin the
operator's plan in the API and worker settings. Supply
`ALETHEIA_HOSTED_CLAIM_PROVIDER_KEY` through the worker's secret environment.

```toml
[worker.claim_provider]
manifest_path = "/run/aletheia/claim-provider-plan.json"
manifest_sha256 = "<SHA-256 of the approved manifest bytes>"
```

The expired or mismatched plan cannot authorize a send. The plan identifies
one workspace, actor, candidate and exact request digest, together with the
model, HTTPS destination, call and output limits, budget and expiration.
The provider receives a typed assertion and opaque, snapshot-scoped tags for
the observed and allowed states. Provider output is recorded as a separate
comparison with the host finding. The request uses `store: false`. Provider
data handling is governed by the provider's policies.

The worker verifies current rights and persists the attempt, original
deadline and budget hold before sending. It makes at most one application
send per plan on the live ledger. If the answer or usage is uncertain, the
hold remains available for reconciliation. Reclaiming the job or retrying
the HTTP request reuses the attempt. A late outcome may settle the ledger
but cannot extend the original deadline or change a published receipt.
Disable provider configuration before restoring an older database.
Reconcile outstanding attempts against the provider before enabling a
newly approved plan on the restored installation.

Run the local checks with the hosted dependencies and an isolated PostgreSQL
test database in `K05_TEST_DATABASE_URL`.

```bash
python -m pytest -q tests/unit/test_hosted_claim_review.py \
  tests/unit/test_hosted_claim_review_provider.py \
  tests/unit/test_k02_claim_review_http.py \
  tests/unit/test_k02_claim_review_export.py \
  tests/integration/test_k20_claim_review_postgres.py \
  tests/integration/test_k21_claim_provider_postgres.py
```
