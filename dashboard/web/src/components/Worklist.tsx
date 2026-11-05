import { Check, ChevronLeft, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import type { Claim, ProductView } from "../types";
import type { Page } from "../data/paging";
import { reviewOf, reviewStats, useStore } from "../store";
import { gloss, plain, fieldTerm, supportTone } from "../vocab";
import { Mark } from "./primitives";

export function ReviewBar({ v }: { v: ProductView }) {
  const { s, dispatch } = useStore();
  if (!v.claims.length) return null;
  const ids = v.claims.map(c => c.id);
  const st = reviewStats(s, ids);
  const pct = (n: number) => (st.total ? (n / st.total) * 100 : 0);
  return (
    <div className="review-bar">
      <div className="meter" role="img" aria-label={`${st.done} of ${st.total} reviewed`}>
        <span className="m-ok" style={{ width: pct(st.checked) + "%" }} />
        <span className="m-dis" style={{ width: pct(st.disputed) + "%" }} />
      </div>
      <span className="txt"><b>{st.done}</b> of <b>{st.total}</b> reviewed{st.disputed ? <> · <b>{st.disputed}</b> disputed</> : null}</span>
      {st.done > 0 && <button className="btn btn--sm btn--ghost" onClick={() => dispatch({ type: "reviewReset" })}>Reset review</button>}
    </div>
  );
}

const count = (n: number, one: string, many = one + "s") => `${n} ${n === 1 ? one : many}`;

/* A claim first: what it says, whether it is backed, how many sources it has. IDs and contract
   terms live in the reader. Every row in the result stays reachable through the pager. */
export function Worklist({ v, page, selectedId, onSelect, onPage, onClearFilters, inline }: {
  v: ProductView; page: Page<Claim>; selectedId: string | null;
  onSelect: (c: Claim) => void; onPage: (p: number) => void; onClearFilters: () => void;
  /** the reading layout opens the source under the selected claim */
  inline?: (c: Claim) => ReactNode;
}) {
  const { s, dispatch } = useStore();

  // a result with no claims is not a filter miss: say so plainly
  if (!v.claims.length) {
    return <div className="empty"><h3>No claims in this result</h3><p>The answer above is all this result states.</p></div>;
  }
  if (!page.total) {
    return (
      <div className="empty">
        <h3>Nothing matches</h3>
        <p>No claim matches the search and filter. {count(v.claims.length, "claim")} in this result are hidden by them.</p>
        <button className="btn" onClick={onClearFilters}>Clear search and filter</button>
      </div>
    );
  }

  return (
    <>
      <ul className="wl" id="worklist" aria-label="Claims">
        {page.rows.map(c => {
          const r = reviewOf(s, c.id), on = c.id === selectedId;
          const support = fieldTerm("support", c.support)?.[0] ?? plain(c.support);
          const tone = supportTone(c.support);
          return (
            <li key={c.id} className={`wl-row${r !== "unchecked" ? " reviewed" : ""}${on ? " on" : ""}`}>
              <button
                className={`check ${r}`}
                aria-label={`${r === "checked" ? "Reviewed" : r === "disputed" ? "Disputed" : "Not reviewed yet"}: ${c.text.slice(0, 80)}`}
                aria-pressed={r === "checked"}
                title={r === "checked" ? "Reviewed. Click to clear." : "Mark as reviewed  (x)"}
                onClick={() => dispatch({ type: "review", id: c.id, value: r === "checked" ? "unchecked" : "checked" })}
              >
                {r === "checked" ? <Check size={13} strokeWidth={3} /> : r === "disputed" ? <span className="check-mark">!</span> : null}
              </button>

              <button className="wl-main" data-claim={c.id} aria-current={on ? "true" : undefined} onClick={() => onSelect(c)}>
                <span className="wl-text">{c.text}</span>
                <span className="wl-meta">
                  <span className="wl-fact" title={gloss(c.support) || undefined}>{tone ? <Mark tone={tone} /> : <Mark hollow />}{support}</span>
                  <span className="wl-fact">{c.citation_ids.length ? <Mark tone="accent" /> : <Mark hollow />}{c.citation_ids.length ? count(c.citation_ids.length, "source") : "cites no source"}</span>
                  {c.counterevidence_ids.length > 0 && <span className="wl-fact"><Mark tone="contra" />{count(c.counterevidence_ids.length, "source")} against</span>}
                  {c.claim_type !== "evidence_statement" && <span className="wl-fact">{plain(c.claim_type)}</span>}
                  {r !== "unchecked" && <span className="wl-fact"><Mark tone={r === "checked" ? "tick" : "partial"} />{r === "checked" ? "Reviewed" : "Disputed"}</span>}
                </span>
                {r === "disputed" && s.notes[c.id]?.trim() && <span className="objection"><b>Your objection:</b> {s.notes[c.id]}</span>}
              </button>
              {on && inline?.(c)}
            </li>
          );
        })}
      </ul>
      {page.pages > 1 && <Pager page={page} noun="claims" onPage={onPage} />}
    </>
  );
}

export function Pager<T>({ page, noun, onPage }: { page: Page<T>; noun: string; onPage: (p: number) => void }) {
  return (
    <nav className="pager" aria-label={`Pages of ${noun}`}>
      <span className="shown" aria-live="polite">{page.first.toLocaleString()}–{page.last.toLocaleString()} of {page.total.toLocaleString()} {noun}</span>
      <span className="pager-btns">
        <button className="btn btn--sm" disabled={page.page <= 1} onClick={() => onPage(page.page - 1)}><ChevronLeft size={14} aria-hidden="true" />Previous</button>
        <span className="shown">Page {page.page.toLocaleString()} of {page.pages.toLocaleString()}</span>
        <button className="btn btn--sm" disabled={page.page >= page.pages} onClick={() => onPage(page.page + 1)}>Next<ChevronRight size={14} aria-hidden="true" /></button>
      </span>
    </nav>
  );
}
