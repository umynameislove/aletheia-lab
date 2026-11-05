/* What a source is to the claim being read (Q-09). Pure, no JSON import, so the tests run this module.

   Being in the view does not make evidence a citation. Only claim.citation_ids and
   claim.counterevidence_ids state a relation; anything else that can be opened is context, and the
   reader has to say so. No relation is ever inferred from the graph, from turn visibility or from a URL. */

import type { Claim, Evidence, ProductView, Turn } from "../types.ts";

/** counter is checked first: evidence against a claim must never read as support for it. */
export type Relation = "counter" | "cited" | "context" | "unrelated" | "absent";

export interface ClaimSources { cited: Evidence[]; counter: Evidence[] }

export function sourcesOf(v: ProductView, claim: Claim): ClaimSources {
  const pick = (ids: string[]) => ids.flatMap(id => v.evidence.find(e => e.id === id) ?? []);
  return { cited: pick(claim.citation_ids), counter: pick(claim.counterevidence_ids) };
}

const turnOf = (v: ProductView, claim: Claim): Turn | undefined => v.conversation.turns.find(t => t.id === claim.turn_id);

/** The relation between one claim and one evidence ID. With no claim there is nothing to relate to. */
export function relationOf(v: ProductView, claim: Claim | null, evidenceId: string): Relation {
  if (!v.evidence.some(e => e.id === evidenceId)) return "absent";
  if (!claim) return "unrelated";
  if (claim.counterevidence_ids.includes(evidenceId)) return "counter";
  if (claim.citation_ids.includes(evidenceId)) return "cited";
  return turnOf(v, claim)?.visible_evidence_ids.includes(evidenceId) ? "context" : "unrelated";
}

export interface Reading {
  evidence: Evidence | null;
  relation: Relation | null;
  /** the ID the link asked for when this result does not contain it */
  missingId: string | null;
}

/** What the reader opens: the linked source if there is one, otherwise the claim's first source.
    A linked ID that is not in this result is reported. Another source is never put in its place. */
export function readingFor(v: ProductView, claim: Claim | null, evidenceId: string | null): Reading {
  if (evidenceId) {
    const evidence = v.evidence.find(e => e.id === evidenceId) ?? null;
    return evidence
      ? { evidence, relation: relationOf(v, claim, evidenceId), missingId: null }
      : { evidence: null, relation: "absent", missingId: evidenceId };
  }
  if (!claim) return { evidence: null, relation: null, missingId: null };
  const { cited, counter } = sourcesOf(v, claim);
  const first = cited[0] ?? counter[0] ?? null;
  return { evidence: first, relation: first ? relationOf(v, claim, first.id) : null, missingId: null };
}

export const claimsCiting = (v: ProductView, evidenceId: string): Claim[] => v.claims.filter(c => c.citation_ids.includes(evidenceId));

/** A reference someone can paste into a review: what it is, which record, which bytes. No path, no credential. */
export const referenceText = (e: Evidence): string =>
  `${e.title} — ${e.reproduction_ref.record_kind} ${e.reproduction_ref.record_id} — sha256 ${e.source_sha256}`;
