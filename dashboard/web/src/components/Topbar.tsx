import type { ReactNode } from "react";
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
  const { s } = useStore();
  const projectScoped = !s.deletedProject && (name === "project" || name === "investigation" || name === "results");
  const label = name === NOT_FOUND ? "Page not found" : CRUMB[name];
  return <TopbarFrame onMenu={onMenu} onShortcuts={onShortcuts} label={label}
    mode={modeWords(capabilities.deployment_mode)} sample={VIEW.demo_only && projectScoped}>
    {projectScoped && <>
      <a href={hrefFor("project")}>{VIEW.project.display_name}</a>
      <span className="crumb-sep" aria-hidden="true">/</span>
    </>}
  </TopbarFrame>;
}

export function TopbarFrame({ onMenu, onShortcuts, label, mode, sample, children }: {
  onMenu: () => void; onShortcuts?: () => void; label: string; mode: string; sample?: boolean; children?: ReactNode;
}) {
  const { theme, toggleTheme } = useStore();
  return (
    <header className="topbar">
      <button className="icon-btn menu-btn" onClick={onMenu} aria-label="Open navigation"><Menu size={17} /></button>
      <nav className="crumbs" aria-label="Breadcrumb">
        {children}
        <span className="here" aria-current="page" title={label}>{label}</span>
      </nav>

      <div className="top-right">
        {/* where the data on this screen comes from: only one of these is ever true */}
        <span className="mode">{mode}</span>
        {sample && <span className="mode mode--sample" title="demo_only: true">Sample data</span>}
        {onShortcuts && <button className="icon-btn" onClick={onShortcuts} aria-label="Keyboard shortcuts" title="Keyboard shortcuts  ?"><Keyboard size={16} /></button>}
        <button className="icon-btn" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title="Theme">
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>
      </div>
    </header>
  );
}
