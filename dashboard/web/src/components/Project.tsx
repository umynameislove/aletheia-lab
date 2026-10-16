import { useMemo, useState } from "react";
import { ArrowRight, FileText, RefreshCw, Share2 } from "lucide-react";
import type { MetricChange, MetricObservation, ProductView, Turn } from "../types";
import { evidenceRequests, lastTurn, observedInSnapshot, projectView } from "../data/view";
import { isProductError, port, useResultSwitch, useView } from "../service";
import type { RefreshResult } from "../service/port";
import { FirstQuestion, RefusalNote, type Refusal } from "./Connect";
import { useRoute, useStore } from "../store";
import { Absent, Id, Mark, shortId, Term } from "./primitives";

/* The project at a glance. Every value here is read from the view; what the view does not
   carry is shown as absent with the reason — never derived from text, never filled in. */
export function Project() {
  const VIEW = useView();
  const { q, setQ, go } = useRoute();
  const { s } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const tab = q.get("tab") === "timeline" ? "timeline" : "overview";
  const { showResult } = useResultSwitch();
  const [refreshed, setRefreshed] = useState<RefreshResult | null>(null);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [busy, setBusy] = useState(false);
  // Live shows the service's demo_view until a project is connected; it has no stored project to refresh
  const sample = port.kind === "live" && VIEW.demo_only;

  /* refresh re-reads the granted source. This result stays pinned to its own snapshot either way;
     a new snapshot is only analysed when someone asks about it. */
  const refresh = async () => {
    setBusy(true); setRefusal(null); setRefreshed(null);
    try { setRefreshed(await port.refresh(v.project.id)); }
    catch (e) { setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }); }
    finally { setBusy(false); }
  };

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head page-head--row">
          <h1>{v.project.display_name}</h1>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="btn" onClick={() => void refresh()} disabled={busy || sample || s.deletedProject} title={sample ? "Sample view. Connect a project first." : "refresh(project_id)"}>
              <RefreshCw size={14} aria-hidden="true" />{busy ? "Refreshing…" : "Refresh"}
            </button>
            <button className="btn" onClick={() => go("retention")}><Share2 size={14} aria-hidden="true" />Export or delete</button>
          </div>
        </header>

        <RefusalNote refusal={refusal} />
        {refreshed && (
          <div className={`notice${refreshed.status === "new_snapshot" ? " notice--ok" : ""}`} role="status">
            <Mark tone={refreshed.status === "new_snapshot" ? "ok" : "accent"} hollow={refreshed.status !== "new_snapshot"} />
            <div><b>{refreshed.status === "new_snapshot" ? "New snapshot" : "Unchanged"}</b><code className="mono-s"><Id v={refreshed.snapshot_id} /></code>
              {refreshed.snapshot_id !== v.snapshot.id && <span className="dim"> This result stays on <Id v={v.snapshot.id} />.</span>}</div>
          </div>
        )}
        {refreshed && refreshed.snapshot_id !== v.snapshot.id && (
          <FirstQuestion result={refreshed} onAnalyzed={view => { setRefreshed(null); showResult(view); go("investigation"); }} />
        )}

        <div className="tabbar" role="tablist" aria-label="Project views">
          <button role="tab" aria-selected={tab === "overview"} onClick={() => setQ({ tab: null })}>Overview</button>
          <button role="tab" aria-selected={tab === "timeline"} onClick={() => setQ({ tab: "timeline" })}>Timeline</button>
        </div>

        {tab === "overview" ? <Overview /> : <Timeline />}
      </div>
    </div>
  );

  function Overview() {
    // only what the diagnosis may see is counted as visible to it
    const visible = v.evidence.filter(e => e.visibility === "diagnosis");
    const ev = visible[0] ?? v.evidence[0];
    const turn: Turn | undefined = lastTurn(v);
    // every request still open in this result, across its turns, each once
    const requested = [...new Set(v.conversation.turns.flatMap(t => evidenceRequests(t.missing_evidence)))];
    const counter = v.claims.reduce((n, c) => n + c.counterevidence_ids.length, 0);
    const redacted = v.evidence.filter(e => e.redacted).length;
    return (
      <div className="stack-lg">
        {/* rows of two equal boxes: same width, same height */}
        <div className="pair">
          <section className="panel">
            <div className="sec-head"><span className="eyebrow">Observed change</span></div>
            {ev ? <>
              <div className="quote">
                <span className="quote-bar" aria-hidden="true" />
                <div className="quote-title"><Mark tone="ok" />{ev.title}</div>
                <blockquote>{ev.text}</blockquote>
                <div className="quote-src" title="reproduction_ref — an immutable P3 record"><FileText size={12} aria-hidden="true" />{ev.reproduction_ref.record_kind} · {ev.reproduction_ref.record_id}</div>
              </div>
              <div className="term-row"><Term k={v.result.causal_status} hollow /></div>
            </> : <Absent title="No evidence in this view" code="evidence = []" />}
          </section>

          <section className="panel panel--col">
            <div className="sec-head"><span className="eyebrow">Open result</span><code className="aside mono-s"><Id v={v.result.id} /></code></div>
            <p className="body-copy" style={{ color: "var(--ink)" }}>{v.result.summary}</p>
            <div className="term-grid">
              <Term k={v.result.disposition ?? "null"} hollow />
              <span className="term">
                <span className="term-plain">{v.claims.length ? <Mark tone="accent" /> : <Mark hollow />}{v.claims.length} claim{v.claims.length === 1 ? "" : "s"}</span>
                <code className="term-code">claims</code>
              </span>
            </div>
            <div className="actions push-end">
              <button className="btn btn--primary" onClick={() => go("investigation")}>Investigate<ArrowRight size={14} aria-hidden="true" /></button>
            </div>
          </section>
        </div>

        <div className="pair">
          <section className="panel">
            <div className="sec-head"><span className="eyebrow">Evidence</span></div>
            <Row on={visible.length > 0} tone="ok" k="Visible to diagnosis" val={`${visible.length}`} />
            <Row on={false} k="Requested, not supplied" val={turn ? `${requested.length}` : "—"} />
            <Row on={counter > 0} k="Counterevidence" val={String(counter)} />
            <Row on={redacted > 0} k="Redacted" val={String(redacted)} />
          </section>
          <section className="panel">
            <div className="sec-head"><span className="eyebrow">Wording</span></div>
            <div className="word-line"><Mark tone="ok" /><span className="dim">Say</span><p>{v.result.allowed_wording}</p></div>
            <div className="word-line"><Mark hollow /><span className="dim">Don&rsquo;t</span><p>{v.result.forbidden_wording}</p></div>
          </section>
        </div>

        <MetricChanges v={v} />
      </div>
    );
  }

  function Timeline() {
    const observed = [...observedInSnapshot(v)];
    return (
      <div className="stack-lg">
        <section className="panel">
          <div className="snap-pair">
            <div className="snap-card"><span className="eyebrow">Baseline</span><code><Id v={v.snapshot.baseline_id} /></code></div>
            <ArrowRight size={16} className="snap-arrow" aria-hidden="true" />
            <div className="snap-card"><span className="eyebrow">Current</span><code><Id v={v.snapshot.id} /></code><code className="snap-sha">sha256 {v.snapshot.sha256.slice(0, 16)}…</code></div>
          </div>
        </section>
        <div className="pair">
          <section className="panel">
            <div className="sec-head"><span className="eyebrow">Recorded</span></div>
            <Row on tone="accent" k="Snapshot pair" val={`${shortId(v.snapshot.baseline_id)} → ${shortId(v.snapshot.id)}`} mono />
            <Row on={observed.length > 0} tone="ok" k="Observed in current" val={observed.length ? `${observed.length} of ${v.evidence.length}` : "none"} />
            <Row on={false} k="Cause" val={`causal_status ${v.result.causal_status}`} mono />
          </section>
          <section className="panel">
            <div className="sec-head"><span className="eyebrow">Not supplied</span></div>
            <Row on={false} k="Regression event" val="—" />
            <Row on={false} k="Git commit" val="—" />
            <Row on={false} k="Config version" val="—" />
            <Row on={false} k="Metric series" val="two observations per change" />
          </section>
        </div>
      </div>
    );
  }
}

