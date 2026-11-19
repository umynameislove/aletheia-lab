import { useEffect, useRef, useState } from "react";
import { hostedPort } from "../service";
import type { HostedDeletionStatus } from "../service/hosted-client";
import { HostedRequestDiscarded } from "../service/port";
import { formatNumber } from "../data/format";
import { RefusalNote, refusalOf, type Refusal } from "./Ask";

/* K13: how far a hosted logical delete has gone. Read when the receipt appears and again on request,
   never polled. It says only what the service reports: "purged" covers this project's own records, not
   the workspace's raw sources or the operator's backups, and a requested time that is not the delete's
   own commit says where it came from. */
const STAGE: Record<HostedDeletionStatus["status"], string> = {
  retained: "Retained; eligible for removal after the retention period",
  purging: "Removing this project's records",
  purged: "This project's records are removed",
};
const BASIS: Record<HostedDeletionStatus["time_basis"], string> = {
  delete_commit: "when the delete was committed",
  migration_first_seen: "first seen after a data migration, not the delete itself",
  restore_checkpoint: "from a restore checkpoint, not the delete itself",
};
const utc = (instant: string) => new Date(instant).toISOString().slice(0, 16).replace("T", " ") + " UTC";

export function DeletionStatus({ projectId, tombstoneId }: { projectId: string; tombstoneId: string }) {
  const context = hostedPort?.context();
  const offered = context?.caps.features?.["projects.deletion_status"] === true;
  const [status, setStatus] = useState<HostedDeletionStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const stop = useRef<AbortController | null>(null);
  async function read() {
    if (!context) return;
    stop.current?.abort(); stop.current = new AbortController();
    setBusy(true); setRefusal(null);
    try {
      const next = await context.client.loadDeletionStatus(context.workspace, context.caps, projectId, tombstoneId, stop.current.signal);
      if (context.isCurrent()) setStatus(next);
    } catch (error) {
      if (context.isCurrent() && !(error instanceof HostedRequestDiscarded)) setRefusal(refusalOf(error));
    } finally { setBusy(false); }
  }
  useEffect(() => {
    if (offered) void read();
    return () => stop.current?.abort();
  }, [offered, projectId, tombstoneId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!offered) return null;
  return (
    <div className="deletion-status">
      <div className="sec-head">
        <span className="eyebrow">Deletion status</span>
        <button className="btn btn--sm btn--ghost" aria-busy={busy} aria-disabled={busy || undefined}
          onClick={() => { if (!busy) void read(); }}>{busy ? "Checking…" : "Check again"}</button>
      </div>
      {status ? <dl className="kv">
        <dt>Stage</dt><dd>{STAGE[status.status]} ({status.status})</dd>
        <dt>Requested</dt><dd>{utc(status.requested_at)}, {BASIS[status.time_basis]}</dd>
        <dt>Removal from</dt><dd>{utc(status.eligible_at)} ({formatNumber(status.project_retention_days)}-day retention)</dd>
        <dt>Removed</dt><dd>{status.purged_at ? utc(status.purged_at) : "Not yet"}</dd>
        <dt>Records removed</dt><dd>{formatNumber(status.records_removed)}</dd>
        <dt>Stored objects still to remove</dt><dd>{formatNumber(status.pending_objects)}</dd>
        <dt>Kept, shared with other projects</dt><dd>{formatNumber(status.shared_objects_retained)}</dd>
      </dl> : !refusal && <p className="dim" role="status">Checking how far the delete has gone…</p>}
      <RefusalNote refusal={refusal} compact />
      <p className="foot-note">Uploaded source files stay in the workspace <code className="mono-s">workspace_sources_retained</code>,
        and backups are kept by the operator <code className="mono-s">operator_managed_not_erased_here</code>. Neither is erased here.</p>
    </div>
  );
}
