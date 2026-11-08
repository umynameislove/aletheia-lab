import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import type { GraphEdge, GraphNode, ProductView } from "../types";
import { observedInSnapshot } from "../data/view";
import { LANE_KINDS, lineageLayout, ZOOM_MAX, ZOOM_MIN, zoomKey, zoomStep } from "../data/lineage";
import { pageOf } from "../data/paging";
import { claimPath, FULL_GRAPH_LIMIT, LANE_WINDOW } from "../data/subgraph";
import { useRoute } from "../store";
import { plain } from "../vocab";
import { Absent, Id, Mark, shortId, TableScroll } from "./primitives";
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
    <div className="lg">
      {large && (
        <div className="notice" style={{ marginTop: 0 }}>
          <Mark hollow />
          {/* only the words are the status: the button beside them would be read again on every change */}
          <div role="status">
            <b>{path ? "Showing this claim's path" : "Showing part of the graph at a time"}</b>
            {path ? `${path.nodes.length.toLocaleString()} of ${v.graph.nodes.length.toLocaleString()} nodes.` : `${v.graph.nodes.length.toLocaleString()} nodes in this result. Each stage opens with its first ${LANE_WINDOW}; select a claim to see only its path.`}
          </div>
          {/* one button that changes its label, so focus stays on it */}
          {selectedClaim && <div className="n-act"><button className="btn btn--sm" onClick={() => setWhole(w => !w)}>{whole ? <>Show this claim&rsquo;s path</> : "Show all stages"}</button></div>}
        </div>
      )}
      {/* a new result starts at 100%: the level belongs to the drawing it was set on */}
      <ZoomableDrawing key={v.result.id}>
        {zoom => <Drawing v={v} layout={layout} selectedClaim={selectedClaim} selectedEvidence={selectedEvidence} selectedNode={selectedNode} shownIds={shownIds} onNode={onNode} zoom={zoom} />}
      </ZoomableDrawing>
      <EdgeTable nodes={nodes} edges={layout.edges} selectedNode={selectedNode} onNode={onNode} />
    </div>
  );
}

/* Zoom and pan for the drawing. The drawing is ordinary content in its own native scroll box, so the
   wheel, the trackpad, touch and the scrollbar pan it as anywhere else, and pinch and Ctrl/Cmd + wheel
   stay the browser's page zoom: nothing here listens to the wheel or to dragging. The buttons do the
   same for a keyboard or a single pointer, and + - 0 work while focus is in the drawing. The arrow
   keys are left alone: the investigation uses them to move between claims. The table below stays the
   way to read the lineage without the picture; zooming changes nothing a screen reader hears. */
function ZoomableDrawing({ children }: { children: (zoom: number) => ReactNode }) {
  const [zoom, setZoom] = useState(1);
  const canvas = useRef<HTMLDivElement>(null);
  const id = useId();
  // where the view was centred before a change of level, as a share of the drawing's width
  const centre = useRef<number | null>(null);
  const [edges, setEdges] = useState({ scrolls: false, left: false, right: false });
  // which way the drawing can still be panned, for the pan buttons
  const measure = useCallback(() => {
    const c = canvas.current;
    if (!c) return;
    const max = c.scrollWidth - c.clientWidth;
    const next = { scrolls: max > 1, left: c.scrollLeft > 1, right: c.scrollLeft < max - 1 };
    setEdges(e => (e.scrolls === next.scrolls && e.left === next.left && e.right === next.right ? e : next));
  }, []);

  const zoomBy = (by: -1 | 0 | 1) => {
    const next = zoomStep(zoom, by);
    if (next === zoom) return;
    const c = canvas.current;
    centre.current = c && c.scrollWidth ? (c.scrollLeft + c.clientWidth / 2) / c.scrollWidth : null;
    setZoom(next);
  };

  // after a change of level, keep in view what the person was looking at: the node with focus, else
  // the selected node, else the same part of the drawing. The browser centres the node itself: Safari
  // before late 2025 measured boxes inside CSS zoom unzoomed, so sums over getBoundingClientRect drift.
  useLayoutEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const focused = c.contains(document.activeElement) && document.activeElement !== c ? document.activeElement as HTMLElement : null;
    const anchor = focused ?? c.querySelector<HTMLElement>('.lg-node[aria-pressed="true"]');
    if (anchor) {
      anchor.scrollIntoView({ block: "nearest", inline: "center" });
    } else if (centre.current !== null) {
      c.scrollLeft = centre.current * c.scrollWidth - c.clientWidth / 2;
    }
    centre.current = null;
    // a zoomed box keeps its own size in its own units, so a resize observer does not see this change
    measure();
  }, [zoom, measure]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    c.addEventListener("scroll", measure, { passive: true });
    const watch = new ResizeObserver(measure);
    watch.observe(c);
    if (c.firstElementChild) watch.observe(c.firstElementChild);
    return () => { c.removeEventListener("scroll", measure); watch.disconnect(); };
  }, [measure]);

  const pan = (dir: -1 | 1) => canvas.current?.scrollBy({ left: dir * canvas.current.clientWidth * 0.8 });
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const by = zoomKey(e);
    if (by === null) return;
    e.preventDefault();
    zoomBy(by);
  };
  // at a limit the button stays focusable and says so, instead of vanishing from under the keyboard
  const off = (no: boolean) => ({ "aria-disabled": no || undefined, "aria-controls": id });
  const reset = useRef<HTMLButtonElement>(null);
  const toReset = useCallback(() => reset.current?.focus(), []);

  return (
    <div className="lg-view" onKeyDown={onKey}>
      <div className="lg-tools" role="group" aria-label="Zoom and pan the drawing">
        <button className="icon-btn" aria-label="Zoom out" title="Zoom out  (-)" {...off(zoom <= ZOOM_MIN)} onClick={() => zoomBy(-1)}><ZoomOut size={15} aria-hidden="true" /></button>
        <output className="lg-level" aria-live="polite"><span className="sr">Zoom </span>{Math.round(zoom * 100)}%</output>
        <button className="icon-btn" aria-label="Zoom in" title="Zoom in  (+)" {...off(zoom >= ZOOM_MAX)} onClick={() => zoomBy(1)}><ZoomIn size={15} aria-hidden="true" /></button>
        <button ref={reset} className="btn btn--sm btn--ghost" title="Back to 100%  (0)" {...off(zoom === 1)} onClick={() => zoomBy(0)}>Reset<span className="sr"> zoom</span></button>
        {edges.scrolls && <PanButtons left={!edges.left} right={!edges.right} off={off} pan={pan} onGone={toReset} />}
      </div>
      <div className="lg-canvas" id={id} ref={canvas}>{children(zoom)}</div>
    </div>
  );
}

