import { Keyboard, Menu, Moon, Sun } from "lucide-react";
import { capabilities, useView } from "../service";
import { modeWords } from "../service/capabilities";
import { NOT_FOUND, useRoute, useStore, type RouteName } from "../store";

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
  const { name, hrefFor } = useRoute();
  const { theme, toggleTheme, s } = useStore();
  const projectScoped = !s.deletedProject && (name === "project" || name === "investigation" || name === "results");
  const sample = VIEW.demo_only;

  return (
    <header className="topbar">
      <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Open navigation"><Menu size={17} /></button>
      <nav className="crumbs" aria-label="Breadcrumb">
        {projectScoped && <>
          <a href={hrefFor("project")}>{VIEW.project.display_name}</a>
          <span className="crumb-sep" aria-hidden="true">/</span>
        </>}
        <span className="here" aria-current="page" title={name === NOT_FOUND ? "Page not found" : CRUMB[name]}>{name === NOT_FOUND ? "Page not found" : CRUMB[name]}</span>
      </nav>

      <div className="top-right">
        {/* where the data on this screen comes from: only one of these is ever true */}
        <span className="mode" title={`deployment_mode ${capabilities.deployment_mode}`}>{modeWords(capabilities.deployment_mode)}</span>
        {sample && projectScoped && <span className="mode mode--sample" title="demo_only: true">Sample data</span>}
        <button className="icon-btn" onClick={onShortcuts} aria-label="Keyboard shortcuts" title="Keyboard shortcuts  ?"><Keyboard size={16} /></button>
        <button className="icon-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title="Theme">
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}
