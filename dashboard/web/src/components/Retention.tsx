import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronRight, Download, Lock, Trash2 } from "lucide-react";
import { projectView } from "../data/view";
import { AFTER_DELETE, capabilities, fence, port, scoped, useView } from "../service";
import { forgetProject } from "../service/opened";
import { downloadReview, reportName, saveReport, type ReportKind } from "../export";
import { useStore } from "../store";
import { Modal } from "./Modal";
import { RefusalNote, refusalOf, type Refusal } from "./Ask";
import { Absent, Mark } from "./primitives";

type Scope = "conversation" | "project";

/* Exactly what goes into the file, and exactly what is gone afterwards. */
export function Retention() {
  const VIEW = useView();
  const { s, dispatch, toast } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const [confirm, setConfirm] = useState<Scope | null>(null);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  // a second press can arrive before the re-render that marks the button busy; this cannot be undone
  const deleting = useRef(false);
  const r = s.receipt;
  const demo = port.kind === "demo";
  // after a delete the row that opened the dialog is gone: focus goes to what the delete produced
  const receiptRef = useRef<HTMLElement>(null);
  const toReceipt = useRef(false);
  useEffect(() => { if (r && toReceipt.current) { toReceipt.current = false; receiptRef.current?.focus(); } }, [r]);
  // the failure preview is a demo scaffold over the screens; files always come from the stored result
  // Live shows the service's demo_view until a project is connected; that view is not stored, so
  // the service has nothing to export or delete for it
  const sample = !demo && VIEW.demo_only ? "This is sample data. Connect a project first." : null;
  const formatOff = (f: "json" | "markdown" | "pdf") => (capabilities.supported_export_formats.includes(f) ? exportOff : "Not available in this build.");
  const exportOff = sample ?? (s.failed ? "Turn off “Preview a failed run” to export." : null);

  /* Each format says where it stands: fetching, handed to the browser under which name, or why not. The
     page cannot see whether the browser kept the file (it may ask, or be refused), so it never says "saved".
     If the person has moved to another result meanwhile, nothing is saved in the new context. */
  const [exporting, setExporting] = useState<Partial<Record<ReportKind, ExportState>>>({});
  const startExport = (kind: ReportKind) => {
    setExporting(x => ({ ...x, [kind]: { status: "busy" } }));
    void scoped("export", () => port.export_report(v.result.id, kind), {
      key: `export:${v.result.id}:${kind}`,
      done: bytes => {
        saveReport(v, kind, bytes);
        setExporting(x => ({ ...x, [kind]: { status: "saved", name: reportName(v, kind) } }));
        toast("Download started", reportName(v, kind));
      },
      fail: e => setExporting(x => ({ ...x, [kind]: { status: "failed", refusal: refusalOf(e) } })),
    });
  };

  const del = async (scope: Scope) => {
    if (scope !== "project" || deleting.current) return; // no service method deletes a conversation alone
    deleting.current = true;
    setBusy(true); setRefusal(null);
    const ticket = fence.ticket();
    try {
      const receipt = await port.delete_project(VIEW.project.id);
      // the project is gone wherever the person is now, so its state closes either way; the receipt
      // names its project and cannot close another (service/transitions.ts). Only the toast and the focus move are scoped.
      forgetProject(VIEW.project.id);
      dispatch({ type: "deleteProject", receipt: { ...receipt, scope: "" } });
      if (fence.current(ticket)) { toReceipt.current = true; toast("Project deleted"); }
      setConfirm(null);
    } catch (e) {
      // the dialog stays open and says why, in place; a toast would vanish before the remedy is read.
      // If the person has moved on, the dialog no longer belongs to what is on screen: it just closes.
      if (fence.current(ticket)) setRefusal(refusalOf(e));
      else setConfirm(null);
    } finally { deleting.current = false; setBusy(false); }
  };

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head">
          <h1>Data &amp; retention</h1>
        </header>

        {r && (
          <section className="panel receipt" aria-label="Deletion receipt" tabIndex={-1} ref={receiptRef}>
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
                <div className="sec-head"><span className="eyebrow">Export</span></div>
                {!sample && <p className="body-copy export-for">Files for: <b>{v.conversation.turns[v.conversation.turns.length - 1]?.question ?? v.result.summary}</b></p>}
                <ExportRow name="JSON" desc="The result exactly as stored, for tools" state={exporting.json}
                  off={formatOff("json")} onClick={() => startExport("json")} />
                <ExportRow name="Markdown" desc="A readable report" state={exporting.markdown}
                  off={formatOff("markdown")} onClick={() => startExport("markdown")} />
                <ExportRow name="PDF" desc="A report to share. Vietnamese may not print correctly." off={formatOff("pdf")} state={exporting.pdf}
                  onClick={() => startExport("pdf")} />
                <ExportRow name="Your review" desc="Your marks and notes"
                  onClick={() => { downloadReview(v, s); toast("Download started", "Your review"); }} />

              </section>

              <section className="panel panel--col">
                <div className="sec-head"><span className="eyebrow">Delete</span><span className="aside">irreversible</span></div>
                <DeleteRow title="Delete the conversation" desc="The questions and answers only."
                  done={s.deletedConversation} off="Not available yet." onClick={() => setConfirm("conversation")} />
                <DeleteRow title="Delete the project" desc="Everything Aletheia stored for it. Your folder is not touched."
                  done={s.deletedProject} off={sample ?? (busy ? "Deleting…" : null)} onClick={() => setConfirm("project")} />
                {demo && <p className="local-note push-end"><Lock size={12} aria-hidden="true" />Demo: Reset demo data brings it back.</p>}
              </section>
            </div>

            <details className="more">
              <summary><ChevronRight size={14} aria-hidden="true" />What is in the files</summary>
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
            </details>
          </div>
        )}
      </div>

      {confirm && <ConfirmDelete scope={confirm} busy={busy} refusal={refusal} onCancel={() => { setConfirm(null); setRefusal(null); }} onConfirm={() => void del(confirm)} />}
    </div>
  );
}

