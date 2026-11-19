import { useEffect, useRef, useState } from "react";
import { HostedRequestDiscarded, type createHostedClient, type HostedJobAttempt, type HostedJobAttempts } from "../../service/hosted-client";
import type { HostedCapabilities, HostedJob, JobReceipt } from "../../service/hosted";
import { isProductError } from "../../service/port";
import { RefusalNote, refusalOf, type Refusal } from "../Ask";

/* K09 job controls, each only where the workspace offers it (jobs.cancel, jobs.retry, jobs.execution_v1).
   A cancel request is not a stop: the job is still watched until the service reports where it ended.
   A retry is a new, linked job; it is offered only when the service says this job is retryable, and an
   unknown answer (null) is not taken as yes. Its request key is kept until the service settles it. */
const OUTCOME: Record<HostedJobAttempt["outcome"], string> = {
  running: "running", cancel_requested: "cancel requested, not stopped yet", succeeded: "succeeded", failed: "failed",
  cancelled: "cancelled", expired: "passed its deadline", lease_lost: "lost its worker", retrying: "to be tried again",
};
const FAILURE: Record<NonNullable<HostedJobAttempt["failure_kind"]>, string> = {
  transient: "a temporary failure", permanent: "a failure that retrying will not fix", authorization: "access changed",
  attempts_exhausted: "no attempts left", deadline: "the deadline passed", cancelled: "it was cancelled",
  conflict: "the project changed after this job was accepted",
};
const ENDED = new Set(["failed", "cancelled", "expired"]);
const utc = (instant: string) => new Date(instant).toISOString().slice(11, 19) + " UTC";
const ignored = (error: unknown) => error instanceof HostedRequestDiscarded;

export function JobControls({ client, caps, workspaceId, jobId, state, waiting, retryOf, isCurrent, onRetried }: {
  client: ReturnType<typeof createHostedClient>; caps: HostedCapabilities; workspaceId: string; jobId: string;
  /** the last state the poll reported; null while it is still being watched */
  state: string | null; waiting: boolean; retryOf: string | null; isCurrent: () => boolean;
  onRetried: (receipt: JobReceipt) => void;
}) {
  const can = (name: string) => caps.workspace_id === workspaceId && caps.features?.[name] === true;
  const [cancel, setCancel] = useState<HostedJob["status"] | null>(null);
  const [job, setJob] = useState<HostedJob | null>(null);
  const [attempts, setAttempts] = useState<HostedJobAttempts | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<"cancel" | "retry" | "attempts" | null>(null);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const retryKey = useRef<string | null>(null), stop = useRef<AbortController | null>(null);
  useEffect(() => () => stop.current?.abort(), []);
  const ended = state !== null && ENDED.has(state);

  async function run<T>(kind: "cancel" | "retry" | "attempts", work: (signal: AbortSignal) => Promise<T>, done: (value: T) => void) {
    if (busy) return;
    stop.current = new AbortController(); setBusy(kind); setRefusal(null);
    try { const value = await work(stop.current.signal); if (isCurrent()) done(value); }
    catch (error) {
      if (!isCurrent() || ignored(error)) return;
      // a retry whose outcome is unknown keeps its key, so pressing again replays it instead of adding a job
      if (kind === "retry" && !(isProductError(error) && error.code === "outcome_unknown")) retryKey.current = null;
      setRefusal(refusalOf(error));
    } finally { if (isCurrent()) setBusy(null); }
  }
  // where the job ended decides whether a retry is offered: the service's own `retryable`, read once
  useEffect(() => {
    if (!ended || !can("jobs.retry")) return;
    const controller = new AbortController();
    client.loadJob(workspaceId, jobId, controller.signal).then(value => { if (isCurrent()) setJob(value); },
      error => { if (isCurrent() && !ignored(error)) setRefusal(refusalOf(error)); });
    return () => controller.abort();
  }, [ended, jobId]); // eslint-disable-line react-hooks/exhaustive-deps

  const showAttempts = () => {
    setOpen(true);
    void run("attempts", signal => client.loadJobAttempts(workspaceId, caps, jobId, signal), setAttempts);
  };
  if (!can("jobs.cancel") && !can("jobs.retry") && !can("jobs.execution_v1") && !retryOf) return null;
  return (
    <div className="job-controls">
      {retryOf && <p className="fld-note">This job retries <code className="mono-s">{retryOf}</code>, which keeps its own outcome.</p>}
      {waiting && cancel === "cancel_requested" && <p className="fld-note" role="status">Cancel requested. The job has not stopped yet, so this keeps waiting until the service reports where it ended.</p>}
      {waiting && cancel === "succeeded" && <p className="fld-note" role="status">The job finished before the cancel reached it.</p>}
      {ended && can("jobs.retry") && job && <p className="fld-note">{job.retryable === true ? "This job can be retried once, as a new linked job."
        : job.retryable === false ? "This job cannot be retried. The service decides this from the time it has left, your access and any earlier retry."
        : "Whether this job can be retried is not known, so no retry is offered."}</p>}
      <div className="actions">
        {waiting && can("jobs.cancel") && cancel !== "cancel_requested" && cancel !== "succeeded" && <button type="button" className="btn btn--sm"
          aria-disabled={busy !== null || undefined} onClick={() => void run("cancel", signal => client.cancelJob(workspaceId, caps, jobId, signal), value => setCancel(value.status))}>
          {busy === "cancel" ? "Cancelling…" : "Cancel job"}</button>}
        {ended && can("jobs.retry") && job?.retryable === true && <button type="button" className="btn btn--sm" aria-disabled={busy !== null || undefined}
          onClick={() => { retryKey.current ??= crypto.randomUUID(); const key = retryKey.current;
            void run("retry", signal => client.retryJob(workspaceId, caps, jobId, key, signal), receipt => { retryKey.current = null; onRetried(receipt); }); }}>
          {busy === "retry" ? "Retrying…" : "Retry job"}</button>}
        {can("jobs.execution_v1") && <button type="button" className="btn btn--sm btn--ghost" aria-expanded={open}
          onClick={() => open ? setOpen(false) : showAttempts()}>{open ? "Hide attempts" : "Show attempts"}</button>}
      </div>
      {open && attempts && <ol className="job-attempts" aria-label="Attempts">
        {attempts.items.length === 0 ? <li className="dim">No attempt has started yet.</li> : attempts.items.map(attempt => (
          <li key={attempt.epoch}>Attempt {attempt.epoch}: {OUTCOME[attempt.outcome]}{attempt.failure_kind ? `, ${FAILURE[attempt.failure_kind]}` : ""}.
            {" "}{attempt.started_at ? `Started ${utc(attempt.started_at)}` : "Start time not recorded"}{attempt.finished_at ? `, ended ${utc(attempt.finished_at)}.` : "."}</li>
        ))}
      </ol>}
      {open && busy === "attempts" && <p className="dim" role="status">Loading attempts…</p>}
      <RefusalNote refusal={refusal} compact />
    </div>
  );
}
