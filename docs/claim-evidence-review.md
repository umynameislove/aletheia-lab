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

## Verification and persistence

- `approve_in_scope` means the assertion matches the decision derived from
  the original evidence. `revise` means it contradicts that decision.
- `unknown` records missing observations or authority. `unsupported` records
  cases outside the check's scope. `tool_error` records evidence the check
  could not reconcile. These outcomes are distinct from queue failure.
- The receipt records `local_check` and `not_incurred`. The finding's execution
  mode identifies the original evidence producer's fresh or replay operation.
- The worker verifies the binding again before publishing. Lease, deadline,
  current rights, object metadata, object reference, review index and job
  completion participate in the publication transaction.

Apply persistence migration 20 to an independently verified migration 19
schema and reapply the existing runtime role grants before enabling managed
traffic. API roles read the review index, and worker roles insert receipts
through the existing object leases. The new review has a separate version
from product views and runs without a provider request.

Run the local checks with the hosted dependencies and an isolated PostgreSQL
test database in `K05_TEST_DATABASE_URL`.

```bash
python -m pytest -q tests/unit/test_hosted_claim_review.py \
  tests/unit/test_k02_claim_review_http.py \
  tests/integration/test_k20_claim_review_postgres.py
```
