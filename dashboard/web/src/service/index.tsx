import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import viewJson from "../data/synthetic_p6_view.json";
import { PREVIEW } from "../data/preview";
import type { ProductView } from "../types";
import { DemoProductPort } from "./demo";
import { LiveProductPort } from "./live";
import { checkedPort } from "./contract";
import { localCapabilities } from "./capabilities";
import { createFence, createTracker, scopeFromHash } from "./fence";
import { forgetAllProjects, rememberProject } from "./opened";
import { createSessionGuard, sessionChannel, type SessionAnswer } from "./session";
import { onWipe, wipeAll } from "./wipe";
import { isProductError, ProductError, type ProductPort } from "./port";
import { resultTransitions } from "./transitions";
import { useStore } from "../store";

/* The app talks to the backend only through this port. The build picks it once:
   `--mode live` → LiveProductPort over the local bridge to ProductService; otherwise the
   offline DemoProductPort over the shared fixtures. A Live failure is shown, never swapped for demo. */
// the condition is resolved at build time, so a Live bundle carries neither the demo port nor its fixtures
export const demoPort = import.meta.env.MODE === "live" ? null : new DemoProductPort({ view: viewJson as ProductView, preview: PREVIEW });
const livePort = demoPort ? null : new LiveProductPort({
  fetch: (url, init) => fetch(url, init),
  onAnswer: (status, code) => sessionGuard?.noteResponse(status, code),
});
export const port: ProductPort = demoPort ?? checkedPort(livePort!);
export const capabilities = localCapabilities(port.kind);
export { ProductError, isProductError };

/* The scope every async call is fenced against (service/fence.ts). */
export const fence = createFence(() => scopeFromHash(location.hash));

/* Work in flight, and work that finished after the person moved on. In memory, gone with the session. */
const tracker = createTracker(fence);
export function useLateWork() { return useSyncExternalStore(tracker.subscribe, tracker.late); }
export function useRunningWork() { return useSyncExternalStore(tracker.subscribe, tracker.running); }
export const dismissLate = (id: number) => tracker.dismiss(id);

type ScopedThen<T> = Parameters<typeof tracker.run<T>>[2];
export function scoped<T>(op: string, work: () => Promise<T>, then: ScopedThen<T>): Promise<void> {
  return tracker.run(op, work, { codeOf: e => (isProductError(e) ? e.code : "unavailable"), ...then });
}

/* Ending a session forgets the scope: nothing in flight may touch the screen afterwards. */
onWipe(() => { fence.advance(); tracker.clear(); });
onWipe(forgetAllProjects);

/* Sessions (service/session.ts). The local app has no accounts, so it has no guard. The demo drives the
   real guard from a simulated answer, so the screens can be reviewed and tested; a hosted build gets
   its guard from GET /v1/session when K-03 exists. */
let demoAnswer: SessionAnswer = { status: "ok", context_id: "demo-session" };
const channel = demoPort ? sessionChannel(message => sessionGuard?.onPeer(message)) : null;
export const sessionGuard = demoPort
  ? createSessionGuard({ check: async () => demoAnswer, contextId: "demo-session", now: Date.now, tell: m => channel?.tell(m), onWipe: wipeAll })
  : null;
export function previewSession(answer: SessionAnswer) { demoAnswer = answer; void sessionGuard?.check(true); }
export const sessionActions = {
  signIn() { demoAnswer = { status: "ok", context_id: "demo-session" }; sessionGuard?.resume("demo-session"); },
  startOver() { location.replace(location.pathname + "#/start"); location.reload(); },
};

/* The results opened in this tab. There is no listing method yet, so this is what can be offered
   for going back. The question is held in memory only and never written to storage. */
export interface OpenedResult { result_id: string; project_id: string; snapshot_id: string; question: string; project: string }
let history: OpenedResult[] = [];
const historyListeners = new Set<() => void>();
const subscribeHistory = (l: () => void) => { historyListeners.add(l); return () => { historyListeners.delete(l); }; };
onWipe(() => { history = []; historyListeners.forEach(l => l()); });
export function useResultHistory() { return useSyncExternalStore(subscribeHistory, () => history); }
function noteResult(view: ProductView) {
  if (view.demo_only || history[0]?.result_id === view.result.id) return;
  const turns = view.conversation.turns;
  const entry = { result_id: view.result.id, project_id: view.project.id, snapshot_id: view.snapshot.id, question: turns[turns.length - 1]?.question ?? "", project: view.project.display_name };
  history = [entry, ...history.filter(h => h.result_id !== view.result.id)].slice(0, 50);
  historyListeners.forEach(l => l());
}