type ExportState = { status: "busy" } | { status: "saved"; name: string } | { status: "failed"; refusal: Refusal };

function ExportRow({ name, desc, onClick, off = null, state }: { name: string; desc: string; onClick?: () => void; off?: string | null; state?: ExportState }) {
  const live = !!onClick && off === null;
  const busy = state?.status === "busy";
  const why = useId();
  return (
    <div className="np-row action-row">
      {live ? <Mark tone="accent" /> : <Mark hollow />}
      <span>
        <span className="row-k">{name}</span><span className="sub-line">{desc}</span>
        {off ? <span className="sub-line off-why" id={why}>{off}</span> : null}
        {/* the status is always there, so the first outcome is read too; a failure is its own alert */}
        <span className="sub-line" role="status">{state?.status === "saved" && `Sent to your browser as ${state.name}`}</span>
        {state?.status === "failed" && <RefusalNote refusal={state.refusal} compact />}
      </span>
      {/* busy keeps focus (aria-disabled); unavailable cannot change while focused, so it is disabled, with the reason beside it */}
      <button className="btn btn--sm" onClick={() => { if (!busy) onClick?.(); }} disabled={!live} aria-disabled={busy || undefined} aria-busy={busy} aria-describedby={off ? why : undefined}>
        {!live ? "Unavailable" : busy ? "Preparing…" : <><Download size={13} aria-hidden="true" />{state?.status === "failed" ? "Try again" : "Download"}</>}
      </button>
    </div>
  );
}

function DeleteRow({ title, desc, done, onClick, off = null }: { title: string; desc: string; done: boolean; onClick: () => void; off?: string | null }) {
  const why = useId();
  return (
    <div className="np-row action-row">
      <span><span className="row-k">{title}</span><span className="sub-line">{desc}</span>{off && !done && <span className="sub-line off-why" id={why}>{off}</span>}</span>
      <button className="btn btn--sm btn--danger" onClick={onClick} disabled={done || !!off} aria-describedby={off && !done ? why : undefined}><Trash2 size={13} aria-hidden="true" />{done ? "Deleted" : "Delete"}</button>
    </div>
  );
}

/* While deleting, nothing closes the dialog and the button cannot be pressed again. A refusal is shown
   here, in the dialog, and it closes only once the service has said the project is gone. */
function ConfirmDelete({ scope, busy, refusal, onCancel, onConfirm }: { scope: Scope; busy: boolean; refusal: Refusal; onCancel: () => void; onConfirm: () => void }) {
  const [ack, setAck] = useState(false);
  const project = scope === "project";
  return (
    <Modal title={project ? "Delete the project" : "Delete the conversation"} onClose={onCancel} busy={busy}>
      <div className="notice notice--stop" style={{ marginTop: 0 }}>
        <Mark tone="no" />
        <div><b>This cannot be undone</b>{project
          ? "Deleted: this project's snapshots, evidence, questions and claims. Kept: your source folder, and objects other projects still use. This folder cannot be imported into this store again."
          : "Deleted: the questions and answers. Kept: the snapshot and evidence."}</div>
      </div>
      <label className="ack">
        <input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />
        <span>I understand what will be deleted.</span>
      </label>
      <RefusalNote refusal={refusal} />
      <div className="modal-actions">
        <button className="btn" aria-disabled={busy || undefined} onClick={() => { if (!busy) onCancel(); }}>Cancel</button>
        <button className="btn btn--danger" disabled={!ack} aria-disabled={busy || undefined} aria-busy={busy} onClick={() => { if (!busy) onConfirm(); }}>
          <Trash2 size={13} aria-hidden="true" />{busy ? "Deleting…" : project ? "Delete project" : "Delete conversation"}
        </button>
      </div>
    </Modal>
  );
}
