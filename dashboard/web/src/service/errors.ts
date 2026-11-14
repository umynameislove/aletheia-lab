/* The next step for a ProductError, by code (PRODUCT_CONTRACT §1: "UI hiển thị remediation theo code;
   không in stack trace"). The service's safe_message says what happened; this says what to do, so the
   two never repeat each other. The code list is the service's own, as given by its owner (2026-10-05,
   K08 selection codes and graph_projection_invalid added 2026-10-08).
   Anything not listed falls back to the safe message alone — never to a raw error. */

const REMEDIATION: Record<string, string> = {
  invalid_id: "Malformed link. Reopen the result from the project.",
  record_not_found: "Not found. It may have been deleted, or the link may be from elsewhere.",
  invalid_selection_kind: "Select a claim or lineage node, then ask.",
  selection_not_available: "Selection is gone. Pick a claim or node again.",
  store_unavailable: "Store unreachable. Try again shortly.",
  store_integrity_error: "Store failed its integrity check. Nothing shown or changed.",
  preview_failed: "Check the granted folder, then scan again.",
  preview_blocked: "Resolve the blocker, then scan again.",
  preview_stale: "Scan the project again.",
  preview_expired: "Scan the project again.",
  source_changed: "Get a fresh preview, then review the mapping again.",
  preview_required: "Get a fresh preview before confirming the mapping.",
  upload_expired: "Choose the ZIP again to start a new upload.",
  upload_cancelled: "Choose the ZIP again to start a new upload.",
  preview_already_confirmed: "Scan again to import anew.",
  project_already_imported: "Nothing new was created. Go to Start to continue it, then use Refresh.",
  confirm_failed: "Not confirmed. Nothing was created.",
  confirm_conflict: "The project changed, or already holds this ZIP with another mapping, so nothing was replaced. Open it from Workspaces to see what it holds.",
  mapping_invalid: "Mapping doesn't match the scan. Fix the flagged fields.",
  refresh_stale: "Project changed meanwhile. Refresh again.",
  refresh_failed: "Refresh failed. Previous snapshot unchanged.",
  follow_up_failed: "The follow-up did not complete. The open result is unchanged.",
  project_changed: "Someone changed this project after your request was accepted, so its answer was not saved. Open the current project, then ask again. Your question is kept.",
  export_expired: "That export expired before it was downloaded. Download again to create a new one.",
  refresh_source_unavailable: "Folder unreachable. Previous snapshot unchanged.",
  evidence_not_available: "Not visible in this view.",
  evidence_projection_invalid: "Failed a safe projection, so hidden.",
  evidence_reference_invalid: "Points outside visible evidence, so hidden.",
  invalid_question: "Write a question to ask.",
  result_invalid: "This result cannot be shown.",
  result_integrity_error: "Result failed its integrity check, so hidden.",
  graph_projection_invalid: "Lineage unverified. No graph shown, nothing stored.",
  demo_integrity_error: "Demo data failed its integrity check.",
  feature_unavailable: "Not available in this build.",
  invalid_request: "Check the values, then try again.",
  // the local bridge in front of ProductService (Live only)
  bridge_unavailable: "Start the dashboard bridge, then reload.",
  backend_unavailable: "Install the product package in this checkout.",
  service_failure: "Service failed safely. Nothing changed; try again.",
  forbidden: "Open the dashboard from its own address.",
  // what an HTTP status means when a proxy answers instead of the service (service/live.ts)
  session_expired: "Sign in again. Your work on this screen is kept.",
  session_revoked: "This session was ended. Sign in again.",
  membership_revoked: "Your access to this workspace has ended. Ask a workspace owner if you still need it.",
  permission_denied: "You do not have access to this. Ask a workspace owner.",
  csrf_invalid: "Not verified, so nothing was changed. Reload the page, then try again.",
  access_unconfirmed: "Your access is being checked. If this keeps happening, sign in again.",
  not_found: "Not found, or you do not have access. Go back to Start.",
  revision_conflict: "Someone changed this first. Reload to see their version; your text is kept.",
  precondition_required: "This needs the version you are editing. Reload, then try again; your text is kept.",
  idempotency_conflict: "That request was already used for something else. Nothing new was started.",
  job_state_conflict: "This job cannot be retried in its current state, or it was already retried. Nothing new was started.",
  cursor_invalid: "This list can't continue from there. Going back to the first page.",
  cursor_expired: "This list changed or timed out. Going back to the first page.",
  payload_too_large: "That is larger than this service accepts. Make it smaller and try again.",
  response_too_large: "Too large to open whole in this view. Nothing was shown or changed; retrying will not help.",
  rate_limited: "Too many requests. Wait a moment, then try again.",
  service_busy: "The service is busy or under maintenance. Nothing was changed. Try again shortly.",
  // the client boundary (service/contract.ts, service/live.ts)
  schema_unsupported: "App and service versions differ. Update both, then reload.",
  response_invalid: "Nothing from that answer was shown. Try again; report it if it repeats.",
  response_out_of_scope: "Nothing from that answer was shown. Reopen it from the project.",
  deadline_exceeded: "Nothing was changed by this read. Try again.",
  outcome_unknown: "Check the project before retrying. Asking the same question again is safe.",
};

export interface Remedy { code: string; known: boolean; advice: string | null }

/** `import_<issue_code>` is the import boundary passing an importer issue through. */
export function remedyFor(code: string): Remedy {
  if (REMEDIATION[code]) return { code, known: true, advice: REMEDIATION[code] };
  if (code.startsWith("import_")) return { code, known: true, advice: `Importer: ${code.slice("import_".length)}. Fix it, then scan again.` };
  return { code, known: false, advice: null };
}

export const KNOWN_CODES = Object.keys(REMEDIATION);
