import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { App } from "../../App";
import { ServiceProvider, bindHostedSession, setHostedContext, type Boot } from "../../service";
import { createHostedBootstrap, type HostedBootstrap, type HostedBoot } from "../../service/hosted-bootstrap";
import { sessionChannel } from "../../service/session";
import { HostedRequestDiscarded, type HostedAnalysisResult } from "../../service/hosted-client";
import { ProductError } from "../../service/port";
import { wipeAll } from "../../service/wipe";
import type { ProductView } from "../../types";
import { createWorkspaceClient } from "../../service/workspace-client";
import type { HostedAccountActions } from "../Rail";
import { HostedFrame, type HostedScreen } from "./HostedFrame";
import { WorkspaceDashboard } from "./WorkspaceDashboard";
import { SessionGate } from "../SessionGate";
import { RefusalNote } from "../Ask";
import { ScreenBoundary } from "../primitives";
import type { LargeResult as LargeResultView } from "./LargeResult";

/* Each of these is one screen; loaded when it opens, so the signed-in shell stays small (bundle budget).
   A chunk that fails to load (404, offline) lands in ScreenBoundary, inside SessionGate, never a blank root. */
const WorkspaceManager = lazy(() => import("../workspaces/WorkspaceManager").then(m => ({ default: m.WorkspaceManager })));
const HostedConnect = lazy(() => import("./HostedConnect").then(m => ({ default: m.HostedConnect })));
const LargeResult = lazy(() => import("./LargeResult").then(m => ({ default: m.LargeResult })));
const opening = <div className="boot" role="status">Opening…</div>;

const safe = (error: unknown) => error instanceof ProductError ? error : new ProductError("unavailable", "The request could not be confirmed. Try again.");
const id = (value: string | null): value is string => value !== null && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
const REASONS: Record<string, string> = {
  login_cancelled: "Sign-in was cancelled.", login_failed: "Sign-in could not be completed. New beta accounts need approval from a workspace administrator.",
  account_disabled: "This account is disabled. Contact its administrator.", idp_unavailable: "The identity service could not be reached. Try signing in again.",
  signed_out: "You signed out.",
};
interface ResultLink { workspace: string; job?: string; project: string; snapshot: string; result: string; paged?: boolean }
function resultLink(hash: string): ResultLink | null {
  const query = new URLSearchParams(hash.split("?")[1] ?? "");
  const result = Object.fromEntries(["workspace", "project", "snapshot", "result"].map(key => [key, query.get(key)]));
  if (!Object.entries(result).every(([key, value]) => query.getAll(key).length === 1 && id(value))) return null;
  const job = query.get("job");
  if (query.has("job") && (query.getAll("job").length !== 1 || !id(job))) return null;
  return { ...result as unknown as ResultLink, ...(job ? {job} : {}), ...(query.get("paged") === "1" ? { paged: true } : {}) };
}

const SIGN_IN_BOOKMARK = "aletheia.sign-in-bookmark";
const SIGN_OUT_RETURN = "aletheia.sign-out-return";
const BOOKMARK_LIFETIME_MS = 300_000;
function bookmark(hash: string): string | null {
  if (hash.length > 2048 || !hash.startsWith("#/investigation?") || !resultLink(hash)) return null;
  const query = new URLSearchParams(hash.slice("#/investigation?".length));
  const fields = ["workspace", "job", "project", "snapshot", "result", "paged", "claim", "evidence", "node", "turn", "review"];
  const kept = new URLSearchParams();
  for (const key of fields) {
    const value = query.get(key);
    if (query.getAll(key).length === 1 && id(value)) kept.set(key, value);
  }
  return "#/investigation?" + kept;
}
function forgetBookmark() {
  try { sessionStorage.removeItem(SIGN_IN_BOOKMARK); } catch { /* Storage can be unavailable. */ }
}
function rememberBookmark(hash: string | null) {
  if (!hash) return;
  try {
    sessionStorage.setItem(SIGN_IN_BOOKMARK, JSON.stringify({ hash, expires: Date.now() + BOOKMARK_LIFETIME_MS }));
  } catch { /* Sign-in remains available without tab storage. */ }
}
function takeBookmark(): string | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(SIGN_IN_BOOKMARK) ?? "null");
    const now = Date.now();
    return typeof value?.hash === "string" && Number.isFinite(value.expires)
      && value.expires > now && value.expires <= now + BOOKMARK_LIFETIME_MS ? bookmark(value.hash) : null;
  } catch { return null; }
  finally { forgetBookmark(); }
}

