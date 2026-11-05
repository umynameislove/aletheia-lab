import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronRight, Copy, FileText, Link2, Lock, X } from "lucide-react";
import type { Claim, Evidence, GraphNode, ProductView, Turn } from "../types";
import { reviewOf, useStore } from "../store";
import { evidenceRequests, hasCounterfactualMarker } from "../data/view";
import { claimsCiting, readingFor, referenceText, sourcesOf, type Relation } from "../data/relation";
import { ADVERSE_WORDS, definitionWords, formatNumber, formatSigned, KIND_WORDS } from "../data/format";
import { ABSTAIN_CEILING, LADDER, plain, supportTone } from "../vocab";
import { Id, Mark, Term } from "./primitives";
import { LineagePanel, TimelinePanel } from "./Lineage";

export type Panel = "source" | "lineage" | "timeline";

const FOCUSABLE = 'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex]:not([tabindex="-1"])';

const RELATION: Record<Exclude<Relation, "absent">, { label: string; note: string; tone: "accent" | "contra" | "" }> = {
  cited: { label: "Cited by this claim", note: "The claim names this source as its support.", tone: "accent" },
  counter: { label: "Evidence against this claim", note: "This source speaks against the claim. It is not support.", tone: "contra" },
  context: { label: "Context only, not cited by this claim", note: "The question could see this source, but the claim does not rely on it.", tone: "" },
  unrelated: { label: "No relation to the selected claim", note: "This source is in the result, but the selected claim neither cites it nor saw it.", tone: "" },
};

/* The reader: one claim and the source being checked against it. The relation shown comes only from
   the claim's own citation_ids and counterevidence_ids (data/relation.ts). Details open on request. */
export function Reader({ v, turn, claim, evidenceId, panel, drawer, open, selectedNode, shownIds, onClose, onPanel, onEvidence, onNode }: {
  v: ProductView; turn: Turn | undefined; claim: Claim | null; evidenceId: string | null; panel: Panel;
  /** narrow screens: the reader is a drawer, and while closed it is out of the tab order too */
  drawer: boolean; open: boolean;
  selectedNode: string | null; shownIds: Set<string>;
  onClose: () => void; onPanel: (p: Panel) => void; onEvidence: (id: string | null) => void; onNode: (n: GraphNode) => void;
}) {
  const ref = useRef<HTMLElement>(null);
  const shut = drawer && !open;

  // opening the drawer moves focus into it; closing hands it back to where the person was
  useEffect(() => {
    if (!drawer || !open) return;
    const before = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>(".reader-focus")?.focus();
    return () => { if (before?.isConnected) before.focus(); };
  }, [drawer, open]);

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (!drawer || e.key !== "Tab" || !ref.current) return;
    const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const title = claim ? "Claim and source" : evidenceId ? "Source" : "Lineage";
  return (
    <>
      {drawer && open && <div className="insp-scrim" onClick={onClose} aria-hidden="true" />}
      <aside
        ref={ref}
        className={`insp${drawer ? " insp--drawer" : ""}${open ? " open" : ""}`}
        aria-label={title}
        role={drawer ? "dialog" : undefined}
        aria-modal={drawer ? true : undefined}
        onKeyDown={onKeyDown}
        {...(shut ? { inert: "" } : {})}
      >
        <div className="insp-head">
          <div className="seg" role="tablist" aria-label="What to read">
            {(["source", "lineage", "timeline"] as const).map(p => (
              <button key={p} role="tab" aria-selected={panel === p} className={panel === p ? "reader-focus" : undefined} onClick={() => onPanel(p)}>
                {p === "source" ? "Source" : p === "lineage" ? "Lineage" : "Timeline"}
              </button>
            ))}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close" title="Close  Esc"><X size={15} /></button>
        </div>
        <div className="insp-body">
          {panel === "source" && <ClaimAndSource v={v} turn={turn} claim={claim} evidenceId={evidenceId} onEvidence={onEvidence} />}
          {panel === "lineage" && <LineagePanel v={v} selectedClaim={claim?.id ?? null} selectedEvidence={evidenceId} selectedNode={selectedNode} shownIds={shownIds} onNode={onNode} />}
          {panel === "timeline" && <TimelinePanel v={v} />}
        </div>
      </aside>
    </>
  );
}

