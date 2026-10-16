/* The next step for a ProductError, by code (PRODUCT_CONTRACT §1: "UI hiển thị remediation theo code;
   không in stack trace"). The service's safe_message says what happened; this says what to do, so the
   two never repeat each other. The code list is the service's own, as given by its owner (2026-10-05,
   K08 selection codes and graph_projection_invalid added 2026-10-08).
   Anything not listed falls back to the safe message alone — never to a raw error. */

const REMEDIATION: Record<string, string> = {
  invalid_id: "Malformed link. Reopen the result from the project.",
  record_not_found: "Not found. Reopen it from the project.",
  invalid_selection_kind: "Select a claim or lineage node, then ask.",
  selection_not_available: "Selection is gone. Pick a claim or node again.",
  store_unavailable: "Store unreachable. Try again shortly.",
  store_integrity_error: "Store failed its integrity check. Nothing shown or changed.",
  preview_failed: "Check the granted folder, then scan again.",
  preview_blocked: "Resolve the blocker, then scan again.",
  preview_stale: "Scan the project again.",
  preview_expired: "Scan the project again.",
  preview_already_confirmed: "Scan again to import anew.",
  confirm_failed: "Not confirmed. Nothing was created.",
  mapping_invalid: "Mapping doesn't match the scan. Fix the flagged fields.",
  refresh_stale: "Project changed meanwhile. Refresh again.",
  refresh_failed: "Refresh failed. Previous snapshot unchanged.",
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
  invalid_request: "Unsupported request. Use json, markdown or pdf.",
  // the local bridge in front of ProductService (Live only)
  bridge_unavailable: "Start the dashboard bridge, then reload.",
  backend_unavailable: "Install the product package in this checkout.",
  service_failure: "Service failed safely. Nothing changed; try again.",
  forbidden: "Open the dashboard from its own address.",
};

export interface Remedy { code: string; known: boolean; advice: string | null }

/** `import_<issue_code>` is the import boundary passing an importer issue through. */
export function remedyFor(code: string): Remedy {
  if (REMEDIATION[code]) return { code, known: true, advice: REMEDIATION[code] };
  if (code.startsWith("import_")) return { code, known: true, advice: `Importer: ${code.slice("import_".length)}. Fix it, then scan again.` };
  return { code, known: false, advice: null };
}

export const KNOWN_CODES = Object.keys(REMEDIATION);
