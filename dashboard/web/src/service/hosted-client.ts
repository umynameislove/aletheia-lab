/* Same-origin hosted transport. Session/CSRF values live only in this caller-owned context. */
import type { MappingCandidates, ProductView } from "../types.ts";
import { checkProductView } from "./contract.ts";
import {
  PRODUCT_API_VERSIONS, isInstant, readCapabilities, readJob, readJobReceipt, readPage, readProblem, readSession,
  type HostedCapabilities, type HostedJob, type HostedSession,
} from "./hosted.ts";
import { HostedRequestDiscarded, ProductError, type ReportFormat } from "./port.ts";

export interface HostedClientOptions {
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  isCurrent: () => boolean;
  session?: HostedSession;
  signal?: AbortSignal;
  onAnswer?: (status: number, trustedCode: string | null) => void;
  idpOrigin?: string;
  timeoutMs?: number;
  maximumResponseBytes?: number;
}
export type HostedSubmission =
  | { operation: "preview"; source_id: string }
  | { operation: "confirm"; source_id: string; mapping: Record<string, unknown>; preview_id?: string }
  | { operation: "analyze"; snapshot_id: string; question: string };
export interface HostedSource { workspace_id: string; source_id: string; sha256: string }
export interface HostedJobUpdate { revision: number; disposition: "snapshot" | "unchanged" | "resync"; cursor: string;
  poll_after_ms: number | null; valid_for_ms: number; job: HostedJob | null }
export interface HostedPreview { source_sha256: string; source_state_sha256: string; mapping_candidates: MappingCandidates;
  preview_id?: string; workspace_id?: string; source_id?: string; expires_at?: string }
export interface HostedUploadResume { upload_id?: string }
export interface HostedUploadStatus {
  upload_id: string; state: "active" | "ready" | "cancelled" | "expired";
  byte_size: number; sha256: string; expires_at: string; received_bytes: number;
  received_parts: number[]; missing_parts: number[]; source_id: string | null;
}
interface UploadManifest {
  schema_version: "product-api/v1-draft"; source_type: "zip_bundle"; byte_size: number; sha256: string;
  parts: { part_number: number; byte_size: number; sha256: string }[]; parent_source_id: null;
}
export type OutputExpectation =
  | { operation: "preview"; sourceSha256: string; leaseSourceId?: string }
  | { operation: "confirm" }
  | { operation: "analyze"; snapshotId: string; projectId?: string }
  | { operation: "refresh"; projectId: string }
  | { operation: "follow_up"; projectId: string; parentResultId: string; snapshotId: string };
/** RefreshResult: the old snapshot is never changed; unchanged means the same snapshot ID. */
export interface HostedRefreshResult {
  project_id: string; status: "unchanged" | "new_snapshot"; previous_snapshot_id: string; snapshot_id: string; revision: number;
}
/** FollowUpResult is a summary. The child view is read separately from GET results/{result_id}. */
export interface HostedFollowUpResult { project_id: string; result_id: string; parent_result_id: string; snapshot_id: string; revision: number }
/** ExportDescriptor. A null expiry is a legacy descriptor whose expiry is unknown, never an unlimited one. */
export interface HostedExport {
  workspace_id: string; project_id: string; result_id: string; export_id: string; format: ReportFormat;
  byte_size: number; sha256: string; download_url: string; expires_at: string | null;
}
/* K10 bounded reads (catalog.paged_read): a result too large to open whole is read as its header, claim
   pages and one evidence item per claim relation. Each answer names the result, project and snapshot. */
export interface HostedResultHeader {
  project_id: string; result_id: string; snapshot_id: string; display_name: string;
  runtime: { provider: string; model: string; external_call: boolean };
  result: { disposition: string | null; causal_status: string; summary: string; caveat: string };
  /** exact counts of the whole result, never of what has been read */
  counts: { turns: number; claims: number; evidence: number; nodes: number; edges: number };
}
export interface HostedClaim { id: string; text: string; support: string; epistemic_state: string; citation_ids: string[]; counterevidence_ids: string[] }
export interface HostedClaimPage { items: HostedClaim[]; next_cursor: string | null; total: number | null }
export interface HostedEvidenceItem { id: string; kind: string; title: string; text: string; redacted: boolean; relation: "citation" | "counterevidence" }
export interface HostedResultScope { resultId: string; projectId: string; snapshotId: string }
/* K10 graph pages: an edge page carries its endpoint nodes, so nodes repeat across pages; the reader
   merges by ID. Only next_cursor === null ends the graph; `truncated` describes one page. */
export interface HostedGraphNode { id: string; kind: string; source_id: string }
export interface HostedGraphEdge { id: string; kind: string; source: string; target: string }
/* K09 attempt history (jobs.execution_v1): at most three ordered epochs; times are null when unknown. */
export const ATTEMPT_OUTCOMES = ["running", "cancel_requested", "succeeded", "failed", "cancelled", "expired", "lease_lost", "retrying"] as const;
export const FAILURE_KINDS = ["transient", "permanent", "authorization", "attempts_exhausted", "deadline", "cancelled", "conflict"] as const;
export interface HostedJobAttempt { epoch: number; started_at: string | null; heartbeat_at: string | null; finished_at: string | null;
  outcome: (typeof ATTEMPT_OUTCOMES)[number]; failure_kind: (typeof FAILURE_KINDS)[number] | null }
/* jobs.conflict_v1 (Kiên R4.12): the project changed after the job was accepted. Only when the latest attempt ended
   in conflict does the page name that project and its revision as read now: never permission to overwrite. */
export interface HostedJobConflict { project_id: string; current_revision: number }
export interface HostedJobAttempts { retry_of_job_id: string | null; items: HostedJobAttempt[]; conflict: HostedJobConflict | null }
export interface HostedGraphPage { nodes: HostedGraphNode[]; edges: HostedGraphEdge[]; next_cursor: string | null; truncated: boolean }
/* K13 DeletionStatus (projects.deletion_status): how far a logical delete has gone. "purged" removes this
   project's own records only; raw workspace sources and operator backups stay, as the two policies say. */
export interface HostedDeletionStatus {
  project_id: string; tombstone_id: string; revision: number; status: "retained" | "purging" | "purged";
  requested_at: string; eligible_at: string; purged_at: string | null; project_retention_days: number;
  /** where requested_at comes from: only delete_commit is the delete's own time */
  time_basis: "delete_commit" | "migration_first_seen" | "restore_checkpoint";
  records_removed: number; pending_objects: number; shared_objects_retained: number;
  raw_source_policy: "workspace_sources_retained"; backup_policy: "operator_managed_not_erased_here";
}
/** DeletedProject: a logical tombstone. Records are retained; nothing is purged. */
export interface HostedDeletedProject {
  workspace_id: string; project_id: string; revision: number; tombstone_id: string; status: "tombstoned"; retention: "retained";
}
export type HostedOutput = { workspace_id: string; job_id: string } & (
  | { operation: "preview"; result: HostedPreview }
  | { operation: "confirm"; result: { snapshot_id: string } }
  | { operation: "analyze"; result: ProductView }
  | { operation: "refresh"; result: HostedRefreshResult }
  | { operation: "follow_up"; result: HostedFollowUpResult }
);

export { HostedRequestDiscarded };