/** The same content, without the panel around it: the reading layout puts it under the claim. */
export function ClaimAndSource({ v, turn, claim, evidenceId, onEvidence, inline = false }: {
  v: ProductView; turn: Turn | undefined; claim: Claim | null; evidenceId: string | null; onEvidence: (id: string | null) => void; inline?: boolean;
}) {
  const reading = readingFor(v, claim, evidenceId);
  return (
    <>
      {claim && !inline && <ClaimSection claim={claim} />}
      {reading.missingId && (
        <section className="insp-sec">
          <div className="notice notice--warn" role="status">
            <Mark tone="partial" />
            <div><b>That source is not in this result</b>The link pointed to something this result does not contain or you cannot see. Nothing was shown in its place.</div>
            {claim && <div className="n-act"><button className="btn btn--sm" onClick={() => onEvidence(null)}>Show this claim&rsquo;s sources</button></div>}
          </div>
        </section>
      )}
      {claim && <SourceChoice v={v} claim={claim} current={reading.evidence?.id ?? null} onEvidence={onEvidence} />}
      {reading.evidence
        ? <SourceBody key={reading.evidence.id} v={v} evidence={reading.evidence} relation={reading.relation!} hasClaim={!!claim} />
        : claim && !reading.missingId && (
          <section className="insp-sec"><div className="absent"><h3><Mark hollow />This claim cites no source</h3><p>It stands without cited evidence. That is a limit of the claim, not an error.</p></div></section>
        )}
      {claim && <Review claim={claim} />}
      {claim && <MoreAboutClaim claim={claim} turn={turn} caveat={v.result.caveat} />}
    </>
  );
}

function ClaimSection({ claim }: { claim: Claim }) {
  return (
    <section className="insp-sec">
      <p className="insp-claim">{claim.text}</p>
      <div className="insp-terms">
        <Term k={claim.epistemic_state} tone="accent" />
        <Term k={claim.support} field="support" tone={supportTone(claim.support)} hollow={!supportTone(claim.support)} />
      </div>
    </section>
  );
}

/* Every source the claim has, in the two relations it can have. Support and opposition never share a list. */
function SourceChoice({ v, claim, current, onEvidence }: { v: ProductView; claim: Claim; current: string | null; onEvidence: (id: string) => void }) {
  const { cited, counter } = sourcesOf(v, claim);
  if (cited.length + counter.length <= 1) return null;
  const row = (e: Evidence, tone: "accent" | "contra") => (
    <button key={e.id} className="src-chip" aria-current={e.id === current ? "true" : undefined} onClick={() => onEvidence(e.id)}>
      <Mark tone={tone} /><span>{e.title}</span>
    </button>
  );
  return (
    <section className="insp-sec">
      {cited.length > 0 && <><div className="src-head">Cited by this claim <span className="dim">{cited.length}</span></div><div className="src-list">{cited.map(e => row(e, "accent"))}</div></>}
      {counter.length > 0 && <><div className="src-head">Evidence against <span className="dim">{counter.length}</span></div><div className="src-list">{counter.map(e => row(e, "contra"))}</div></>}
    </section>
  );
}

function SourceBody({ v, evidence: ev, relation, hasClaim }: { v: ProductView; evidence: Evidence; relation: Relation; hasClaim: boolean }) {
  const citedBy = claimsCiting(v, ev.id);
  const r = relation === "absent" ? null : RELATION[relation];
  const metric = v.snapshot.metric_changes.find(c => c.evidence_id === ev.id);
  const definition = metric ? v.snapshot.metric_definitions.find(d => d.metric_name === metric.metric_name) : undefined;
  return (
    <section className="insp-sec" aria-label="Source being read">
      {hasClaim && r ? (
        <div className={`relation relation--${relation}`}>
          {r.tone ? <Mark tone={r.tone} /> : <Mark hollow />}
          <div><b>{r.label}</b><span>{r.note}</span></div>
        </div>
      ) : (
        <div className="relation relation--unrelated"><Mark hollow /><div><b>Source, no claim selected</b><span>{citedBy.length ? `Cited by ${citedBy.length} claim${citedBy.length === 1 ? "" : "s"} in this result.` : "No claim in this result cites it."}</span></div></div>
      )}

      <h3 className="src-title">{ev.title}</h3>
      {ev.redacted && (
        <div className="notice notice--warn"><Mark tone="partial" /><div><b>Part of this source is withheld</b>What is shown is everything you can see of it. The rest was not replaced or summarised.</div></div>
      )}
      <blockquote className="src-text">{ev.text}</blockquote>

      {metric && (
        <div className="tbl-wrap src-metric">
          <table className="tbl tbl--plain">
            <caption className="sr">The metric change this source records</caption>
            <thead><tr><th scope="col">Metric</th><th scope="col" className="num">Before</th><th scope="col" className="num">After</th><th scope="col" className="num">Change</th></tr></thead>
            <tbody><tr>
              <td>{metric.metric_name}</td>
              <td className="num">{metric.before ? formatNumber(metric.before.value) : <span className="dim">No value</span>}</td>
              <td className="num">{metric.after ? formatNumber(metric.after.value) : <span className="dim">No value</span>}</td>
              <td className="num">{metric.delta === null ? <span className="dim">No value</span> : formatSigned(metric.delta)}</td>
            </tr></tbody>
          </table>
          <p className="foot-note">{ADVERSE_WORDS[metric.adverse_status]}; it {KIND_WORDS[metric.kind]}. {definitionWords(definition)}.</p>
        </div>
      )}

      <div className="src-foot">
        <span className="quote-src" title="An immutable record, not a file path"><FileText size={13} aria-hidden="true" />{ev.reproduction_ref.record_kind.split("_").join(" ")} · <Id v={ev.reproduction_ref.record_id} /></span>
        <span className="src-actions">
          <CopyButton label="Copy reference" text={() => referenceText(ev)} icon="copy" />
          <CopyButton label="Copy link" text={() => location.href} icon="link" />
        </span>
      </div>
      <details className="meta-toggle">
        <summary><ChevronRight size={13} aria-hidden="true" />Source record</summary>
        <dl className="kv">
          <dt>Evidence ID</dt><dd>{ev.id}</dd>
          <dt>Kind</dt><dd>{ev.kind}</dd>
          <dt>Record</dt><dd>{ev.reproduction_ref.record_kind} · {ev.reproduction_ref.record_id}</dd>
          <dt>SHA-256</dt><dd>{ev.source_sha256}</dd>
          <dt>Visibility</dt><dd>{ev.visibility}</dd>
          <dt>Redacted</dt><dd>{String(ev.redacted)}</dd>
        </dl>
      </details>
    </section>
  );
}

