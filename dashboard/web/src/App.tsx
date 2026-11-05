import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Info } from "lucide-react";
import { port, useView } from "./service";
import { NOT_FOUND, useRoute, useStore } from "./store";
import { Rail } from "./components/Rail";
import { CRUMB, Topbar } from "./components/Topbar";
import { Investigation } from "./components/Investigation";
import { Start } from "./components/Start";
import { Counterfactual, NotFound, Prospective, ProjectDeleted } from "./components/Gated";
import { Kbd, ScreenBoundary } from "./components/primitives";
import { Modal } from "./components/Modal";

// the two screens people land on are in the main file; the rest load when opened
const Connect = lazy(() => import("./components/Connect").then(m => ({ default: m.Connect })));
const Project = lazy(() => import("./components/Project").then(m => ({ default: m.Project })));
const Results = lazy(() => import("./components/Results").then(m => ({ default: m.Results })));
const Retention = lazy(() => import("./components/Retention").then(m => ({ default: m.Retention })));

type ModalKind = "shortcuts" | "project" | null;

export function App() {
  const VIEW = useView();
  const { name, go } = useRoute();
  const { toasts, s } = useStore();
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
  useEffect(() => {
    if (!unsaved) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [unsaved]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, select");
      if (e.key === "Escape") { setModal(null); setRailOpen(false); }
      else if (e.key === "?" && !typing) { e.preventDefault(); setModal("shortcuts"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <>
      <a className="skip" href="#main" onClick={e => { e.preventDefault(); document.getElementById("main")?.focus(); }}>Skip to content</a>
      <div className="app">
        <Rail open={railOpen} onClose={() => setRailOpen(false)} onOpenProject={() => setModal("project")} />
        <div className="shell">
          <Topbar onMenu={() => setRailOpen(true)} onShortcuts={() => setModal("shortcuts")} />
          <main className="content">
            <ScreenBoundary key={name}>
              <Suspense fallback={<div className="page" id="main" tabIndex={-1}><p className="page-loading" role="status">Loading…</p></div>}>
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

      <div className="toasts" aria-live="polite">
        {toasts.map(t => (
          <div className="toast" key={t.id}><Info size={15} aria-hidden="true" /><div><b>{t.title}</b>{t.body}</div></div>
        ))}
      </div>

      {modal === "shortcuts" && (
        <Modal title="Keyboard shortcuts" onClose={() => setModal(null)}>
          {([
            [["j", "↓"], "Next claim"], [["k", "↑"], "Previous claim"], [["x"], "Mark reviewed"],
            [["e"], "Open source"], [["/"], "Search"], [["Esc"], "Clear / close"], [["?"], "This list"],
          ] as [string[], string][]).map(([keys, label]) => (
            <div className="shortcut" key={label}>{label}<span className="keys-r">{keys.map(k => <Kbd key={k}>{k}</Kbd>)}</span></div>
          ))}
        </Modal>
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
