import { ChevronRight, FileText, Lock, MousePointerClick, X } from "lucide-react";
import type { Claim, ProductView, Turn } from "../types";
import { reviewOf, useStore } from "../store";
import { evidenceRequests, hasCounterfactualMarker } from "../data/view";
import { ABSTAIN_CEILING, LADDER, supportTone } from "../vocab";
import { Kbd, Mark, Term } from "./primitives";

export function Inspector({ v, turn, claim, evidenceId, open, onClose }: {
  v: ProductView; turn: Turn | undefined; claim: Claim | null; evidenceId: string | null; open: boolean; onClose: () => void;
}) {
  const ev = v.evidence.find(e => e.id === evidenceId)
    ?? (claim ? v.evidence.find(e => e.id === claim.citation_ids[0]) : undefined)
    ?? null;
  const citedBy = ev ? v.claims.filter(c => c.citation_ids.includes(ev.id)) : [];

  return (
    <>
      <div className="insp-scrim" hidden={!open} onClick={onClose} aria-hidden="true" />
      <aside className={`insp${open ? " open" : ""}`} aria-label="Inspector">
        <div className="insp-head">
          <span className="eyebrow">{claim ? "Claim" : ev ? "Evidence" : "Inspector"}</span>
          {(claim || ev) && <button className="icon-btn" onClick={onClose} aria-label="Clear selection" title="Clear selection  Esc"><X size={15} /></button>}
        </div>
        <div className="insp-body">
          {!claim && !ev ? <EmptyInspector /> : <>
            {claim && <ClaimSection claim={claim} />}
            {claim && <Ceiling claim={claim} caveat={v.result.caveat} />}
            {claim && <Review claim={claim} />}
            {ev && (
              <section className="insp-sec">
                <div className="sec-head"><span className="eyebrow">Cited evidence</span>
                  {citedBy.length > 0 && <span className="aside">cited by {citedBy.length} claim{citedBy.length === 1 ? "" : "s"}</span>}</div>
                <div className="quote">
                  <span className="quote-bar" aria-hidden="true" />
                  <div className="quote-title">{ev.title}</div>
                  <blockquote>{ev.text}</blockquote>
                  <div className="quote-src" title="An immutable P3 record — not a file path"><FileText size={12} aria-hidden="true" />{ev.reproduction_ref.record_kind} · {ev.reproduction_ref.record_id}</div>
                </div>
                <details className="meta-toggle">
                  <summary><ChevronRight size={13} aria-hidden="true" />Source record</summary>
                  <dl className="kv">
                    <dt>id</dt><dd>{ev.id}</dd>
                    <dt>kind</dt><dd>{ev.kind}</dd>
                    <dt>reproduction_ref</dt><dd>{ev.reproduction_ref.record_kind} · {ev.reproduction_ref.record_id}</dd>
                    <dt>sha256</dt><dd>{ev.source_sha256.slice(0, 20)}…</dd>
                    <dt>visibility</dt><dd>{ev.visibility}</dd>
                    <dt>redacted</dt><dd>{String(ev.redacted)}</dd>
                  </dl>
                </details>
              </section>
            )}
            {claim && <NotPresent claim={claim} turn={turn} />}
          </>}
        </div>
      </aside>
    </>
  );
}

function EmptyInspector() {
  return (
    <div className="insp-empty">
      <div className="insp-empty-glyph"><MousePointerClick size={20} aria-hidden="true" /></div>
      <h3>Select a claim or a lineage node</h3>
      <div className="keys">
        <div><span className="k2"><Kbd>j</Kbd><Kbd>k</Kbd></span>move between claims</div>
        <div><span className="k2"><Kbd>x</Kbd></span>mark reviewed</div>
        <div><span className="k2"><Kbd>e</Kbd></span>open cited evidence</div>
        <div><span className="k2"><Kbd>/</Kbd></span>search</div>
      </div>
    </div>
  );
}