async function openAuthorizedBookmark(boot: HostedBootstrap, target: string, session: NonNullable<HostedBoot["session"]>,
  setHash: (hash: string) => void) {
  const destination = resultLink(target);
  if (!destination) return;
  const from = location.hash;
  await boot.selectWorkspace(destination.workspace);
  const opened = boot.snapshot(), caps = opened.workspace;
  if (opened.session?.user_id !== session.user_id || opened.session.context_id !== session.context_id
    || caps?.workspace_id !== destination.workspace || caps.features?.["catalog.paged_read"] !== true) return;
  await boot.client().loadResultHeader(destination.workspace, caps, {
    projectId: destination.project, snapshotId: destination.snapshot, resultId: destination.result,
  });
  if (!boot.isCurrent(opened.generation) || location.hash !== from) return;
  history.replaceState(history.state, "", location.pathname + location.search + target);
  setHash(location.hash);
}

const REGISTRATION = import.meta.env.VITE_ALETHEIA_REGISTRATION === "enabled";

function authEntry(): "signin" | "signup" | null {
  const values = new URLSearchParams(location.search).getAll("auth");
  if (values.length !== 1) return null;
  return values[0] === "signin" || values[0] === "signup" ? values[0] : null;
}

function signOutReturn() {
  try {
    const expires = Number(sessionStorage.getItem(SIGN_OUT_RETURN));
    const now = Date.now();
    return expires > now && expires <= now + BOOKMARK_LIFETIME_MS;
  } catch { return false; }
}

async function chooseInitialWorkspace(boot: HostedBootstrap, session: NonNullable<HostedBoot["session"]>, path: string) {
  const sameContext = () => {
    const current = boot.snapshot();
    return current.status === "ready" && current.session?.context_id === session.context_id
      && current.session?.user_id === session.user_id && location.hash === path;
  };
  if (session.last_workspace_id) {
    await boot.selectWorkspace(session.last_workspace_id);
    if (!sameContext() || boot.snapshot().workspace) return;
  }
  const { generation } = boot.snapshot();
  if (!sameContext()) return;
  const directory = createWorkspaceClient({ session, workspaceId: null, fetch: boot.fetchForSession(generation),
    signal: boot.signal(), isCurrent: () => boot.isCurrent(generation) && sameContext(), onAuthError: boot.authError });
  const page = await directory.workspaces({ limit: 2 });
  if (sameContext() && boot.isCurrent(generation) && page.items.length === 1 && page.next_cursor === null) {
    await boot.selectWorkspace(page.items[0]!.workspace_id);
  }
}

