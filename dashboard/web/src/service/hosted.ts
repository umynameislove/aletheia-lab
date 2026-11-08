/* The hosted product API, as far as it is agreed (J-01 review v2, Kiên, 2026-11-07): Problem errors,
   GET /v1/session and GET /v1/capabilities. Draft only: there is no hosted endpoint yet, so nothing
   calls these readers over the network. They exist so the screens are built against the agreed shapes,
   and so every answer is checked here, at run time, before anything reads it.

   - Problem: a code is trusted only when it is in PROBLEM_STATUS and both the HTTP status and the
     body's own status match it. A mismatched pair or an unknown code is a generic failure; it is never
     guessed into expired or permission_denied (J-01 review v2, point 1).
   - Session and capabilities fail closed: wrong version, a missing field, or another workspace than
     the one asked for, and nothing from that answer is used.

   Erasable TypeScript only, so the tests can import it as is. */

import { ProductError } from "./port.ts";

/** Versions this client reads. `product-api/v1` is added when both sides release it. */
export const PRODUCT_API_VERSIONS: readonly string[] = ["product-api/v1-draft"];

/** The codes whose meaning is agreed, with the only HTTP status each may come with. */
export const PROBLEM_STATUS: Readonly<Record<string, number>> = {
  session_expired: 401,
  session_revoked: 401,
  membership_revoked: 403,
  permission_denied: 403,
  csrf_invalid: 403,
};

/** Snake case, starts with a letter, at most 64 characters (J-01 review v2, point 2). */
export const PROBLEM_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const OPAQUE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-](\d{2}):(\d{2}))$/;
/** The service allows 1000 characters of detail; the screen shows at most this many. */
export const DETAIL_SHOWN = 256;

type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);
// Lengths are in code points, as Python's len and JSON Schema maxLength count them. `.length` counts
// UTF-16 units, so 600 emoji would read as 1200 and a detail the producer accepts would be refused.
const text = (x: unknown, max: number): x is string =>
  typeof x === "string" && x.length >= 1 && (x.length <= max || (x.length <= 2 * max && [...x].length <= max));
const id = (x: unknown): x is string => typeof x === "string" && OPAQUE_ID.test(x);
const isInt = (x: unknown): x is number => Number.isInteger(x);

/** Counted in code points, not UTF-16 units, so a cut never splits an emoji or other astral character.
    A letter written with a separate combining mark can still lose its mark at the cut. */
function shorten(s: string, max: number) {
  const chars = [...s];
  return chars.length <= max ? s : chars.slice(0, max - 1).join("") + "…";
}

/** RFC 3339 in shape and on the calendar. Date.parse alone rolls 2026-02-30 over to 2 March. A leap
    second (:60) is refused, as the producer's Python datetime refuses it. */
function isInstant(s: string) {
  const m = RFC3339.exec(s);
  if (!m) return false;
  const [y, mo, d, h, mi, sec] = m.slice(1, 7).map(Number);
  const leap = (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1];
  return y >= 1 && days !== undefined && d >= 1 && d <= days && h <= 23 && mi <= 59 && sec <= 59
    && (m[9] === undefined || (Number(m[9]) <= 23 && Number(m[10]) <= 59));
}

/** True when the body claims to be a hosted Problem. Such a body is checked in full, never in part. */
export const looksLikeProblem = (body: unknown) =>
  isObj(body) && ("schema_version" in body || "detail" in body || "type" in body);

/** The error to show for a Problem body, and whether its code may be acted on (wipe, sign in again).
    null means the body is not a Problem this client can trust. Unknown members are ignored. */
export function readProblem(body: unknown, httpStatus: number): ProductError | null {
  if (!isObj(body)) return null;
  const { schema_version, type, title, status, code, detail, request_id } = body;
  const wellFormed = typeof schema_version === "string" && PRODUCT_API_VERSIONS.includes(schema_version)
    && text(type, 256) && text(title, 256) && isInt(status) && status >= 400 && status <= 599
    && typeof code === "string" && PROBLEM_CODE.test(code) && text(detail, 1000) && id(request_id);
  if (!wellFormed) return null;
  if (status !== httpStatus || PROBLEM_STATUS[code] !== httpStatus) return null;
  return new ProductError(code, shorten(detail, DETAIL_SHOWN));
}

const UNSUPPORTED = (what: string) => new ProductError("schema_unsupported", `This version of the app cannot read the service's ${what}.`);
const MALFORMED = (where: string) => new ProductError("response_invalid", `The service sent an answer this app cannot trust (${where}).`);
const OUT_OF_SCOPE = () => new ProductError("response_out_of_scope", "The service answered about another workspace, so nothing from it was used.");

function need(ok: boolean, where: string): asserts ok {
  if (!ok) throw MALFORMED(where);
}

export interface HostedSession {
  user_id: string;
  display_name: string;
  email: string;
  email_verified: boolean;
  context_id: string;
  expires_at: string;
  csrf_token: string;
  onboarding: "ready" | "needs_workspace";
  last_workspace_id: string | null;
}