function ClaimSection({ claim }: { claim: Claim }) {
  return (
    <section className="insp-sec">
      <code className="insp-claim-id">{claim.id}</code>
      <p className="insp-claim">{claim.text}</p>
      <div className="insp-terms">
        <Term k={claim.claim_type} hollow />
        <Term k={claim.epistemic_state} tone="accent" />
        <Term k={claim.support} field="support" tone={supportTone(claim.support)} hollow={!supportTone(claim.support)} />
      </div>
    </section>
  );
}

function Ceiling({ claim, caveat }: { claim: Claim; caveat: string }) {
  // abstain sits below the ladder: no rung is allowed, not even an observation
  const reached = claim.max_strength === ABSTAIN_CEILING ? -1 : Math.max(0, LADDER.findIndex(r => r.key === claim.max_strength));
  return (
    <section className="insp-sec">
      <div className="sec-head"><span className="eyebrow">Ceiling</span><code className="aside mono-s">max_strength</code></div>
      <div className="ceiling" role="list">
        {LADDER.map((r, i) => (
          <div key={r.key} role="listitem" className={`rung${i <= reached ? " on" : ""}`} aria-label={`${r.label}: ${i <= reached ? "allowed" : "not reached"}`}>
            <div className="rung-bar" />
            <span className="rung-label">{r.label}</span>
            <span className="rung-hint">{i <= reached ? "allowed" : ""}</span>
          </div>
        ))}
      </div>
      {reached < 0 && <p className="ceiling-note"><code className="mono-s">max_strength abstain</code> — not statable at any level.</p>}
      <p className="ceiling-note">{caveat}</p>
    </section>
  );
}

function Review({ claim }: { claim: Claim }) {
  const { s, dispatch } = useStore();
  const r = reviewOf(s, claim.id);
  const set = (value: typeof r) => dispatch({ type: "review", id: claim.id, value });
  return (
    <section className="insp-sec">
      <div className="sec-head"><span className="eyebrow">Your review</span></div>
      <div className="review-seg">
        <button className={`btn btn--sm${r === "checked" ? " is-on is-on--ok" : ""}`} aria-pressed={r === "checked"} onClick={() => set(r === "checked" ? "unchecked" : "checked")}>
          <Mark tone="tick" />I checked this
        </button>
        <button className={`btn btn--sm${r === "disputed" ? " is-on is-on--warn" : ""}`} aria-pressed={r === "disputed"} onClick={() => set(r === "disputed" ? "unchecked" : "disputed")}>
          <Mark tone="partial" />I disagree
        </button>
      </div>
      {r === "disputed" && (
        <div className="note-box">
          <label htmlFor="objection">What does the record miss?</label>
          <textarea
            id="objection" className="input" rows={3} autoFocus
            value={s.notes[claim.id] ?? ""}
            onChange={e => dispatch({ type: "note", id: claim.id, text: e.target.value })}
          />
        </div>
      )}
      <p className="local-note"><Lock size={12} aria-hidden="true" />Local; exported with the claim.</p>
    </section>
  );
}

function NotPresent({ claim, turn }: { claim: Claim; turn: Turn | undefined }) {
  // the counterfactual marker is a status, not a request — it is never counted or offered as one
  const requested = evidenceRequests(claim.missing_evidence);
  const turnRequests = turn ? evidenceRequests(turn.missing_evidence) : [];
  const marked = hasCounterfactualMarker(claim.missing_evidence) || (!!turn && hasCounterfactualMarker(turn.missing_evidence));
  return (
    <section className="insp-sec">
      <div className="sec-head"><span className="eyebrow">Not present</span></div>
      <div className="np-row"><Mark hollow />Counterevidence<span className="v">{claim.counterevidence_ids.length}</span></div>
      <div className="np-row"><Mark hollow />Missing evidence (claim)<span className="v vm-list">{requested.length ? requested.map(r => <span key={r}>{r}</span>) : "none"}</span></div>
      <div className="np-row"><Mark hollow />Missing evidence (turn)<span className="v vm-list">{turnRequests.length ? turnRequests.map(r => <span key={r}>{r}</span>) : "none"}</span></div>
      <div className="np-row"><Mark hollow />Counterfactual<span className="v">{marked ? "not_available" : "not evaluated"}</span></div>
    </section>
  );
}
