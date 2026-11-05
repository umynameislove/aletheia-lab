import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { isProductError, linked, linkIds, port, scoped } from "../service";
import { remedyFor } from "../service/errors";
import { rememberProject } from "../service/opened";
import { canonicalQuestion, QUESTION_MAX, questionProblem } from "../service/question";
import type { ConfirmResult } from "../service/port";
import type { ProductView } from "../types";
import { Mark } from "./primitives";

/** Live keeps the confirmed or refreshed IDs (URL + local), so a reload can continue this project. */
export function openProject(r: { project_id: string; snapshot_id: string }) {
  if (!linked) return;
  rememberProject({ project_id: r.project_id, snapshot_id: r.snapshot_id });
  linkIds({ project: r.project_id, snapshot: r.snapshot_id, result: null });
}

/* The step after import: ask the first question. analyze_mock returns the new project's own view,
   so the screens never fall back to the packaged demo. */
/** `bare` drops the panel frame, for use inside another panel. */
export function FirstQuestion({ result, onAnalyzed, bare = false }: { result: Pick<ConfirmResult, "project_id" | "snapshot_id">; onAnalyzed: (v: ProductView) => void; bare?: boolean }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const q = canonicalQuestion(text);
  const problem = q ? questionProblem(q) : null;
  const ask = () => {
    if (!q || problem) return;
    setBusy(true); setRefusal(null);
    return scoped("analyze", () => port.analyze_mock(result.project_id, result.snapshot_id, q), {
      done: onAnalyzed,
      fail: e => setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }),
      late: view => ({ project_id: view.project.id, snapshot_id: view.snapshot.id, result_id: view.result.id, question: q }),
      always: () => setBusy(false),
      key: `analyze:${result.snapshot_id}:${q}`,
      label: q,
    });
  };
  const form = (
    <>
      <div className="ask-row">
        <input className="input" aria-label="First question" placeholder="For example: what changed in validation accuracy?" value={text} disabled={busy}
          onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && q && !problem && !busy) { e.preventDefault(); void ask(); } }} aria-invalid={!!problem} />
        <button className="btn btn--primary" disabled={!q || !!problem || busy} aria-busy={busy} onClick={() => void ask()}>{busy ? "Asking…" : "Ask"}<ArrowRight size={14} aria-hidden="true" /></button>
      </div>
      {(problem || [...q].length > QUESTION_MAX - 200) && <p className="fld-error" role="status"><Mark tone={problem ? "no" : "partial"} />{problem ?? `${[...q].length} of ${QUESTION_MAX} characters.`}</p>}
      <RefusalNote refusal={refusal} />
    </>
  );
  if (bare) return <div className="ask-bare">{form}</div>;
  return (
    <section className="panel" style={{ marginTop: "var(--s5)" }}>
      <div className="sec-head"><span className="eyebrow">Ask the first question</span></div>
      <p className="fld-note" style={{ marginBottom: "var(--s3)" }}>The answer is scripted and local: no model runs, and nothing leaves this computer.</p>
      {form}
    </section>
  );
}

/** `advice` overrides the general remedy when the screen knows more than the code says. */
export type Refusal = { code: string; message: string; advice?: string } | null;

/* code + what to do + the service's safe message; an unknown code still shows the safe message */
export function RefusalNote({ refusal }: { refusal: Refusal }) {
  if (!refusal) return null;
  const r = remedyFor(refusal.code);
  return (
    <div className="notice notice--stop" role="alert">
      <Mark tone="no" />
      <div><b>Refused · <code className="mono-s">{refusal.code}</code></b>{(refusal.advice ?? r.advice) && <>{refusal.advice ?? r.advice} </>}<span className="dim">{refusal.message}</span></div>
    </div>
  );
}
