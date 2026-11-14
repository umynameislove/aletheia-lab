# Capacity harness and evidence (K17)

The tools exercise authenticated business actions over real HTTP. A configured VU count, a successful functional pytest case, a generated seed or a single calibration run does not establish the primary capacity gate. The canonical acceptance definition remains `Aletheia-Kien-API/02-Phoi-hop/Thu-tai-50k.md`.

## Inputs and preparation

Use an isolated, authorized test database and object root. Keep migration/seed owner credentials separate from API and worker roles. The seed adapter refuses non-fresh targets and requires the schema name `k17_seed_` followed by 32 hexadecimal characters. Create that owned schema, then use the maintained owner migration command:

```text
python scripts/k18_schema_admin.py migrate --database-file PRIVATE_OWNER_URL_FILE --schema k17_seed_UUID
python scripts/k18_schema_admin.py verify --database-file PRIVATE_OWNER_URL_FILE --schema k17_seed_UUID
```

Replace `UUID` with the actual 32-character value. The command does not create a schema, identities or an IdP. Keep the database URL and CSRF signing secret in private files/environment variables, never command arguments or captured shell output. On Windows use ACLs that allow only the operator account; on POSIX private actor/credential files require mode 600.

Save this small plan before generating data:

```json
{
  "schema_version": "k17-native-seed-plan/v1",
  "namespace": "k17_calibration1",
  "seed": 17,
  "workspaces": 4,
  "projects_per_workspace": 5,
  "generations": 1,
  "results_per_generation": 1,
  "size_cycle": ["S","S","S","S","S","S","S","S","S","S","S","S","M","M","M","M","M","M","M","L"],
  "source_rows": {"S":32,"M":128,"L":512},
  "metric_series": {"S":1,"M":8,"L":32},
  "idle_seconds": 900,
  "absolute_seconds": 28800
}
```

This produces 100 distinct principals: five real roles per project, five projects per workspace, four workspaces. Their project-size cohort is 60/35/5. These are debug physical dimensions, not the guide's primary S/M/L fixture.

With `ALETHEIA_HOSTED_DATABASE_URL` and `ALETHEIA_HOSTED_CSRF_SECRET` already loaded privately, run:

```text
python scripts/k17_seed.py --plan small-plan.json --schema k17_seed_UUID --output NEW_PRIVATE_SEED_DIRECTORY --max-bytes 524288000 --max-source-rows 100000 --max-records 100 --max-seconds 300
```

The adapter streams real ZIP inputs through native confirm/refresh and exact local metric analysis, then publishes typed snapshots/results/projections through the current repositories. It verifies source/object hashes, scope, record counts and disk allocation. It writes a public `manifest.json`, inventories and private `actors.jsonl`. Do not include the actor file in a handoff ZIP. An interrupted run retains an `INCOMPLETE` marker; do not treat it as a qualified dataset or overwrite it with a rerun.

The manifest separates logical snapshot/result counts, unique source hashes, actual physical object files/bytes and PostgreSQL allocation. The old metadata-only K05 probe with shared objects is not a substitute for this importer or the primary physical workload.

The primary geometry is 50,000 principals, 2,000 workspaces, 10,000 projects, 20 generations/project and 10 results/generation. The current native metric analyzer cannot generate the required 100/1,000/10,000 claims and a 10,000-node graph. `--require-primary` therefore rejects before writes; the harness also refuses a primary-labelled run when the manifest is unqualified, incomplete or has debug counts/dimensions. Do not relabel the debug seed as primary or remove these guards to obtain a pass. Full metadata geometry alone does not resolve this dimension gap.

## Build a profile, then run it

Start the authorized API and separate worker with the same runtime source, schema, catalog object root and CSRF secret as the seed. Use the K18 deployment runbook for runtime role grants, TLS and process configuration. The standalone harness works on Windows and POSIX; it verifies TLS and supports a private test CA. It never starts Docker, an IdP or paid infrastructure.

```text
python scripts/k17_capacity.py template --target https://TEST_HOST:PORT --origin https://PUBLIC_ORIGIN --host PUBLIC_AUTHORITY --tls-ca-file TEST_CA_PEM --release-sha256 VERIFIED_RELEASE_SHA256 --dataset SEED_DIRECTORY/manifest.json --mode closed --run-label calibration_closed --output closed-profile.json
python scripts/k17_capacity.py run --profile closed-profile.json --actors SEED_DIRECTORY/actors.jsonl --dataset SEED_DIRECTORY/manifest.json --allow-target https://TEST_HOST:PORT --output NEW_CLOSED_RUN_DIRECTORY
```

For a loopback target the exact `--allow-target` option is optional. For an external target it is mandatory and must match the profile's bare origin exactly. It records the intended target; it does not grant infrastructure authority. Redirect following, environment proxy discovery and TLS-verification bypass are disabled.