type Obj = Record<string, unknown>;
const obj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);
const id = (x: unknown): x is string => typeof x === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(x);
const hash = (x: unknown): x is string => typeof x === "string" && /^[0-9a-f]{64}$/.test(x);
const strings = (x: unknown): x is string[] => Array.isArray(x) && x.every(v => typeof v === "string");
const matches = (pattern: RegExp, x: unknown): x is string => typeof x === "string" && pattern.test(x);
/** A PROJECT revision, the CAS value; a membership revision is never one. */
const revision = (x: unknown): x is number => Number.isSafeInteger(x) && (x as number) >= 0;
const PROJECT = /^p3-project-[0-9a-f]{64}$/, UPLOAD_SNAPSHOT = /^upload-snapshot-[0-9a-f]{64}$/;
const SNAPSHOT = /^(?:p3-snapshot|upload-snapshot)-[0-9a-f]{64}$/, RESULT = /^p6-result-[0-9a-f]{64}$/;
const TOMBSTONE = /^p6-deletion-[0-9a-f]{64}$/;
const EXPORT_BYTES = 33_554_432;
/** The media type each report format is delivered as (K12: PDF is additive and only offered when advertised). */
const EXPORT_MEDIA: Readonly<Record<ReportFormat, string>> = { json: "application/json", markdown: "text/markdown", pdf: "application/pdf" };
const exportFormat = (value: unknown): value is ReportFormat => typeof value === "string" && Object.keys(EXPORT_MEDIA).includes(value);
const invalid = () => new ProductError("response_invalid", "The service sent an answer this app cannot trust.");
const outOfScope = (what: string) => new ProductError("response_out_of_scope", `The service answered about another ${what}.`);
const unknownOutcome = () => new ProductError("outcome_unknown", "The change could not be confirmed. Check its state before retrying with the same request key.");
function need(ok: boolean): asserts ok { if (!ok) throw invalid(); }
function input(ok: boolean): asserts ok {
  if (!ok) throw new ProductError("invalid_request", "Check the request values before trying again.");
}
function envelope(value: unknown, workspace: string): Obj {
  need(obj(value));
  if (typeof value.schema_version !== "string" || !PRODUCT_API_VERSIONS.includes(value.schema_version)) {
    throw new ProductError("schema_unsupported", "The app and service versions differ.");
  }
  if (value.workspace_id !== workspace) throw new ProductError("response_out_of_scope", "The service answered about another workspace.");
  return value;
}

function preview(value: unknown, sourceSha256: string, workspace: string, leaseSourceId?: string): HostedPreview {
  need(obj(value) && hash(value.source_sha256) && value.source_sha256 === sourceSha256
    && hash(value.source_state_sha256) && obj(value.mapping_candidates));
  const candidates = value.mapping_candidates;
  const reason = (x: unknown) => x === null || ["sensitive_fields_withheld", "unsupported_source", "unreadable_source"].includes(x as string);
  const base = (x: unknown) => obj(x) && typeof x.project_item_id === "string" && typeof x.label === "string"
    && (x.mapping_id === null || typeof x.mapping_id === "string") && typeof x.available === "boolean"
    && reason(x.unavailable_reason) && (x.relative_path === null || typeof x.relative_path === "string")
    && (x.available ? x.mapping_id !== null && x.unavailable_reason === null : x.mapping_id === null && x.unavailable_reason !== null);
  need(Array.isArray(candidates.targets) && candidates.targets.every(x => base(x) && obj(x) && strings(x.field_names)));
  need(Array.isArray(candidates.configs) && candidates.configs.every(base));
  need(Array.isArray(candidates.metric_sources) && candidates.metric_sources.every(x => base(x) && obj(x)
    && (x.format === "csv" || x.format === "json") && strings(x.field_names) && strings(x.run_id_field_candidates)
    && strings(x.metric_name_candidates) && Array.isArray(x.records_path_candidates) && x.records_path_candidates.every(strings)));
  need(Array.isArray(candidates.runs) && candidates.runs.every(x => obj(x) && typeof x.run_id === "string"
    && typeof x.available === "boolean" && reason(x.unavailable_reason)
    && x.available === (x.unavailable_reason === null) && strings(x.source_metric_item_ids) && strings(x.available_config_item_ids)));
  const result: HostedPreview = { source_sha256: value.source_sha256, source_state_sha256: value.source_state_sha256,
    mapping_candidates: candidates as unknown as MappingCandidates };
  if (leaseSourceId !== undefined) {
    need(value.schema_version === "product-api/v1-draft" && typeof value.preview_id === "string"
      && /^prev_[0-9a-f]{32}$/.test(value.preview_id) && typeof value.expires_at === "string" && isInstant(value.expires_at));
    if (value.workspace_id !== workspace || value.source_id !== leaseSourceId) {
      throw new ProductError("response_out_of_scope", "The preview belongs to another workspace or source.");
    }
    Object.assign(result, { preview_id: value.preview_id, workspace_id: workspace, source_id: leaseSourceId, expires_at: value.expires_at });
  }
  return result;
}

function uploadStatus(value: unknown, workspace: string, manifest: UploadManifest, uploadId?: string): HostedUploadStatus {
  const x = envelope(value, workspace);
  need(id(x.upload_id) && (uploadId === undefined || x.upload_id === uploadId)
    && x.byte_size === manifest.byte_size && x.sha256 === manifest.sha256
    && typeof x.expires_at === "string" && isInstant(x.expires_at)
    && ["active", "ready", "cancelled", "expired"].includes(x.state as string));
  const partList = (value: unknown): value is number[] => Array.isArray(value) && value.every(n =>
    Number.isSafeInteger(n) && n >= 1 && n <= manifest.parts.length) && new Set(value).size === value.length;
  need(partList(x.received_parts) && partList(x.missing_parts));
  const all = [...x.received_parts, ...x.missing_parts];
  need(all.length === manifest.parts.length && new Set(all).size === all.length
    && x.received_bytes === x.received_parts.reduce((sum, n) => sum + manifest.parts[n - 1].byte_size, 0)
    && (x.state === "ready" ? id(x.source_id) && x.missing_parts.length === 0 : x.source_id === null));
  return { upload_id: x.upload_id, state: x.state as HostedUploadStatus["state"], byte_size: manifest.byte_size,
    sha256: manifest.sha256, expires_at: x.expires_at, received_bytes: x.received_bytes as number,
    received_parts: x.received_parts, missing_parts: x.missing_parts, source_id: x.source_id as string | null };
}

/* A hidden or offline tab reads nothing in the background (Kiên, 13/11): polling waits here until the tab is
   shown and online again. The job itself keeps running on the server; nothing is cancelled or resubmitted.
   Answers how long it waited, so a client deadline does not count the time away. */
function shown(signal?: AbortSignal): Promise<number> {
  const page = (globalThis as { document?: Document }).document;
  const away = () => page?.visibilityState === "hidden" || globalThis.navigator?.onLine === false;
  if (!page || !away() || signal?.aborted) return Promise.resolve(0);
  const start = performance.now(), win = globalThis as unknown as Partial<EventTarget>;
  return new Promise(resolve => {
    const done = () => { if (away() && !signal?.aborted) return;
      page.removeEventListener("visibilitychange", done); win.removeEventListener?.("online", done); signal?.removeEventListener("abort", done);
      resolve(performance.now() - start); };
    page.addEventListener("visibilitychange", done); win.addEventListener?.("online", done); signal?.addEventListener("abort", done);
  });
}

