/* Authoritative session/workspace bootstrap; every context change fences older requests. */
import { createHostedClient, HostedRequestDiscarded, type HostedResultScope } from "./hosted-client.ts";
import type { HostedCapabilities, HostedSession } from "./hosted.ts";
import { ProductError } from "./port.ts";
import { createSessionGuard, type SessionAnswer, type SessionGuard } from "./session.ts";

export interface HostedBoot {
  status: "loading" | "ready" | "signed_out" | "error";
  session: HostedSession | null;
  workspace: HostedCapabilities | null;
  error: ProductError | null;
  generation: number;
  /** the workspace being opened: nothing of a workspace is shown until its capabilities answer */
  opening: string | null;
}

/** The hosted authorization lease: nothing private stays shown longer than this after the request that confirmed it was sent. */
const LEASE_MS = 4_000;
const TERMINAL = new Set(["succeeded", "failed", "cancelled", "expired"]);

export function createHostedBootstrap(options: {
  fetch: typeof fetch;
  onClear: () => void;
  now?: () => number;
  tell?: Parameters<typeof createSessionGuard>[0]["tell"];
  idpOrigin?: string;
}) {
  const now = options.now ?? (() => performance.now());
  let state: HostedBoot = { status: "loading", session: null, workspace: null, error: null, generation: 0, opening: null };
  let alive = true, controller = new AbortController(), guard: SessionGuard | null = null;
  let client: ReturnType<typeof createHostedClient>;
  let checking: Promise<HostedSession> | null = null;
  let watchedJob: string | null = null;
  /** the result on screen: its own read must still be allowed, not just the session, workspace or a job */
  let watchedResult: HostedResultScope | null = null;
  /** when this tab's own successful read of the watched job was sent (its polling), so the check need not read it again */
  let jobReadAt: { job: string; at: number } | null = null;
  /** when this guard's previous check was sent */
  let lastAsked = -Infinity;
  let selecting = 0;
  const listeners = new Set<() => void>();
  const publish = (patch: Partial<HostedBoot>) => {
    state = { ...state, ...patch };
    listeners.forEach(listener => listener());
  };
  const current = (generation: number) => alive && generation === state.generation && !controller.signal.aborted;
  function clear(status: HostedBoot["status"] = "signed_out", error: ProductError | null = null) {
    controller.abort(); controller = new AbortController(); checking = null; guard = null; watchedJob = null; watchedResult = null; jobReadAt = null; selecting++;
    options.onClear();
    publish({ status, session: null, workspace: null, error, generation: state.generation + 1, opening: null });
  }
  function makeClient() {
    const generation = state.generation;
    // A 2xx GET of the watched job (or its updates) from this generation is a read of that job's own scope.
    const fetch: typeof options.fetch = async (url, init) => {
      const at = now(), response = await options.fetch(url, init);
      const job = watchedJob, workspace = state.workspace?.workspace_id;
      if (response.ok && (init?.method ?? "GET") === "GET" && job && workspace && current(generation)
        && (String(url) === `/v1/workspaces/${workspace}/jobs/${job}` || String(url).startsWith(`/v1/workspaces/${workspace}/jobs/${job}/updates`))) {
        jobReadAt = { job, at: Math.max(at, jobReadAt?.job === job ? jobReadAt.at : -Infinity) };
      }
      return response;
    };
    return createHostedClient({ fetch, session: state.session ?? undefined,
      signal: controller.signal, isCurrent: () => current(generation), idpOrigin: options.idpOrigin,
      // A session/capability answer alone cannot renew a result's authorization lease.
      onAnswer: (status, code) => { if (status >= 300) guard?.noteResponse(status, code); } });
  }
  function requireSession() {
    if (!state.session || !alive) throw new HostedRequestDiscarded();
    return state.session;
  }
  function applySession(next: HostedSession) {
    const previous = requireSession();
    if (previous.user_id !== next.user_id || previous.context_id !== next.context_id) {
      clear("signed_out", new ProductError("session_changed", "Another session is active. Reload its workspaces to continue."));
      throw new HostedRequestDiscarded();
    }
    if (previous.csrf_token !== next.csrf_token) {
      controller.abort(); controller = new AbortController(); selecting++;
      options.onClear();
      publish({ session: next, workspace: null, generation: state.generation + 1, opening: null });
      client = makeClient();
    } else if (JSON.stringify(previous) !== JSON.stringify(next)) publish({ session: next });
    return next;
  }
  async function refreshSession(): Promise<HostedSession> {
    requireSession();
    if (checking) return checking;
    const generation = state.generation;
    const request = client.loadSession().then(next => {
      if (!current(generation)) throw new HostedRequestDiscarded();
      return applySession(next);
    });
    checking = request;
    try { return await request; }
    finally { if (checking === request) checking = null; }
  }
  /* One read in this scope. 403/404 means the scope is gone: everything held for it is dropped. */
  async function scoped(session: HostedSession, generation: number, read: (probe: ReturnType<typeof createHostedClient>) => Promise<unknown>) {
    let denied = false;
    const probe = createHostedClient({ fetch: options.fetch, session, signal: controller.signal,
      isCurrent: () => current(generation), onAnswer: (status, code) => {
        denied = status === 403 || status === 404;
        if (status >= 300) guard?.noteResponse(status, code);
      } });
    try { await read(probe); }
    catch (error) {
      if (!denied || !current(generation)) throw error;
      controller.abort(); controller = new AbortController(); checking = null; watchedJob = null; watchedResult = null;
      options.onClear();
      state = { ...state, workspace: null, generation: state.generation + 1, opening: null,
        error: new ProductError("permission_denied", "This work is no longer available. Select a workspace to continue.") };
      client = makeClient(); publish({});
    }
  }
  /* The result on screen is confirmed by its own bounded header (Kiên, 13/11). A server without it is never
     read in full on a timer: the result is not shown there at all (HostedApp), and nothing is assumed here. */
  function readShown(probe: ReturnType<typeof createHostedClient>, workspace: string, caps: HostedCapabilities, shown: HostedResultScope) {
    if (caps.features?.["catalog.paged_read"] === true) return probe.loadResultHeader(workspace, caps, shown);
    throw new ProductError("feature_unavailable", "Access to this result cannot be confirmed here.");
  }
  function installGuard(session: HostedSession) {
    lastAsked = -Infinity;
    guard = createSessionGuard({ contextId: session.context_id, now, tell: options.tell, authorizationLeaseMs: LEASE_MS,
      onWipe: () => clear(),
      check: async (): Promise<SessionAnswer> => {
        const asked = now(), previous = lastAsked;
        lastAsked = asked;
        try {
          const next = await refreshSession();
          const workspace = state.workspace?.workspace_id;
          if (workspace) await refreshCapabilities(workspace);
          const job = watchedJob, shown = watchedResult, caps = state.workspace;
          // A job this tab is already polling was read by that polling: its newest read counts, from when it was sent.
          // Only a read made since the previous check is new: reusing an older one would date this confirmation back
          // and make the next check due at once (FL-125).
          const polled = job && jobReadAt?.job === job && now() - jobReadAt.at < LEASE_MS && jobReadAt.at > previous ? jobReadAt.at : null;
          if (workspace && caps && (job || shown)) {
            // a job being waited on and the result on screen are separate scopes: both must still be allowed
            const generation = state.generation;
            const settled = await Promise.allSettled([job && polled === null && scoped(next, generation, async probe => {
                const read = await probe.loadJob(workspace, job);
                // a finished job cannot change any more: nothing of it on screen needs its own read again
                if (TERMINAL.has(read.status) && watchedJob === job) watchedJob = null;
              }),
              shown && scoped(next, generation, probe => readShown(probe, workspace, caps, shown))]);
            const failed = settled.find(outcome => outcome.status === "rejected");
            // once a denial has dropped the workspace, nothing of the old scope is on screen to confirm
            if (failed && current(generation)) throw failed.reason;
          }
          return { status: "ok", context_id: next.context_id, ...(polled !== null ? { since: polled } : {}) };
        } catch (error) {
          if (error instanceof ProductError && error.code === "session_expired") return { status: "expired" };
          if (error instanceof ProductError && ["session_revoked", "membership_revoked"].includes(error.code)) return { status: "revoked" };
          return { status: "unreachable" };
        }
      } });
  }
  async function start() {
    alive = true; clear("loading");
    const generation = state.generation;
    let unauthorized = false;
    const bootstrap = createHostedClient({ fetch: options.fetch, signal: controller.signal,
      isCurrent: () => current(generation), onAnswer: status => { unauthorized ||= status === 401; } });
    try {
      const session = await bootstrap.loadSession();
      if (!current(generation)) return;
      state = { ...state, status: "ready", session, error: null };
      installGuard(session); client = makeClient(); publish({});
    } catch (error) {
      if (!current(generation) || error instanceof HostedRequestDiscarded) return;
      publish({ status: unauthorized ? "signed_out" : "error", error: unauthorized ? null :
        error instanceof ProductError ? error : new ProductError("unavailable", "Access could not be confirmed. Try again.") });
    }
  }
  async function selectWorkspace(workspaceId: string) {
    requireSession();
    const sequence = ++selecting;
    controller.abort(); controller = new AbortController(); checking = null; watchedJob = null; watchedResult = null; jobReadAt = null;
    options.onClear();
    publish({ workspace: null, error: null, generation: state.generation + 1, opening: workspaceId });
    client = makeClient();
    const generation = state.generation;
    try {
      const workspace = await client.loadCaps(workspaceId);
      if (current(generation) && sequence === selecting) publish({ workspace, opening: null });
    } catch (error) {
      if (current(generation) && sequence === selecting) {
        publish({ opening: null, error: error instanceof HostedRequestDiscarded ? state.error
          : error instanceof ProductError ? error : new ProductError("unavailable", "The workspace could not be opened.") });
      }
    }
  }
  async function refreshCapabilities(workspaceId: string) {
    requireSession(); const generation = state.generation;
    const next = await client.loadCaps(workspaceId);
    if (!current(generation)) throw new HostedRequestDiscarded();
    const previous = state.workspace;
    if (previous?.workspace_id !== workspaceId) return;
    if (JSON.stringify(previous) !== JSON.stringify(next)) {
      controller.abort(); controller = new AbortController(); checking = null;
      options.onClear();
      publish({ workspace: next, generation: state.generation + 1 });
      client = makeClient();
    }
  }
  return {
    snapshot: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    start, refreshSession, selectWorkspace, refreshCapabilities,
    guard: () => guard,
    watchJob: (job: string | null) => { watchedJob = job; },
    watchResult: (scope: HostedResultScope | null) => {
      const same = scope?.resultId === watchedResult?.resultId && scope?.projectId === watchedResult?.projectId
        && scope?.snapshotId === watchedResult?.snapshotId;
      watchedResult = scope;
      // a different result on screen is confirmed by its own header, never by a check sent for the previous one
      if (scope && !same) guard?.invalidate();
    },
    client: () => { requireSession(); return client; },
    signal: () => controller.signal,
    isCurrent: (generation: number) => current(generation),
    authError(error: ProductError) {
      if (error.code === "session_expired") guard?.noteResponse(401, error.code);
      else if (["session_revoked", "membership_revoked"].includes(error.code)) clear();
    },
    async renew() { const generation = state.generation; const next = await client.renew(); if (current(generation)) applySession(next); },
    async logout(all = false) {
      const generation = state.generation;
      const destination = all ? (await client.logoutAll(), null) : await client.logout();
      if (!current(generation)) throw new HostedRequestDiscarded();
      options.tell?.({ type: "revoked" }); clear(); return destination;
    },
    stop() { alive = false; clear(); },
  };
}

export type HostedBootstrap = ReturnType<typeof createHostedBootstrap>;
