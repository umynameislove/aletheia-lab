/* Two-tier vocabulary. Plain words carry the meaning; the contract term stays beside
   them so the screen and the schema never drift apart. Never one without the other. */

export const TERM: Record<string, [plain: string, gloss: string]> = {
  abstain: ["No conclusion", "the visible evidence does not carry the question"],
  abstained: ["Did not conclude", "the visible evidence does not carry the question"],
  answered: ["Answered", ""],
  complete: ["Run finished", ""],
  technical_failure: ["Run did not complete", "a technical failure, not a finding"],
  unverified: ["Cause not established", "an evidence limit, not a model error"],
  fully_supported: ["Backed by evidence", "all parts cite visible evidence"],
  partially_supported: ["Partly backed", "some parts uncited"],
  unsupported: ["Nothing backs this", "no visible evidence"],
  contradicted: ["Evidence disagrees", ""],
  observed: ["Observed directly", "read off the evidence"],
  hypothesized: ["Proposed, not shown", "a candidate, not a finding"],
  verified: ["Verified", "independently checked"],
  observation: ["Observation", "state what was seen"],
  project_audit: ["Project audit", "no hidden answer key"],
  diagnosis: ["Visible to diagnosis", ""],
  deterministic_mock: ["Demo answer, no model", "not a model result"],
  not_estimated: ["Not counted", "null, not 0"],
  // claim_type — closed enum (K08)
  evidence_statement: ["States what was seen", ""],
  cause_assertion: ["Asserts a cause", "check support first"],
  uncertainty_statement: ["States an open question", ""],
  recommended_action: ["Recommends an action", "a suggestion, not a finding"],
  other: ["Other claim", ""],
};
/* The same contract word can mean different things in different fields. support = not_estimated is
   about a claim, not a count, so it gets its own wording; the code shown beneath stays verbatim. */
const BY_FIELD: Record<string, Record<string, [string, string]>> = {
  support: { not_estimated: ["Support not assessed", "support was not estimated"] },
};
export const fieldTerm = (field: string, k: string): [string, string] | undefined => BY_FIELD[field]?.[k];

export const plain = (k: string) => TERM[k]?.[0] ?? k;
export const gloss = (k: string) => TERM[k]?.[1] ?? "";

export type Tone = "ok" | "partial" | "no" | "contra" | "accent" | "tick" | "";

/** max_strength = abstain: the claim may not be stated as a finding at any level. */
export const ABSTAIN_CEILING = "abstain";

export const supportTone = (s: string): Tone =>
  (({ fully_supported: "ok", partially_supported: "partial", unsupported: "no", contradicted: "contra" }) as Record<string, Tone>)[s] ?? "";

/** The ceiling, lowest first. A claim may be stated up to its max_strength and no further. */
export const LADDER = [
  { key: "observation", label: "Observation", hint: "state what was seen" },
  { key: "bounded_hypothesis", label: "Bounded hypothesis", hint: "propose, within named limits" },
  { key: "verified_cause", label: "Verified cause", hint: "needs an independent check" },
] as const;
