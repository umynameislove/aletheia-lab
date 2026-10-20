import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import viewJson from "../data/synthetic_p6_view.json";
import { PREVIEW, PREVIEW_VERSION } from "../data/preview";
import type { ProductView } from "../types";
import { DemoProductPort } from "./demo";
import { LiveProductPort } from "./live";
import { rememberProject } from "./opened";
import { isProductError, ProductError, type ProductPort } from "./port";
import { resultTransitions } from "./transitions";
import { useStore } from "../store";

/* The app talks to the backend only through this port. The build picks it once:
   `--mode live` → LiveProductPort over the local bridge to ProductService; otherwise the
   offline DemoProductPort over the shared fixtures. A Live failure is shown, never swapped for demo. */
export const demoPort = new DemoProductPort({ view: viewJson as ProductView, preview: PREVIEW });
export const port: ProductPort = import.meta.env.MODE === "live"
  ? new LiveProductPort({ fetch: (url, init) => fetch(url, init) })
  : demoPort;
export { PREVIEW_VERSION, ProductError, isProductError };

/** What to do after a delete: the demo can be reset; a Live delete is final. */
export const AFTER_DELETE = port.kind === "demo"
  ? "Reset demo data from the account menu to restore it."
  : "Deleted from the store. Connect a project to start again.";

export type Boot = { status: "loading" } | { status: "ready"; view: ProductView } | { status: "error"; error: ProductError };

/* Live results are stored by the service, so the open IDs live in the URL (?project=&snapshot=
   &result=) and a reload or back/forward reopens exactly that result through view(). The same
   opaque IDs are kept locally so a cold start can continue the open project (service/opened.ts).
   The demo keeps its results in memory only, so it never puts one in the URL. */
export const linked = port.kind === "live";
const hashResult = () => new URLSearchParams(location.hash.split("?")[1] ?? "").get("result");
export function linkIds(ids: { project?: string; snapshot?: string; result?: string | null }) {
  if (!linked) return;
  const [route, qs] = (location.hash || "#/investigation").split("?");
  const q = new URLSearchParams(qs ?? "");
  const before = q.toString();
  for (const [k, v] of Object.entries(ids)) {
    if (v === undefined) continue;
    if (v === null) q.delete(k); else q.set(k, v);
  }
  if (q.toString() !== before) location.hash = `${route}?${q}`;
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
      if (linked && !view.demo_only) {
        linkIds({ project: view.project.id, snapshot: view.snapshot.id, result: view.result.id });
      }
      setBoot({ status: "ready", view });
    },
    reject: error => setBoot({ status: "error", error: safe(error) }),
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
