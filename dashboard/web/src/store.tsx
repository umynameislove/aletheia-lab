import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { fresh, reduce, type Action, type DemoState } from "./state";

export { reviewOf, reviewStats, type DemoState, type Receipt } from "./state";

/* ------------------------------------------------------------------ routing --
   Hash routing with deep-linkable selection: #/investigation?project=&snapshot=&result=&claim=&evidence=&view=&filter=&q=
   Every update reads the live hash, so two patches in one tick never drop the first. */

export const ROUTES = [
  "start", "connect", "project", "investigation", "results", "retention", "counterfactual", "prospective",
] as const;
export type RouteName = (typeof ROUTES)[number];
/** An address this app has no screen for. It is reported, never rerouted to some other screen. */
export const NOT_FOUND = "not-found" as const;
const STICKY = ["workspace", "job", "project", "snapshot", "result"] as const;

function readHash(): { name: RouteName | typeof NOT_FOUND; q: URLSearchParams } {
  const raw = (location.hash || "#/start").replace(/^#\/?/, "");
  const [name, qs] = raw.split("?");
  return {
    name: (ROUTES as readonly string[]).includes(name) ? (name as RouteName) : NOT_FOUND,
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
  /* `replace` rewrites the current entry instead of adding one: for typing, stepping with the keys, and
     the app correcting its own address. Otherwise Back walks through every letter, or lands on the bad
     address the app corrected and is sent forward again, so it can never leave. */
  const setQ = useCallback((patch: Record<string, string | null | undefined>, opts?: { replace?: boolean }) => {
    const cur = readHash();
    Object.entries(patch).forEach(([k, v]) => (v == null || v === "") ? cur.q.delete(k) : cur.q.set(k, v));
    const qs = cur.q.toString();
    const next = "#/" + cur.name + (qs ? "?" + qs : "");
    if (opts?.replace) location.replace(next); else location.hash = next;
  }, []);
  /** A real address for the same move, so links can be opened in a new tab and copied. */
  const hrefFor = useCallback((name: RouteName, params?: Record<string, string>) => {
    const cur = readHash().q;
    const all: Record<string, string> = { ...params };
    for (const k of STICKY) { const v = cur.get(k); if (v && !(params && k in params)) all[k] = v; }
    const qs = Object.keys(all).length ? "?" + new URLSearchParams(all) : "";
    return "#/" + name + qs;
  }, []);
  return { name: r.name, q: r.q, go, setQ, hrefFor };
}

/* ------------------------------------------------------------------ toasts -- */
export interface Toast { id: number; title: string; body?: string }

interface Ctx {
  s: DemoState;
  dispatch: (a: Action) => void;
  toast: (title: string, body?: string) => void;
  toasts: Toast[];
  dismissToast: (id: number) => void;
  holdToasts: (on: boolean) => void;
  theme: "dark" | "light";
  toggleTheme: () => void;
  /** Contract terms and field names beside the plain words. Off by default. */
  terms: boolean;
  toggleTerms: () => void;
  /** Shortcuts on a single letter or symbol (j k x e / ?). On by default; they can be turned off
      because they fire on one key press, which speech input and slips of the hand set off too (WCAG 2.1.4). */
  letterKeys: boolean;
  toggleLetterKeys: () => void;
}
const StoreCtx = createContext<Ctx | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [s, dispatch] = useReducer(reduce, undefined, fresh);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const n = useRef(0);
  const dismissToast = useCallback((id: number) => setToasts(t => t.filter(x => x.id !== id)), []);
  // a toast cannot leave while someone is reading it: hovering or focusing the stack holds them all
  const held = useRef(false);
  const expire = useCallback((id: number) => {
    const tick = () => { if (held.current) setTimeout(tick, 500); else dismissToast(id); };
    setTimeout(tick, 5000);
  }, [dismissToast]);
  const holdToasts = useCallback((on: boolean) => { held.current = on; }, []);
  const toast = useCallback((title: string, body?: string) => {
    const id = ++n.current;
    setToasts(t => [...t, { id, title, body }]);
    expire(id);
  }, [expire]);

  const [terms, setTerms] = useState(() => document.documentElement.dataset.terms === "show");
  const toggleTerms = useCallback(() => {
    setTerms(on => {
      const next = !on;
      if (next) document.documentElement.dataset.terms = "show"; else delete document.documentElement.dataset.terms;
      try { localStorage.setItem("p6-terms", next ? "show" : "hide"); } catch { /* storage may be blocked; the toggle still works */ }
      return next;
    });
  }, []);
  const [letterKeys, setLetterKeys] = useState(() => {
    try { return localStorage.getItem("p6-letter-keys") !== "off"; } catch { return true; }
  });
  const toggleLetterKeys = useCallback(() => {
    setLetterKeys(on => {
      try { localStorage.setItem("p6-letter-keys", on ? "off" : "on"); } catch { /* storage may be blocked; the toggle still works */ }
      return !on;
    });
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

  const value = useMemo(() => ({ s, dispatch, toast, toasts, dismissToast, holdToasts, theme, toggleTheme, terms, toggleTerms, letterKeys, toggleLetterKeys }),
    [s, toast, toasts, dismissToast, holdToasts, theme, toggleTheme, terms, toggleTerms, letterKeys, toggleLetterKeys]);
  return <StoreCtx.Provider value={value}>{children}</StoreCtx.Provider>;
}

export function useStore() {
  const c = useContext(StoreCtx);
  if (!c) throw new Error("useStore outside StoreProvider");
  return c;
}

