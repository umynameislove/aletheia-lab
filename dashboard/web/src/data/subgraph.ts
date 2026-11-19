/* The part of the lineage that concerns one claim, and windows over the rest (Q-10).
   Only edges the service sent are followed. A shared node such as the Disposition ends the walk,
   so a claim's path never picks up its neighbours' evidence by adjacency. This trims a graph the
   client already holds; a server-side subgraph query is K-10's. */

import type { GraphEdge, GraphNode } from "../types.ts";

export interface ClaimPath { nodes: GraphNode[]; edges: GraphEdge[] }

export function claimPath(nodes: readonly GraphNode[], edges: readonly GraphEdge[], claimId: string): ClaimPath {
  const claimNodes = nodes.filter(n => n.kind === "AtomicClaim" && n.source_id === claimId);
  const byId = new Map(nodes.map(n => [n.id, n]));
  const keepNodes = new Map(claimNodes.map(n => [n.id, n]));
  const keepEdges = new Map<string, GraphEdge>();
  const near = new Set(claimNodes.map(n => n.id));

  for (const e of edges) {
    if (!near.has(e.source) && !near.has(e.target)) continue;
    keepEdges.set(e.id, e);
    for (const id of [e.source, e.target]) { const n = byId.get(id); if (n) keepNodes.set(id, n); }
  }
  // one step further, only from this claim's evidence and only to where it was observed
  const evidence = new Set([...keepNodes.values()].filter(n => n.kind === "EvidenceItem").map(n => n.id));
  for (const e of edges) {
    if (e.kind !== "OBSERVED_IN" || !evidence.has(e.source)) continue;
    keepEdges.set(e.id, e);
    const n = byId.get(e.target);
    if (n) keepNodes.set(n.id, n);
  }
  return { nodes: [...keepNodes.values()], edges: [...keepEdges.values()] };
}

/** Above this the lanes open on the selected claim's path instead of everything at once. */
export const FULL_GRAPH_LIMIT = 60;
/** Nodes drawn per lane before "show more". */
export const LANE_WINDOW = 12;
