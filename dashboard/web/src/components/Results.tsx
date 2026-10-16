import { useMemo } from "react";
import { projectView } from "../data/view";
import { useView } from "../service";
import { useStore } from "../store";
import { plain } from "../vocab";
import { Absent, Id, Mark, Term } from "./primitives";

/* How many independent things is this built on, and could someone else arrive here again? */
export function Results() {
  const VIEW = useView();
  const { s } = useStore();
  const v = useMemo(() => projectView(VIEW, s), [VIEW, s]);
  const d = v.result.denominators;
  const ev = v.evidence[0];
  const failed = v.result.status === "technical_failure";

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
        </header>

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
