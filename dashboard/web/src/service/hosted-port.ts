/* ProductPort for the implemented hosted slice; no bridge or demo fallback.

   Refresh, follow-up, export and delete are offered only when the workspace advertises the binding's
   feature and the role holds its actions (bindingOff). Every CAS uses the PROJECT revision read from the
   server, never a membership revision; 412/428 drop it so the next attempt reads it again, and the
   person reviews before retrying. An intent keeps its key, CAS revision, accepted job and uploaded source
   until a trusted result or a definite refusal settles it, so a retry after a lost answer, a 5xx or a
   failed read reconciles with what the server may already have done instead of starting something new.
   A replacement ZIP is the same intent only when its SHA-256 matches; other bytes start a new upload. */
import type { ProductView } from "../types.ts";
import type { createHostedClient, HostedUploadResume } from "./hosted-client.ts";
import type { HostedCapabilities, HostedSession } from "./hosted.ts";
import { HostedRequestDiscarded, ProductError, type ProductPort, type RefreshResult, type TombstoneReceipt } from "./port.ts";

export interface HostedPortContext {
  session: HostedSession;
  workspace: string;
  caps: HostedCapabilities;
  client: ReturnType<typeof createHostedClient>;
  isCurrent: () => boolean;
  initialView?: ProductView;
  initialJobId?: string;
  initialProjectId?: string;
  initialSnapshotId?: string;
  /** told which result is on screen, or none, so its own read is what keeps confirming access (K15) */
  onShown?: (scope: { resultId: string; projectId: string; snapshotId: string } | null) => void;
}
export type HostedBinding = "refresh" | "follow_up" | "export" | "delete";
export interface HostedProductPort extends ProductPort {
  setContext(context: HostedPortContext): void;
  clearContext(): void;
  context(): HostedPortContext | null;
  capabilities(): HostedCapabilities | null;
  latestJobId(): string | null;
  /** Why a binding is not offered right now, or null when it is. */
  bindingOff(binding: HostedBinding): string | null;
  /** Uploads the replacement ZIP for a refresh with the current uploader; the server's source ID comes back. */
  uploadReplacement(project_id: string, file: Blob, signal?: AbortSignal): Promise<{ source_id: string }>;
}

/* Feature names and actions as the producer's capabilities set them (hosted/upload_runtime.py, 2026-11-11). */
const BINDINGS: Readonly<Record<HostedBinding, { feature: string; actions: readonly string[] }>> = {
  refresh: { feature: "projects.refresh", actions: ["import", "analyze"] },
  follow_up: { feature: "projects.follow_up", actions: ["analyze"] },
  export: { feature: "exports.download", actions: ["export"] },
  delete: { feature: "projects.logical_delete", actions: ["delete"] },
};
export function bindingOff(caps: HostedCapabilities | null, binding: HostedBinding): string | null {
  if (!caps) return "Open a workspace first.";
  const { feature, actions } = BINDINGS[binding];
  if (caps.features?.[feature] !== true) return "This workspace does not offer it.";
  if (!actions.every(action => caps.allowed_actions.includes(action))) return "Your role in this workspace does not allow it.";
  return null;
}

type Pointer = { job?: string; project: string; snapshot: string };
type Intent = { fingerprint: string; key: string; resume?: HostedUploadResume; source?: string; expected?: number; job?: string };
const id = (value: unknown): value is string => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
const noResult = () => new ProductError("no_result", "Open a completed hosted result first.");
const outOfScope = () => new ProductError("response_out_of_scope", "The hosted answer belongs to another result, project or snapshot.");
const invalid = () => new ProductError("response_invalid", "The hosted service sent an answer this app cannot trust.");
const unsettled = () => new ProductError("outcome_unknown", "The change could not be confirmed. Check its state before retrying with the same request key.");
const badRequest = (message: string) => new ProductError("invalid_request", message);
const unsupported = async (): Promise<never> => {
  throw new ProductError("feature_unavailable", "This operation is not available in the current hosted slice.");
};
const FINAL_UPLOAD = new Set(["upload_expired", "upload_cancelled", "upload_conflict", "upload_part_conflict",
  "upload_integrity_failed", "upload_too_large", "upload_quota_exceeded", "idempotency_conflict", "invalid_request", "source_invalid"]);
const casFailure = (error: unknown) => error instanceof ProductError
  && (error.code === "revision_conflict" || error.code === "precondition_required");
