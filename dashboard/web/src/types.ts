/* p6-product-view/v1 — the shape the UI reads, field names verbatim from the service.
   Locked against the shared fixture 0f541342… (Kiên, commit 4e37e551): per-turn result_id/runtime,
   claim_type, reproduction_ref, structured metrics, the K07 counterfactual marker and the K08
   follow-up child. Mirrors p6-web-prototype/contract/product-view-additions.d.ts. */

export type Visibility = "public" | "diagnosis" | "evaluator";
export type ResultStatus = "complete" | "technical_failure";
export type Support = "fully_supported" | "partially_supported" | "unsupported" | "contradicted" | "not_estimated";

/** Closed enum; a new value needs a new schema version. */
export type ClaimType = "evidence_statement" | "cause_assertion" | "uncertainty_statement" | "recommended_action" | "other";

export interface Runtime {
  provider: string;
  model: string;
  external_call: boolean;
}

export interface Turn {
  id: string;
  snapshot_id: string;
  /** The result this turn belongs to. In a follow-up child the earlier turns keep their own. */
  result_id: string;
  runtime: Runtime;
  question: string;
  answer: string;
  abstained: boolean;
  visible_evidence_ids: string[];
  /** Evidence the turn asked for and did not get — plus, at most once and always last, the
      counterfactual marker (COUNTERFACTUAL_MARKER in data/view.ts), which is a status, not a request. */
  missing_evidence: string[];
}

export interface Claim {
  id: string;
  turn_id: string;
  text: string;
  claim_type: ClaimType;
  epistemic_state: string;
  support: Support | string;
  citation_ids: string[];
  counterevidence_ids: string[];
  missing_evidence: string[];
  max_strength: string;
}

/** Points at an immutable P3 record — never a host path, never a file. */
export interface ReproductionRef {
  record_id: string;
  record_kind: "snapshot" | "snapshot_comparison" | "regression_event";
}

export interface Evidence {
  id: string;
  kind: string;
  title: string;
  text: string;
  reproduction_ref: ReproductionRef;
  source_sha256: string;
  visibility: Visibility | string;
  redacted: boolean;
}

export interface GraphNode { id: string; kind: string; source_id: string; visibility: string }
export interface GraphEdge { id: string; kind: string; source: string; target: string; visibility: string }

export interface Result {
  id: string;
  status: ResultStatus;
  /** null under technical_failure */
  disposition: string | null;
  causal_status: string;
  summary: string;
  denominators: {
    independent_families: number | null;
    contexts: number | null;
    outputs: number | null;
    claims: number | null;
  };
  allowed_wording: string;
  forbidden_wording: string;
  caveat: string;
}

export interface ProductView {
  schema_version: string;
  demo_only: boolean;
  mode: string;
  visibility: Visibility | string;
  project: { id: string; display_name: string };
  snapshot: {
    id: string; baseline_id: string; sha256: string;
    /** Always present — [] when there is nothing, never null or missing. */
    metric_definitions: MetricDefinition[];
    metric_changes: MetricChange[];
  };
  runtime: Runtime;
  result: Result;
  conversation: { id: string; turns: Turn[] };
  claims: Claim[];
  evidence: Evidence[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
}

export type ReviewState = "unchecked" | "checked" | "disputed";

/* ---------------------------------------------------------- preview_import --
   Mirrors PreviewImportResult in p6-web-prototype/contract/product-view-additions.d.ts.
   The contract test type-checks the shared fixture against both. */

export type IssueSeverity = "info" | "warning" | "error" | "blocker";
export type UnavailableReason = "sensitive_fields_withheld" | "unsupported_source" | "unreadable_source";

export interface IssueDTO {
  code: string;
  severity: IssueSeverity;
  stage: string;
  message: string;
  relative_path: string | null;
  occurrences: number;
}

interface CandidateBase {
  project_item_id: string;
  label: string;
  mapping_id: string | null;
  available: boolean;
  unavailable_reason: UnavailableReason | null;
  relative_path: string | null;
}
export interface TargetCandidate extends CandidateBase { field_names: string[] }
export interface MetricSourceCandidate extends CandidateBase {
  format: "csv" | "json";
  records_path_candidates: string[][];
  field_names: string[];
  run_id_field_candidates: string[];
  /** v2 (K04d). [] = not resolved → manual entry. Sensitive → candidate unavailable, all arrays []. */
  metric_name_candidates: string[];
}
export type ConfigCandidate = CandidateBase;
export interface RunCandidate {
  run_id: string;
  available: boolean;
  unavailable_reason: UnavailableReason | null;
  source_metric_item_ids: string[];
  available_config_item_ids: string[];
}
export interface MappingCandidates {
  targets: TargetCandidate[];
  metric_sources: MetricSourceCandidate[];
  configs: ConfigCandidate[];
  runs: RunCandidate[];
}
export type MetricDirection = "higher_is_better" | "lower_is_better";
/** K04d — sent in the confirm_import mapping. No defaults. */
export interface MetricDefinition {
  metric_name: string;
  direction: MetricDirection;
  regression_threshold: number;
}

export interface PreviewImportResult {
  preview_id: string;
  included_count: number;
  redacted_count: number;
  excluded_count: number;
  withheld_count: number;
  blockers: IssueDTO[];
  warnings: IssueDTO[];
  outbound_categories: string[];
  mapping_candidates: MappingCandidates;
}

/* ----------------------------------------- structured metrics (locked by Kiên, 2026-10-08) ---
   Under snapshot. Identity of a change is (run_id, metric_name, step): one change per changed step,
   never pooled, never a picked first/last step. before and after share run_id and step and differ only
   in snapshot. evidence_id is 1:1 with a metric_observation evidence item and is the change's key
   inside one view — not stable across snapshots. The UI never recomputes adverse_status or delta. */

export type MetricChangeKind = "added" | "removed" | "increased" | "decreased";
export type AdverseStatus = "adverse" | "not_adverse" | "not_applicable";

export interface MetricObservation {
  /** before: snapshot.baseline_id · after: snapshot.id */
  snapshot_id: string;
  run_id: string;
  step: number | null;
  /** Finite JSON number, normalised by the service with Decimal from a string. */
  value: number;
}

export interface MetricChange {
  evidence_id: string;
  /** Joins snapshot.metric_definitions (unique by metric_name; several changes may share one). */
  metric_name: string;
  kind: MetricChangeKind;
  /** added: null */
  before: MetricObservation | null;
  /** removed: null */
  after: MetricObservation | null;
  /** after.value - before.value; null for added/removed */
  delta: number | null;
  /** not_applicable for added/removed */
  adverse_status: AdverseStatus;
}

/* --------------------------------------------- confirm_import mapping (exact, agreed) --- */

export interface TargetMapping {
  mapping_id: string;
  project_item_id: string;
  target_field: string;
  identifier_field: string;
}
export interface MetricSourceMapping {
  mapping_id: string;
  project_item_id: string;
  format: "csv" | "json";
  /** Always an array. CSV: []. JSON root array: []. */
  records_path: string[];
  metric_name_field: string;
  metric_value_field: string;
  run_id_field: string;
  step_field: string | null;
}
export interface RunMapping {
  run_id: string;
  config_item_ids: string[];
}
export interface ConfirmMapping {
  target: TargetMapping;
  metric_sources: MetricSourceMapping[];
  runs: RunMapping[];
  baseline_run_id: string;
  metric_definitions: MetricDefinition[];
}
