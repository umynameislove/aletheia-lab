import { Fragment, useMemo, useState } from "react";
import type { GraphEdge, GraphNode, ProductView } from "../types";
import { observedInSnapshot } from "../data/view";
import { LANE_KINDS, lineageLayout } from "../data/lineage";
import { pageOf } from "../data/paging";
import { claimPath, FULL_GRAPH_LIMIT, LANE_WINDOW } from "../data/subgraph";
import { useRoute } from "../store";
import { plain } from "../vocab";
import { Absent, Id, Mark, shortId } from "./primitives";
import { Pager } from "./Worklist";

/* The lineage: Snapshot → Evidence → Claim → Disposition. A tool to open when needed, not the page's
   centre. Columns are fixed by stage; the labels between them are the backend's own edge `kind`, so the
   drawing never invents a relation the projection does not carry. Position carries no weight; order
   within a lane is by node ID (data/lineage.ts), never by arrival order. The table below says the same
   thing without a picture, for the keyboard and screen readers. */
const LANES: { h: string; kind: (typeof LANE_KINDS)[number] }[] = [
  { h: "Snapshot", kind: "Snapshot" },
  { h: "Evidence", kind: "EvidenceItem" },
  { h: "Claim", kind: "AtomicClaim" },
  { h: "Disposition", kind: "Disposition" },
];

/** ASSIGNED_DISPOSITION may wrap after an underscore, never mid-word. */
const breakable = (k: string, at = "_") => k.split(at).flatMap((p, i, a) => (i < a.length - 1 ? [p + at, <wbr key={i} />] : [p]));

export function LineagePanel({ v, selectedClaim, selectedEvidence, selectedNode, shownIds, onNode }: {
  v: ProductView; selectedClaim: string | null; selectedEvidence: string | null; selectedNode: string | null;
  shownIds: Set<string>; onNode: (n: GraphNode) => void;
}) {
  const large = v.graph.nodes.length > FULL_GRAPH_LIMIT;
  const [whole, setWhole] = useState(false);
  const path = useMemo(() => (large && selectedClaim && !whole ? claimPath(v.graph.nodes, v.graph.edges, selectedClaim) : null), [large, selectedClaim, whole, v.graph]);
  const nodes = path?.nodes ?? v.graph.nodes;
  const edges = path?.edges ?? v.graph.edges;
  const layout = useMemo(() => lineageLayout(nodes, edges), [nodes, edges]);

  // a dangling edge or a reused node ID means the projection is not trustworthy: draw nothing
  // rather than a partial graph. The claim list stays complete without it.
  const full = useMemo(() => lineageLayout(v.graph.nodes, v.graph.edges), [v.graph]);
  if (full.dangling.length || full.duplicates.length) {
    return (
      <Absent title="Lineage not drawn" code={full.dangling.length ? `dangling edge ${full.dangling[0]}` : `duplicate node ${full.duplicates[0]}`}>
        The graph refers to nodes it does not contain. Use the claim list instead.
      </Absent>
    );
  }
  if (!v.graph.nodes.length) return <Absent title="No lineage in this result" />;

  return (
    <div className="lg" aria-label="Lineage">
      {large && (
        <div className="notice" role="status" style={{ marginTop: 0 }}>
          <Mark hollow />
          <div>
            <b>{path ? "Showing this claim's path" : "Showing part of the graph at a time"}</b>
            {path ? `${path.nodes.length.toLocaleString()} of ${v.graph.nodes.length.toLocaleString()} nodes.` : `${v.graph.nodes.length.toLocaleString()} nodes in this result. Each stage opens with its first ${LANE_WINDOW}; select a claim to see only its path.`}
          </div>
          {path && <div className="n-act"><button className="btn btn--sm" onClick={() => setWhole(true)}>Show all stages</button></div>}
          {!path && selectedClaim && <div className="n-act"><button className="btn btn--sm" onClick={() => setWhole(false)}>Show this claim&rsquo;s path</button></div>}
        </div>
      )}
      <Drawing v={v} layout={layout} selectedClaim={selectedClaim} selectedEvidence={selectedEvidence} selectedNode={selectedNode} shownIds={shownIds} onNode={onNode} />
      <EdgeTable nodes={nodes} edges={layout.edges} selectedNode={selectedNode} onNode={onNode} />
    </div>
  );
}

