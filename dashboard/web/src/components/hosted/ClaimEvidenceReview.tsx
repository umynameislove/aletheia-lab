import { useEffect, useId, useMemo, useRef, useState } from "react";
import { download } from "../../export";
import { hostedPort } from "../../service";
import {
  PROVIDER_REVIEW_FEATURE, REVIEW_FEATURE, REVIEW_ID,
  type ClaimReviewReceipt, type ClaimReviewScope, type ReviewDecision, type ReviewMode, type ReviewVerdict,
} from "../../service/claim-review";
import { HostedRequestDiscarded, ProductError, type ReportFormat } from "../../service/port";
import { useRoute } from "../../store";
import { Id } from "../primitives";

const decisions: Record<ReviewDecision, string> = {
  approve_in_scope: "Approved in scope", revise: "Revision needed",
  unknown: "Unknown", unsupported: "Unsupported", tool_error: "Tool error",
};
const formats: Record<ReportFormat, string> = { json: "JSON", markdown: "Markdown", pdf: "PDF" };
const denied = (error: unknown) => error instanceof ProductError &&
  ["not_found", "permission_denied", "session_revoked", "session_expired", "membership_revoked"].includes(error.code);
const note = (error: unknown) => denied(error) ? "This review is unavailable in the current scope."
  : error instanceof ProductError && error.code === "deadline_exceeded"
    ? "The review has not been confirmed yet. Check it again."
    : "The service could not confirm the review. Try again.";
function onReviewRoute(workspace: string, scope: ClaimReviewScope, reviewId?: string): boolean {
  const [screen, search] = location.hash.split("?");
  const p = new URLSearchParams(search);
  return screen === "#/investigation" && p.get("workspace") === workspace && p.get("project") === scope.projectId
    && p.get("snapshot") === scope.snapshotId && p.get("result") === scope.resultId && p.get("claim") === scope.claimId
    && (reviewId === undefined || p.get("review") === reviewId);
}

type Attempt = { mode: ReviewMode; verdict: ReviewVerdict };
type ReadState = { id: string; receipt?: ClaimReviewReceipt; status: string; retry?: true };

