/* Q08 render benchmark — not part of the app or the test suite. Built separately with
   `npm run bench:build` (production React) and opened from `npm run bench:serve`.

   Each run mounts the real Lineage component on a fresh root with a synthetic graph of ?n= nodes
   (default 1,000) in a newly shuffled order, then measures from render start until the browser has
   laid out and painted the result (two animation frames). It also checks that every run drew the
   same lanes in the same order, so the timing is of a deterministic layout. */

import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import "../styles/tokens.css";
import "../styles/app.css";
import viewJson from "../data/synthetic_p6_view.json";
import type { ProductView } from "../types";
import { Lineage } from "../components/Lineage";
import { shuffled, syntheticGraph } from "../data/syntheticGraph";

const params = new URLSearchParams(location.search);
const N = Number(params.get("n") ?? 1000);
const RUNS = Number(params.get("runs") ?? 30);
const WARMUP = 3;

const frame = () => new Promise<void>(r => requestAnimationFrame(() => r()));
const pct = (sorted: number[], p: number) => sorted[Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)];

async function run() {
  const g = syntheticGraph(N);
  const base = viewJson as ProductView;
  const stage = document.getElementById("stage")!;
  const times: number[] = [];
  let order: string | null = null;
  let deterministic = true;
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
    const drawn = [...host.querySelectorAll(".lg-node .i")].map(e => e.getAttribute("title")).join("|");
    if (order === null) order = drawn; else if (drawn !== order) deterministic = false;
    if (i >= WARMUP) times.push(t);
    root.unmount();
  }
  const sorted = [...times].sort((a, b) => a - b);
  const nodesDrawn = order ? order.split("|").length : 0;
  const result = {
    nodes: N, nodesDrawn, edges: g.edges.length, runs: RUNS, warmup: WARMUP, deterministic,
    p50_ms: +pct(sorted, 50).toFixed(1), p95_ms: +pct(sorted, 95).toFixed(1), max_ms: +sorted[sorted.length - 1].toFixed(1),
    target_ms: 2000, pass: pct(sorted, 95) <= 2000 && deterministic && nodesDrawn === N,
    userAgent: navigator.userAgent, viewport: `${innerWidth}x${innerHeight}`, devicePixelRatio,
  };
  (window as unknown as { __bench: typeof result }).__bench = result;
  document.getElementById("report")!.textContent = JSON.stringify(result, null, 2);
}

void run();