/** What to do after a delete: the demo can be reset; a Live delete is final. */
export const AFTER_DELETE = port.kind === "demo"
  ? "Reset demo data from the account menu to restore it."
  : "Deleted from the store. The same folder cannot be imported again here; you can connect a different one.";

export type Boot = { status: "loading" } | { status: "ready"; view: ProductView } | { status: "error"; error: ProductError };

/* Live results are stored by the service, so the open IDs live in the URL (?project=&snapshot=
   &result=) and a reload or back/forward reopens exactly that result through view(). The same
   opaque IDs are kept locally so a cold start can continue the open project (service/opened.ts).
   The demo keeps its results in memory only, so it never puts one in the URL. */
export const linked = port.kind === "live";
const SCREEN_PARAMS = ["claim", "evidence", "node", "page", "q", "filter", "turn", "panel", "pane"];
const hashResult = () => new URLSearchParams(location.hash.split("?")[1] ?? "").get("result");
export function linkIds(ids: { project?: string; snapshot?: string; result?: string | null }, opts?: { replace?: boolean }) {
  if (!linked) return;
  const [route, qs] = (location.hash || "#/investigation").split("?");
  const q = new URLSearchParams(qs ?? "");
  const before = q.toString();
  // another project is another scope: its selection, page and filter do not carry over
  const leaving = q.get("project");
  if (ids.project && leaving && ids.project !== leaving) for (const k of SCREEN_PARAMS) q.delete(k);
  for (const [k, v] of Object.entries(ids)) {
    if (v === undefined) continue;
    if (v === null) q.delete(k); else q.set(k, v);
  }
  if (q.toString() === before) return;
  if (opts?.replace) location.replace(`${route}?${q}`); else location.hash = `${route}?${q}`;
}
function remember(view: ProductView) {
  if (!linked || view.demo_only) return;
  rememberProject({ project_id: view.project.id, snapshot_id: view.snapshot.id, result_id: view.result.id });
}
const safe = (e: unknown) => isProductError(e) ? e : new ProductError("unavailable", "The product view could not be loaded.");

/* The result on screen. Every screen reads this one value — nothing keeps a second copy. */
interface ViewState { view: ProductView; showResult: (v: ProductView) => void; resetResult: () => Promise<void> }
const ViewCtx = createContext<ViewState | null>(null);

export function ServiceProvider({ children, fallback }: { children: ReactNode; fallback: (b: Boot) => ReactNode }) {
  const [boot, setBoot] = useState<Boot>({ status: "loading" });
  const { dispatch } = useStore();
  const transitions = useRef<ReturnType<typeof resultTransitions<ProductView>> | null>(null);
  if (!transitions.current) transitions.current = resultTransitions<ProductView>({
    read: id => id ? port.view(id) : port.demo_view(),
    identify: view => view.result.id,
    accept: view => {
      dispatch({ type: "activateProject", project_id: view.project.id });
      remember(view);
      noteResult(view);
      // the address is brought in line with what opened, not made a step of its own
      if (linked && !view.demo_only) {
        linkIds({ project: view.project.id, snapshot: view.snapshot.id, result: view.result.id }, { replace: true });
      }
      setBoot({ status: "ready", view });
    },
    reject: error => setBoot({ status: "error", error: safe(error) }),
    abandon: () => livePort?.abandonViews(),
  });
  const requests = transitions.current;

  useEffect(() => {
    void requests.load(linked ? hashResult() : null);
    return () => requests.cancel();
  }, [requests]);

  // back/forward or a pasted link to another stored result: open that one, nothing else
  useEffect(() => {
    if (!linked) return;
    const on = () => { void requests.navigate(hashResult()); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [requests]);

  const showResult = useCallback((view: ProductView) => {
    requests.show(view);
    if (linked && !view.demo_only) {
      linkIds({ project: view.project.id, snapshot: view.snapshot.id, result: view.result.id });
    }
  }, [requests]);
  const resetResult = useCallback(() => requests.load(null), [requests]);
  const value = useMemo(() => (boot.status === "ready" ? { view: boot.view, showResult, resetResult } : null), [boot, showResult, resetResult]);
  if (!value) return <>{fallback(boot)}</>;
  return <ViewCtx.Provider value={value}>{children}</ViewCtx.Provider>;
}

/** The p6-product-view/v1 on screen, exactly as the port returned it. */
export function useView(): ProductView {
  const c = useContext(ViewCtx);
  if (!c) throw new Error("useView outside ServiceProvider");
  return c.view;
}

/** Switch the screen to another result the port returned (a follow-up child), or back to demo_view. */
export function useResultSwitch() {
  const c = useContext(ViewCtx);
  if (!c) throw new Error("useResultSwitch outside ServiceProvider");
  return { showResult: c.showResult, resetResult: c.resetResult };
}
