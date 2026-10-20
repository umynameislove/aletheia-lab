import type { ReactNode } from "react";
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
      <h3><Mark hollow />{title}</h3>
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
