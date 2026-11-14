# K18 operator and container review

The recovery/controller review found and verified fixes for three concrete failure paths. Restore now rejects default object ACLs, pre-existing routines/namespaces and other target access, then seals nonowner database connections before executing an archive. A public security-definer function in an otherwise table-empty database can no longer expose restored data before checkpoint application. The controller rejects a checkpoint older than the backup before writing the target. Checkpoint time uses its transaction start as a conservative lower bound, so a paused repeatable-read transaction cannot claim that an older authority snapshot was captured later.

Independent verification:

| Check | Evidence |
| --- | --- |
| Native pre-existing public function and empty namespace rejected before external archive execution | `test_k18_restore_target_isolation_postgres.py`, two cases |
| Ordinary login loses CONNECT/TEMP before archive execution and remains blocked before checkpoint | Same file, third native case |
| Authenticated old checkpoint rejected before any restore write; later checkpoint proceeds | `test_k18_operator_review.py`, two signed-fixture cases |
| Container health uses real verified public DNS SNI and Host, including nondefault public port | `test_k18_container_health_tls.py`, native loopback TLS/Uvicorn |
| Current readiness 503 remains unhealthy while liveness is alive; wrong CA and wrong hostname are rejected | Same TLS case |
| New polling/retention readiness ports use explicit fixture mocks and fail closed on false/hang/loss | Four runtime fixture files, 55 focused cases |

The TLS probe test uses a synthetic readiness backend to isolate transport. It does not qualify PostgreSQL, IdP, Docker startup or production capacity. The process-recovery test separately uses actual PostgreSQL authorization and HTTPS consumers. Raw review logs reside in the canonical K18 evidence directory.

Open execution gates remain:

- Build/run the pinned Linux image and inspect its actual dependency imports, UID10001 secret/volume access, image digest, OS package vulnerabilities and SBOM. The Mac review and source/wheel checks do not substitute for image execution.
- Supply authorized IdP configuration, TLS/CA material and real login/revocation canaries. For a confidential client, apply the documented `oidc_client_secret` Compose override while retaining the existing API secrets.
- Validate API/worker replicas, ingress health availability, active download drain and rolling or drained upgrade on the declared staging topology.
- Keep latest authority/deletion inventory and encrypted off-host backups/WAL independently of the failed host; measure the storage DR tier on the actual dataset/storage.
- Complete Quán browser binding/visibility/reconnect checks and cross-person rebuild/restore handoff with matching source/image/schema identities.
- Run and retain the declared 50k normal-load evidence; local bounded probes do not close that capacity gate.

No additional root-code correctness defect was confirmed in the reviewed container entry point, health probe, Dockerfile, Compose template, secret filenames, schema CLI or connected grant flow after these fixes. Linux image execution, external IdP and operator deployment were not run in this review.
