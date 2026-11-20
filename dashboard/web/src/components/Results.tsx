import { useMemo } from "react";
import { projectView } from "../data/view";
import { linked, useResultHistory, useView } from "../service";
import { openedProject } from "../service/opened";
import { useRoute, useStore } from "../store";
import { plain } from "../vocab";
import { Absent, Id, Mark, Term } from "./primitives";
import { LateWorkList } from "./Late";

/* How many independent things is this built on, and could someone else arrive here again? */
export function Results() {
  const VIEW = useView();
  const { s } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const d = v.result.denominators;
  const ev = v.evidence[0];
  const failed = v.result.status === "technical_failure";
  const history = useResultHistory();
  const { hrefFor } = useRoute();
  // every question on this result's line keeps its own immutable result; opening one runs nothing.
  // Opening is a link to that result's address: one step in the history, and it can be opened in a
  // new tab or copied. The address alone reopens it (service/index.tsx), so no click handler is needed.
  const line = v.conversation.turns;
  const others = history.filter(h => h.result_id !== v.result.id && !line.some(t => t.result_id === h.result_id));

  const cells: [string, string, number | null][] = [
    ["Independent families", "independent_families", d.independent_families],
    ["Contexts", "contexts", d.contexts],
    ["Outputs", "outputs", d.outputs],
    ["Claims", "claims", d.claims],
  ];
  const rep: [string, string | null][] = [
    ["Snapshot sha256", v.snapshot.sha256],
    ["Evidence source sha256", ev ? ev.source_sha256 : null],
    ["Artifact manifest", null],
    ["Command and environment", null],
    ["Reproduction receipt", null],
  ];

  return (
    <div className="page" id="main" tabIndex={-1}>
      <div className="page-inner">
        <header className="page-head">
          <h1>Results &amp; reproduction</h1>
          <p className="lede">The open result, and others you opened in this tab.</p>
        </header>

        <LateWorkList />

        {v.demo_only && (
          // sample data has no stored results to list; say where they come from, and offer the way there
          <section className="panel" style={{ marginTop: "var(--s5)" }} aria-label="Stored results">
            <div className="sec-head"><span className="eyebrow">Stored results</span></div>
            {!linked ? <>
              <p className="body-copy">The sample has no stored results. The questions asked on it are in the investigation.</p>
              <div className="actions"><a className="btn btn--sm" href={hrefFor("investigation")}>Open the investigation</a></div>
            </> : openedProject() ? <>
              <p className="body-copy">Your project is saved. Ask a question about it, and its results are listed here.</p>
              <div className="actions"><a className="btn btn--sm" href={hrefFor("start")}>Go to Start</a></div>
            </> : <>
              <p className="body-copy">This is sample data. Connect a project, and its results are listed here.</p>
              <div className="actions"><a className="btn btn--sm" href={hrefFor("connect")}>Connect a project</a></div>
            </>}
          </section>
        )}

        {!v.demo_only && (
          <section className="panel" style={{ marginTop: "var(--s5)" }} aria-label="Results you can open">
            <div className="sec-head"><span className="eyebrow">Questions on this result</span><span className="aside">Stored results do not change</span></div>
            {line.map(t => (
              <div className="np-row action-row" key={t.id}>
                {t.result_id === v.result.id ? <Mark tone="accent" /> : <Mark hollow />}
                <span><span className="row-k">{t.question}</span><span className="sub-line">{t.abstained ? "Did not conclude" : "Answered"}</span></span>
                {t.result_id === v.result.id ? <span className="v">Open now</span> : <a className="btn btn--sm" href={hrefFor("investigation", { result: t.result_id })}>Open<span className="sr">: {t.question}</span></a>}
              </div>
            ))}
            {/* the conversation can be deleted while the result stays: say so rather than show a bare heading */}
            {line.length === 0 && <Absent title="No questions on this result" />}
            {others.length > 0 && <div className="sec-head" style={{ marginTop: "var(--s4)" }}><span className="eyebrow">Also opened in this tab</span></div>}
            {others.map(h => (
              <div className="np-row action-row" key={h.result_id}>
                <Mark hollow />
                <span><span className="row-k">{h.question}</span><span className="sub-line">{h.project}</span></span>
                <a className="btn btn--sm" href={hrefFor("investigation", { project: h.project_id, snapshot: h.snapshot_id, result: h.result_id })}>Open<span className="sr">: {h.question}</span></a>
              </div>
            ))}
          </section>
        )}

        <h2 className="sec-title">Denominators <span className="sec-note">never pooled</span></h2>
        <div className="denoms" role="list" aria-label="Denominators">
          {cells.map(([label, code, val]) => (
            <div key={code} role="listitem" className={`den${val === null ? " absent" : ""}`}>
              <span className="den-label tile-label">{val === null ? <Mark hollow /> : <Mark tone="accent" />}{label}</span>
              {val === null ? <span className="den-val">{plain("not_estimated")}</span> : <span className="den-val">{val}</span>}
              <code className="den-code">{val === null ? `${code} = null — not zero` : code}</code>
            </div>
          ))}
        </div>

        <div className="cols" style={{ marginTop: "var(--s6)" }}>
          <section className="panel">
            <div className="sec-head"><span className="eyebrow">Reproduction</span></div>
            {rep.map(([k, val]) => (
              <div className="np-row" key={k}>
                {val ? <Mark tone="accent" /> : <Mark hollow />}
                <span className={val ? "row-k" : undefined}>{k}</span>
                <span className={`v${val ? " mono-s" : ""}`} title={val ?? undefined}>{val ? val.slice(0, 24) + "…" : "not in view"}</span>
              </div>
            ))}
          </section>

          <aside className="stack-lg">
            <section className="panel">
              <div className="sec-head"><span className="eyebrow">Disposition</span><code className="aside mono-s"><Id v={v.result.id} /></code></div>
              <p className="big-state">{v.result.disposition === null ? "No disposition" : plain(v.result.disposition)}</p>
              <code className="fld-code">disposition: {v.result.disposition ?? "null"} · status: {v.result.status}</code>
              {failed && <p className="foot-note"><span className="tag">Preview</span></p>}
              <div className="term-row"><Term k={v.result.causal_status} hollow /></div>
              <p className="body-copy" style={{ marginTop: "var(--s3)" }}>{v.result.caveat}</p>
            </section>
            <Absent title="Machine and human evaluation" code="not in contract" />
            <Absent title="Risk–coverage and effect intervals" code="needs a population" />
          </aside>
        </div>
      </div>
    </div>
  );
}
