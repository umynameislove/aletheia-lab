import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ChevronUp, GitBranch, Info, MessageSquareText, Search } from "lucide-react";
import type { Claim, GraphNode, ProductView, Turn } from "../types";
import { counterfactualStatus, evidenceRequests, observedInSnapshot, projectView } from "../data/view";
import { ADVERSE_WORDS, definitionWords, formatNumber, formatSigned } from "../data/format";
import { pageOf, pageOfIndex } from "../data/paging";
import { isProductError, port, ProductError, scoped, useResultSwitch, useView } from "../service";
import { remedyFor } from "../service/errors";
import { canonicalQuestion, QUESTION_MAX, questionProblem } from "../service/question";
import type { SelectionKind } from "../service/port";
import { reviewOf, useRoute, useStore } from "../store";
import { plain } from "../vocab";
import { Absent, Id, Kbd, Mark, shortId, Term } from "./primitives";
import { ClaimAndSource, Reader, type Panel } from "./Reader";
import { ReviewBar, Worklist } from "./Worklist";

const NARROW = 1023;

export function Investigation() {
  const VIEW = useView();
  const { q, setQ, go } = useRoute();
  const { s, dispatch, toast } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const { showResult } = useResultSwitch();
  // ?turn= is 1-based and defaults to the latest turn; undefined once the conversation is deleted
  const turns = v.conversation.turns;
  const turnNo = Math.min(turns.length, Math.max(1, Number(q.get("turn")) || turns.length));
  const turn: Turn | undefined = turns[turnNo - 1];
  const searchRef = useRef<HTMLInputElement>(null);
  const debounce = useRef<number>();
  const drawer = useNarrow();

  const filter = q.get("filter") || "all";
  const query = q.get("q") || "";
  const selectedId = q.get("claim");
  const evidenceId = q.get("evidence");
  const panel: Panel = q.get("panel") === "lineage" || q.get("panel") === "timeline" ? (q.get("panel") as Panel) : "source";
  const reading = q.get("layout") === "reading";
  // the box is controlled locally and synced from the URL, so "Clear search" empties it too
  const [text, setText] = useState(query);
  useEffect(() => setText(query), [query]);
  const [compact, setCompact] = useState(() => document.documentElement.dataset.density === "compact");
  const toggleDensity = () => setCompact(on => {
    const next = !on;
    if (next) document.documentElement.dataset.density = "compact"; else delete document.documentElement.dataset.density;
    try { localStorage.setItem("p6-density", next ? "compact" : "comfortable"); } catch { /* storage may be blocked; the toggle still works */ }
    return next;
  });

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return v.claims.filter(c => {
      if (filter === "unchecked" || filter === "checked" || filter === "disputed") { if (reviewOf(s, c.id) !== filter) return false; }
      else if (filter !== "all" && c.support !== filter) return false;
      if (needle && !(c.id.toLowerCase().includes(needle) || c.text.toLowerCase().includes(needle)
        || c.citation_ids.some(i => i.toLowerCase().includes(needle)))) return false;
      return true;
    });
  }, [v, s, filter, query]);
  const shownIds = useMemo(() => new Set(shown.map(c => c.id)), [shown]);

  const selectedClaim = useMemo(() => v.claims.find(c => c.id === selectedId) ?? null, [v, selectedId]);
  const selectedIndex = useMemo(() => shown.findIndex(c => c.id === selectedId), [shown, selectedId]);
  // the page in the URL, or the page the selected claim is on when a link gives none
  const page = pageOf(shown, Number(q.get("page")) || (selectedIndex >= 0 ? pageOfIndex(selectedIndex) : 1));
  // node IDs are scoped to one result (Disposition IDs change with every follow-up), so a node
  // in the URL counts only if it exists in the result on screen: it is never carried across
  const nodeId = q.get("node");
  const selectedNode = nodeId ? v.graph.nodes.find(n => n.id === nodeId) ?? null : null;
  useEffect(() => { if (nodeId && !selectedNode) setQ({ node: null }); }, [nodeId, selectedNode, setQ]);
  const selection: Selection | null = selectedNode
    ? { kind: "node", id: selectedNode.id, label: `${selectedNode.kind} · ${shortId(selectedNode.source_id)}` }
    : selectedClaim ? { kind: "claim", id: selectedClaim.id, label: shortId(selectedClaim.text) } : null;
  const filteredOut = !!selectedClaim && selectedIndex < 0;
  // a link to a claim this result does not have: say so, and open nothing in its place
  const staleClaim = !!selectedId && !selectedClaim;
  const readerOpen = !reading && (!!selectedClaim || !!evidenceId || q.has("panel"));

  const select = useCallback((c: Claim) =>
    setQ({ claim: c.id, evidence: null, node: null, panel: null }), [setQ]);
  const clear = useCallback(() => setQ({ claim: null, evidence: null, node: null, panel: null }), [setQ]);
  const openPanel = useCallback((p: Panel) => setQ({ panel: p === "source" ? null : p }), [setQ]);

  const onNode = (n: GraphNode) => {
    if (n.kind === "AtomicClaim") {
      const c = v.claims.find(x => x.id === n.source_id);
      if (c) setQ({ claim: c.id, evidence: null, node: n.id, panel: null });
    } else if (n.kind === "EvidenceItem") {
      // reading a source does not change which claim is selected, and does not relate the two
      setQ({ evidence: n.source_id, node: n.id, panel: null });
    } else {
      setQ({ node: n.id });
      toast(`${n.kind} selected`);
    }
  };

  const onFollowUp = (child: ProductView) => {
    showResult(child);
    // the child's node IDs are its own; the claim selection survives because parent claims are its prefix
    setQ({ turn: null, node: null, page: null });
    toast("Follow-up recorded", `${shortId(child.result.id)} · the earlier result is unchanged`);
  };

  /* keyboard: the auditor's loop, without leaving the keys */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target instanceof HTMLElement ? e.target : null;
      const typing = el?.closest("input, textarea, select, [contenteditable]");
      if (e.key === "Escape") { if (typing) el!.blur(); else if (readerOpen) clear(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey || s.failed) return;
      if (e.key === "/") { e.preventDefault(); searchRef.current?.focus(); return; }
      if (!shown.length) return;
      const i = shown.findIndex(c => c.id === selectedId);
      const focusRow = (id: string) => requestAnimationFrame(() => (document.querySelector(`[data-claim="${CSS.escape(id)}"]`) as HTMLElement | null)?.focus());
      const move = (index: number) => { const c = shown[index]; setQ({ claim: c.id, evidence: null, node: null, page: String(pageOfIndex(index)) }); focusRow(c.id); };
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); move(Math.min(shown.length - 1, i + 1)); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); move(Math.max(0, i - 1)); }
      else if ((e.key === "x" || e.key === "X") && selectedClaim) {
        e.preventDefault();
        dispatch({ type: "review", id: selectedClaim.id, value: reviewOf(s, selectedClaim.id) === "checked" ? "unchecked" : "checked" });
      }
      else if ((e.key === "e" || e.key === "E") && selectedClaim?.citation_ids[0]) {
        e.preventDefault(); setQ({ evidence: selectedClaim.citation_ids[0], panel: null });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, selectedId, selectedClaim, readerOpen, s, setQ, clear, dispatch]);

  // a failure the service reported and the demo's preview of one read the same
  if (s.failed || VIEW.result.status === "technical_failure") return <FailedRun />;

  const d = v.result.denominators;
  const denoms: [string, string, number | null][] = [
    ["Independent families", "independent_families", d.independent_families],
    ["Contexts", "contexts", d.contexts],
    ["Outputs", "outputs", d.outputs],
    ["Claims", "claims", d.claims],
  ];
  const changes = v.snapshot.metric_changes;
  const defs = new Map(v.snapshot.metric_definitions.map(x => [x.metric_name, x]));
  const needed = turn ? evidenceRequests(turn.missing_evidence) : [];
  const runtime = turn ? turn.runtime : v.runtime;

  return (
    <div className="inv">
      <div className="center" id="main" tabIndex={-1}>
        <div className="center-inner">
          {VIEW.demo_only && (
            <div className="sample-note" role="note">
              <b>Sample data.</b> This is a made-up project for looking around, not your result.
              {port.kind === "live" && <button className="link-btn" onClick={() => go("connect")}>Connect your project</button>}
            </div>
          )}

          {/* ------------------------------------------------ the question on the record */}
          {turn ? <>
          {/* every turn stays readable: the history is the questions themselves, not numbers */}
          {turns.length > 1 && (
            <div className="turn-tabs" role="tablist" aria-label="Questions asked on this result">
              {turns.map((t, i) => (
                <button key={t.id} role="tab" aria-selected={i + 1 === turnNo} title={t.question}
                  onClick={() => setQ({ turn: i + 1 === turns.length ? null : String(i + 1) })}>
                  <span className="tt-n">{i + 1}</span><span className="tt-q">{t.question}</span>
                </button>
              ))}
            </div>
          )}
          <h1 className="question">{turn.question}</h1>
          </> : <h1 className="question">The conversation was deleted</h1>}

          <section className="verdict" aria-label="What this result says">
            {turn ? (
              <p className="verdict-answer">{turn.answer}</p>
            ) : (
              <p className="verdict-answer">The question and its answer are gone.</p>
            )}
            {/* each of these is a separate thing: being backed by a source is not knowing the cause */}
            <div className="verdict-facts">
              <div className="fact"><span className="eyebrow">Conclusion</span><Term k={turn?.abstained ? "abstained" : (v.result.disposition ?? "null")} hollow={!!turn?.abstained} /></div>
              <div className="fact"><span className="eyebrow">Cause</span><Term k={v.result.causal_status} hollow /></div>
              <div className="fact"><span className="eyebrow">Answered by</span><Term k={runtime.provider} hollow />
                <span className="fact-note">{runtime.external_call ? "Sent to an external service" : "Nothing left this computer"}</span></div>
            </div>
          </section>

          {changes.length > 0 && (
            <section className="metric-strip" aria-label="What changed">
              <h2 className="sr">What changed</h2>
              {changes.slice(0, 3).map(c => (
                <div className="metric" key={c.evidence_id}>
                  <span className="metric-name">{c.metric_name}<span className="metric-run">{(c.after ?? c.before)?.run_id}{(c.after ?? c.before)?.step === null ? "" : ` · step ${(c.after ?? c.before)?.step}`}</span></span>
                  <span className="metric-values">
                    {c.before ? formatNumber(c.before.value) : <span className="dim">No value</span>}
                    <span className="dim" aria-hidden="true"> → </span><span className="sr"> to </span>
                    {c.after ? formatNumber(c.after.value) : <span className="dim">No value</span>}
                  </span>
                  <span className="metric-delta">{c.delta === null ? "No change to compare" : formatSigned(c.delta)}</span>
                  <span className="metric-state">{c.adverse_status === "adverse" ? <Mark tone="no" /> : c.adverse_status === "not_adverse" ? <Mark tone="ok" /> : <Mark hollow />}{ADVERSE_WORDS[c.adverse_status]}</span>
                  <span className="metric-def">{definitionWords(defs.get(c.metric_name))}</span>
                </div>
              ))}
              {changes.length > 3 && <button className="link-btn" onClick={() => go("project")}>{changes.length - 3} more in the project overview</button>}
            </section>
          )}
          {changes.length === 0 && <p className="foot-note">No metric change is recorded for this snapshot.</p>}

          {needed.length > 0 && (
            <div className="needed"><span className="eyebrow">Still needed to answer this</span>
              <ul>{needed.map(r => <li key={r}>{r}</li>)}</ul>
            </div>
          )}

          {/* ------------------------------------------------ claims and their sources */}
          <section className={`bench${readerOpen && !drawer ? " bench--open" : ""}`} aria-label="Claims and sources">
            <div className="claims-sec">
              <div className="claims-head">
                <h2 id="claims-h">Claims <span className="count-badge">{v.claims.length.toLocaleString()}</span></h2>
                <span className="claims-tools">
                  <button className="btn btn--sm btn--ghost" aria-pressed={compact} onClick={toggleDensity}>{compact ? "Comfortable" : "Compact"}</button>
                  <button className="btn btn--sm btn--ghost" onClick={() => setQ({ panel: "lineage" })}><GitBranch size={14} aria-hidden="true" />Lineage</button>
                </span>
              </div>

              <div className="toolbar">
                <div className="search">
                  <Search size={14} aria-hidden="true" />
                  <input
                    ref={searchRef} className="input" aria-label="Search claims and citations" placeholder="Search claims"
                    value={text}
                    onChange={e => { const t = e.target.value; setText(t); window.clearTimeout(debounce.current); debounce.current = window.setTimeout(() => setQ({ q: t, page: null }), 200); }}
                  />
                  <Kbd>/</Kbd>
                </div>
                <span className="select-wrap"><select className="select" aria-label="Show" value={filter} onChange={e => setQ({ filter: e.target.value === "all" ? null : e.target.value, page: null })}>
                  <option value="all">All claims</option>
                  <optgroup label="Review">
                    <option value="unchecked">Not reviewed</option>
                    <option value="checked">Reviewed</option>
                    <option value="disputed">Disputed</option>
                  </optgroup>
                  <optgroup label="Support">
                    {["fully_supported", "partially_supported", "unsupported", "contradicted"].map(k => <option key={k} value={k}>{plain(k)}</option>)}
                    <option value="not_estimated">Support not assessed</option>
                  </optgroup>
                </select><ChevronDown size={14} aria-hidden="true" /></span>
                {shown.length !== v.claims.length && <span className="shown" role="status">{shown.length.toLocaleString()} of {v.claims.length.toLocaleString()} shown</span>}
              </div>

              <ReviewBar v={v} />

              {staleClaim && (
                <div className="notice notice--warn" role="status">
                  <Mark tone="partial" />
                  <div><b>That claim is not in this result</b>The link may be from another result. Nothing was opened in its place.</div>
                  <div className="n-act"><button className="btn btn--sm" onClick={clear}>Dismiss</button></div>
                </div>
              )}
              {filteredOut && (
                <div className="notice notice--warn">
                  <Mark tone="partial" />
                  <div><b>Selected but filtered out</b>The claim you are reading is hidden by the current search or filter.</div>
                  <div className="n-act">
                    <button className="btn btn--sm" onClick={() => setQ({ filter: null, q: null, page: null })}>Reveal</button>
                    <button className="btn btn--sm btn--ghost" onClick={clear}>Clear</button>
                  </div>
                </div>
              )}

              <Worklist
                v={v} page={page} selectedId={selectedId} onSelect={select}
                onPage={p => setQ({ page: p === 1 ? null : String(p) })}
                onClearFilters={() => setQ({ filter: null, q: null, page: null })}
                inline={reading ? c => (
                  <div className="wl-inline">
                    <ClaimAndSource inline v={v} turn={turns.find(t => t.id === c.turn_id) ?? turn} claim={c} evidenceId={evidenceId} onEvidence={id => setQ({ evidence: id })} />
                  </div>
                ) : undefined}
              />
            </div>

            {!reading && (
              <Reader
                v={v} turn={selectedClaim ? turns.find(t => t.id === selectedClaim.turn_id) ?? turn : turn}
                claim={selectedClaim} evidenceId={evidenceId} panel={panel}
                drawer={drawer} open={readerOpen}
                selectedNode={selectedNode?.id ?? null} shownIds={shownIds}
                onClose={clear} onPanel={openPanel} onEvidence={id => setQ({ evidence: id })} onNode={onNode}
              />
            )}
          </section>

          {/* ------------------------------------------------ limits and technical detail, on request */}
          <details className="more">
            <summary><ChevronRight size={14} aria-hidden="true" />Limits of this result and technical details</summary>
            <div className="wording">
              <div className="word">
                <div className="word-head"><Mark tone="ok" />You may say</div>
                <p>{v.result.allowed_wording}</p>
              </div>
              <div className="word forbid">
                <div className="word-head"><Mark hollow />Do not say</div>
                <p>{v.result.forbidden_wording}</p>
              </div>
            </div>
            <p className="foot-note">{v.result.caveat}</p>
            <div className="denoms" role="list" aria-label="Denominators, four tracks that are never pooled">
              {denoms.map(([label, code, val]) => (
                <div key={code} role="listitem" className={`den${val === null ? " absent" : ""}`}>
                  <span className="den-label">{label}</span>
                  {val === null
                    ? <span className="den-val"><Mark hollow />Not counted</span>
                    : <span className="den-val">{val.toLocaleString()}</span>}
                  <code className="den-code">{val === null ? `${code} = null` : code}</code>
                </div>
              ))}
            </div>
            <dl className="kv" style={{ marginTop: "var(--s4)" }}>
              <dt>Result</dt><dd>{v.result.id}</dd>
              <dt>Snapshot</dt><dd>{v.snapshot.id}</dd>
              <dt>Baseline</dt><dd>{v.snapshot.baseline_id}</dd>
              {turn && <><dt>Turn</dt><dd>{turn.id}</dd>
              <dt>Saw</dt><dd>{turn.visible_evidence_ids.length ? turn.visible_evidence_ids.join(", ") : "no evidence"}</dd>
              <dt>Model</dt><dd>{runtime.model}</dd></>}
            </dl>
          </details>

          {/* a follow-up needs a turn to follow; with the conversation deleted there is none */}
          {turn && <Composer v={VIEW} selection={selection} onDone={onFollowUp} />}
          {!turn && <div style={{ height: "var(--s8)" }} />}
        </div>
      </div>
    </div>
  );
}

