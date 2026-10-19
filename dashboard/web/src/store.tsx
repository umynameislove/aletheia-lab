import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import type { ReviewState } from "./types";
import { activateProject, projectDeleted } from "./service/transitions";

/* ------------------------------------------------------------------ routing --
   Hash routing with deep-linkable selection: #/investigation?project=&snapshot=&result=&claim=&evidence=&view=&filter=&q=
   Every update reads the live hash, so two patches in one tick never drop the first. */

export const ROUTES = [
  "start", "connect", "project", "investigation", "results", "retention", "counterfactual", "prospective",
] as const;
export type RouteName = (typeof ROUTES)[number];
const STICKY = ["project", "snapshot", "result"] as const;

function readHash(): { name: RouteName; q: URLSearchParams } {
  const raw = (location.hash || "#/investigation").replace(/^#\/?/, "");
  const [name, qs] = raw.split("?");
  return {
    name: (ROUTES as readonly string[]).includes(name) ? (name as RouteName) : "investigation",
    q: new URLSearchParams(qs || ""),
  };
}

export function useRoute() {
  const [r, setR] = useState(readHash);
  useEffect(() => {
    const on = () => setR(readHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  // the open IDs (?project=&snapshot=&result=, Live only) travel with every move; other params belong to one screen
  const go = useCallback((name: RouteName, params?: Record<string, string>) => {
    const cur = readHash().q;
    const all: Record<string, string> = { ...params };
    for (const k of STICKY) { const v = cur.get(k); if (v && !(params && k in params)) all[k] = v; }
    const qs = all && Object.keys(all).length ? "?" + new URLSearchParams(all) : "";
    location.hash = "#/" + name + qs;
  }, []);
  const setQ = useCallback((patch: Record<string, string | null | undefined>) => {
    const cur = readHash();
    Object.entries(patch).forEach(([k, v]) => (v == null || v === "") ? cur.q.delete(k) : cur.q.set(k, v));
    const qs = cur.q.toString();
    location.hash = "#/" + cur.name + (qs ? "?" + qs : "");
  }, []);
  return { name: r.name, q: r.q, go, setQ };
}

/* ------------------------------------------------------- local demo state --
   The reviewer's own pass. Lives in memory on this machine, is never sent anywhere,
   and never changes what the service reported. */

export interface Receipt {
  scope: string;
  project_id: string;
  status: string;
  deleted_count: number;
  retained_shared_count: number;
}

export interface DemoState {
  activeProjectId?: string;
  review: Record<string, ReviewState>;
  notes: Record<string, string>;
  failed: boolean;
  /* UI state reflects the deletion receipt; Live deletion itself is performed by the port. */
  deletedConversation: boolean;
  deletedProject: boolean;
  receipt: Receipt | null;
}
type Action =
  | { type: "review"; id: string; value: ReviewState }
  | { type: "reviewAll"; ids: string[] }
  | { type: "reviewReset" }
  | { type: "note"; id: string; text: string }
  | { type: "toggleFailed" }
  | { type: "deleteConversation"; receipt: Receipt }
  | { type: "deleteProject"; receipt: Receipt }
  | { type: "activateProject"; project_id: string }
  | { type: "reset" };

const fresh = (): DemoState => ({ review: {}, notes: {}, failed: false, deletedConversation: false, deletedProject: false, receipt: null });

function reduce(s: DemoState, a: Action): DemoState {
  switch (a.type) {
    case "review": {
      const review = { ...s.review }, notes = { ...s.notes };
      if (a.value === "unchecked") { delete review[a.id]; delete notes[a.id]; } else review[a.id] = a.value;
      return { ...s, review, notes };
    }
    case "reviewAll": {
      const review = { ...s.review };
      a.ids.forEach(id => { if (!review[id]) review[id] = "checked"; });
      return { ...s, review };
    }
    case "reviewReset": return { ...s, review: {}, notes: {} };
    case "note": return { ...s, notes: { ...s.notes, [a.id]: a.text } };
    case "toggleFailed": return { ...s, failed: !s.failed };
    case "deleteConversation": return { ...s, deletedConversation: true, receipt: a.receipt };
    case "deleteProject": return projectDeleted(s, a.receipt);
    case "activateProject": return activateProject(s, a.project_id);
    case "reset": return fresh();
  }
}

/* ------------------------------------------------------------------ toasts -- */
export interface Toast { id: number; title: string; body?: string }

interface Ctx {
  s: DemoState;
  dispatch: (a: Action) => void;
  toast: (title: string, body?: string) => void;
  toasts: Toast[];
  theme: "dark" | "light";
  toggleTheme: () => void;
}
const StoreCtx = createContext<Ctx | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [s, dispatch] = useReducer(reduce, undefined, fresh);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const n = useRef(0);
  const toast = useCallback((title: string, body?: string) => {
    const id = ++n.current;
    setToasts(t => [...t, { id, title, body }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 5000);
  }, []);

  const [theme, setTheme] = useState<"dark" | "light">(
    () => (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
  const toggleTheme = useCallback(() => {
    setTheme(t => {
      const next = t === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("p6-theme", next); } catch { /* storage may be blocked; the toggle still works */ }
      return next;
    });
  }, []);

  const value = useMemo(() => ({ s, dispatch, toast, toasts, theme, toggleTheme }), [s, toast, toasts, theme, toggleTheme]);
  return <StoreCtx.Provider value={value}>{children}</StoreCtx.Provider>;
}

export function useStore() {
  const c = useContext(StoreCtx);
  if (!c) throw new Error("useStore outside StoreProvider");
  return c;
}

export const reviewOf = (s: DemoState, id: string): ReviewState => s.review[id] ?? "unchecked";
export function reviewStats(s: DemoState, ids: string[]) {
  let checked = 0, disputed = 0;
  ids.forEach(id => { const r = reviewOf(s, id); if (r === "checked") checked++; else if (r === "disputed") disputed++; });
  return { total: ids.length, checked, disputed, done: checked + disputed, left: ids.length - checked - disputed };
}
