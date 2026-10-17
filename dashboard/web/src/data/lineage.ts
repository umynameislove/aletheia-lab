/* The lineage drawing's layout, as a pure function of view.graph (no JSON import, so the tests
   can load it under Node's type stripping).

   - Deterministic: each lane is sorted by node ID and the text path by edge ID, so the same graph
     draws the same picture whatever order the nodes and edges arrive in.
   - Fail closed: an edge whose endpoint is missing, or a node ID used twice, means the projection
     cannot be trusted. The caller then draws nothing rather than a partial graph; no edge is ever
     added, guessed or repaired here. */

import type { GraphEdge, GraphNode } from "../types.ts";

export const LANE_KINDS = ["Snapshot", "EvidenceItem", "AtomicClaim", "Disposition"] as const;

export interface LineageLayout {
  /** one array per LANE_KINDS entry, sorted by node ID */
  lanes: GraphNode[][];
  /** edge kinds joining lane i and lane i + 1, sorted and unique */
  links: string[][];
  /** every edge, sorted by ID — the textual path */
  edges: GraphEdge[];
  /** edge IDs whose source or target node is not in the graph */
  dangling: string[];
  /** node IDs that occur more than once */
  duplicates: string[];
  /** nodes of a kind the drawing has no lane for; counted, never placed */
  unplaced: number;
}

const byId = <T extends { id: string }>(a: T, b: T) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function lineageLayout(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): LineageLayout {
  const index = new Map<string, GraphNode>();
  const duplicates = new Set<string>();
  for (const n of nodes) {
    if (index.has(n.id)) duplicates.add(n.id);
    index.set(n.id, n);
  }
  const sortedEdges = [...edges].sort(byId);
  const dangling = sortedEdges.filter(e => !index.has(e.source) || !index.has(e.target)).map(e => e.id);
  const lanes = LANE_KINDS.map(kind => nodes.filter(n => n.kind === kind).sort(byId));
  const links = LANE_KINDS.slice(0, -1).map((a, i) => {
    const b = LANE_KINDS[i + 1];
    const kinds = sortedEdges
      .filter(e => {
        const s = index.get(e.source)?.kind, t = index.get(e.target)?.kind;
        return (s === a && t === b) || (s === b && t === a);
      })
      .map(e => e.kind);
    return [...new Set(kinds)].sort();
  });
  const placed = lanes.reduce((n, l) => n + l.length, 0);
  return { lanes, links, edges: sortedEdges, dangling, duplicates: [...duplicates].sort(), unplaced: nodes.length - placed };
}