function Drawing({ v, layout, selectedClaim, selectedEvidence, selectedNode, shownIds, onNode }: {
  v: ProductView; layout: ReturnType<typeof lineageLayout>; selectedClaim: string | null; selectedEvidence: string | null; selectedNode: string | null;
  shownIds: Set<string>; onNode: (n: GraphNode) => void;
}) {
  // OBSERVED_IN is emitted only for evidence observed in the analyzed snapshot, so the first link
  // says how many evidence items it actually joins instead of implying all of them
  const observed = useMemo(() => observedInSnapshot(v), [v]);
  const evCount = layout.lanes[1].length;
  const [limit, setLimit] = useState<Record<number, number>>({});
  return (
    <div className="lg-canvas">
      <div className="lg-row">
        {LANES.map((l, i) => {
          const all = layout.lanes[i];
          const cap = limit[i] ?? LANE_WINDOW;
          const ns = all.slice(0, cap);
          const linkKinds = i < LANES.length - 1 ? layout.links[i] : [];
          const kinds = linkKinds.join(" · ");
          return (
            <Fragment key={l.h}>
              <div className="lg-col">
                <div className="lg-stage">{l.h}{all.length > 1 && <span className="dim"> {all.length.toLocaleString()}</span>}</div>
                {ns.length ? ns.map(n => {
                  const dim = n.kind === "AtomicClaim" && !shownIds.has(n.source_id);
                  const on = n.id === selectedNode || (!selectedNode && (n.source_id === selectedClaim || n.source_id === selectedEvidence));
                  const unobserved = n.kind === "EvidenceItem" && !observed.has(n.source_id);
                  return (
                    <button key={n.id} className={`lg-node${dim ? " faded" : ""}${unobserved ? " off" : ""}`} aria-pressed={on} onClick={() => onNode(n)}
                      aria-label={`${l.h} ${shortId(n.source_id)}${dim ? ", hidden by the current filter" : ""}${unobserved ? ", not observed in the analyzed snapshot" : ""}`}
                      title={dim ? "hidden by the current filter" : unobserved ? "no OBSERVED_IN edge to the analyzed snapshot" : n.id}>
                      <span className="i">{breakable(shortId(n.source_id), "-")}{n.kind === "Disposition" && n.source_id === v.result.id && <b className="lg-disp">{v.result.disposition ?? "null"}</b>}</span>
                    </button>
                  );
                }) : <div className="lg-node none"><Mark hollow /><span className="i">none</span></div>}
                {all.length > ns.length && <button className="btn btn--sm btn--ghost" onClick={() => setLimit(x => ({ ...x, [i]: cap + 50 }))}>Show 50 more ({(all.length - ns.length).toLocaleString()} left)</button>}
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
  );
}

/* The drawing in words, paged. Every endpoint opens the same selection the drawing does. */
function EdgeTable({ nodes, edges, selectedNode, onNode }: { nodes: readonly GraphNode[]; edges: GraphEdge[]; selectedNode: string | null; onNode: (n: GraphNode) => void }) {
  const byId = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes]);
  const [requested, setRequested] = useState(1);
  const page = pageOf(edges, requested);
  const end = (id: string) => {
    const n = byId.get(id);
    if (!n) return <span className="dim">{shortId(id)}</span>;
    return <button className="link-btn" aria-pressed={n.id === selectedNode} onClick={() => onNode(n)} title={n.source_id}>{n.kind} <span className="mono-s">{shortId(n.source_id)}</span></button>;
  };
  return (
    <div className="lg-foot">
      <h3 className="src-head">Relations, as a table <span className="dim">{edges.length.toLocaleString()}</span></h3>
      <div className="tbl-wrap">
        <table className="tbl tbl--plain tbl--edges">
          <caption className="sr">Each relation in the lineage, from one node to another</caption>
          <thead><tr><th scope="col">From</th><th scope="col">Relation</th><th scope="col">To</th></tr></thead>
          <tbody>
            {page.rows.map(e => <tr key={e.id}><td>{end(e.source)}</td><td><code className="mono-s">{e.kind}</code></td><td>{end(e.target)}</td></tr>)}
          </tbody>
        </table>
      </div>
      {page.pages > 1 && <Pager page={page} noun="relations" onPage={setRequested} />}
    </div>
  );
}

/* What happened, in order: the snapshot pair, then each question asked on this result's line.
   Built only from the turns in the view; this is not an activity feed. */
export function TimelinePanel({ v }: { v: ProductView }) {
  const { setQ } = useRoute();
  return (
    <ol className="timeline" aria-label="Timeline of this result">
      <li className="tl-item">
        <span className="tl-dot" aria-hidden="true" />
        <div>
          <b>{v.snapshot.baseline_id === v.snapshot.id ? "First snapshot" : "Snapshot compared with its baseline"}</b>
          <span className="sub-line">Snapshot <Id className="mono-s" v={v.snapshot.id} />{v.snapshot.baseline_id !== v.snapshot.id && <> against <Id className="mono-s" v={v.snapshot.baseline_id} /></>}</span>
          <span className="sub-line">{v.snapshot.metric_changes.length ? `${v.snapshot.metric_changes.length} metric change${v.snapshot.metric_changes.length === 1 ? "" : "s"} recorded` : "No metric change recorded"}</span>
        </div>
      </li>
      {v.conversation.turns.map((t, i) => (
        <li className="tl-item" key={t.id}>
          <span className="tl-dot" aria-hidden="true" />
          <div>
            <button className="link-btn tl-q" onClick={() => setQ({ turn: i + 1 === v.conversation.turns.length ? null : String(i + 1), panel: null })}>{t.question}</button>
            <span className="sub-line">{t.abstained ? "Did not conclude" : "Answered"} · {plain(t.runtime.provider)}</span>
            <span className="sub-line">Result <Id className="mono-s" v={t.result_id} />{t.result_id === v.result.id && " (open now)"}</span>
          </div>
        </li>
      ))}
      {v.conversation.turns.length === 0 && <li className="tl-item"><span className="tl-dot" aria-hidden="true" /><div><b>No questions recorded</b></div></li>}
    </ol>
  );
}
