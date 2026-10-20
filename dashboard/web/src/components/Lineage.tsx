import { Fragment } from "react";
import { ChevronRight } from "lucide-react";
import type { GraphNode, ProductView } from "../types";
import { observedInSnapshot } from "../data/view";
import { LANE_KINDS, lineageLayout } from "../data/lineage";
import { Absent, Mark, shortId } from "./primitives";

/* The lineage as a left-to-right graph: Snapshot → Evidence → Claim → Disposition.
   Columns are fixed by stage; the labels between them are the backend's own edge `kind`, so the
   drawing never invents a relation the projection does not carry. Position carries no weight;
   order within a lane is by node ID (data/lineage.ts), never by arrival order. */
const LANES: { h: string; kind: (typeof LANE_KINDS)[number] }[] = [
  { h: "Snapshot", kind: "Snapshot" },
  { h: "Evidence", kind: "EvidenceItem" },
  { h: "Claim", kind: "AtomicClaim" },
  { h: "Disposition", kind: "Disposition" },
];

/** ASSIGNED_DISPOSITION may wrap after an underscore, never mid-word. */
const breakable = (k: string, at = "_") => k.split(at).flatMap((p, i, a) => (i < a.length - 1 ? [p + at, <wbr key={i} />] : [p]));

export function Lineage({ v, selectedClaim, selectedEvidence, selectedNode, shownIds, onNode }: {
  v: ProductView; selectedClaim: string | null; selectedEvidence: string | null; selectedNode: string | null;
  shownIds: Set<string>; onNode: (n: GraphNode) => void;
}) {
  // OBSERVED_IN is emitted only for evidence observed in the analyzed snapshot, so the first link
  // says how many evidence items it actually joins instead of implying all of them
  const observed = observedInSnapshot(v);
  const layout = lineageLayout(v.graph.nodes, v.graph.edges);
  const byId = new Map(v.graph.nodes.map(n => [n.id, n]));
  const evCount = layout.lanes[1].length;

  // a dangling edge or a reused node ID means the projection is not trustworthy: draw nothing
  // rather than a partial graph. The claim table above stays complete without it.
  if (layout.dangling.length || layout.duplicates.length) {
    return (
      <Absent title="Lineage not drawn" code={layout.dangling.length ? `dangling edge ${layout.dangling[0]}` : `duplicate node ${layout.duplicates[0]}`}>
        The graph refers to nodes it does not contain. Use the claim table instead.
      </Absent>
    );
  }

  return (
    <div className="lg" aria-label="Lineage">
      <div className="lg-canvas">
        <div className="lg-row">
          {LANES.map((l, i) => {
            const ns = layout.lanes[i];
            const linkKinds = i < LANES.length - 1 ? layout.links[i] : [];
            const kinds = linkKinds.join(" · ");
            return (
              <Fragment key={l.h}>
                <div className="lg-col">
                  <div className="lg-stage">{l.h}</div>
                  {ns.length ? ns.map(n => {
                    const dim = n.kind === "AtomicClaim" && !shownIds.has(n.source_id);
                    const on = n.id === selectedNode || (!selectedNode && (n.source_id === selectedClaim || n.source_id === selectedEvidence));
                    const unobserved = n.kind === "EvidenceItem" && !observed.has(n.source_id);
                    return (
                      <button key={n.id} className={`lg-node${dim ? " faded" : ""}${unobserved ? " off" : ""}`} aria-pressed={on} onClick={() => onNode(n)}
                        title={dim ? "hidden by the current filter" : unobserved ? "no OBSERVED_IN edge to the analyzed snapshot" : n.id}>
                        <span className="i" title={n.source_id}>{breakable(shortId(n.source_id), "-")}{n.kind === "Disposition" && n.source_id === v.result.id && <b className="lg-disp">{v.result.disposition ?? "null"}</b>}</span>
                      </button>
                    );
                  }) : <div className="lg-node none"><Mark hollow /><span className="i">none</span></div>}
                </div>
                {i < LANES.length - 1 && (
                  <div className={`lg-link${linkKinds.length ? "" : " none"}`} aria-hidden="true">
                    <i className="lg-line" />
                    {linkKinds.length
                      ? <span>{breakable(kinds)}{l.kind === "Snapshot" && observed.size < evCount ? ` ${observed.size}/${evCount}` : ""}</span>
                      : <em>—</em>}
                  </div>
                )}
              </Fragment>
            );
          })}
        </div>
      </div>
      <details className="lg-foot">
        <summary><ChevronRight size={13} aria-hidden="true" />Edges <span className="dim">{layout.edges.length}</span></summary>
        <div className="path">
          {layout.edges.map(e => (
            <div className="path-row" key={e.id}>
              <span>{byId.get(e.source)?.source_id ?? e.source}</span>
              <span className="v">{e.kind}</span>
              <span>{byId.get(e.target)?.source_id ?? e.target}</span>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
