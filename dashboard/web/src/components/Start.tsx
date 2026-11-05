import { useState } from "react";
import { ArrowRight, FolderInput, LayoutList, RefreshCw } from "lucide-react";
import { AFTER_DELETE, isProductError, linked, linkIds, port, scoped, useResultSwitch, useView } from "../service";
import { openedProject, type OpenedProject } from "../service/opened";
import { FirstQuestion, openProject, RefusalNote, type Refusal } from "./Ask";
import { LateWorkList } from "./Late";
import { useRoute, useStore } from "../store";
import { Absent, Id } from "./primitives";

/* Where a session begins: your own project first, the sample clearly second. Opening the app never
   lands on a result the person did not ask for. */
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
      <div className="page-inner page-inner--narrow">
        <header className="page-head">
          <h1>Check what the evidence supports</h1>
          <p className="lede">Import a project snapshot, ask about a change, then read each claim next to the source it cites. Aletheia states what was observed and what stays unknown.</p>
        </header>

        <LateWorkList />

        <div className="stack-lg" style={{ marginTop: "var(--s6)" }}>
          {opened ? <Continue opened={opened} /> : (
            <section className="panel">
              <h2 className="panel-title">Start with your project</h2>
              {demo
                ? <p className="body-copy">This demo build cannot read a real folder. You can walk through connecting with sample data.</p>
                : <p className="body-copy">Choose one folder on this computer. You review what would be read, and map its metrics, before anything is stored. Your source files are never changed.</p>}
              <div className="actions">
                <button className="btn btn--primary" onClick={() => go("connect")}><FolderInput size={15} aria-hidden="true" />Connect a project</button>
              </div>
            </section>
          )}

          <section className="panel">
            <h2 className="panel-title">Look around with sample data</h2>
            {gone ? (
              <Absent title="Deleted in this session">{AFTER_DELETE}</Absent>
            ) : <>
              <p className="body-copy">A made-up project, so you can see how a claim and its source read together. It is not your data and no model produced it.</p>
              <div className="actions">
                <button className="btn" onClick={() => go("investigation")}><LayoutList size={15} aria-hidden="true" />Open the sample: {VIEW.demo_only ? VIEW.project.display_name : "result"}</button>
              </div>
            </>}
          </section>
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
  const refresh = () => {
    setBusy(true); setRefusal(null);
    return scoped("refresh", () => port.refresh(snap.project_id), {
      done: r => { openProject(r); setSnap({ project_id: r.project_id, snapshot_id: r.snapshot_id }); },
      fail: e => setRefusal(isProductError(e) ? { code: e.code, message: e.safe_message } : { code: "unavailable", message: "The service did not answer." }),
      always: () => setBusy(false),
    });
  };
  return (
    <section className="panel">
      <h2 className="panel-title">Continue your project</h2>
      <p className="body-copy">Kept on this computer from your last visit: snapshot <Id className="mono-s" v={snap.snapshot_id} />.</p>
      <RefusalNote refusal={refusal} />
      <div className="actions">
        {snap.result_id && <button className="btn btn--primary" onClick={() => { linkIds({ project: snap.project_id, snapshot: snap.snapshot_id, result: snap.result_id }); go("investigation"); }}><LayoutList size={15} aria-hidden="true" />Open last result<ArrowRight size={15} aria-hidden="true" /></button>}
        <button className="btn" onClick={() => void refresh()} disabled={busy} aria-busy={busy}><RefreshCw size={15} aria-hidden="true" />{busy ? "Refreshing…" : "Refresh"}</button>
        <button className="btn" onClick={() => go("connect")}><FolderInput size={15} aria-hidden="true" />Connect another</button>
      </div>
      <FirstQuestion bare key={snap.snapshot_id} result={snap} onAnalyzed={view => { showResult(view); go("investigation"); }} />
    </section>
  );
}