Generate a separate open profile with `--mode open`, a distinct run label and a new output directory. Before execution, review and lock its arrival rate, duration, warm-up/drain, worker/pending limits, think-time/jitter, roles, S/M/L weights, per-size deadlines, source/dataset/shard identities and all 38 operation dispositions. Profile hashes and exclusive output directories preserve changed/failed/interrupted attempts. Do not edit thresholds after seeing an outcome and reuse the old identity.

The supplied profile is an explicit calibration assumption: project/result/header/claim/citation/graph reads, actual analysis/follow-up jobs, full-file export and a separate foreign-workspace denial. It is not a fully observed user profile or the primary reference mix. Unimplemented/dedicated/browser/IdP operations remain visible in the operation38 inventory. Logout, destructive mutations, invitations, upload/confirm/refresh, reconnect/revoke and browser journeys require their own correctly scoped recipes or dedicated receipts; the generic bounded recipe format does not automatically prove them.

## Identity, sessions and scheduling

Every actor has a distinct session, signed proof, real workspace role and real object bindings. The private actor file hash must match the pinned public manifest. Bounded HTTP preflight verifies `/v1/session` user/CSRF/expiry and current workspace capability/role. A shared owner cookie with different actor labels cannot qualify. Preflight requests are reported separately from offered business actions.

Actor records also carry the actual stored `context_id` and `absolute_expires_at`. The private renewal coordinator uses observed preflight expiry, rechecks aging leases before measurement, and renews through the real `/v1/auth/renew` route. It validates response identity/context/hard expiry and both host-only Secure/HttpOnly/SameSite=Lax cookies with matching proof. Every request supplies its actor's current cookie explicitly; the client's mutable cookie jar never supplies identity.

Renewal and same-principal actions serialize under one actor lock. The original business deadline includes queue, actor-lock and renewal wait. Renewal HTTP/time/failure counters are maintenance, not new engaged users or successful business actions. Unknown rotation, revoked/expired sessions or hard expiry block that actor's work and require authorized session recovery. There is no automatic IdP login or extension of the stored absolute expiry. A long campaign must use the declared authentication policy and include warm-up/bootstrap in its hard-expiry budget; renewing an idle deadline does not extend an eight-hour hard expiry.

Closed mode keeps one loop per configured actor and a shared bounded execution queue. Cadence adjusts for cohort-size fractions versus the pinned S/M/L weights; actual offered mix and 30-second coverage are reported. A cadence longer than the 30-second window cannot establish the engaged target. Open mode schedules independently of completion, uses seeded weighted size choice and rotates through distinct principals in each size. Generator lateness/queue overflow remains dropped offered work. The offering window is held to its declared end, with warm-up, steady and drain sampled separately.

The worker/client counts, queue and actor state are bounded. There are no per-user logging threads, hidden unlimited task queues or preloaded whole-catalog payloads. Real 50k generator headroom must still be measured on the intended hosts.

## Interpret the counters correctly

`planned = offered + not_offered_after_stop`; `offered = terminal + pending`. Terminal outcomes include correct completion, expected denial, failure, conflict, rejection, timeout, interrupted, dropped and outcome-unknown. Expected denials have their own denominator. A defined CAS conflict remains visible and is not silently counted as positive success.

`sent` means the HTTP client began issuing an action's request; it is not proof of network delivery. `admitted` is an observed positive initial response; job admission has a separate durable-receipt count. Jobs are followed through bounded status/updates polling to a real terminal state and usable output. Polling preserves the original deadline and handles scope, cursor order, snapshot/resync and expiry. A poll does not create another engaged principal or action. Whole exports require matching descriptor scope, exact byte count and SHA-256 over the complete stream.

All offered positive actions, including queued pending work, remain in the success denominator and deadline-inclusive histogram. Nonresponses and pending observations are deadline-censored; completed-response latency is reported separately. Logarithmic histogram percentile values are bucket upper estimates, not exact sample percentiles. Each operation × size × run reports its own `n`; fewer than 10,000 observations cannot qualify the primary p99 precision gate.

HTTP request/body byte totals include job polling and same-key transport retries. Session maintenance, preflight and external health/monitoring calls must be added separately when budgeting total SUT traffic. The native fixture's 15-second SUT telemetry window may omit its tail; harness request accounting is authoritative only for its own requests.

## Reconcile ambiguous writes without rewriting failed deadlines

A bounded transport retry reuses the same generated idempotency key. A timeout or interrupted already-sent mutation may still have committed. The report preserves that SLO failure plus a bounded set of scoped key-hash witnesses; it does not submit a fresh post-deadline mutation to manufacture certainty.

