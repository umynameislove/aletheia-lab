import { ArrowRight, Cpu, Keyboard, Menu, Moon, Sun } from "lucide-react";
import { port, useView } from "../service";
import { useRoute, useStore, type RouteName } from "../store";
import { Id } from "./primitives";

export const CRUMB: Record<RouteName, string> = {
  start: "Start",
  connect: "Connect a project",
  project: "Project overview",
  investigation: "Investigation",
  results: "Results & reproduction",
  retention: "Data & retention",
  counterfactual: "Counterfactual",
  prospective: "Prospective quality",
};

export function Topbar({ onMenu, onShortcuts }: { onMenu: () => void; onShortcuts: () => void }) {
  const VIEW = useView();
  const { name, go } = useRoute();
  const { theme, toggleTheme, s } = useStore();
  const projectScoped = !s.deletedProject && (name === "project" || name === "investigation" || name === "results");

  return (
    <header className="topbar">
      <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Open navigation"><Menu size={17} /></button>
      <nav className="crumbs" aria-label="Breadcrumb">
        {projectScoped && <>
          <button onClick={() => go("project")}>{VIEW.project.display_name}</button>
          <span className="crumb-sep" aria-hidden="true">|</span>
        </>}
        <span className="here" aria-current="page">{CRUMB[name]}</span>
      </nav>

      <div className="top-right">
        {/* read-only facts about the view on screen, verbatim from the service */}
        <span className="chip snap" title="baseline → current snapshot">
          <Id v={VIEW.snapshot.baseline_id} />
          <ArrowRight size={11} className="arrow" aria-hidden="true" />
          <Id v={VIEW.snapshot.id} />
        </span>
        <span className="chip prov" title={`runtime.model ${VIEW.runtime.model}`}>
          <Cpu size={12} aria-hidden="true" />{VIEW.runtime.provider}<span className="chip-sep" aria-hidden="true">·</span>external_call {String(VIEW.runtime.external_call)}
        </span>
        <span className="chip" title="visibility of this view">{VIEW.visibility}</span>
        {port.kind === "live" && <span className="chip" title="LiveProductPort → local ProductService">LIVE</span>}
        {VIEW.demo_only && <span className="chip demo" title={`demo_only · ${port.kind === "demo" ? "DemoProductPort" : "LiveProductPort"}`}>DEMO</span>}
        <button className="icon-btn" onClick={onShortcuts} aria-label="Keyboard shortcuts" title="Keyboard shortcuts  ?"><Keyboard size={16} /></button>
        <button className="icon-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title="Theme">
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}
