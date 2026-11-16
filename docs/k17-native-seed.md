# K17 native seed/importer and its qualification boundary

The importer creates real deterministic ZIP sources, validates actual mappings,
refreshes the same project through distinct generations, and publishes exact
native metric analyzer results with real canonical projections, parent/source
scope, immutable bytes and hashes. Each result is an independent question for
its snapshot; a follow-up conversation chain is a separate workload recipe.
The importer never relaxes the analyzer or inserts arbitrary result IDs.

The supplied small plan creates 30 unique synthetic principals, 2 workspaces,
6 projects, 12 snapshots, 24 results and all five roles. S/M/L have independent
physical source rows (32/128/512), metric series per run (1/8/32), and actual
claims (2/16/64). JSON result bytes and graph nodes/edges are measured from the
real generated objects; a size label is not accepted as evidence of file size.

The full cardinality plan describes 50,000 principals, 2,000 workspaces,
10,000 projects, 200,000 snapshots and 2,000,000 results: 25 distinct actors
per workspace, five projects with one actor of each role per project, twenty
generations and ten questions per generation. Its project cycle is S/M/L
60/35/5 and actual source rows are 1,000/10,000/100,000. It streams generation
and result inventories instead of constructing the whole dataset in memory.
Every source payload and every managed object file is hashed again at the end.
Logical bytes, unique source hashes, physical object files, PostgreSQL column
sizes and actual schema allocation are reported separately. Deduplication is
measured; logical object/record counts are never called physical diversity.

## Current primary gate

The small plan and the legacy full-cardinality v2 plan still report
`primary_dataset_qualified=false`. The v3 plan in `k17-seed-window-catalog.json`
pins 100/1,000/10,000 source-based claims and 202/2,002/20,002 graph nodes by
stratum. It keeps separate identities for 50,000 principals and adds verified
50-member shared and 500-member hot workspaces without granting access to
private projects. The loader verifies counts, scope and hashes after import.
Qualification of the resulting dataset requires a completed import. Heavy-job
journeys and load SLOs remain separate K17 acceptance gates.

## Prepare a private target

Use a dedicated, disposable empty schema named `k17_seed_` followed by exactly
32 lowercase hex characters. Apply the current canonical, project-policy and
review migrations using the existing owner migration tools first. The seed
command never creates/migrates a schema, drops data or grants runtime roles.
It refuses an existing identity/workspace/project/source dataset or a schema
whose owner is not the connected role. Never point it at production data.

Set the owner database environment variable and the same CSRF signing secret
used by the target runtime through the existing secret mechanism. Do not put
secrets in command arguments, manifests, logs or chat. Use a new absolute output
directory under an existing private parent. The importer initializes its own
managed store at `OUTPUT/objects`; configure the isolated runtime against that
schema/store for calibration. It does not start or enable a server.

```text
python scripts/k17_seed.py --plan docs/k17-seed-small.json --schema k17_seed_32_LOWERCASE_HEX_CHARACTERS --output ABSOLUTE_NEW_PRIVATE_DIRECTORY --max-bytes 524288000 --max-source-rows 10000 --max-records 100 --max-seconds 300
```

The example schema token must be replaced by a real UUID hex value. All row,
record, byte and time guards are explicit and validated before business writes.
Free disk reserve is checked first and throughout. Byte admission conservatively
includes logical bytes written plus measured database allocation; it can stop
earlier than actual deduplicated disk usage. On failure the target stays
`INCOMPLETE`; preserve its receipt, then dispose of that owned schema/output
with the operator's existing cleanup workflow. Re-running into the same output
or populated schema is rejected. No successful resume is implied.

Generate the full catalog on the test server using storage for the object store
and PostgreSQL. Keeping these files on the server avoids a local copy on the
operator's laptop. Verify free space for both allocations, including PostgreSQL
WAL and temporary data, before choosing a byte ceiling. This exact plan has
1,820,000,000 source rows and 2,390,000 canonical records, including 190,000
generation comparisons. See the K17.01 sizing receipt in
`delivery/kien-r418/evidence/k1701-catalog` before attempting this command. A
complete run at that scale has yet to establish its elapsed time and resource budget.

```text
python scripts/k17_seed.py --plan docs/k17-seed-window-catalog.json --schema k17_seed_32_LOWERCASE_HEX_CHARACTERS --output ABSOLUTE_NEW_PRIVATE_DIRECTORY --max-bytes VERIFIED_BYTE_LIMIT --max-source-rows 1820000000 --max-records 2390000 --max-seconds 604800 --require-primary
```

## Private actors and handoff

Identity/bootstrap SQL is explicitly synthetic and runs before workload
measurement. Business publication still uses the real native validators.
Temporary owner sessions are revoked per workspace. After all data is loaded,
each actor receives its own fresh opaque server session and genuine signed
CSRF proof. The importer confirms the number of distinct active user sessions.
The file `actors.jsonl` has mode 600, unique actor IDs, full cookie, proof, role,
S/M/L and plain template IDs for latest workspace/project/snapshot/result,
claim/evidence/source, revision/question and a different workspace's IDs for
negative scope checks. It is directly compatible with K17's strict actor loader.

**Do not package actors.jsonl, signing keys or database secrets in a handoff.**
Share `manifest.json`, the pinned plan, public inventory hashes and test receipts.
The manifest's dataset hash pins that particular generated dataset and records
that the native analyzer, sources and sessions were synthetic fixtures. Session
idle/absolute lifetimes are real (defaults 15 minutes/eight hours). A long run
must renew through the actual session flow; seeding does not bypass or extend
hard expiry and does not qualify IdP/login/renewal. Keep actor credentials only
on the authorized isolated load generator.

The local native acceptance test exercises every small-plan actor through real
ServerSessions, signed CSRF verification, typed canonical and source reads,
role/size allocation and cross-workspace denial; it checks physical hashes and
the early fresh/primary gates. Default suites skip native DB tests unless their
explicit test database variable is supplied. Full-scale generation is never
triggered by the ordinary unit test suite.

Each private actor also carries `context_id` and `absolute_expires_at` from its
actual inserted server-session row (`INSERT ... RETURNING`), expressed as plain
strings and a UTC ISO8601 timestamp. A renewal coordinator can use that fixed
hard expiry to stop before the real session budget ends. These values do not
extend that budget, authorize a request, or qualify renewal by themselves.
