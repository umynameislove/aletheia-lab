/* A synthetic lineage of a chosen size, for the render benchmark and layout tests only. It follows
   the K07 shape — one Snapshot, EvidenceItem → Snapshot OBSERVED_IN, AtomicClaim → EvidenceItem
   CITES, AtomicClaim → Disposition ASSIGNED_DISPOSITION — with made-up IDs. It is never shown as
   a result and carries no research meaning. */

import type { GraphEdge, GraphNode } from "../types.ts";

export interface SyntheticGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  claimIds: string[];
  evidenceIds: string[];
  resultId: string;
}

const pad = (n: number) => String(n).padStart(4, "0");
const node = (id: string, kind: string, source_id: string): GraphNode => ({ id, kind, source_id, visibility: "diagnosis" });
const edge = (id: string, kind: string, source: string, target: string): GraphEdge => ({ id, kind, source, target, visibility: "diagnosis" });

/** `total` nodes: 1 snapshot, 1 disposition, ~60% evidence, the rest claims citing two evidence items each. */
export function syntheticGraph(total: number): SyntheticGraph {
  const evidenceCount = Math.max(1, Math.round((total - 2) * 0.6));
  const claimCount = Math.max(1, total - 2 - evidenceCount);
  const resultId = "bench-result-0001";
  const nodes: GraphNode[] = [node("bench-node-snapshot", "Snapshot", "bench-snapshot-after"), node("bench-node-disposition", "Disposition", resultId)];
  const edges: GraphEdge[] = [];
  const evidenceIds: string[] = [], claimIds: string[] = [];
  for (let i = 0; i < evidenceCount; i++) {
    const id = `bench-evidence-${pad(i)}`;
    evidenceIds.push(id);
    nodes.push(node(`bench-node-evidence-${pad(i)}`, "EvidenceItem", id));
    edges.push(edge(`bench-edge-observed-${pad(i)}`, "OBSERVED_IN", `bench-node-evidence-${pad(i)}`, "bench-node-snapshot"));
  }
  for (let i = 0; i < claimCount; i++) {
    const id = `bench-claim-${pad(i)}`;
    claimIds.push(id);
    nodes.push(node(`bench-node-claim-${pad(i)}`, "AtomicClaim", id));
    for (const k of [i % evidenceCount, (i * 7 + 3) % evidenceCount]) {
      edges.push(edge(`bench-edge-cites-${pad(i)}-${pad(k)}`, "CITES", `bench-node-claim-${pad(i)}`, `bench-node-evidence-${pad(k)}`));
    }
    edges.push(edge(`bench-edge-disposition-${pad(i)}`, "ASSIGNED_DISPOSITION", `bench-node-claim-${pad(i)}`, "bench-node-disposition"));
  }
  // de-duplicate CITES when both picks land on the same evidence item
  const seen = new Set<string>();
  return { nodes, edges: edges.filter(e => (seen.has(e.id) ? false : (seen.add(e.id), true))), claimIds, evidenceIds, resultId };
}

/** Deterministic Fisher–Yates shuffle (mulberry32), so a permutation test is reproducible. */
export function shuffled<T>(items: readonly T[], seed: number): T[] {
  let a = seed >>> 0;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = [...items];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}