const unknownOutcome = (error: unknown) => (error instanceof ProductError && error.code === "outcome_unknown")
  || (error instanceof HostedRequestDiscarded && error.outcome_unknown);

export function createHostedPort(): HostedProductPort {
  let selected: HostedPortContext | null = null;
  let actor: string | null = null, contextId: string | null = null;
  let generation = 0, latestJob: string | null = null, currentView: ProductView | null = null;
  const pointers = new Map<string, Pointer>();
  const revisions = new Map<string, number>();
  const intents = new Map<string, Intent>();
  const clearContext = () => {
    generation++; selected = null; actor = null; contextId = null;
    latestJob = null; currentView = null; pointers.clear(); revisions.clear(); intents.clear();
  };
  const fence = () => {
    const context = selected, epoch = generation;
    const check = () => {
      if (!context || selected !== context || generation !== epoch || !context.isCurrent()
        || context.session.user_id !== actor || context.session.context_id !== contextId
        || context.caps.workspace_id !== context.workspace) throw new HostedRequestDiscarded();
    };
    check();
    if (!context) throw noResult();
    const wait = async <T>(answer: Promise<T>): Promise<T> => {
      try { return await answer; } finally { check(); }
    };
    return { context, check, wait };
  };
  const gate = (context: HostedPortContext, binding: HostedBinding) => {
    const off = bindingOff(context.caps, binding);
    if (off) throw new ProductError("feature_unavailable", off);
  };
  const newKey = () => {
    if (typeof globalThis.crypto?.randomUUID !== "function") throw new ProductError("feature_unavailable", "Secure request keys are unavailable.");
    return globalThis.crypto.randomUUID();
  };
  /** The same intent keeps its key until the server has settled it; a changed intent gets a new one. */
  const intent = (name: string, fingerprint: string): Intent => {
    const held = intents.get(name);
    if (held && held.fingerprint === fingerprint) return held;
    const fresh = { fingerprint, key: newKey() };
    intents.set(name, fresh);
    return fresh;
  };
  const mutate = async <T>(names: string[], projectId: string | null, send: () => Promise<T>): Promise<T> => {
    try { return await send(); }
    catch (error) {
      if (!unknownOutcome(error)) for (const name of names) intents.delete(name);
      if (projectId !== null && casFailure(error)) revisions.delete(projectId);
      throw error;
    }
  };
  const projectRevision = async (context: HostedPortContext, wait: <T>(answer: Promise<T>) => Promise<T>, projectId: string) => {
    const known = revisions.get(projectId);
    if (known !== undefined) return known;
    const value = await wait(context.client.loadProjectRevision(context.workspace, context.caps, projectId));
    revisions.set(projectId, value);
    return value;
  };
  const accepted = (context: HostedPortContext, receipt: { workspace_id: string; job_id: string }) => {
    if (receipt.workspace_id !== context.workspace || !id(receipt.job_id)) throw outOfScope();
    return receipt.job_id;
  };
  const terminal = async (context: HostedPortContext, wait: <T>(answer: Promise<T>) => Promise<T>, jobId: string) => {
    const job = await wait(context.client.pollJob(context.workspace, jobId, context.caps));
    if (job.workspace_id !== context.workspace || job.job_id !== jobId) throw outOfScope();
    return job.status;
  };
  /* jobs.conflict_v1: a job that ended because its project changed after it was accepted says so in its own attempt
     history. Nothing is retried; the revision held for that project is dropped, so the next explicit action reads a
     fresh one and sends its own request key. Without the feature, or when the history cannot be read, the job's own
     failure stands. */
  const ended = async (context: HostedPortContext, wait: <T>(answer: Promise<T>) => Promise<T>, jobId: string,
                       projectId: string, failure: ProductError, conflictCode = "project_changed") => {
    if (context.caps.features?.["jobs.conflict_v1"] !== true) return failure;
    let history;
    try { history = await wait(context.client.loadJobAttempts(context.workspace, context.caps, jobId)); }
    catch (error) { if (error instanceof HostedRequestDiscarded) throw error; return failure; }
    if (history.items.at(-1)?.failure_kind !== "conflict" || (history.conflict && history.conflict.project_id !== projectId)) return failure;
    revisions.delete(projectId);
    // the revision is as the service read it now: shown for orientation, never sent as a CAS
    return new ProductError(conflictCode, history.conflict ? `The project is now at revision ${history.conflict.current_revision}.` : "");
  };
  /* The view check travels with the client, so the contract and its Unicode tables stay out of the main chunk. */
  const storedView = (context: HostedPortContext, value: unknown, expected?: { result?: string; project: string; snapshot: string }) => {
    const view = context.client.checkView(value);
    if (view.demo_only || !id(view.project.id) || !id(view.snapshot.id) || !id(view.result.id)
      || (expected && (view.project.id !== expected.project || view.snapshot.id !== expected.snapshot
      || (expected.result !== undefined && view.result.id !== expected.result)))) throw outOfScope();
    return view;
  };
  const remember = (view: ProductView, job?: string) => {
    pointers.delete(view.result.id);
    pointers.set(view.result.id, { ...(job === undefined ? {} : { job }), project: view.project.id, snapshot: view.snapshot.id });
    while (pointers.size > 16) pointers.delete(pointers.keys().next().value!);
    currentView = view;
    selected?.onShown?.({ resultId: view.result.id, projectId: view.project.id, snapshotId: view.snapshot.id });
  };
  return {
    kind: "live",
    clearContext,
    setContext: context => {
      clearContext();
      if (!id(context.workspace) || context.caps.workspace_id !== context.workspace
        || !id(context.session.user_id) || !id(context.session.context_id) || typeof context.isCurrent !== "function"
        || (context.initialJobId !== undefined && !id(context.initialJobId))
        || (context.initialProjectId !== undefined && !id(context.initialProjectId))
        || (context.initialSnapshotId !== undefined && !id(context.initialSnapshotId))) throw invalid();
      let view: ProductView | null = null;
      if (context.initialView !== undefined) {
        view = storedView(context, context.initialView);
        if ((context.initialProjectId !== undefined && view.project.id !== context.initialProjectId)
          || (context.initialSnapshotId !== undefined && view.snapshot.id !== context.initialSnapshotId)) throw outOfScope();
      }
      selected = { ...context,
        initialProjectId: context.initialProjectId ?? view?.project.id,
        initialSnapshotId: context.initialSnapshotId ?? view?.snapshot.id };
      actor = context.session.user_id; contextId = context.session.context_id;
      latestJob = context.initialJobId ?? null;
      currentView = view;
      if (view && context.initialJobId) remember(view, context.initialJobId);
    },
    context: () => selected,
    capabilities: () => selected?.caps ?? null,
    latestJobId: () => latestJob,
    bindingOff: binding => bindingOff(selected?.caps ?? null, binding),
    demo_view: async () => {
      const { context, check } = fence();
      if (!currentView) throw noResult();
      const view = storedView(context, currentView); check(); return view;
    },
    view: async resultId => {
      const { context, check, wait } = fence();
      if (!id(resultId)) throw noResult();
      // a result is immutable: the one on screen is not read again just because a screen opened it
      if (currentView?.result.id === resultId) { const view = storedView(context, currentView); check(); return view; }
      const held = pointers.get(resultId);
      const pointer = held ?? (context.initialJobId && context.initialProjectId && context.initialSnapshotId
        ? { job: context.initialJobId, project: context.initialProjectId, snapshot: context.initialSnapshotId } : undefined);
      if (!pointer) throw noResult();
      // A follow-up child has no analysis output of its own; a reopened link may name either kind of job.
      if (pointer.job === undefined || (!held && context.caps.features?.["catalog.read"] === true)) {
        const view = storedView(context, await wait(context.client.loadResult(context.workspace, resultId,
          { projectId: pointer.project, snapshotId: pointer.snapshot })), { result: resultId, project: pointer.project, snapshot: pointer.snapshot });
        check(); remember(view); if (pointer.job) latestJob = pointer.job; return view;
      }
      const job = pointer.job;
      const answer = await wait(context.client.loadOutput(context.workspace, job,
        { operation: "analyze", projectId: pointer.project, snapshotId: pointer.snapshot }));
      if (answer.workspace_id !== context.workspace || answer.job_id !== job || answer.operation !== "analyze") throw outOfScope();
      const view = storedView(context, answer.result, { result: resultId, project: pointer.project, snapshot: pointer.snapshot });
      check(); remember(view, job); latestJob = job; return view;
    },
    analyze_observations: async (projectId, snapshotId, question) => {
      const { context, check, wait } = fence();
      if (!id(projectId) || !id(snapshotId)) throw new ProductError("invalid_request", "Select a valid project and snapshot.");
      const receipt = await wait(context.client.submitJob(context.workspace, context.caps,
        { operation: "analyze", snapshot_id: snapshotId, question }, newKey()));
      if (receipt.workspace_id !== context.workspace || !id(receipt.job_id)) throw outOfScope();
      latestJob = receipt.job_id;
      const job = await wait(context.client.pollJob(context.workspace, receipt.job_id, context.caps));
      if (job.workspace_id !== context.workspace || job.job_id !== receipt.job_id) throw outOfScope();
      if (job.status !== "succeeded") throw new ProductError("analysis_failed", "The hosted analysis job did not complete.");
      const answer = await wait(context.client.loadOutput(context.workspace, receipt.job_id,
        { operation: "analyze", projectId, snapshotId }));
      if (answer.workspace_id !== context.workspace || answer.job_id !== receipt.job_id || answer.operation !== "analyze") throw outOfScope();
      const view = storedView(context, answer.result, { project: projectId, snapshot: snapshotId });
      check(); remember(view, receipt.job_id); return view;
    },
    uploadReplacement: async (projectId, file, signal) => {
      const { context, check, wait } = fence();
      gate(context, "refresh");
      if (!id(projectId) || !(file instanceof Blob)) throw badRequest("Choose the replacement ZIP for this project.");
      if (file.size === 0 || file.size > context.caps.upload_bundle_bytes) throw badRequest("Choose a ZIP within this workspace's upload limit.");
      if (!globalThis.crypto?.subtle) throw new ProductError("feature_unavailable", "Secure file hashing is unavailable in this browser.");
      // Only the same bytes are the same upload: name, type, size and date can all match a different ZIP.
      const digest = await wait(file.arrayBuffer().then(bytes => crypto.subtle.digest("SHA-256", bytes)));
      if (signal?.aborted) throw new HostedRequestDiscarded();
      const name = `upload:${projectId}`;
      const held = intent(name, Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join(""));
      // the same content keeps its source until its refresh settles, so choosing it again retries that refresh
      if (held.source) return { source_id: held.source };
      held.resume ??= {};
      let source;
      try {
        source = await wait(context.client.uploadZip(context.workspace, context.caps, file, held.key, { resume: held.resume, signal }));
      } catch (error) {
        // the same upload resumes from the server's own progress, unless the server ended or refused it
        if (error instanceof ProductError && FINAL_UPLOAD.has(error.code)) intents.delete(name);
        throw error;
      }
      check();
      if (source.workspace_id !== context.workspace || !id(source.source_id)) { intents.delete(name); throw outOfScope(); }
      held.source = source.source_id;
      return { source_id: source.source_id };
    },
    refresh: async (projectId, replacement) => {
      const { context, check, wait } = fence();
      gate(context, "refresh");
      if (!id(projectId)) throw badRequest("Select a valid project.");
      if (!replacement || !id(replacement.source_id)) throw badRequest("Choose the replacement ZIP before refreshing.");
      const name = `refresh:${projectId}`, upload = `upload:${projectId}`;
      const held = intent(name, replacement.source_id);
      // A retry keeps the first attempt's CAS and accepted job: it reads that job again, never submits another.
      const expected = held.expected ??= await projectRevision(context, wait, projectId);
      const jobId = held.job ??= accepted(context, await mutate([name, upload], projectId, () => wait(context.client.refreshProject(
        context.workspace, context.caps, projectId, replacement.source_id, expected, held.key))));
      const status = await terminal(context, wait, jobId);
      if (status !== "succeeded") {
        intents.delete(name); intents.delete(upload);
        // a refresh that met a newer project says so as a stale refresh: there is no question to keep
        throw await ended(context, wait, jobId, projectId, new ProductError("refresh_failed", "The hosted refresh did not complete."), "refresh_stale");
      }
      const answer = await wait(context.client.loadOutput(context.workspace, jobId, { operation: "refresh", projectId }));
      if (answer.workspace_id !== context.workspace || answer.job_id !== jobId || answer.operation !== "refresh") throw outOfScope();
      const result = answer.result;
      check();
      intents.delete(name); intents.delete(upload);
      revisions.set(projectId, result.revision);
      const outcome: RefreshResult = { project_id: result.project_id, snapshot_id: result.snapshot_id, status: result.status,
        previous_snapshot_id: result.previous_snapshot_id, revision: result.revision };
      return outcome;
    },
    follow_up: async (resultId, selectionKind, selectionId, question) => {
      const { context, check, wait } = fence();
      gate(context, "follow_up");
      const parent = currentView;
      if (!parent || !id(resultId) || parent.result.id !== resultId) throw noResult();
      if ((selectionKind !== "claim" && selectionKind !== "node") || !id(selectionId)
        || !(selectionKind === "claim" ? parent.claims.some(claim => claim.id === selectionId)
          : parent.graph.nodes.some(node => node.id === selectionId))) {
        throw new ProductError("selection_not_available", "Select a claim or node of the open result.");
      }
      if (typeof question !== "string" || question.trim() !== question || !question
        || [...question].length > Math.min(4000, context.caps.question_chars)) {
        throw new ProductError("invalid_question", "Write a question within this workspace's limit.");
      }
      const projectId = parent.project.id, snapshotId = parent.snapshot.id;
      const name = `follow_up:${resultId}`;
      const held = intent(name, JSON.stringify([selectionKind, selectionId, question]));
      const expected = held.expected ??= await projectRevision(context, wait, projectId);
      const jobId = held.job ??= accepted(context, await mutate([name], projectId, () => wait(context.client.submitFollowUp(
        context.workspace, context.caps, resultId, { kind: selectionKind, id: selectionId }, question, expected, held.key))));
      const status = await terminal(context, wait, jobId);
      if (status !== "succeeded") {
        intents.delete(name);
        throw await ended(context, wait, jobId, projectId, new ProductError("follow_up_failed", "The hosted follow-up did not complete."));
      }
      const answer = await wait(context.client.loadOutput(context.workspace, jobId,
        { operation: "follow_up", projectId, parentResultId: resultId, snapshotId }));
      if (answer.workspace_id !== context.workspace || answer.job_id !== jobId || answer.operation !== "follow_up") throw outOfScope();
      const summary = answer.result;
      // The summary is not a view: the child is read from its own result address.
      const view = storedView(context, await wait(context.client.loadResult(context.workspace, summary.result_id,
        { projectId, snapshotId: summary.snapshot_id })), { result: summary.result_id, project: projectId, snapshot: snapshotId });
      // a follow-up child keeps its parent's turns under the parent's own result_id
      if (!view.conversation.turns.some(turn => turn.result_id === resultId)) throw outOfScope();
      check();
      intents.delete(name);
      revisions.set(projectId, summary.revision);
      remember(view); latestJob = jobId; return view;
    },
    export_report: async (resultId, format) => {
      const { context, check, wait } = fence();
      gate(context, "export");
      if (!context.caps.supported_export_formats.includes(format)) {
        throw new ProductError("feature_unavailable", "This format is not offered in this workspace.");
      }
      const pointer = pointers.get(resultId) ?? (currentView?.result.id === resultId
        ? { project: currentView.project.id, snapshot: currentView.snapshot.id } : undefined);
      if (!id(resultId) || !pointer) throw noResult();
      const name = `export:${resultId}:${format}`;
      const held = intent(name, "");
      const descriptor = await mutate([name], null, () => wait(context.client.createExport(context.workspace, context.caps,
        resultId, format, held.key)));
      // An export may exist but its descriptor names another project: keep the key, so a retry replays it.
      if (descriptor.project_id !== pointer.project) throw unsettled();
      let bytes;
      try { bytes = await wait(context.client.downloadExport(context.workspace, descriptor)); }
      catch (error) {
        // a gone or expired export ends this intent; the next press asks for a new one
        if (error instanceof ProductError && (error.code === "export_expired" || error.code === "not_found")) intents.delete(name);
        throw error;
      }
      check(); intents.delete(name); return bytes;
    },
    delete_project: async projectId => {
      const { context, check, wait } = fence();
      gate(context, "delete");
      if (!id(projectId)) throw badRequest("Select a valid project.");
      const expected = await projectRevision(context, wait, projectId);
      // A plain CAS receipt: an unknown outcome keeps the same If-Match, which the server replays.
      const deleted = await mutate([], projectId,() => wait(context.client.deleteProject(context.workspace, context.caps, projectId, expected)));
      check();
      revisions.delete(projectId);
      for (const [result, pointer] of pointers) if (pointer.project === projectId) pointers.delete(result);
      // after the delete only its receipt is on screen; the result's own read would now be refused
      if (currentView?.project.id === projectId) { currentView = null; context.onShown?.(null); }
      const receipt: TombstoneReceipt = { project_id: deleted.project_id, status: deleted.status, retention: deleted.retention,
        revision: deleted.revision, tombstone_id: deleted.tombstone_id };
      return receipt;
    },
    preview_import: unsupported,
    confirm_import: unsupported,
    analyze_mock: unsupported,
  };
}