After an independently witnessed drain/quiescence, inspect the preserved report with read-only operator credentials:

```text
python scripts/k17_reconcile.py --result RUN_DIRECTORY/result.json --schema OWNED_SCHEMA --database-env ALETHEIA_OPERATOR_DATABASE_URL --output NEW_RECONCILIATION_JSON
```

The bounded operator query reports visible export/job effects or point-in-time absence. Absence is a final no-effect conclusion only when no producer can still commit. A visible descriptor still requires full object hash verification. Keep this supplementary receipt beside the original report; do not turn original 503/timeout/rejection outcomes into success.

## Local evidence and primary campaign

The preserved first native attempt stopped on a missing follow-up `If-Match` in the recipe; current recipes fetch the compact current project revision before submission. The next closed calibration had 491 offered actions, 466 positive completions, eight expected denials and 17 rejected exports: 96.48% positive success, below 99.9%. The following open run had 500 offered, 468 positive completions, 14 expected denials and 18 rejected exports: 96.296%, also below target. All 21 acknowledged jobs reached success in each complete run; 30/32 exports respectively passed full-file verification. The open run quiesced both actual processes and found no durable export for all 18 ambiguous keys. These historical failures remain preserved with their own source identities. Later fixes require new receipts; a green functional pytest case does not change these SLO failures.

After the production export fix (one renderer with four bounded FIFO waiters before view materialization) and private session coordination, the final open calibration retained the same 25-second/20-actions-per-second demand, 60/35/5 cohort, 32 action slots and business deadlines. It scheduled and settled all 500 actions: 486/486 positive actions completed correctly, 14 expected denials, 21/21 jobs succeeded and 50/50 exports passed whole-file verification, with no rejection/drop/timeout/unknown/pending. All 50 export attempts were present in both open runs; the earlier run had 18 rejected exports. The fix changed the implementation rather than lowering the offered export workload or increasing renderer slots/deadlines. A separate real-time renewal fixture produced three actual credential rotations, retained the actor/context, rejected the original cookie with 401 and accepted the current cookie with 200. Neither receipt establishes the primary campaign or a precise p99.

The final run/renewal receipts and current qualification live in canonical `Bang-chung/20261113-k13-k18/k17/result.json`. Their runtime tree SHA-256 is `61e0308f504058123d4b2de9a3f97cb2efda25f713a2e86065fe110d260fe274`, checked unchanged before/after both runs. The native fixture uses real TLS sockets/Uvicorn/PostgreSQL/runtime roles and actual workers, with synthetic preauthenticated sessions. It does not exercise real IdP discovery/login, a separate generator host, deployed infrastructure or 50k.

Before primary testing, resolve the fixture/operation/browser/IdP gaps and obtain authorized resources. Preserve the guide's 5k→10k→25k→50k ramp; at least three closed and three open normal runs each need 600-second warm-up plus 1,800-second steady periods, locked 50k coverage and 5k offered actions/second for the reference open profile. Primary soak remains two hours at 50k; the eight-hour extension is a distinct unrun gate until executed. Stress/breakpoint/spike, hot-tenant fairness, DB40/F26, process/drain and storage DR retain their exact independent definitions. A sequence of manual profile edits is not evidence those scenarios ran.

`tests/integration/test_k17_fault_load_postgres.py` adds a bounded native subset:
one tenant's project-row lock or exhausted one-slot pool overlaps another
tenant's continuous authorized source reads. It observes the actual database or
pool wait, verifies every source payload, checks that the failed mutation has no
visible effect, then releases the fault and verifies one deletion with an exact
replay receipt and no remaining pool waiters. Set `K17_TEST_DATABASE_URL` to an
owned disposable PostgreSQL database; optional `K17_FAULT_EVIDENCE_DIR` receives
safe outcome JSON. The test creates and removes its UUID schema. This is a small
repository-boundary fault workload: it does not cover HTTP/browser behavior,
physical storage failure, the full DB40/F26 matrix, or primary-load SLOs.

Record generator CPU/RAM/FD/NIC/clock/RTT and SUT CPU/queue/pool/locks/WAL/object growth under the same window. The included generator process CPU/RSS samples do not prove 20% machine CPU idle, network headroom or a separate-host envelope. Stop on corruption, cross-tenant data, lost acknowledged effects or uncontrolled growth, preserve evidence and correct the cause.

Report infrastructure/DB/object/bandwidth/observability/generator/IdP/provider costs with the source of each rate and measured billable interval. Divide by correctly completed journeys/jobs, never just 202 ACKs. Local tests authorize no paid provider or infrastructure spend; total local cost is unpriced, not zero. Hardware cost estimates, cloud price estimates and actual approved bills must remain separate. Do not extrapolate a debug dataset's byte cost or 100-VU throughput to primary capacity.