function Row({ on, tone = "accent", k, val, mono = false }: { on: boolean; tone?: "ok" | "accent"; k: string; val: string; mono?: boolean }) {
  return (
    <div className="np-row">
      {on ? <Mark tone={tone} /> : <Mark hollow />}
      <span className={on ? "row-k" : undefined}>{k}</span>
      <span className={`v${mono ? " mono-s" : ""}`}>{val}</span>
    </div>
  );
}

/* snapshot.metric_changes, exactly as the service states them: one row per (run, metric, step),
   keyed by evidence_id inside this view. delta and adverse_status are the service's — the UI never
   recomputes either, never pools steps and never picks a first or last step. */
function MetricChanges({ v }: { v: ProductView }) {
  const defs = new Map(v.snapshot.metric_definitions.map(d => [d.metric_name, d]));
  const rows = v.snapshot.metric_changes;
  const val = (o: MetricObservation | null) => (o === null ? <span className="dim">—</span> : String(o.value));
  const runStep = (c: MetricChange) => { const o = c.after ?? c.before; return o ? `${o.run_id} · ${o.step === null ? "no step" : `step ${o.step}`}` : "—"; };
  const signed = (d: number | null) => (d === null ? <span className="dim">null</span> : `${d > 0 ? "+" : ""}${d}`);
  const tone = (a: MetricChange["adverse_status"]) => (a === "adverse" ? "no" : a === "not_adverse" ? "ok" : "") as "no" | "ok" | "";
  return (
    <section className="panel">
      <div className="sec-head"><span className="eyebrow">Metric changes</span><span className="aside">baseline → current</span></div>
      {rows.length ? (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>Metric</th><th>Run · step</th><th className="num">Before</th><th className="num">After</th><th className="num">Delta</th><th>Change</th><th>Adverse</th><th>Definition</th></tr></thead>
            <tbody>
              {rows.map(c => {
                const d = defs.get(c.metric_name);
                return (
                  <tr key={c.evidence_id}>
                    <td><code className="mono-s">{c.metric_name}</code><div className="dim mono-s"><Id v={c.evidence_id} /></div></td>
                    <td className="mono-s">{runStep(c)}</td>
                    <td className="num">{val(c.before)}</td>
                    <td className="num">{val(c.after)}</td>
                    <td className="num">{signed(c.delta)}</td>
                    <td><code className="mono-s">{c.kind}</code></td>
                    <td><span className="term-plain">{tone(c.adverse_status) ? <Mark tone={tone(c.adverse_status)} /> : <Mark hollow />}<code className="mono-s">{c.adverse_status}</code></span></td>
                    <td className="mono-s">{d ? `${d.direction} · threshold ${d.regression_threshold}` : <span className="dim">no definition</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <p className="foot-note">No changes <code className="mono-s">metric_changes = []</code></p>}
    </section>
  );
}
