import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Info, X } from "lucide-react";
import { demoPort, port, useResultSwitch, useView } from "./service";
import { warnBeforeUnload } from "./service/unsaved";
import { NOT_FOUND, useRoute, useStore } from "./store";
import { Rail } from "./components/Rail";
import { CRUMB, Topbar } from "./components/Topbar";
import { Start } from "./components/Start";
import { Counterfactual, NotFound, Prospective, ProjectDeleted } from "./components/Gated";
import { Kbd, ScreenBoundary } from "./components/primitives";
import { ConfirmDialog, Modal } from "./components/Modal";

// Start is where people land, so it is in the main file; the rest load when opened. Investigation with its
// reader is the largest screen, and a deep link to it pays one small request instead of every visit paying for it
const Investigation = lazy(() => import("./components/Investigation").then(m => ({ default: m.Investigation })));
const Connect = lazy(() => import("./components/Connect").then(m => ({ default: m.Connect })));
const Project = lazy(() => import("./components/Project").then(m => ({ default: m.Project })));
const Results = lazy(() => import("./components/Results").then(m => ({ default: m.Results })));
const Retention = lazy(() => import("./components/Retention").then(m => ({ default: m.Retention })));

type ModalKind = "shortcuts" | "project" | "reset" | null;

/* Stands in while a screen's code loads. Moving to a new screen focuses this; when the screen (or its
   error) replaces it, focus would fall back to the page, so it is handed on to what took its place. */
function Loading() {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => () => {
    // still in the page at this point, so it can tell whether it had focus; what replaces it is in
    // the page once this commit ends, which a microtask waits for even in a background tab
    if (ref.current && ref.current === document.activeElement) {
      queueMicrotask(() => document.getElementById("main")?.focus({ preventScroll: true }));
    }
  }, []);
  return <div className="page" id="main" tabIndex={-1} ref={ref}><p className="page-loading" role="status">Loading…</p></div>;
}

export function App() {
  const VIEW = useView();
  const { name, go } = useRoute();
  const { toasts, dismissToast, holdToasts, s, dispatch, toast, letterKeys, toggleLetterKeys } = useStore();
  const { resetResult } = useResultSwitch();
  const [railOpen, setRailOpen] = useState(false);
  const [modal, setModal] = useState<ModalKind>(null);

  // the first screen says where to begin; it never opens the sample as if it were someone's result
  useEffect(() => {
    if (!location.hash) location.replace("#/start");
  }, []);

  // a new screen is a new page: name it, and move a keyboard or screen reader user to its content
  const firstRoute = useRef(true);
  useEffect(() => {
    document.title = `${name === NOT_FOUND ? "Page not found" : CRUMB[name]} · Aletheia`;
    if (firstRoute.current) { firstRoute.current = false; return; }
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [name]);

  // review marks live only in this tab, so say so before they are lost
  const unsaved = Object.keys(s.review).length > 0;
  useEffect(() => (unsaved ? warnBeforeUnload(window) : undefined), [unsaved]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, select");
      if (e.key === "Escape") { setModal(null); setRailOpen(false); }
      else if (e.key === "?" && !typing && letterKeys) { e.preventDefault(); setModal("shortcuts"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [letterKeys]);

  return (
    <>
      <a className="skip" href="#main" onClick={e => { e.preventDefault(); document.getElementById("main")?.focus(); }}>Skip to content</a>
      <div className="app">
        <Rail open={railOpen} onClose={() => setRailOpen(false)} onOpenProject={() => setModal("project")} onResetDemo={() => setModal("reset")} />
        <div className="shell">
          <Topbar onMenu={() => setRailOpen(true)} onShortcuts={() => setModal("shortcuts")} />
          <main className="content">
            <ScreenBoundary key={name}>
              <Suspense fallback={<Loading />}>
                {name === NOT_FOUND ? <NotFound /> : s.deletedProject && (name === "project" || name === "investigation" || name === "results")
                  ? <ProjectDeleted />
                  : {
                    start: <Start />, connect: <Connect />, project: <Project />, investigation: <Investigation />,
                    results: <Results />, retention: <Retention />, counterfactual: <Counterfactual />, prospective: <Prospective />,
                  }[name]}
              </Suspense>
            </ScreenBoundary>
          </main>
        </div>
      </div>

      <div className="toasts" aria-live="polite" onMouseEnter={() => holdToasts(true)} onMouseLeave={() => holdToasts(false)} onFocus={() => holdToasts(true)} onBlur={() => holdToasts(false)}>
        {toasts.map(t => (
          <div className="toast" key={t.id}>
            <Info size={15} aria-hidden="true" /><div><b>{t.title}</b>{t.body}</div>
            <button className="icon-btn" aria-label={`Dismiss: ${t.title}`} onClick={() => dismissToast(t.id)}><X size={14} /></button>
          </div>
        ))}
      </div>

      {modal === "shortcuts" && (
        <Modal title="Keyboard shortcuts" onClose={() => setModal(null)}>
          {/* one key press is enough to fire these, so they can be turned off; zoom keys work only while
              focus is in the lineage drawing, and Esc and the arrows are not letters, so they stay */}
          <button className="demo-btn" role="switch" aria-checked={letterKeys} onClick={toggleLetterKeys}>
            Single-key shortcuts (j k x e / ?)
            <span className={`switch${letterKeys ? " on" : ""}`} aria-hidden="true"><span className="knob" /></span>
          </button>
          {([
            [["j", "↓"], "Next claim"], [["k", "↑"], "Previous claim"], [["x"], "Mark reviewed"],
            [["e"], "Open source"], [["/"], "Search"], [["+", "-", "0"], "In the lineage: zoom in, out, reset"], [["Esc"], "Clear / close"], [["?"], "This list"],
          ] as [string[], string][]).map(([keys, label]) => (
            <div className="shortcut" key={label}>{label}<span className="keys-r">{keys.map(k => <Kbd key={k}>{k}</Kbd>)}</span></div>
          ))}
        </Modal>
      )}

      {/* rendered here, not in the rail: on narrow screens the rail is a moved drawer, which would trap a fixed dialog inside it */}
      {modal === "reset" && (
        <ConfirmDialog title="Reset the demo data?" action="Reset demo data" onCancel={() => setModal(null)}
          onConfirm={() => { demoPort?.reset(); void resetResult(); dispatch({ type: "reset" }); setModal(null); go("start"); toast("Demo data reset"); }}>
          <b>This cannot be undone</b>
          The sample goes back to how it was packaged. Your review marks, objections and the questions asked in this tab are cleared.
        </ConfirmDialog>
      )}

      {modal === "project" && (
        <Modal title="Project" onClose={() => setModal(null)}>
          <dl className="kv">
            <dt>name</dt><dd>{VIEW.project.display_name}</dd>
            <dt>id</dt><dd>{VIEW.project.id}</dd>
            <dt>mode</dt><dd>{VIEW.mode}</dd>
            <dt>snapshot</dt><dd>{VIEW.snapshot.id}</dd>
            <dt>baseline</dt><dd>{VIEW.snapshot.baseline_id}</dd>
          </dl>
          <div style={{ display: "flex", gap: "var(--s2)", justifyContent: "flex-end", marginTop: "var(--s5)" }}>
            <button className="btn" onClick={() => setModal(null)}>Close</button>
            {port.kind === "live" && <button className="btn btn--primary" onClick={() => { setModal(null); go("connect"); }}>Connect another</button>}
          </div>
        </Modal>
      )}
    </>
  );
}
