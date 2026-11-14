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

/* Zoom for the drawing, in fixed steps. The floor is 0.8: a lane node is about 30 px tall, so it
   stays at least 24 px (WCAG 2.5.8 target size) when zoomed out. 1 is the fluid layout. */
export const ZOOM_STEPS = [0.8, 1, 1.25, 1.5, 2] as const;
export const ZOOM_MIN = ZOOM_STEPS[0];
export const ZOOM_MAX = ZOOM_STEPS[ZOOM_STEPS.length - 1];

/** The next step in that direction, clamped to the ends; 0 resets to 100%. A level between steps
    (none is ever set, but it costs nothing) moves to the nearest step on that side. */
export function zoomStep(current: number, by: -1 | 0 | 1): number {
  if (by === 0 || !Number.isFinite(current)) return 1;
  const eps = 1e-6;
  if (by > 0) return ZOOM_STEPS.find(z => z > current + eps) ?? ZOOM_MAX;
  return [...ZOOM_STEPS].reverse().find(z => z < current - eps) ?? ZOOM_MIN;
}

/** + and = zoom in, - zooms out, 0 resets. Not with Ctrl, Cmd or Alt: those belong to the browser's
    own page zoom, which must keep working. Shift is allowed, since + needs it on many layouts. */
export function zoomKey(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean }): -1 | 0 | 1 | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  return e.key === "+" || e.key === "=" ? 1 : e.key === "-" ? -1 : e.key === "0" ? 0 : null;
}