function CopyButton({ label, text, icon }: { label: string; text: () => string; icon: "copy" | "link" }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const copy = async () => {
    try { await navigator.clipboard.writeText(text()); setState("done"); }
    catch { setState("failed"); }
    window.setTimeout(() => setState("idle"), 2000);
  };
  const Icon = state === "done" ? Check : icon === "copy" ? Copy : Link2;
  return (
    <button className="btn btn--sm btn--ghost" onClick={() => void copy()} aria-live="polite">
      <Icon size={13} aria-hidden="true" />{state === "done" ? "Copied" : state === "failed" ? "Copy failed" : label}
    </button>
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
      <p className="local-note"><Lock size={13} aria-hidden="true" />Not saved. It stays in this tab until you reload; download it from Data &amp; retention to keep it.</p>
    </section>
  );
}

function MoreAboutClaim({ claim, turn, caveat }: { claim: Claim; turn: Turn | undefined; caveat: string }) {
  // the counterfactual marker is a status, not a request: it is never counted or offered as one
  const requested = evidenceRequests(claim.missing_evidence);
  const turnRequests = turn ? evidenceRequests(turn.missing_evidence) : [];
  const marked = hasCounterfactualMarker(claim.missing_evidence) || (!!turn && hasCounterfactualMarker(turn.missing_evidence));
  // abstain sits below the ladder: no rung is allowed, not even an observation
  const reached = claim.max_strength === ABSTAIN_CEILING ? -1 : Math.max(0, LADDER.findIndex(r => r.key === claim.max_strength));
  return (
    <section className="insp-sec">
      <details className="meta-toggle">
        <summary><ChevronRight size={13} aria-hidden="true" />How strongly this can be stated, and what is missing</summary>
        <div className="ceiling" role="list" style={{ marginTop: "var(--s3)" }}>
          {LADDER.map((r, i) => (
            <div key={r.key} role="listitem" className={`rung${i <= reached ? " on" : ""}`} aria-label={`${r.label}: ${i <= reached ? "allowed" : "not reached"}`}>
              <div className="rung-bar" />
              <span className="rung-label">{r.label}</span>
              <span className="rung-hint">{i <= reached ? "allowed" : "not reached"}</span>
            </div>
          ))}
        </div>
        {reached < 0 && <p className="ceiling-note">This claim cannot be stated as a finding at any level.</p>}
        <p className="ceiling-note">{caveat}</p>
        <div className="np-row"><Mark hollow />Evidence against<span className="v">{claim.counterevidence_ids.length}</span></div>
        <div className="np-row"><Mark hollow />Missing for this claim<span className="v vm-list">{requested.length ? requested.map(r => <span key={r}>{r}</span>) : "none listed"}</span></div>
        <div className="np-row"><Mark hollow />Missing for this question<span className="v vm-list">{turnRequests.length ? turnRequests.map(r => <span key={r}>{r}</span>) : "none listed"}</span></div>
        <div className="np-row"><Mark hollow />Counterfactual check<span className="v">{marked ? "Not available" : "Not evaluated"}</span></div>
        <dl className="kv" style={{ marginTop: "var(--s3)" }}>
          <dt>Claim ID</dt><dd>{claim.id}</dd>
          <dt>Claim type</dt><dd>{plain(claim.claim_type)}</dd>
          <dt>Stated in turn</dt><dd>{claim.turn_id}</dd>
        </dl>
      </details>
    </section>
  );
}
