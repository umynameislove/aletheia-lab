import { Check } from "lucide-react";
import type { Claim, ProductView } from "../types";
import { reviewOf, reviewStats, useStore } from "../store";
import { plain, supportTone } from "../vocab";
import { Id, Mark, Term } from "./primitives";

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

export function Worklist({ v, rows, selectedId, onSelect, onClearFilters }: {
  v: ProductView; rows: Claim[]; selectedId: string | null;
  onSelect: (c: Claim) => void; onClearFilters: () => void;
}) {
  const { s, dispatch } = useStore();

  // a result with no claims is not a filter miss — say so plainly
  if (!v.claims.length) {
    return <div className="empty"><h3>No claims in this result</h3></div>;
  }
  if (!rows.length) {
    return (
      <div className="empty">
        <h3>Nothing matches</h3>
        <p>No claim matches the search and filter.</p>
        <button className="btn" onClick={onClearFilters}>Clear search and filter</button>
      </div>
    );
  }

  return (
    <>
      <ul className="wl" id="worklist" aria-label="Claims">
        {rows.map(c => {
          const r = reviewOf(s, c.id), on = c.id === selectedId;
          return (
            <li
              key={c.id}
              className={`wl-row${r !== "unchecked" ? " reviewed" : ""}`}
              data-claim={c.id}
              tabIndex={0}
              aria-selected={on}
              onClick={e => { if ((e.target as HTMLElement).closest("button")) return; onSelect(c); }}
              onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(c); } }}
            >
              <button
                className={`check ${r}`}
                aria-label={`${r === "checked" ? "Reviewed" : r === "disputed" ? "Disputed" : "Not reviewed yet"} — ${c.id}`}
                title={r === "checked" ? "Reviewed — click to clear" : "Mark as reviewed  (x)"}
                onClick={() => dispatch({ type: "review", id: c.id, value: r === "checked" ? "unchecked" : "checked" })}
              >
                {r === "checked" ? <Check size={13} strokeWidth={3} /> : r === "disputed" ? <span style={{ font: "700 12px/1 var(--font-sans)" }}>!</span> : null}
              </button>

              <div style={{ minWidth: 0 }}>
                <div className="wl-top">
                  <code className="wl-id"><Id v={c.id} /></code>
                  <span className="tag" title={`claim_type ${c.claim_type}`}><Mark hollow />{plain(c.claim_type)}</span>
                  {v.conversation.turns.length > 1 && <span className="tag mono-s" title="The turn this claim was made in"><Id v={c.turn_id} /></span>}
                  {r !== "unchecked" && <span className="tag"><Mark tone={r === "checked" ? "tick" : "partial"} />{r === "checked" ? "reviewed" : "disputed"}</span>}
                </div>
                <p className="wl-text">{c.text}</p>
                {r === "disputed" && s.notes[c.id]?.trim() && <p className="objection"><b>Your objection:</b> {s.notes[c.id]}</p>}
                <div className="wl-cite">
                  {c.citation_ids.length
                    ? <><Mark tone="ok" />cites {c.citation_ids.map(id => <code key={id}>{id}</code>)}</>
                    : <><Mark hollow />cites nothing</>}
                </div>
              </div>

              <div className="wl-states">
                <Term k={c.epistemic_state} tone="accent" />
                <Term k={c.support} field="support" tone={supportTone(c.support)} hollow={!supportTone(c.support)} />
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