export function ClaimEvidenceReview({ scope: input, onEvidence }: {
  scope: ClaimReviewScope;
  onEvidence: (id: string) => void;
}) {
  const port = hostedPort;
  const context = port?.context();
  const { q, setQ } = useRoute();
  const id = useId();
  const { projectId, snapshotId, resultId, claimId } = input;
  const scope = useMemo(() => ({ projectId, snapshotId, resultId, claimId }), [projectId, snapshotId, resultId, claimId]);
  const [verdict, setVerdict] = useState<ReviewVerdict | "">("");
  const [read, setRead] = useState<ReadState | null>(null);
  const [retryRead, setRetryRead] = useState(0);
  const [retrySubmit, setRetrySubmit] = useState<Attempt | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const action = useRef<AbortController | null>(null);
  const submitted = useRef<(Attempt & { id: string }) | null>(null);
  const reviewId = q.get("review");
  const enabled = !!context && context.caps.features?.[REVIEW_FEATURE] === true;
  const currentScope = (expectedReview?: string) => {
    return !!context && port?.context() === context && context.isCurrent()
      && onReviewRoute(context.workspace, scope, expectedReview);
  };

  useEffect(() => {
    if (!reviewId || !enabled || !context) return;
    const jobId = reviewId;
    const request = new AbortController();
    const { client, workspace, caps } = context;
    const active = () => !request.signal.aborted && context.isCurrent() && hostedPort?.context() === context
      && onReviewRoute(workspace, scope, jobId);
    const show = (state: Omit<ReadState, "id">) => { if (active()) setRead({ id: jobId, ...state }); };
    const load = () => client.loadClaimReview(workspace, caps, scope, jobId, request.signal);
    async function open() {
      if (!REVIEW_ID.test(jobId)) { show({ status: "This review link is invalid." }); return; }
      try {
        let receipt;
        try { receipt = await load(); } catch (error) {
          // A retained receipt may not exist yet. Only the authorized job status can settle that.
          if (!(error instanceof ProductError && error.code === "not_found") || !active()) throw error;
          show({ status: "Waiting for the review…" });
          const job = await client.pollJob(workspace, jobId, caps, { signal: request.signal });
          if (job.status !== "succeeded") { show({ status: `Review job: ${job.status}.` }); return; }
          receipt = await load();
        }
        const expected = submitted.current;
        if (expected?.id === jobId && (expected.mode !== receipt.mode || expected.verdict !== receipt.binding.asserted_verdict)) {
          throw new ProductError("response_out_of_scope", "The receipt belongs to another assertion.");
        }
        show({ receipt, status: "Review saved." });
      } catch (error) {
        if (!(error instanceof HostedRequestDiscarded)) show({ status: note(error), ...(denied(error) ? {} : { retry: true }) });
      }
    }
    void open();
    return () => request.abort();
  }, [context, enabled, reviewId, retryRead, scope]);

  useEffect(() => () => action.current?.abort(), []);

  if (!context || !enabled) return null;
  const { client, workspace, caps } = context;
  const maySubmit = caps.allowed_actions.includes("review");
  const provider = caps.features?.[PROVIDER_REVIEW_FEATURE] === true;
  const state = read?.id === reviewId ? read : null;
  const receipt = state?.receipt;
  if (!maySubmit && !reviewId) return null;

  function act(operation: (signal: AbortSignal) => Promise<void>, expectedReview?: string, failed?: (error: unknown) => void) {
    if (action.current || !currentScope(expectedReview)) return;
    const stop = new AbortController();
    action.current = stop;
    setBusy(true);
    setMessage("");
    void operation(stop.signal).catch(error => {
      if (!stop.signal.aborted && currentScope(expectedReview) && !(error instanceof HostedRequestDiscarded)) {
        if (denied(error) && expectedReview) setRead({ id: expectedReview, status: note(error) });
        if (failed) failed(error); else setMessage(note(error));
      }
    }).finally(() => {
      if (action.current === stop) {
        action.current = null;
        if (!stop.signal.aborted) setBusy(false);
      }
    });
  }

  function submit(attempt: Attempt) {
    act(async signal => {
      const accepted = await port!.submitClaimReview(scope, attempt.verdict, attempt.mode, signal);
      if (signal.aborted || !currentScope()) return;
      submitted.current = { ...attempt, id: accepted };
      setRetrySubmit(null);
      setQ({ review: accepted });
    }, undefined, error => {
      if (error instanceof ProductError && error.code === "outcome_unknown") {
        setRetrySubmit(attempt);
        setMessage("Submission was not confirmed. Retry this same request to check its outcome.");
      } else {
        setRetrySubmit(null);
        setMessage(note(error));
      }
    });
  }

  function exportReceipt(format: ReportFormat) {
    if (!receipt) return;
    act(async signal => {
      const file = await client.downloadClaimReview(workspace, caps, scope, receipt.job_id, format, signal);
      if (signal.aborted || !currentScope(receipt.job_id)) return;
      download(file.filename, file.bytes, file.media);
      setMessage(`${formats[format]} download started.`);
    }, receipt.job_id);
  }

  function citation(evidenceId: string) {
    if (!receipt) return;
    act(async signal => {
      const evidence = await client.loadClaimEvidence(workspace, caps, scope, evidenceId, scope.claimId, signal);
      if (evidence.relation !== "citation") throw new ProductError("response_out_of_scope", "The evidence is outside this finding.");
      if (!signal.aborted && currentScope(receipt.job_id)) onEvidence(evidenceId);
    }, receipt.job_id);
  }

  return <section className="insp-sec" aria-label="Claim evidence review">
    <details className="meta-toggle" open={reviewId ? true : undefined}>
      <summary>Check claim evidence</summary>
      {maySubmit && <div className="note-box">
        <label htmlFor={id}>Asserted verdict</label>
        <select id={id} className="input" value={verdict} disabled={busy || !!retrySubmit}
          onChange={event => setVerdict(event.target.value as ReviewVerdict | "")}>
          <option value="">Choose a verdict</option><option value="allowed">Allowed</option><option value="denied">Denied</option>
        </select>
        <div className="actions">
          {retrySubmit
            ? <button className="btn btn--sm" type="button" disabled={busy} onClick={() => submit(retrySubmit)}>Retry submission</button>
            : <>
              <button className="btn btn--sm" type="button" disabled={!verdict || busy}
                onClick={() => verdict && submit({ verdict, mode: "local_check" })}>Run local check</button>
              {provider && <button className="btn btn--sm" type="button" disabled={!verdict || busy}
                onClick={() => verdict && submit({ verdict, mode: "provider" })}>Run provider review</button>}
            </>}
        </div>
      </div>}
      <p className="foot-note" role="status" aria-atomic="true">{message || (busy ? "Working…" : state?.status ?? (reviewId ? "Opening review…" : ""))}</p>
      {state?.retry && <button className="btn btn--sm" type="button" onClick={() => {
        setRead(null); setRetryRead(n => n + 1);
      }}>Check review again</button>}
      {receipt && <>
        <p className="src-text">{receipt.finding.reason}</p>
        <dl className="kv">
          <dt>Finding</dt><dd>{decisions[receipt.finding.decision]}</dd>
          <dt>Asserted verdict</dt><dd>{receipt.binding.asserted_verdict}</dd>
          <dt>Review mode</dt><dd>{receipt.mode === "provider" ? "Provider review" : "Local check"}</dd>
          <dt>Evidence mode</dt><dd>{receipt.finding.execution_mode ?? "Unavailable"}</dd>
          {receipt.mode === "provider" && <>
            <dt>Provider usage</dt><dd>{receipt.usage}</dd>
            <dt>Provider response</dt><dd>{receipt.attempt.outcome?.status ?? "Unresolved"}</dd>
            {receipt.attempt.outcome?.provider_decision && <><dt>Provider opinion</dt>
              <dd>{decisions[receipt.attempt.outcome.provider_decision]}</dd></>}
          </>}
        </dl>
        <div className="actions">
          {receipt.finding.evidence_ids.map(evidence => (
            <button key={evidence} type="button" className="btn btn--sm" disabled={busy}
              onClick={() => citation(evidence)}>Open citation <Id v={evidence} /></button>
          ))}
          {caps.allowed_actions.includes("export") && caps.supported_export_formats.map(format => (
            <button key={format} type="button" className="btn btn--sm" disabled={busy}
              onClick={() => exportReceipt(format)}>Download {formats[format]}</button>
          ))}
        </div>
        <p className="foot-note">Review ID <Id v={receipt.job_id} /></p>
      </>}
    </details>
  </section>;
}
