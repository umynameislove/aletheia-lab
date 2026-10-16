import { useEffect, useState } from "react";
import { Info } from "lucide-react";
import { useView } from "./service";
import { useRoute, useStore } from "./store";
import { Rail } from "./components/Rail";
import { Topbar } from "./components/Topbar";
import { Investigation } from "./components/Investigation";
import { Start } from "./components/Start";
import { Project } from "./components/Project";
import { Results } from "./components/Results";
import { Retention } from "./components/Retention";
import { Counterfactual, Prospective, ProjectDeleted } from "./components/Gated";
import { Connect } from "./components/Connect";
import { Kbd } from "./components/primitives";
import { Modal } from "./components/Modal";

type ModalKind = "shortcuts" | "project" | null;

export function App() {
  const VIEW = useView();
  const { name, go } = useRoute();
  const { toasts, s } = useStore();
  const [railOpen, setRailOpen] = useState(false);
  const [modal, setModal] = useState<ModalKind>(null);

  useEffect(() => {
    if (!location.hash) location.replace("#/investigation");
  }, []);

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
            {s.deletedProject && (name === "project" || name === "investigation" || name === "results")
              ? <ProjectDeleted />
              : {
                start: <Start />, connect: <Connect />, project: <Project />, investigation: <Investigation />,
                results: <Results />, retention: <Retention />, counterfactual: <Counterfactual />, prospective: <Prospective />,
              }[name]}
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
            [["e"], "Open evidence"], [["/"], "Search"], [["Esc"], "Clear / close"], [["?"], "This list"],
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
            <button className="btn btn--primary" onClick={() => { setModal(null); go("connect"); }}>Connect another</button>
          </div>
        </Modal>
      )}
    </>
  );
}
