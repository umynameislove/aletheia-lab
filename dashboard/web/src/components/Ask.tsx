import { useId, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ArrowRight, RefreshCw } from "lucide-react";
import { isProductError, linked, linkIds, port, scoped } from "../service";
import { remedyFor } from "../service/errors";
import { keptOnArrival, rememberProject } from "../service/opened";
import { canonicalQuestion, QUESTION_MAX, questionProblem } from "../service/question";
import type { ConfirmResult, RefreshResult } from "../service/port";
import type { ConfirmMapping, ProductView } from "../types";
import { Id, Mark } from "./primitives";

/** Live keeps the confirmed or refreshed IDs (URL + local), so a reload can continue this project. */
export function openProject(r: { project_id: string; snapshot_id: string }) {
  if (!linked) return;
  rememberProject({ project_id: r.project_id, snapshot_id: r.snapshot_id });
  linkIds({ project: r.project_id, snapshot: r.snapshot_id, result: null });
}

/** confirm_import whose project is kept (Live) even if the person has left Connect by the time it answers. */
export const confirmAndKeep = (preview_id: string, mapping: ConfirmMapping) =>
  linked ? keptOnArrival(port.confirm_import(preview_id, mapping)) : port.confirm_import(preview_id, mapping);

/* The step after import: ask the first question. Live computes observations from the stored metrics
   (analyze_observations); the demo keeps its scripted answer (analyze_mock). Either returns the new
   project's own view, so the screens never fall back to the packaged demo. */
/** `bare` drops the panel frame, for use inside another panel. */
export function FirstQuestion({ result, onAnalyzed, bare = false }: { result: Pick<ConfirmResult, "project_id" | "snapshot_id">; onAnalyzed: (v: ProductView) => void; bare?: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const noteId = useId();
  const q = canonicalQuestion(text);
  const problem = q ? questionProblem(q) : null;
  const ask = () => {
    if (!q || problem || busy) return;
    setBusy(true); setRefusal(null);
    const analyze = port.kind === "live" ? port.analyze_observations : port.analyze_mock;
    return scoped("analyze", () => analyze.call(port, result.project_id, result.snapshot_id, q), {
      done: onAnalyzed,
      fail: e => setRefusal(refusalOf(e)),
      late: view => ({ project_id: view.project.id, snapshot_id: view.snapshot.id, result_id: view.result.id, question: q }),
      always: () => setBusy(false),
      key: `analyze:${result.snapshot_id}:${q}`,
      label: q,
    });
  };
  const note = !!problem || [...q].length > QUESTION_MAX - 200;
  // while asking, the box and the button keep focus: read-only and aria-disabled, not disabled
  const form = (
    <>
      <div className="ask-row">
        <input className="input" aria-label="First question" placeholder="For example: what changed in validation accuracy?" value={text} readOnly={busy}
          onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && q && !problem && !busy) { e.preventDefault(); void ask(); } }}
          aria-invalid={!!problem} aria-describedby={note ? noteId : undefined} />
        <button className="btn btn--primary" disabled={!q || !!problem} aria-disabled={busy || undefined} aria-busy={busy} onClick={() => void ask()}>{busy ? "Asking…" : "Ask"}<ArrowRight size={14} aria-hidden="true" /></button>
      </div>
      {/* the status is always in the page, so the first note is read too; only its text changes */}
      <p className="fld-error" id={noteId} role="status">{note && <><Mark tone={problem ? "no" : "partial"} />{problem ?? `${[...q].length} of ${QUESTION_MAX} characters.`}</>}</p>
      <RefusalNote refusal={refusal} />
    </>
  );
  if (bare) return <div className="ask-bare">{form}</div>;
  return (
    <section className="panel" style={{ marginTop: "var(--s5)" }}>
      <div className="sec-head"><span className="eyebrow">Ask the first question</span></div>
      <p className="fld-note" style={{ marginBottom: "var(--s3)" }}>
        {port.kind === "live" ? "Worked out from your stored metrics, no model." : "Scripted answer, no model."} Nothing leaves this computer.
      </p>
      {form}
    </section>
  );
}

/** `advice` overrides the general remedy when the screen knows more than the code says. */
export type Refusal = { code: string; message: string; advice?: string } | null;

/** Any failure as a Refusal: a ProductError keeps its code and safe message, anything else is "unavailable". */
export const refusalOf = (e: unknown, message = "The service did not answer."): NonNullable<Refusal> =>
  isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message };

/* The one way a service error is shown (Q-02.03 M8): code + what to do + the service's safe message; an
   unknown code still shows the safe message. It is its own alert, so nothing around it is a live region.
   `id` lets a field name it as its error (aria-describedby); `compact` is the one-line form for a row;
   `alert={false}` for a list that is read again on every visit, where the note is a record, not news. */
export function RefusalNote({ refusal, id, compact = false, alert = true }: { refusal: Refusal; id?: string; compact?: boolean; alert?: boolean }) {
  if (!refusal) return null;
  const advice = refusal.advice ?? remedyFor(refusal.code).advice;
  const body = <><b>Refused · <code className="mono-s">{refusal.code}</code></b> {advice && <>{advice} </>}{refusal.message && <span className="dim">{refusal.message}</span>}</>;
  const role = alert ? "alert" : undefined;
  return compact
    ? <span className="refusal-line" role={role} id={id}><Mark tone="no" /><span>{body}</span></span>
    : <div className="notice notice--stop" role={role} id={id}><Mark tone="no" /><div>{body}</div></div>;
}

/* One Refresh on every screen (Q-02.03 M7). Busy keeps focus (aria-disabled + aria-busy, and the handler
   guards); the outcome is said by RefreshStatus, whose status region is in the page before the answer. */
export function useRefresh(projectId: string, onDone?: (r: RefreshResult) => void) {
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const [outcome, setOutcome] = useState<RefreshResult | null>(null);
  const run = () => {
    if (busy) return;
    setBusy(true); setRefusal(null); setOutcome(null);
    // a new snapshot is kept even if the person moves on before the refresh answers
    void scoped("refresh", () => (linked ? keptOnArrival(port.refresh(projectId)) : port.refresh(projectId)), {
      done: r => { setOutcome(r); onDone?.(r); },
      fail: e => setRefusal(refusalOf(e)),
      always: () => setBusy(false),
    });
  };
  return { busy, refusal, outcome, setOutcome, run };
}

export function RefreshButton({ r, className = "btn", ...rest }: { r: ReturnType<typeof useRefresh> } & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={className} title="Read the granted folder again" {...rest} aria-disabled={r.busy || undefined} aria-busy={r.busy} onClick={r.run}>
      <RefreshCw size={14} aria-hidden="true" />{r.busy ? "Refreshing…" : "Refresh"}
    </button>
  );
}

/** `children` adds what this screen knows, such as which snapshot its result stays on. */
export function RefreshStatus({ r, children }: { r: ReturnType<typeof useRefresh>; children?: ReactNode }) {
  const fresh = r.outcome?.status === "new_snapshot";
  return (
    <div role="status">
      {r.outcome && (
        <div className={`notice${fresh ? " notice--ok" : ""}`}>
          <Mark tone={fresh ? "ok" : "accent"} hollow={!fresh} />
          <div><b>{fresh ? "New snapshot" : "Unchanged"}</b><code className="mono-s"><Id v={r.outcome.snapshot_id} /></code>{children}</div>
        </div>
      )}
    </div>
  );
}
