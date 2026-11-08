import { useState } from "react";
import { ArrowRight, FolderInput, LayoutList } from "lucide-react";
import { AFTER_DELETE, linked, port, useResultSwitch, useView } from "../service";
import { openedProject, type OpenedProject } from "../service/opened";
import { FirstQuestion, openProject, RefreshButton, RefreshStatus, RefusalNote, useRefresh } from "./Ask";
import { LateWorkList } from "./Late";
import { useRoute, useStore } from "../store";
import { Absent, Id } from "./primitives";

/* Where a session begins: your own project first, the sample clearly second. Opening the app never
   lands on a result the person did not ask for. */
export function Start() {
  const VIEW = useView();
  const { hrefFor } = useRoute();
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
          <p className="lede">Ask about a change in your ML project, then check each claim against its source.</p>
        </header>

        <LateWorkList />

        <div className="stack-lg" style={{ marginTop: "var(--s6)" }}>
          {opened ? <Continue opened={opened} /> : (
            <section className="panel">
              <h2 className="panel-title">Start with your project</h2>
              {demo
                ? <p className="body-copy">This demo cannot read real folders. Try the steps with sample data.</p>
                : <p className="body-copy">Pick a folder. You check what is read before anything is saved, and your files are never changed.</p>}
              <div className="actions">
                <a className="btn btn--primary" href={hrefFor("connect")}><FolderInput size={15} aria-hidden="true" />Connect a project</a>
              </div>
            </section>
          )}

          <section className="panel">
            <h2 className="panel-title">Look around with sample data</h2>
            {gone ? (
              <Absent title="Deleted in this session">{AFTER_DELETE}</Absent>
            ) : <>
              <p className="body-copy">Made-up data, not yours. No model was used.</p>
              <div className="actions">
                <a className="btn btn--wrap" href={hrefFor("investigation")}><LayoutList size={15} aria-hidden="true" />Open the sample: {VIEW.demo_only ? VIEW.project.display_name : "result"}</a>
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
  const { go, hrefFor } = useRoute();
  const { showResult } = useResultSwitch();
  const [snap, setSnap] = useState(opened);
  const refresh = useRefresh(snap.project_id, r => { openProject(r); setSnap({ project_id: r.project_id, snapshot_id: r.snapshot_id }); });
  return (
    <section className="panel">
      <h2 className="panel-title">Continue your project</h2>
      <p className="body-copy">Kept on this computer from your last visit: snapshot <Id className="mono-s" v={snap.snapshot_id} />.</p>
      <RefusalNote refusal={refresh.refusal} />
      <RefreshStatus r={refresh} />
      <div className="actions">
        {/* the result's own address: one step back returns here, and it can be opened in a new tab */}
        {snap.result_id && <a className="btn btn--primary" href={hrefFor("investigation", { project: snap.project_id, snapshot: snap.snapshot_id, result: snap.result_id })}><LayoutList size={15} aria-hidden="true" />Open last result<ArrowRight size={15} aria-hidden="true" /></a>}
        <RefreshButton r={refresh} />
        <a className="btn" href={hrefFor("connect")}><FolderInput size={15} aria-hidden="true" />Connect another</a>
      </div>
      <FirstQuestion bare key={snap.snapshot_id} result={snap} onAnalyzed={view => { showResult(view); go("investigation"); }} />
    </section>
  );
}
