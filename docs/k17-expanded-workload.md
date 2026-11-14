# R4 expanded workload and remaining lifecycle inputs

The original small calibration profile stays available. `k17_capacity.py template --workload expanded` adds executable session/workspace/catalog/settings/member reads, preview→terminal output→preview-lease read, and an own-record synthetic review with revision0. These are protocol workload assumptions, not measured browser proportions or a complete primary campaign. Every output retains its pinned profile/hash and actual operation counters.

P13 now follows the client's actual `POST /v1/workspaces/{ws}/jobs` with `operation=analyze`; the job's updates/output are HTTP fanout of that one action, not extra analyses. The alternative `/projects/{id}/analyses` API remains supported.

Expanded recipes cover21operations as recipe or shared job/download protocol: P01,P03,P04,P05,P07,P08,P11–P20,P23,P24,P26,P27,P37. P22 uses the permitted bounded polling alternative already exercised by following jobs; no SSE handler or fake event delivery is claimed. P32–P34 remain `unsupported` because `sources.connector=false`. The13operations below stay `not_run`; adding an expected status or a `dedicated` string without a real workflow is not coverage.

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

New review IDs are per action/run/shard and remain owned by the current actor. Comments explicitly use the synthetic action question and `status=comment`; no research label or evaluator truth is imported. Preview and annotation writes are counted/reconciled as real writes, not called read-only traffic.

Timer semantics: both every scheduled open-model arrival and the end of the pinned measurement window recheck the absolute monotonic deadline after a timer wake. Windows early timers cannot offer actions early or shorten the window. Action deadlines, planned count, weights and failure denominators are unchanged.

Unit transport tests validate the added recipe flows and exact actor/source/current-CSRF binding. They do not replace a native HTTP/PG/worker/browser campaign. Primary v3 still needs the explicit model/consumer decisions in `k17-primary-v3-proposal.md`; neither an expanded profile nor38disposition entries can turn the current2/16/64claim fixture into a primary dataset.