export function createHostedClient(options: HostedClientOptions) {
  const { fetch: requestFetch, session, signal, isCurrent, onAnswer } = options;
  const timeout = options.timeoutMs ?? 15_000, maximum = options.maximumResponseBytes ?? 1_048_576;
  input(typeof isCurrent === "function" && Number.isSafeInteger(timeout) && timeout > 0 && timeout <= 120_000
    && Number.isSafeInteger(maximum) && maximum > 0 && maximum <= 16_777_216);
  let idpOrigin: string | undefined;
  if (options.idpOrigin !== undefined) {
    try {
      const url = new URL(options.idpOrigin);
      input(url.protocol === "https:" && options.idpOrigin === url.origin);
      idpOrigin = url.origin;
    } catch { throw new ProductError("invalid_request", "A trusted identity origin is required."); }
  }
  const actor = session?.user_id, context = session?.context_id;
  const check = (mutation = false, extra?: AbortSignal) => {
    if (signal?.aborted || extra?.aborted || !isCurrent() || session?.user_id !== actor || session?.context_id !== context) {
      throw new HostedRequestDiscarded(mutation);
    }
  };
  const authenticated = () => {
    check();
    input(session !== undefined && id(session.user_id) && id(session.context_id)
      && /^[A-Za-z0-9_.-]{1,512}$/.test(session.csrf_token));
    return session;
  };
  const base = (workspace: string) => { input(id(workspace)); return `/v1/workspaces/${workspace}`; };
  const capability = (workspace: string, caps: HostedCapabilities, action: string) => {
    authenticated();
    input(caps.workspace_id === workspace && caps.allowed_actions.includes(action)
      && Number.isSafeInteger(caps.upload_bundle_bytes) && caps.upload_bundle_bytes > 0
      && Number.isSafeInteger(caps.question_chars) && caps.question_chars > 0);
  };
  const key = (value: string) => { input(/^[A-Za-z0-9_-]{16,128}$/.test(value)); return value; };
  const feature = (workspace: string, caps: HostedCapabilities, name: string) => {
    authenticated();
    if (caps.workspace_id !== workspace || caps.features?.[name] !== true) {
      throw new ProductError("feature_unavailable", "This workspace does not offer that job control.");
    }
  };
  const paged = (workspace: string, caps: HostedCapabilities) => {
    authenticated();
    if (caps.workspace_id !== workspace || caps.features?.["catalog.paged_read"] !== true) {
      throw new ProductError("feature_unavailable", "This workspace does not offer paged reading.");
    }
  };

  /* `ifMatch` is a project revision sent as a quoted CAS precondition. `raw` reads a successful body as
     bytes of exactly one media type, bounded by its own maximum; failures are still Problem JSON. */
  async function request<T>(path: string, method: "GET" | "POST" | "PUT" | "DELETE", decode: (value: unknown, status: number) => T,
                            body?: BodyInit, contentType?: string, requestKey?: string,
                            extraSignal?: AbortSignal, budget = timeout,
                            extra: { ifMatch?: number; raw?: { media: string; maximum: number };
                                     /** every answer's status and its Retry-After in ms, for callers that pace themselves */
                                     onStatus?: (status: number, retryAfterMs: number | null) => void } = {}): Promise<T> {
    const mutation = method !== "GET";
    check(false, extraSignal);
    const headers: Record<string, string> = { Accept: extra.raw ? `${extra.raw.media}, application/problem+json` : "application/json" };
    if (contentType) headers["Content-Type"] = contentType;
    if (mutation) headers["X-CSRF-Token"] = authenticated().csrf_token;
    if (requestKey !== undefined) headers["Idempotency-Key"] = key(requestKey);
    if (extra.ifMatch !== undefined) { input(revision(extra.ifMatch)); headers["If-Match"] = `"${extra.ifMatch}"`; }
    const controller = new AbortController();
    const deadline = performance.now() + Math.min(timeout, budget);
    let expired = false;
    const timeoutFailure = () => mutation ? unknownOutcome() : new ProductError("deadline_exceeded", "The service did not answer in time.");
    let rejectStop: (error: Error) => void = () => {};
    const stopped = new Promise<never>((_, reject) => { rejectStop = reject; });
    const abort = () => { controller.abort(); rejectStop(new HostedRequestDiscarded(mutation)); };
    signal?.addEventListener("abort", abort, { once: true });
    extraSignal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(() => {
      expired = true;
      controller.abort();
      rejectStop(timeoutFailure());
    }, Math.min(timeout, budget));
    const active = () => {
      check(mutation, extraSignal);
      if (expired || performance.now() >= deadline) throw timeoutFailure();
      if (controller.signal.aborted) throw new HostedRequestDiscarded(mutation);
    };
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let complete = false;
    const work = async () => {
      const response = await requestFetch(path, { method, headers, body, credentials: "same-origin", redirect: "error",
        cache: "no-store", signal: controller.signal });
      try { active(); } catch (error) { void response.body?.cancel().catch(() => {}); throw error; }
      need(!response.redirected && response.status >= 200 && response.status < 600);
      const retryAfter = response.headers.get("retry-after");
      extra.onStatus?.(response.status, retryAfter !== null && /^\d{1,5}$/.test(retryAfter) ? Number(retryAfter) * 1000 : null);
      let value: unknown = null;
      const raw = response.status >= 200 && response.status < 300 ? extra.raw : undefined;
      if (response.status !== 204) {
        try {
          const declaredType = response.headers.get("content-type") ?? "";
          const media = declaredType.split(";", 1)[0].trim().toLowerCase();
          if (raw) need(media === raw.media && /^[^;]*(;\s*charset=utf-8\s*)?$/i.test(declaredType));
          else need(media === "application/json" || media === "application/problem+json");
          const limit = raw ? raw.maximum : maximum;
          const declared = response.headers.get("content-length");
          need(declared === null || (/^\d+$/.test(declared) && Number(declared) <= limit));
          need(response.body !== null);
          reader = response.body.getReader();
          const decoder = new TextDecoder("utf-8", { fatal: true });
          let bytes = 0, text = "";
          const chunks: Uint8Array[] = [];
          while (true) {
            const chunk = await reader.read();
            active();
            if (chunk.done) break;
            bytes += chunk.value.byteLength;
            need(bytes <= limit);
            if (raw) chunks.push(chunk.value); else text += decoder.decode(chunk.value, { stream: true });
          }
          if (raw) {
            const all = new Uint8Array(bytes);
            let offset = 0;
            for (const chunk of chunks) { all.set(chunk, offset); offset += chunk.byteLength; }
            value = all;
          } else {
            text += decoder.decode();
            value = JSON.parse(text);
          }
          complete = true;
        } catch (error) {
          active();
          if (response.status >= 400) onAnswer?.(response.status, null);
          if (mutation) throw unknownOutcome();
          throw error instanceof ProductError ? error : invalid();
        }
      }
      active();
      if (response.status >= 300) {
        const failure = readProblem(value, response.status);
        onAnswer?.(response.status, failure?.code ?? null);
        // Only a trusted refusal below 500 settles a mutation; a 5xx may follow an effect already committed.
        if (mutation && (!failure || response.status >= 500)) throw unknownOutcome();
        throw failure ?? invalid();
      }
      let decoded: T;
      try { decoded = decode(value, response.status); }
      catch (error) { if (mutation) throw unknownOutcome(); throw error; }
      active();
      onAnswer?.(response.status, null);
      active();
      return decoded;
    };
    try { return await Promise.race([work(), stopped]); }
    catch (error) {
      check(mutation, extraSignal);
      if (expired || performance.now() >= deadline) throw timeoutFailure();
      if (error instanceof ProductError || error instanceof HostedRequestDiscarded) throw error;
      throw mutation ? unknownOutcome() : new ProductError("unavailable", "The service could not be reached.");
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      extraSignal?.removeEventListener("abort", abort);
      if (reader) {
        if (!complete) void reader.cancel().catch(() => {});
        try { reader.releaseLock(); } catch { /* A cancelled read may still be settling. */ }
      }
    }
  }

  const loadJob = (workspace: string, jobId: string, extraSignal?: AbortSignal, budget?: number) => {
    authenticated(); input(id(jobId));
    return request(`${base(workspace)}/jobs/${jobId}`, "GET", (value, status) => {
      need(status === 200); return readJob(value, workspace, jobId);
    }, undefined, undefined, undefined, extraSignal, budget);
  };
  /* K15 JobUpdate: current state coalesced, not an event log. `unchanged` carries no job; the caller keeps
     the one it holds. A pending update names when to ask again; a terminal one says null. */
  const loadJobUpdate = (workspace: string, jobId: string, cursor: string | null, extraSignal: AbortSignal | undefined, budget: number,
                         onStatus: (status: number, retryAfterMs: number | null) => void) => {
    authenticated(); input(id(jobId) && (cursor === null || (cursor.length >= 1 && cursor.length <= 4096)));
    const query = cursor === null ? "" : "?" + new URLSearchParams({ cursor });
    return request(`${base(workspace)}/jobs/${jobId}/updates${query}`, "GET", (value, status): HostedJobUpdate => {
      need(status === 200); const x = envelope(value, workspace);
      if (x.job_id !== jobId) throw outOfScope("job");
      const valid = x.authorization_valid_for_ms ?? 4000;
      need(Number.isSafeInteger(x.revision) && (x.revision as number) >= 1
        && (x.disposition === "snapshot" || x.disposition === "unchanged" || x.disposition === "resync")
        && typeof x.cursor === "string" && x.cursor.length >= 1 && x.cursor.length <= 4096
        && (x.poll_after_ms === null || (Number.isSafeInteger(x.poll_after_ms) && (x.poll_after_ms as number) >= 2000 && (x.poll_after_ms as number) <= 3500))
        && valid === 4000 && (x.disposition === "unchanged" ? x.job === null : obj(x.job)));
      return { revision: x.revision as number, disposition: x.disposition, cursor: x.cursor, poll_after_ms: x.poll_after_ms as number | null,
        valid_for_ms: valid, job: x.job === null ? null : readJob(x.job, workspace, jobId) };
    }, undefined, undefined, undefined, extraSignal, budget, { onStatus });
  };
  async function uploadParts(workspace: string, bundle: Blob, requestKey: string,
      upload: { resume?: HostedUploadResume; onProgress?: (status: HostedUploadStatus) => void; signal?: AbortSignal }) {
    const digest = async (blob: Blob) => {
      const bytes = await blob.arrayBuffer(); check(false, upload.signal);
      const value = await crypto.subtle.digest("SHA-256", bytes); check(false, upload.signal);
      return Array.from(new Uint8Array(value), byte => byte.toString(16).padStart(2, "0")).join("");
    };
    const manifest: UploadManifest = { schema_version: "product-api/v1-draft", source_type: "zip_bundle",
      byte_size: bundle.size, sha256: await digest(bundle), parts: [], parent_source_id: null };
    const partBytes = 4 * 1024 * 1024;
    for (let start = 0; start < bundle.size; start += partBytes) {
      const part = bundle.slice(start, Math.min(start + partBytes, bundle.size));
      manifest.parts.push({ part_number: manifest.parts.length + 1, byte_size: part.size, sha256: await digest(part) });
    }
    const route = base(workspace) + "/uploads";
    const decode = (expectedStatus: number, uploadId?: string) => (value: unknown, status: number) => {
      need(status === expectedStatus); return uploadStatus(value, workspace, manifest, uploadId);
    };
    let state = upload.resume?.upload_id
      ? await request(`${route}/${upload.resume.upload_id}`, "GET", decode(200, upload.resume.upload_id), undefined, undefined, undefined, upload.signal)
      : await request(route, "POST", decode(201), JSON.stringify(manifest), "application/json", requestKey, upload.signal);
    if (upload.resume) upload.resume.upload_id = state.upload_id;
    const report = () => {
      check(false, upload.signal);
      if (state.state === "expired" || (state.state === "active" && Date.parse(state.expires_at) <= Date.now())) {
        throw new ProductError("upload_expired", "The upload expired. Start a new upload after reviewing the selected ZIP.");
      }
      if (state.state === "cancelled") throw new ProductError("upload_cancelled", "This upload was cancelled. Start a new upload to continue.");
      upload.onProgress?.(state); check(false, upload.signal);
    };
    report();
    for (const number of [...state.missing_parts].sort((a, b) => a - b)) {
      if (state.received_parts.includes(number)) continue;
      const previous = state.received_parts;
      state = await request(`${route}/${state.upload_id}/parts/${number}`, "PUT", decode(200, state.upload_id),
        bundle.slice((number - 1) * partBytes, Math.min(number * partBytes, bundle.size)), "application/octet-stream", undefined, upload.signal);
      need(state.received_parts.includes(number) && previous.every(part => state.received_parts.includes(part)));
      report();
    }
    if (state.state !== "ready") {
      need(state.missing_parts.length === 0);
      state = await request(`${route}/${state.upload_id}/complete`, "POST", decode(200, state.upload_id), "{}", "application/json", undefined, upload.signal);
      report();
    }
    need(state.state === "ready" && id(state.source_id));
    return { workspace_id: workspace, source_id: state.source_id, sha256: manifest.sha256 };
  }
  const polling = new Set<string>();
  return {
    /** ProductView check, for the hosted port: carried here so the main chunk need not load the contract. */
    checkView: checkProductView,
    loginUrl: () => "/v1/auth/start",
    loadSession: () => request("/v1/session", "GET", (value, status) => { need(status === 200); return readSession(value); }),
    loadCaps: (workspace: string) => {
      authenticated();
      return request(base(workspace) + "/capabilities", "GET", (value, status) => { need(status === 200); return readCapabilities(value, workspace); });
    },
    renew: () => {
      authenticated();
      return request("/v1/auth/renew", "POST", (value, status) => { need(status === 200); return readSession(value); }, "{}", "application/json");
    },
    logoutAll: () => {
      authenticated();
      return request("/v1/auth/logout-all", "POST", (_value, status) => { need(status === 204); }, "{}", "application/json");
    },
    uploadZip: (workspace: string, caps: HostedCapabilities, bundle: Blob, requestKey: string,
                upload: { resume?: HostedUploadResume; onProgress?: (status: HostedUploadStatus) => void; signal?: AbortSignal } = {}) => {
      capability(workspace, caps, "import");
      check(false, upload.signal);
      input(bundle instanceof Blob && bundle.size > 0 && bundle.size <= caps.upload_bundle_bytes);
      key(requestKey);
      if (caps.features?.["sources.resumable"] === true) {
        input(bundle.size <= 200 * 1024 * 1024 && (upload.resume?.upload_id === undefined || id(upload.resume.upload_id)));
        if (!globalThis.crypto?.subtle) throw new ProductError("feature_unavailable", "Secure file hashing is unavailable in this browser.");
        return uploadParts(workspace, bundle, requestKey, upload);
      }
      return request(base(workspace) + "/sources", "POST", (value, status): HostedSource => {
        need(status === 201); const x = envelope(value, workspace); need(id(x.source_id) && hash(x.sha256));
        return { workspace_id: workspace, source_id: x.source_id, sha256: x.sha256 };
      }, bundle, "application/zip", key(requestKey), upload.signal);
    },
    submitJob: (workspace: string, caps: HostedCapabilities, submission: HostedSubmission, requestKey: string, extraSignal?: AbortSignal) => {
      input(obj(submission));
      capability(workspace, caps, submission.operation === "analyze" ? "analyze" : "import");
      const leased = caps.features?.["sources.preview_lease"] === true && submission.operation !== "analyze";
      if (submission.operation === "analyze") {
        input(Object.keys(submission).sort().join() === "operation,question,snapshot_id" && id(submission.snapshot_id)
          && typeof submission.question === "string" && submission.question.trim() === submission.question
          && submission.question.length > 0 && [...submission.question].length <= Math.min(2000, caps.question_chars));
      } else {
        input((submission.operation === "preview" || submission.operation === "confirm") && id(submission.source_id)
          && Object.keys(submission).sort().join() === (submission.operation === "preview" ? "operation,source_id"
            : leased ? "mapping,operation,preview_id,source_id" : "mapping,operation,source_id")
          && (submission.operation !== "confirm" || obj(submission.mapping)
            && !Object.keys(submission.mapping).some(name => name.startsWith("_"))
            && (!leased || typeof submission.preview_id === "string" && /^prev_[0-9a-f]{32}$/.test(submission.preview_id))));
      }
      const payload = !leased ? submission : submission.operation === "preview"
        ? { schema_version: "product-api/v1-draft", source_id: submission.source_id }
        : { schema_version: "product-api/v1-draft", mapping: (submission as Extract<HostedSubmission, { operation: "confirm" }>).mapping };
      const route = !leased ? base(workspace) + "/jobs" : submission.operation === "preview"
        ? base(workspace) + "/imports/preview"
        : base(workspace) + "/imports/" + (submission as Extract<HostedSubmission, { operation: "confirm" }>).preview_id + "/confirm";
      let body: string;
      try { body = JSON.stringify(payload); } catch { throw new ProductError("invalid_request", "The job values cannot be sent."); }
      input(new TextEncoder().encode(body).byteLength <= 65_536);
      return request(route, "POST", (value, status) => {
        need(status === 202); return readJobReceipt(value, workspace);
      }, body, "application/json", key(requestKey), extraSignal);
    },
    loadJob,
    loadOutput: (workspace: string, jobId: string, expected: OutputExpectation, extraSignal?: AbortSignal) => {
      authenticated(); input(id(jobId));
      input(expected.operation === "preview" ? hash(expected.sourceSha256) && (expected.leaseSourceId === undefined || id(expected.leaseSourceId))
        : expected.operation === "refresh" ? id(expected.projectId)
        : expected.operation === "follow_up" ? id(expected.projectId) && id(expected.parentResultId) && id(expected.snapshotId)
        : expected.operation === "confirm" || (expected.operation === "analyze" && id(expected.snapshotId)
          && (expected.projectId === undefined || id(expected.projectId))));
      return request(`${base(workspace)}/jobs/${jobId}/output`, "GET", (value, status): HostedOutput => {
        need(status === 200); const x = envelope(value, workspace);
        if (x.job_id !== jobId || x.operation !== expected.operation) throw new ProductError("response_out_of_scope", "The service answered about another job.");
        const scope = { workspace_id: workspace, job_id: jobId };
        if (expected.operation === "preview") return { ...scope, operation: "preview", result: preview(x.result, expected.sourceSha256, workspace, expected.leaseSourceId) };
        if (expected.operation === "confirm") {
          need(obj(x.result) && typeof x.result.snapshot_id === "string" && /^upload-snapshot-[0-9a-f]{64}$/.test(x.result.snapshot_id));
          return { ...scope, operation: "confirm", result: { snapshot_id: x.result.snapshot_id } };
        }
        if (expected.operation === "refresh") {
          const r = x.result;
          need(obj(r) && matches(PROJECT, r.project_id) && (r.status === "unchanged" || r.status === "new_snapshot")
            && matches(UPLOAD_SNAPSHOT, r.previous_snapshot_id) && matches(UPLOAD_SNAPSHOT, r.snapshot_id) && revision(r.revision)
            && (r.status === "unchanged") === (r.snapshot_id === r.previous_snapshot_id));
          if (r.project_id !== expected.projectId) throw outOfScope("project");
          return { ...scope, operation: "refresh", result: { project_id: r.project_id, status: r.status as HostedRefreshResult["status"],
            previous_snapshot_id: r.previous_snapshot_id, snapshot_id: r.snapshot_id, revision: r.revision } };
        }
        if (expected.operation === "follow_up") {
          const r = x.result;
          need(obj(r) && matches(PROJECT, r.project_id) && matches(RESULT, r.result_id) && matches(RESULT, r.parent_result_id)
            && matches(SNAPSHOT, r.snapshot_id) && revision(r.revision) && r.result_id !== r.parent_result_id);
          if (r.project_id !== expected.projectId || r.parent_result_id !== expected.parentResultId || r.snapshot_id !== expected.snapshotId) {
            throw outOfScope("result, project or snapshot");
          }
          return { ...scope, operation: "follow_up", result: { project_id: r.project_id, result_id: r.result_id,
            parent_result_id: r.parent_result_id, snapshot_id: r.snapshot_id, revision: r.revision } };
        }
        const view = checkProductView(x.result);
        if (view.demo_only || view.snapshot.id !== expected.snapshotId || (expected.projectId !== undefined && view.project.id !== expected.projectId)) {
          throw new ProductError("response_out_of_scope", "The analysis belongs to another snapshot or project.");
        }
        return { ...scope, operation: "analyze", result: view };
      }, undefined, undefined, undefined, extraSignal);
    },
    /** The PROJECT revision for CAS: ProjectDetails when the management route is on, else the project's
        ProjectPage entry. A deleting project is not offered for another change. */
    loadProjectRevision: async (workspace: string, caps: HostedCapabilities, projectId: string, extraSignal?: AbortSignal): Promise<number> => {
      authenticated(); input(id(projectId) && caps.workspace_id === workspace && Number.isSafeInteger(caps.page_max) && caps.page_max > 0);
      const details = await request(`${base(workspace)}/projects/${projectId}`, "GET", (value, status): number | null => {
        need(status === 200); const x = envelope(value, workspace);
        if (x.project_id !== projectId) throw outOfScope("project");
        if ("record_kind" in x) return null;
        need(revision(x.revision) && (x.state === "active" || x.state === "deleting"));
        if (x.state !== "active") throw new ProductError("not_found", "This project is being deleted.");
        return x.revision;
      }, undefined, undefined, undefined, extraSignal);
      if (details !== null) return details;
      let cursor: string | null = null;
      for (let page = 0; page < 50; page++) {
        const query = new URLSearchParams({ limit: String(caps.page_max) });
        if (cursor !== null) query.set("cursor", cursor);
        const listed = await request(`${base(workspace)}/projects?${query}`, "GET", (value, status) => {
          need(status === 200); return readPage<Obj>(value, workspace, caps.page_max);
        }, undefined, undefined, undefined, extraSignal);
        for (const item of listed.items) {
          need(id(item.project_id) && revision(item.revision));
          if (item.project_id === projectId) return item.revision;
        }
        cursor = listed.next_cursor;
        if (cursor === null) break;
      }
      throw new ProductError("not_found", "This project is not listed in this workspace.");
    },
    refreshProject: (workspace: string, caps: HostedCapabilities, projectId: string, sourceId: string,
                     expectedRevision: number, requestKey: string, extraSignal?: AbortSignal) => {
      capability(workspace, caps, "analyze");
      input(id(projectId) && id(sourceId) && revision(expectedRevision));
      return request(`${base(workspace)}/projects/${projectId}/refresh`, "POST", (value, status) => {
        need(status === 202); return readJobReceipt(value, workspace);
      }, JSON.stringify({ schema_version: "product-api/v1-draft", source_id: sourceId }), "application/json",
      key(requestKey), extraSignal, undefined, { ifMatch: expectedRevision });
    },
    submitFollowUp: (workspace: string, caps: HostedCapabilities, parentResultId: string,
                     selection: { kind: "claim" | "node"; id: string }, question: string,
                     expectedRevision: number, requestKey: string, extraSignal?: AbortSignal) => {
      capability(workspace, caps, "analyze");
      input(id(parentResultId) && obj(selection) && (selection.kind === "claim" || selection.kind === "node") && id(selection.id)
        && typeof question === "string" && question.trim() === question && question.length > 0
        && [...question].length <= Math.min(4000, caps.question_chars) && revision(expectedRevision));
      return request(`${base(workspace)}/results/${parentResultId}/follow-ups`, "POST", (value, status) => {
        need(status === 202); return readJobReceipt(value, workspace);
      }, JSON.stringify({ schema_version: "product-api/v1-draft", selection_kind: selection.kind, selection_id: selection.id, question }),
      "application/json", key(requestKey), extraSignal, undefined, { ifMatch: expectedRevision });
    },
    /** GET projects/{id}/deletion-status: read again on request, never polled; it is held to the tombstone it reports on. */
    loadDeletionStatus: (workspace: string, caps: HostedCapabilities, projectId: string, tombstoneId: string, extraSignal?: AbortSignal) => {
      authenticated();
      if (caps.workspace_id !== workspace || caps.features?.["projects.deletion_status"] !== true) {
        throw new ProductError("feature_unavailable", "This workspace does not report deletion status.");
      }
      input(matches(PROJECT, projectId) && matches(TOMBSTONE, tombstoneId));
      return request(`${base(workspace)}/projects/${projectId}/deletion-status`, "GET", (value, status): HostedDeletionStatus => {
        need(status === 200); const x = envelope(value, workspace);
        if (x.project_id !== projectId || x.tombstone_id !== tombstoneId) throw outOfScope("project or deletion");
        const count = (n: unknown): n is number => Number.isSafeInteger(n) && (n as number) >= 0;
        const instant = (s: unknown): s is string => typeof s === "string" && isInstant(s);
        need(["retained", "purging", "purged"].includes(x.status as string)
          && ["delete_commit", "migration_first_seen", "restore_checkpoint"].includes(x.time_basis as string)
          && revision(x.revision) && instant(x.requested_at) && instant(x.eligible_at)
          && (x.status === "purged" ? instant(x.purged_at) : x.purged_at === null)
          && count(x.project_retention_days) && (x.project_retention_days as number) <= 3650
          && count(x.records_removed) && count(x.pending_objects) && count(x.shared_objects_retained)
          // the two scope statements are fixed: anything else would claim more, or less, than was erased
          && (x.raw_source_policy ?? "workspace_sources_retained") === "workspace_sources_retained"
          && (x.backup_policy ?? "operator_managed_not_erased_here") === "operator_managed_not_erased_here");
        return { project_id: projectId, tombstone_id: tombstoneId, revision: x.revision as number,
          status: x.status as HostedDeletionStatus["status"], requested_at: x.requested_at as string, eligible_at: x.eligible_at as string,
          purged_at: x.purged_at as string | null, project_retention_days: x.project_retention_days as number,
          time_basis: x.time_basis as HostedDeletionStatus["time_basis"], records_removed: x.records_removed as number,
          pending_objects: x.pending_objects as number, shared_objects_retained: x.shared_objects_retained as number,
          raw_source_policy: "workspace_sources_retained", backup_policy: "operator_managed_not_erased_here" };
      }, undefined, undefined, undefined, extraSignal);
    },
    /** GET results/{id}/header: the bounded summary and exact counts, without metric arrays or turns. */
    loadResultHeader: (workspace: string, caps: HostedCapabilities, scope: HostedResultScope, extraSignal?: AbortSignal) => {
      const { resultId, projectId, snapshotId } = scope;
      paged(workspace, caps); input(matches(RESULT, resultId) && id(projectId) && id(snapshotId));
      return request(`${base(workspace)}/results/${resultId}/header`, "GET", (value, status): HostedResultHeader => {
        need(status === 200); const x = envelope(value, workspace);
        if (x.result_id !== resultId || x.project_id !== projectId || x.snapshot_id !== snapshotId) throw outOfScope("result, project or snapshot");
        const { project: p, runtime: rt, result: r } = x;
        need(obj(p) && p.id === projectId && typeof p.display_name === "string"
          && obj(rt) && typeof rt.provider === "string" && typeof rt.model === "string" && typeof rt.external_call === "boolean"
          && obj(r) && r.id === resultId && (r.disposition === null || typeof r.disposition === "string")
          && typeof r.causal_status === "string" && typeof r.summary === "string" && typeof r.caveat === "string");
        const counts = [x.turn_count, x.claim_count, x.evidence_count, x.node_count, x.edge_count];
        need(counts.every(n => Number.isSafeInteger(n) && (n as number) >= 0));
        const [turns, claims, evidence, nodes, edges] = counts as number[];
        return { project_id: projectId, result_id: resultId, snapshot_id: snapshotId, display_name: p.display_name,
          runtime: { provider: rt.provider, model: rt.model, external_call: rt.external_call },
          result: { disposition: r.disposition as string | null, causal_status: r.causal_status, summary: r.summary, caveat: r.caveat },
          counts: { turns, claims, evidence, nodes, edges } };
      }, undefined, undefined, undefined, extraSignal);
    },
    /** GET results/{id}/claims: one page. The cursor is opaque and only continues the same search. */
    loadClaimPage: (workspace: string, caps: HostedCapabilities, scope: HostedResultScope,
                    page: { cursor: string | null; search: string; limit?: number }, extraSignal?: AbortSignal) => {
      paged(workspace, caps);
      const limit = page.limit ?? 50;
      input(matches(RESULT, scope.resultId) && id(scope.projectId) && id(scope.snapshotId) && Number.isSafeInteger(limit) && limit >= 1 && limit <= 100
        && [...page.search].length <= 128 && (page.cursor === null || (page.cursor.length >= 1 && page.cursor.length <= 4096)));
      const query = new URLSearchParams({ limit: String(limit), ...(page.cursor ? { cursor: page.cursor } : {}), ...(page.search ? { search: page.search } : {}) });
      return request(`${base(workspace)}/results/${scope.resultId}/claims?${query}`, "GET", (value, status): HostedClaimPage => {
        need(status === 200); const answer = readPage<Obj>(value, workspace, limit), x = value as Obj;
        if (x.result_id !== scope.resultId || x.project_id !== scope.projectId || x.snapshot_id !== scope.snapshotId) throw outOfScope("result, project or snapshot");
        const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every(id);
        const items = answer.items.map(c => {
          need(id(c.id) && typeof c.text === "string" && typeof c.support === "string" && typeof c.epistemic_state === "string"
            && ids(c.citation_ids) && ids(c.counterevidence_ids));
          return { id: c.id, text: c.text, support: c.support, epistemic_state: c.epistemic_state, citation_ids: c.citation_ids, counterevidence_ids: c.counterevidence_ids };
        });
        need(new Set(items.map(c => c.id)).size === items.length);
        return { items, next_cursor: answer.next_cursor, total: answer.total_kind === "exact" ? answer.total : null };
      }, undefined, undefined, undefined, extraSignal);
    },
    /** GET jobs/{id}/attempts: the job's bounded attempt history, never its credential or payload. */
    loadJobAttempts: (workspace: string, caps: HostedCapabilities, jobId: string, extraSignal?: AbortSignal) => {
      feature(workspace, caps, "jobs.execution_v1"); input(id(jobId));
      return request(`${base(workspace)}/jobs/${jobId}/attempts`, "GET", (value, status): HostedJobAttempts => {
        need(status === 200); const x = envelope(value, workspace);
        if (x.job_id !== jobId) throw new ProductError("response_out_of_scope", "The service answered about another job.");
        need((x.retry_of_job_id === null || id(x.retry_of_job_id)) && Array.isArray(x.items) && x.items.length <= 3);
        const time = (v: unknown): v is string | null => v === null || (typeof v === "string" && isInstant(v));
        const items = (x.items as unknown[]).map(a => {
          need(obj(a) && Number.isSafeInteger(a.epoch) && (a.epoch as number) >= 1 && (a.epoch as number) <= 3
            && time(a.started_at) && time(a.heartbeat_at) && time(a.lease_until) && time(a.finished_at)
            && (ATTEMPT_OUTCOMES as readonly unknown[]).includes(a.outcome)
            && (a.failure_kind === null || (FAILURE_KINDS as readonly unknown[]).includes(a.failure_kind))
            // only an attempt still going has no end
            && ((a.outcome === "running" || a.outcome === "cancel_requested") === (a.finished_at === null)));
          return { epoch: a.epoch as number, started_at: a.started_at as string | null, heartbeat_at: a.heartbeat_at as string | null,
            finished_at: a.finished_at as string | null, outcome: a.outcome as HostedJobAttempt["outcome"], failure_kind: a.failure_kind as HostedJobAttempt["failure_kind"] };
        });
        need(items.every((a, i) => i === 0 || a.epoch > items[i - 1].epoch) && items.slice(0, -1).every(a => a.finished_at !== null));
        const latest = items.at(-1)?.failure_kind ?? null, c = x.conflict;
        // a conflict object belongs only to a latest attempt that ended in conflict
        need(c === undefined || c === null || (latest === "conflict" && obj(c) && id(c.project_id)
          && Number.isSafeInteger(c.current_revision) && (c.current_revision as number) >= 0));
        const conflict = obj(c) ? { project_id: c.project_id as string, current_revision: c.current_revision as number } : null;
        return { retry_of_job_id: x.retry_of_job_id as string | null, items, conflict };
      }, undefined, undefined, undefined, extraSignal);
    },
    /** POST jobs/{id}/cancel: answers the job as it now is. cancel_requested has not stopped yet. */
    cancelJob: (workspace: string, caps: HostedCapabilities, jobId: string, extraSignal?: AbortSignal) => {
      feature(workspace, caps, "jobs.cancel"); input(id(jobId));
      return request(`${base(workspace)}/jobs/${jobId}/cancel`, "POST", (value, status) => {
        need(status === 200); return readJob(value, workspace, jobId);
      }, "{}", "application/json", undefined, extraSignal);
    },
    /** POST jobs/{id}/retry: a new, linked job with its own receipt; the same key replays it. */
    retryJob: (workspace: string, caps: HostedCapabilities, jobId: string, requestKey: string, extraSignal?: AbortSignal) => {
      feature(workspace, caps, "jobs.retry"); input(id(jobId));
      return request(`${base(workspace)}/jobs/${jobId}/retry`, "POST", (value, status) => {
        need(status === 202); const receipt = readJobReceipt(value, workspace);
        if (receipt.job_id === jobId) throw new ProductError("response_out_of_scope", "A retry must answer with a new job.");
        return receipt;
      }, "{}", "application/json", key(requestKey), extraSignal);
    },
    /** GET results/{id}/graph: one page of the lineage graph. The cursor is opaque and belongs to this result. */
    loadGraphPage: (workspace: string, caps: HostedCapabilities, scope: HostedResultScope,
                    page: { cursor: string | null; limit?: number }, extraSignal?: AbortSignal) => {
      paged(workspace, caps);
      const limit = page.limit ?? 50;
      input(matches(RESULT, scope.resultId) && id(scope.projectId) && Number.isSafeInteger(limit) && limit >= 1 && limit <= 100
        && (page.cursor === null || (page.cursor.length >= 1 && page.cursor.length <= 4096)));
      const query = new URLSearchParams({ limit: String(limit), ...(page.cursor ? { cursor: page.cursor } : {}) });
      return request(`${base(workspace)}/results/${scope.resultId}/graph?${query}`, "GET", (value, status): HostedGraphPage => {
        need(status === 200); const x = envelope(value, workspace), g = x.graph;
        if (x.result_id !== scope.resultId || x.project_id !== scope.projectId) throw outOfScope("result or project");
        const next = x.next_cursor ?? null, truncated = x.truncated ?? false;
        need(obj(g) && Array.isArray(g.nodes) && Array.isArray(g.edges) && typeof truncated === "boolean"
          && (next === null || (typeof next === "string" && next.length >= 1 && next.length <= 4096))
          // a page holds at most `limit` edges, and its nodes are at most `limit` more plus their endpoints
          && g.edges.length <= limit && g.nodes.length <= limit + 2 * g.edges.length);
        const nodes = (g.nodes as unknown[]).map(n => {
          need(obj(n) && id(n.id) && typeof n.kind === "string" && n.kind.length >= 1 && n.kind.length <= 64 && id(n.source_id) && n.visibility === "diagnosis");
          return { id: n.id, kind: n.kind, source_id: n.source_id };
        });
        const edges = (g.edges as unknown[]).map(e => {
          need(obj(e) && id(e.id) && typeof e.kind === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(e.kind)
            && id(e.source) && id(e.target) && e.visibility === "diagnosis");
          return { id: e.id, kind: e.kind, source: e.source, target: e.target };
        });
        need(new Set(nodes.map(n => n.id)).size === nodes.length && new Set(edges.map(e => e.id)).size === edges.length);
        return { nodes, edges, next_cursor: next as string | null, truncated: truncated as boolean };
      }, undefined, undefined, undefined, extraSignal);
    },
    /** GET results/{id}/evidence/{evidence}?claim_id=: one item, with how it relates to that claim. */
    loadClaimEvidence: (workspace: string, caps: HostedCapabilities, scope: HostedResultScope, evidenceId: string, claimId: string, extraSignal?: AbortSignal) => {
      paged(workspace, caps); input(matches(RESULT, scope.resultId) && id(scope.projectId) && id(evidenceId) && id(claimId));
      return request(`${base(workspace)}/results/${scope.resultId}/evidence/${evidenceId}?${new URLSearchParams({ claim_id: claimId })}`, "GET",
        (value, status): HostedEvidenceItem => {
          need(status === 200); const x = envelope(value, workspace), e = x.evidence;
          if (x.result_id !== scope.resultId || x.project_id !== scope.projectId || x.evidence_id !== evidenceId || x.claim_id !== claimId) {
            throw outOfScope("result, project, evidence or claim");
          }
          need((x.relation === "citation" || x.relation === "counterevidence") && obj(e) && e.id === evidenceId
            && typeof e.kind === "string" && typeof e.title === "string" && typeof e.text === "string" && typeof e.redacted === "boolean");
          return { id: evidenceId, kind: e.kind, title: e.title, text: e.text, redacted: e.redacted, relation: x.relation };
        }, undefined, undefined, undefined, extraSignal);
    },
    /** GET results/{id}: ResultView v1/v2. Its view is checked like any ProductView, then held to its scope. */
    loadResult: (workspace: string, resultId: string, expected: { projectId: string; snapshotId?: string }, extraSignal?: AbortSignal) => {
      authenticated();
      input(id(resultId) && id(expected.projectId) && (expected.snapshotId === undefined || id(expected.snapshotId)));
      return request(`${base(workspace)}/results/${resultId}`, "GET", (value, status): ProductView => {
        need(status === 200); const x = envelope(value, workspace);
        if (x.result_id !== resultId || x.project_id !== expected.projectId) throw outOfScope("result or project");
        const view = checkProductView(x.view);
        if (view.demo_only || view.result.id !== resultId || view.project.id !== expected.projectId
          || (expected.snapshotId !== undefined && view.snapshot.id !== expected.snapshotId)) throw outOfScope("result, project or snapshot");
        return view;
      }, undefined, undefined, undefined, extraSignal);
    },
    /** POST exports answers 201 with a descriptor, not a job. Only formats the workspace advertises are asked for. */
    createExport: (workspace: string, caps: HostedCapabilities, resultId: string, format: ReportFormat,
                   requestKey?: string, extraSignal?: AbortSignal) => {
      capability(workspace, caps, "export");
      input(matches(RESULT, resultId) && exportFormat(format) && caps.supported_export_formats.includes(format));
      return request(`${base(workspace)}/results/${resultId}/exports`, "POST", (value, status): HostedExport => {
        need(status === 201); const x = envelope(value, workspace);
        need(id(x.project_id) && matches(RESULT, x.result_id) && typeof x.export_id === "string"
          && exportFormat(x.format) && Number.isSafeInteger(x.byte_size)
          && (x.byte_size as number) >= 0 && (x.byte_size as number) <= EXPORT_BYTES && hash(x.sha256)
          && typeof x.download_url === "string");
        if (x.result_id !== resultId || x.format !== format) throw outOfScope("result or format");
        const legacy = `ex_${format}_${resultId}`;
        need(x.export_id === legacy || new RegExp(`^${legacy}_[0-9a-f]{32}$`).test(x.export_id));
        const expires = x.expires_at ?? null;
        need(expires === null || (typeof expires === "string" && isInstant(expires)));
        need((x.export_id !== legacy) === (expires !== null));
        if (x.download_url !== `${base(workspace)}/exports/${x.export_id}/download`) throw outOfScope("download address");
        return { workspace_id: workspace, project_id: x.project_id, result_id: resultId, export_id: x.export_id, format,
          byte_size: x.byte_size as number, sha256: x.sha256, download_url: x.download_url, expires_at: expires as string | null };
      }, JSON.stringify({ format }), "application/json", requestKey === undefined ? undefined : key(requestKey), extraSignal);
    },
    /** The described bytes, from this workspace's own download route: media type, size and SHA-256 must all match. */
    downloadExport: async (workspace: string, descriptor: HostedExport, extraSignal?: AbortSignal): Promise<Uint8Array> => {
      authenticated();
      input(obj(descriptor) && descriptor.workspace_id === workspace && id(descriptor.export_id)
        && descriptor.download_url === `${base(workspace)}/exports/${descriptor.export_id}/download`
        && Number.isSafeInteger(descriptor.byte_size) && descriptor.byte_size >= 0 && descriptor.byte_size <= EXPORT_BYTES && hash(descriptor.sha256)
        && exportFormat(descriptor.format));
      if (descriptor.expires_at !== null && !(Date.parse(descriptor.expires_at) > Date.now())) {
        throw new ProductError("export_expired", "This export expired before it was downloaded.");
      }
      if (!globalThis.crypto?.subtle) throw new ProductError("feature_unavailable", "Secure file hashing is unavailable in this browser.");
      const bytes = await request(descriptor.download_url, "GET", (value, status) => {
        need(status === 200 && value instanceof Uint8Array); return value as Uint8Array<ArrayBuffer>;
      }, undefined, undefined, undefined, extraSignal, undefined,
      { raw: { media: EXPORT_MEDIA[descriptor.format], maximum: descriptor.byte_size } });
      need(bytes.byteLength === descriptor.byte_size);
      const digest = await crypto.subtle.digest("SHA-256", bytes); check(false, extraSignal);
      need(Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("") === descriptor.sha256);
      return bytes;
    },
    /** DELETE answers 200 with a plain CAS receipt: the next revision and a tombstone, never a job or a purge. */
    deleteProject: (workspace: string, caps: HostedCapabilities, projectId: string, expectedRevision: number, extraSignal?: AbortSignal) => {
      capability(workspace, caps, "delete");
      input(id(projectId) && revision(expectedRevision));
      return request(`${base(workspace)}/projects/${projectId}`, "DELETE", (value, status): HostedDeletedProject => {
        need(status === 200); const x = envelope(value, workspace);
        need(id(x.project_id) && Number.isSafeInteger(x.revision) && x.revision === expectedRevision + 1
          && matches(TOMBSTONE, x.tombstone_id) && x.status === "tombstoned" && x.retention === "retained");
        if (x.project_id !== projectId) throw outOfScope("project");
        return { workspace_id: workspace, project_id: projectId, revision: x.revision as number, tombstone_id: x.tombstone_id,
          status: "tombstoned", retention: "retained" };
      }, "{}", "application/json", undefined, extraSignal, undefined, { ifMatch: expectedRevision });
    },
    pollJob: async (workspace: string, jobId: string, caps: HostedCapabilities,
                    poll: { signal?: AbortSignal; timeoutMs?: number } = {}): Promise<HostedJob> => {
      authenticated(); input(id(jobId) && caps.workspace_id === workspace && Number.isSafeInteger(caps.job_poll_min_ms) && caps.job_poll_min_ms > 0);
      const budget = poll.timeoutMs ?? 120_000;
      input(Number.isSafeInteger(budget) && budget > 0 && budget <= 120_000);
      const pollingKey = `${workspace}/${jobId}`;
      if (polling.has(pollingKey)) throw new ProductError("invalid_request", "This job is already being watched.");
      polling.add(pollingKey);
      let deadline = performance.now() + budget;
      const terminal = (job: HostedJob) => ["succeeded", "failed", "cancelled", "expired"].includes(job.status);
      // K15 (jobs.polling_v1): the update route, its cursor and its pacing; otherwise the job itself at the workspace minimum
      const updates = caps.features?.["jobs.polling_v1"] === true;
      let cursor: string | null = null, held: HostedJob | null = null, heldRevision = 0, failures = 0;
      try {
        while (true) {
          deadline += await shown(poll.signal);
          check(false, poll.signal);
          const remaining = deadline - performance.now();
          if (remaining <= 0) throw new ProductError("deadline_exceeded", "The job is still pending. Check its state later.");
          let pause: number;
          if (!updates) {
            const job = await loadJob(workspace, jobId, poll.signal, remaining);
            if (terminal(job)) return job;
            pause = caps.job_poll_min_ms;
          } else {
            let answered: { status: number; retryAfterMs: number | null } | null = null;
            const sentAt = performance.now(), asked = cursor;
            try {
              const update = await loadJobUpdate(workspace, jobId, cursor, poll.signal, remaining, (status, retryAfterMs) => { answered = { status, retryAfterMs }; });
              // an answer that arrives after its own authorization window says nothing current
              if (performance.now() - sentAt > update.valid_for_ms) throw new ProductError("unavailable", "The job update arrived too late to use.");
              failures = 0; cursor = update.cursor;
              if (update.job && (update.disposition === "resync" || update.revision >= heldRevision)) { held = update.job; heldRevision = update.revision; }
              // nothing held to keep: ask once for a fresh snapshot; an answer to that must carry the job
              if (!held) { need(asked !== null); cursor = null; continue; }
              if (terminal(held)) return held;
              // a stale update that was not taken may carry a terminal null; the job held is still pending
              pause = Math.max(update.poll_after_ms ?? 2000, caps.job_poll_min_ms);
            } catch (error) {
              if (error instanceof HostedRequestDiscarded) throw error;
              const code = error instanceof ProductError ? error.code : null;
              const status = (answered as { status: number } | null)?.status ?? null;
              if ((code === "cursor_invalid" || code === "cursor_expired") && cursor !== null) {
                // a continuation the service no longer honours: drop it and read one fresh snapshot
                cursor = null; heldRevision = 0; continue;
              }
              if (!(code === "unavailable" || code === "deadline_exceeded" || code === "service_unavailable" || status === 429 || status === 503)) throw error;
              // equal-jitter backoff, 1 s doubling to 30 s, never sooner than the service asked
              failures += 1;
              const ceiling = Math.min(1000 * 2 ** (failures - 1), 30_000);
              pause = Math.max((answered as { retryAfterMs: number | null } | null)?.retryAfterMs ?? 0, ceiling / 2) + Math.random() * ceiling / 2;
            }
          }
          const resumeAt = Math.min(deadline, performance.now() + pause);
          await new Promise<void>((resolve, reject) => {
            const stop = () => { clearTimeout(timer); cleanup(); reject(new HostedRequestDiscarded()); };
            const cleanup = () => { signal?.removeEventListener("abort", stop); poll.signal?.removeEventListener("abort", stop); };
            let timer: ReturnType<typeof setTimeout>;
            const ready = () => {
              const wait = resumeAt - performance.now();
              if (wait > 0) { timer = setTimeout(ready, Math.ceil(wait)); return; }
              cleanup(); resolve();
            };
            timer = setTimeout(ready, Math.max(0, Math.ceil(resumeAt - performance.now())));
            signal?.addEventListener("abort", stop, { once: true }); poll.signal?.addEventListener("abort", stop, { once: true });
            if (signal?.aborted || poll.signal?.aborted) stop();
          });
        }
      } finally { polling.delete(pollingKey); }
    },
    logout: () => {
      authenticated();
      return request("/v1/auth/logout", "POST", (value, status): string | null => {
        if (status === 204) return null;
        need(status === 200 && obj(value) && (value.logout_url === null || typeof value.logout_url === "string"));
        if (value.logout_url === null) return null;
        need(value.logout_url.length <= 8192 && !/[\u0000-\u0020\u007f\\]/.test(value.logout_url));
        const url = new URL(value.logout_url);
        need(idpOrigin !== undefined && url.origin === idpOrigin && url.protocol === "https:" && !url.username && !url.password && !url.hash);
        return value.logout_url;
      }, "{}", "application/json");
    },
  };
}
