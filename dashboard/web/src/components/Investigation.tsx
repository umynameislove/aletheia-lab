import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, CircleDashed, GitBranch, Info, MessageSquareText, Search } from "lucide-react";
import type { Claim, GraphNode, ProductView, Turn } from "../types";
import { counterfactualStatus, evidenceRequests, observedInSnapshot, projectView } from "../data/view";
import { isProductError, port, ProductError, useResultSwitch, useView } from "../service";
import { remedyFor } from "../service/errors";
import { canonicalQuestion, QUESTION_MAX, questionProblem } from "../service/question";
import type { SelectionKind } from "../service/port";
import { reviewOf, useRoute, useStore } from "../store";
import { plain } from "../vocab";
import { Absent, Id, Kbd, Mark, shortId, Term } from "./primitives";
import { Inspector } from "./Inspector";
import { Lineage } from "./Lineage";
import { ReviewBar, Worklist } from "./Worklist";

const NARROW = 1240;

export function Investigation() {
  const VIEW = useView();
  const { q, setQ } = useRoute();
  const { s, dispatch, toast } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const { showResult } = useResultSwitch();
  // ?turn= is 1-based and defaults to the latest turn; undefined once the conversation is deleted
  const turns = v.conversation.turns;
  const turnNo = Math.min(turns.length, Math.max(1, Number(q.get("turn")) || turns.length));
  const turn: Turn | undefined = turns[turnNo - 1];
  const searchRef = useRef<HTMLInputElement>(null);
  const debounce = useRef<number>();

  const filter = q.get("filter") || "all";
  const query = q.get("q") || "";
  const selectedId = q.get("claim");
  const evidenceId = q.get("evidence");
  const paneOpen = q.get("pane") === "insp";
  // the box is controlled locally and synced from the URL, so "Clear search" empties it too
  const [text, setText] = useState(query);
  useEffect(() => setText(query), [query]);

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

  const selectedClaim = v.claims.find(c => c.id === selectedId) ?? null;
  // node IDs are scoped to one result (Disposition IDs change with every follow-up), so a node
  // in the URL counts only if it exists in the result on screen — it is never carried across
  const nodeId = q.get("node");
  const selectedNode = nodeId ? v.graph.nodes.find(n => n.id === nodeId) ?? null : null;
  useEffect(() => { if (nodeId && !selectedNode) setQ({ node: null }); }, [nodeId, selectedNode, setQ]);
  const selection: Selection | null = selectedNode
    ? { kind: "node", id: selectedNode.id, label: `${selectedNode.kind} · ${shortId(selectedNode.source_id)}` }
    : selectedClaim ? { kind: "claim", id: selectedClaim.id, label: shortId(selectedClaim.id) } : null;
  const filteredOut = !!selectedClaim && !shown.some(c => c.id === selectedClaim.id);
  const narrow = () => window.innerWidth <= NARROW;

  const select = useCallback((c: Claim) =>
    setQ({ claim: c.id, evidence: c.citation_ids[0] ?? null, node: null, pane: narrow() ? "insp" : null }), [setQ]);
  const clear = useCallback(() => setQ({ claim: null, evidence: null, node: null, pane: null }), [setQ]);

  const onNode = (n: GraphNode) => {
    if (n.kind === "AtomicClaim") {
      const c = v.claims.find(x => x.id === n.source_id);
      if (c) setQ({ claim: c.id, evidence: c.citation_ids[0] ?? null, node: n.id, pane: narrow() ? "insp" : null });
    } else if (n.kind === "EvidenceItem") {
      const citing = v.claims.find(c => c.citation_ids.includes(n.source_id));
      setQ({ evidence: n.source_id, claim: citing?.id ?? null, node: n.id, pane: narrow() ? "insp" : null });
    } else {
      setQ({ node: n.id });
      toast(`${n.kind} selected`);
    }
  };

  const onFollowUp = (child: ProductView) => {
    showResult(child);
    // the child's node IDs are its own; the claim selection survives because parent claims are its prefix
    setQ({ turn: null, node: null });
    toast("Follow-up recorded", `${shortId(child.result.id)} · parent unchanged`);
  };

  /* keyboard: the auditor's loop, without leaving the keys */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target instanceof HTMLElement ? e.target : null;
      const typing = el?.closest("input, textarea, select, [contenteditable]");
      if (e.key === "Escape") { if (typing) el!.blur(); else if (selectedId || paneOpen) clear(); return; }
      if (typing || e.metaKey || e.ctrlKey || e.altKey || s.failed) return;
      if (e.key === "/") { e.preventDefault(); searchRef.current?.focus(); return; }
      if (!shown.length) return;
      const i = shown.findIndex(c => c.id === selectedId);
      const focusRow = (id: string) => requestAnimationFrame(() => (document.querySelector(`[data-claim="${CSS.escape(id)}"]`) as HTMLElement | null)?.focus());
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); const c = shown[Math.min(shown.length - 1, i + 1)]; setQ({ claim: c.id, evidence: c.citation_ids[0] ?? null, node: null }); focusRow(c.id); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); const c = shown[Math.max(0, i - 1)]; setQ({ claim: c.id, evidence: c.citation_ids[0] ?? null, node: null }); focusRow(c.id); }
      else if ((e.key === "x" || e.key === "X") && selectedClaim) {
        e.preventDefault();
        dispatch({ type: "review", id: selectedClaim.id, value: reviewOf(s, selectedClaim.id) === "checked" ? "unchecked" : "checked" });
      }
      else if ((e.key === "e" || e.key === "E") && selectedClaim?.citation_ids[0]) {
        e.preventDefault(); setQ({ evidence: selectedClaim.citation_ids[0], pane: narrow() ? "insp" : null });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shown, selectedId, selectedClaim, paneOpen, s, setQ, clear, dispatch]);

  if (s.failed) return <FailedRun />;

  const d = v.result.denominators;
  const denoms: [string, string, number | null][] = [
    ["Independent families", "independent_families", d.independent_families],
    ["Contexts", "contexts", d.contexts],
    ["Outputs", "outputs", d.outputs],
    ["Claims", "claims", d.claims],
  ];

  return (
    <div className="inv">
      <div className="center" id="main" tabIndex={-1}>
        <div className="center-inner">
          {/* ------------------------------------------------ the question on the record */}
          {turn ? <>
          {/* every turn stays readable: the history is the questions themselves, not numbers */}
          {turns.length > 1 && (
            <div className="turn-tabs" role="tablist" aria-label="Turns">
              {turns.map((t, i) => (
                <button key={t.id} role="tab" aria-selected={i + 1 === turnNo} title={`${t.id} · ${t.result_id}`}
                  onClick={() => setQ({ turn: i + 1 === turns.length ? null : String(i + 1) })}>
                  <span className="tt-n">{i + 1}</span><span className="tt-q">{t.question}</span>
                </button>
              ))}
            </div>
          )}
          <div className="turn-row">
            <span className="turn-pill"><MessageSquareText size={12} aria-hidden="true" />Turn {turnNo} of {turns.length}</span>
            <span className="turn-meta">
              <Id v={turn.id} /><span className="sep" aria-hidden="true">·</span><Id v={turn.snapshot_id} /><span className="sep" aria-hidden="true">·</span><Id v={turn.result_id} />
              <span className="sep" aria-hidden="true">·</span><span title="visible_evidence_ids">sees {turn.visible_evidence_ids.length ? turn.visible_evidence_ids.map(shortId).join(", ") : "no evidence"}</span>
            </span>
          </div>
          <h1 className="question">{turn.question}</h1>
          </> : <>
          <div className="turn-row"><span className="turn-pill"><MessageSquareText size={12} aria-hidden="true" />No turns</span></div>
          <h1 className="question">The conversation was deleted</h1>
          </>}

          <section className="verdict" aria-label="Answer on the record">
            {turn ? (
            <div className="verdict-body">
              <div className="verdict-glyph" aria-hidden="true"><CircleDashed size={17} /></div>
              <div>
                <div className="verdict-kicker">{turn.abstained ? <><Mark hollow />{plain("abstained")}</> : <><Mark tone="accent" />{plain("answered")}</>}</div>
                <p className="verdict-answer">{turn.answer}</p>
              </div>
            </div>
            ) : (
            <div className="verdict-body">
              <div className="verdict-glyph" aria-hidden="true"><CircleDashed size={17} /></div>
              <div>
                <div className="verdict-kicker"><Mark hollow />Deleted in this session</div>
                <p className="verdict-answer">The question and its answer are gone.</p>
              </div>
            </div>
            )}
            {/* what the user needs next to the answer: the change in question, what would settle it, the caveat */}
            {(v.snapshot.metric_changes.length > 0 || (turn && evidenceRequests(turn.missing_evidence).length > 0)) && (
              <div className="verdict-more">
                {v.snapshot.metric_changes.slice(0, 3).map(c => (
                  <div className="vm-row" key={c.evidence_id} title={`${c.evidence_id} · run ${(c.after ?? c.before)?.run_id} · step ${(c.after ?? c.before)?.step ?? "null"}`}>
                    <span className="vm-k">Change</span>
                    <span className="vm-v"><code className="mono-s">{c.metric_name}</code>
                      <span className="num">{c.before ? String(c.before.value) : "—"} → {c.after ? String(c.after.value) : "—"}</span>
                      {c.delta !== null && <span className="num dim">Δ {c.delta > 0 ? "+" : ""}{c.delta}</span>}
                      <span className="term-plain">{c.adverse_status === "adverse" ? <Mark tone="no" /> : c.adverse_status === "not_adverse" ? <Mark tone="ok" /> : <Mark hollow />}<code className="mono-s">{c.adverse_status}</code></span>
                    </span>
                  </div>
                ))}
                {v.snapshot.metric_changes.length > 3 && <div className="vm-row"><span className="vm-k" /><span className="vm-v dim">+{v.snapshot.metric_changes.length - 3} more in Overview</span></div>}
                {turn && evidenceRequests(turn.missing_evidence).length > 0 && (
                  <div className="vm-row">
                    <span className="vm-k">Needed</span>
                    <span className="vm-v vm-list">{evidenceRequests(turn.missing_evidence).map(r => <span key={r}>{r}</span>)}</span>
                  </div>
                )}
              </div>
            )}
            <div className="verdict-facts">
              <div className="fact"><span className="eyebrow">Disposition</span><Term k={v.result.disposition ?? "null"} hollow /></div>
              <div className="fact"><span className="eyebrow">Cause</span><Term k={v.result.causal_status} hollow /></div>
              <div className="fact"><span className="eyebrow">Answer from</span><Term k={(turn ? turn.runtime : v.runtime).provider} hollow /></div>
            </div>
            <p className="verdict-caveat"><code className="mono-s">caveat</code> {v.result.caveat}</p>
          </section>

          {/* ------------------------------------------------ lineage, always in view */}
          <section className="lineage-sec" aria-labelledby="lineage-h">
            <h2 id="lineage-h" className="sec-h2"><GitBranch size={15} aria-hidden="true" />Lineage</h2>
            <Lineage v={v} selectedClaim={selectedId} selectedEvidence={evidenceId} selectedNode={selectedNode?.id ?? null} shownIds={new Set(shown.map(c => c.id))} onNode={onNode} />
          </section>

          {/* ------------------------------------------------ claims */}
          <section className="claims-sec" aria-labelledby="claims-h">
            <div className="claims-head">
              <h2 id="claims-h">Claims <span className="count-badge">{v.claims.length}</span></h2>
            </div>

            <div className="toolbar">
              <div className="search">
                <Search size={14} aria-hidden="true" />
                <input
                  ref={searchRef} className="input" aria-label="Search claims and citations" placeholder="Search"
                  value={text}
                  onChange={e => { const t = e.target.value; setText(t); window.clearTimeout(debounce.current); debounce.current = window.setTimeout(() => setQ({ q: t }), 200); }}
                />
                <Kbd>/</Kbd>
              </div>
              <span className="select-wrap"><select className="select" aria-label="Show" value={filter} onChange={e => setQ({ filter: e.target.value === "all" ? null : e.target.value })}>
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
              <span className="shown">{shown.length} of {v.claims.length}</span>
            </div>

            <ReviewBar v={v} />

            {filteredOut && (
              <div className="notice notice--warn">
                <Mark tone="partial" />
                <div><b>Selected but filtered</b><code className="mono-s">{selectedClaim!.id}</code> hidden by the filter.</div>
                <div className="n-act">
                  <button className="btn btn--sm" onClick={() => setQ({ filter: null, q: null })}>Reveal</button>
                  <button className="btn btn--sm btn--ghost" onClick={clear}>Clear</button>
                </div>
              </div>
            )}

            <Worklist v={v} rows={shown} selectedId={selectedId} onSelect={select} onClearFilters={() => setQ({ filter: null, q: null })} />
          </section>

          {/* ------------------------------------------------ limits: secondary, under the claims */}
          <section className="limits" aria-labelledby="limits-h">
            <div className="limits-head"><h2 id="limits-h">Limits</h2></div>
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

            <div className="denoms" role="list" aria-label="Denominators — four tracks, never pooled">
              {denoms.map(([label, code, val]) => (
                <div key={code} role="listitem" className={`den${val === null ? " absent" : ""}`}>
                  <span className="den-label">{label}</span>
                  {val === null
                    ? <span className="den-val"><Mark hollow />Not counted</span>
                    : <span className="den-val">{val}</span>}
                  <code className="den-code">{val === null ? `${code} = null` : code}</code>
                </div>
              ))}
            </div>
          </section>

          {/* a follow-up needs a turn to follow; with the conversation deleted there is none */}
          {turn && <Composer v={VIEW} selection={selection} onDone={onFollowUp} />}
          {!turn && <div style={{ height: "var(--s8)" }} />}
        </div>
      </div>

      {/* a claim is read against the turn it was made in, not whichever turn is on screen */}
      <Inspector v={v} turn={selectedClaim ? turns.find(t => t.id === selectedClaim.turn_id) ?? turn : turn} claim={selectedClaim} evidenceId={evidenceId} open={paneOpen} onClose={clear} />
    </div>
  );
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

  async function ask() {
    if (!selection || !question) return;
    if (problem) { setErr({ code: "invalid_question", message: problem, advice: null }); return; }
    setBusy(true); setErr(null);
    try {
      const child = await port.follow_up(v.result.id, selection.kind, selection.id, question);
      setText("");
      onDone(child);
    } catch (e) {
      const pe = isProductError(e) ? e : new ProductError("unavailable", "The follow-up could not be sent.");
      setErr({ code: pe.code, message: pe.safe_message, advice: remedyFor(pe.code).advice });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        <div className="composer-in">
          <input
            aria-label="Ask a follow-up" value={text} disabled={busy || sample}
            placeholder={sample ? "Sample view. Connect a project to ask." : selection ? (selection.kind === "claim" ? "Ask about this claim…" : "Ask about this node…") : "Select a claim or a lineage node to ask"}
            onChange={e => { setText(e.target.value); setErr(null); }}
            onKeyDown={e => { if (e.key === "Enter" && can) { e.preventDefault(); void ask(); } }}
          />
          <span className="composer-scope" title="What the follow-up is scoped to">{selection ? selection.label : "nothing selected"}</span>
          <button className="btn btn--primary btn--sm" disabled={!can} onClick={() => void ask()}>{busy ? "Asking…" : "Ask"}</button>
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
          <span>Offline mock · <code className="mono-s">external_call {String(v.runtime.external_call)}</code></span>
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
  const v = projectView(VIEW, { failed: true, deletedConversation: s.deletedConversation });
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
            <div><b>The run did not complete <span className="tag">Preview</span></b>No claim, no disposition. <code className="mono-s">technical_failure</code></div>
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
      <aside className="insp" aria-label="Inspector">
        <div className="insp-head"><span className="eyebrow">Inspector</span></div>
        <div className="insp-body">
          <Absent title="Nothing to inspect" code="result.status = technical_failure" />
        </div>
      </aside>
    </div>
  );
}
