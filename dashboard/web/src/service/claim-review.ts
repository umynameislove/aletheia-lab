import { isInstant } from "./hosted.ts";
import { ProductError } from "./port.ts";

export type ReviewVerdict = "allowed" | "denied";
export type ReviewMode = "local_check" | "provider";
export type ReviewDecision = "approve_in_scope" | "revise" | "unknown" | "unsupported" | "tool_error";
export interface ClaimReviewScope {
  projectId: string; snapshotId: string; resultId: string; claimId: string;
}

interface ReviewFinding {
  decision: ReviewDecision; reason: string; evidence_ids: string[];
  observed_state_sha256: string | null; receipt_sha256: string | null; contract_sha256: string | null;
  execution_mode: "fresh" | "replay" | null;
}
interface ReviewBinding {
  workspace_id: string; project_id: string; snapshot_id: string; result_id: string; claim_id: string;
  source_id: string | null; source_sha256: string | null; result_sha256: string; claim_sha256: string;
  evidence_sha256: string; receipt_sha256: string | null; contract_sha256: string | null;
  asserted_verdict: ReviewVerdict; policy: "historical-state-authorization/v1"; candidate_sha256: string;
}
interface ProviderAttempt {
  attempt_id: string; request_sha256: string; model: string; dispatched_at: string; deadline_at: string;
  received_at: string | null; held_microusd: number;
  outcome: {
    status: "completed" | "unavailable" | "unresolved";
    provider_request_id: string | null; input_tokens: number | null; output_tokens: number | null;
    provider_decision: ReviewDecision | null; agrees_with_host: boolean | null;
  } | null;
}
type ReviewReceipt = { job_id: string; binding: ReviewBinding; finding: ReviewFinding };
export type ClaimReviewReceipt = ReviewReceipt & (
  { schema_version: "claim-evidence-review/v1"; mode: "local_check"; usage: "not_incurred" } |
  { schema_version: "claim-evidence-review/v2"; mode: "provider"; usage: "settled" | "unsettled";
    binding: ReviewBinding & { provider_plan_sha256: string }; attempt: ProviderAttempt }
);

export const REVIEW_FEATURE = "reviews.claim_evidence";
export const PROVIDER_REVIEW_FEATURE = "reviews.claim_evidence_provider";
export const REVIEW_ID = /^job_[0-9a-f]{32}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const HASH = /^[0-9a-f]{64}$/;
const decisions: readonly unknown[] = ["approve_in_scope", "revise", "unknown", "unsupported", "tool_error"];
const obj = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);
const matches = (re: RegExp, x: unknown) => typeof x === "string" && re.test(x);
const hash = (x: unknown) => matches(HASH, x);
const optionalHash = (x: unknown) => x === null || hash(x);
const text = (x: unknown, max: number) => typeof x === "string" && x.length > 0 && x.length <= max;
const count = (x: unknown) => typeof x === "number" && Number.isSafeInteger(x) && x >= 0;
const optionalCount = (x: unknown) => x === null || count(x);
const invalid = () => new ProductError("response_invalid", "The service sent a receipt this app cannot trust.");

function providerAttempt(x: unknown, settled: boolean, hostDecision: unknown): boolean {
  if (!obj(x) || !matches(/^[0-9a-f]{32}$/, x.attempt_id) || !hash(x.request_sha256) || !matches(ID, x.model)
    || typeof x.dispatched_at !== "string" || !isInstant(x.dispatched_at)
    || typeof x.deadline_at !== "string" || !isInstant(x.deadline_at)
    || !count(x.held_microusd) || x.held_microusd === 0) return false;
  const started = Date.parse(x.dispatched_at), deadline = Date.parse(x.deadline_at);
  if (started >= deadline) return false;
  const out = x.outcome;
  if (out === null) return !settled && x.received_at === null;
  if (typeof x.received_at !== "string" || !isInstant(x.received_at)
    || Date.parse(x.received_at) < started || Date.parse(x.received_at) > deadline) return false;
  return obj(out) && (out.provider_request_id === null || matches(ID, out.provider_request_id))
    && optionalCount(out.input_tokens) && optionalCount(out.output_tokens)
    && (out.input_tokens !== null) === settled && (out.output_tokens !== null) === settled
    && (out.status === "completed"
      ? decisions.includes(out.provider_decision) && out.agrees_with_host === (out.provider_decision === hostDecision)
      : (out.status === "unavailable" || out.status === "unresolved")
        && out.provider_decision === null && out.agrees_with_host === null);
}

export function readClaimReviewReceipt(value: unknown, workspace: string, scope: ClaimReviewScope, reviewId: string): ClaimReviewReceipt {
  if (!obj(value) || !obj(value.binding) || !obj(value.finding)) throw invalid();
  const b = value.binding, f = value.finding;
  if (value.job_id !== reviewId || b.workspace_id !== workspace || b.project_id !== scope.projectId
    || b.result_id !== scope.resultId || b.snapshot_id !== scope.snapshotId || b.claim_id !== scope.claimId) {
    throw new ProductError("response_out_of_scope", "The receipt belongs to another job or claim.");
  }
  if (!matches(REVIEW_ID, reviewId)
    || (b.source_id === null ? b.source_sha256 !== null : !matches(ID, b.source_id) || !hash(b.source_sha256))
    || ![b.result_sha256, b.claim_sha256, b.evidence_sha256, b.candidate_sha256].every(hash)
    || !optionalHash(b.receipt_sha256) || !optionalHash(b.contract_sha256)
    || b.policy !== "historical-state-authorization/v1"
    || (b.asserted_verdict !== "allowed" && b.asserted_verdict !== "denied")
    || !decisions.includes(f.decision) || !text(f.reason, 500)
    || !Array.isArray(f.evidence_ids) || f.evidence_ids.length > 128 || !f.evidence_ids.every(id => matches(ID, id))
    || new Set(f.evidence_ids).size !== f.evidence_ids.length
    || ![f.observed_state_sha256, f.receipt_sha256, f.contract_sha256].every(optionalHash)
    || (f.execution_mode !== null && f.execution_mode !== "fresh" && f.execution_mode !== "replay")) throw invalid();
  if (value.mode === "local_check" && value.schema_version === "claim-evidence-review/v1"
    && value.usage === "not_incurred" && !("attempt" in value) && !("provider_plan_sha256" in b)) {
    return value as unknown as ClaimReviewReceipt;
  }
  if (value.mode === "provider" && value.schema_version === "claim-evidence-review/v2"
    && (value.usage === "settled" || value.usage === "unsettled")
    && hash(b.provider_plan_sha256) && providerAttempt(value.attempt, value.usage === "settled", f.decision)) {
    return value as unknown as ClaimReviewReceipt;
  }
  throw invalid();
}