/** GET /v1/session. The CSRF token is returned to be held in memory only, never stored. */
export function readSession(x: unknown): HostedSession {
  need(isObj(x), "session");
  if (typeof x.schema_version !== "string" || !PRODUCT_API_VERSIONS.includes(x.schema_version)) throw UNSUPPORTED("session");
  const { actor, session } = x;
  need(isObj(actor) && id(actor.user_id) && text(actor.display_name, 256) && text(actor.email, 254)
    && typeof actor.email_verified === "boolean", "session actor");
  // RFC 3339 by pattern: `format: date-time` alone is only an annotation to most schema validators
  need(isObj(session) && id(session.context_id) && typeof session.expires_at === "string"
    && isInstant(session.expires_at), "session context");
  need(text(x.csrf_token, 512), "session token");
  need(x.onboarding === "ready" || x.onboarding === "needs_workspace", "session onboarding");
  need(x.last_workspace_id === null || id(x.last_workspace_id), "session workspace");
  return {
    user_id: actor.user_id as string,
    display_name: actor.display_name as string,
    email: actor.email as string,
    email_verified: actor.email_verified as boolean,
    context_id: session.context_id as string,
    expires_at: session.expires_at as string,
    csrf_token: x.csrf_token as string,
    onboarding: x.onboarding as HostedSession["onboarding"],
    last_workspace_id: x.last_workspace_id as string | null,
  };
}

export const ROLES = ["owner", "admin", "analyst", "reviewer", "viewer"] as const;
export const EXPORT_FORMATS = ["json", "markdown", "pdf"] as const;

export interface HostedCapabilities {
  workspace_id: string;
  workspace_name: string;
  role: (typeof ROLES)[number];
  membership_revision: number;
  analysis_backends: string[];
  supported_source_types: string[];
  supported_export_formats: (typeof EXPORT_FORMATS)[number][];
  /** Hints for what to offer. The server still checks every request and object. */
  allowed_actions: string[];
  features: Record<string, boolean>;
  question_chars: number;
  upload_bundle_bytes: number;
  page_max: number;
  job_poll_min_ms: number;
}

const strList = (x: unknown, max: number): x is string[] => Array.isArray(x) && x.length <= max && x.every(v => text(v, 256));
const positive = (x: unknown): x is number => isInt(x) && x > 0;

/** GET /v1/capabilities for the workspace that was asked for. Another workspace's answer is refused. */
export function readCapabilities(x: unknown, workspaceId: string): HostedCapabilities {
  need(isObj(x), "capabilities");
  if (typeof x.schema_version !== "string" || !PRODUCT_API_VERSIONS.includes(x.schema_version)) throw UNSUPPORTED("capabilities");
  need(x.deployment_mode === "hosted", "deployment mode");
  need(id(x.workspace_id) && text(x.workspace_name, 256), "workspace");
  if (x.workspace_id !== workspaceId) throw OUT_OF_SCOPE();
  const { membership, features, limits } = x;
  need(isObj(membership) && (ROLES as readonly unknown[]).includes(membership.role) && isInt(membership.revision) && membership.revision >= 0, "membership");
  need(strList(x.analysis_backends, 32) && strList(x.supported_source_types, 32) && strList(x.allowed_actions, 128), "capability lists");
  need(Array.isArray(x.supported_export_formats) && x.supported_export_formats.length <= 32
    && x.supported_export_formats.every(f => (EXPORT_FORMATS as readonly unknown[]).includes(f)), "export formats");
  need(isObj(features) && Object.keys(features).length <= 128 && Object.values(features).every(v => typeof v === "boolean"), "features");
  need(isObj(limits) && positive(limits.question_chars) && positive(limits.upload_bundle_bytes)
    && positive(limits.page_max) && positive(limits.job_poll_min_ms), "limits");
  return {
    workspace_id: x.workspace_id as string,
    workspace_name: x.workspace_name as string,
    role: membership.role as HostedCapabilities["role"],
    membership_revision: membership.revision as number,
    analysis_backends: x.analysis_backends as string[],
    supported_source_types: x.supported_source_types as string[],
    supported_export_formats: x.supported_export_formats as HostedCapabilities["supported_export_formats"],
    allowed_actions: x.allowed_actions as string[],
    features: features as Record<string, boolean>,
    question_chars: limits.question_chars as number,
    upload_bundle_bytes: limits.upload_bundle_bytes as number,
    page_max: limits.page_max as number,
    job_poll_min_ms: limits.job_poll_min_ms as number,
  };
}

/** The client's screens and the hosted feature names they would read (proposed back to Kiên).
    A feature the service does not list is off: never a fallback to the demo. */
export const HOSTED_FEATURE: Readonly<Record<string, string>> = {
  counterfactual: "analysis.counterfactual",
  prospective: "analysis.prospective",
};
export const featureOn = (caps: HostedCapabilities, screen: string) =>
  HOSTED_FEATURE[screen] !== undefined && caps.features[HOSTED_FEATURE[screen]] === true;
