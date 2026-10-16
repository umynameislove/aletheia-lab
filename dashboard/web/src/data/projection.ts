/* Pure view logic — no JSON import, so the contract tests run this exact module under Node.
   data/view.ts re-exports all of it next to the packaged fixture. */
import type { ProductView, Turn } from "../types.ts";
import { COUNTERFACTUAL_MARKER } from "../service/markers.ts";
export { COUNTERFACTUAL_MARKER };

/* K07 — the service puts counterfactual availability in missing_evidence as one fixed string,
   at most once per array and always last. It is a status, not a request for evidence, so the UI
   matches it exactly and keeps it out of every "requested" list and count. Its absence means
   nothing: the UI never reads a missing marker as "available". */

/** The evidence actually requested — missing_evidence without the counterfactual marker. */
export const evidenceRequests = (missing: string[]) => missing.filter(m => m !== COUNTERFACTUAL_MARKER);
export const hasCounterfactualMarker = (missing: string[]) => missing.includes(COUNTERFACTUAL_MARKER);

/** "not_available" only when the service said so; otherwise the UI draws no conclusion. */
export function counterfactualStatus(v: ProductView): "not_available" | "not_evaluated" {
  const said = v.conversation.turns.some(t => hasCounterfactualMarker(t.missing_evidence))
    || v.claims.some(c => hasCounterfactualMarker(c.missing_evidence));
  return said ? "not_available" : "not_evaluated";
}

/** The view as the demo state leaves it.
    - technical_failure (the invariant locked with Kiên): disposition null, no claims and
      denominators.claims 0; the diagnosis-visible evidence already collected stays; the graph keeps
      only Snapshot/EvidenceItem nodes and OBSERVED_IN edges; every turn abstains and carries no
      counterfactual marker, because a failed run cannot conclude availability.
    - conversation deleted: turns go; snapshot, evidence and the claim record stay. */
export function projectView(v: ProductView, s: { failed: boolean; deletedConversation?: boolean }): ProductView {
  let out = v;
  if (s.failed) {
    const nodes = out.graph.nodes.filter(n => n.kind === "Snapshot" || n.kind === "EvidenceItem");
    const kept = new Set(nodes.map(n => n.id));
    out = {
      ...out,
      result: {
        ...out.result, status: "technical_failure", disposition: null,
        denominators: { ...out.result.denominators, claims: 0 },
      },
      claims: [],
      conversation: {
        ...out.conversation,
        turns: out.conversation.turns.map(t => ({ ...t, abstained: true, missing_evidence: evidenceRequests(t.missing_evidence) })),
      },
      graph: {
        nodes,
        edges: out.graph.edges.filter(e => e.kind === "OBSERVED_IN" && kept.has(e.source) && kept.has(e.target)),
      },
    };
  }
  if (s.deletedConversation) out = { ...out, conversation: { ...out.conversation, turns: [] } };
  return out;
}

/** Evidence the graph observes in the analyzed snapshot. Since K07 OBSERVED_IN is only emitted for
    after-snapshot evidence and evidence with direct provenance to it — not for every item. */
export function observedInSnapshot(v: ProductView): Set<string> {
  const byId = new Map(v.graph.nodes.map(n => [n.id, n]));
  const out = new Set<string>();
  for (const e of v.graph.edges) {
    if (e.kind !== "OBSERVED_IN") continue;
    const src = byId.get(e.source), dst = byId.get(e.target);
    if (src?.kind === "EvidenceItem" && dst?.kind === "Snapshot") out.add(src.source_id);
  }
  return out;
}

export const lastTurn = (v: ProductView): Turn | undefined => v.conversation.turns[v.conversation.turns.length - 1];
