import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { App } from "../../App";
import { ServiceProvider, bindHostedSession, setHostedContext, type Boot } from "../../service";
import { createHostedBootstrap, type HostedBootstrap, type HostedBoot } from "../../service/hosted-bootstrap";
import { sessionChannel } from "../../service/session";
import { HostedRequestDiscarded } from "../../service/hosted-client";
import { ProductError } from "../../service/port";
import { wipeAll } from "../../service/wipe";
import type { ProductView } from "../../types";
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
  login_cancelled: "Sign-in was cancelled.", login_failed: "Sign-in could not be completed.",
  account_disabled: "This account is disabled. Contact its administrator.", idp_unavailable: "The identity service could not be reached. Try signing in again.",
};
interface ResultLink { workspace: string; job: string; project: string; snapshot: string; result: string }
function resultLink(hash: string): ResultLink | null {
  const query = new URLSearchParams(hash.split("?")[1] ?? "");
  const result = Object.fromEntries(["workspace", "job", "project", "snapshot", "result"].map(key => [key, query.get(key)]));
  return Object.entries(result).every(([key, value]) => query.getAll(key).length === 1 && id(value)) ? result as unknown as ResultLink : null;
}

export function HostedApp() {
  const channel = useRef<ReturnType<typeof sessionChannel> | null>(null);
  const instance = useRef<HostedBootstrap | null>(null);
  if (!instance.current) instance.current = createHostedBootstrap({ fetch: (url, init) => fetch(url, init),
    idpOrigin: import.meta.env.VITE_IDP_ORIGIN,
    tell: message => channel.current?.tell(message),
    onClear: () => { wipeAll(); setHostedContext(null); } });
  const boot = instance.current;
  const state = useSyncExternalStore(boot.subscribe, boot.snapshot);
  const [hash, setHash] = useState(location.hash);
  const selected = useRef<string | null>(null);
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
    if (state.status !== "ready" || !state.session) { selected.current = null; return; }
    const key = state.session.user_id + "/" + state.session.context_id;
    if (selected.current === key) return;
    selected.current = key;
    const target = resultLink(location.hash)?.workspace ?? state.session.last_workspace_id;
    if (target) void boot.selectWorkspace(target);
  }, [boot, state.status, state.session]);
  useEffect(() => {
    // Once no one is signed in, nothing of the previous account's scope stays in the address bar (Kiên R4.18):
    // workspace, project, snapshot, result and selection IDs are dropped in place, without a new history entry.
    // Only a sign-out reason survives. Earlier history entries keep their own addresses; opening one asks again.
    if (state.status !== "signed_out") return;
    const query = new URLSearchParams(location.hash.split("?")[1] ?? "");
    if ([...query.keys()].every(key => key === "reason")) return;
    history.replaceState(history.state, "", location.pathname + location.search + "#/workspaces");
    setHash(location.hash);
  }, [state.status, hash]);
  if (state.status === "loading") return <div className="boot" role="status">Checking your session…</div>;
  if (state.status !== "ready" || !state.session) {
    const reason = REASONS[new URLSearchParams(hash.split("?")[1] ?? "").get("reason") ?? ""];
    return <main className="page" id="main"><div className="page-inner"><h1>Sign in to Aletheia</h1>
      <p>{reason ?? "Use your workspace account. Your password and verification codes stay with your identity provider."}</p>
      {state.error && <RefusalNote refusal={{ code: state.error.code, message: state.error.safe_message }} />}
      <div className="actions"><a className="btn btn--primary" href="/v1/auth/start">Sign in</a>
        <button className="btn" onClick={() => { void boot.start(); }}>Check session again</button></div>
    </div></main>;
  }
  return <SessionGate guard={boot.guard()}><Workspace key={state.generation} boot={boot} state={state} hash={hash} /></SessionGate>;
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
  const link = resultLink(hash), connect = hash.split("?")[0] === "#/connect";
  function show(result: ProductView, job: string) {
    if (!current() || !caps) return;
    setView(result);
    const query = new URLSearchParams({ workspace: caps.workspace_id, job, project: result.project.id, snapshot: result.snapshot.id, result: result.result.id });
    location.hash = "#/investigation?" + query;
  }
  return <>
    <header className="page-inner"><div className="actions" aria-label="Account and workspace">
      <span>{session.display_name}{caps && <> · {caps.workspace_name}</>}</span>
      <button className="btn btn--sm" onClick={() => go("workspaces")}>Workspaces</button>
      {caps && <button className="btn btn--sm" onClick={() => go("connect")}>Connect project</button>}
      <button className="btn btn--sm" disabled={busy} onClick={() => { void action(() => boot.renew()); }}>Renew session</button>
      <button className="btn btn--sm" disabled={busy} onClick={() => { void action(async () => { const destination = await boot.logout(); if (destination) location.assign(destination); }); }}>Sign out</button>
      <button className="btn btn--sm" disabled={busy} onClick={() => { void action(() => boot.logout(true)); }}>Sign out all sessions</button>
    </div>{(error || state.error) && <RefusalNote refusal={{ code: (error ?? state.error)!.code, message: (error ?? state.error)!.safe_message }} />}</header>
    <ScreenBoundary key={connect ? "connect" : link ? JSON.stringify(link) : "workspaces"}><Suspense fallback={opening}>{connect && caps ? <main className="page" id="main"><div className="page-inner"><HostedConnect client={boot.client()} session={session}
      workspaceId={caps.workspace_id} caps={caps} isCurrent={current} onView={show} onJob={boot.watchJob} onBack={() => go("workspaces")} /></div></main>
      : link && caps?.workspace_id === link.workspace
        ? <Result key={JSON.stringify(link)} boot={boot} state={state} link={link} view={view} />
        // a workspace still being opened shows nothing of it yet, and its lists are read once it is open
        : state.opening ? opening
        : <WorkspaceManager session={session} workspace={caps} canCreateWorkspace={session.can_create_workspace}
          fetch={(url, init) => fetch(url, init)} signal={boot.signal()} isCurrent={current}
          onSessionRefresh={() => boot.refreshSession().then(() => {})} onCapabilitiesRefresh={boot.refreshCapabilities}
          onWorkspaceSelected={workspace => { void boot.selectWorkspace(workspace); go("workspaces"); }} onAuthError={boot.authError} />}</Suspense></ScreenBoundary>
  </>;
}