export function HostedApp() {
  const [hash, setHash] = useState(location.hash);
  const [entry] = useState(authEntry);
  const [invalidEntry] = useState(() => !entry && new URLSearchParams(location.search).has("auth"));
  const [returningFromSignOut] = useState(() => !entry && !location.hash && signOutReturn());
  const [leaving, setLeaving] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const consumedEntry = useRef(false);
  const incomingBookmark = useRef(bookmark(location.hash));
  const checkedBookmark = useRef(false);
  const openingBookmark = useRef(false);
  const channel = useRef<ReturnType<typeof sessionChannel> | null>(null);
  const instance = useRef<HostedBootstrap | null>(null);
  if (!instance.current) instance.current = createHostedBootstrap({ fetch: (url, init) => fetch(url, init),
    idpOrigin: import.meta.env.VITE_IDP_ORIGIN,
    tell: message => channel.current?.tell(message),
    onWorkspaceLost: workspace => {
      // Retire the denied link without replacing a link to a different workspace already pasted in this tab.
      if (resultLink(location.hash)?.workspace !== workspace) return;
      history.replaceState(history.state, "", location.pathname + location.search + "#/workspaces");
      setHash(location.hash);
    },
    onClear: () => { wipeAll(); setHostedContext(null); } });
  const boot = instance.current;
  const state = useSyncExternalStore(boot.subscribe, boot.snapshot);
  const reason = new URLSearchParams(hash.split("?")[1] ?? "").get("reason");
  const autoSignIn = !consumedEntry.current && state.status === "signed_out" && !state.error
    && reason === null && !invalidEntry && !returningFromSignOut && (entry !== "signup" || REGISTRATION);
  const linkWorkspace = resultLink(hash)?.workspace;
  const linkHash = linkWorkspace ? hash : undefined;
  const selected = useRef<{ account: string; linkHash?: string } | null>(null);
  useEffect(() => {
    channel.current = sessionChannel(message => boot.guard()?.onPeer(message));
    void boot.start();
    const changed = () => setHash(location.hash);
    window.addEventListener("hashchange", changed);
    return () => { boot.stop(); channel.current?.close(); channel.current = null; window.removeEventListener("hashchange", changed); };
  }, [boot]);
  useEffect(() => {
    bindHostedSession(boot.guard(), { signIn: () => location.assign("/v1/auth/start"), startOver: () => { location.hash = "#/workspaces"; void boot.start(); } });
    return () => bindHostedSession(null, null);
  }, [boot, state.session, state.generation]);
  useEffect(() => {
    if (leaving || (state.status !== "ready" && state.status !== "signed_out")) return;
    const first = !consumedEntry.current;
    if (!first && state.status !== "signed_out") return;
    if (first) {
      consumedEntry.current = true;
      try { sessionStorage.removeItem(SIGN_OUT_RETURN); } catch { /* Storage can be unavailable. */ }
    }
    const url = new URL(location.href);
    if (first) url.searchParams.delete("auth");
    if (state.status === "signed_out") {
      // Retire the private scope from the address bar, keeping the safe bookmark in this tab for the callback.
      forgetBookmark();
      const params = new URLSearchParams(url.hash.split("?")[1] ?? "");
      if (first && returningFromSignOut) url.hash = "#/workspaces?reason=signed_out";
      else if ([...params.keys()].some(key => key !== "reason")) {
        const stop = params.get("reason");
        url.hash = "#/workspaces" + (stop ? "?" + new URLSearchParams({ reason: stop }) : "");
      }
    }
    if (url.href !== location.href) {
      history.replaceState(history.state, "", url.pathname + url.search + url.hash);
      setHash(location.hash);
    }
    if (autoSignIn) {
      rememberBookmark(incomingBookmark.current);
      setLeaving(true);
      location.replace(entry === "signup" ? "/v1/auth/start?mode=register" : "/v1/auth/start");
    }
  }, [autoSignIn, entry, hash, leaving, returningFromSignOut, state.status]);
  useLayoutEffect(() => {
    if (state.status !== "ready" || !state.session) { selected.current = null; return; }
    if (!checkedBookmark.current) {
      checkedBookmark.current = true;
      incomingBookmark.current = null;
      const target = takeBookmark();
      if (target && !linkWorkspace) {
        openingBookmark.current = true;
        void openAuthorizedBookmark(boot, target, state.session, setHash)
          .catch(() => { /* An inaccessible bookmark stays closed. */ })
          .finally(() => { openingBookmark.current = false; });
        return;
      }
    }
    if (openingBookmark.current) return;
    const account = state.session.user_id + "/" + state.session.context_id;
    const sameAccount = selected.current?.account === account;
    if (sameAccount && selected.current?.linkHash === linkHash) return;
    selected.current = { account, linkHash };
    if (linkWorkspace) {
      if (linkWorkspace !== state.workspace?.workspace_id && linkWorkspace !== state.opening) void boot.selectWorkspace(linkWorkspace);
    } else if (!sameAccount && !state.workspace && !state.opening && !location.hash.startsWith("#/workspaces")) {
      setChoosing(true);
      void chooseInitialWorkspace(boot, state.session, location.hash)
        .catch(() => { /* The workspace chooser can retry the directory request. */ })
        .finally(() => setChoosing(false));
    }
  }, [boot, linkHash, linkWorkspace, state.status, state.session, state.workspace?.workspace_id, state.opening]);
  if (leaving || autoSignIn) return <div className="boot" role="status">Opening secure sign-in…</div>;
  if (state.status === "loading") return <div className="boot" role="status">Checking your session…</div>;
  if (state.status === "error") return <main className="page" id="main"><div className="page-inner page-inner--narrow">
    <header className="page-head"><h1>Could not confirm your access</h1>
      <p className="lede">{state.error?.safe_message ?? "Check your connection and try again."}</p></header>
    <button className="btn btn--primary" onClick={() => { void boot.start(); }}>Try again</button>
  </div></main>;
  if (state.status !== "ready" || !state.session) {
    const notice = reason ? REASONS[reason] : undefined;
    const signupClosed = entry === "signup" && !REGISTRATION && !notice;
    return <main className="page" id="main"><div className="page-inner page-inner--narrow"><header className="page-head">
      <h1>{signupClosed ? "Beta accounts by invitation" : "Sign in to Aletheia"}</h1>
      <p className="lede">{notice ?? (signupClosed ? "Ask your workspace administrator for a beta account. If you already have one, sign in below." : "Use your workspace account.")}</p></header>
      {state.error && <RefusalNote refusal={{ code: state.error.code, message: state.error.safe_message }} />}
      <div className="actions"><a className="btn btn--primary" href={entry === "signup" && REGISTRATION ? "/v1/auth/start?mode=register" : "/v1/auth/start"} onClick={event => {
        if (!event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) rememberBookmark(incomingBookmark.current);
      }}>{entry === "signup" && REGISTRATION ? "Create account" : "Sign in"}</a>
      <button className="btn" onClick={() => { void boot.start(); }}>Check session again</button></div>
    </div></main>;
  }
  return <SessionGate guard={boot.guard()}>{choosing ? opening : <Workspace key={state.generation} boot={boot} state={state} hash={hash} />}</SessionGate>;
}

