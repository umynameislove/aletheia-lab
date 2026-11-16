# R4 expanded workload and remaining lifecycle inputs

The original small calibration profile stays available. `k17_capacity.py template --workload expanded` adds executable session/workspace/catalog/settings/member reads, preview→terminal output→preview-lease read, and an own-record synthetic review with revision0. These are protocol workload assumptions, not measured browser proportions or a complete primary campaign. Every output retains its pinned profile/hash and actual operation counters.

P13 now follows the client's actual `POST /v1/workspaces/{ws}/jobs` with `operation=analyze`; the job's updates/output are HTTP fanout of that one action, not extra analyses. The alternative `/projects/{id}/analyses` API remains supported.

Expanded recipes cover21operations as recipe or shared job/download protocol: P01,P03,P04,P05,P07,P08,P11–P20,P23,P24,P26,P27,P37. P22 uses the permitted bounded polling alternative already exercised by following jobs; no SSE handler or fake event delivery is claimed. P32–P34 remain `unsupported` because `sources.connector=false`. The13operations below require dedicated fixtures and remain outside the generic continuous-load profile.

The 2026-11-15 supplemental K17 receipts execute these dedicated backend lifecycle paths through an installed two-API/two-worker HTTPS deployment. The driver is `delivery/kien-r418/tools/k17_lifecycle_probe.py`; evidence is in `delivery/kien-r418/evidence/k17-remaining`. It creates disposable projects and upload intents, recovers expired fixture sessions through real Keycloak login, and observes a foreign workspace during the work. The fixtures below define the remaining inputs needed for execution under the primary workload.

| Operations | Exact missing inputs/execution |
|---|---|
| P02 logout | One-shot actor/session pool separate from continuous-load actors; actual logout then401/current-tab/cross-tab confirmation. IdP/session renewal and hard expiry must remain real. |
| P06/P29/P30/P31 upload | Per-actor original ZIP bytes + source hash/part manifest/size class, bounded raw-binary sender, resumable progress reconciliation; separate cancelled upload so published evidence is never removed. |
| P09 confirm | Explicit approved mapping for the newly previewed source, stable creator/current grants, followed confirm output and canonical snapshot/source identity; no auto-select of unresolved fields. |
| P10 refresh | Per-actor owned isolated project and a real next-generation payload, expected revision and before/after/no-op oracle; sharing a project among simultaneous mutation actors must retain CAS conflicts rather than relabel them success. |
| P21 cancel | A genuinely accepted pending job with controlled worker/lease state and actor permission; verify cancel_requested versus terminal cancelled and exactly one effect/no publication afterward. |
| P25 delete | Disposable per-actor project distinct from read cohorts and accepted pending work; tombstone plus old result/export denial, shared source preservation and restore checkpoint receipt. |
| P28 member change | Disposable target identity and workspace, existing revision, least-privilege policy and a retained last owner; subsequent target session/object denial and measured revoke timing. |
| P35 project update | Isolated mutable metadata fixture and fresh expected revision, exact changed display name with immutable snapshot preserved; deliberate conflicts reported separately. |
| P36 invitation | An actually provisioned eligible target identity not already a member, app-invitation capability and explicit allowed role; or a real IdP-managed onboarding disposition with independent receipt. |
| P38 settings update | Owner/admin's isolated workspace, pinned platform limits and fresh revision; read-back plus restoration of intended policy, with no quota expansion affecting another cohort. |

Native seed actor files already provide `context_id`,source/result/snapshot/claim/evidence IDs and source SHA. Expanded mode requires the complete ManagedRuntime route composition, including workspace/member management, runtime settings, preview leases and review authorization. A source-only ASGI fixture that omits those routes is not an expanded deployment.

The supplemental `imports-03` receipt covers actual S/M/L ZIP resume, duplicate part delivery, upload completion replay, explicit native fixture mapping, confirm replay, unchanged and changed refresh, project metadata and tombstones. `delete-integrity-02` additionally checks complete export bytes/hash before deletion and denies the old result/download afterward. `management-01` contains a successful settings/invitation/membership slice; its later logout assertion failed and remains recorded. The separate `logout-01` receipt checks real IdP login, HTTP 204 logout, both cleared cookies and old-session HTTP 401. `cancel-02` checks a queued job cancelled while both owned workers are stopped, then confirms HTTP 404 for its absent output. These receipts close dedicated fixture gaps, while their small workloads retain `primary_campaign_passed=false`.

Continuous-load calibration receipts `native-open-02` and `native-closed-01` use 100 distinct authenticated principals, 5 seconds warm-up and 40 seconds steady. Their positive deadline success fractions are 729/782 and 682/715 respectively, below 99.9%. Export rejection and read/job timeouts remain counted. Functional pytest success confirms its correctness/accounting assertions, while the measured SLO verdict is still failed. The public result keeps both values.

New review IDs are per action/run/shard and remain owned by the current actor. Comments explicitly use the synthetic action question and `status=comment`; no research label or evaluator truth is imported. Preview and annotation writes are counted/reconciled as real writes, not called read-only traffic.

Timer semantics: both every scheduled open-model arrival and the end of the pinned measurement window recheck the absolute monotonic deadline after a timer wake. Windows early timers cannot offer actions early or shorten the window. Action deadlines, planned count, weights and failure denominators are unchanged.

Unit transport tests validate the added recipe flows and exact actor/source/current-CSRF binding. The calibration profiles still use the v2 model and the small fixtures. See `k17-primary-v3-proposal.md` for the remaining primary campaign design.

The hosted product can opt in to the local window analyzer by setting `api.analysis_model` to `upload-snapshot-windowed-metric-observation/v3` after applying persistence migration 18. Each accepted job pins its model. The analyzer groups canonical mapped observations by source, run, metric, class and unit, then summarizes up to ten consecutive records per window. Each evidence item binds the snapshot, operands and source ZIP hash. The calculation uses canonical mapped values and retains the original snapshot as its source. The product keeps the existing ProductView schema versions and renders the summary, claim pages and graph from the result reference. JSON, Markdown and PDF exports use the complete authorized view.

The verified large-fixture settings are `api.backend_timeout_seconds=60`, `api.request_timeout_seconds=90` and `api.maximum_response_bytes=1048576`. On the test host the large confirm output took 30.47 seconds and creation of the PDF export took 52.06 seconds. Use the verified settings when enabling this profile for those fixtures.

The K17.01 integration receipts are in `delivery/kien-r418/evidence/k1701-large-model`. They use three synthetic sources to verify the product path. Full catalogue import and the primary load campaign are separate acceptance steps.