/** Below this width the reader is a drawer. */
function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.innerWidth <= NARROW);
  useEffect(() => {
    const media = window.matchMedia(`(max-width: ${NARROW}px)`);
    const on = () => setNarrow(media.matches);
    on();
    media.addEventListener("change", on);
    return () => media.removeEventListener("change", on);
  }, []);
  return narrow;
}

interface Selection { kind: SelectionKind; id: string; label: string }

/* follow_up (K08). Scoped to exactly one claim or one lineage node of the result on screen; the
   answer is a new child result, the parent is never changed. The question is trimmed and put in
   NFC before it is sent, then checked against the service's own rules so a refusal is explained
   before the call. Replaying the same question on the same selection returns the same child. */
function Composer({ v, selection, onDone }: { v: ProductView; selection: Selection | null; onDone: (child: ProductView) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<{ code: string; message: string; advice: string | null } | null>(null);
  const question = canonicalQuestion(text);
  const problem = question ? questionProblem(question) : null;
  const length = [...question].length;
  // in Live, the service's demo_view is a sample that was never stored, so nothing can follow up on it
  const sample = port.kind === "live" && v.demo_only;
  const can = !sample && !!selection && !!question && !problem && !busy;

  function ask() {
    if (!selection || !question) return;
    if (problem) { setErr({ code: "invalid_question", message: problem, advice: null }); return; }
    setBusy(true); setErr(null);
    void scoped("follow_up", () => port.follow_up(v.result.id, selection.kind, selection.id, question), {
      done: child => { setText(""); onDone(child); },
      fail: e => {
        const pe = isProductError(e) ? e : new ProductError("unavailable", "The follow-up could not be sent.");
        setErr({ code: pe.code, message: pe.safe_message, advice: remedyFor(pe.code).advice });
      },
      late: child => ({ project_id: child.project.id, snapshot_id: child.snapshot.id, result_id: child.result.id, question }),
      always: () => setBusy(false),
    });
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        <div className="composer-in">
          <input
            aria-label="Ask a follow-up" value={text} disabled={busy || sample}
            placeholder={sample ? "Sample data. Connect a project to ask." : selection ? (selection.kind === "claim" ? "Ask about this claim…" : "Ask about this node…") : "Select a claim above to ask about it"}
            onChange={e => { setText(e.target.value); setErr(null); }}
            onKeyDown={e => { if (e.key === "Enter" && can) { e.preventDefault(); ask(); } }}
          />
          <span className="composer-scope" title="What the follow-up is scoped to">{selection ? selection.label : "nothing selected"}</span>
          <button className="btn btn--primary btn--sm" disabled={!can} aria-busy={busy} onClick={ask}>{busy ? "Asking…" : "Ask"}</button>
        </div>
        {(err || problem || length > QUESTION_MAX - 200) && (
          <div className="composer-err" role="status">
            <Mark tone={err || problem ? "no" : "partial"} />
            {err
              ? <span><code className="mono-s">{err.code}</code> {err.message}{err.advice ? ` ${err.advice}` : ""}</span>
              : problem ? <span>{problem}</span>
              : <span>{length} of {QUESTION_MAX} characters.</span>}
          </div>
        )}
        <div className="composer-why">
          <Info size={12} aria-hidden="true" />
          <span>{plain(v.runtime.provider)} · {v.runtime.external_call ? "sent to an external service" : "nothing leaves this computer"}</span>
          <details>
            <summary>What is sent <ChevronUp size={12} aria-hidden="true" /></summary>
            <div className="scope-pop">
              <dl className="kv">
                <dt>result_id</dt><dd>{v.result.id}</dd>
                <dt>selection_kind</dt><dd>{selection?.kind ?? "—"}</dd>
                <dt>selection_id</dt><dd>{selection?.id ?? "none selected"}</dd>
                <dt>question</dt><dd>{question ? "trimmed, NFC" : "—"}</dd>
                <dt>snapshot</dt><dd>{v.snapshot.id}</dd>
                <dt>provider</dt><dd>{v.runtime.provider}</dd>
              </dl>
            </div>
          </details>
        </div>
      </div>
    </div>
  );
}

/* technical_failure: the envelope stays auditable, the diagnosis half fails closed. The evidence
   already collected stays visible, with no claim, disposition or support conclusion attached. */
function FailedRun() {
  const VIEW = useView();
  const { s } = useStore();
  // the preview projection for the demo's toggle; a failure from the service is shown exactly as sent
  const v = s.failed ? projectView(VIEW, { failed: true, deletedConversation: s.deletedConversation }) : projectView(VIEW, s);
  const turn: Turn | undefined = v.conversation.turns[v.conversation.turns.length - 1];
  const rt = turn ? turn.runtime : v.runtime;
  const observed = observedInSnapshot(v);
  return (
    <div className="inv">
      <div className="center" id="main" tabIndex={-1}>
        <div className="center-inner" style={{ paddingBottom: "var(--s8)" }}>
          <div className="turn-row"><span className="turn-pill"><MessageSquareText size={12} aria-hidden="true" />{turn ? `Turn ${v.conversation.turns.length} of ${v.conversation.turns.length}` : "No turns"}</span>
            {turn && <span className="turn-meta"><Id v={turn.id} /><span className="sep" aria-hidden="true">·</span><span>against <Id v={turn.snapshot_id} /></span></span>}</div>
          <h1 className="question">{turn ? turn.question : "The conversation was deleted"}</h1>

          <div className="notice notice--stop" style={{ marginTop: "var(--s6)" }}>
            <Mark tone="no" />
            <div><b>The run did not complete {s.failed && <span className="tag">Preview</span>}</b>This is a technical failure, not a finding. There is no claim and no conclusion to read. <code className="mono-s">technical_failure</code></div>
          </div>

          <div className="fail-grid">
            <div className="panel">
              <div className="sec-head"><span className="eyebrow">What the record still holds</span></div>
              <dl className="kv">
                <dt>project</dt><dd>{v.project.id}</dd>
                <dt>snapshot</dt><dd>{v.snapshot.id}</dd>
                <dt>result</dt><dd>{v.result.id}</dd>
                <dt>turn</dt><dd>{turn ? `${turn.id} · abstained ${String(turn.abstained)}` : "deleted"}</dd>
                <dt>status</dt><dd>{v.result.status}</dd>
                <dt>disposition</dt><dd>null</dd>
                <dt>claims</dt><dd>{v.result.denominators.claims}</dd>
                <dt>provider</dt><dd>{rt.provider}</dd>
                <dt>external_call</dt><dd>{String(rt.external_call)}</dd>
              </dl>
            </div>
            <div className="stack">
              <Absent title="No claims" code="claims = [] · denominators.claims = 0" />
              <section className="panel">
                <div className="sec-head"><span className="eyebrow">Evidence collected, not assessed</span><span className="aside">{v.evidence.length} item{v.evidence.length === 1 ? "" : "s"}</span></div>
                {v.evidence.length ? v.evidence.map(e => (
                  <div className="np-row" key={e.id}>
                    <Mark hollow /><Id className="mono-s" v={e.id} />
                    <span className="v">{observed.has(e.id) ? "OBSERVED_IN" : "no OBSERVED_IN"}</span>
                  </div>
                )) : <p className="foot-note">None</p>}
              </section>
              <Absent title="Lineage: provenance only" code={`${v.graph.nodes.length} nodes · ${v.graph.edges.length} OBSERVED_IN`} />
              <Absent title="Counterfactual not evaluated" code={counterfactualStatus(v)} />
            </div>
          </div>
        </div>
      </div>
          </div>
  );
}