function Workspace({ boot, state, hash }: { boot: HostedBootstrap; state: HostedBoot; hash: string }) {
  const session = state.session!, caps = state.workspace;
  const [error, setError] = useState<ProductError | null>(null), [busy, setBusy] = useState(false);
  const [view, setView] = useState<ProductView | undefined>();
  const current = () => boot.isCurrent(state.generation);
  const go = (screen: string) => { location.hash = "#/" + screen; };
  async function action(work: () => Promise<unknown>) {
    if (busy || !current()) return;
    setBusy(true); setError(null);
    try { await work(); } catch (failure) { if (current() && !(failure instanceof HostedRequestDiscarded)) setError(safe(failure)); }
    finally { if (current()) setBusy(false); }
  }
  async function signOut(all: boolean) {
    const destination = await boot.logout(all);
    location.replace("#/workspaces?reason=signed_out");
    if (destination) {
      // Keycloak returns to the registered app URL. The one-use marker keeps that return signed out.
      try { sessionStorage.setItem(SIGN_OUT_RETURN, String(Date.now() + BOOKMARK_LIFETIME_MS)); } catch { /* Storage can be unavailable. */ }
      location.assign(destination);
    }
  }
  const path = hash.split("?")[0];
  const connect = path === "#/connect" && caps?.allowed_actions.includes("import") === true;
  const results = path === "#/results";
  const managing = path === "#/workspaces";
  const parsed = resultLink(hash);
  const link = parsed && caps?.workspace_id === parsed.workspace ? parsed : null;
  const account: HostedAccountActions = { displayName: session.display_name, pending: busy,
    renew: () => { void action(() => boot.renew()); },
    signOut: all => { void action(() => signOut(all)); } };
  const notice = error ?? state.error;
  function show(result: HostedAnalysisResult, job: string) {
    if (!current() || !caps) return;
    const reference = "kind" in result ? result : { project_id: result.project.id, snapshot_id: result.snapshot.id, result_id: result.result.id };
    setView("kind" in result ? undefined : result);
    const query = new URLSearchParams({ workspace: caps.workspace_id, job, project: reference.project_id, snapshot: reference.snapshot_id, result: reference.result_id });
    if ("kind" in result) query.set("paged", "1");
    location.hash = "#/investigation?" + query;
  }
  if (link && !link.paged && caps?.features?.["catalog.paged_read"] === true) {
    return <ScreenBoundary key={JSON.stringify(link)}><ShownResult boot={boot} state={state} link={link} view={view} account={account} /></ScreenBoundary>;
  }
  const screen: HostedScreen = connect ? "connect" : link ? "investigation" : !caps || managing ? "workspaces" : results ? "results" : "start";
  return <HostedFrame workspace={caps} screen={screen} account={account} resultHref={link ? hash : undefined}>
    {notice && <div className="hosted-notice"><RefusalNote refusal={{ code: notice.code, message: notice.safe_message }} /></div>}
    <ScreenBoundary key={link ? JSON.stringify(link) : screen}><Suspense fallback={opening}>
      {state.opening ? opening : connect && caps ? <div className="page" id="main" tabIndex={-1}><div className="page-inner">
        <HostedConnect client={boot.client()} session={session} workspaceId={caps.workspace_id} caps={caps} isCurrent={current}
          onView={show} onJob={boot.watchJob} onBack={() => go("start")} /></div></div>
      : link ? caps?.features?.["catalog.paged_read"] === true
        ? <PagedResult boot={boot} state={state} link={link} />
        : <Unconfirmable boot={boot} workspace={link.workspace} />
      : caps && !managing && !parsed ? <WorkspaceDashboard client={boot.client()} caps={caps} resultsOnly={results} />
      : <WorkspaceManager session={session} workspace={caps} canCreateWorkspace={session.can_create_workspace}
          fetch={boot.fetchForSession(state.generation)} signal={boot.signal()} isCurrent={current}
          onSessionRefresh={() => boot.refreshSession().then(() => {})} onCapabilitiesRefresh={boot.refreshCapabilities}
          onWorkspaceSelected={workspace => { void boot.selectWorkspace(workspace); go("start"); }} onAuthError={boot.authError} />}
    </Suspense></ScreenBoundary>
  </HostedFrame>;
}

