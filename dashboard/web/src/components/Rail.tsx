import { useEffect, useRef, useState } from "react";
import {
  ChevronDown, ChevronsUpDown, FileCheck2, FlaskConical, FolderInput, Gauge, Home, LayoutList, RotateCcw, ShieldCheck, TriangleAlert, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AFTER_DELETE, demoPort, port, useResultSwitch, useView } from "../service";
import { useRoute, useStore, type RouteName } from "../store";
import { Id } from "./primitives";

/** Two-letter avatar from the project's own name, never hard-coded. */
const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]!.toUpperCase()).join("") || "P";

interface NavItem { id: RouteName; label: string; icon: LucideIcon; gate?: string }

const WORK: NavItem[] = [
  { id: "start", label: "Start", icon: Home },
  { id: "connect", label: "Connect a project", icon: FolderInput },
  { id: "project", label: "Project overview", icon: Gauge },
  { id: "investigation", label: "Investigation", icon: LayoutList },
  { id: "results", label: "Results & reproduction", icon: FileCheck2 },
  { id: "retention", label: "Data & retention", icon: ShieldCheck },
];
// gated, not dead: they stay reachable so someone can open them and read why they are closed
const GATED: NavItem[] = [
  { id: "counterfactual", label: "Counterfactual", icon: FlaskConical, gate: "gated" },
  { id: "prospective", label: "Prospective quality", icon: FlaskConical, gate: "out of scope" },
];

export function Rail({ open, onClose, onOpenProject }: { open: boolean; onClose: () => void; onOpenProject: () => void }) {
  const VIEW = useView();
  const { name, go } = useRoute();
  const { s } = useStore();

  // a deleted project closes the screens that belong to it; gated screens stay reachable on purpose
  const dead = (id: RouteName) => s.deletedProject && (id === "project" || id === "investigation" || id === "results");
  const link = (n: NavItem) => (
    <button
      key={n.id}
      className={`nav-link${n.gate ? " gated" : ""}`}
      aria-current={name === n.id ? "page" : undefined}
      disabled={dead(n.id)}
      title={dead(n.id) ? "project deleted" : undefined}
      onClick={() => { go(n.id); onClose(); }}
    >
      {name === n.id && <span className="nav-ind" aria-hidden="true" />}
      <n.icon size={16} strokeWidth={1.75} aria-hidden="true" />
      <span>{n.label}</span>
      {n.gate
        ? <span className="nav-tag">{n.gate}</span>
        : dead(n.id) ? <span className="nav-tag">deleted</span>
          : n.id === "investigation" ? <span className="nav-count">{VIEW.claims.length}</span> : null}
    </button>
  );

  return (
    <>
      {open && <div className="rail-scrim" onClick={onClose} aria-hidden="true" />}
      <aside className={`rail${open ? " open" : ""}`} aria-label="Sections">
        <div className="rail-head">
          <div className="brand"><span className="brand-mark" aria-hidden="true"><span className="bm-a" /><span className="bm-b" /></span>Aletheia<span className="brand-tag">P6</span></div>
          <button className="icon-btn rail-x" onClick={onClose} aria-label="Close navigation"><X size={16} /></button>
        </div>

        {s.deletedProject ? (
          <p className="proj-empty">No project — deleted in this session. {AFTER_DELETE}</p>
        ) : <button className="proj" onClick={onOpenProject} aria-label={`Project: ${VIEW.project.display_name}. Show project details`}>
          <span className="proj-av" aria-hidden="true">{initials(VIEW.project.display_name)}</span>
          <span className="proj-txt">
            <span className="proj-name">{VIEW.project.display_name}</span>
            <Id className="proj-id" v={VIEW.project.id} />
          </span>
          <ChevronsUpDown size={14} aria-hidden="true" />
        </button>}

        <nav className="nav" aria-label="Main">
          <div className="nav-label">Workspace</div>
          {WORK.map(link)}
          <div className="nav-label">Gated</div>
          {GATED.map(link)}
        </nav>

        <div className="rail-foot">
          <Account />
        </div>
      </aside>
    </>
  );
}

/* The account row, laid out like Claude's: round avatar, name · plan, chevron. The demo controls
   and the local-only note live in its menu instead of taking room in the rail. */
function Account() {
  const { go } = useRoute();
  const { s, dispatch, toast } = useStore();
  const { resetResult } = useResultSwitch();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  return (
    <div className="acct-wrap" ref={ref}>
      {open && (
        <div className="acct-menu" role="menu" aria-label="Account and demo controls">
          <div className="acct-local"><span className="pulse" aria-hidden="true" />Local only — nothing leaves this machine</div>
          {/* demo controls change in-memory demo state only; a Live store is never reset from here */}
          {port.kind === "demo" && <>
          <button
            className="demo-btn" role="menuitemcheckbox" aria-checked={s.failed}
            onClick={() => {
              dispatch({ type: "toggleFailed" });
              toast(s.failed ? "Back to the completed run" : "Previewing a failed run");
            }}
          >
            <TriangleAlert size={14} aria-hidden="true" />Preview a failed run
            <span className={`switch${s.failed ? " on" : ""}`} aria-hidden="true"><span className="knob" /></span>
          </button>
          <button
            className="demo-btn" role="menuitem"
            onClick={() => { demoPort.reset(); void resetResult(); dispatch({ type: "reset" }); go("investigation"); setOpen(false); toast("Demo data reset"); }}
          >
            <RotateCcw size={14} aria-hidden="true" />Reset demo data
          </button>
          </>}
        </div>
      )}
      <button className="acct" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span className="acct-av" aria-hidden="true">Q</span>
        <span className="acct-name">Quân</span>
        <span className="acct-plan">· {port.kind === "live" ? "Live" : "Demo"}</span>
        <ChevronDown size={16} className="acct-chev" aria-hidden="true" />
      </button>
    </div>
  );
}