function Result({ boot, state, link, view }: { boot: HostedBootstrap; state: HostedBoot; link: ResultLink; view?: ProductView }) {
  // without the bounded header there is no light way to keep confirming access to one result, so none is shown
  if (state.workspace?.features?.["catalog.paged_read"] !== true) return <Unconfirmable boot={boot} workspace={link.workspace} />;
  return <ShownResult boot={boot} state={state} link={link} view={view} />;
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
  return <main className="page" id="main"><div className="page-inner page-inner--narrow" role="alert">
    <header className="page-head"><h1>This result cannot be shown here yet</h1>
      <p className="lede">This workspace's server cannot confirm access to a single result, so the result stays hidden. Nothing is read from it in the background.</p></header>
    {failed && <RefusalNote refusal={{ code: failed.code, message: failed.safe_message }} />}
    <div className="actions">
      <button className="btn btn--primary" aria-busy={busy} aria-disabled={busy || undefined} onClick={() => { void again(); }}>{busy ? "Checking…" : "Check again"}</button>
      <button className="btn" onClick={() => { location.hash = "#/workspaces"; }}>Workspaces</button>
    </div>
  </div></main>;
}

function ShownResult({ boot, state, link, view }: { boot: HostedBootstrap; state: HostedBoot; link: ResultLink; view?: ProductView }) {
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
  return <ServiceProvider fallback={value => <ResultFallback boot={value} reader={{ client: boot.client(), caps: state.workspace!,
    scope: { resultId: result, projectId: project, snapshotId: snapshot }, isCurrent: () => boot.isCurrent(state.generation) }} />}><App /></ServiceProvider>;
}

/* A result that cannot be opened says why. One too large to open whole is read in pages instead (K10),
   when this workspace offers that; otherwise its refusal says so. */
function ResultFallback({ boot, reader }: { boot: Boot; reader: Parameters<typeof LargeResultView>[0] }) {
  if (boot.status !== "error") return <div className="boot" role="status">Loading result…</div>;
  if (boot.error.code === "response_too_large" && reader.caps.features?.["catalog.paged_read"] === true) return <Suspense fallback={opening}><LargeResult {...reader} /></Suspense>;
  return <div className="boot"><div><b>This result could not be opened</b>
    <RefusalNote refusal={{ code: boot.error.code, message: boot.error.safe_message }} />
    <div className="actions">
      <button className="btn btn--sm" onClick={() => location.reload()}>Try again</button>
      <button className="btn btn--sm" onClick={() => { location.hash = "#/workspaces"; }}>Workspaces</button>
    </div></div></div>;
}
