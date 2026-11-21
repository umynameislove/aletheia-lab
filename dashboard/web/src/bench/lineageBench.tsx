/* Q08 render benchmark — not part of the app or the test suite. Built separately with
   `npm run bench:build` (production React) and opened from `npm run bench:serve`.

   Each run mounts the real Lineage component on a fresh root with a synthetic graph of ?n= nodes
   (default 1,000) in a newly shuffled order, then measures from render start until the browser has
   laid out and painted the result (two animation frames). Each run checks the actual node IDs
   against the first window of each lane, in order. */

import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import "../styles/tokens.css";
import "../styles/app.css";
import viewJson from "../data/synthetic_p6_view.json";
import type { ProductView } from "../types";
import { LineagePanel as Lineage } from "../components/Lineage";
import { LANE_KINDS } from "../data/lineage";
import { LANE_WINDOW } from "../data/subgraph";
import { shuffled, syntheticGraph } from "../data/syntheticGraph";

const params = new URLSearchParams(location.search);
const N = Number(params.get("n") ?? 1000);
const RUNS = Number(params.get("runs") ?? 30);
const WARMUP = 3;

const frame = () => new Promise<void>(r => requestAnimationFrame(() => r()));
const pct = (sorted: number[], p: number) => sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)];
const sameOrder = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((id, i) => id === b[i]);

async function run() {
  const g = syntheticGraph(N);
  const expected = LANE_KINDS.flatMap(kind => g.nodes.filter(node => node.kind === kind)
    .map(node => node.id).sort().slice(0, LANE_WINDOW));
  const base = viewJson as ProductView;
  const stage = document.getElementById("stage")!;
  const times: number[] = [];
  let order: string[] | null = null;
  let deterministic = true;
  let visibleNodeIdsMatch = true;
  for (let i = 0; i < WARMUP + RUNS; i++) {
    const view: ProductView = {
      ...base,
      graph: { nodes: shuffled(g.nodes, i + 1), edges: shuffled(g.edges, i + 101) },
      result: { ...base.result, id: g.resultId },
    };
    const host = document.createElement("div");
    stage.replaceChildren(host);
    const root = createRoot(host);
    const t0 = performance.now();
    flushSync(() => root.render(
      <Lineage v={view} selectedClaim={null} selectedEvidence={null} selectedNode={null} shownIds={new Set(g.claimIds)} onNode={() => {}} />,
    ));
    host.getBoundingClientRect();
    await frame();
    await frame();
    const t = performance.now() - t0;
    // The fixture observes every evidence node and shows every claim, so each
    // node button's tooltip contains its full ID.
    const drawn = [...host.querySelectorAll<HTMLButtonElement>("button.lg-node")].map(button => button.title);
    if (order === null) order = drawn; else if (!sameOrder(drawn, order)) deterministic = false;
    if (!sameOrder(drawn, expected)) visibleNodeIdsMatch = false;
    if (i >= WARMUP) times.push(t);
    root.unmount();
  }
  const sorted = [...times].sort((a, b) => a - b);
  const result = {
    nodes: g.nodes.length, nodesDrawn: order?.length ?? 0, expectedNodesDrawn: expected.length,
    edges: g.edges.length, runs: RUNS, warmup: WARMUP, deterministic, visibleNodeIdsMatch,
    p50_ms: +pct(sorted, 50).toFixed(1), p95_ms: +pct(sorted, 95).toFixed(1), max_ms: +sorted[sorted.length - 1].toFixed(1),
    target_ms: 2000, pass: pct(sorted, 95) <= 2000 && deterministic && visibleNodeIdsMatch,
    userAgent: navigator.userAgent, viewport: `${innerWidth}x${innerHeight}`, devicePixelRatio,
  };
  (window as unknown as { __bench: typeof result }).__bench = result;
  document.getElementById("report")!.textContent = JSON.stringify(result, null, 2);
}

void run();
