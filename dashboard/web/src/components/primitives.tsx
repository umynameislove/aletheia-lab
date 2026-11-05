import { Component, useEffect, useState, type KeyboardEvent, type ReactNode } from "react";
import { fieldTerm, gloss, plain, TERM, type Tone } from "../vocab";

/** The grammar in one element: solid = present in visible evidence, hollow = absent. */
export function Mark({ hollow = false, tone = "" }: { hollow?: boolean; tone?: Tone }) {
  return <span className={`mk ${hollow ? "hollow" : tone}`} aria-hidden="true" />;
}

/** Plain words on top, the contract term in mono beneath. The pair is the unit.
    `field` picks a field-specific wording when one word means different things in different fields. */
export function Term({ k, tone = "", hollow = false, field }: { k: string; tone?: Tone; hollow?: boolean; field?: string }) {
  const own = field ? fieldTerm(field, k) : undefined;
  const known = !!own || !!TERM[k];
  return (
    <span className="term" title={(own ? own[1] : known ? gloss(k) : "") || undefined}>
      <span className="term-plain"><Mark hollow={hollow} tone={tone} />{own ? own[0] : known ? plain(k) : k}</span>
      {known && <code className="term-code">{k}</code>}
    </span>
  );
}

export function Absent({ title, children, code }: { title: string; children?: ReactNode; code?: string }) {
  return (
    <div className="absent">
      <p className="absent-title"><Mark hollow />{title}</p>
      {children && <p>{children}</p>}
      {code && <code className="code">{code}</code>}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd>{children}</kbd>;
}

/** Service IDs can be a prefix plus 64 hex characters. Compact places show prefix + 8…4 of the hex;
    the tooltip holds the full ID and copying the text copies the full ID. Short IDs pass through. */
export function shortId(id: string): string {
  const m = /^(.*?[-_])([0-9a-f]{20,})$/.exec(id);
  if (m) return `${m[1]}${m[2].slice(0, 8)}…${m[2].slice(-4)}`;
  return id.length > 32 ? `${id.slice(0, 20)}…${id.slice(-6)}` : id;
}

export function Id({ v, className }: { v: string; className?: string }) {
  const s = shortId(v);
  if (s === v) return <span className={className}>{v}</span>;
  return (
    <span className={className} title={v} onCopy={e => { e.preventDefault(); e.clipboardData.setData("text/plain", v); }}>{s}</span>
  );
}

/** True at or below this width. Layout that changes behaviour, such as a drawer, needs it in code too. */
export function useNarrow(px: number) {
  const [narrow, setNarrow] = useState(() => window.innerWidth <= px);
  useEffect(() => {
    const media = window.matchMedia(`(max-width: ${px}px)`);
    const on = () => setNarrow(media.matches);
    on();
    media.addEventListener("change", on);
    return () => media.removeEventListener("change", on);
  }, [px]);
  return narrow;
}

/** Arrow keys, Home and End for a role="tablist": they move and activate, and Tab leaves the list. */
export function tabKeys(e: KeyboardEvent<HTMLElement>) {
  const tabs = [...e.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')];
  const at = tabs.indexOf(document.activeElement as HTMLElement);
  if (at < 0) return;
  const next = e.key === "ArrowRight" ? (at + 1) % tabs.length
    : e.key === "ArrowLeft" ? (at - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : -1;
  if (next < 0) return;
  e.preventDefault();
  tabs[next].focus();
  tabs[next].click();
}

/* A screen that throws, or a code chunk that cannot load after an update, must not leave a blank
   page. Only a fixed message is shown: never the error's own text. */
export class ScreenBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="page" id="main" tabIndex={-1}>
        <div className="page-inner page-inner--narrow" role="alert">
          <header className="page-head"><h1>This screen could not be shown</h1>
            <p className="lede">Nothing was changed. Reloading usually fixes it, and also picks up a newer version of the app.</p></header>
          <div className="actions"><button className="btn btn--primary" onClick={() => location.reload()}>Reload</button></div>
        </div>
      </div>
    );
  }
}
