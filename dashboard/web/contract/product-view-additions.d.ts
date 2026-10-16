/* ============================================================================
   P6 — exact shapes the UI reads, for the five agreed interface changes.
   Mirror these names verbatim; the UI reads them with no aliasing.

   Version: preview_import v2 (K04d) — adds metric_name_candidates and MetricDefinition.

   Scope: additions only. The nine service methods are unchanged, and nothing
   here replaces p6-product-view/v1 — it extends preview_import's return and
   two fields on a turn.
   ============================================================================ */

/* ---------------------------------------------------------------- shared --- */

/** Stable project item identifier, e.g. "pi-metrics-validation". */
export type ProjectItemId = string;

/** Severity lattice already used by the importer on main. */
export type IssueSeverity = "info" | "warning" | "error" | "blocker";

/** Visibility lattice. The service filters before returning; the UI never widens it. */
export type Visibility = "public" | "diagnosis" | "evaluator";

/** Why a candidate exists but cannot be used. Safe to show; carries no raw sensitive value. */
export type UnavailableReason =
  | "sensitive_fields_withheld"   // field labels withheld -> no manual entry offered
  | "unsupported_source"
  | "unreadable_source";

/** Every candidate carries these. `available: false` means listed-but-not-selectable. */
interface CandidateBase {
  project_item_id: ProjectItemId;
  /** Human label. When the path itself is sensitive this is a safe stand-in, never the raw path. */
  label: string;
  /** Suggested stable mapping id, or null when the candidate is unavailable. */
  mapping_id: string | null;
  available: boolean;
  /** null when available === true. */
  unavailable_reason: UnavailableReason | null;
  /** Relative, already past the boundary. null when the path itself is withheld. */
  relative_path: string | null;
}

/* ------------------------------------------- 1. preview_import additions --- */

/**
 * Four collections, these exact key names.
 *
 * Membership: a stable-sorted subset of items that passed P3 admission and are
 * still diagnosis-visible — typically include or redact. Never exclude, block,
 * or withheld content. The count therefore does NOT have to equal
 * included_count: a dataset whose rows were withheld can still offer usable
 * structural metadata and appear here while sitting in redacted_count.
 */
export interface MappingCandidates {
  targets: TargetCandidate[];
  metric_sources: MetricSourceCandidate[];
  configs: ConfigCandidate[];
  runs: RunCandidate[];
}

export interface TargetCandidate extends CandidateBase {
  /** Safe field labels only. Empty when available === false. */
  field_names: string[];
}

export interface MetricSourceCandidate extends CandidateBase {
  /** From the P3 source profile, never inferred from field names. */
  format: "csv" | "json";
  /**
   * Each entry is a path expressed as segments.
   * - JSON   : paths to usable array-of-object, e.g. [["runs"], ["records"]]
   * - root   : the empty path [] is a valid candidate, so [[]] means "root array"
   * - CSV    : always [] — records_path does not apply
   * - unsure : [] — the UI then offers manual entry rather than the service guessing
   */
  records_path_candidates: string[][];
  field_names: string[];
  /** Fields that could be the run id. [] when ambiguous; the UI then offers manual entry. */
  run_id_field_candidates: string[];
  /**
   * v2 (K04d). Observed metric names, when the backend resolved a single name column
   * (name / metric / metric_name) and every value is safe to show.
   * - [] when not resolved with certainty -> the UI offers manual entry
   * - sensitive names -> the candidate is available:false, sensitive_fields_withheld,
   *   relative_path null and EVERY structural array empty; no manual entry is offered
   */
  metric_name_candidates: string[];
}

export type ConfigCandidate = CandidateBase;

export interface RunCandidate {
  run_id: string;
  available: boolean;
  unavailable_reason: UnavailableReason | null;
  /** Metric sources this run id was read from. */
  source_metric_item_ids: ProjectItemId[];
  /** Configs the user may attach. NOT an assignment the service already made. */
  available_config_item_ids: ProjectItemId[];
}

/* -------------------------------------- K04d: metric definitions (confirm_import) --- */

export type MetricDirection = "higher_is_better" | "lower_is_better";

/**
 * One per observed metric. Sent in the confirm_import mapping as `metric_definitions`.
 * No defaults. Missing/empty, a negative or non-finite threshold, a duplicate name, or a
 * set that does not match the observed metric names exactly -> mapping_invalid.
 *
 * Adverse (inclusive, compared with Decimal from the persisted representation):
 *   lower_is_better : delta > 0 and delta >= regression_threshold
 *   higher_is_better: delta < 0 and |delta| >= regression_threshold
 *   threshold 0 with delta 0 is not adverse.
 * An adverse change is an observation; causal_status stays unverified.
 */
export interface MetricDefinition {
  metric_name: string;
  direction: MetricDirection;
  /** Absolute deviation in the metric's own units. Finite, >= 0. */
  regression_threshold: number;
}