function PagedResult({ boot, state, link }: { boot: HostedBootstrap; state: HostedBoot; link: ResultLink }) {
  const { result, project, snapshot } = link;
  const scope = useMemo(() => ({ resultId: result, projectId: project, snapshotId: snapshot }), [result, project, snapshot]);
  const isCurrent = useCallback(() => boot.isCurrent(state.generation), [boot, state.generation]);
  useLayoutEffect(() => {
    boot.watchResult(scope);
    return () => boot.watchResult(null);
  }, [boot, scope]);
  return <LargeResult client={boot.client()} caps={state.workspace!} scope={scope} isCurrent={isCurrent} />;
}

/* Kiên, 13/11: a server that cannot confirm access to a single result shows none, rather than reading the
   whole result again every few seconds. Checking again reloads the workspace's capabilities only. */
function Unconfirmable({ boot, workspace }: { boot: HostedBootstrap; workspace: string }) {
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState<ProductError | null>(null);
  const again = async () => {
    if (busy) return;
    setBusy(true); setFailed(null);
    try { await boot.refreshCapabilities(workspace); } catch (error) { if (!(error instanceof HostedRequestDiscarded)) setFailed(safe(error)); }
    finally { setBusy(false); }
  };
  return <div className="page" id="main" tabIndex={-1}><div className="page-inner page-inner--narrow" role="alert">
    <header className="page-head"><h1>This result cannot be shown here yet</h1>
      <p className="lede">This workspace's server cannot confirm access to a single result, so the result stays hidden. Nothing is read from it in the background.</p></header>
    {failed && <RefusalNote refusal={{ code: failed.code, message: failed.safe_message }} />}
    <div className="actions">
      <button className="btn btn--primary" aria-busy={busy} aria-disabled={busy || undefined} onClick={() => { void again(); }}>{busy ? "Checking…" : "Check again"}</button>
      <button className="btn" onClick={() => { location.hash = "#/workspaces"; }}>Workspaces</button>
    </div>
  </div></div>;
}

