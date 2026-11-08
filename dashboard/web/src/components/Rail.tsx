import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  ChevronDown, ChevronRight, ChevronsUpDown, FileCheck2, FlaskConical, FolderInput, Gauge, Home, LayoutList, RotateCcw, ShieldCheck, TriangleAlert, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AFTER_DELETE, capabilities, port, previewSession, useView } from "../service";
import { FEATURES, modeWords } from "../service/capabilities";
import { openedProject } from "../service/opened";
import { useRoute, useStore, type RouteName } from "../store";
import { plainClick, useNarrow } from "./primitives";

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
// out of the way, not out of reach: they can still be opened to read why they are closed
const GATED: NavItem[] = [
  { id: "counterfactual", label: "Counterfactual", icon: FlaskConical, gate: FEATURES.counterfactual.label },
  { id: "prospective", label: "Prospective quality", icon: FlaskConical, gate: FEATURES.prospective.label },
];

export function Rail({ open, onClose, onOpenProject, onResetDemo }: { open: boolean; onClose: () => void; onOpenProject: () => void; onResetDemo: () => void }) {
  const VIEW = useView();
  const { name, go, hrefFor } = useRoute();
  const { s } = useStore();
  const sample = VIEW.demo_only;
  const drawer = useNarrow(900);
  const shut = drawer && !open;
  const ref = useRef<HTMLElement>(null);

  // the drawer takes focus while open, and hands it back to the menu button when it closes
  useEffect(() => {
    if (!drawer || !open) return;
    const before = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>(".rail-x")?.focus();
    return () => { if (before?.isConnected) before.focus(); };
  }, [drawer, open]);
  const onTrap = (e: ReactKeyboardEvent<HTMLElement>) => {
    if (!drawer || e.key !== "Tab" || !ref.current) return;
    const items = [...ref.current.querySelectorAll<HTMLElement>("a[href], button:not(:disabled), summary")].filter(el => el.offsetParent !== null);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  // a deleted project closes the screens that belong to it; gated screens stay reachable on purpose
  const dead = (id: RouteName) => s.deletedProject && (id === "project" || id === "investigation" || id === "results");
  const link = (n: NavItem) => dead(n.id) ? (
    <span key={n.id} className="nav-link is-off" title="Project deleted">
      <n.icon size={16} strokeWidth={1.75} aria-hidden="true" /><span>{n.label}</span><span className="nav-tag">deleted</span>
    </span>
  ) : (
    <a
      key={n.id}
      href={hrefFor(n.id)}
      className={`nav-link${n.gate ? " gated" : ""}`}
      aria-current={name === n.id ? "page" : undefined}
      onClick={e => { if (!plainClick(e)) return; e.preventDefault(); go(n.id); onClose(); }}
    >
      {name === n.id && <span className="nav-ind" aria-hidden="true" />}
      <n.icon size={16} strokeWidth={1.75} aria-hidden="true" />
      <span>{n.label}</span>
      {n.gate ? <span className="nav-tag">{n.gate}</span>
        : n.id === "investigation" && !sample ? <span className="nav-count" aria-label={`${VIEW.claims.length} claims`}>{VIEW.claims.length}</span> : null}
    </a>
  );

  return (
    <>
      {open && <div className="rail-scrim" onClick={onClose} aria-hidden="true" />}
      <aside ref={ref} className={`rail${open ? " open" : ""}`} aria-label="Sections" onKeyDown={onTrap} {...(shut ? { inert: "" } : {})}>
        <div className="rail-head">
          <div className="brand"><span className="brand-mark" aria-hidden="true"><span className="bm-a" /><span className="bm-b" /></span>Aletheia</div>
          <button className="icon-btn rail-x" onClick={onClose} aria-label="Close navigation"><X size={16} /></button>
        </div>

        {s.deletedProject ? (
          <p className="proj-empty">No project open. {AFTER_DELETE}</p>
        ) : sample && port.kind === "live" ? (
          // a project imported but not yet asked about has no result to show, yet it is not "not connected"
          <p className="proj-empty">{openedProject()
            ? <><b>Project saved.</b> Ask a question to open it.</>
            : <><b>No project open yet.</b> The other screens show sample data until you connect one.</>}</p>
        ) : <button className="proj" onClick={onOpenProject} aria-label={`Project: ${VIEW.project.display_name}. Show project details`}>
          <span className="proj-av" aria-hidden="true">{initials(VIEW.project.display_name)}</span>
          <span className="proj-txt">
            <span className="proj-name">{VIEW.project.display_name}</span>
            <span className="proj-id">{sample ? "Sample data" : `${VIEW.claims.length} claim${VIEW.claims.length === 1 ? "" : "s"}`}</span>
          </span>
          <ChevronsUpDown size={14} aria-hidden="true" />
        </button>}

        <nav className="nav" aria-label="Main">
          {WORK.map(link)}
          <details className="nav-more" open={GATED.some(g => g.id === name) || undefined}>
            <summary><ChevronRight size={13} aria-hidden="true" />Not available yet</summary>
            {GATED.map(link)}
          </details>
        </nav>

        <div className="rail-foot">
          <Account onResetDemo={onResetDemo} />
        </div>
      </aside>
    </>
  );
}

/* The local app has no accounts: this row says so instead of naming a person. A hosted session (K-03)
   replaces it with the signed-in identity from the server. */
function Account({ onResetDemo }: { onResetDemo: () => void }) {
  const { s, dispatch, toast, terms, toggleTerms } = useStore();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const panel = useId();
  useEffect(() => {
    if (!open) return;
    const away = (e: globalThis.MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  // Esc or a chosen action closes it and hands focus back to the button; a click elsewhere leaves focus where it went
  const close = () => { setOpen(false); btn.current?.focus(); };
  // handled here, not on document: one Esc closes this, not also the drawer around it (App listens on window)
  const onKey = (e: ReactKeyboardEvent) => { if (e.key === "Escape" && open) { e.stopPropagation(); close(); } };
  const mode = modeWords(capabilities.deployment_mode);

  /* A disclosure, not a menu: the panel follows its button, so Tab goes from the button into it */
  return (
    <div className="acct-wrap" ref={ref} onKeyDown={onKey}>
      <button ref={btn} className="acct" aria-expanded={open} aria-controls={open ? panel : undefined} onClick={() => setOpen(o => !o)}>
        <span className="acct-name">{mode}</span>
        <span className="acct-plan">no account</span>
        <ChevronDown size={16} className="acct-chev" aria-hidden="true" />
      </button>
      {open && (
        <div className="acct-menu" id={panel} role="group" aria-label="Settings on this device">
          <div className="acct-local"><span className="pulse" aria-hidden="true" />No account. Nothing leaves this machine.</div>
          <button className="demo-btn" role="switch" aria-checked={terms} onClick={toggleTerms}>
            Show contract terms
            <span className={`switch${terms ? " on" : ""}`} aria-hidden="true"><span className="knob" /></span>
          </button>
          {/* demo controls change in-memory demo state only; a Live store is never reset from here */}
          {port.kind === "demo" && <>
          <button
            className="demo-btn" role="switch" aria-checked={s.failed}
            onClick={() => {
              dispatch({ type: "toggleFailed" });
              toast(s.failed ? "Back to the completed run" : "Previewing a failed run");
            }}
          >
            <TriangleAlert size={14} aria-hidden="true" />Preview a failed run
            <span className={`switch${s.failed ? " on" : ""}`} aria-hidden="true"><span className="knob" /></span>
          </button>
          <button
            className="demo-btn"
            // it clears review marks and questions that exist nowhere else, so the app asks first
            onClick={() => { close(); onResetDemo(); }}
          >
            <RotateCcw size={14} aria-hidden="true" />Reset demo data
          </button>
          <button className="demo-btn" onClick={() => { close(); previewSession({ status: "expired" }); }}>
            <TriangleAlert size={14} aria-hidden="true" />Preview: session ended
          </button>
          <button className="demo-btn" onClick={() => { close(); previewSession({ status: "revoked" }); }}>
            <TriangleAlert size={14} aria-hidden="true" />Preview: access removed
          </button>
          </>}
        </div>
      )}
    </div>
  );
}
