import { useMemo, useState } from "react";
import { Download, Lock, Trash2 } from "lucide-react";
import { projectView } from "../data/view";
import { AFTER_DELETE, capabilities, fence, isProductError, port, useView } from "../service";
import { remedyFor } from "../service/errors";
import { forgetProject } from "../service/opened";
import { downloadJSON, downloadMarkdown, downloadPdf, downloadReview } from "../export";
import { useStore } from "../store";
import { Modal } from "./Modal";
import { Absent, Mark } from "./primitives";

type Scope = "conversation" | "project";

/* Exactly what goes into the file, and exactly what is gone afterwards. */
export function Retention() {
  const VIEW = useView();
  const { s, dispatch, toast } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const [confirm, setConfirm] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const r = s.receipt;
  const demo = port.kind === "demo";
  const fail = (e: unknown) => toast(isProductError(e) ? `Refused · ${e.code}` : "Action failed",
    isProductError(e) ? remedyFor(e.code).advice ?? e.safe_message : undefined);
  // the failure preview is a demo scaffold over the screens; files always come from the stored result
  // Live shows the service's demo_view until a project is connected; that view is not stored, so
  // the service has nothing to export or delete for it
  const sample = !demo && VIEW.demo_only ? "This is sample data. Connect a project first." : null;
  const formatOff = (f: "json" | "markdown" | "pdf") => (capabilities.supported_export_formats.includes(f) ? exportOff : "Not available in this build.");
  const exportOff = sample ?? (s.failed ? "Turn off “Preview a failed run” to export." : null);

  const del = async (scope: Scope) => {
    if (scope !== "project") return; // no service method deletes a conversation alone
    setBusy(true);
    const ticket = fence.ticket();
    try {
      const receipt = await port.delete_project(VIEW.project.id);
      // the project is gone wherever the person is now, so its state closes either way; the receipt
      // names its project and cannot close another (service/transitions.ts). Only the toast is scoped.
      forgetProject(VIEW.project.id);
      dispatch({ type: "deleteProject", receipt: { ...receipt, scope: "" } });
      if (fence.current(ticket)) toast("Project deleted");
    } catch (e) { if (fence.current(ticket)) fail(e); } finally { setBusy(false); setConfirm(null); }
  };

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head">
          <h1>Data &amp; retention</h1>
        </header>

        {r && (
          <section className="panel receipt" aria-label="Deletion receipt">
            <div className="sec-head"><span className="eyebrow">Deletion receipt</span>{demo && <span className="aside">demo</span>}</div>
            <dl className="kv">
              <dt>Project</dt><dd>{r.project_id}</dd>
              <dt>Status</dt><dd>{r.status}</dd>
              <dt>Items deleted</dt><dd>{r.deleted_count}</dd>
              <dt>Kept, shared with other projects</dt><dd>{r.retained_shared_count}</dd>
            </dl>
            <p className="foot-note">Reported by the service. It holds IDs and counts only.</p>
          </section>
        )}

        {s.deletedProject ? (
          <div style={{ marginTop: "var(--s5)" }}>
            <Absent title="Nothing left to export or delete">{AFTER_DELETE}</Absent>
          </div>
        ) : (
          <div className="stack-lg" style={{ marginTop: "var(--s5)" }}>
            <div className="pair">
              <section className="panel">
                <div className="sec-head"><span className="eyebrow">Export</span><span className="aside">from the stored result</span></div>
                <ExportRow name="JSON" desc="The result exactly as stored, for tools"
                  off={formatOff("json")} onClick={() => downloadJSON(v).then(() => toast("JSON downloaded"), fail)} />
                <ExportRow name="Markdown" desc="A readable report"
                  off={formatOff("markdown")} onClick={() => downloadMarkdown(v).then(() => toast("Markdown downloaded"), fail)} />
                <ExportRow name="PDF" desc="A report to share. Vietnamese text is not reliable in PDF yet." off={formatOff("pdf")}
                  onClick={demo ? undefined : () => downloadPdf(v).then(() => toast("PDF downloaded"), fail)} />
                <ExportRow name="Your review" desc="Your marks and notes from this tab, as a separate file"
                  onClick={() => { downloadReview(v, s); toast("Review downloaded"); }} />
                
              </section>

              <section className="panel panel--col">
                <div className="sec-head"><span className="eyebrow">Delete</span><span className="aside">irreversible</span></div>
                <DeleteRow title="Delete the conversation" desc="The questions and answers only."
                  done={s.deletedConversation} off="Not available yet." onClick={() => setConfirm("conversation")} />
                <DeleteRow title="Delete the project" desc="Snapshots, evidence, questions and claims stored by Aletheia. Your source folder is not touched."
                  done={s.deletedProject} off={sample ?? (busy ? "Deleting…" : null)} onClick={() => setConfirm("project")} />
                {demo && <p className="local-note push-end"><Lock size={12} aria-hidden="true" />Demo: in-memory state only.</p>}
              </section>
            </div>

            <section className="panel">
              <div className="sec-head"><span className="eyebrow">What the file carries</span></div>
              <div className="carry">
                {([
                  ["Claims and IDs", true], ["Citations", true], ["Counter/missing evidence", true],
                  ["Denominators (null kept)", true], ["Wording and caveat", true], ["Hashes", true],
                  ["Absolute paths", false], ["Secrets, withheld rows", false], ["Evaluator truth", false],
                ] as const).map(([k, on]) => (
                  <div className="np-row" key={k}>
                    {on ? <Mark tone="ok" /> : <Mark hollow />}
                    <span className={on ? "row-k" : undefined}>{k}</span>
                    <span className="v">{on ? "included" : "never"}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>

      {confirm && <ConfirmDelete scope={confirm} onCancel={() => setConfirm(null)} onConfirm={() => del(confirm)} />}
    </div>
  );
}

function ExportRow({ name, desc, onClick, off = null }: { name: string; desc: string; onClick?: () => void; off?: string | null }) {
  const live = !!onClick && off === null;
  return (
    <div className="np-row action-row">
      {live ? <Mark tone="accent" /> : <Mark hollow />}
      <span><span className="row-k">{name}</span><span className="sub-line">{desc}</span>{off ? <span className="sub-line off-why">{off}</span> : null}</span>
      <button className="btn btn--sm" onClick={onClick} disabled={!live}>{live ? <><Download size={13} aria-hidden="true" />Download</> : "Unavailable"}</button>
    </div>
  );
}

function DeleteRow({ title, desc, done, onClick, off = null }: { title: string; desc: string; done: boolean; onClick: () => void; off?: string | null }) {
  return (
    <div className="np-row action-row">
      <span><span className="row-k">{title}</span><span className="sub-line">{desc}</span>{off && !done && <span className="sub-line off-why">{off}</span>}</span>
      <button className="btn btn--sm btn--danger" onClick={onClick} disabled={done || !!off}><Trash2 size={13} aria-hidden="true" />{done ? "Deleted" : "Delete"}</button>
    </div>
  );
}

function ConfirmDelete({ scope, onCancel, onConfirm }: { scope: Scope; onCancel: () => void; onConfirm: () => void }) {
  const [ack, setAck] = useState(false);
  const project = scope === "project";
  return (
    <Modal title={project ? "Delete the project" : "Delete the conversation"} onClose={onCancel}>
      <div className="notice notice--stop" style={{ marginTop: 0 }}>
        <Mark tone="no" />
        <div><b>This cannot be undone</b>{project
          ? "Deleted: this project's snapshots, evidence, questions and claims. Kept: your source folder, and objects other projects still use."
          : "Deleted: the questions and answers. Kept: the snapshot and evidence."}</div>
      </div>
      <label className="ack">
        <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />
        <span>I understand what will be deleted.</span>
      </label>
      <div className="modal-actions">
        <button className="btn" onClick={onCancel}>Cancel</button>
        <button className="btn btn--danger" disabled={!ack} onClick={onConfirm}><Trash2 size={13} aria-hidden="true" />{project ? "Delete project" : "Delete conversation"}</button>
      </div>
    </Modal>
  );
}