function ShownResult({ boot, state, link, view, account }: {
  boot: HostedBootstrap; state: HostedBoot; link: ResultLink; view?: ProductView; account: HostedAccountActions;
}) {
  const [bound, setBound] = useState(false);
  const { workspace, job, project, snapshot, result } = link;
  useLayoutEffect(() => {
    // the linked result is checked by its own header from before it is shown; the port names any other one it shows.
    // The link's job has finished, so it is not watched: polling a finished job would confirm nothing on screen.
    boot.watchResult({ resultId: result, projectId: project, snapshotId: snapshot });
    setHostedContext({ session: state.session!, workspace: link.workspace, caps: state.workspace!, client: boot.client(),
      isCurrent: () => boot.isCurrent(state.generation), initialJobId: link.job, initialProjectId: link.project,
      initialSnapshotId: link.snapshot, ...(view?.result.id === link.result ? { initialView: view } : {}), onShown: boot.watchResult });
    setBound(true);
    return () => { boot.watchResult(null); setHostedContext(null); };
  }, [boot, state.generation, workspace, job, project, snapshot, result, view]);
  if (!bound) return <div className="boot" role="status">Opening the saved result…</div>;
  return <ServiceProvider fallback={value => <HostedFrame workspace={state.workspace} screen="investigation" account={account} resultHref={location.hash}>
    <ResultFallback boot={value} reader={{ client: boot.client(), caps: state.workspace!,
      scope: { resultId: result, projectId: project, snapshotId: snapshot }, isCurrent: () => boot.isCurrent(state.generation) }} />
  </HostedFrame>}><App hostedAccount={account} /></ServiceProvider>;
}

/* A result that cannot be opened says why. One too large to open whole is read in pages instead (K10),
   when this workspace offers that; otherwise its refusal says so. */
function ResultFallback({ boot, reader }: { boot: Boot; reader: Parameters<typeof LargeResultView>[0] }) {
  if (boot.status !== "error") return <div className="boot" role="status">Loading result…</div>;
  if (boot.error.code === "response_too_large" && reader.caps.features?.["catalog.paged_read"] === true) {
    return <ScreenBoundary key={reader.scope.resultId}><Suspense fallback={opening}><LargeResult {...reader} /></Suspense></ScreenBoundary>;
  }
  return <div className="boot"><div><b>This result could not be opened</b>
    <RefusalNote refusal={{ code: boot.error.code, message: boot.error.safe_message }} />
    <div className="actions">
      <button className="btn btn--sm" onClick={() => location.reload()}>Try again</button>
      <button className="btn btn--sm" onClick={() => { location.hash = "#/workspaces"; }}>Workspaces</button>
    </div></div></div>;
}
