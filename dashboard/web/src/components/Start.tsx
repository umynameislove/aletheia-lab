import { useState } from "react";
import { ArrowRight, FolderInput, GitBranch, LayoutList, MessageSquareText, RefreshCw } from "lucide-react";
import { AFTER_DELETE, isProductError, linked, linkIds, port, useResultSwitch, useView } from "../service";
import { openedProject, type OpenedProject } from "../service/opened";
import { FirstQuestion, openProject, RefusalNote, type Refusal } from "./Connect";
import { useRoute, useStore } from "../store";
import { Absent, Id } from "./primitives";

/* Where a session begins: the three-step path, then two equal ways in. */
export function Start() {
  const VIEW = useView();
  const { go } = useRoute();
  const { s } = useStore();
  const gone = s.deletedProject;
  const demo = port.kind === "demo";
  // Live only: the project confirmed or opened before this reload, by its stored IDs
  const opened = linked ? openedProject() : null;

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head">
          <h1>Check what the evidence supports</h1>
        </header>

        <ol className="path3" aria-label="How it works">
          <li><FolderInput size={16} aria-hidden="true" /><span><b>Import</b>a project snapshot</span></li>
          <li><MessageSquareText size={16} aria-hidden="true" /><span><b>Ask</b>about a change</span></li>
          <li><GitBranch size={16} aria-hidden="true" /><span><b>Trace</b>each claim to its evidence</span></li>
        </ol>

        <div className="pair" style={{ marginTop: "var(--s6)" }}>
          <section className="panel panel--col">
            <div className="sec-head"><span className="eyebrow">Demo project</span></div>
            {gone ? (
              <Absent title="Deleted in this session">{AFTER_DELETE}</Absent>
            ) : <>
              <dl className="kv">
                <dt>project</dt><dd>{VIEW.project.id}</dd>
                <dt>snapshot</dt><dd>{VIEW.snapshot.baseline_id} → {VIEW.snapshot.id}</dd>
                <dt>mode</dt><dd>{VIEW.mode}</dd>
              </dl>
              <div className="actions push-end">
                <button className="btn btn--primary" onClick={() => go("investigation")}><LayoutList size={14} aria-hidden="true" />Open investigation</button>
                <button className="btn" onClick={() => go("project")}>Overview<ArrowRight size={14} aria-hidden="true" /></button>
              </div>
            </>}
          </section>

          {opened ? <Continue opened={opened} /> : (
            <section className="panel panel--col">
              <div className="sec-head"><span className="eyebrow">Your project</span>{demo && <span className="aside">demo — no real folder is read</span>}</div>
              <p className="body-copy">Grant one folder, review the scan, then confirm.</p>
              <div className="actions push-end">
                <button className="btn" onClick={() => go("connect")}><FolderInput size={14} aria-hidden="true" />Connect a project</button>
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

/* Continue the project kept from before the reload: open its last result, refresh, or ask about
   the kept snapshot. The contract has no project listing, so this is the only project offered. */
function Continue({ opened }: { opened: OpenedProject }) {
  const { go } = useRoute();
  const { showResult } = useResultSwitch();
  const [snap, setSnap] = useState(opened);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal>(null);
  const refresh = async () => {
    setBusy(true); setRefusal(null);
    try { const r = await port.refresh(snap.project_id); openProject(r); setSnap({ project_id: r.project_id, snapshot_id: r.snapshot_id }); }
    catch (e) { setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }); }
    finally { setBusy(false); }
  };
  return (
    <section className="panel panel--col">
      <div className="sec-head"><span className="eyebrow">Your project</span><span className="aside">kept on this machine</span></div>
      <dl className="kv">
        <dt>project</dt><dd><Id v={snap.project_id} /></dd>
        <dt>snapshot</dt><dd><Id v={snap.snapshot_id} /></dd>
      </dl>
      <RefusalNote refusal={refusal} />
      <FirstQuestion bare key={snap.snapshot_id} result={snap} onAnalyzed={view => { showResult(view); go("investigation"); }} />
      <div className="actions push-end">
        {snap.result_id && <button className="btn btn--primary" onClick={() => { linkIds({ project: snap.project_id, snapshot: snap.snapshot_id, result: snap.result_id }); go("investigation"); }}><LayoutList size={14} aria-hidden="true" />Open last result</button>}
        <button className="btn" onClick={() => void refresh()} disabled={busy}><RefreshCw size={14} aria-hidden="true" />{busy ? "Refreshing…" : "Refresh"}</button>
        <button className="btn" onClick={() => go("connect")}><FolderInput size={14} aria-hidden="true" />Connect another</button>
      </div>
    </section>
  );
}