/** preview_import return, with the addition marked. */
export interface PreviewImportResult {
  preview_id: string;
  included_count: number;
  redacted_count: number;
  excluded_count: number;
  withheld_count: number;
  blockers: IssueDTO[];     // severity === "blocker" only
  warnings: IssueDTO[];     // non-blocking group: info | warning | error
  outbound_categories: string[];
  mapping_candidates: MappingCandidates;   // <- added
}

/* ----------------------------------------- 2. blockers / warnings DTO --- */

/**
 * Product-facing issue. Deliberately NOT the P3 model: no subject_sha256, since
 * the integrity hash is internal and the UI has no use for it.
 *
 * A row reads its own `severity` rather than inferring it from which list it
 * arrived in, because `warnings` may legitimately carry an `error`.
 */
export interface IssueDTO {
  code: string;                 // e.g. "pii_redacted", "baseline_invalid"
  severity: IssueSeverity;
  stage: string;                // e.g. "read" | "parse" | "collect" | "normalize"
  message: string;
  /** Relative, already past the boundary. Never an absolute host path. */
  relative_path: string | null;
  occurrences: number;
}

/* --------------------------------------------- 3. turn result_id + runtime --- */

/** Same shape as the top-level runtime. */
export interface Runtime {
  provider: string;
  model: string;
  external_call: boolean;
}

export interface Turn {
  id: string;
  snapshot_id: string;
  result_id: string;      // <- added: a follow-up creates a new result; each turn stays auditable
  runtime: Runtime;       // <- added: provider may differ between turns
  question: string;
  answer: string;
  abstained: boolean;
  visible_evidence_ids: string[];
  missing_evidence: string[];
}

/* --------------------------------------------------- 4. result.status --- */

/** Two terminal states. Nothing half-finished is persisted in an immutable view. */
export type ResultStatus = "complete" | "technical_failure";

/**
 * Under technical_failure the envelope stays auditable (project, snapshot,
 * runtime, result identity, the turn that was created) while the diagnosis half
 * fails closed (locked with the service owner, 2026-10-07):
 *   disposition             = null
 *   claims                  = []          denominators.claims = 0
 *   evidence                = the diagnosis-visible evidence already collected
 *   graph                   = Snapshot / EvidenceItem nodes and OBSERVED_IN edges only
 *                             (no AtomicClaim, Disposition, CITES, ASSIGNED_DISPOSITION)
 *   turn                    = keeps snapshot/result/runtime/question, abstained = true
 *   counterfactual marker   = not emitted
 * snapshot.metric_definitions / metric_changes stay as they are: they belong to
 * the immutable snapshot, not to the analysis result.
 * A failure before a valid scoped envelope exists returns ProductError instead,
 * and no view is created at all.
 */
export interface Result {
  id: string;
  status: ResultStatus;
  disposition: string | null;   // null under technical_failure
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

/* ----------------------------------------- structured metrics (locked by the service owner, 2026-10-08) ---
   Under snapshot. Both arrays always present ([] when empty). Identity of a change is
   (run_id, metric_name, step): one change per changed step, never pooled, never a picked first/last
   step. before/after share run_id and step and differ only in snapshot. evidence_id is 1:1 with a
   metric_observation evidence item; metric_changes is sorted by evidence_id. The UI never recomputes
   delta or adverse_status. No row index, path or mapping internal is exposed. */

export type MetricChangeKind = "added" | "removed" | "increased" | "decreased";
export type AdverseStatus = "adverse" | "not_adverse" | "not_applicable";

export interface MetricObservation {
  snapshot_id: string;      // before: snapshot.baseline_id · after: snapshot.id
  run_id: string;
  step: number | null;
  value: number;            // normalised with Decimal from a string before it is emitted
}

export interface MetricChange {
  evidence_id: string;
  metric_name: string;      // joins snapshot.metric_definitions (unique by metric_name)
  kind: MetricChangeKind;
  before: MetricObservation | null;   // added: null
  after: MetricObservation | null;    // removed: null
  delta: number | null;               // after.value - before.value; null for added/removed
  adverse_status: AdverseStatus;      // not_applicable for added/removed
}

/* ---------------------------------------------------- claims and evidence (K06–K08) --- */

/** Required on every claim. Closed enum; a new value needs a new schema version. */
export type ClaimType = "evidence_statement" | "cause_assertion" | "uncertainty_statement" | "recommended_action" | "other";

/** Replaces evidence.relative_path. An immutable P3 record ID — never a host path, never a file. */
export interface ReproductionRef {
  record_id: string;
  record_kind: "snapshot" | "snapshot_comparison" | "regression_event";
}

/** K07: written into turn/claim missing_evidence at most once, always last. A status, not a request. */
export type CounterfactualMarker = "Counterfactual comparison: not_available";

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