/* Shown only while the drawing is wider than its box. Zooming back out from a pan button removes them,
   so focus goes to Reset rather than falling to the page. */
function PanButtons({ left, right, off, pan, onGone }: {
  left: boolean; right: boolean; off: (no: boolean) => Record<string, unknown>; pan: (dir: -1 | 1) => void; onGone: () => void;
}) {
  const box = useRef<HTMLSpanElement>(null);
  // still in the page at this point, so it can tell whether it had focus; Reset gets it once this commit ends
  useLayoutEffect(() => () => {
    if (box.current?.contains(document.activeElement)) queueMicrotask(onGone);
  }, [onGone]);
  return (
    <span className="lg-pan" ref={box}>
      <button className="icon-btn" aria-label="Pan left" title="Pan left" {...off(left)} onClick={() => pan(-1)}><ChevronLeft size={15} aria-hidden="true" /></button>
      <button className="icon-btn" aria-label="Pan right" title="Pan right" {...off(right)} onClick={() => pan(1)}><ChevronRight size={15} aria-hidden="true" /></button>
    </span>
  );
}

function Drawing({ v, layout, selectedClaim, selectedEvidence, selectedNode, shownIds, onNode, zoom }: {
  v: ProductView; layout: ReturnType<typeof lineageLayout>; selectedClaim: string | null; selectedEvidence: string | null; selectedNode: string | null;
  shownIds: Set<string>; onNode: (n: GraphNode) => void; zoom: number;
}) {
  // OBSERVED_IN is emitted only for evidence observed in the analyzed snapshot, so the first link
  // says how many evidence items it actually joins instead of implying all of them
  const observed = useMemo(() => observedInSnapshot(v), [v]);
  const evCount = layout.lanes[1].length;
  const [limit, setLimit] = useState<Record<number, number>>({});
  // "Show 50 more" goes once nothing is left: focus moves to the first node it revealed, not to the page
  const reveal = useRef<string | null>(null);
  const focusRevealed = (el: HTMLButtonElement | null) => { if (el) { reveal.current = null; el.focus(); } };
  return (
    <>
      {/* CSS zoom, not a transform: the row really grows, so its box scrolls and what follows moves down.
          Zoomed in, the four columns keep their 100% layout and are that much wider than the box, so there
          is room to pan (--lg-w, app.css). Stacked in a narrow panel, the row only grows taller. */}
      <div className="lg-row" style={zoom === 1 ? undefined : { zoom, "--lg-w": zoom > 1 ? `${zoom * 100}%` : "auto" } as CSSProperties}>
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
                    <button key={n.id} ref={n.id === reveal.current ? focusRevealed : undefined} className={`lg-node${dim ? " faded" : ""}${unobserved ? " off" : ""}`} aria-pressed={on} onClick={() => onNode(n)}
                      aria-label={`${l.h} ${shortId(n.source_id)}${dim ? ", hidden by the current filter" : ""}${unobserved ? ", not observed in the analyzed snapshot" : ""}`}
                      title={dim ? "hidden by the current filter" : unobserved ? "no OBSERVED_IN edge to the analyzed snapshot" : n.id}>
                      <span className="i">{breakable(shortId(n.source_id), "-")}{n.kind === "Disposition" && n.source_id === v.result.id && <b className="lg-disp">{v.result.disposition ?? "null"}</b>}</span>
                    </button>
                  );
                }) : <div className="lg-node none"><Mark hollow /><span className="i">none</span></div>}
                {all.length > ns.length && <button className="btn btn--sm btn--ghost" onClick={() => { if (cap + 50 >= all.length) reveal.current = all[cap].id; setLimit(x => ({ ...x, [i]: cap + 50 })); }}>Show 50 more ({(all.length - ns.length).toLocaleString()} left)</button>}
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
    </>
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
    return <button className="link-btn" aria-pressed={n.id === selectedNode} onClick={() => onNode(n)} title={n.source_id}><span className="nowrap">{n.kind}</span> <span className="mono-s">{shortId(n.source_id)}</span></button>;
  };
  return (
    <div className="lg-foot">
      <h3 className="src-head">Relations, as a table <span className="dim">{edges.length.toLocaleString()}</span></h3>
      <TableScroll label="Relations in the lineage">
        <table className="tbl tbl--plain tbl--edges">
          <caption className="sr">Each relation in the lineage, from one node to another</caption>
          <thead><tr><th scope="col">From</th><th scope="col">Relation</th><th scope="col">To</th></tr></thead>
          <tbody>
            {page.rows.map(e => <tr key={e.id}><td>{end(e.source)}</td><td><code className="mono-s">{breakable(e.kind)}</code></td><td>{end(e.target)}</td></tr>)}
          </tbody>
        </table>
      </TableScroll>
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
